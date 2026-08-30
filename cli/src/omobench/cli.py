"""Small, dependency-free starting point for the OmoBench CLI."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import __version__


GUIDELLM_VERSION = "0.7.3"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "chat-small-v1": {
        "input_tokens": 1024,
        "output_tokens": 256,
        "profile": "kind=synchronous,warmup=0.1,cooldown=0.1",
        "max_requests": 50,
        "max_duration_seconds": 900,
    },
    "chat-long-v1": {
        "input_tokens": 8192,
        "output_tokens": 512,
        "profile": "kind=synchronous,warmup=0.1,cooldown=0.1",
        "max_requests": 30,
        "max_duration_seconds": 1800,
    },
    "throughput-v1": {
        "input_tokens": 1024,
        "output_tokens": 256,
        "profile": "kind=concurrent,streams=16,max_concurrency=16,warmup=0.1,cooldown=0.1",
        "max_requests": 160,
        "max_duration_seconds": 900,
    },
}


def command_output(command: list[str]) -> str | None:
    """Return a command's combined output, or None when it is unavailable."""
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip()
    return output or None


def executable_version(executable: str) -> str | None:
    if shutil.which(executable) is None:
        return None
    output = command_output([executable, "--version"])
    return output.splitlines()[0] if output else None


def detect_nvidia_gpus() -> list[dict[str, Any]]:
    output = command_output(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    if not output:
        return []

    gpus: list[dict[str, Any]] = []
    for row in output.splitlines():
        parts = [part.strip() for part in row.split(",")]
        if len(parts) != 3:
            continue
        name, memory_mb, driver_version = parts
        try:
            memory_gb = round(int(memory_mb) / 1024, 2)
        except ValueError:
            memory_gb = None
        gpus.append(
            {
                "model": name,
                "memory_gb": memory_gb,
                "driver_version": driver_version,
                "detected_by": "nvidia-smi",
            }
        )
    return gpus


def detect_cpu() -> str | None:
    detected = platform.processor().strip()
    if detected:
        return detected
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", maxsplit=1)[1].strip() or None
        except OSError:
            pass
    return platform.machine().strip() or None


def detect_ram_gb() -> float | None:
    if sys.platform == "win32":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            memory = MemoryStatus()
            memory.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                return round(memory.total_physical / (1024**3), 2)
        except (AttributeError, OSError):
            return None

    try:
        return round(
            os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3),
            2,
        )
    except (AttributeError, OSError, ValueError):
        return None


def doctor_report() -> dict[str, Any]:
    return {
        "omobench_cli_version": __version__,
        "python": sys.version.split()[0],
        "operating_system": platform.platform(),
        "cpu": detect_cpu(),
        "ram_gb": detect_ram_gb(),
        "guidellm": executable_version("guidellm"),
        "git": executable_version("git"),
        "github_cli": executable_version("gh"),
        "nvidia_gpus": detect_nvidia_gpus(),
    }


def find_repository_root() -> Path | None:
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "benchmarks" / "profiles").is_dir():
            return candidate
    return None


def list_profiles() -> int:
    root = find_repository_root()
    if root is None:
        print("Could not find benchmarks/profiles. Run this command from an OmoBench checkout.", file=sys.stderr)
        return 2

    for profile in sorted((root / "benchmarks" / "profiles").glob("*.yaml")):
        print(profile.stem)
    return 0


def parsed_guidellm_version(version_text: str | None) -> str | None:
    if version_text is None:
        return None
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", version_text)
    return match.group(1) if match else None


def build_guidellm_command(
    *,
    endpoint: str,
    model: str,
    profile_id: str,
    raw_output_path: Path,
) -> list[str]:
    spec = PROFILE_SPECS[profile_id]
    target = endpoint.rstrip("/")
    return [
        "guidellm",
        "run",
        "--backend",
        f"kind=openai_http,target={target},model={model},request_format=/v1/chat/completions",
        "--data",
        f"kind=synthetic_text,prompt_tokens={spec['input_tokens']},output_tokens={spec['output_tokens']}",
        "--profile",
        spec["profile"],
        "--constraint",
        f"kind=max_requests,count={spec['max_requests']}",
        "--constraint",
        f"kind=max_duration,seconds={spec['max_duration_seconds']}",
        "--constraint",
        "kind=max_error_rate,rate=0.01",
        "--seed",
        "kind=static,value=20260828",
        "--metrics",
        "kind=generative,sample_size=0",
        "--output",
        f"kind=json,path={raw_output_path}",
        "--disable-console-interactive",
    ]


