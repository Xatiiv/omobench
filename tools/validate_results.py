#!/usr/bin/env python3
"""Validate OmoBench result bundles before they enter the canonical registry."""

from __future__ import annotations

import hashlib
import base64
import binascii
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
PROFILES_PATH = ROOT / "benchmarks" / "profiles"
ELIGIBLE_MODALITY = "text-generation"
MUTABLE_REVISION_LABELS = {"main", "master", "latest"}
PROPOSAL_SCHEMA_PATH = ROOT / "schema" / "model-proposal.schema.json"
PROPOSAL_FILENAME = "model-proposal.json"
MANIFEST_REVISION_PREFIX = "sha256-manifest:"
BRIDGE_TRANSPORT = "windows-wsl-stdio-bridge"


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


def load_profile_acceptance(
    profiles_path: Path, profile_id: str, profile_sha256: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Load the publication acceptance thresholds from the exact profile a result claims to use.

    Never hardcodes thresholds: every profile (CPU or GPU, any workload) supplies
    its own 'acceptance' section, matched by profile_id and reconciled against
    profile_sha256 so a since-edited profile file is never used to judge an older
    result. Returns (acceptance_dict, None) on success, or (None, message) when the
    profile is missing, unreadable, hash-mismatched, or missing a usable
    'acceptance' section.
    """
    profile_path = profiles_path / f"{profile_id}.yaml"
    if not profile_path.is_file():
        return None, f"benchmark.profile_id: profile file is missing: {profile_path}"
    try:
        profile_bytes = profile_path.read_bytes()
        actual_hash = "sha256:" + hashlib.sha256(profile_bytes).hexdigest()
    except OSError as error:
        return None, f"benchmark.profile_id: could not read profile file {profile_path}: {error}"
    if actual_hash != profile_sha256:
        # Only repository-reviewed snapshots may supply historical thresholds.
        # Never derive a path from a submitted digest or normalize result bytes.
        profile_bytes = historical_profile_bytes(profiles_path, profile_id, profile_sha256)
        if profile_bytes is None:
            return None, (
                f"benchmark.profile_sha256: does not match the current contents of {profile_path} "
                "or a verified historical snapshot; the profile may have changed since this result was packaged"
            )
    try:
        document = yaml.safe_load(profile_bytes.decode("utf-8"))
    except (yaml.YAMLError, UnicodeError) as error:
        return None, f"benchmark.profile_id: malformed profile YAML at {profile_path} ({error})"
    acceptance = document.get("acceptance") if isinstance(document, dict) else None
    if not isinstance(acceptance, dict):
        return None, f"benchmark.profile_id: profile {profile_path} is missing an 'acceptance' section"
    minimum = acceptance.get("minimum_successful_requests")
    maximum = acceptance.get("maximum_error_rate_percent")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
        return None, (
            f"benchmark.profile_id: profile {profile_path} acceptance.minimum_successful_requests "
            "must be a non-negative integer"
        )
    if isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or maximum < 0:
        return None, (
            f"benchmark.profile_id: profile {profile_path} acceptance.maximum_error_rate_percent "
            "must be a non-negative number"
        )
    return {"minimum_successful_requests": minimum, "maximum_error_rate_percent": maximum}, None


def historical_profile_bytes(profiles_path: Path, profile_id: str, digest: str) -> bytes | None:
    """Resolve an exact, reviewed snapshot; a corrupt/ambiguous ledger fails closed.

    Base64 keeps the historical bytes intact under Git line-ending conversion.
    Acceptance is read from those bytes, never a second hand-maintained table.
    This does not equate different hashes or make them comparable.
    """
    path = profiles_path / "history.json"
    try:
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            return None
        ledger = json.loads(path.read_text(encoding="utf-8"))
        if ledger.get("format_version") != 1 or not isinstance(ledger.get("entries"), list):
            return None
        matches = [entry for entry in ledger["entries"] if isinstance(entry, dict)
                   and entry.get("profile_id") == profile_id and entry.get("sha256") == digest]
        if len(matches) != 1:
            return None
        raw = base64.b64decode(matches[0]["content_base64"], validate=True)
        if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
            return None
        document = yaml.safe_load(raw.decode("utf-8"))
        if not isinstance(document, dict) or document.get("id") != profile_id:
            return None
        return raw
    except (OSError, ValueError, TypeError, KeyError, AttributeError, binascii.Error, yaml.YAMLError):
        return None


def load_proposal_schema() -> dict[str, Any]:
    """The contract a model proposal is held to, kept beside the result schema."""
    return json.loads(PROPOSAL_SCHEMA_PATH.read_text(encoding="utf-8"))


def safe_artifact_path(result_path: Path, artifact_path: str) -> Path | None:
    candidate = (result_path.parent / artifact_path).resolve()
    try:
        candidate.relative_to(result_path.parent.resolve())
    except ValueError:
        return None
    return candidate


def execution_errors(relative: Path, result: dict[str, Any]) -> list[str]:
    """What a run's own execution record has to be consistent about.

    Three separate things, and the registry keeps them separate.

    A measurement taken while the machine was suspended is not a measurement
    of the model. The benchmark cannot tell a paused virtual machine from slow
    inference -- it records the wait as latency -- so a confirmed suspension
    makes the numbers describe the host's power state. That is refused here,
    and it is refused *for a different reason* than falling short of a
    profile's thresholds: the bundle is schema-valid, its evidence is intact,
    and it stays downloadable, inspectable and packageable. It is the
    publishing that stops.

    An unexplained scheduling gap is not refused. Something did not get to run
    for a while, which is a question about a busy machine rather than an
    answer about a suspended one, and treating a question as a verdict would
    quietly throw away good results from loaded hardware.

    And the record has to agree with itself, because a state nobody can check
    against its own evidence is just an assertion.
    """
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return []
    errors: list[str] = []
    interruption = execution.get("interruption") or {}
    state = interruption.get("state")
    events = interruption.get("events") or []
    kinds = {event.get("kind") for event in events if isinstance(event, dict)}
    severity = ["none", "gap", "clock-jump", "suspended"]

    if state in {"suspended", "clock-jump", "gap"} and state not in kinds:
        errors.append(
            f"{relative}: execution.interruption: state is {state!r} but no event of that kind "
            "is recorded; the state has to be derived from the evidence beside it")
    if state == "none" and events:
        errors.append(
            f"{relative}: execution.interruption: state is 'none' but {len(events)} interruption "
            "event(s) are recorded")
    if state in severity:
        worse = [kind for kind in kinds
                 if kind in severity and severity.index(kind) > severity.index(state)]
        if worse:
            errors.append(
                f"{relative}: execution.interruption: state is {state!r} but {sorted(worse)} "
                "is recorded; the worst thing observed is what the state reports")

    seconds = interruption.get("observed_seconds")
    amount = seconds if seconds is not None else "an unrecorded number of"
    if state == "suspended":
        errors.append(
            f"{relative}: interruption: this run was suspended for {amount} seconds while it "
            "was measuring, so its latencies include time the machine was not running; a "
            "suspended measurement is not a measurement of the model and is not published. "
            "The bundle is otherwise valid and stays available for diagnosis")
    elif state == "clock-jump":
        # Deliberately not called a suspension. Two clocks disagreed and the
        # kernel recorded no suspend, which from inside the machine could be
        # either a pause it was never told about or an adjusted wall clock.
        # Either way nothing here establishes that it was running throughout,
        # and a measurement that might contain a pause is not one to publish.
        errors.append(
            f"{relative}: interruption: this run's wall clock and monotonic clock disagreed by "
            f"{amount} seconds while it was measuring, and the kernel recorded no suspend. That "
            "is either a pause this environment was not told about or an adjusted clock, and "
            "nothing here can tell which, so it is not established that the machine was running "
            "throughout. Not published; the bundle stays available for diagnosis")
    return errors


def transport_errors(relative: Path, result: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    """Reconcile the transport a result declares with the transport its evidence recorded.

    A bridged run used to be refused outright, because schema 1.0 has exactly
    one hardware host and no honest way to say that the server was on Windows
    while the runner was in WSL. Schema 1.1 says it, so the refusal becomes a
    reconciliation: the declaration and the environment snapshot written while
    the benchmark ran must agree, in both directions. A result cannot claim a
    bridge its evidence does not show, and evidence of a bridge cannot be
    packaged as though the requests had gone straight to the server.
    """
    recorded = snapshot.get("transport")
    declared = (result.get("environment") or {}).get("transport") or {}
    declared_kind = declared.get("kind")

    if recorded is None:
        if declared_kind == BRIDGE_TRANSPORT:
            return [f"{relative}: transport: this result declares the Windows bridge, but the "
                    "environment snapshot recorded while it ran shows no bridge"]
        return []

    if not isinstance(recorded, dict) or not isinstance(recorded.get("kind"), str):
        return [f"{relative}: transport: the environment snapshot records a transport this "
                "registry cannot interpret"]
    if result.get("schema_version") != "1.1":
        return [f"{relative}: transport: this run went through the Windows bridge, which schema "
                f"{result.get('schema_version')} cannot represent; package it as schema 1.1, "
                "which records the serving host and the runner host separately"]
    if declared_kind != BRIDGE_TRANSPORT:
        return [f"{relative}: transport: the environment snapshot shows a bridged run, but the "
                f"result declares transport {declared_kind!r}"]
    if declared.get("bridge_version") != recorded["kind"]:
        return [f"{relative}: transport: the declared bridge version "
                f"{declared.get('bridge_version')!r} is not the one the run recorded "
                f"({recorded['kind']!r})"]
    return []


def serving_consistency_errors(relative: Path, result: dict[str, Any]) -> list[str]:
    """Keep the 1.1 serving metadata and the 1.0-shaped fields from drifting apart.

    `hardware` has always described what performed inference. In 1.1 the same
    claim is also made, in more detail, by the accelerators marked
    role=inference. Two places that can disagree are one place that cannot be
    trusted, so they are required to say the same thing.
    """
    environment = result.get("environment")
    if not isinstance(environment, dict):
        return []
    errors: list[str] = []
    serving = environment.get("serving") or {}
    accelerators = serving.get("accelerators") or []

    grouped: dict[tuple[Any, Any, Any], int] = {}
    for device in accelerators:
        if not isinstance(device, dict) or device.get("role") != "inference":
            continue
        key = (device.get("vendor"), device.get("model"), device.get("memory_gb"))
        grouped[key] = grouped.get(key, 0) + (device.get("count") or 0)
    inference = sorted(
        ({"vendor": vendor, "model": model, "memory_gb": memory, "count": count}
         for (vendor, model, memory), count in grouped.items()),
        key=lambda entry: (entry["vendor"] or "", entry["model"] or "", entry["memory_gb"] or 0),
    )
    declared = sorted(
        ({"vendor": gpu.get("vendor"), "model": gpu.get("model"),
          "memory_gb": gpu.get("memory_gb"), "count": gpu.get("count")}
         for gpu in result["hardware"]["gpus"]),
        key=lambda entry: (entry["vendor"] or "", entry["model"] or "", entry["memory_gb"] or 0),
    )
    if inference != declared:
        errors.append(
            f"{relative}: environment.serving.accelerators: the devices marked as having performed "
            "inference do not match hardware.gpus; one result cannot describe its serving hardware "
            "two different ways")

    engine = (serving.get("runtime") or {}).get("engine") or {}
    engine_version = engine.get("version")
    if engine_version is not None and engine_version != result["runtime"]["version"]:
        errors.append(
            f"{relative}: environment.serving.runtime.engine.version: {engine_version!r} is not "
            f"runtime.version {result['runtime']['version']!r}. runtime.version is the inference "
            "engine's build; an application release belongs in "
            "environment.serving.runtime.application.version instead")
    return errors


def weight_identity_errors(relative: Path, result: dict[str, Any]) -> list[str]:
    """Hold the weight identities apart and make each one carry its proof.

    A repository commit and a file checksum are not interchangeable, and a
    revision that names neither identifies nothing. When the identity is the
    bytes, the manifest hash is recomputed here from the listed files: a hash
    nobody can reproduce is not an identity.
    """
    model = result["model"]
    revision = model["revision"].strip()
    weights = model.get("weights")
    errors: list[str] = []

    if weights is None:
        if revision.startswith(MANIFEST_REVISION_PREFIX):
            errors.append(
                f"{relative}: model.revision: a {MANIFEST_REVISION_PREFIX} revision has to be "
                "backed by model.weights listing the files it hashes")
        return errors

    if weights.get("identity") == "content-hash":
        content = weights.get("content") or {}
        files = content.get("files") or []
        document = {"manifest_version": "weight-manifest-1",
                    "files": [{"name": entry["name"], "bytes": entry["bytes"],
                               "sha256": entry["sha256"]} for entry in files]}
        recomputed = "sha256:" + hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if recomputed != content.get("manifest_sha256"):
            errors.append(
                f"{relative}: model.weights.content.manifest_sha256: does not hash the file list "
                "it is attached to")
        elif revision != MANIFEST_REVISION_PREFIX + recomputed.split(":", 1)[1]:
            errors.append(
                f"{relative}: model.revision: content-hashed weights are published under "
                f"{MANIFEST_REVISION_PREFIX}<manifest hash>, not {revision!r}")
        names = [entry["name"] for entry in files]
        if len(set(names)) != len(names):
            errors.append(f"{relative}: model.weights.content.files: lists the same file twice")
    else:
        repository_revision = (weights.get("repository_revision") or "").strip()
        if repository_revision.lower() in MUTABLE_REVISION_LABELS:
            errors.append(
                f"{relative}: model.weights.repository_revision: {repository_revision!r} is a "
                "mutable label; pin the commit the weights were taken from")
        elif repository_revision.startswith(MANIFEST_REVISION_PREFIX):
            errors.append(
                f"{relative}: model.weights.repository_revision: a manifest hash is not a "
                "repository commit")
        elif repository_revision != revision:
            errors.append(
                f"{relative}: model.revision: {revision!r} does not match "
                f"model.weights.repository_revision {repository_revision!r}; one weight set has "
                "one revision")
    return errors


def proposal_errors(
    relative: Path,
    result_path: Path,
    result: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    proposal_validator: Draft202012Validator,
    proposed_ids: dict[str, str],
) -> list[str]:
    """Check an uncatalogued model's route to review, without opening it.

    A proposal travels inside the bundle of the run that used the model, so a
    new model can reach review together with its evidence instead of needing a
    hand-written catalog change first. What it never does is make the model
    eligible: catalog/models stays the only source of eligible models, this
    only records that someone has asked, and acceptance remains a separate
    reviewed change.
    """
    model_id = result["model"]["id"]
    status = result["model"].get("catalog_status")
    path = result_path.parent / PROPOSAL_FILENAME
    present = path.is_file() and not path.is_symlink()

    if status != "proposed":
        if present:
            return [f"{relative}: {PROPOSAL_FILENAME} is present, but model.catalog_status is "
                    f"{status!r}; a bundle carrying a proposal must say so"]
        return []
    if model_id in catalog:
        return [f"{relative}: model.catalog_status: '{model_id}' is already in the catalog, so it "
                "cannot be proposed; submit it as a catalogued model"]
    if not present:
        return [f"{relative}: model.catalog_status is 'proposed' but the bundle has no "
                f"{PROPOSAL_FILENAME}; an uncatalogued model needs a proposal a reviewer can read"]
    try:
        if path.stat().st_size > 64 * 1024:
            raise ValueError("too large")
        proposal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"{relative}: {PROPOSAL_FILENAME}: is not readable JSON under 64 KiB"]

    errors = [f"{relative}: {PROPOSAL_FILENAME}: "
              + (".".join(str(part) for part in error.path) or "root") + f": {error.message}"
              for error in sorted(proposal_validator.iter_errors(proposal),
                                  key=lambda item: list(item.path))]
    if errors:
        return errors

    if proposal["proposed_id"] != model_id:
        errors.append(f"{relative}: {PROPOSAL_FILENAME}: proposes "
                      f"{proposal['proposed_id']!r} but the result measured {model_id!r}")
    if proposal["for_run_id"] != result["run_id"]:
        errors.append(f"{relative}: {PROPOSAL_FILENAME}: names run "
                      f"{proposal['for_run_id']!r}, not this result's {result['run_id']!r}")
    if proposal["proposed_by"] != result["submission"]["submitter"]:
        errors.append(f"{relative}: {PROPOSAL_FILENAME}: proposed_by "
                      f"{proposal['proposed_by']!r} is not this bundle's submitter "
                      f"{result['submission']['submitter']!r}")
    previous = proposed_ids.get(proposal["proposed_id"])
    if previous is not None:
        errors.append(f"{relative}: {PROPOSAL_FILENAME}: '{proposal['proposed_id']}' is already "
                      f"proposed by {previous}; review that proposal rather than adding a second")
    else:
        proposed_ids[proposal["proposed_id"]] = str(relative)
    return errors


def validate_result(
    result_path: Path,
    validator: Draft202012Validator,
    catalog: dict[str, dict[str, Any]],
    run_ids: set[str],
    duplicate_keys: set[tuple[str, str]],
    proposal_validator: Draft202012Validator | None = None,
    proposed_ids: dict[str, str] | None = None,
) -> list[str]:
    errors: list[str] = []
    relative = result_path.relative_to(ROOT)
    try:
        result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"{relative}: invalid JSON ({error.msg})"]

    for error in sorted(validator.iter_errors(result), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.path) or "root"
        errors.append(f"{relative}: {location}: {error.message}")
    if errors:
        return errors

    model_id = result["model"]["id"]
    catalog_entry = catalog.get(model_id)
    # An uncatalogued model is eligible to be *reviewed*, never to be published
    # unreviewed: saying so requires schema 1.1 and a proposal in the bundle,
    # both checked below. Everything else about the result is validated either
    # way, so a reviewer reads a complete bundle rather than a rejection.
    proposed = result["model"].get("catalog_status") == "proposed"
    if catalog_entry is None and not proposed:
        errors.append(
            f"{relative}: model.id: '{model_id}' is not present in the model catalog "
            "(catalog/models/*.yaml); only catalog-listed models are eligible for submission. "
            "Package it as schema 1.1 with a model proposal to submit it for review instead"
        )
    elif catalog_entry is not None and catalog_entry.get("modality") != ELIGIBLE_MODALITY:
        errors.append(
            f"{relative}: model.id: '{model_id}' has catalog modality "
            f"'{catalog_entry.get('modality')}', not '{ELIGIBLE_MODALITY}'"
        )

    revision = result["model"]["revision"]
    if revision.strip().lower() in MUTABLE_REVISION_LABELS:
        errors.append(
            f"{relative}: model.revision: '{revision}' is a mutable label; "
            "submit an immutable revision or commit SHA instead of main, master, or latest"
        )
    errors.extend(weight_identity_errors(relative, result))
    errors.extend(serving_consistency_errors(relative, result))
    if proposal_validator is not None:
        errors.extend(proposal_errors(relative, result_path, result, catalog,
                                      proposal_validator, proposed_ids if proposed_ids is not None else {}))

    if errors:
        return errors

    run_id = result["run_id"]
    if run_id in run_ids:
        errors.append(f"{relative}: duplicate run_id {run_id}")
    run_ids.add(run_id)

    raw_hash = result["artifacts"]["raw_runner_output"]["sha256"]
    duplicate_key = (result["configuration_fingerprint"], raw_hash)
    if duplicate_key in duplicate_keys:
        errors.append(
            f"{relative}: duplicate configuration and raw-runner output; "
            "submit a new execution or mark it as a separate replication"
        )
    duplicate_keys.add(duplicate_key)

    for artifact_name, artifact in result["artifacts"].items():
        artifact_path = safe_artifact_path(result_path, artifact["path"])
        if artifact_path is None:
            errors.append(
                f"{relative}: {artifact_name} must stay inside its result directory"
            )
            continue
        if not artifact_path.is_file():
            errors.append(
                f"{relative}: missing {artifact_name} at {artifact['path']}"
            )
            continue
        actual_hash = sha256(artifact_path)
        if actual_hash != artifact["sha256"]:
            errors.append(
                f"{relative}: hash mismatch for {artifact_name}"
            )
        if artifact_name == "environment_snapshot":
            try:
                with artifact_path.open("rb") as source:
                    contents = source.read(1024 * 1024 + 1)
                if len(contents) > 1024 * 1024:
                    raise ValueError("Snapshot too large")
                snapshot = json.loads(contents)
                if not isinstance(snapshot, dict):
                    raise ValueError("Snapshot must be an object")
            except (ValueError, OSError):
                errors.append(f"{relative}: environment_snapshot: cannot establish transport provenance from a readable JSON object under 1 MiB")
            else:
                errors.extend(transport_errors(relative, result, snapshot))

    errors.extend(execution_errors(relative, result))

    # Publication acceptance: distinct from the checks above. A schema-valid,
    # hash-correct bundle with intact evidence still may not meet its own
    # profile's acceptance thresholds; the benchmark having finished does not by
    # itself mean the result is ready to submit. Every profile (CPU or GPU)
    # supplies its own thresholds here; none are hardcoded.
    acceptance, acceptance_error = load_profile_acceptance(
        PROFILES_PATH, result["benchmark"]["profile_id"], result["benchmark"]["profile_sha256"]
    )
    if acceptance_error:
        errors.append(f"{relative}: {acceptance_error}")
    else:
        profile_id = result["benchmark"]["profile_id"]
        succeeded = result["results"]["requests"]["succeeded"]
        error_rate = result["results"]["error_rate_percent"]
        minimum_successful = acceptance["minimum_successful_requests"]
        maximum_error_rate = acceptance["maximum_error_rate_percent"]
        if succeeded < minimum_successful:
            errors.append(
                f"{relative}: acceptance: {succeeded} successful requests is below "
                f"the {profile_id!r} profile's minimum_successful_requests ({minimum_successful}); "
                "the benchmark finished, but this result does not meet submission requirements"
            )
        if error_rate > maximum_error_rate:
            errors.append(
                f"{relative}: acceptance: error rate {error_rate}% exceeds the "
                f"{profile_id!r} profile's maximum_error_rate_percent ({maximum_error_rate}%); "
                "the benchmark finished, but this result does not meet submission requirements"
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
    proposal_validator = Draft202012Validator(load_proposal_schema(), format_checker=FormatChecker())
    result_files = sorted(RESULTS_PATH.rglob("result.json"))
    if not result_files:
        print("No submitted result bundles yet; schema and validator are ready.")
        return 0

    run_ids: set[str] = set()
    duplicate_keys: set[tuple[str, str]] = set()
    proposed_ids: dict[str, str] = {}
    errors: list[str] = []
    for result_path in result_files:
        errors.extend(validate_result(result_path, validator, catalog, run_ids, duplicate_keys,
                                      proposal_validator, proposed_ids))

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Validated {len(result_files)} OmoBench result bundle(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
