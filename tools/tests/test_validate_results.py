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


BRIDGED_SNAPSHOT = (b'{"operating_system": "Linux-6.6-WSL2", '
                    b'"transport": {"kind": "windows-stdio-http1-v1", '
                    b'"protocol": "HTTP/1.1, one request per connection", '
                    b'"publication": "local-only"}}')


def fact(value, provenance="contributor-declared"):
    return {"value": value, "provenance": "unknown" if value is None else provenance}


def serving_environment(**overrides):
    """A complete schema 1.1 environment for a bridged AMD desktop run."""
    environment = {
        "metadata_version": "serving-environment-v1",
        "relationship": "windows-host-wsl-runner",
        "runner": {"os": "Linux-6.6-WSL2", "kind": "wsl2", "python_version": "3.12.3",
                   "cpu": "x86_64", "ram_gb": 31.0},
        "serving": {
            "host": {"os": fact("Windows-11-10.0.26200"), "cpu": fact("AMD Ryzen 7 9800X3D"),
                     "ram_gb": fact(64.0)},
            "accelerators": [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": 16.0,
                              "count": 1, "role": "inference", "provenance": "detected"}],
            "placement": {"mode": "gpu-only", "detail": None, "provenance": "contributor-declared"},
            "runtime": {
                "application": {"name": "LM Studio", "version": "0.3.31",
                                "provenance": "contributor-declared"},
                "engine": {"name": "llama.cpp", "version": "b10621",
                           "provenance": "contributor-declared"},
                "settings": [{"name": "context_length", "value": "8192",
                              "provenance": "contributor-declared"}],
            },
        },
        "transport": {"kind": "windows-wsl-stdio-bridge",
                      "protocol": "HTTP/1.1, one request per connection",
                      "bridge_version": "windows-stdio-http1-v1",
                      "overhead": "included-not-measured"},
    }
    environment.update(overrides)
    return environment


def bridged_template(**model_overrides):
    """VALID_RESULT_TEMPLATE, repackaged as the bridged 1.1 result it would be."""
    result = copy.deepcopy(VALID_RESULT_TEMPLATE)
    result["schema_version"] = "1.1"
    result["execution_device"] = "gpu"
    result["environment"] = serving_environment()
    result["runtime"] = {"framework": "lmstudio", "version": "b10621", "arguments": [],
                         "endpoint_type": "openai-compatible"}
    result["hardware"] = {
        "os": "Windows-11-10.0.26200", "cpu": "AMD Ryzen 7 9800X3D", "ram_gb": 64.0,
        "gpus": [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": 16.0, "count": 1}],
        "driver_version": None,
        "detection": {"hardware": "user-confirmed", "model": "manual", "runtime": "manual"},
    }
    result["model"] = {"id": "Qwen/Qwen3-4B", "revision": "abc123", "quantization": "Q4_K_M",
                       "served_model_id": "qwen3-4b", "catalog_status": "catalogued",
                       **model_overrides}
    return result


def proposal_document(**overrides):
    document = {
        "schema_version": "model-proposal-1.0",
        "status": "proposed",
        "proposed_id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
        "family": "DeepSeek",
        "parameter_class": "14B",
        "modality": "text-generation",
        "source": "https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
        "licence": "MIT",
        "licence_url": None,
        "weights_publicly_downloadable": True,
        "proposed_by": "local-tester",
        "proposed_at": "2026-11-03T09:00:00Z",
        "for_run_id": "run-fixture-1234",
        "rationale": "Widely used distilled reasoning model; results have no catalog entry yet.",
        "initial_runtimes": ["lmstudio"],
    }
    document.update(overrides)
    return document


def write_profile(profiles_dir: Path, profile_id: str, contents: str) -> str:
    """Write a fixture profile YAML and return its sha256 (matching benchmark.profile_sha256)."""
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / f"{profile_id}.yaml").write_bytes(contents.encode())
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
    (catalog_dir / filename).write_bytes(contents.encode())