def redact_endpoint(text: str, endpoint: str) -> str:
    return text.replace(endpoint.rstrip("/"), "<LOCAL_ENDPOINT>")


def compress_file(path: Path) -> Path:
    compressed_path = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as source, gzip.open(compressed_path, "wb") as destination:
        shutil.copyfileobj(source, destination)
    path.unlink()
    return compressed_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value.strip()


def report_value(report: dict[str, Any], *path: str) -> Any:
    current: Any = report
    for key in path:
        if not isinstance(current, dict) or key not in current:
            location = ".".join(path)
            raise ValueError(f"GuideLLM report is missing {location}.")
        current = current[key]
    return current


def report_number(report: dict[str, Any], *path: str) -> float:
    value = report_value(report, *path)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"GuideLLM report field {'.'.join(path)} must be numeric.")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"GuideLLM report field {'.'.join(path)} must be a finite non-negative number.")
    return number


def report_count(report: dict[str, Any], *path: str) -> int:
    value = report_number(report, *path)
    if not value.is_integer():
        raise ValueError(f"GuideLLM report field {'.'.join(path)} must be a whole number.")
    return int(value)


def normalize_guidellm_report(raw_report: dict[str, Any]) -> dict[str, Any]:
    """Extract the stable MVP metrics from one pinned GuideLLM report."""
    report_version = report_value(raw_report, "metadata", "version")
    if report_version != 2:
        raise ValueError(f"Unsupported GuideLLM report schema version: {report_version!r}.")
    runner_version = required_text(
        report_value(raw_report, "metadata", "guidellm_version"),
        "GuideLLM version",
    )
    if runner_version != GUIDELLM_VERSION:
        raise ValueError(
            f"OmoBench v1 requires GuideLLM {GUIDELLM_VERSION}; report was created by {runner_version}."
        )

    benchmarks = report_value(raw_report, "benchmarks")
    if not isinstance(benchmarks, list) or len(benchmarks) != 1:
        raise ValueError("OmoBench v1 profiles must produce exactly one GuideLLM benchmark.")
    benchmark = benchmarks[0]
    if not isinstance(benchmark, dict):
        raise ValueError("GuideLLM benchmark entry must be an object.")

    successful = report_count(benchmark, "scheduler_metrics", "requests_made", "successful")
    incomplete = report_count(benchmark, "scheduler_metrics", "requests_made", "incomplete")
    errored = report_count(benchmark, "scheduler_metrics", "requests_made", "errored")
    attempted = report_count(benchmark, "scheduler_metrics", "requests_made", "total")
    failed = incomplete + errored
    if attempted != successful + failed:
        raise ValueError("GuideLLM request counts are inconsistent.")
    if attempted == 0:
        raise ValueError("GuideLLM report contains no attempted requests.")
    if successful == 0:
        raise ValueError("GuideLLM report contains no successful requests.")

    started_at_seconds = report_number(benchmark, "scheduler_metrics", "measure_start_time")
    return {
        "started_at": datetime.fromtimestamp(started_at_seconds, UTC).isoformat(),
        "runner_version": runner_version,
        "results": {
            "requests": {
                "attempted": attempted,
                "succeeded": successful,
                "failed": failed,
            },
            "latency_ms": {
                "ttft": {
                    "p50": report_number(
                        benchmark,
                        "metrics",
                        "time_to_first_token_ms",
                        "successful",
                        "percentiles",
                        "p50",
                    ),
                    "p95": report_number(
                        benchmark,
                        "metrics",
                        "time_to_first_token_ms",
                        "successful",
                        "percentiles",
                        "p95",
                    ),
                },
                "inter_token": {
                    "p50": report_number(
                        benchmark,
                        "metrics",
                        "inter_token_latency_ms",
                        "successful",
                        "percentiles",
                        "p50",
                    ),
                    "p95": report_number(
                        benchmark,
                        "metrics",
                        "inter_token_latency_ms",
                        "successful",
                        "percentiles",
                        "p95",
                    ),
                },
            },
            "output_tokens_per_second": report_number(
                benchmark,
                "metrics",
                "output_tokens_per_second",
                "total",
                "mean",
            ),
            "total_tokens_per_second": report_number(
                benchmark,
                "metrics",
                "tokens_per_second",
                "total",
                "mean",
            ),
            "requests_per_second": report_number(
                benchmark,
                "metrics",
                "requests_per_second",
                "total",
                "mean",
            ),
            "error_rate_percent": 100 * failed / attempted,
            "memory_peak_gb": None,
        },
    }


