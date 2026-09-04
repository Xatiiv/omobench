from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from omobench.cli import (
    OTHER_SERVER_FRAMEWORK,
    RUNTIME_FRAMEWORKS,
    build_guidellm_command,
    build_parser,
    normalize_guidellm_report,
    package_local_run,
    redact_endpoint,
    validate_execution_device,
    write_run_intent,
)


GUIDELLM_REPORT_V2 = {
    "metadata": {"version": 2, "guidellm_version": "0.7.3"},
    "benchmarks": [
        {
            "scheduler_metrics": {
                "measure_start_time": 1767225600,
                "requests_made": {
                    "successful": 49,
                    "incomplete": 0,
                    "errored": 1,
                    "total": 50,
                },
            },
            "metrics": {
                "time_to_first_token_ms": {
                    "successful": {"percentiles": {"p50": 210.0, "p95": 315.0}}
                },
                "inter_token_latency_ms": {
                    "successful": {"percentiles": {"p50": 12.5, "p95": 18.0}}
                },
                "output_tokens_per_second": {"total": {"mean": 87.5}},
                "tokens_per_second": {"total": {"mean": 220.0}},
                "requests_per_second": {"total": {"mean": 3.2}},
            },
        }
    ],
}


class GuideLLMCommandTests(unittest.TestCase):
    def test_chat_small_is_a_fixed_single_stream_workload(self) -> None:
        command = build_guidellm_command(
            endpoint="http://localhost:8000/v1",
            model="Qwen/Qwen3-4B",
            profile_id="chat-small-v1",
            raw_output_path=Path("/tmp/benchmarks.json"),
        )
        self.assertIn("kind=synchronous,warmup=0.1,cooldown=0.1", command)
        self.assertIn("kind=synthetic_text,prompt_tokens=1024,output_tokens=256", command)
        self.assertIn("kind=max_requests,count=50", command)
        self.assertIn("kind=generative,sample_size=0", command)

    def test_throughput_is_fixed_at_sixteen_streams(self) -> None:
        command = build_guidellm_command(
            endpoint="http://localhost:8000/v1",
            model="Qwen/Qwen3-4B",
            profile_id="throughput-v1",
            raw_output_path=Path("/tmp/benchmarks.json"),
        )
        self.assertIn(
            "kind=concurrent,streams=16,max_concurrency=16,warmup=0.1,cooldown=0.1",
            command,
        )
        self.assertIn("kind=max_requests,count=160", command)

    def test_local_endpoint_is_redacted_before_evidence_is_written(self) -> None:
        value = "server=http://localhost:8000/v1 and again http://localhost:8000/v1"
        self.assertEqual(
            redact_endpoint(value, "http://localhost:8000/v1"),
            "server=<LOCAL_ENDPOINT> and again <LOCAL_ENDPOINT>",
        )


class RunIntentTests(unittest.TestCase):
    def test_run_intent_uses_a_portable_profile_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile_path = root / "benchmarks" / "profiles" / "chat-small-v1.yaml"
            profile_path.parent.mkdir(parents=True)
            profile_path.write_text("id: chat-small-v1\n", encoding="utf-8")
            output_path = root / ".omobench-runs" / "intent.json"
            args = argparse.Namespace(
                endpoint="http://localhost:8000/v1",
                model="Qwen/Qwen3-4B",
                profile="chat-small-v1",
                output=str(output_path),
                force=False,
            )
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.doctor_report", return_value={}):
                    self.assertEqual(write_run_intent(args), 0)
            finally:
                os.chdir(original_directory)

            intent = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(intent["profile_path"], "benchmarks/profiles/chat-small-v1.yaml")


