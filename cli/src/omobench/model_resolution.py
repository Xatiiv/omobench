"""Which tokenizer, and which files, a served model identifier refers to.

The served identifier is what the server answers to, and it stays what every
inference request sends. The tokenizer is resolved separately, because an
alias such as LM Studio's `qwen2.5-coder-14b-instruct` is not a Hugging Face
repository and the runner cannot load it as one.

The order of evidence, strongest first:

1. A choice this panel already verified for the same model identity.
2. The model file's own metadata -- a GGUF names the repository it was
   converted from. Used automatically when it names exactly one.
3. The name alone -- the curated catalog, or a Hugging Face search for a
   repository of the same name. Offered once, for confirmation, never used on
   its own.

Nothing here downloads anything. A Hugging Face search reads metadata only, and
is reported as skipped rather than failed when the network is unavailable.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

from . import local_models as lm
from .model_identity import suggest_catalog_models

_REPO_ID = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")


def identity_key(served_model_id: str, server_metadata: dict | None = None) -> str:
    """The same model, as far as anything here can tell: its name, format and
    quantisation. Two quantisations of one model share a tokenizer, but the
    key keeps them apart so a remembered choice never crosses an unchecked line."""
    facts = server_metadata or {}
    parts = [lm.served_core(served_model_id),
             str(facts.get("arch") or "").lower(),
             str(facts.get("quantization") or "").upper()]
    return "|".join(parts)


class ChoiceStore:
    """Tokenizer choices that were verified, by model identity."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._load().get(key)
        return entry if isinstance(entry, dict) and entry.get("source") else None

    def put(self, key: str, source: str, *, revision: str | None, basis: str) -> None:
        data = self._load()
        data[key] = {"source": source, "revision": revision, "basis": basis,
                     "verified_at": datetime.now(UTC).isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def huggingface_search(name: str, *, timeout: float = 6.0) -> list[str] | None:
    """Repository ids on Hugging Face whose model name is exactly `name`.

    Metadata only. Returns None when Hugging Face could not be reached, which
    is "not looked", not "nothing found".
    """
    # Most-downloaded first, so an original publisher is listed above its
    # re-uploads. Order is only order: every name match still needs a person.
    url = f"https://huggingface.co/api/models?search={quote(name)}&limit=50&sort=downloads&direction=-1"
    try:
        with urlopen(Request(url, headers={"User-Agent": "omobench-tokenizer-lookup"}),
                     timeout=timeout) as response:
            listing = json.loads(response.read(2 * 1024 * 1024))
    except (OSError, ValueError):
        return None
    wanted = lm.served_core(name)
    found = []
    for entry in listing if isinstance(listing, list) else []:
        if not isinstance(entry, dict):
            continue
        repo = entry.get("id") or entry.get("modelId")
        if not isinstance(repo, str) or not _REPO_ID.match(repo):
            continue
        last = repo.split("/")[1]
        if last.lower().endswith("-gguf") or "gguf" in last.lower():
            continue
        if lm.served_core(last) == wanted:
            found.append(repo)
    return found


def resolve(
    served_model_id: str,
    *,
    server_metadata: dict | None,
    model_root: Path | None,
    choices: ChoiceStore,
    catalog: list[str],
    search: Callable[[str], list[str] | None] = huggingface_search,
) -> dict[str, Any]:
    """What can be established about a served model's tokenizer and files."""
    served = (served_model_id or "").strip()
    key = identity_key(served, server_metadata)
    quantization = (server_metadata or {}).get("quantization")
    answer: dict[str, Any] = {"served_model_id": served, "identity_key": key,
                              "files": {"state": "no-root", "groups": []}, "gguf": None,
                              "tokenizer": {"state": "none", "candidates": []}}

    # Files first: the tokenizer is best read from the file itself.
    if model_root is not None:
        try:
            answer["files"] = lm.find_model_files(model_root, served, quantization=quantization)
            answer["files"]["root"] = str(model_root)
        except ValueError as error:
            answer["files"] = {"state": "unreadable-root", "groups": [], "detail": str(error)}

    remembered = choices.get(key)
    if remembered:
        answer["tokenizer"] = {
            "state": "remembered", "source": remembered["source"],
            "revision": remembered.get("revision"), "needs_confirmation": False,
            "basis": "verified earlier for this model",
            "reason": (f"Verified for this model on {remembered.get('verified_at', 'an earlier run')[:10]}; "
                       "it will be checked again before the run."),
            "candidates": [{"source": remembered["source"], "basis": "remembered"}]}
        return answer

    # The file's own metadata. Every group's header is read, so several
    # quantisations of one model still give one answer when they agree.
    sources: dict[str, dict[str, str]] = {}
    for group in answer["files"].get("groups", [])[:6]:
        if not group.get("complete"):
            continue
        try:
            meta = lm.read_gguf_metadata(Path(model_root) / group["files"][0])
        except (OSError, lm.GGUFError):
            continue
        answer["gguf"] = answer["gguf"] or {name: meta.get(name) for name in (
            "general.name", "general.basename", "general.size_label", "general.architecture",
            "general.base_model.0.repo_url", "general.source.huggingface.repository")}
        for candidate in lm.tokenizer_sources_from_gguf(meta)[:1]:
            sources.setdefault(candidate["source"].lower(), candidate)
    if len(sources) == 1:
        candidate = next(iter(sources.values()))
        answer["tokenizer"] = {
            "state": "metadata", "source": candidate["source"], "needs_confirmation": False,
            "basis": candidate["basis"],
            "reason": (f"The model file says it was converted from {candidate['source']} "
                       f"({candidate['basis'].split(':', 1)[1]}). That repository's tokenizer is used; "
                       "the served identifier is still what every request sends."),
            "candidates": list(sources.values())}
        return answer
    if len(sources) > 1:
        answer["tokenizer"] = {
            "state": "ambiguous", "needs_confirmation": True, "basis": "gguf-metadata",
            "reason": "The model files name more than one origin. Choose the one your server loaded.",
            "candidates": list(sources.values())}
        return answer

    # A served identifier that is itself a repository id: the runner's own
    # default, kept as a candidate that still has to load.
    if _REPO_ID.match(served) and ":" not in served:
        answer["tokenizer"] = {
            "state": "served-id", "source": served, "needs_confirmation": False,
            "basis": "served identifier is a repository id",
            "reason": (f"{served} is in the form of a Hugging Face repository id. It is checked like "
                       "any other source before the run."),
            "candidates": [{"source": served, "basis": "served-id"}]}
        return answer

    # The name alone. Never used without a person saying so.
    named: list[dict[str, str]] = []
    suggestion = suggest_catalog_models(served, catalog)
    for candidate in suggestion.get("candidates") or []:
        named.append({"source": candidate["id"], "basis": "catalog-name"})
    searched = None
    if not named:
        searched = search(served.split("/")[-1].split(":")[0])
        for repo in searched or []:
            named.append({"source": repo, "basis": "huggingface-name"})
    if named:
        answer["tokenizer"] = {
            "state": "name-only", "needs_confirmation": True,
            "source": named[0]["source"] if len(named) == 1 else None,
            "basis": named[0]["basis"] if len(named) == 1 else "name",
            "reason": ("Matched by name only: the model's file could not be read to confirm where it "
                       "came from. Confirm it is the model your server loaded." if len(named) == 1 else
                       "Several repositories share this name. Choose the one your server loaded."),
            "candidates": named[:8]}
        return answer
    answer["tokenizer"] = {
        "state": "none", "needs_confirmation": True, "candidates": [],
        "reason": ("Nothing identifies a tokenizer for this model"
                   + (" and Hugging Face could not be reached" if searched is None else "")
                   + ". Enter one under Advanced, or set your model folder so OmoBench can read the "
                     "model file.")}
    return answer
