from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import validate_results  # noqa: E402  (import after sys.path setup)


def sha256_of(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


VALID_RESULT_TEMPLATE = {
    "schema_version": "1.0",
    "run_id": "run-fixture-1234",
    "started_at": "2026-08-30T12:00:00Z",
    "configuration_fingerprint": "sha256:" + "a" * 64,
    "model": {"id": "Qwen/Qwen3-4B", "revision": "abc123", "quantization": "BF16"},
    "runtime": {
        "framework": "vllm",
        "version": "0.11.0",
        "arguments": ["--max-model-len=4096"],
        "endpoint_type": "openai-compatible",
    },
    "hardware": {
        "os": "Test Linux",
        "cpu": "Test CPU",
        "ram_gb": 64,
        "gpus": [{"model": "NVIDIA Test GPU", "memory_gb": 24, "count": 1}],
        "driver_version": "555.1",
        "detection": {"hardware": "user-confirmed", "model": "user-confirmed", "runtime": "auto"},
    },
    "benchmark": {
        "profile_id": "chat-small-v1",
        "profile_sha256": None,  # filled in below once the fixture profile file's real hash is known
        "runner": "guidellm",
        "runner_version": "0.7.3",
    },
    "results": {
        "requests": {"attempted": 50, "succeeded": 49, "failed": 1},
        "latency_ms": {
            "ttft": {"p50": 210.0, "p95": 315.0},
            "inter_token": {"p50": 12.5, "p95": 18.0},
        },
        "output_tokens_per_second": 87.5,
        "total_tokens_per_second": 220.0,
        "requests_per_second": 3.2,
        "error_rate_percent": 2.0,
    },
    "artifacts": {
        "raw_runner_output": {"path": "guidellm-benchmarks.json.gz", "sha256": ""},
        "runner_stdout_log": {"path": "guidellm.stdout.log", "sha256": ""},
        "environment_snapshot": {"path": "environment.json", "sha256": ""},
        "reproduce_markdown": {"path": "reproduce.md", "sha256": ""},
    },
    "submission": {
        "submitter": "local-tester",
        "run_kind": "initial",
        "repeat_of_run_id": None,
        "source_commit": None,
    },
}

# A fixture profile, deliberately permissive so VALID_RESULT_TEMPLATE's own
# results (49 succeeded, 2.0% errors) satisfy it; specific acceptance-boundary
# tests below write their own tighter profile fixtures instead of relying on this.
DEFAULT_PROFILE_YAML = (
    "id: chat-small-v1\n"
    "acceptance:\n"
    "  minimum_successful_requests: 40\n"
    "  maximum_error_rate_percent: 5\n"
)
DEFAULT_PROFILE_SHA256 = sha256_of(DEFAULT_PROFILE_YAML.encode())
VALID_RESULT_TEMPLATE["benchmark"]["profile_sha256"] = DEFAULT_PROFILE_SHA256


def write_profile(profiles_dir: Path, profile_id: str, contents: str) -> str:
    """Write a fixture profile YAML and return its sha256 (matching benchmark.profile_sha256)."""
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / f"{profile_id}.yaml").write_text(contents, encoding="utf-8")
    return sha256_of(contents.encode())


ARTIFACT_CONTENTS = {
    "raw_runner_output": gzip.compress(b'{"metadata": {"version": 2}}'),
    "runner_stdout_log": b"completed\n",
    "environment_snapshot": b'{"operating_system": "Test Linux"}',
    "reproduce_markdown": b"# Reproduce\n",
}


DEFAULT_CATALOG = """
catalog_version: 1
models:
  - id: Qwen/Qwen3-4B
    family: Qwen
    modality: text-generation
    source: https://huggingface.co/Qwen/Qwen3-4B
    licence: Apache-2.0
"""


def write_catalog(catalog_dir: Path, contents: str = DEFAULT_CATALOG, filename: str = "seed-v1.yaml") -> None:
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / filename).write_text(contents, encoding="utf-8")