def load_gzip_json(path: Path) -> dict[str, Any]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            parsed = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read compressed GuideLLM JSON at {path}: {error}") from error
    if not isinstance(parsed, dict):
        raise ValueError("GuideLLM JSON report must contain an object at its root.")
    return parsed


def current_git_commit() -> str | None:
    commit = command_output(["git", "rev-parse", "HEAD"])
    if commit and re.fullmatch(r"[0-9a-f]{40}", commit):
        return commit
    return None


def artifact_metadata(path: Path, run_directory: Path) -> dict[str, str]:
    try:
        relative_path = path.resolve().relative_to(run_directory.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Artifact {path} must stay inside {run_directory}.") from error
    return {"path": relative_path, "sha256": sha256_file(path)}


def hardware_from_environment(
    environment: dict[str, Any],
    cpu_override: str | None,
    ram_gb_override: float | None,
) -> dict[str, Any]:
    cpu = required_text(cpu_override or environment.get("cpu"), "CPU")
    ram_gb_value = ram_gb_override if ram_gb_override is not None else environment.get("ram_gb")
    if isinstance(ram_gb_value, bool) or not isinstance(ram_gb_value, int | float):
        raise ValueError("RAM was not detected. Supply --ram-gb when packaging the run.")
    ram_gb = float(ram_gb_value)
    if not math.isfinite(ram_gb) or ram_gb <= 0:
        raise ValueError("RAM must be a finite positive number.")

    detected_gpus = environment.get("nvidia_gpus")
    if not isinstance(detected_gpus, list) or not detected_gpus:
        raise ValueError("No NVIDIA GPUs were detected for this run; OmoBench v1 packaging requires a detected GPU.")

    grouped_gpus: dict[tuple[str, float], int] = {}
    driver_versions: set[str] = set()
    for index, gpu in enumerate(detected_gpus, start=1):
        if not isinstance(gpu, dict):
            raise ValueError(f"Detected GPU {index} is invalid.")
        model = required_text(gpu.get("model"), f"Detected GPU {index} model")
        memory_value = gpu.get("memory_gb")
        if isinstance(memory_value, bool) or not isinstance(memory_value, int | float):
            raise ValueError(f"Detected GPU {index} memory is invalid.")
        memory_gb = float(memory_value)
        if not math.isfinite(memory_gb) or memory_gb <= 0:
            raise ValueError(f"Detected GPU {index} memory must be positive.")
        grouped_gpus[(model, memory_gb)] = grouped_gpus.get((model, memory_gb), 0) + 1
        driver = gpu.get("driver_version")
        if isinstance(driver, str) and driver.strip():
            driver_versions.add(driver.strip())

    gpus = [
        {"model": model, "memory_gb": memory_gb, "count": count}
        for (model, memory_gb), count in sorted(grouped_gpus.items())
    ]
    return {
        "os": required_text(environment.get("operating_system"), "Operating system"),
        "cpu": cpu,
        "ram_gb": ram_gb,
        "gpus": gpus,
        "driver_version": next(iter(driver_versions)) if len(driver_versions) == 1 else None,
        "detection": {
            "hardware": "user-confirmed" if cpu_override or ram_gb_override is not None else "auto",
            "model": "manual",
            "runtime": "manual",
        },
    }


def write_local_run(args: argparse.Namespace) -> int:
    root = find_repository_root()
    if root is None:
        print("Could not find an OmoBench checkout.", file=sys.stderr)
        return 2
    if args.profile not in PROFILE_SPECS:
        print(f"Unknown profile: {args.profile}", file=sys.stderr)
        return 2

    run_id = f"run-{uuid4().hex[:12]}"
    run_directory = (root / args.output_dir / run_id).resolve()
    try:
        run_directory.relative_to(root.resolve())
    except ValueError:
        print("--output-dir must stay inside the OmoBench checkout.", file=sys.stderr)
        return 2
    raw_output_path = run_directory / "guidellm-benchmarks.json"
    command = build_guidellm_command(
        endpoint=args.endpoint,
        model=args.model,
        profile_id=args.profile,
        raw_output_path=raw_output_path,
    )
    safe_command = redact_endpoint(shlex.join(command), args.endpoint)

    if not args.execute:
        print("Dry run — this command has not contacted the endpoint:\n")
        print(safe_command)
        print("\nAdd --execute to create a local evidence bundle under .omobench-runs/.")
        return 0

    installed_version = parsed_guidellm_version(executable_version("guidellm"))
    if installed_version is None:
        print(
            "GuideLLM is not installed. Run: pip install -e './cli[runner]'",
            file=sys.stderr,
        )
        return 2
    if installed_version != GUIDELLM_VERSION:
        print(
            f"OmoBench v1 requires GuideLLM {GUIDELLM_VERSION}; found {installed_version}.",
            file=sys.stderr,
        )
        return 2

    run_directory.mkdir(parents=True, exist_ok=False)
    log_path = run_directory / "guidellm.stdout.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        completed = subprocess.run(command, stdout=log_file, stderr=subprocess.STDOUT, text=True)

    log_path.write_text(
        redact_endpoint(log_path.read_text(encoding="utf-8"), args.endpoint),
        encoding="utf-8",
    )
    environment_path = run_directory / "environment.json"
    environment_path.write_text(json.dumps(doctor_report(), indent=2) + "\n", encoding="utf-8")
    reproduce_path = run_directory / "reproduce.md"
    reproduce_path.write_text(
        "# Reproduce this local staging run\n\n"
        f"GuideLLM version: `{GUIDELLM_VERSION}`\n\n"
        "Replace `<LOCAL_ENDPOINT>` with the local OpenAI-compatible endpoint.\n\n"
        "```bash\n"
        f"{safe_command}\n"
        "```\n",
        encoding="utf-8",
    )

    if raw_output_path.exists():
        compress_file(raw_output_path)

    if completed.returncode != 0:
        print(f"GuideLLM failed; inspect {log_path}", file=sys.stderr)
        return completed.returncode

    compressed_raw_output = raw_output_path.with_suffix(".json.gz")
    if not compressed_raw_output.exists():
        print("GuideLLM completed but did not write its JSON output.", file=sys.stderr)
        return 1

    print(f"Created local staging evidence in {run_directory}")
    print("This is intentionally not a public result yet; normalization and submission follow next.")
    return 0


def package_local_run(args: argparse.Namespace) -> int:
    """Convert a completed local staging run into a schema-ready result summary."""
    root = find_repository_root()
    if root is None:
        print("Could not find an OmoBench checkout.", file=sys.stderr)
        return 2
    if args.profile not in PROFILE_SPECS:
        print(f"Unknown profile: {args.profile}", file=sys.stderr)
        return 2

    run_directory = Path(args.run_dir).expanduser()
    if not run_directory.is_absolute():
        run_directory = Path.cwd() / run_directory
    run_directory = run_directory.resolve()
    try:
        run_directory.relative_to(root.resolve())
    except ValueError:
        print("--run-dir must stay inside the OmoBench checkout.", file=sys.stderr)
        return 2
    if not run_directory.is_dir():
        print(f"Run directory does not exist: {run_directory}", file=sys.stderr)
        return 2

    run_id = required_text(args.run_id or run_directory.name, "Run ID")
    if not RUN_ID_PATTERN.fullmatch(run_id):
        print("Run ID must use lowercase letters, digits, and hyphens (8–64 characters).", file=sys.stderr)
        return 2
    if args.run_kind == "repeat" and not args.repeat_of_run_id:
        print("--repeat-of-run-id is required when --run-kind is repeat.", file=sys.stderr)
        return 2
    if args.run_kind != "repeat" and args.repeat_of_run_id:
        print("--repeat-of-run-id can only be used when --run-kind is repeat.", file=sys.stderr)
        return 2

    artifact_paths = {
        "raw_runner_output": run_directory / "guidellm-benchmarks.json.gz",
        "runner_stdout_log": run_directory / "guidellm.stdout.log",
        "environment_snapshot": run_directory / "environment.json",
        "reproduce_markdown": run_directory / "reproduce.md",
    }
    missing = [name for name, path in artifact_paths.items() if not path.is_file()]
    if missing:
        print(f"Run directory is missing required evidence: {', '.join(missing)}", file=sys.stderr)
        return 2

    result_path = run_directory / "result.json"
    if result_path.exists() and not args.force:
        print(f"Refusing to overwrite {result_path}. Use --force to replace it.", file=sys.stderr)
        return 2

    try:
        raw_report = load_gzip_json(artifact_paths["raw_runner_output"])
        normalized = normalize_guidellm_report(raw_report)
        environment = json.loads(
            artifact_paths["environment_snapshot"].read_text(encoding="utf-8")
        )
        if not isinstance(environment, dict):
            raise ValueError("environment.json must contain an object at its root.")
        hardware = hardware_from_environment(environment, args.cpu, args.ram_gb)
        profile_path = root / "benchmarks" / "profiles" / f"{args.profile}.yaml"
        if not profile_path.is_file():
            raise ValueError(f"Missing profile definition: {profile_path}")

        model = {
            "id": required_text(args.model, "Model ID"),
            "revision": required_text(args.model_revision, "Model revision"),
            "quantization": (
                required_text(args.quantization, "Quantization")
                if args.quantization is not None
                else None
            ),
        }
        runtime = {
            "framework": required_text(args.framework, "Framework"),
            "version": required_text(args.framework_version, "Framework version"),
            "arguments": [
                required_text(argument, "Runtime argument")
                for argument in (args.runtime_arg or [])
            ],
            "endpoint_type": "openai-compatible",
        }
        benchmark = {
            "profile_id": args.profile,
            "profile_sha256": sha256_file(profile_path),
            "runner": "guidellm",
            "runner_version": normalized["runner_version"],
        }
        fingerprint_payload = {
            "model": model,
            "runtime": runtime,
            "hardware": {
                "os": hardware["os"],
                "cpu": hardware["cpu"],
                "ram_gb": hardware["ram_gb"],
                "gpus": hardware["gpus"],
                "driver_version": hardware["driver_version"],
            },
            "benchmark": {
                "profile_id": benchmark["profile_id"],
                "profile_sha256": benchmark["profile_sha256"],
            },
        }
        result = {
            "schema_version": "1.0",
            "run_id": run_id,
            "started_at": normalized["started_at"],
            "configuration_fingerprint": sha256_json(fingerprint_payload),
            "model": model,
            "runtime": runtime,
            "hardware": hardware,
            "benchmark": benchmark,
            "results": normalized["results"],
            "artifacts": {
                name: artifact_metadata(path, run_directory)
                for name, path in artifact_paths.items()
            },
            "submission": {
                "submitter": required_text(args.submitter, "Submitter"),
                "run_kind": args.run_kind,
                "repeat_of_run_id": args.repeat_of_run_id,
                "source_commit": current_git_commit(),
            },
        }
    except ValueError as error:
        print(f"Could not package local run: {error}", file=sys.stderr)
        return 2

    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Created normalized local result at {result_path}")
    print("This is still local evidence. Copy the complete run directory into results/ only when you are ready to submit it.")
    return 0


def write_run_intent(args: argparse.Namespace) -> int:
    root = find_repository_root()
    if root is None:
        print("Could not find an OmoBench checkout.", file=sys.stderr)
        return 2

    profile_path = root / "benchmarks" / "profiles" / f"{args.profile}.yaml"
    if not profile_path.exists():
        print(f"Unknown profile: {args.profile}", file=sys.stderr)
        return 2

    output_path = Path(args.output).expanduser()
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    if output_path.exists() and not args.force:
        print(f"Refusing to overwrite {output_path}. Use --force to replace it.", file=sys.stderr)
        return 2

    intent = {
        "state": "prepared",
        "created_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "endpoint": args.endpoint,
        "profile": args.profile,
        "profile_path": profile_path.relative_to(root).as_posix(),
        "detected_environment": doctor_report(),
        "next_step": "Run the benchmark adapter and write the normalized result plus raw evidence.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote benchmark intent to {output_path}")
    return 0


def print_doctor(report: dict[str, Any]) -> None:
    print(f"OmoBench CLI: {report['omobench_cli_version']}")
    print(f"Python: {report['python']}")
    print(f"OS: {report['operating_system']}")
    print(f"CPU: {report['cpu'] or 'not detected'}")
    ram_gb = report["ram_gb"]
    print(f"RAM: {ram_gb} GB" if ram_gb is not None else "RAM: not detected")
    print(f"GuideLLM: {report['guidellm'] or 'not found'}")
    print(f"Git: {report['git'] or 'not found'}")
    print(f"GitHub CLI: {report['github_cli'] or 'not found'}")
    gpus = report["nvidia_gpus"]
    if not gpus:
        print("NVIDIA GPUs: none detected")
        return
    print("NVIDIA GPUs:")
    for gpu in gpus:
        memory = f"{gpu['memory_gb']} GB" if gpu["memory_gb"] is not None else "unknown memory"
        print(f"  - {gpu['model']} ({memory}, driver {gpu['driver_version']})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omobench", description="OmoBench submission tooling")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor = subcommands.add_parser("doctor", help="inspect local benchmark prerequisites")
    doctor.add_argument("--json", action="store_true", help="print machine-readable output")

    subcommands.add_parser("profiles", help="list versioned benchmark profiles")

    intent = subcommands.add_parser("init-run", help="prepare a benchmark intent without running it")
    intent.add_argument("--endpoint", required=True, help="OpenAI-compatible endpoint base URL")
    intent.add_argument("--model", required=True, help="Canonical model ID")
    intent.add_argument("--profile", required=True, help="Versioned OmoBench profile ID")
    intent.add_argument("--output", default="run-intent.json", help="Intent JSON path")
    intent.add_argument("--force", action="store_true", help="allow replacement of an existing output file")

    run = subcommands.add_parser("run", help="create or execute a pinned GuideLLM staging run")
    run.add_argument("--endpoint", required=True, help="local OpenAI-compatible endpoint base URL")
    run.add_argument("--model", required=True, help="canonical model ID served by the endpoint")
    run.add_argument("--profile", required=True, choices=sorted(PROFILE_SPECS), help="versioned OmoBench profile")
    run.add_argument("--output-dir", default=".omobench-runs", help="local staging directory, relative to repository root")
    run.add_argument("--execute", action="store_true", help="actually invoke GuideLLM; omit for a safe dry run")

    package = subcommands.add_parser("package", help="normalize a completed local GuideLLM staging run")
    package.add_argument("--run-dir", required=True, help="local staging directory created by omobench run --execute")
    package.add_argument("--model", required=True, help="canonical model ID")
    package.add_argument("--model-revision", required=True, help="immutable model revision or commit SHA")
    package.add_argument("--quantization", help="quantization label, such as BF16 or Q4_K_M")
    package.add_argument("--framework", required=True, choices=["vllm", "sglang", "llama.cpp"], help="inference framework")
    package.add_argument("--framework-version", required=True, help="exact framework version")
    package.add_argument("--runtime-arg", action="append", default=[], help="one normalized framework startup argument; repeat as needed")
    package.add_argument("--profile", required=True, choices=sorted(PROFILE_SPECS), help="versioned OmoBench profile")
    package.add_argument("--submitter", required=True, help="GitHub username or organization submitting the run")
    package.add_argument("--run-id", help="override the staging directory name as the run ID")
    package.add_argument("--run-kind", choices=["initial", "repeat", "replication"], default="initial")
    package.add_argument("--repeat-of-run-id", help="previous run ID when this is a repeat")
    package.add_argument("--cpu", help="override the detected CPU description")
    package.add_argument("--ram-gb", type=float, help="override detected RAM in gigabytes")
    package.add_argument("--force", action="store_true", help="allow replacement of an existing local result.json")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "doctor":
        report = doctor_report()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print_doctor(report)
        return 0
    if args.command == "profiles":
        return list_profiles()
    if args.command == "init-run":
        return write_run_intent(args)
    if args.command == "run":
        return write_local_run(args)
    if args.command == "package":
        return package_local_run(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
