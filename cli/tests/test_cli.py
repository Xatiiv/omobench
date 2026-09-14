from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from omobench.cli import (
    OTHER_SERVER_FRAMEWORK,
    RUNTIME_FRAMEWORKS,
    build_guidellm_command,
    build_parser,
    command_output,
    detect_amd_gpus,
    detect_gpus,
    detect_nvidia_gpus,
    detect_windows_cim_gpus,
    doctor_report,
    hardware_from_environment,
    normalize_guidellm_report,
    package_local_run,
    print_doctor,
    redact_endpoint,
    sha256_json,
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


class GpuDetectionTests(unittest.TestCase):
    def test_nvidia_detection_records_vendor_nvidia(self) -> None:
        csv_output = "NVIDIA Test GPU, 24576, 555.1\n"
        with patch("omobench.cli.command_output", return_value=csv_output):
            gpus = detect_nvidia_gpus()
        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0]["vendor"], "nvidia")
        self.assertEqual(gpus[0]["model"], "NVIDIA Test GPU")
        self.assertEqual(gpus[0]["memory_gb"], 24.0)

    def test_command_output_returns_none_on_permission_error(self) -> None:
        with patch("omobench.cli.subprocess.run", side_effect=PermissionError(13, "Permission denied")):
            self.assertIsNone(command_output(["rocm-smi", "--json"]))

    def test_command_output_returns_stdout_on_success(self) -> None:
        completed = subprocess.CompletedProcess(args=["rocm-smi"], returncode=0, stdout="ok\n", stderr="")
        with patch("omobench.cli.subprocess.run", return_value=completed):
            self.assertEqual(command_output(["rocm-smi"]), "ok")

    def test_detect_amd_gpus_returns_empty_when_rocm_smi_permission_denied(self) -> None:
        with patch("omobench.cli.sys.platform", "linux"), patch(
            "omobench.cli.subprocess.run", side_effect=PermissionError(13, "Permission denied")
        ):
            self.assertEqual(detect_amd_gpus(), [])

    def test_windows_cim_detects_amd_and_intel_with_null_memory(self) -> None:
        cim_json = json.dumps(
            [
                {"Name": "AMD Radeon RX 9070", "DriverVersion": "31.0.21001.1000"},
                {"Name": "Intel(R) Arc(TM) A770 Graphics", "DriverVersion": "31.0.101.5081"},
                {"Name": "Microsoft Basic Display Adapter", "DriverVersion": "10.0.0.1"},
            ]
        )
        with patch("omobench.cli.sys.platform", "win32"), patch(
            "omobench.cli.command_output", return_value=cim_json
        ):
            gpus = detect_windows_cim_gpus()

        self.assertEqual(len(gpus), 2)
        by_vendor = {gpu["vendor"]: gpu for gpu in gpus}
        self.assertEqual(by_vendor["amd"]["model"], "AMD Radeon RX 9070")
        self.assertEqual(by_vendor["amd"]["driver_version"], "31.0.21001.1000")
        self.assertIsNone(by_vendor["amd"]["memory_gb"])
        self.assertEqual(by_vendor["intel"]["model"], "Intel(R) Arc(TM) A770 Graphics")
        self.assertEqual(by_vendor["intel"]["driver_version"], "31.0.101.5081")
        self.assertIsNone(by_vendor["intel"]["memory_gb"])

    def test_windows_cim_returns_nothing_off_windows(self) -> None:
        with patch("omobench.cli.sys.platform", "linux"):
            self.assertEqual(detect_windows_cim_gpus(), [])

    def test_detect_gpus_suppresses_fallback_records_for_covered_vendors(self) -> None:
        specialized_nvidia = [
            {"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24.0, "driver_version": "555.1", "detected_by": "nvidia-smi"}
        ]
        specialized_amd = [
            {"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": 192.0, "driver_version": "6.2.1", "detected_by": "rocm-smi"}
        ]
        fallback = [
            {"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": None, "driver_version": None, "detected_by": "win32_videocontroller"},
            {"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": None, "driver_version": None, "detected_by": "win32_videocontroller"},
        ]
        with patch("omobench.cli.sys.platform", "win32"), patch(
            "omobench.cli.detect_nvidia_gpus", return_value=specialized_nvidia
        ), patch("omobench.cli.detect_amd_gpus", return_value=specialized_amd), patch(
            "omobench.cli.detect_windows_cim_gpus", return_value=fallback
        ):
            gpus = detect_gpus()

        self.assertEqual(gpus, specialized_nvidia + specialized_amd)

    def test_detect_gpus_retains_uncovered_fallback_vendor(self) -> None:
        specialized_nvidia = [
            {"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24.0, "driver_version": "555.1", "detected_by": "nvidia-smi"}
        ]
        intel_fallback = {
            "vendor": "intel",
            "model": "Intel(R) Arc(TM) A770 Graphics",
            "memory_gb": None,
            "driver_version": "31.0.101.5081",
            "detected_by": "win32_videocontroller",
        }
        fallback = [
            {"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": None, "driver_version": None, "detected_by": "win32_videocontroller"},
            intel_fallback,
        ]
        with patch("omobench.cli.sys.platform", "win32"), patch(
            "omobench.cli.detect_nvidia_gpus", return_value=specialized_nvidia
        ), patch("omobench.cli.detect_amd_gpus", return_value=[]), patch(
            "omobench.cli.detect_windows_cim_gpus", return_value=fallback
        ):
            gpus = detect_gpus()

        self.assertEqual(gpus, specialized_nvidia + [intel_fallback])

    def test_doctor_report_uses_generic_gpus_key(self) -> None:
        with patch("omobench.cli.detect_gpus", return_value=[{"vendor": "amd", "model": "Test", "memory_gb": None, "driver_version": None, "detected_by": "test"}]):
            report = doctor_report()
        self.assertIn("gpus", report)
        self.assertNotIn("nvidia_gpus", report)

    def test_print_doctor_reports_gpus_not_nvidia_gpus(self) -> None:
        report = {
            "omobench_cli_version": "0.0.0",
            "python": "3.12.0",
            "operating_system": "Test OS",
            "cpu": "Test CPU",
            "ram_gb": 32.0,
            "guidellm": None,
            "git": None,
            "github_cli": None,
            "gpus": [
                {"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "driver_version": None, "detected_by": "test"}
            ],
        }
        output = io.StringIO()
        with redirect_stdout(output):
            print_doctor(report)
        printed = output.getvalue()
        self.assertIn("GPUs:", printed)
        self.assertNotIn("NVIDIA GPUs", printed)
        self.assertIn("[amd] AMD Radeon RX 9070", printed)
        self.assertIn("unknown memory", printed)


class HardwareFromEnvironmentTests(unittest.TestCase):
    def test_legacy_nvidia_gpus_key_still_packages_and_infers_vendor(self) -> None:
        environment = {
            "operating_system": "Test Linux",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "nvidia_gpus": [{"model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"}],
        }
        hardware = hardware_from_environment(environment, None, None, execution_device="gpu")
        self.assertEqual(hardware["gpus"], [{"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24.0, "count": 1}])

    def test_generic_amd_environment_packages_and_emits_vendor_amd(self) -> None:
        environment = {
            "operating_system": "Test Linux",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [{"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": 192, "driver_version": "6.2.1"}],
        }
        hardware = hardware_from_environment(environment, None, None, execution_device="gpu")
        self.assertEqual(
            hardware["gpus"],
            [{"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": 192.0, "count": 1}],
        )

    def test_missing_detected_gpu_blocks_packaging(self) -> None:
        environment = {"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64, "gpus": []}
        with self.assertRaisesRegex(ValueError, "No GPUs were detected"):
            hardware_from_environment(environment, None, None, execution_device="gpu")

    def test_single_unknown_memory_amd_gpu_requires_gpu_memory_gb(self) -> None:
        environment = {
            "operating_system": "Test Windows",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "driver_version": "31.0"}],
        }
        with self.assertRaisesRegex(ValueError, "unknown memory"):
            hardware_from_environment(environment, None, None, execution_device="gpu")

        hardware = hardware_from_environment(environment, None, None, gpu_memory_gb_override=16.0, execution_device="gpu")
        self.assertEqual(
            hardware["gpus"],
            [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": 16.0, "count": 1}],
        )
        self.assertEqual(hardware["detection"]["hardware"], "user-confirmed")

    def test_ambiguous_unknown_memory_gpus_fail_clearly(self) -> None:
        environment = {
            "operating_system": "Test Windows",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [
                {"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "driver_version": None},
                {"vendor": "intel", "model": "Intel Arc A770", "memory_gb": None, "driver_version": None},
            ],
        }
        with self.assertRaisesRegex(ValueError, "more than one"):
            hardware_from_environment(environment, None, None, gpu_memory_gb_override=16.0, execution_device="gpu")

    def test_invalid_gpu_memory_gb_override_types_raise_value_error(self) -> None:
        environment = {
            "operating_system": "Test Windows",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "driver_version": None}],
        }
        for invalid_value in ("16", True):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaisesRegex(ValueError, "--gpu-memory-gb must be a finite positive number."):
                    hardware_from_environment(environment, None, None, gpu_memory_gb_override=invalid_value, execution_device="gpu")

    def test_gpu_memory_gb_rejected_when_not_needed(self) -> None:
        environment = {
            "operating_system": "Test Linux",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [{"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"}],
        }
        with self.assertRaisesRegex(ValueError, "already has a known memory value"):
            hardware_from_environment(environment, None, None, gpu_memory_gb_override=16.0, execution_device="gpu")

    def test_cpu_execution_device_records_no_gpus_and_no_driver(self) -> None:
        environment = {"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64}
        hardware = hardware_from_environment(environment, None, None, execution_device="cpu")
        self.assertEqual(hardware["gpus"], [])
        self.assertIsNone(hardware["driver_version"])
        self.assertEqual(hardware["cpu"], "Test CPU")
        self.assertEqual(hardware["ram_gb"], 64.0)

    def test_cpu_execution_device_does_not_require_any_detected_gpu(self) -> None:
        """A CPU packaging must not need GPU detection, VRAM, or drivers at all."""
        environment = {"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64}
        self.assertNotIn("gpus", environment)
        hardware = hardware_from_environment(environment, None, None, execution_device="cpu")
        self.assertEqual(hardware["gpus"], [])

    def test_gpu_equipped_machine_running_cpu_only_does_not_report_the_gpu(self) -> None:
        """A GPU-equipped machine that performed CPU-only inference must not have its idle
        GPU promoted into the normalized hardware; that GPU did not perform inference."""
        environment = {
            "operating_system": "Test Linux",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [
                {
                    "vendor": "nvidia",
                    "model": "NVIDIA RTX 500 Ada Generation Laptop GPU",
                    "memory_gb": 4.0,
                    "driver_version": "596.71",
                }
            ],
        }
        hardware = hardware_from_environment(environment, None, None, execution_device="cpu")
        self.assertEqual(hardware["gpus"], [])
        self.assertIsNone(hardware["driver_version"])

    def test_gpu_memory_gb_override_is_rejected_for_cpu_execution(self) -> None:
        environment = {"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64}
        with self.assertRaisesRegex(ValueError, "cannot be used when --execution-device is cpu"):
            hardware_from_environment(environment, None, None, gpu_memory_gb_override=16.0, execution_device="cpu")

    def test_cpu_override_marks_hardware_detection_user_confirmed(self) -> None:
        environment = {"operating_system": "Test Linux", "cpu": "Detected CPU", "ram_gb": 64}
        hardware = hardware_from_environment(environment, "Overridden CPU", None, execution_device="cpu")
        self.assertEqual(hardware["cpu"], "Overridden CPU")
        self.assertEqual(hardware["detection"]["hardware"], "user-confirmed")


def build_staged_run_directory(root: Path, environment: dict | None = None) -> Path:
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
    default_environment = {
        "operating_system": "Test Linux",
        "cpu": "Test CPU",
        "ram_gb": 64,
        "nvidia_gpus": [
            {"model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"},
            {"model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"},
        ],
    }
    (run_directory / "environment.json").write_text(
        json.dumps(environment if environment is not None else default_environment),
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
        gpu_memory_gb=None,
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

    def test_cpu_and_gpu_are_both_valid_execution_devices(self) -> None:
        """CPU-only benchmarks are first-class, publishable OmoBench results."""
        validate_execution_device("cpu")
        validate_execution_device("gpu")
        with self.assertRaisesRegex(ValueError, "must be 'cpu' or 'gpu'"):
            validate_execution_device("auto")

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
            self.assertEqual(result["hardware"]["gpus"][0]["vendor"], "nvidia")
            self.assertEqual(
                result["artifacts"]["raw_runner_output"]["path"],
                "guidellm-benchmarks.json.gz",
            )
            self.assertTrue(result["configuration_fingerprint"].startswith("sha256:"))
            self.assertNotIn("server_description", result["runtime"])

    def test_generic_amd_environment_packages_and_emits_vendor_amd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={
                    "operating_system": "Test Linux",
                    "cpu": "Test CPU",
                    "ram_gb": 64,
                    "gpus": [
                        {"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": 192, "driver_version": "6.2.1"}
                    ],
                },
            )
            args = base_package_args(run_directory)
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(
                result["hardware"]["gpus"],
                [{"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": 192.0, "count": 1}],
            )

    def test_generic_amd_environment_with_unknown_memory_requires_gpu_memory_gb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={
                    "operating_system": "Test Linux",
                    "cpu": "Test CPU",
                    "ram_gb": 64,
                    "gpus": [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "driver_version": "31.0"}],
                },
            )
            original_directory = Path.cwd()

            args_without_override = base_package_args(run_directory)
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        exit_code = package_local_run(args_without_override)
            finally:
                os.chdir(original_directory)
            self.assertEqual(exit_code, 2)
            self.assertIn("unknown memory", stderr.getvalue())
            self.assertFalse((run_directory / "result.json").exists())

            args_with_override = base_package_args(run_directory, gpu_memory_gb=16.0)
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args_with_override), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(
                result["hardware"]["gpus"],
                [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": 16.0, "count": 1}],
            )
            self.assertEqual(result["hardware"]["detection"]["hardware"], "user-confirmed")

    def test_missing_detected_gpu_still_blocks_gpu_packaging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64, "gpus": []},
            )
            args = base_package_args(run_directory)
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
            self.assertIn("No GPUs were detected", stderr.getvalue())
            self.assertFalse((run_directory / "result.json").exists())

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

    def test_gpu_fingerprint_matches_the_pre_execution_device_historical_format(self) -> None:
        """A gpu result's configuration_fingerprint must still equal what pre-change
        code (whose payload never had an execution_device key) would have computed
        for the same model/runtime/hardware/benchmark. Otherwise an old GPU bundle
        and a newly repackaged, otherwise-identical GPU run would never collide under
        the validator's (configuration_fingerprint, raw_output_sha256) duplicate key,
        silently breaking duplicate detection across the schema change."""
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

            # The exact pre-execution_device fingerprint payload shape, built from
            # this same run's normalized fields.
            historical_payload = {
                "model": result["model"],
                "runtime": result["runtime"],
                "hardware": {
                    "os": result["hardware"]["os"],
                    "cpu": result["hardware"]["cpu"],
                    "ram_gb": result["hardware"]["ram_gb"],
                    "gpus": result["hardware"]["gpus"],
                    "driver_version": result["hardware"]["driver_version"],
                },
                "benchmark": {
                    "profile_id": result["benchmark"]["profile_id"],
                    "profile_sha256": result["benchmark"]["profile_sha256"],
                },
            }
            self.assertEqual(result["configuration_fingerprint"], sha256_json(historical_payload))

    def test_cpu_execution_device_packages_without_any_gpu_evidence(self) -> None:
        """CPU results must not require GPU detection, VRAM, or GPU drivers."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64},
            )
            args = base_package_args(run_directory, execution_device="cpu")
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["execution_device"], "cpu")
            self.assertEqual(result["hardware"]["gpus"], [])
            self.assertIsNone(result["hardware"]["driver_version"])
            self.assertTrue(result["configuration_fingerprint"].startswith("sha256:"))

    def test_gpu_equipped_machine_performing_cpu_only_inference_does_not_report_the_gpu(self) -> None:
        """A machine with a real, detected GPU that nonetheless ran inference on the CPU
        must not have that idle GPU represented as having performed inference."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={
                    "operating_system": "Test Linux",
                    "cpu": "Test CPU",
                    "ram_gb": 64,
                    "gpus": [
                        {
                            "vendor": "nvidia",
                            "model": "NVIDIA RTX 500 Ada Generation Laptop GPU",
                            "memory_gb": 4.0,
                            "driver_version": "596.71",
                        }
                    ],
                },
            )
            args = base_package_args(run_directory, execution_device="cpu")
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["execution_device"], "cpu")
            self.assertEqual(result["hardware"]["gpus"], [])
            self.assertIsNone(result["hardware"]["driver_version"])

    def test_gpu_memory_gb_override_is_rejected_when_packaging_a_cpu_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64},
            )
            args = base_package_args(run_directory, execution_device="cpu", gpu_memory_gb=16.0)
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
            self.assertIn("cannot be used when --execution-device is cpu", stderr.getvalue())
            self.assertFalse((run_directory / "result.json").exists())

    def test_cpu_and_gpu_runs_with_identical_settings_are_distinct_configurations(self) -> None:
        """Execution device must be part of configuration identity: an otherwise-identical
        CPU and GPU run must not collapse into the same configuration_fingerprint."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={
                    "operating_system": "Test Linux",
                    "cpu": "Test CPU",
                    "ram_gb": 64,
                    "gpus": [{"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"}],
                },
            )
            original_directory = Path.cwd()

            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(base_package_args(run_directory, execution_device="gpu")), 0)
            finally:
                os.chdir(original_directory)
            gpu_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(
                        package_local_run(base_package_args(run_directory, execution_device="cpu", force=True)), 0
                    )
            finally:
                os.chdir(original_directory)
            cpu_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            self.assertNotEqual(gpu_result["configuration_fingerprint"], cpu_result["configuration_fingerprint"])

    def test_different_cpu_configurations_do_not_collapse_to_one_identity(self) -> None:
        """Two different CPU machines/configs must not share a configuration_fingerprint
        just because they both have an empty gpus list."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={"operating_system": "Test Linux", "cpu": "CPU A", "ram_gb": 64},
            )
            original_directory = Path.cwd()

            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(base_package_args(run_directory, execution_device="cpu")), 0)
            finally:
                os.chdir(original_directory)
            first_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(
                        package_local_run(
                            base_package_args(run_directory, execution_device="cpu", cpu="CPU B", force=True)
                        ),
                        0,
                    )
            finally:
                os.chdir(original_directory)
            second_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            self.assertEqual(first_result["hardware"]["gpus"], [])
            self.assertEqual(second_result["hardware"]["gpus"], [])
            self.assertNotEqual(first_result["hardware"]["cpu"], second_result["hardware"]["cpu"])
            self.assertNotEqual(
                first_result["configuration_fingerprint"], second_result["configuration_fingerprint"]
            )


if __name__ == "__main__":
    unittest.main()