def write_bundle(
    bundle_dir: Path,
    *,
    result_overrides: dict | None = None,
    artifact_contents: dict[str, bytes] | None = None,
    write_artifacts: set[str] | None = None,
) -> None:
    """Write a result.json plus its evidence files into bundle_dir."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    contents = {**ARTIFACT_CONTENTS, **(artifact_contents or {})}
    names_to_write = write_artifacts if write_artifacts is not None else set(contents)

    result = copy.deepcopy(VALID_RESULT_TEMPLATE)
    for name, artifact in result["artifacts"].items():
        artifact["sha256"] = sha256_of(contents[name])
    if result_overrides:
        result.update(result_overrides)

    (bundle_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    for name, artifact in VALID_RESULT_TEMPLATE["artifacts"].items():
        if name in names_to_write:
            (bundle_dir / artifact["path"]).write_bytes(contents[name])


class ValidateResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_directory.name)
        self.results_path = self.root / "results"
        self.catalog_path = self.root / "catalog" / "models"
        self.profiles_path = self.root / "benchmarks" / "profiles"
        self.addCleanup(self._temp_directory.cleanup)

        patcher_root = patch.object(validate_results, "ROOT", self.root)
        patcher_results = patch.object(validate_results, "RESULTS_PATH", self.results_path)
        patcher_catalog = patch.object(validate_results, "CATALOG_PATH", self.catalog_path)
        patcher_profiles = patch.object(validate_results, "PROFILES_PATH", self.profiles_path)
        patcher_root.start()
        patcher_results.start()
        patcher_catalog.start()
        patcher_profiles.start()
        self.addCleanup(patcher_root.stop)
        self.addCleanup(patcher_results.stop)
        self.addCleanup(patcher_catalog.stop)
        self.addCleanup(patcher_profiles.stop)

        # A valid default catalog so tests unrelated to catalog behavior keep passing.
        write_catalog(self.catalog_path)
        # A permissive default profile so tests unrelated to acceptance keep passing.
        write_profile(self.profiles_path, "chat-small-v1", DEFAULT_PROFILE_YAML)

    def run_main(self) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = validate_results.main()
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_empty_registry_succeeds(self) -> None:
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0)
        self.assertIn("No submitted result bundles yet", stdout)

    def test_valid_bundle_passes(self) -> None:
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234")
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_schema_violation_is_reported(self) -> None:
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"schema_version": "2.0"},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("schema_version", stderr)

    def test_missing_artifact_file_is_reported(self) -> None:
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            write_artifacts={"runner_stdout_log", "environment_snapshot", "reproduce_markdown"},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("missing raw_runner_output", stderr)

    def test_hash_mismatch_is_reported(self) -> None:
        bundle_dir = self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234"
        write_bundle(bundle_dir)
        # Tamper with an evidence file after its hash was recorded.
        (bundle_dir / "guidellm.stdout.log").write_bytes(b"tampered\n")
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("hash mismatch for runner_stdout_log", stderr)

    def test_path_escaping_the_bundle_is_rejected(self) -> None:
        bundle_dir = self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234"
        outside_file = self.results_path / "qwen3-4b" / "outside.log"
        outside_file.parent.mkdir(parents=True, exist_ok=True)
        outside_file.write_bytes(b"not part of the bundle\n")

        result = copy.deepcopy(VALID_RESULT_TEMPLATE)
        for name, artifact in result["artifacts"].items():
            artifact["sha256"] = sha256_of(ARTIFACT_CONTENTS[name])
        result["artifacts"]["runner_stdout_log"] = {
            "path": "../outside.log",
            "sha256": sha256_of(outside_file.read_bytes()),
        }
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
        for name, artifact in VALID_RESULT_TEMPLATE["artifacts"].items():
            if name != "runner_stdout_log":
                (bundle_dir / artifact["path"]).write_bytes(ARTIFACT_CONTENTS[name])

        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("must stay inside its result directory", stderr)

    def test_absolute_artifact_path_is_rejected(self) -> None:
        bundle_dir = self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234"
        escape_target = self.root / "secret.txt"
        escape_target.write_bytes(b"outside the registry\n")
        result = copy.deepcopy(VALID_RESULT_TEMPLATE)
        artifacts = result["artifacts"]
        for name, artifact in artifacts.items():
            artifact["sha256"] = sha256_of(ARTIFACT_CONTENTS[name])
        artifacts["reproduce_markdown"] = {
            "path": str(escape_target),
            "sha256": sha256_of(escape_target.read_bytes()),
        }
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
        for name, artifact in VALID_RESULT_TEMPLATE["artifacts"].items():
            if name != "reproduce_markdown":
                (bundle_dir / artifact["path"]).write_bytes(ARTIFACT_CONTENTS[name])

        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("must stay inside its result directory", stderr)

    def test_duplicate_run_id_is_reported(self) -> None:
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"configuration_fingerprint": "sha256:" + "a" * 64},
            artifact_contents={"raw_runner_output": gzip.compress(b'{"a": 1}')},
        )
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-31-run-fixture-1234",
            result_overrides={"configuration_fingerprint": "sha256:" + "c" * 64},
            artifact_contents={"raw_runner_output": gzip.compress(b'{"a": 2}')},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("duplicate run_id", stderr)

    def test_duplicate_configuration_and_raw_output_is_reported(self) -> None:
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234")
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-31-run-fixture-5678",
            result_overrides={"run_id": "run-fixture-5678"},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("duplicate configuration and raw-runner output", stderr)

    def test_eligible_catalog_model_passes(self) -> None:
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234")
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_unknown_model_is_rejected(self) -> None:
        write_bundle(
            self.results_path / "unknown-model" / "2026-08-30-run-fixture-1234",
            result_overrides={
                "model": {"id": "not-in-catalog/Model", "revision": "abc123", "quantization": "BF16"}
            },
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("is not present in the model catalog", stderr)

    def test_non_text_generation_model_is_rejected(self) -> None:
        write_catalog(
            self.catalog_path,
            contents="""
