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
import re
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
# The files a tokenizer is built from, looked for in the cache when an offline
# load fails, so "not downloaded" can be told apart from "downloaded, broken".
TOKENIZER_NAMES = ("tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt",
                   "spiece.model", "tokenizer.model", "special_tokens_map.json")


def error_types(error):
    """The class names along an exception's chain. Transformers wraps the hub's
    own errors (GatedRepoError, RepositoryNotFoundError, connection errors) in
    an OSError, and the class is better evidence than the wording."""
    names, seen = [], set()
    while error is not None and id(error) not in seen and len(names) < 8:
        seen.add(id(error))
        names.append(type(error).__name__)
        error = error.__cause__ or error.__context__
    return names


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
    report["load_error_types"] = error_types(error)
else:
    files = {}
    for attribute in ("vocab_file", "merges_file"):
        path = getattr(tokenizer, attribute, None)
        if isinstance(path, str):
            files[attribute] = os.path.basename(path)
    # Loading is not using. The workload is built by encoding text, so that is
    # what is tried, on a sentence of the same kind.
    try:
        encoded = tokenizer.encode("OmoBench tokenizer check: please count the words in this sentence.")
    except Exception as error:
        finish(outcome="encode-failed", error="%s: %s" % (type(error).__name__, error))
    if not encoded:
        finish(outcome="encode-failed", error="The tokenizer returned no tokens for a sample sentence.")
    # Which bytes loaded: the cached snapshot's commit for a repository, or a
    # hash of the tokenizer file for a local folder. Read from what is on disk,
    # so it is the revision that was used, not whatever the repository says now.
    revision = None
    if os.path.isdir(source):
        import hashlib
        for name in ("tokenizer.json", "tokenizer_config.json", "tokenizer.model", "vocab.json"):
            candidate = os.path.join(source, name)
            if os.path.isfile(candidate):
                with open(candidate, "rb") as handle:
                    revision = "local-sha256:" + hashlib.sha256(handle.read()).hexdigest()
                break
    else:
        try:
            from huggingface_hub import try_to_load_from_cache
            for name in ("tokenizer_config.json", "tokenizer.json", "vocab.json", "tokenizer.model"):
                cached = try_to_load_from_cache(source, name)
                if isinstance(cached, str) and "/snapshots/" in cached.replace(os.sep, "/"):
                    revision = cached.replace(os.sep, "/").split("/snapshots/")[1].split("/")[0]
                    break
        except Exception:
            revision = None
    finish(outcome="loaded", identity={
        "class": type(tokenizer).__name__,
        "vocabulary_size": int(getattr(tokenizer, "vocab_size", 0)) or None,
    }, resolved_files=files, encoded_tokens=len(encoded), revision=revision)

if level == "none":
    cached = []
    if os.path.isdir(source):
        cached = sorted(name for name in os.listdir(source) if name in TOKENIZER_NAMES)
    else:
        try:
            from huggingface_hub import try_to_load_from_cache
            cached = [name for name in TOKENIZER_NAMES
                      if isinstance(try_to_load_from_cache(source, name), str)]
        except Exception:
            cached = None  # could not look: unknown, not "absent"
    finish(outcome="not-loaded", cached_files=cached)

report["stage"] = "inspect"
try:
    from huggingface_hub import HfApi
    info = HfApi().model_info(source, files_metadata=False)
