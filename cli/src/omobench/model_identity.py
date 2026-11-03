"""Four identities that get confused with one another, kept apart.

A **served model identifier** is whatever string the inference API answers to.
LM Studio will happily serve `deepseek-r1-distill-qwen-14b`; that is an alias
its own catalog made up, and no part of it is a claim about which weights are
loaded.

A **canonical model identity** is the catalog entry a result is compared under
(`deepseek-ai/DeepSeek-R1-Distill-Qwen-14B`).

A **weight source** is where the bytes actually came from, which is routinely a
different repository from the canonical one -- a quantized re-export, or a
folder on disk.

A **weight revision** pins those bytes: a commit of the source repository, or,
when there is no public immutable revision, a hash over the exact files.

This module maps between the first two without ever doing it silently, and
builds the fourth from bytes rather than from filenames.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

# Tokens that describe a packaging of a model rather than a different model.
# `-instruct`, `-chat` and `-base` are deliberately absent: those name
# different weights, and dropping one would propose a model nobody ran.
FORMAT_TOKENS = (
    "gguf", "ggml", "mlx", "awq", "gptq", "exl2", "exl3", "hqq", "safetensors",
    "int2", "int3", "int4", "int8", "fp4", "fp8", "fp16", "bf16", "f16", "f32",
    "2bit", "3bit", "4bit", "5bit", "6bit", "8bit", "quantized", "quant",
)
QUANTIZATION_TOKEN = re.compile(r"\Aiq?[0-9]+(?:[_-][a-z0-9]+)*\Z|\Aq[0-9]+(?:[_-][a-z0-9]+)*\Z")
SERVING_TAG = re.compile(r":[^:/]+\Z")
WEIGHT_MANIFEST_VERSION = "weight-manifest-1"
MANIFEST_REVISION_PREFIX = "sha256-manifest:"
MAX_WEIGHT_FILES = 128
MAX_WEIGHT_BYTES = 2 * 1024**4  # 2 TiB across the whole selection.

WEIGHT_SUFFIXES = (".gguf", ".safetensors", ".bin", ".pt", ".pth", ".mlx", ".npz")


class ModelIdentityError(ValueError):
    """A model identity that cannot be established from what was supplied."""


def _tokens(name: str) -> list[str]:
    """Split a model name into comparable parts.

    Underscores stay inside a token on purpose: a quantization label is written
    `Q4_K_M`, and splitting it would leave `k` and `m` as parts no rule
    recognises, so the whole name would stop matching. Dots are split, so
    `Llama-3.1` and `Llama-3-1` compare equal -- the same rule runs over the
    catalog side, so both become the same string or neither does.
    """
    return [token for token in re.split(r"[-.\s]+", name.lower()) if token]


def _is_format_token(token: str) -> bool:
    return token in FORMAT_TOKENS or bool(QUANTIZATION_TOKEN.fullmatch(token))


def alias_core(served_model_id: str) -> tuple[str | None, str, list[str]]:
    """Split a served identifier into publisher, comparable name, and the
    packaging tokens that were stripped to get there.

    The stripped tokens are returned rather than discarded so a suggestion can
    say what it ignored. Nothing else is removed: a token this does not
    recognise stays in the name and simply fails to match.
    """
    text = SERVING_TAG.sub("", (served_model_id or "").strip())
    publisher, _, name = text.rpartition("/")
    tokens = _tokens(name)
    stripped: list[str] = []
    while len(tokens) > 1 and _is_format_token(tokens[-1]):
        stripped.insert(0, tokens.pop())
    return (publisher or None), "-".join(tokens), stripped


def catalog_name(model_id: str) -> str:
    return "-".join(_tokens(model_id.rpartition("/")[2]))


def suggest_catalog_models(served_model_id: str, catalog_ids: Iterable[str]) -> dict[str, Any]:
    """Offer catalog entries a served alias may mean, and never choose one.

    The result always has to be confirmed by a person. When exactly one entry
    matches, that is a suggestion worth presenting first; when several do, the
    mapping is ambiguous and the caller must make the contributor pick, because
    picking for them would publish a measurement under a model nobody ran.
    """
    catalog = [model_id for model_id in catalog_ids if isinstance(model_id, str) and model_id]
    served = (served_model_id or "").strip()
    publisher, core, stripped = alias_core(served)
    bare = "-".join(_tokens(SERVING_TAG.sub("", served).rpartition("/")[2]))

    tiers: list[tuple[str, list[dict[str, Any]]]] = []
    exact = [model_id for model_id in catalog if model_id.lower() == served.lower()]
    tiers.append(("exact-id", [
        {"id": model_id, "basis": "exact-id",
         "reason": "The served identifier is this catalog entry, character for character."}
        for model_id in exact]))
    name_matches = [model_id for model_id in catalog
                    if model_id not in exact and catalog_name(model_id) == bare and bare]
    tiers.append(("same-name", [
        {"id": model_id, "basis": "same-name",
         "reason": ("The served identifier is this entry's model name without its publisher."
                    if publisher is None else
                    f"The served identifier names the same model under the publisher {publisher!r}, "
                    "which is where the weights came from rather than the canonical entry.")}
        for model_id in name_matches]))
    packaging = [model_id for model_id in catalog
                 if model_id not in exact and model_id not in name_matches
                 and catalog_name(model_id) == core and core and stripped]
    tiers.append(("same-name-different-packaging", [
        {"id": model_id, "basis": "same-name-different-packaging",
         "reason": ("The served identifier matches this entry once the packaging tokens "
                    + ", ".join(repr(token) for token in stripped)
                    + " are set aside. Those describe a build of the weights, not a different "
                      "model -- confirm that is what you ran.")}
        for model_id in packaging]))

    candidates: list[dict[str, Any]] = []
    basis = "none"
    for tier_name, tier in tiers:
        if tier:
            candidates, basis = tier, tier_name
            break

    ambiguous = len(candidates) > 1
    if not candidates:
        message = (
            f"No catalog entry matches the served identifier {served!r}. Choose the canonical "
            "model yourself, or propose it for the catalog with this result.")
    elif ambiguous:
        message = (
            f"The served identifier {served!r} matches more than one catalog entry "
            f"({', '.join(candidate['id'] for candidate in candidates)}). Select the one you "
            "actually ran; OmoBench will not choose between them.")
    else:
        message = (
            f"The served identifier {served!r} looks like {candidates[0]['id']}. Confirm it before "
            "packaging: a serving alias is not proof of which weights were loaded.")
    return {
        "served_model_id": served,
        "basis": basis,
        "candidates": candidates,
        "ambiguous": ambiguous,
        # There is no tier at which OmoBench fills this in by itself.
        "requires_selection": True,
        "message": message,
    }


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return f"sha256:{digest.hexdigest()}", size


def weight_manifest(paths: Iterable[Path], *, root: Path | None = None) -> dict[str, Any]:
    """Identify a weight set by the bytes of the files it is made of.

    For weights with no public immutable revision -- a local conversion, a
    quantization someone produced themselves, a sharded set spread over a dozen
    files -- this is the identity. Every file is read and hashed; a name is
    never evidence of content. The manifest hash covers the whole ordered set,
    so one shard differing makes a different weight set rather than the same
    one with a footnote.
    """
    selected = [Path(path) for path in paths]
    if not selected:
        raise ModelIdentityError("Select at least one weight file to identify.")
    if len(selected) > MAX_WEIGHT_FILES:
        raise ModelIdentityError(
            f"At most {MAX_WEIGHT_FILES} weight files can be hashed; this selection has "
            f"{len(selected)}.")
    base = Path(root).resolve() if root is not None else None
    entries: list[dict[str, Any]] = []
    total = 0
    seen: set[str] = set()
    for path in selected:
        resolved = path.resolve()
        if base is not None and not resolved.is_relative_to(base):
            raise ModelIdentityError(f"{path} is outside the selected weights folder.")
        if resolved.is_symlink() or not resolved.is_file():
            raise ModelIdentityError(f"{path} is not a regular file.")
        name = resolved.name if base is None else resolved.relative_to(base).as_posix()
        if name in seen:
            raise ModelIdentityError(f"{name} was selected twice.")
        seen.add(name)
        digest, size = _hash_file(resolved)
        total += size
        if total > MAX_WEIGHT_BYTES:
            raise ModelIdentityError("The selected weight files exceed the supported total size.")
        entries.append({"name": name, "bytes": size, "sha256": digest})
    entries.sort(key=lambda entry: entry["name"])
    document = {"manifest_version": WEIGHT_MANIFEST_VERSION, "files": entries}
    manifest_sha256 = "sha256:" + hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"manifest_sha256": manifest_sha256, "files": entries}


def manifest_revision(content: dict[str, Any]) -> str:
    """The revision string a content-hashed weight set is published under.

    Deliberately in its own `sha256-manifest:` namespace: a reader must never
    be able to mistake it for a repository commit, and a repository commit must
    never be accepted where this belongs.
    """
    digest = content["manifest_sha256"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest or ""):
        raise ModelIdentityError("The weight manifest hash is not a SHA-256 digest.")
    return MANIFEST_REVISION_PREFIX + digest.split(":", 1)[1]


def is_manifest_revision(revision: str) -> bool:
    return bool(re.fullmatch(MANIFEST_REVISION_PREFIX + r"[a-f0-9]{64}", (revision or "").strip()))


def weight_files_in(directory: Path) -> list[Path]:
    """Weight files a contributor is likely to want to select, for listing only.

    This suggests a selection; it does not make one, and nothing is hashed
    until the contributor confirms which files are theirs.
    """
    base = Path(directory)
    if not base.is_dir():
        raise ModelIdentityError(f"{directory} is not a folder.")
    found = []
    for path in sorted(base.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() in WEIGHT_SUFFIXES:
            found.append(path)
    return found