def write_bundle(
    bundle_dir: Path,
    *,
    result_overrides: dict | None = None,
    artifact_contents: dict[str, bytes] | None = None,
    write_artifacts: set[str] | None = None,
    proposal: dict | bytes | None = None,
    template: dict | None = None,
) -> None:
    """Write a result.json plus its evidence files into bundle_dir."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    contents = {**ARTIFACT_CONTENTS, **(artifact_contents or {})}
    names_to_write = write_artifacts if write_artifacts is not None else set(contents)

    result = copy.deepcopy(template or VALID_RESULT_TEMPLATE)
    for name, artifact in result["artifacts"].items():
        artifact["sha256"] = sha256_of(contents[name])
    if result_overrides:
        result.update(result_overrides)

    (bundle_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    for name, artifact in VALID_RESULT_TEMPLATE["artifacts"].items():
        if name in names_to_write:
            (bundle_dir / artifact["path"]).write_bytes(contents[name])
    if proposal is not None:
        (bundle_dir / "model-proposal.json").write_bytes(
            proposal if isinstance(proposal, bytes) else json.dumps(proposal).encode())


class RegistryFixture(unittest.TestCase):
    """An isolated registry: its own results, catalog and profiles directories."""

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


class ValidateResultsTests(RegistryFixture):
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

    def test_a_bridged_run_cannot_be_published_under_schema_1_0(self) -> None:
        """1.0 has one hardware host, so it cannot say where the server was."""
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT})
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("schema 1.0 cannot represent", stderr)
        self.assertNotIn("hash mismatch", stderr)

    def test_unreadable_transport_provenance_fails_closed(self) -> None:
        for contents in (b'not json', b'[]', b'{"transport":null}' + b' ' * (1024 * 1024)):
            with self.subTest(contents_length=len(contents)):
                write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                             artifact_contents={"environment_snapshot": contents})
                exit_code, _, stderr = self.run_main()
                self.assertEqual(exit_code, 1)
                self.assertIn("cannot establish transport provenance", stderr)

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
        self.assertIn("is not on the curated list", stderr)
        self.assertIn("model.eligibility", stderr)

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


class BridgedSubmissionTests(RegistryFixture):
    """A bridged run is publishable exactly when it says what it really was."""

    def bundle(self, **kwargs):
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=bridged_template(), **kwargs)

    def test_a_complete_bridged_bundle_is_accepted(self) -> None:
        self.bundle()
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_declaring_a_bridge_without_evidence_of_one_is_rejected(self) -> None:
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     template=bridged_template())
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("declares the Windows bridge, but the environment snapshot", stderr)

    def test_evidence_of_a_bridge_cannot_be_packaged_as_a_direct_run(self) -> None:
        template = bridged_template()
        template["environment"]["relationship"] = "same-host"
        template["environment"]["runner"]["kind"] = "native"
        template["environment"]["transport"] = {"kind": "direct", "protocol": "HTTP/1.1",
                                                "bridge_version": None, "overhead": "none-direct"}
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=template)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("shows a bridged run, but the result declares transport 'direct'", stderr)

    def test_a_bridge_version_the_run_did_not_use_is_rejected(self) -> None:
        template = bridged_template()
        template["environment"]["transport"]["bridge_version"] = "windows-stdio-http1-v2"
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=template)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("is not the one the run recorded", stderr)

    def test_a_bridged_run_cannot_claim_the_two_hosts_are_one(self) -> None:
        template = bridged_template()
        template["environment"]["relationship"] = "same-host"
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=template)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("relationship", stderr)

    def test_transport_overhead_cannot_be_declared_away(self) -> None:
        template = bridged_template()
        template["environment"]["transport"]["overhead"] = "none-direct"
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=template)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("overhead", stderr)

    def test_a_direct_run_stays_valid_under_schema_1_0(self) -> None:
        """The route that already worked is not disturbed by any of this."""
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234")
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Validated 1", stdout)


class ServingMetadataValidationTests(RegistryFixture):
    def write(self, template):
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=template)

    def test_serving_accelerators_must_agree_with_hardware_gpus(self) -> None:
        template = bridged_template()
        template["hardware"]["gpus"] = [{"vendor": "amd", "model": "AMD Radeon RX 7900 XTX",
                                         "memory_gb": 24.0, "count": 1}]
        self.write(template)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("do not match hardware.gpus", stderr)

    def test_a_present_but_unused_gpu_is_not_promoted_into_hardware(self) -> None:
        """Detecting a second card is not evidence that it served anything."""
        template = bridged_template()
        template["environment"]["serving"]["accelerators"].append(
            {"vendor": "amd", "model": "AMD Radeon 780M", "memory_gb": 2.0, "count": 1,
             "role": "present-not-used", "provenance": "detected"})
        self.write(template)
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Validated 1", stdout)

    def test_two_identical_cards_become_one_entry_with_a_count(self) -> None:
        template = bridged_template()
        template["environment"]["serving"]["accelerators"] = [
            {"vendor": "nvidia", "model": "NVIDIA RTX 4090", "memory_gb": 24.0, "count": 1,
             "role": "inference", "provenance": "detected"},
            {"vendor": "nvidia", "model": "NVIDIA RTX 4090", "memory_gb": 24.0, "count": 1,
             "role": "inference", "provenance": "detected"},
        ]
        template["hardware"]["gpus"] = [{"vendor": "nvidia", "model": "NVIDIA RTX 4090",
                                         "memory_gb": 24.0, "count": 2}]
        self.write(template)
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Validated 1", stdout)

    def test_mixed_cpu_gpu_offload_is_representable_and_needs_its_split(self) -> None:
        template = bridged_template()
        template["environment"]["serving"]["placement"] = {
            "mode": "cpu-gpu-offload", "detail": "32 of 49 layers on GPU",
            "provenance": "contributor-declared"}
        self.write(template)
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Validated 1", stdout)

    def test_a_cpu_only_bridged_run_records_no_inference_accelerator(self) -> None:
        template = bridged_template()
        template["execution_device"] = "cpu"
        template["hardware"]["gpus"] = []
        template["hardware"]["driver_version"] = None
        template["environment"]["serving"]["placement"] = {
            "mode": "cpu-only", "detail": None, "provenance": "contributor-declared"}
        template["environment"]["serving"]["accelerators"] = [
            {"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": 16.0, "count": 1,
             "role": "present-not-used", "provenance": "detected"}]
        self.write(template)
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Validated 1", stdout)

    def test_an_application_version_cannot_stand_in_for_the_engine_build(self) -> None:
        template = bridged_template()
        template["runtime"]["version"] = "0.3.31"  # the LM Studio release
        self.write(template)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("runtime.version is the inference engine's build", stderr)

    def test_an_unknown_fact_may_not_carry_a_value(self) -> None:
        template = bridged_template()
        template["environment"]["serving"]["host"]["cpu"] = {"value": "AMD Ryzen",
                                                             "provenance": "unknown"}
        self.write(template)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("provenance", stderr)

    def test_a_known_fact_may_not_claim_unknown_provenance_by_omission(self) -> None:
        template = bridged_template()
        template["environment"]["serving"]["host"]["cpu"] = {"value": None,
                                                             "provenance": "detected"}
        self.write(template)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("provenance", stderr)

    def test_schema_1_0_cannot_carry_serving_metadata(self) -> None:
        template = bridged_template()
        template["schema_version"] = "1.0"
        self.write(template)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("environment", stderr)


class WeightIdentityTests(RegistryFixture):
    def write(self, model):
        template = copy.deepcopy(VALID_RESULT_TEMPLATE)
        template["schema_version"] = "1.1"
        template["environment"] = {
            "metadata_version": "serving-environment-v1", "relationship": "same-host",
            "runner": {"os": "Test Linux", "kind": "native", "python_version": "3.12.3",
                       "cpu": "Test CPU", "ram_gb": 64.0},
            "serving": {
                "host": {"os": fact("Test Linux", "detected"), "cpu": fact("Test CPU", "detected"),
                         "ram_gb": fact(64.0, "detected")},
                "accelerators": [{"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24.0,
                                  "count": 1, "role": "inference", "provenance": "detected"}],
                "placement": {"mode": "gpu-only", "detail": None, "provenance": "contributor-declared"},
                "runtime": {"application": None,
                            "engine": {"name": "vllm", "version": "0.11.0",
                                       "provenance": "contributor-declared"},
                            "settings": []},
            },
            "transport": {"kind": "direct", "protocol": "HTTP/1.1", "bridge_version": None,
                          "overhead": "none-direct"},
        }
        template["hardware"]["gpus"] = [{"vendor": "nvidia", "model": "NVIDIA Test GPU",
                                         "memory_gb": 24.0, "count": 1}]
        template["model"] = {"id": "Qwen/Qwen3-4B", "quantization": "Q4_K_M",
                             "catalog_status": "catalogued", **model}
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     template=template)

    @staticmethod
    def manifest(files):
        document = {"manifest_version": "weight-manifest-1", "files": files}
        return "sha256:" + hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def test_a_quantization_repository_is_recorded_as_the_weights_source(self) -> None:
        self.write({"revision": "f" * 40, "weights": {
            "kind": "repository", "repository": "https://huggingface.co/unsloth/Qwen3-4B-GGUF",
            "repository_revision": "f" * 40, "identity": "repository-revision", "content": None}})
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Validated 1", stdout)

    def test_a_repository_revision_and_the_model_revision_must_be_the_same_commit(self) -> None:
        self.write({"revision": "a" * 40, "weights": {
            "kind": "repository", "repository": "https://huggingface.co/unsloth/Qwen3-4B-GGUF",
            "repository_revision": "f" * 40, "identity": "repository-revision", "content": None}})
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("one weight set has one revision", stderr)

    def test_a_file_checksum_is_not_a_repository_commit(self) -> None:
        self.write({"revision": "sha256-manifest:" + "0" * 64, "weights": {
            "kind": "repository", "repository": "https://huggingface.co/unsloth/Qwen3-4B-GGUF",
            "repository_revision": "sha256-manifest:" + "0" * 64,
            "identity": "repository-revision", "content": None}})
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("a manifest hash is not a repository commit", stderr)

    def test_local_weights_are_identified_by_the_bytes_of_their_files(self) -> None:
        files = [{"name": "model.gguf", "bytes": 1234, "sha256": "sha256:" + "1" * 64}]
        digest = self.manifest(files)
        self.write({"revision": "sha256-manifest:" + digest.split(":")[1], "weights": {
            "kind": "local-files", "repository": None, "repository_revision": None,
            "identity": "content-hash", "content": {"manifest_sha256": digest, "files": files}}})
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Validated 1", stdout)

    def test_a_sharded_weight_set_is_one_identity_over_every_shard(self) -> None:
        files = [{"name": f"model-0000{index}-of-00003.safetensors", "bytes": 1000 + index,
                  "sha256": "sha256:" + str(index) * 64} for index in (1, 2, 3)]
        digest = self.manifest(files)
        self.write({"revision": "sha256-manifest:" + digest.split(":")[1], "weights": {
            "kind": "local-files", "repository": None, "repository_revision": None,
            "identity": "content-hash", "content": {"manifest_sha256": digest, "files": files}}})
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)

    def test_one_changed_shard_changes_the_identity(self) -> None:
        files = [{"name": f"model-0000{index}-of-00003.safetensors", "bytes": 1000 + index,
                  "sha256": "sha256:" + str(index) * 64} for index in (1, 2, 3)]
        digest = self.manifest(files)
        files[1] = {**files[1], "sha256": "sha256:" + "9" * 64}
        self.write({"revision": "sha256-manifest:" + digest.split(":")[1], "weights": {
            "kind": "local-files", "repository": None, "repository_revision": None,
            "identity": "content-hash", "content": {"manifest_sha256": digest, "files": files}}})
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("does not hash the file list", stderr)

    def test_a_manifest_revision_without_a_manifest_is_rejected(self) -> None:
        self.write({"revision": "sha256-manifest:" + "0" * 64})
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("has to be backed by model.weights", stderr)

    def test_local_weights_cannot_claim_a_repository_revision_identity(self) -> None:
        self.write({"revision": "f" * 40, "weights": {
            "kind": "local-files", "repository": None, "repository_revision": "f" * 40,
            "identity": "repository-revision", "content": None}})
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("identity", stderr)

    def test_a_mutable_weights_revision_is_rejected(self) -> None:
        self.write({"revision": "abc123", "weights": {
            "kind": "repository", "repository": "https://huggingface.co/unsloth/Qwen3-4B-GGUF",
            "repository_revision": "main", "identity": "repository-revision", "content": None}})
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("mutable label", stderr)


class ModelProposalTests(RegistryFixture):
    def write(self, *, model_overrides=None, proposal=None, directory="2026-08-30-run-fixture-1234"):
        template = bridged_template()
        template["model"].update({"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
                                  "served_model_id": "deepseek-r1-distill-qwen-14b",
                                  "catalog_status": "proposed"})
        template["model"].update(model_overrides or {})
        write_bundle(self.results_path / "deepseek" / directory,
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=template, proposal=proposal)

    def test_a_new_model_reaches_review_with_its_result(self) -> None:
        self.write(proposal=proposal_document())
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Validated 1", stdout)

    def test_an_uncatalogued_model_with_neither_route_is_refused(self) -> None:
        """Not a proposal and not an eligibility block: nothing says it may be
        published, and the refusal names both ways out."""
        template = bridged_template()
        template["model"]["id"] = "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"
        write_bundle(self.results_path / "deepseek" / "2026-08-30-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=template)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("is not on the curated list", stderr)
        self.assertIn("model.eligibility", stderr)
        self.assertIn("model.eligibility", stderr)

    def test_claiming_proposed_without_the_proposal_file_is_rejected(self) -> None:
        self.write()
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("has no model-proposal.json", stderr)

    def test_a_proposal_cannot_declare_itself_accepted(self) -> None:
        self.write(proposal=proposal_document(status="accepted"))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("status", stderr)

    def test_a_proposal_for_a_catalogued_model_is_rejected(self) -> None:
        template = bridged_template()
        template["model"]["catalog_status"] = "proposed"
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=template,
                     proposal=proposal_document(proposed_id="Qwen/Qwen3-4B"))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("already in the catalog", stderr)

    def test_a_proposal_for_a_different_model_than_the_one_measured_is_rejected(self) -> None:
        self.write(proposal=proposal_document(proposed_id="meta-llama/Llama-3.1-8B-Instruct"))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("but the result measured", stderr)

    def test_a_proposal_bound_to_another_run_is_rejected(self) -> None:
        self.write(proposal=proposal_document(for_run_id="run-somebody-else"))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("not this result's", stderr)

    def test_a_proposal_from_another_account_is_rejected(self) -> None:
        self.write(proposal=proposal_document(proposed_by="someone-else"))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("is not this bundle's submitter", stderr)

    def test_a_proposal_file_in_a_bundle_that_does_not_claim_one_is_rejected(self) -> None:
        template = bridged_template()
        write_bundle(self.results_path / "qwen3-4b" / "2026-08-30-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=template, proposal=proposal_document())
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("a bundle carrying a proposal must say so", stderr)

    def test_a_second_proposal_for_the_same_model_is_reported_as_a_duplicate(self) -> None:
        self.write(proposal=proposal_document())
        second = bridged_template()
        second["run_id"] = "run-fixture-5678"
        second["model"].update({"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
                                "catalog_status": "proposed"})
        write_bundle(self.results_path / "deepseek" / "2026-08-31-run-fixture-5678",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=second, proposal=proposal_document(for_run_id="run-fixture-5678"))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("is already proposed by", stderr)

    def test_an_unreviewed_proposal_does_not_become_a_catalog_entry(self) -> None:
        """The catalog is loaded from catalog/models only; a bundle cannot add to it."""
        self.write(proposal=proposal_document())
        self.run_main()
        catalog = validate_results.load_model_catalog(self.catalog_path)
        self.assertNotIn("deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", catalog)
        self.assertEqual(set(catalog), {"Qwen/Qwen3-4B"})

    def test_a_proposal_for_a_non_text_modality_is_out_of_scope(self) -> None:
        self.write(proposal=proposal_document(modality="text-to-image"))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("modality", stderr)

    def test_a_proposal_that_is_not_readable_json_is_rejected(self) -> None:
        self.write(proposal=b"{not json")
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("not readable JSON", stderr)


class UncataloguedEligibilityTests(RegistryFixture):
    """A model nobody curated, judged by the requirements instead of by a list.

    The curated list stops being the set of models allowed to exist and goes
    back to being a recommendation. What makes a result publishable is the
    same either way, so these check that each requirement is enforced and
    that a refusal names the one that is missing.
    """

    ELIGIBLE = {
        "licence": "apache-2.0",
        "licence_url": "https://huggingface.co/someone/Model-7B/blob/main/LICENSE",
        "weights_publicly_downloadable": True,
        "modality": "text-generation",
        "publisher": "someone",
        "source": "https://huggingface.co/someone/Model-7B",
        "family": "Model",
        "parameter_class": "7B",
    }

    def write(self, *, model_id="someone/Model-7B", status="uncatalogued",
              eligibility="default", version="1.2", raw_report=None):
        result = bridged_template()
        result["schema_version"] = version
        result["model"]["id"] = model_id
        result["model"]["catalog_status"] = status
        if eligibility == "default":
            eligibility = dict(self.ELIGIBLE)
        if eligibility is not None:
            result["model"]["eligibility"] = eligibility
        contents = {"environment_snapshot": BRIDGED_SNAPSHOT}
        if raw_report is not None:
            contents["raw_runner_output"] = gzip.compress(json.dumps(raw_report).encode())
        write_bundle(self.results_path / "custom" / "2026-11-06-run-fixture-1234",
                     artifact_contents=contents,
                     template=result)

    def test_a_complete_uncatalogued_model_is_accepted(self) -> None:
        self.write()
        exit_code, stdout, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Validated 1 OmoBench result bundle(s).", stdout)

    def test_vision_language_model_requires_text_only_runner_evidence(self):
        eligibility = dict(self.ELIGIBLE, modality="image-text-to-text")
        spec = {"backend": {"request_format": "/v1/chat/completions"},
                "data": [{"kind": "synthetic_text"}]}
        request = {"input_metrics": {"text_tokens": 1024, "image_count": None}}
        report = {"config": {"spec": spec},
                  "benchmarks": [{"requests": {"successful": [request]}}]}
        self.write(eligibility=eligibility, raw_report=report)
        code, _, error = self.run_main()
        self.assertEqual(code, 0, error)
        request["input_metrics"]["image_count"] = 1
        self.write(eligibility=eligibility, raw_report=report)
        self.assertIn("text-only workload", self.run_main()[2])
        request["input_metrics"]["image_count"] = None
        spec["data"].append({"kind": "image"})
        self.write(eligibility=eligibility, raw_report=report)
        self.assertIn("text-only workload", self.run_main()[2])
        self.write(eligibility=eligibility)
        self.assertIn("text-only workload", self.run_main()[2])

    def test_no_proposal_is_required(self) -> None:
        """The point of the change: a proposal is a route, not a toll."""
        self.write()
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)
        self.assertNotIn("model-proposal.json", stderr)

    def test_a_missing_eligibility_block_is_refused_by_name(self) -> None:
        self.write(eligibility=None)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("model.eligibility: required when model.catalog_status is 'uncatalogued'",
                      stderr)

    def test_a_missing_licence_is_refused_by_name(self) -> None:
        eligibility = dict(self.ELIGIBLE)
        eligibility["licence"] = "  "
        self.write(eligibility=eligibility)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("model.eligibility.licence:", stderr)
        self.assertIn("a published licence is required", stderr)

    def test_a_licence_nobody_can_open_is_refused(self) -> None:
        eligibility = dict(self.ELIGIBLE)
        eligibility["licence_url"] = ""
        self.write(eligibility=eligibility)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("licence_url", stderr)

    def test_weights_nobody_else_can_obtain_are_refused(self) -> None:
        """A private fine-tune is a fine thing to benchmark and not to publish."""
        eligibility = dict(self.ELIGIBLE)
        eligibility["weights_publicly_downloadable"] = False
        self.write(eligibility=eligibility)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("obtainable by anybody", stderr)
        self.assertIn("hosted API with no weights behind it", stderr)

    def test_another_modality_is_refused(self) -> None:
        eligibility = dict(self.ELIGIBLE)
        eligibility["modality"] = "image-classification"
        self.write(eligibility=eligibility)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("modality", stderr)

    def test_a_fine_tune_may_not_claim_itself_as_its_base(self) -> None:
        eligibility = dict(self.ELIGIBLE)
        eligibility["base_model"] = "someone/Model-7B"
        self.write(eligibility=eligibility)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("a fine-tune's base is a different model", stderr)

    def test_a_curated_model_needs_no_eligibility_block(self) -> None:
        """Curation still buys something: the list already recorded this."""
        self.write(model_id="Qwen/Qwen3-4B", status="catalogued", eligibility=None)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)

    def test_a_curated_model_cannot_call_itself_uncatalogued(self) -> None:
        self.write(model_id="Qwen/Qwen3-4B", status="uncatalogued")
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("is on the curated list, so it is not uncatalogued", stderr)

    def test_schema_1_1_cannot_say_uncatalogued(self) -> None:
        """1.1 keeps the meaning it was written with."""
        self.write(version="1.1", eligibility=None)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("catalog_status", stderr)

    def test_schema_1_1_cannot_carry_eligibility(self) -> None:
        self.write(version="1.1", status="proposed")
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("eligibility", stderr)


class InterruptedMeasurementTests(RegistryFixture):
    """A measurement taken while the machine was suspended is not a
    measurement of the model, and is refused for that reason rather than for
    falling short of a threshold."""

    def write(self, execution, *, template=None):
        result = template if template is not None else bridged_template()
        if execution is not None:
            result["execution"] = execution
        write_bundle(self.results_path / "qwen3-4b" / "2026-11-06-run-fixture-1234",
                     artifact_contents={"environment_snapshot": BRIDGED_SNAPSHOT},
                     template=result)

    @staticmethod
    def record(state, **interruption):
        return {
            "contract": "bounded-execution-v1",
            "limits": {"inactivity_timeout_seconds": 300.0,
                       "request_deadline_seconds": 900.0,
                       "measurement_ceiling_seconds": 900.0,
                       "shutdown_grace_seconds": 60.0},
            "interruption": {"state": state, "observed_seconds": 0.0,
                             "longest_seconds": 0.0, "events": [], **interruption},
        }

    def test_a_run_with_no_execution_record_is_unaffected(self) -> None:
        """Every result published before this contract existed stays valid."""
        self.write(None)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)

    def test_an_uninterrupted_run_passes(self) -> None:
        self.write(self.record("none"))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)

    def test_a_confirmed_suspension_is_refused_and_says_why(self) -> None:
        self.write(self.record(
            "suspended", observed_seconds=4296.0, longest_seconds=4296.0,
            events=[{"kind": "suspended", "seconds": 4296.0,
                     "started_at": "2026-11-06T03:47:26Z", "ended_at": "2026-11-06T04:59:26Z"}]))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("interruption:", stderr)
        self.assertIn("4296.0 seconds while it was measuring", stderr)
        self.assertIn("stays available for diagnosis", stderr)
        # Not an acceptance failure: different problem, different sentence.
        self.assertNotIn("acceptance:", stderr)

    def test_an_unexplained_gap_is_not_refused(self) -> None:
        """Something did not get to run. That is a question, not a verdict."""
        self.write(self.record(
            "gap", observed_seconds=119.0, longest_seconds=119.0,
            events=[{"kind": "gap", "seconds": 119.0,
                     "started_at": "2026-11-06T03:47:26Z", "ended_at": "2026-11-06T03:49:26Z"}]))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)

    def test_an_ambiguous_clock_jump_is_refused_without_being_called_a_suspension(self) -> None:
        """Measured on this project's WSL2 distribution: its CLOCK_BOOTTIME
        equals CLOCK_MONOTONIC through host sleep cycles, and NTP is active.
        So a wall-clock divergence has two possible causes and the registry
        must not pick one."""
        self.write(self.record(
            "clock-jump", observed_seconds=4296.0, longest_seconds=4296.0,
            events=[{"kind": "clock-jump", "seconds": 4296.0,
                     "started_at": "2026-11-06T03:47:26Z", "ended_at": "2026-11-06T04:59:26Z"}]))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("interruption:", stderr)
        self.assertIn("kernel recorded no suspend", stderr)
        self.assertIn("nothing here can tell which", stderr)
        self.assertNotIn("was suspended for", stderr)
        self.assertNotIn("acceptance:", stderr)

    def test_a_clock_jump_claimed_without_an_event_is_refused(self) -> None:
        self.write(self.record("clock-jump", observed_seconds=10.0))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("no event of that kind is recorded", stderr)

    def test_a_clock_jump_hiding_a_confirmed_suspension_is_refused(self) -> None:
        self.write(self.record("clock-jump", events=[
            {"kind": "clock-jump", "seconds": 10.0, "started_at": "2026-11-06T03:47:26Z",
             "ended_at": "2026-11-06T03:47:36Z"},
            {"kind": "suspended", "seconds": 600.0, "started_at": "2026-11-06T03:48:26Z",
             "ended_at": "2026-11-06T03:58:26Z"}]))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("the worst thing observed is what the state reports", stderr)

    def test_a_gap_hiding_a_clock_jump_is_refused(self) -> None:
        self.write(self.record("gap", events=[
            {"kind": "gap", "seconds": 40.0, "started_at": "2026-11-06T03:47:26Z",
             "ended_at": "2026-11-06T03:48:06Z"},
            {"kind": "clock-jump", "seconds": 90.0, "started_at": "2026-11-06T03:49:26Z",
             "ended_at": "2026-11-06T03:50:56Z"}]))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("the worst thing observed is what the state reports", stderr)

    def test_an_unknown_interruption_state_is_refused_by_the_schema(self) -> None:
        self.write(self.record("probably-fine"))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("interruption.state", stderr)

    def test_the_anchor_is_recorded_and_checked(self) -> None:
        """A deadline is meaningless without saying what it was measured from."""
        record = self.record("none")
        record["supervisor"] = {"fired": True, "reason": "measurement-window-overrun",
                                "escalated": True, "at_seconds": 5461.0,
                                "anchor": "measurement-start", "detail": "fixture"}
        self.write(record)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 0, stderr)

        record["supervisor"]["anchor"] = "whenever"
        self.write(record)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("anchor", stderr)

    def test_a_state_that_its_own_evidence_does_not_support_is_refused(self) -> None:
        self.write(self.record("suspended", observed_seconds=100.0))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("no event of that kind is recorded", stderr)

    def test_a_clean_state_with_events_beside_it_is_refused(self) -> None:
        self.write(self.record("none", events=[
            {"kind": "gap", "seconds": 40.0, "started_at": "2026-11-06T03:47:26Z",
             "ended_at": "2026-11-06T03:48:26Z"}]))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("state is 'none' but 1 interruption event", stderr)

    def test_a_gap_hiding_a_suspension_is_refused(self) -> None:
        """The state reports the worst thing observed, not a convenient one."""
        self.write(self.record("gap", events=[
            {"kind": "suspended", "seconds": 600.0, "started_at": "2026-11-06T03:47:26Z",
             "ended_at": "2026-11-06T03:57:26Z"}]))
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("the worst thing observed is what the state reports", stderr)

    def test_schema_1_0_cannot_carry_an_execution_record(self) -> None:
        """1.0 is frozen: it never acquires a fact nobody collected."""
        write_bundle(self.results_path / "qwen3-4b" / "2026-11-06-run-fixture-1234",
                     result_overrides={"execution": self.record("none")})
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("execution", stderr)

    def test_an_unknown_contract_is_refused_by_the_schema(self) -> None:
        record = self.record("none")
        record["contract"] = "bounded-execution-v99"
        self.write(record)
        exit_code, _, stderr = self.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIn("contract", stderr)


if __name__ == "__main__":
    unittest.main()