catalog_version: 1
models:
  - id: Qwen/Qwen3-4B
    family: Qwen
    modality: text-generation
    source: https://huggingface.co/Qwen/Qwen3-4B
    licence: Apache-2.0
  - id: some-org/vision-model
    family: Other
    modality: image-generation
    source: https://huggingface.co/some-org/vision-model
    licence: Apache-2.0
""",
        )
        write_bundle(
            self.results_path / "vision-model" / "2026-08-30-run-fixture-1234",
            result_overrides={
                "model": {"id": "some-org/vision-model", "revision": "abc123", "quantization": "BF16"}
            },
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("catalog modality", stderr)
        self.assertIn("not 'text-generation'", stderr)

    def test_malformed_catalog_yaml_is_reported(self) -> None:
        write_catalog(self.catalog_path, contents="models: [this is: not: valid: yaml")
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("Model catalog error", stderr)
        self.assertIn("malformed YAML", stderr)

    def test_missing_catalog_directory_is_reported(self) -> None:
        shutil.rmtree(self.catalog_path)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("Model catalog error", stderr)
        self.assertIn("Model catalog directory is missing", stderr)

    def test_mutable_revision_labels_are_rejected(self) -> None:
        for mutable_label in ("main", "master", "latest", "MAIN"):
            with self.subTest(revision=mutable_label):
                bundle_dir = self.results_path / "qwen3-4b" / f"2026-08-30-run-{mutable_label.lower()}"
                write_bundle(
                    bundle_dir,
                    result_overrides={
                        "run_id": f"run-fixture-{mutable_label.lower()}",
                        "model": {"id": "Qwen/Qwen3-4B", "revision": mutable_label, "quantization": "BF16"},
                    },
                )
                exit_code, _, stderr = self.run_main()
                self.assertEqual(exit_code, 1)
                self.assertIn("is a mutable label", stderr)

    def test_openai_compatible_other_without_server_description_is_rejected(self) -> None:
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={
                "runtime": {
                    "framework": "openai-compatible-other",
                    "version": "1.0.0",
                    "arguments": [],
                    "endpoint_type": "openai-compatible",
                }
            },
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("server_description", stderr)

    def test_openai_compatible_other_with_blank_server_description_is_rejected(self) -> None:
        for blank_value in ("", "   "):
            with self.subTest(server_description=repr(blank_value)):
                run_id = f"run-fixture-blank-{len(blank_value)}"
                write_bundle(
                    self.results_path / "qwen3-4b" / f"2026-08-30-{run_id}",
                    result_overrides={
                        "run_id": run_id,
                        "runtime": {
                            "framework": "openai-compatible-other",
                            "version": "1.0.0",
                            "arguments": [],
                            "endpoint_type": "openai-compatible",
                            "server_description": blank_value,
                        },
                    },
                )
                exit_code, _, stderr = self.run_main()
                self.assertEqual(exit_code, 1)
                self.assertIn("server_description", stderr)

    def test_openai_compatible_other_with_server_description_is_accepted(self) -> None:
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={
                "runtime": {
                    "framework": "openai-compatible-other",
                    "version": "1.0.0",
                    "arguments": [],
                    "endpoint_type": "openai-compatible",
                    "server_description": "Custom internal OpenAI-compatible gateway",
                }
            },
        )
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_duplicate_model_id_across_catalog_files_is_rejected(self) -> None:
        write_catalog(
            self.catalog_path,
            contents="""