def build_staged_run_directory(root: Path) -> Path:
    """Create a complete .omobench-runs/ staging directory with valid evidence."""
    profile_path = root / "benchmarks" / "profiles" / "chat-small-v1.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("id: chat-small-v1\n", encoding="utf-8")
    run_directory = root / ".omobench-runs" / "run-fixture-1234"
    run_directory.mkdir(parents=True)
    with gzip.open(run_directory / "guidellm-benchmarks.json.gz", "wt", encoding="utf-8") as raw_file:
        json.dump(GUIDELLM_REPORT_V2, raw_file)
    (run_directory / "guidellm.stdout.log").write_text("completed\n", encoding="utf-8")
    (run_directory / "reproduce.md").write_text("# Reproduce\n", encoding="utf-8")
    (run_directory / "environment.json").write_text(
        json.dumps(
            {
                "operating_system": "Test Linux",
                "cpu": "Test CPU",
                "ram_gb": 64,
                "nvidia_gpus": [
                    {"model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"},
                    {"model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return run_directory


def base_package_args(run_directory: Path, **overrides: object) -> argparse.Namespace:
    args = dict(
        run_dir=str(run_directory),
        model="Qwen/Qwen3-4B",
        model_revision="abc123",
        quantization="BF16",
        framework="vllm",
        framework_version="0.11.0",
        server_description=None,
        execution_device="gpu",
        runtime_arg=["--max-model-len=4096"],
        profile="chat-small-v1",
        submitter="local-tester",
        run_id=None,
        run_kind="initial",
        repeat_of_run_id=None,
        cpu=None,
        ram_gb=None,
        force=False,
    )
    args.update(overrides)
    return argparse.Namespace(**args)


class RuntimeFrameworkParserTests(unittest.TestCase):
    def test_package_accepts_every_documented_framework_choice(self) -> None:
        parser = build_parser()
        for framework in RUNTIME_FRAMEWORKS:
            with self.subTest(framework=framework):
                args = parser.parse_args(
                    [
                        "package",
                        "--run-dir",
                        ".omobench-runs/run-fixture-1234",
                        "--model",
                        "Qwen/Qwen3-4B",
                        "--model-revision",
                        "abc123",
                        "--framework",
                        framework,
                        "--framework-version",
                        "1.0.0",
                        "--execution-device",
                        "gpu",
                        "--profile",
                        "chat-small-v1",
                        "--submitter",
                        "local-tester",
                    ]
                )
                self.assertEqual(args.framework, framework)

    def test_unknown_framework_is_rejected_by_the_parser(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "package",
                    "--run-dir",
                    ".omobench-runs/run-fixture-1234",
                    "--model",
                    "Qwen/Qwen3-4B",
                    "--model-revision",
                    "abc123",
                    "--framework",
                    "unsupported-framework",
                    "--framework-version",
                    "1.0.0",
                    "--execution-device",
                    "gpu",
                    "--profile",
                    "chat-small-v1",
                    "--submitter",
                    "local-tester",
                ]
            )

    def test_server_description_argument_is_optional_and_defaults_to_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "package",
                "--run-dir",
                ".omobench-runs/run-fixture-1234",
                "--model",
                "Qwen/Qwen3-4B",
                "--model-revision",
                "abc123",
                "--framework",
                "vllm",
                "--framework-version",
                "1.0.0",
                "--execution-device",
                "gpu",
                "--profile",
                "chat-small-v1",
                "--submitter",
                "local-tester",
            ]
        )
        self.assertIsNone(args.server_description)


class ResultPackagingTests(unittest.TestCase):
    def test_normalizer_rejects_an_unpinned_guidellm_report(self) -> None:
        report = json.loads(json.dumps(GUIDELLM_REPORT_V2))
        report["metadata"]["guidellm_version"] = "0.7.2"
        with self.assertRaisesRegex(ValueError, "requires GuideLLM 0.7.3"):
            normalize_guidellm_report(report)

    def test_normalizer_rejects_a_report_with_no_successful_requests(self) -> None:
        report = json.loads(json.dumps(GUIDELLM_REPORT_V2))
        requests = report["benchmarks"][0]["scheduler_metrics"]["requests_made"]
        requests.update(
            {
                "successful": 0,
                "incomplete": 0,
                "errored": 50,
                "total": 50,
            }
        )

        with self.assertRaisesRegex(ValueError, "no successful requests"):
            normalize_guidellm_report(report)

    def test_v1_blocks_cpu_only_packaging(self) -> None:
        with self.assertRaisesRegex(ValueError, "GPU-backed runs only"):
            validate_execution_device("cpu")

        validate_execution_device("gpu")

    def test_package_creates_a_schema_ready_local_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(root)
            args = base_package_args(run_directory)
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["schema_version"], "1.0")
            self.assertEqual(result["run_id"], "run-fixture-1234")
            self.assertEqual(result["results"]["latency_ms"]["ttft"]["p95"], 315.0)
            self.assertEqual(result["results"]["error_rate_percent"], 2.0)
            self.assertEqual(result["hardware"]["gpus"][0]["count"], 2)
            self.assertEqual(
                result["artifacts"]["raw_runner_output"]["path"],
                "guidellm-benchmarks.json.gz",
            )
            self.assertTrue(result["configuration_fingerprint"].startswith("sha256:"))
            self.assertNotIn("server_description", result["runtime"])

    def test_openai_compatible_other_without_description_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(root)
            args = base_package_args(
                run_directory,
                framework=OTHER_SERVER_FRAMEWORK,
                server_description=None,
            )
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        exit_code = package_local_run(args)
            finally:
                os.chdir(original_directory)

            self.assertEqual(exit_code, 2)
            self.assertIn("--server-description is required", stderr.getvalue())
            self.assertFalse((run_directory / "result.json").exists())

    def test_openai_compatible_other_with_description_is_recorded_and_fingerprinted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(root)
            args = base_package_args(
                run_directory,
                framework=OTHER_SERVER_FRAMEWORK,
                server_description="Custom internal OpenAI-compatible gateway",
            )
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["runtime"]["framework"], OTHER_SERVER_FRAMEWORK)
            self.assertEqual(
                result["runtime"]["server_description"],
                "Custom internal OpenAI-compatible gateway",
            )

            # Changing only the server description must change the configuration fingerprint.
            vllm_args = base_package_args(run_directory, force=True)
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(vllm_args), 0)
            finally:
                os.chdir(original_directory)
            vllm_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertNotEqual(
                result["configuration_fingerprint"],
                vllm_result["configuration_fingerprint"],
            )


if __name__ == "__main__":
    unittest.main()