except Exception as error:
    finish(outcome="not-loaded", inspect_error="%s: %s" % (type(error).__name__, error),
           inspect_error_types=error_types(error))

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
    # The commit of the cached snapshot that loaded, or local-sha256:... for a
    # folder. None when it could not be read, which is not the same as "main".
    revision: str | None = None
    # Tokens the sample sentence encoded to: proof the tokenizer was used, not
    # just constructed.
    encoded_tokens: int | None = None
    # What would move this forward, so the panel offers the right action and
    # only offers a download when a download can help. None once it loaded.
    recovery: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "kind": self.kind, "message": self.message, "source": self.source,
            "network": self.network, "checks": self.checks, "identity": self.identity,
            "suggestion": self.suggestion, "repository_files": self.repository_files,
            "downloaded": self.downloaded, "destination": self.destination,
            "detail": self.detail, "seconds": round(self.seconds, 2),
            "revision": self.revision, "encoded_tokens": self.encoded_tokens,
            "recovery": self.recovery,
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
            recovery=RECOVER_CHOOSE,
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
            recovery=RECOVER_RETRY,
            message="The tokenizer check was stopped. Nothing further was loaded or downloaded.",
            checks=_checks(("ok", f"Accepted: {resolved}."),
                           ("skipped", "The check was stopped before this was established."),
                           ("skipped", "The check was stopped before loading.")))
    if state == "timeout":
        return TokenizerPreflight(
            ok=False, kind="timeout", source=resolved, network=network, seconds=seconds, detail=detail,
            recovery=RECOVER_RETRY,
            message=(f"Loading the tokenizer did not finish within {int(timeout)} s and was stopped. "
                     "On a slow connection try again with more time, or download the files first."),
            checks=_checks(("ok", f"Accepted: {resolved}."),
                           ("unknown", "Not established before the time limit."),
                           ("failed", "Did not finish within the time limit.")))
    if state in {"unavailable", "crashed"} or document.get("outcome") == "runner-unusable":
        return TokenizerPreflight(
            ok=False, kind="runner-unusable", source=resolved, network=network, seconds=seconds,
            recovery=RECOVER_RUNNER,
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

    if document.get("outcome") == "encode-failed":
        return TokenizerPreflight(
            ok=False, kind="encode-failed", source=resolved, network=network, seconds=seconds,
            recovery=RECOVER_CHOOSE,
            detail=document.get("error", ""),
            message=(f"{resolved} loaded but could not encode a sample sentence, so the runner "
                     "could not build the workload with it. Downloading will not change that: "
                     "choose a different tokenizer source under Advanced: tokenizer."),
            checks=_checks(syntax, ("ok", "Present where the loader looks for them."),
                           ("failed", "Loaded, but encoding a sample sentence failed.")))

    if document.get("outcome") == "loaded":
        identity = document.get("identity") or {}
        downloaded = [] if network != NETWORK_DOWNLOAD else sorted(
            name for name in (document.get("resolved_files") or {}).values() if name)
        return TokenizerPreflight(
            ok=True, kind="loaded", source=resolved, network=network, seconds=seconds,
            identity={"class": identity.get("class"), "vocabulary_size": identity.get("vocabulary_size")},
            revision=document.get("revision"), encoded_tokens=document.get("encoded_tokens"),
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
                                  "execution refused, and used to encode a sample sentence"
                                  + (f" ({document['encoded_tokens']} tokens)."
                                     if document.get("encoded_tokens") else "."))))

    error = document.get("load_error", "")
    files = [name for name in (document.get("repository_files") or document.get("files") or [])
             if isinstance(name, str)]
    suggestion = _suggestion(document, resolved)

    error_types = [name for name in (document.get("load_error_types") or []) if isinstance(name, str)]
    inspect_error = document.get("inspect_error", "")
    inspect_types = [name for name in (document.get("inspect_error_types") or []) if isinstance(name, str)]
    evidence = "\n".join(part for part in (error, inspect_error) if part)
    common = dict(source=resolved, network=network, seconds=seconds, repository_files=files,
                  suggestion=suggestion)

    def failed(kind: str, recovery: str, message: str, files_check: tuple[str, str],
               loaded_check: tuple[str, str]) -> TokenizerPreflight:
        return TokenizerPreflight(ok=False, kind=kind, recovery=recovery, message=message,
                                  detail=evidence, checks=_checks(syntax, files_check, loaded_check),
                                  **common)

    # Refused, whatever else is true: OmoBench never runs repository code.
    if _needs_remote_code(error):
        return failed(
            "needs-remote-code", RECOVER_CHOOSE,
            f"{resolved} ships its own tokenizer code and asks to execute it. OmoBench refuses "
            "that, here and in the benchmark, so downloading will not help. Choose a repository "
            "whose tokenizer loads with the standard implementation, under Advanced: tokenizer.",
            ("ok", "Found."), ("failed", "Refused: loading it would run code from the repository."))

    if _gated(error_types + inspect_types, evidence):
        return failed(
            "gated", RECOVER_ACCESS,
            f"{resolved} is a gated repository: Hugging Face serves its files only to an account "
            "that has accepted its terms. Accept them on the repository's page, sign the "
            "benchmark runner's environment in to Hugging Face (for example with "
            "`hf auth login`), then download the tokenizer files again. Or choose a local "
            "tokenizer folder.",
            ("missing", "Not downloadable without access."), ("failed", "Not loaded: access was refused."))

    if _gguf_only(files):
        return failed(
            "gguf-only", RECOVER_CHOOSE,
            f"{resolved} contains GGUF weights and no Transformers tokenizer "
            f"({', '.join(TOKENIZER_FILENAMES[:3])}…). Your server loads GGUF happily; "
            "the benchmark runner cannot build a workload from it, and downloading will not "
            "add a tokenizer. Enter the repository that publishes the tokenizer instead"
            + (f" — the publisher names {suggestion['source']}." if suggestion else
               " — usually the original, unquantized model the GGUF was converted from."),
            ("missing", "This repository publishes weight files only; it has no "
                        "tokenizer file for the loader to read."),
            ("failed", "Not loaded: there is nothing here to load."))

    if network == NETWORK_NONE:
        cached = document.get("cached_files")
        if local:
            return failed(
                "local-unloadable", RECOVER_CHOOSE,
                f"{resolved} could not be loaded from disk. The folder exists, but no tokenizer "
                "could be built from what is in it"
                + (f" (tokenizer files found: {', '.join(cached)})." if cached else
                   " (it has none of the usual tokenizer files).")
                + " There is nothing to download for a folder: choose a folder that holds the "
                "model's tokenizer files, or a repository.",
                ("ok" if cached else "missing",
                 "Tokenizer files are present." if cached else "No tokenizer file in the folder."),
                ("failed", "Loading from the folder failed."))
        if cached and not _cache_miss(error_types, error):
            return failed(
                "cached-unloadable", RECOVER_CHOOSE,
                f"Tokenizer files for {resolved} are in this machine's cache "
                f"({', '.join(cached)}), but they could not be loaded. The cause could not be "
                "determined from here; the loader's own error is below. Downloading again "
                "reuses the cached files, so it is unlikely to help. Choose a different "
                "tokenizer source under Advanced: tokenizer, or remove this repository from the "
                "Hugging Face cache and download it again.",
                ("ok", "In the local cache."), ("failed", "Present, but the loader could not use it."))
        return failed(
            "not-cached", RECOVER_DOWNLOAD,
            f"{resolved} is not on this machine yet"
            + (" (some of its tokenizer files are cached, not all)" if cached else "")
            + ": its tokenizer files are not in this machine's cache. This check stayed offline, "
            "so it cannot tell whether the repository exists or is "
            "accessible. Download its tokenizer files (a few megabytes, never the model weights) "
            "to continue; the download also finds out whether the repository exists.",
            ("missing", "Only partly in the local cache." if cached else "Not in the local cache."),
            ("failed", "Not attempted over the network: this check stayed offline."))

    # Two separate pieces of evidence: the tokenizer load (which downloads the
    # files) and the repository lookup (model_info). Either can fail on its
    # own -- the files can be refused or cut off while the lookup succeeds --
    # so each is judged on what it actually said, and the lookup's error only
    # counts when the lookup itself failed.
    listed = bool(document.get("repository_listed"))
    lookup_types = [] if listed else inspect_types
    lookup_error = "" if listed else inspect_error
    files_check = (("ok", "The repository was listed.") if listed
                   else ("unknown", "Not established: the repository lookup failed too."))

    if not listed and _not_found(error_types + lookup_types, evidence):
        return failed(
            "not-found", RECOVER_CHOOSE,
            f"Hugging Face has no public repository called {resolved}. It may be misspelled, "
            "not exist, or be private to an account this computer is not signed in with; "
            "Hugging Face answers the same way for all three. Check the name against the "
            "model card, or choose a different tokenizer source under Advanced: tokenizer.",
            ("missing", "Not found on Hugging Face."), ("failed", "Not loaded: nothing to load."))
    if _network_failure(error_types, error) or _network_failure(lookup_types, lookup_error):
        return failed(
            "network", RECOVER_NETWORK,
            (f"{resolved} exists, but its tokenizer files could not be downloaded: the "
             "connection to Hugging Face failed. " if listed else
             f"Hugging Face could not be reached, so {resolved} could not be downloaded. ")
            + "Check this computer's internet connection, proxy or firewall, then try the "
            "download again. The tokenizer source itself is not in question.",
            files_check, ("failed", "Not loaded: the files could not be fetched."))
    if _access_refused(error_types, error) or _access_refused(lookup_types, lookup_error):
        return failed(
            "access-refused", RECOVER_ACCESS,
            (f"{resolved} exists, but Hugging Face refused to serve its tokenizer files. "
             if listed else f"Hugging Face refused access to {resolved}. ")
            + "If the repository is private or gated, sign the benchmark runner's environment "
            "in to Hugging Face with an account that has access, then try the download again; "
            "otherwise choose a different tokenizer source.",
            files_check, ("failed", "Not loaded: access was refused."))
    if not listed:
        return failed(
            "load-failed", RECOVER_CHOOSE,
            f"{resolved} could not be loaded, and the repository could not be looked up. The "
            "cause could not be determined from here; both errors are below. The runner would "
            "fail in the same place, before any request is sent. Check the name and your "
            "connection, or choose a different tokenizer source under Advanced: tokenizer.",
            files_check, ("failed", "AutoTokenizer could not build a tokenizer from this source."))

    return failed(
        "load-failed", RECOVER_CHOOSE,
        f"{resolved} exists, but no tokenizer could be built from it. The runner would fail in "
        "the same place, before any request is sent. The loader's own error is below; choose a "
        "different tokenizer source under Advanced: tokenizer.",
        files_check, ("failed", "AutoTokenizer could not build a tokenizer from this source."))


