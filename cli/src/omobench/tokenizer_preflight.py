"""Load the tokenizer before the benchmark does, in the interpreter that will.

A tokenizer failure is the most common way a run dies with nothing to show for
it: GuideLLM resolves the tokenizer before it sends a single request, so an
identifier that is merely well-formed buys nothing. The usual case is a GGUF
repository, which serves perfectly well and contains no Transformers tokenizer
at all.

Four separate questions, kept separate, because a yes to one is routinely read
as a yes to the next:

    syntax         Is this a usable repository id or folder path?
    files          Are the tokenizer's files where the loader will look?
    loaded         Did `AutoTokenizer.from_pretrained` actually return one?
    compatibility  Is it the tokenizer the served weights were trained with?

Only the first three can be answered here. The fourth never can: a tokenizer
that loads is not thereby the right one, and choosing the wrong one changes the
prompts and the token counts, which changes the measurements.

The probe runs in the runner's own interpreter, through the same
`AutoTokenizer.from_pretrained` call GuideLLM 0.7.3 makes
(`guidellm/data/tokenizers/huggingface.py`), so a tokenizer that loads here
loads there. It never executes code from a repository, and it never downloads
model weights -- not as a side effect and not on request.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import Any, Callable

from . import cli

# What the probe may reach. Each level is chosen by the caller and named in the
# panel before anything happens, because "checking" something over a metered
# connection is not what people expect a check to do.
NETWORK_NONE = "none"           # cache only; nothing leaves the machine.
NETWORK_METADATA = "metadata"   # may list a repository and read its model card.
NETWORK_DOWNLOAD = "download"   # may fetch tokenizer files, never weights.
NETWORK_LEVELS = (NETWORK_NONE, NETWORK_METADATA, NETWORK_DOWNLOAD)

NOT_COMPARED = (
    "Not verified, and not verifiable from here. A tokenizer that loads is not thereby the "
    "tokenizer your server's weights were trained with. Choosing the wrong one changes how "
    "prompts are built and how tokens are counted, which changes the measurements -- so pick the "
    "repository the model card names, not merely one that loads."
)

# Files whose presence means weights, not a tokenizer. The probe refuses to
# report a download that touched one, so "tokenizer only" is checked rather
# than asserted.
WEIGHT_SUFFIXES = (".gguf", ".safetensors", ".bin", ".pt", ".pth", ".h5", ".msgpack", ".ckpt")
TOKENIZER_FILENAMES = (
    "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt",
    "spiece.model", "tokenizer.model", "special_tokens_map.json", "added_tokens.json",
    "chat_template.jinja",
)

PROBE_SOURCE = r'''
import json, os, sys

request = json.loads(sys.stdin.read())
source, level = request["source"], request["network"]
report = {"stage": "import", "files": [], "downloaded": [], "base_model": None,
          "repository_listed": False, "identity": None, "cache_dir": None}


def finish(**fields):
    report.update(fields)
    sys.stdout.write("OMOBENCH_TOKENIZER " + json.dumps(report) + "\n")
    sys.stdout.flush()
    raise SystemExit(0)


try:
    from transformers import AutoTokenizer
except Exception as error:
    finish(outcome="runner-unusable", error="%s: %s" % (type(error).__name__, error))

report["stage"] = "load"
try:
    # The call GuideLLM makes, with remote code execution refused explicitly
    # rather than left to a default that could change.
    tokenizer = AutoTokenizer.from_pretrained(
        source, trust_remote_code=False,
        local_files_only=(level != "download"))
except Exception as error:
    report["load_error"] = "%s: %s" % (type(error).__name__, error)
else:
    files = {}
    for attribute in ("vocab_file", "merges_file"):
        path = getattr(tokenizer, attribute, None)
        if isinstance(path, str):
            files[attribute] = os.path.basename(path)
    finish(outcome="loaded", identity={
        "class": type(tokenizer).__name__,
        "vocabulary_size": int(getattr(tokenizer, "vocab_size", 0)) or None,
    }, resolved_files=files)

if level == "none":
    finish(outcome="not-loaded")

report["stage"] = "inspect"
try:
    from huggingface_hub import HfApi
    info = HfApi().model_info(source, files_metadata=False)
except Exception as error:
    finish(outcome="not-loaded", inspect_error="%s: %s" % (type(error).__name__, error))

report["repository_listed"] = True
report["files"] = sorted(
    sibling.rfilename for sibling in (getattr(info, "siblings", None) or [])
    if "/" not in sibling.rfilename)[:400]
# cardData is a ModelCardData object, not a plain dict, so reach the
# publisher's declaration through whichever of the two this version gives.
card = getattr(info, "cardData", None)
if card is not None and hasattr(card, "to_dict"):
    card = card.to_dict()
if isinstance(card, dict):
    base = card.get("base_model")
else:
    base = getattr(card, "base_model", None)
# Several base models is an ambiguous declaration, not a suggestion.
if isinstance(base, list):
    base = base[0] if len(base) == 1 else None
report["base_model"] = base if isinstance(base, str) and base.strip() else None
finish(outcome="not-loaded")
'''


@dataclass
class TokenizerPreflight:
    """What was established about one tokenizer source, and what was not."""

    ok: bool
    kind: str
    message: str
    source: str
    network: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    identity: dict[str, Any] | None = None
    suggestion: dict[str, Any] | None = None
    repository_files: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    destination: str | None = None
    detail: str = ""
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "kind": self.kind, "message": self.message, "source": self.source,
            "network": self.network, "checks": self.checks, "identity": self.identity,
            "suggestion": self.suggestion, "repository_files": self.repository_files,
            "downloaded": self.downloaded, "destination": self.destination,
            "detail": self.detail, "seconds": round(self.seconds, 2),
        }


def _checks(syntax: tuple[str, str], files: tuple[str, str], loaded: tuple[str, str]) -> list[dict[str, Any]]:
    return [
        {"id": "syntax", "state": syntax[0], "detail": syntax[1]},
        {"id": "files", "state": files[0], "detail": files[1]},
        {"id": "loaded", "state": loaded[0], "detail": loaded[1]},
        {"id": "compatibility", "state": "unverified", "detail": NOT_COMPARED},
    ]


def cache_destination() -> str:
    directories = cli.huggingface_cache_directories()
    return str(directories[0]) if directories else "the Hugging Face hub cache"


def _run_probe(
    source: str,
    *,
    interpreter: Sequence[str],
    network: str,
    timeout: float,
    cancel: Callable[[], bool] | None,
) -> tuple[str, dict[str, Any], str, float]:
    """Run the probe in a bounded child process and return (state, document, detail, seconds)."""
    environment = {**os.environ,
                   "HF_HUB_DISABLE_TELEMETRY": "1",
                   "TOKENIZERS_PARALLELISM": "false"}
    if network == NETWORK_NONE:
        # Belt and braces: the probe already passes local_files_only, and these
        # make a request impossible rather than merely unasked for.
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    else:
        environment.pop("HF_HUB_OFFLINE", None)
        environment.pop("TRANSFORMERS_OFFLINE", None)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            # -I: isolated, so a stray PYTHONPATH cannot answer about a
            # different installation than the one the benchmark will use.
            [*interpreter, "-I", "-c", PROBE_SOURCE],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=environment, start_new_session=os.name != "nt",
        )
    except OSError as error:
        return "unavailable", {}, cli.sanitize_diagnostics(str(error)), time.monotonic() - started

    request = json.dumps({"source": source, "network": network})
    deadline = started + max(5.0, timeout)
    cancelled = False
    stdout = stderr = ""
    while True:
        try:
            stdout, stderr = process.communicate(input=request if process.stdin else None, timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            request = None  # communicate() only accepts input on the first call.
        if cancel is not None and cancel():
            cancelled = True
        if cancelled or time.monotonic() > deadline:
            cli._terminate_process_tree(process)
            with __import__("contextlib").suppress(subprocess.TimeoutExpired):
                stdout, stderr = process.communicate(timeout=10)
            return ("cancelled" if cancelled else "timeout"), {}, \
                cli.sanitize_diagnostics(stderr or ""), time.monotonic() - started

    seconds = time.monotonic() - started
    detail = cli.sanitize_diagnostics((stderr or "") + ("\n" + stdout if stdout else ""))
    if "OMOBENCH_TOKENIZER" not in (stdout or ""):
        return "crashed", {}, detail, seconds
    try:
        document = json.loads(stdout.split("OMOBENCH_TOKENIZER", 1)[1].strip().splitlines()[0])
    except (ValueError, IndexError):
        return "crashed", {}, detail, seconds
    return "completed", document, cli.sanitize_diagnostics(stderr or ""), seconds


def _gguf_only(files: list[str]) -> bool:
    """A repository that serves fine and has no Transformers tokenizer in it."""
    if not files:
        return False
    return (any(name.lower().endswith(".gguf") for name in files)
            and not any(name in TOKENIZER_FILENAMES for name in files))


def _suggestion(document: dict[str, Any], source: str) -> dict[str, Any] | None:
    """The tokenizer the publisher itself points at, when it named one.

    `base_model` is the publisher's own declaration on the repository card, not
    a guess OmoBench made from the name. Nothing picks a generic tokenizer that
    merely loads: a plausible stand-in silently changes every token count.
    """
    base = document.get("base_model")
    if not isinstance(base, str) or not base.strip() or base.strip() == source:
        return None
    return {
        "source": base.strip(),
        "basis": "publisher-declared-base-model",
        "reason": (
            f"The publisher of {source} declares {base.strip()} as its base model, which is where "
            "its tokenizer comes from. Check the model card and select it deliberately -- this is "
            "the publisher's statement, not a verified match with the weights your server loaded."
        ),
    }


def preflight(
    source: str,
    *,
    interpreter: str | Sequence[str] | None = None,
    network: str = NETWORK_NONE,
    timeout: float = 180.0,
    cancel: Callable[[], bool] | None = None,
) -> TokenizerPreflight:
    """Answer the three answerable questions about one tokenizer source.

    `network` says what this is allowed to reach, and the answer always reports
    the level it ran at, so "it works" is never read as "it works offline".
    """
    if network not in NETWORK_LEVELS:
        raise ValueError(f"network must be one of {', '.join(NETWORK_LEVELS)}.")
    text = (source or "").strip()
    try:
        resolved = cli.validate_tokenizer_source(text)
    except ValueError as error:
        return TokenizerPreflight(
            ok=False, kind="invalid", source=text, network=network, message=str(error),
            checks=_checks(("failed", str(error)),
                           ("skipped", "Not looked for: there is no usable repository id or path."),
                           ("skipped", "Not attempted: nothing to load.")),
        )

    # A sequence is accepted so the runner can be reached through a launcher
    # ("wsl", "python3") as well as by path.
    command = ([interpreter] if isinstance(interpreter, str)
               else list(interpreter) if interpreter else [sys.executable])
    executable = " ".join(command)
    state, document, detail, seconds = _run_probe(
        resolved, interpreter=command, network=network, timeout=timeout, cancel=cancel)
    destination = cache_destination() if network == NETWORK_DOWNLOAD else None

    if state == "cancelled":
        return TokenizerPreflight(
            ok=False, kind="cancelled", source=resolved, network=network, seconds=seconds,
            message="The tokenizer check was stopped. Nothing further was loaded or downloaded.",
            checks=_checks(("ok", f"Accepted: {resolved}."),
                           ("skipped", "The check was stopped before this was established."),
                           ("skipped", "The check was stopped before loading.")))
    if state == "timeout":
        return TokenizerPreflight(
            ok=False, kind="timeout", source=resolved, network=network, seconds=seconds, detail=detail,
            message=(f"Loading the tokenizer did not finish within {int(timeout)} s and was stopped. "
                     "On a slow connection try again with more time, or download the files first."),
            checks=_checks(("ok", f"Accepted: {resolved}."),
                           ("unknown", "Not established before the time limit."),
                           ("failed", "Did not finish within the time limit.")))
    if state in {"unavailable", "crashed"} or document.get("outcome") == "runner-unusable":
        return TokenizerPreflight(
            ok=False, kind="runner-unusable", source=resolved, network=network, seconds=seconds,
            detail=detail or document.get("error", ""),
            message=("The tokenizer could not be checked because the runner's own environment "
                     f"could not load Transformers using {executable}. Fix the runner "
                     "installation first; the benchmark would fail in the same place."),
            checks=_checks(("ok", f"Accepted: {resolved}."),
                           ("skipped", "Not looked for: the runner environment is unusable."),
                           ("failed", "The runner's interpreter could not import Transformers.")))

    local = not cli.HF_REPO_ID.fullmatch(resolved)
    syntax = ("ok", f"Accepted as a local tokenizer folder: {resolved}." if local
              else f"Accepted as a Hugging Face repository id: {resolved}.")

    if document.get("outcome") == "loaded":
        identity = document.get("identity") or {}
        downloaded = [] if network != NETWORK_DOWNLOAD else sorted(
            name for name in (document.get("resolved_files") or {}).values() if name)
        return TokenizerPreflight(
            ok=True, kind="loaded", source=resolved, network=network, seconds=seconds,
            identity={"class": identity.get("class"), "vocabulary_size": identity.get("vocabulary_size")},
            downloaded=downloaded, destination=destination if downloaded else None,
            message=(f"Loaded: {identity.get('class') or 'a tokenizer'}"
                     + (f", vocabulary {identity['vocabulary_size']}" if identity.get("vocabulary_size") else "")
                     + f". The runner will build and count this workload with {resolved}."
                     + (" Whether it matches the weights your server loaded is a separate question "
                        "this cannot answer.")),
            checks=_checks(syntax,
                           ("ok", "Present where the loader looks for them."
                                  if network != NETWORK_DOWNLOAD else
                                  f"Present, after fetching what was missing into {destination}. "
                                  "No model weights were requested or downloaded."),
                           ("ok", "Loaded in the runner's own interpreter, with repository code "
                                  "execution refused.")))

    error = document.get("load_error", "")
    files = [name for name in (document.get("repository_files") or document.get("files") or [])
             if isinstance(name, str)]
    suggestion = _suggestion(document, resolved)

    if _gguf_only(files):
        return TokenizerPreflight(
            ok=False, kind="gguf-only", source=resolved, network=network, seconds=seconds,
            detail=error, repository_files=files, suggestion=suggestion,
            message=(f"{resolved} contains GGUF weights and no Transformers tokenizer "
                     f"({', '.join(TOKENIZER_FILENAMES[:3])}…). Your server loads GGUF happily; "
                     "the benchmark runner cannot build a workload from it. Enter the repository "
                     "that publishes the tokenizer instead"
                     + (f" — the publisher names {suggestion['source']}." if suggestion else
                        " — usually the original, unquantized model the GGUF was converted from.")),
            checks=_checks(syntax,
                           ("missing", "This repository publishes weight files only; it has no "
                                       "tokenizer file for the loader to read."),
                           ("failed", "Not loaded: there is nothing here to load.")))

    if network == NETWORK_NONE:
        return TokenizerPreflight(
            ok=False, kind="not-cached", source=resolved, network=network, seconds=seconds, detail=error,
            message=(f"{resolved} is not in this machine's cache, so this offline check could not "
                     "load it. Check it with repository access, or download its tokenizer files, "
                     "before starting a benchmark — the run would stop here otherwise."
                     if not local else
                     f"{resolved} could not be loaded from disk. The folder exists, but "
                     "AutoTokenizer could not build a tokenizer from what is in it."),
            checks=_checks(syntax,
                           ("missing", "Not in the local cache." if not local
                                       else "The folder has no file the loader could use."),
                           ("failed", "Not attempted over the network: this check stayed offline."
                                      if not local else "Loading from the folder failed.")))

    if "trust_remote_code" in error or "custom code" in error.lower():
        return TokenizerPreflight(
            ok=False, kind="needs-remote-code", source=resolved, network=network, seconds=seconds,
            detail=error, repository_files=files, suggestion=suggestion,
            message=(f"{resolved} ships its own tokenizer code and asks to execute it. OmoBench "
                     "refuses that, here and in the benchmark. Use a repository whose tokenizer "
                     "loads with the standard implementation."),
            checks=_checks(syntax, ("ok", "The repository is reachable."),
                           ("failed", "Refused: loading it would run code from the repository.")))

    return TokenizerPreflight(
        ok=False, kind="load-failed", source=resolved, network=network, seconds=seconds,
        detail=error or document.get("inspect_error", ""), repository_files=files, suggestion=suggestion,
        message=(f"{resolved} did not load. The runner would fail in the same place, before any "
                 "request is sent. The loader's own error is below."),
        checks=_checks(syntax,
                       ("ok" if files else "unknown",
                        "The repository was listed." if files else "Could not be established."),
                       ("failed", "AutoTokenizer could not build a tokenizer from this source.")))


def download_plan(source: str) -> dict[str, Any]:
    """What an authorized tokenizer-only download would do, before it does it.

    Shown and confirmed first: a download is a deliberate act with a visible
    source and a visible destination, not a side effect of pressing "check".
    """
    resolved = cli.validate_tokenizer_source(source)
    if not cli.HF_REPO_ID.fullmatch(resolved):
        raise ValueError(f"{resolved} is a local folder; there is nothing to download.")
    return {
        "source": f"https://huggingface.co/{resolved}",
        "destination": cache_destination(),
        "downloads": "Tokenizer files only: vocabulary, merges and tokenizer configuration.",
        "never_downloads": "Model weights are never requested by this action.",
    }
