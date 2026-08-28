#!/usr/bin/env python3
"""Validate OmoBench result bundles before they enter the canonical registry."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "benchmark-run.schema.json"
RESULTS_PATH = ROOT / "results"


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
        errors.extend(validate_result(result_path, validator, run_ids, duplicate_keys))

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Validated {len(result_files)} OmoBench result bundle(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
