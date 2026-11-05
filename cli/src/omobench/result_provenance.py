"""Assemble everything schema 1.1 adds, from one declaration document.

Packaging a result under 1.1 needs a lot of separate facts: which machine
served the model, what it served with, which bytes the weights were, which
tokenizer counted the tokens, and -- when the model is not catalogued yet --
the proposal that asks for it to be. Spreading that across a dozen command-line
flags would make every one of them optional and every combination a case
nobody checked.

So it arrives as one JSON document, written by the panel or by hand, and this
module turns it into the result's `environment` object, the additions to
`model`, and the proposal file. A declaration that cannot be turned into honest
provenance is refused here, before anything is written, with a message naming
what is missing.

Schema 1.0 remains exactly what it was. A result is packaged as 1.1 only when
one of these declarations is supplied, or when the run went through the bridge
and therefore cannot be told truthfully in 1.0 at all.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import model_identity, model_proposal, serving_metadata

SCHEMA_VERSION = "1.1"
TOKENIZER_KINDS = ("huggingface", "local-directory", "served-model-default")
TOKENIZER_LOADED = ("loaded-before-run", "not-checked")
MAX_DOCUMENT_BYTES = 256 * 1024
COMMIT = re.compile(r"[0-9a-fA-F]{7,64}\Z")


class ProvenanceError(ValueError):
    """A declaration that cannot be turned into honest provenance."""


def read_declaration(path: Path) -> dict[str, Any]:
    source = Path(path)
    try:
        if source.is_symlink() or source.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ProvenanceError(
                f"{source} must be a regular file under {MAX_DOCUMENT_BYTES // 1024} KiB.")
        document = json.loads(source.read_text(encoding="utf-8"))
    except OSError as error:
        raise ProvenanceError(f"Could not read the provenance declaration: {error}") from error
    except ValueError as error:
        raise ProvenanceError(f"The provenance declaration is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ProvenanceError("The provenance declaration must be a JSON object.")
    unexpected = set(document) - {"serving", "served_model_id", "weights", "tokenizer",
                                  "catalog_status", "proposal", "result"}
    if unexpected:
        raise ProvenanceError(
            f"Unsupported provenance field(s): {', '.join(sorted(unexpected))}.")
    return document


def _weights(declaration: Any, *, revision: str) -> dict[str, Any] | None:
    """The weight set this result measured, by commit or by bytes.

    `revision` is the result's own `model.revision`, and the two are reconciled
    here rather than left to disagree: one weight set has one revision, and
    which kind of thing that revision is depends on which route identified it.
    """
    if declaration is None:
        return None
    if not isinstance(declaration, dict):
        raise ProvenanceError("weights must be an object.")
    unexpected = set(declaration) - {"kind", "repository", "repository_revision", "manifest"}
    if unexpected:
        raise ProvenanceError(f"Unsupported weights field(s): {', '.join(sorted(unexpected))}.")
    kind = declaration.get("kind")
    if kind not in {"repository", "local-files"}:
        raise ProvenanceError("weights.kind must be 'repository' or 'local-files'.")

    if kind == "local-files":
        manifest = declaration.get("manifest")
        if not isinstance(manifest, dict) or "manifest_sha256" not in manifest:
            raise ProvenanceError(
                "Local weights are identified by hashing the files themselves. Supply the "
                "manifest produced by `omobench weights --directory ...`.")
        expected = model_identity.manifest_revision(manifest)
        if revision != expected:
            raise ProvenanceError(
                f"Local weights are published under the revision {expected}, not {revision!r}. "
                "A filename is not an identity, and neither is a revision nobody can check.")
        return {"kind": "local-files", "repository": None, "repository_revision": None,
                "identity": "content-hash",
                "content": {"manifest_sha256": manifest["manifest_sha256"],
                            "files": manifest["files"]}}

    repository = declaration.get("repository")
    repository_revision = (declaration.get("repository_revision") or "").strip()
    if not isinstance(repository, str) or not repository.startswith("https://"):
        raise ProvenanceError("weights.repository must be the https:// URL the bytes came from.")
    if model_identity.is_manifest_revision(repository_revision):
        raise ProvenanceError(
            "A manifest hash is not a repository commit. Record the repository's own commit, or "
            "identify these weights as local files instead.")
    if not COMMIT.fullmatch(repository_revision):
        raise ProvenanceError(
            "weights.repository_revision must be an immutable commit of that repository. A file's "
            "SHA-256 is a different thing and does not belong here.")
    if repository_revision != revision:
        raise ProvenanceError(
            f"model.revision {revision!r} and weights.repository_revision "
            f"{repository_revision!r} disagree. One weight set has one revision.")
    return {"kind": "repository", "repository": repository,
            "repository_revision": repository_revision, "identity": "repository-revision",
            "content": None}


def _tokenizer(declaration: Any, *, served_model_id: str | None) -> dict[str, Any]:
    """Which tokenizer built and counted this workload, and whether it was loaded.

    Recorded on every 1.1 result, because the token numbers being compared are
    this tokenizer's numbers. When no override was given the runner used the
    served identifier, and that is written down as exactly that -- a default,
    not a verified tokenizer source.
    """
    if declaration is None:
        return {"source": served_model_id or "unknown", "kind": "served-model-default",
                "loaded": "not-checked", "matches_served_weights": "not-verifiable-here",
                "identity": None}
    if not isinstance(declaration, dict):
        raise ProvenanceError("tokenizer must be an object.")
    unexpected = set(declaration) - {"source", "kind", "loaded", "identity"}
    if unexpected:
        raise ProvenanceError(f"Unsupported tokenizer field(s): {', '.join(sorted(unexpected))}.")
    source = declaration.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ProvenanceError("tokenizer.source is required.")
    kind = declaration.get("kind")
    if kind not in TOKENIZER_KINDS:
        raise ProvenanceError(f"tokenizer.kind must be one of {', '.join(TOKENIZER_KINDS)}.")
    loaded = declaration.get("loaded", "not-checked")
    if loaded not in TOKENIZER_LOADED:
        raise ProvenanceError(f"tokenizer.loaded must be one of {', '.join(TOKENIZER_LOADED)}.")
    identity = declaration.get("identity")
    if identity is not None:
        if not isinstance(identity, dict) or set(identity) - {"class", "vocabulary_size"}:
            raise ProvenanceError("tokenizer.identity may hold class and vocabulary_size only.")
        vocabulary = identity.get("vocabulary_size")
        if vocabulary is not None and (isinstance(vocabulary, bool)
                                       or not isinstance(vocabulary, int) or vocabulary < 0):
            raise ProvenanceError("tokenizer.identity.vocabulary_size must be a whole number.")
        identity = {"class": identity.get("class"), "vocabulary_size": vocabulary}
    return {"source": source.strip(), "kind": kind, "loaded": loaded,
            # Never derived from anything above: nothing here can establish it.
            "matches_served_weights": "not-verifiable-here", "identity": identity}


def build(
    declaration: dict[str, Any],
    *,
    model_id: str,
    revision: str,
    run_id: str,
    submitter: str,
    runner_report: dict[str, Any],
    transport_provenance: dict[str, Any] | None,
    catalog: list[str],
    execution_device: str,
    allow_probe: bool = True,
) -> dict[str, Any]:
    """Build the 1.1 additions, or explain exactly why they cannot be built.

    Returns the `environment` object, the fields to merge into `model`, the
    `hardware` object derived from the serving side, and the proposal document
    when one is being submitted.
    """
    environment = serving_metadata.build_environment(
        declaration.get("serving"),
        runner_report=runner_report,
        transport_provenance=transport_provenance,
        allow_probe=allow_probe,
    )
    if execution_device == "cpu":
        # The contributor has declared that no accelerator performed inference,
        # and the schema enforces an empty hardware.gpus for a CPU run. A
        # device a probe found is therefore one that did not serve -- which is
        # read from that declaration, not guessed at, and is more use to a
        # reader than leaving its role unresolved beside a CPU-only placement.
        for device in environment["serving"]["accelerators"]:
            if device["role"] == "unknown":
                device["role"] = "present-not-used"
    hardware = serving_metadata.hardware_from_serving(
        environment, execution_device=execution_device)

    served_model_id = declaration.get("served_model_id")
    if served_model_id is not None and (not isinstance(served_model_id, str)
                                        or not served_model_id.strip()):
        raise ProvenanceError("served_model_id must be the identifier your server answers to.")

    catalog_status = declaration.get("catalog_status")
    if catalog_status is None:
        catalog_status = "catalogued" if model_id in set(catalog) else "proposed"
    if catalog_status not in {"catalogued", "proposed"}:
        raise ProvenanceError("catalog_status must be 'catalogued' or 'proposed'.")
    if catalog_status == "catalogued" and model_id not in set(catalog):
        raise ProvenanceError(
            f"{model_id} is not in the OmoBench catalog. Submit a model proposal with this "
            "result, or choose the catalogued model you actually ran. Never substitute a "
            "different, catalogued model for the one you measured.")
    if catalog_status == "proposed" and model_id in set(catalog):
        raise ProvenanceError(
            f"{model_id} is already catalogued, so it does not need a proposal.")

    proposal = None
    if catalog_status == "proposed":
        conflict = model_proposal.conflicts(model_id, catalog)
        if conflict:
            raise ProvenanceError(conflict)
        if declaration.get("proposal") is None:
            raise ProvenanceError(
                f"{model_id} is not catalogued yet. Include a model proposal -- its family, "
                "parameter class, publisher page, licence and why it belongs -- so a reviewer can "
                "consider it alongside this result.")
        proposal = model_proposal.build(declaration["proposal"], model_id=model_id,
                                        run_id=run_id, submitter=submitter)

    model_fields: dict[str, Any] = {"catalog_status": catalog_status}
    if served_model_id:
        model_fields["served_model_id"] = served_model_id.strip()
    weights = _weights(declaration.get("weights"), revision=revision)
    if weights is not None:
        model_fields["weights"] = weights
    model_fields["tokenizer"] = _tokenizer(declaration.get("tokenizer"),
                                           served_model_id=served_model_id or model_id)
    return {"schema_version": SCHEMA_VERSION, "environment": environment,
            "hardware": hardware, "model_fields": model_fields, "proposal": proposal}
