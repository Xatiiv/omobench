"""Propose one uncatalogued model for review, alongside the result that used it.

Before this, a contributor who ran a model OmoBench had not catalogued finished
a valid benchmark and then found there was no way to submit it: the catalog is
the registry's source of truth, and adding to it meant someone hand-writing a
separate change first. The measurement was real and the evidence was complete,
and none of that mattered.

A proposal is the missing step and nothing more than that step. It travels
inside the bundle of the run that used the model, so a reviewer reads the
request and the evidence together. It cannot describe itself as reviewed, it
never becomes a catalog entry on its own, and a result carrying one is marked
`proposed` so nothing downstream mistakes it for an accepted model.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "model-proposal-1.0"
FILENAME = "model-proposal.json"
MODALITY = "text-generation"
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z")
PARAMETER_CLASS = re.compile(r"[0-9]+(\.[0-9]+)?[MB]\Z")
RUNTIMES = ("vllm", "sglang", "llama.cpp", "lmstudio", "openai-compatible-other")
MAX_RATIONALE = 2000


class ProposalError(ValueError):
    """A proposal that could not be built from what was supplied."""


def _text(value: Any, label: str, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProposalError(f"{label} is required.")
    cleaned = " ".join(value.split()) if limit <= 200 else value.strip()
    if len(cleaned) > limit:
        raise ProposalError(f"{label} must be at most {limit} characters.")
    if any(ord(character) < 32 and character not in "\n\t" for character in cleaned):
        raise ProposalError(f"{label} contains control characters.")
    return cleaned


def _https(value: Any, label: str) -> str:
    url = _text(value, label, limit=500)
    if not url.startswith("https://"):
        raise ProposalError(f"{label} must be an https:// URL on the publisher's own site.")
    return url


def build(
    declaration: dict[str, Any],
    *,
    model_id: str,
    run_id: str,
    submitter: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Turn a contributor's declaration into a proposal bound to one result.

    The three fields that bind it -- the model, the run and the account -- are
    taken from the result being packaged rather than from the declaration, so a
    proposal cannot quietly name a different model, a different run, or
    somebody else.
    """
    if not isinstance(declaration, dict):
        raise ProposalError("A model proposal must be a JSON object.")
    unexpected = set(declaration) - {"family", "parameter_class", "source", "licence",
                                     "licence_url", "rationale", "initial_runtimes",
                                     "weights_publicly_downloadable"}
    if unexpected:
        raise ProposalError(f"Unsupported proposal field(s): {', '.join(sorted(unexpected))}.")
    if not MODEL_ID.fullmatch(model_id or ""):
        raise ProposalError(
            f"{model_id!r} is not a canonical model identity. Propose it in publisher/name form, "
            "as the publisher's own repository names it -- not the alias your server answers to.")
    if declaration.get("weights_publicly_downloadable") is not True:
        raise ProposalError(
            "OmoBench publishes results for weights anyone can download. Confirm that these "
            "weights are publicly downloadable, or keep this run as a local report.")

    parameter_class = _text(declaration.get("parameter_class"), "Parameter class", limit=20)
    if not PARAMETER_CLASS.fullmatch(parameter_class):
        raise ProposalError(
            "Parameter class is the publisher's nominal size, such as 14B or 600M.")
    runtimes = declaration.get("initial_runtimes") or []
    if not isinstance(runtimes, list) or len(runtimes) > 10 or any(
            runtime not in RUNTIMES for runtime in runtimes):
        raise ProposalError(f"Initial runtimes must be chosen from {', '.join(RUNTIMES)}.")

    licence_url = declaration.get("licence_url")
    return {
        "schema_version": SCHEMA_VERSION,
        # Never anything else: a submitted proposal is a request for review.
        "status": "proposed",
        "proposed_id": model_id,
        "family": _text(declaration.get("family"), "Model family", limit=100),
        "parameter_class": parameter_class,
        "modality": MODALITY,
        "source": _https(declaration.get("source"), "Model source"),
        "licence": _text(declaration.get("licence"), "Licence", limit=100),
        "licence_url": None if licence_url is None else _https(licence_url, "Licence URL"),
        "weights_publicly_downloadable": True,
        "proposed_by": _text(submitter, "Submitter", limit=100),
        "proposed_at": (now or datetime.now(UTC)).isoformat(),
        "for_run_id": run_id,
        "rationale": _text(declaration.get("rationale"), "Rationale", limit=MAX_RATIONALE),
        "initial_runtimes": list(runtimes),
    }


def conflicts(model_id: str, catalog: dict[str, Any] | list[str]) -> str | None:
    """Why this model cannot be proposed, when it cannot be.

    Separate from building one so the panel can say so before a contributor
    fills in a form that was never going to be accepted.
    """
    catalogued = set(catalog)
    if model_id in catalogued:
        return (f"{model_id} is already in the OmoBench catalog, so it does not need a proposal. "
                "Submit it as a catalogued model.")
    folded = {entry.lower(): entry for entry in catalogued}
    if model_id.lower() in folded:
        return (f"The catalog already has {folded[model_id.lower()]}, which differs from "
                f"{model_id} only in capitalization. Use the catalogued spelling rather than "
                "proposing a second entry for the same model.")
    return None