catalog_version: 1
models:
  - id: Qwen/Qwen3-4B
    family: Qwen
    modality: text-generation
    source: https://huggingface.co/Qwen/Qwen3-4B
    licence: Apache-2.0
""",
            filename="seed-v1.yaml",
        )
        write_catalog(
            self.catalog_path,
            contents="""
catalog_version: 1
models:
  - id: Qwen/Qwen3-4B
    family: Qwen
    modality: text-generation
    source: https://huggingface.co/Qwen/Qwen3-4B
    licence: Apache-2.0
""",
            filename="seed-v2.yaml",
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("Model catalog error", stderr)
        self.assertIn("duplicate catalog model id", stderr)

    def test_catalog_entry_missing_id_is_rejected(self) -> None:
        write_catalog(
            self.catalog_path,
            contents="""
catalog_version: 1
models:
  - family: Qwen
    modality: text-generation
    source: https://huggingface.co/Qwen/Qwen3-4B
    licence: Apache-2.0
""",
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("Model catalog error", stderr)
        self.assertIn("missing a valid 'id'", stderr)

    def test_catalog_entry_empty_id_is_rejected(self) -> None:
        write_catalog(
            self.catalog_path,
            contents="""
catalog_version: 1
models:
  - id: "  "
    family: Qwen
    modality: text-generation
    source: https://huggingface.co/Qwen/Qwen3-4B
    licence: Apache-2.0