# What would move a failed check forward. The panel offers a download for
# RECOVER_DOWNLOAD, and a retry of the download after a network or access
# problem has been dealt with; never for the others.
RECOVER_DOWNLOAD = "download"
RECOVER_CHOOSE = "choose-source"
RECOVER_ACCESS = "fix-access"
RECOVER_NETWORK = "fix-network"
RECOVER_RUNNER = "fix-runner"
RECOVER_RETRY = "retry"

_NETWORK_TYPES = {"ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "TimeoutError",
                  "NewConnectionError", "MaxRetryError", "ProxyError", "SSLError", "gaierror",
                  "ConnectError", "NetworkError", "OfflineModeIsEnabled"}
# Only consulted after a load that was allowed to reach the network: offline
# checks return earlier, so "couldn't connect" here means a real attempt failed.
_NETWORK_TEXT = ("couldn't connect to", "failed to resolve", "name or service not known",
                 "temporary failure in name",
                 "max retries exceeded", "connection refused", "connection reset", "timed out",
                 "network is unreachable")


def _needs_remote_code(error: str) -> bool:
    return "trust_remote_code" in error or "custom code" in error.lower()


def _gated(types: list[str], text: str) -> bool:
    # Not merely the word "gated": a repository that does not exist is answered
    # with "If you are trying to access a private or gated repo...", and that is
    # a not-found, not a gate.
    lowered = text.lower()
    return ("GatedRepoError" in types or "trying to access a gated repo" in lowered
            or "awaiting a review" in lowered)


def _not_found(types: list[str], text: str) -> bool:
    return "RepositoryNotFoundError" in types or "repository not found" in text.lower()


def _network_failure(types: list[str], text: str) -> bool:
    lowered = text.lower()
    return bool(_NETWORK_TYPES.intersection(types)) or any(marker in lowered for marker in _NETWORK_TEXT)


def _access_refused(types: list[str], text: str) -> bool:
    # requests raises HTTPError, httpx HTTPStatusError; the hub's text reads
    # "401 Client Error". A status code alone, in arbitrary text, is not enough.
    http = any(name.endswith(("HTTPError", "HTTPStatusError")) for name in types) \
        or "client error" in text.lower() or "HTTPError" in text
    return bool(re.search(r"\b(401|403)\b", text)) and http


def _cache_miss(types: list[str], error: str) -> bool:
    """The offline load failed because a file was absent, not because one was bad."""
    lowered = error.lower()
    return ("LocalEntryNotFoundError" in types or "couldn't find them in the cached files" in lowered
            or "does not appear to have a file named" in lowered or "not cached locally" in lowered)


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
