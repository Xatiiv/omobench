from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

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
        "profile_sha256": "sha256:" + "b" * 64,
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

ARTIFACT_CONTENTS = {
    "raw_runner_output": gzip.compress(b'{"metadata": {"version": 2}}'),
    "runner_stdout_log": b"completed\n",
    "environment_snapshot": b'{"operating_system": "Test Linux"}',
    "reproduce_markdown": b"# Reproduce\n",
}


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
        self.addCleanup(self._temp_directory.cleanup)

        patcher_root = patch.object(validate_results, "ROOT", self.root)
        patcher_results = patch.object(validate_results, "RESULTS_PATH", self.results_path)
        patcher_root.start()
        patcher_results.start()
        self.addCleanup(patcher_root.stop)
        self.addCleanup(patcher_results.stop)

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


if __name__ == "__main__":
    unittest.main()