""",
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("Model catalog error", stderr)
        self.assertIn("missing a valid 'id'", stderr)

    def test_catalog_entry_missing_or_empty_modality_source_licence_is_rejected(self) -> None:
        base_entry = {
            "id": "Qwen/Qwen3-4B",
            "family": "Qwen",
            "modality": "text-generation",
            "source": "https://huggingface.co/Qwen/Qwen3-4B",
            "licence": "Apache-2.0",
        }
        for missing_field in ("modality", "source", "licence"):
            for bad_value, label in ((None, "missing"), ("   ", "empty")):
                with self.subTest(field=missing_field, variant=label):
                    entry = dict(base_entry)
                    if bad_value is None:
                        del entry[missing_field]
                    else:
                        entry[missing_field] = bad_value
                    contents = yaml.safe_dump({"catalog_version": 1, "models": [entry]})
                    write_catalog(self.catalog_path, contents=contents)
                    exit_code, _, stderr = self.run_main()
                    self.assertEqual(exit_code, 1)
                    self.assertIn("Model catalog error", stderr)
                    self.assertIn(f"missing a valid '{missing_field}'", stderr)

    def test_gpu_vendor_amd_is_accepted(self) -> None:
        hardware = copy.deepcopy(VALID_RESULT_TEMPLATE["hardware"])
        hardware["gpus"][0]["vendor"] = "amd"
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"hardware": hardware},
        )
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_invalid_gpu_vendor_is_rejected(self) -> None:
        hardware = copy.deepcopy(VALID_RESULT_TEMPLATE["hardware"])
        hardware["gpus"][0]["vendor"] = "not-a-real-vendor"
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"hardware": hardware},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("vendor", stderr)

    def test_legacy_bundle_without_execution_device_still_passes(self) -> None:
        """A result.json published before execution-device tracking has no
        execution_device field at all and must remain valid."""
        result_overrides = {}
        self.assertNotIn("execution_device", VALID_RESULT_TEMPLATE)
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides=result_overrides,
        )
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_cpu_execution_device_with_empty_gpus_and_null_driver_is_accepted(self) -> None:
        hardware = copy.deepcopy(VALID_RESULT_TEMPLATE["hardware"])
        hardware["gpus"] = []
        hardware["driver_version"] = None
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"execution_device": "cpu", "hardware": hardware},
        )
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_cpu_execution_device_may_not_list_a_gpu(self) -> None:
        """A cpu run must not represent an installed-but-unused GPU as having performed inference."""
        hardware = copy.deepcopy(VALID_RESULT_TEMPLATE["hardware"])
        hardware["driver_version"] = None
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"execution_device": "cpu", "hardware": hardware},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("hardware.gpus", stderr)

    def test_cpu_execution_device_may_not_carry_a_driver_version(self) -> None:
        hardware = copy.deepcopy(VALID_RESULT_TEMPLATE["hardware"])
        hardware["gpus"] = []
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"execution_device": "cpu", "hardware": hardware},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("hardware.driver_version", stderr)

    def test_gpu_execution_device_still_requires_at_least_one_gpu(self) -> None:
        hardware = copy.deepcopy(VALID_RESULT_TEMPLATE["hardware"])
        hardware["gpus"] = []
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"execution_device": "gpu", "hardware": hardware},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("hardware.gpus", stderr)

    def test_invalid_execution_device_value_is_rejected(self) -> None:
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"execution_device": "tpu"},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("execution_device", stderr)

    def test_model_source_is_accepted_alongside_canonical_model_id(self) -> None:
        """model.id stays the canonical catalog identity; model.source records the
        actual weights source and is accepted as additional, optional metadata."""
        model = copy.deepcopy(VALID_RESULT_TEMPLATE["model"])
        model["source"] = "https://huggingface.co/ggml-org/Qwen3-4B-GGUF/resolve/abc123/Qwen3-4B-Q4_K_M.gguf"
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"model": model},
        )
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_bundle_without_model_source_remains_valid(self) -> None:
        """A result.json that predates model.source, or simply omits it, must
        keep validating exactly as before."""
        self.assertNotIn("source", VALID_RESULT_TEMPLATE["model"])
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234")
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_cpu_and_gpu_bundles_with_same_raw_output_are_not_duplicates(self) -> None:
        """Configuration identity distinguishes cpu and gpu; the fingerprint values used
        here differ, as omobench package itself would produce for otherwise-identical runs."""
        cpu_hardware = copy.deepcopy(VALID_RESULT_TEMPLATE["hardware"])
        cpu_hardware["gpus"] = []
        cpu_hardware["driver_version"] = None
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={
                "execution_device": "cpu",
                "hardware": cpu_hardware,
                "configuration_fingerprint": "sha256:" + "a" * 64,
            },
        )
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-31-run-fixture-5678",
            result_overrides={
                "run_id": "run-fixture-5678",
                "execution_device": "gpu",
                "configuration_fingerprint": "sha256:" + "d" * 64,
            },
        )
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0)
        self.assertIn("Validated 2 OmoBench result bundle(s).", stdout)

    def test_legacy_gpu_bundle_and_newly_packaged_equivalent_gpu_run_are_duplicates(self) -> None:
        """A GPU bundle predating execution_device tracking and a freshly packaged,
        otherwise-identical GPU run of the same raw-runner output must still collide
        under the duplicate-configuration check, even though only the new one carries
        execution_device. This models the real compatibility requirement: cli.py's
        fingerprint algorithm for a gpu run is unchanged, so both bundles legitimately
        share the same configuration_fingerprint value in practice."""
        shared_fingerprint = "sha256:" + "e" * 64
        shared_raw_output = gzip.compress(b'{"a": "same-config"}')
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-legacy-0001",
            result_overrides={
                "run_id": "run-legacy-0001",
                "configuration_fingerprint": shared_fingerprint,
                # No execution_device key at all: this predates the field.
            },
            artifact_contents={"raw_runner_output": shared_raw_output},
        )
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-31-run-new-0002",
            result_overrides={
                "run_id": "run-new-0002",
                "execution_device": "gpu",
                "configuration_fingerprint": shared_fingerprint,
            },
            artifact_contents={"raw_runner_output": shared_raw_output},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("duplicate configuration and raw-runner output", stderr)

    def test_invalid_models_structure_is_rejected(self) -> None:
        cases = {
            "root_not_a_mapping": "- just\n- a\n- list\n",
            "models_not_a_list": "catalog_version: 1\nmodels: not-a-list\n",
            "entry_not_a_mapping": "catalog_version: 1\nmodels:\n  - just-a-string\n",
        }
        for case_name, contents in cases.items():
            with self.subTest(case=case_name):
                write_catalog(self.catalog_path, contents=contents)
                exit_code, _, stderr = self.run_main()
                self.assertEqual(exit_code, 1)
                self.assertIn("Model catalog error", stderr)


class AcceptanceValidationTests(unittest.TestCase):
    """Every profile (CPU or GPU, any workload) supplies its own acceptance
    thresholds; nothing here is hardcoded to a specific profile's numbers."""

    def setUp(self) -> None:
        self._temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_directory.name)
        self.results_path = self.root / "results"
        self.catalog_path = self.root / "catalog" / "models"
        self.profiles_path = self.root / "benchmarks" / "profiles"
        self.addCleanup(self._temp_directory.cleanup)

        for name, path in [
            ("ROOT", self.root),
            ("RESULTS_PATH", self.results_path),
            ("CATALOG_PATH", self.catalog_path),
            ("PROFILES_PATH", self.profiles_path),
        ]:
            patcher = patch.object(validate_results, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)

        write_catalog(self.catalog_path)

    def run_main(self) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = validate_results.main()
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def write_result(self, *, succeeded: int, attempted: int, error_rate_percent: float, execution_device: str,
                      profile_yaml: str, run_id: str = "run-fixture-1234") -> None:
        profile_sha256 = write_profile(self.profiles_path, "chat-small-v1", profile_yaml)
        hardware = copy.deepcopy(VALID_RESULT_TEMPLATE["hardware"])
        overrides: dict = {
            "run_id": run_id,
            "execution_device": execution_device,
            "benchmark": {**VALID_RESULT_TEMPLATE["benchmark"], "profile_sha256": profile_sha256},
            "results": {
                **VALID_RESULT_TEMPLATE["results"],
                "requests": {"attempted": attempted, "succeeded": succeeded, "failed": attempted - succeeded},
                "error_rate_percent": error_rate_percent,
            },
        }
        if execution_device == "cpu":
            hardware["gpus"] = []
            hardware["driver_version"] = None
            overrides["hardware"] = hardware
        write_bundle(self.results_path / "qwen3-4b" / f"2026-08-30-{run_id}", result_overrides=overrides)

    BOUNDARY_PROFILE = (
        "id: chat-small-v1\n"
        "acceptance:\n"
        "  minimum_successful_requests: 40\n"
        "  maximum_error_rate_percent: 1\n"
    )

    def test_cpu_bundle_meeting_requirements_passes(self) -> None:
        self.write_result(
            succeeded=45, attempted=45, error_rate_percent=0.0, execution_device="cpu",
            profile_yaml=self.BOUNDARY_PROFILE,
        )
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0, stdout)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_gpu_bundle_meeting_requirements_passes(self) -> None:
        self.write_result(
            succeeded=45, attempted=45, error_rate_percent=0.0, execution_device="gpu",
            profile_yaml=self.BOUNDARY_PROFILE,
        )
        exit_code, stdout, _ = self.run_main()
        self.assertEqual(exit_code, 0, stdout)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_exactly_the_minimum_successful_requests_passes(self) -> None:
        """Exactly 40 successes against minimum_successful_requests: 40 must pass, not fail."""
        self.write_result(
            succeeded=40, attempted=40, error_rate_percent=0.0, execution_device="cpu",
            profile_yaml=self.BOUNDARY_PROFILE,
        )
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)

    def test_exactly_the_maximum_error_rate_passes(self) -> None:
        """Exactly 1% errors against maximum_error_rate_percent: 1 must pass, not fail."""
        self.write_result(
            succeeded=40, attempted=41, error_rate_percent=1.0, execution_device="cpu",
            profile_yaml=self.BOUNDARY_PROFILE,
        )
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)

    def test_one_below_minimum_successful_requests_is_rejected(self) -> None:
        self.write_result(
            succeeded=39, attempted=39, error_rate_percent=0.0, execution_device="cpu",
            profile_yaml=self.BOUNDARY_PROFILE,
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("acceptance:", stderr)
        self.assertIn("39 successful requests is below", stderr)
        self.assertIn("minimum_successful_requests (40)", stderr)
        self.assertIn("does not meet submission requirements", stderr)

    def test_insufficient_successful_requests_is_rejected(self) -> None:
        """The real-world case: this run's actual counts, far short of 40."""
        self.write_result(
            succeeded=13, attempted=13, error_rate_percent=0.0, execution_device="cpu",
            profile_yaml=self.BOUNDARY_PROFILE,
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("13 successful requests is below", stderr)

    def test_error_rate_just_above_maximum_is_rejected(self) -> None:
        self.write_result(
            succeeded=40, attempted=41, error_rate_percent=1.1, execution_device="cpu",
            profile_yaml=self.BOUNDARY_PROFILE,
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("acceptance:", stderr)
        self.assertIn("error rate 1.1%", stderr)
        self.assertIn("maximum_error_rate_percent (1%)", stderr)
        self.assertIn("does not meet submission requirements", stderr)

    def test_excessive_error_rate_is_rejected(self) -> None:
        self.write_result(
            succeeded=40, attempted=50, error_rate_percent=20.0, execution_device="cpu",
            profile_yaml=self.BOUNDARY_PROFILE,
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("error rate 20.0%", stderr)

    def test_missing_profile_file_is_rejected_clearly(self) -> None:
        profile_sha256 = "sha256:" + "f" * 64
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"benchmark": {**VALID_RESULT_TEMPLATE["benchmark"], "profile_sha256": profile_sha256}},
        )
        # No profile file written at all in self.profiles_path.
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("profile file is missing", stderr)

    def test_profile_hash_mismatch_is_rejected_clearly(self) -> None:
        """Guards against judging a result against a profile that has since changed."""
        write_profile(self.profiles_path, "chat-small-v1", self.BOUNDARY_PROFILE)
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={
                "benchmark": {**VALID_RESULT_TEMPLATE["benchmark"], "profile_sha256": "sha256:" + "0" * 64}
            },
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("does not match the current contents", stderr)

    def test_profile_without_acceptance_section_is_rejected_clearly(self) -> None:
        profile_sha256 = write_profile(self.profiles_path, "chat-small-v1", "id: chat-small-v1\n")
        write_bundle(
            self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
            result_overrides={"benchmark": {**VALID_RESULT_TEMPLATE["benchmark"], "profile_sha256": profile_sha256}},
        )
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("missing an 'acceptance' section", stderr)


class RealChatSmallExtendedProfileTests(unittest.TestCase):
    """Exercises the actual shipped benchmarks/profiles/chat-small-extended-v1.yaml
    (unpatched PROFILES_PATH, the real repo), not an isolated fixture."""

    def test_real_profile_file_has_the_required_acceptance_thresholds(self) -> None:
        digest = validate_results.sha256(validate_results.PROFILES_PATH / "chat-small-extended-v1.yaml")
        acceptance, error = validate_results.load_profile_acceptance(
            validate_results.PROFILES_PATH, "chat-small-extended-v1", digest
        )
        self.assertIsNone(error)
        self.assertEqual(acceptance["minimum_successful_requests"], 40)
        self.assertEqual(acceptance["maximum_error_rate_percent"], 1)

    def test_real_profile_matches_chat_small_v1s_acceptance_bar_exactly(self) -> None:
        """Same acceptance requirements as chat-small-v1 -- preserved, not loosened."""
        classic_digest = validate_results.sha256(validate_results.PROFILES_PATH / "chat-small-v1.yaml")
        classic, _ = validate_results.load_profile_acceptance(
            validate_results.PROFILES_PATH, "chat-small-v1", classic_digest
        )
        extended_digest = validate_results.sha256(validate_results.PROFILES_PATH / "chat-small-extended-v1.yaml")
        extended, _ = validate_results.load_profile_acceptance(
            validate_results.PROFILES_PATH, "chat-small-extended-v1", extended_digest
        )
        self.assertEqual(classic, extended)

    def test_real_profile_only_changes_the_duration_ceiling(self) -> None:
        """workload/execution/acceptance/report stay the same shape as chat-small-v1
        except maximum_duration_seconds and the request-count warmup/cooldown note."""
        classic = yaml.safe_load(
            (validate_results.PROFILES_PATH / "chat-small-v1.yaml").read_text(encoding="utf-8")
        )
        extended = yaml.safe_load(
            (validate_results.PROFILES_PATH / "chat-small-extended-v1.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(classic["workload"], extended["workload"])
        self.assertEqual(classic["execution"]["concurrency"], extended["execution"]["concurrency"])
        self.assertEqual(classic["execution"]["measured_requests"], extended["execution"]["measured_requests"])
        self.assertEqual(classic["execution"]["warmup_requests"], extended["execution"]["warmup_requests"])
        self.assertEqual(classic["execution"]["cooldown_requests"], extended["execution"]["cooldown_requests"])
        self.assertEqual(classic["acceptance"], extended["acceptance"])
        self.assertEqual(classic["report"], extended["report"])
        self.assertNotEqual(
            classic["execution"]["maximum_duration_seconds"], extended["execution"]["maximum_duration_seconds"]
        )
        self.assertEqual(extended["execution"]["maximum_duration_seconds"], 5400)


if __name__ == "__main__":
    unittest.main()
