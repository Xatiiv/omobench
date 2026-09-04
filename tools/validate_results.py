#!/usr/bin/env python3
"""Validate OmoBench result bundles before they enter the canonical registry."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "benchmark-run.schema.json"
RESULTS_PATH = ROOT / "results"
CATALOG_PATH = ROOT / "catalog" / "models"
ELIGIBLE_MODALITY = "text-generation"
MUTABLE_REVISION_LABELS = {"main", "master", "latest"}


class CatalogError(Exception):
    """Raised when catalog/models/*.yaml cannot be loaded or is malformed."""


def load_model_catalog(catalog_path: Path) -> dict[str, dict[str, Any]]:
    """Load catalog/models/*.yaml into a model id -> catalog entry map.

    catalog/models/*.yaml is the source of truth for eligible submitted
    models; a submitted result whose model.id is absent here, or whose
    catalog modality is not text-generation, is rejected.
    """
    if not catalog_path.is_dir():
        raise CatalogError(f"Model catalog directory is missing: {catalog_path.relative_to(ROOT)}")

    catalog_files = sorted(catalog_path.glob("*.yaml"))
    if not catalog_files:
        raise CatalogError(f"No model catalog files found in {catalog_path.relative_to(ROOT)}")

    models: dict[str, dict[str, Any]] = {}
    for catalog_file in catalog_files:
        relative_path = catalog_file.relative_to(ROOT)
        try:
            document = yaml.safe_load(catalog_file.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise CatalogError(f"{relative_path}: malformed YAML ({error})") from error

        if not isinstance(document, dict):
            raise CatalogError(f"{relative_path}: catalog file must contain a mapping at its root")

        entries = document.get("models")
        if not isinstance(entries, list):
            raise CatalogError(f"{relative_path}: catalog file must contain a 'models' list")

        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise CatalogError(f"{relative_path}: models[{index}] must be a mapping")

            model_id = entry.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise CatalogError(f"{relative_path}: models[{index}] is missing a valid 'id'")

            modality = entry.get("modality")
            if not isinstance(modality, str) or not modality.strip():
                raise CatalogError(f"{relative_path}: model '{model_id}' is missing a valid 'modality'")

            source = entry.get("source")
            if not isinstance(source, str) or not source.strip():
                raise CatalogError(f"{relative_path}: model '{model_id}' is missing a valid 'source'")

            licence = entry.get("licence")
            if not isinstance(licence, str) or not licence.strip():
                raise CatalogError(f"{relative_path}: model '{model_id}' is missing a valid 'licence'")

            if model_id in models:
                raise CatalogError(f"{relative_path}: duplicate catalog model id '{model_id}'")

            models[model_id] = entry

    return models


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def safe_artifact_path(result_path: Path, artifact_path: str) -> Path | None:
    candidate = (result_path.parent / artifact_path).resolve()
    try:
        candidate.relative_to(result_path.parent.resolve())
    except ValueError:
        return None
    return candidate


def validate_result(
    result_path: Path,
    validator: Draft202012Validator,
    catalog: dict[str, dict[str, Any]],
    run_ids: set[str],
    duplicate_keys: set[tuple[str, str]],
) -> list[str]:
    errors: list[str] = []
    try:
        result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"{result_path.relative_to(ROOT)}: invalid JSON ({error.msg})"]

    for error in sorted(validator.iter_errors(result), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.path) or "root"
        errors.append(f"{result_path.relative_to(ROOT)}: {location}: {error.message}")
    if errors:
        return errors

    model_id = result["model"]["id"]
    catalog_entry = catalog.get(model_id)
    if catalog_entry is None:
        errors.append(
            f"{result_path.relative_to(ROOT)}: model.id: '{model_id}' is not present in the model catalog "
            "(catalog/models/*.yaml); only catalog-listed models are eligible for submission"
        )
    elif catalog_entry.get("modality") != ELIGIBLE_MODALITY:
        errors.append(
            f"{result_path.relative_to(ROOT)}: model.id: '{model_id}' has catalog modality "
            f"'{catalog_entry.get('modality')}', not '{ELIGIBLE_MODALITY}'"
        )

    revision = result["model"]["revision"]
    if revision.strip().lower() in MUTABLE_REVISION_LABELS:
        errors.append(
            f"{result_path.relative_to(ROOT)}: model.revision: '{revision}' is a mutable label; "
            "submit an immutable revision or commit SHA instead of main, master, or latest"
        )

    if errors:
        return errors

    run_id = result["run_id"]
    if run_id in run_ids:
        errors.append(f"{result_path.relative_to(ROOT)}: duplicate run_id {run_id}")
    run_ids.add(run_id)

    raw_hash = result["artifacts"]["raw_runner_output"]["sha256"]
    duplicate_key = (result["configuration_fingerprint"], raw_hash)
    if duplicate_key in duplicate_keys:
        errors.append(
            f"{result_path.relative_to(ROOT)}: duplicate configuration and raw-runner output; "
            "submit a new execution or mark it as a separate replication"
        )
    duplicate_keys.add(duplicate_key)

    for artifact_name, artifact in result["artifacts"].items():
        artifact_path = safe_artifact_path(result_path, artifact["path"])
        if artifact_path is None:
            errors.append(
                f"{result_path.relative_to(ROOT)}: {artifact_name} must stay inside its result directory"
            )
            continue
        if not artifact_path.is_file():
            errors.append(
                f"{result_path.relative_to(ROOT)}: missing {artifact_name} at {artifact['path']}"
            )
            continue
        actual_hash = sha256(artifact_path)
        if actual_hash != artifact["sha256"]:
            errors.append(
                f"{result_path.relative_to(ROOT)}: hash mismatch for {artifact_name}"
            )

    return errors


def main() -> int:
    try:
        catalog = load_model_catalog(CATALOG_PATH)
    except CatalogError as error:
        print(f"Model catalog error: {error}", file=sys.stderr)
        return 1

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    result_files = sorted(RESULTS_PATH.rglob("result.json"))
    if not result_files:
        print("No submitted result bundles yet; schema and validator are ready.")
        return 0

    run_ids: set[str] = set()
    duplicate_keys: set[tuple[str, str]] = set()
    errors: list[str] = []
    for result_path in result_files:
        errors.extend(validate_result(result_path, validator, catalog, run_ids, duplicate_keys))

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Validated {len(result_files)} OmoBench result bundle(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
