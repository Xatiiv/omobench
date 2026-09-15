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
from urllib.parse import urlsplit
from uuid import uuid4

from . import __version__


GUIDELLM_VERSION = "0.7.3"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
RUNTIME_FRAMEWORKS = ["vllm", "sglang", "llama.cpp", "lmstudio", "openai-compatible-other"]
OTHER_SERVER_FRAMEWORK = "openai-compatible-other"
GPU_VENDORS = ("nvidia", "amd", "intel", "other")

# Substring needles (checked against a lowercased GPU name) used to classify a
# vendor-neutral detection (Windows CIM, Linux lspci) that has no vendor tool
# of its own to ask directly. Order matters only in that each vendor's list is
# checked independently; ambiguous/unmatched names fall back to "other".
GPU_VENDOR_NAME_NEEDLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nvidia", ("nvidia", "geforce", "quadro", "tesla", "rtx", "gtx")),
    ("amd", ("amd", "radeon", "instinct", "firepro")),
    ("intel", ("intel", "arc graphics", "iris", "uhd graphics")),
)

# Windows Win32_VideoController rows that are not a real, physical accelerator
# (remote/virtual/basic display stacks). Matched as a case-insensitive
# substring of the reported name.
NON_PHYSICAL_DISPLAY_ADAPTER_NEEDLES = (
    "microsoft basic display",
    "microsoft basic render",
    "microsoft remote display",
    "microsoft hyper-v video",
    "remote desktop",
    "vmware svga",
    "virtualbox graphics",
    "parsec virtual display",
    "citrix",
)


def classify_gpu_vendor(model_name: str) -> str:
    """Best-effort vendor classification from a free-text GPU name.

    Used only by vendor-neutral OS fallbacks (Windows CIM, Linux lspci) that
    report a name string but not a vendor. nvidia-smi/rocm-smi already know
    their own vendor and never call this.
    """
    lowered = model_name.lower()
    for vendor, needles in GPU_VENDOR_NAME_NEEDLES:
        if any(needle in lowered for needle in needles):
            return vendor
    return "other"


def is_non_physical_display_adapter(name: str) -> bool:
    lowered = name.lower()
    return any(needle in lowered for needle in NON_PHYSICAL_DISPLAY_ADAPTER_NEEDLES)


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
    except (OSError, subprocess.TimeoutExpired):
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
                "vendor": "nvidia",
                "model": name,
                "memory_gb": memory_gb,
                "driver_version": driver_version,
                "detected_by": "nvidia-smi",
            }
        )
    return gpus


def detect_amd_gpus() -> list[dict[str, Any]]:
    """Best-effort AMD GPU detection via rocm-smi (Linux only).

    Does not require the user to install ROCm: this only runs when rocm-smi
    is already on PATH, and any missing/unparseable field degrades to an
    empty result rather than raising.
    """
    if sys.platform == "win32":
        return []
    output = command_output(
        ["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--showdriverversion", "--json"]
    )
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []

    gpus: list[dict[str, Any]] = []
    for key, card in parsed.items():
        if not isinstance(card, dict) or not key.lower().startswith("card"):
            continue
        name = (
            card.get("Card series")
            or card.get("Card Series")
            or card.get("Device Name")
            or "AMD GPU"
        )
        memory_gb = None
        for memory_key in ("VRAM Total Memory (B)", "VRAM Total Memory(B)", "VRAM Total (B)"):
            raw_memory = card.get(memory_key)
            if raw_memory is None:
                continue
            try:
                memory_gb = round(int(raw_memory) / (1024**3), 2)
            except (TypeError, ValueError):
                memory_gb = None
            break
        driver_version = card.get("Driver version") or card.get("Driver Version")
        gpus.append(
            {
                "vendor": "amd",
                "model": str(name),
                "memory_gb": memory_gb,
                "driver_version": str(driver_version).strip() if driver_version else None,
                "detected_by": "rocm-smi",
            }
        )
    return gpus


def detect_windows_cim_gpus() -> list[dict[str, Any]]:
    """Best-effort, vendor-neutral Windows GPU fallback via Win32_VideoController.

    Deliberately does not read AdapterRAM: it is capped/unreliable under
    modern Windows drivers (observed reporting 4 GB for a 16 GB RX 9070), so
    memory is always null here and must come from a specialised tool (for
    example nvidia-smi) or --gpu-memory-gb at packaging time.
    """
    if sys.platform != "win32":
        return []
    output = command_output(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion | ConvertTo-Json -Compress",
        ]
    )
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return []
    entries = parsed if isinstance(parsed, list) else [parsed]

    gpus: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("Name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        if is_non_physical_display_adapter(name):
            continue
        driver_version = entry.get("DriverVersion")
        gpus.append(
            {
                "vendor": classify_gpu_vendor(name),
                "model": name,
                "memory_gb": None,
                "driver_version": (
                    driver_version.strip() if isinstance(driver_version, str) and driver_version.strip() else None
                ),
                "detected_by": "win32_videocontroller",
            }
        )
    return gpus


def detect_linux_pci_gpus() -> list[dict[str, Any]]:
    """Best-effort, vendor-neutral Linux GPU fallback via lspci.

    Used so AMD/Intel hardware is not invisible merely because rocm-smi (or
    an equivalent vendor tool) is absent. lspci exposes no memory or driver
    version, so both are always null here.
    """
    if not sys.platform.startswith("linux"):
        return []
    output = command_output(["lspci", "-mm"])
    if not output:
        return []

    gpu_classes = ("vga compatible controller", "3d controller", "display controller")
    gpus: list[dict[str, Any]] = []
    for line in output.splitlines():
        try:
            fields = shlex.split(line)
        except ValueError:
            continue
        if len(fields) < 4:
            continue
        device_class = fields[1].lower()
        if not any(device_class.startswith(gpu_class) for gpu_class in gpu_classes):
            continue
        model = f"{fields[2]} {fields[3]}".strip()
        if not model:
            continue
        gpus.append(
            {
                "vendor": classify_gpu_vendor(model),
                "model": model,
                "memory_gb": None,
                "driver_version": None,
                "detected_by": "lspci",
            }
        )
    return gpus


def detect_gpus() -> list[dict[str, Any]]:
    """Vendor-neutral GPU aggregator.

    Specialised vendor tools (nvidia-smi, rocm-smi) are preferred whenever
    they succeed, since they report real memory and driver values. The OS
    fallback (Windows CIM / Linux lspci) only supplies vendors that no
    specialised tool already covered, so the same physical GPU is never
    reported twice and a lower-quality (null-memory) record never shadows a
    higher-quality one.
    """
    specialized = detect_nvidia_gpus() + detect_amd_gpus()
    covered_vendors = {gpu["vendor"] for gpu in specialized}

    if sys.platform == "win32":
        fallback = detect_windows_cim_gpus()
    elif sys.platform.startswith("linux"):
        fallback = detect_linux_pci_gpus()
    else:
        fallback = []

    supplemental = [gpu for gpu in fallback if gpu["vendor"] not in covered_vendors]
    return specialized + supplemental


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
        "gpus": detect_gpus(),
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


def require_https_source_url(value: Any, label: str) -> str:
    """Validate a generic, operator-supplied weights-source URL.

    Unlike managed_setup's ALLOWED_DOWNLOAD_HOSTS, this applies to any
    supported model or runtime, not a specific reviewed recipe: it only
    requires https and a complete, credential-free URL, without a host
    allowlist.
    """
    text = required_text(value, label)
    parts = urlsplit(text)
    if parts.scheme != "https":
        raise ValueError(f"{label} must use https.")
    if not parts.hostname:
        raise ValueError(f"{label} must be a complete URL.")
    if parts.username or parts.password:
        raise ValueError(f"{label} must not carry credentials.")
    return text


def validate_execution_device(execution_device: str) -> None:
    """Confirm an explicit, recognized execution device was declared.

    execution_device records what performed inference for this run, not
    merely what hardware the machine happens to have. CPU and GPU runs are
    both first-class, publishable OmoBench results; this only rejects a
    missing or unrecognized declaration.
    """
    if execution_device not in {"cpu", "gpu"}:
        raise ValueError("--execution-device must be 'cpu' or 'gpu'.")


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
    gpu_memory_gb_override: float | None = None,
    *,
    execution_device: str,
) -> dict[str, Any]:
    """Build the normalized `hardware` object for what actually performed inference.

    execution_device decides how GPUs are handled, independent of what the
    machine happens to have: a "cpu" run always records an empty gpus list
    and a null driver_version, even when the saved environment snapshot
    shows an installed GPU, because that GPU did not perform inference. The
    full detected hardware (including any idle GPU) still lives in the
    environment.json artifact; it is just not promoted into this object.
    """
    cpu = required_text(cpu_override or environment.get("cpu"), "CPU")
    ram_gb_value = ram_gb_override if ram_gb_override is not None else environment.get("ram_gb")
    if isinstance(ram_gb_value, bool) or not isinstance(ram_gb_value, int | float):
        raise ValueError("RAM was not detected. Supply --ram-gb when packaging the run.")
    ram_gb = float(ram_gb_value)
    if not math.isfinite(ram_gb) or ram_gb <= 0:
        raise ValueError("RAM must be a finite positive number.")
    os_name = required_text(environment.get("operating_system"), "Operating system")

    if execution_device == "cpu":
        if gpu_memory_gb_override is not None:
            raise ValueError("--gpu-memory-gb cannot be used when --execution-device is cpu.")
        return {
            "os": os_name,
            "cpu": cpu,
            "ram_gb": ram_gb,
            "gpus": [],
            "driver_version": None,
            "detection": {
                "hardware": "user-confirmed" if (cpu_override or ram_gb_override is not None) else "auto",
                "model": "manual",
                "runtime": "manual",
            },
        }

    if gpu_memory_gb_override is not None and (
        isinstance(gpu_memory_gb_override, bool)
        or not isinstance(gpu_memory_gb_override, int | float)
        or not math.isfinite(gpu_memory_gb_override)
        or gpu_memory_gb_override <= 0
    ):
        raise ValueError("--gpu-memory-gb must be a finite positive number.")

    # "gpus" is the generic key written by current doctor_report()/environment.json.
    # Older staged runs recorded the same shape under the legacy "nvidia_gpus"
    # key; fall back to it so an already-executed staging directory still
    # packages without having to re-run the benchmark.
    detected_gpus = environment.get("gpus")
    using_legacy_key = detected_gpus is None
    if using_legacy_key:
        detected_gpus = environment.get("nvidia_gpus")
    if not isinstance(detected_gpus, list) or not detected_gpus:
        raise ValueError("No GPUs were detected for this run; OmoBench v1 packaging requires a detected GPU.")

    parsed_gpus: list[dict[str, Any]] = []
    driver_versions: set[str] = set()
    for index, gpu in enumerate(detected_gpus, start=1):
        if not isinstance(gpu, dict):
            raise ValueError(f"Detected GPU {index} is invalid.")
        model = required_text(gpu.get("model"), f"Detected GPU {index} model")
        vendor = gpu.get("vendor")
        if vendor is None:
            # Legacy nvidia_gpus entries predate the vendor field but are, by
            # definition, NVIDIA; a hand-authored generic "gpus" entry with no
            # vendor is treated as unknown rather than guessed at.
            vendor = "nvidia" if using_legacy_key else "other"
        elif not isinstance(vendor, str) or vendor not in GPU_VENDORS:
            raise ValueError(f"Detected GPU {index} vendor is invalid.")

        memory_value = gpu.get("memory_gb")
        if memory_value is None:
            memory_gb: float | None = None
        elif isinstance(memory_value, bool) or not isinstance(memory_value, int | float):
            raise ValueError(f"Detected GPU {index} memory is invalid.")
        else:
            memory_gb = float(memory_value)
            if not math.isfinite(memory_gb) or memory_gb <= 0:
                raise ValueError(f"Detected GPU {index} memory must be positive.")

        parsed_gpus.append({"vendor": vendor, "model": model, "memory_gb": memory_gb})
        driver = gpu.get("driver_version")
        if isinstance(driver, str) and driver.strip():
            driver_versions.add(driver.strip())

    unknown_memory_gpus = [gpu for gpu in parsed_gpus if gpu["memory_gb"] is None]
    hardware_detection_state = "auto"
    if unknown_memory_gpus:
        classes = sorted({(gpu["vendor"], gpu["model"]) for gpu in unknown_memory_gpus})
        if gpu_memory_gb_override is None:
            class_list = ", ".join(f"{vendor}:{model}" for vendor, model in classes)
            raise ValueError(
                f"Detected GPU(s) with unknown memory: {class_list}. Supply --gpu-memory-gb to "
                "record their VRAM; confirm the value in the serving runtime's hardware panel first."
            )
        if len(classes) > 1:
            class_list = ", ".join(f"{vendor}:{model}" for vendor, model in classes)
            raise ValueError(
                "Cannot apply --gpu-memory-gb: unknown-memory GPUs span more than one "
                f"vendor/model class ({class_list}). This value can only be applied when every "
                "GPU with unknown memory is the same vendor and model."
            )
        for gpu in unknown_memory_gpus:
            gpu["memory_gb"] = gpu_memory_gb_override
        hardware_detection_state = "user-confirmed"
    elif gpu_memory_gb_override is not None:
        raise ValueError(
            "--gpu-memory-gb was supplied, but every detected GPU already has a known memory "
            "value; remove --gpu-memory-gb instead of overriding detected facts."
        )

    grouped_gpus: dict[tuple[str, str, float], int] = {}
    for gpu in parsed_gpus:
        key = (gpu["vendor"], gpu["model"], gpu["memory_gb"])
        grouped_gpus[key] = grouped_gpus.get(key, 0) + 1

    gpus = [
        {"vendor": vendor, "model": model, "memory_gb": memory_gb, "count": count}
        for (vendor, model, memory_gb), count in sorted(grouped_gpus.items())
    ]
    return {
        "os": os_name,
        "cpu": cpu,
        "ram_gb": ram_gb,
        "gpus": gpus,
        "driver_version": next(iter(driver_versions)) if len(driver_versions) == 1 else None,
        "detection": {
            "hardware": (
                "user-confirmed"
                if cpu_override or ram_gb_override is not None or hardware_detection_state == "user-confirmed"
                else "auto"
            ),
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
    if args.framework == OTHER_SERVER_FRAMEWORK and not (args.server_description and args.server_description.strip()):
        print(
            f"--server-description is required when --framework is {OTHER_SERVER_FRAMEWORK}.",
            file=sys.stderr,
        )
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
        validate_execution_device(args.execution_device)
        raw_report = load_gzip_json(artifact_paths["raw_runner_output"])
        normalized = normalize_guidellm_report(raw_report)
        environment = json.loads(
            artifact_paths["environment_snapshot"].read_text(encoding="utf-8")
        )
        if not isinstance(environment, dict):
            raise ValueError("environment.json must contain an object at its root.")
        hardware = hardware_from_environment(
            environment,
            args.cpu,
            args.ram_gb,
            getattr(args, "gpu_memory_gb", None),
            execution_device=args.execution_device,
        )
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
        model_source = getattr(args, "model_source", None)
        if model_source:
            # When supplied, model.revision refers to this source's own repository,
            # not necessarily model.id's canonical catalog/upstream repository.
            model["source"] = require_https_source_url(model_source, "--model-source")
        runtime = {
            "framework": required_text(args.framework, "Framework"),
            "version": required_text(args.framework_version, "Framework version"),
            "arguments": [
                required_text(argument, "Runtime argument")
                for argument in (args.runtime_arg or [])
            ],
            "endpoint_type": "openai-compatible",
        }
        if args.server_description is not None:
            runtime["server_description"] = required_text(args.server_description, "Server description")
        benchmark = {
            "profile_id": args.profile,
            "profile_sha256": sha256_file(profile_path),
            "runner": "guidellm",
            "runner_version": normalized["runner_version"],
        }
        # execution_device is deliberately excluded from this payload: a gpu run's
        # fingerprint must stay identical to what pre-execution_device code
        # computed for the same configuration, so an old GPU bundle and a newly
        # repackaged equivalent GPU run still collide under the validator's
        # (configuration_fingerprint, raw_output_sha256) duplicate check. CPU vs
        # GPU and CPU vs CPU identity are still distinguished below: a cpu run
        # always has an empty hardware.gpus (schema-enforced), and a cpu run's
        # own hardware.cpu/ram_gb/os still vary per machine.
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
            "execution_device": args.execution_device,
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


def print_managed_review(report: dict[str, Any]) -> None:
    recipe = report["recipe"]
    print(f"Recipe: {recipe['id']} — {recipe['title']}")
    print(
        f"Model: {recipe['model_id']} ({recipe['quantization']}) · profile {recipe['benchmark_profile']} "
        f"· execution device {recipe['execution_device']}"
    )
    print(f"Supported environment: {recipe['supported_environment']}")
    platform_status = report["platform"]
    print(f"Platform check: {'OK' if platform_status['ok'] else 'BLOCKED'} — {platform_status['message']}")
    print(f"Storage folder: {report['storage_dir']} (exists: {report['storage_dir_exists']})")
    if report["free_space_bytes"] is not None:
        print(
            f"Free space: {report['free_space_bytes']:,} bytes; required: {report['required_bytes']:,} bytes; "
            f"enough: {report['has_enough_space']}"
        )
    else:
        print(f"Free space: unknown; required: {report['required_bytes']:,} bytes")
    for file_entry in report["files"]:
        print(
            f"- {file_entry['kind']}: {file_entry['filename']} ({file_entry['size_bytes']:,} bytes, "
            f"sha256:{file_entry['sha256']}, licence {file_entry['licence']})"
        )
        print(f"    {file_entry['url']}")
    installed = report["already_installed"]
    if installed["installed"]:
        state = f"valid={installed['valid']}"
        if installed["error"]:
            state += f" ({installed['error']})"
        print(f"Already installed: {state}")
    else:
        print("Not yet installed.")
    print("\nThis is a review only. Nothing has been downloaded. Add --download to install after reviewing.")


def run_prepare_command(args: argparse.Namespace) -> int:
    from . import managed_setup

    root = find_repository_root()
    if root is None:
        print("Could not find an OmoBench checkout.", file=sys.stderr)
        return 2

    if args.list:
        try:
            recipes = managed_setup.load_recipes(root)
        except managed_setup.ManagedSetupError as error:
            print(f"Could not load the managed download catalog: {error}", file=sys.stderr)
            return 2
        for recipe in recipes:
            print(
                f"{recipe['id']}: {recipe['title']} · model {recipe['model_id']} · "
                f"profile {recipe['benchmark_profile']} · execution device {recipe['execution_device']}"
            )
        return 0

    if not args.recipe:
        print("Choose --recipe, or use --list to see reviewed recipes.", file=sys.stderr)
        return 2
    if not args.storage_dir:
        print("Choose --storage-dir, for example /mnt/d/OmoBench in WSL/Linux.", file=sys.stderr)
        return 2

    try:
        storage_dir = managed_setup.normalize_storage_dir(args.storage_dir)
    except managed_setup.ManagedSetupError as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        if not args.download:
            report = managed_setup.review(root, args.recipe, storage_dir)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print_managed_review(report)
            return 0

        def progress(label: str, done: int, total: int) -> None:
            print(f"\r{label}: {done:,}/{total:,} bytes", end="", file=sys.stderr, flush=True)

        manifest = managed_setup.download_and_install(root, args.recipe, storage_dir, progress=progress)
        print(file=sys.stderr)
        print(f"Installed {args.recipe} under {managed_setup.managed_root(storage_dir)}")
        print(f"Model: {manifest['model']['path']}")
        print(f"Runtime executable: {manifest['runtime']['executable_path']}")
        print("This is local, reviewed, verified evidence for setup only; it is not a benchmark result.")
        return 0
    except managed_setup.ManagedSetupError as error:
        print(str(error), file=sys.stderr)
        return 2


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
    gpus = report["gpus"]
    if not gpus:
        print("GPUs: none detected")
        return
    print("GPUs:")
    for gpu in gpus:
        memory = f"{gpu['memory_gb']} GB" if gpu["memory_gb"] is not None else "unknown memory"
        driver = gpu["driver_version"] or "unknown"
        vendor = gpu.get("vendor", "other")
        print(f"  - [{vendor}] {gpu['model']} ({memory}, driver {driver})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omobench", description="OmoBench submission tooling")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor = subcommands.add_parser("doctor", help="inspect local benchmark prerequisites")
    doctor.add_argument("--json", action="store_true", help="print machine-readable output")

    subcommands.add_parser("profiles", help="list versioned benchmark profiles")

    ui = subcommands.add_parser("ui", help="open the local browser control panel")
    ui.add_argument("--port", type=int, default=8765, help="localhost port (default: 8765)")
    ui.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")

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
    package.add_argument(
        "--model-source",
        help=(
            "optional https URL identifying the actual weights source, ideally the exact "
            "download URL pinned to an immutable revision and filename; when supplied, "
            "--model-revision refers to this source's own repository, not necessarily "
            "the canonical --model repository"
        ),
    )
    package.add_argument("--quantization", help="quantization label, such as BF16 or Q4_K_M")
    package.add_argument("--framework", required=True, choices=RUNTIME_FRAMEWORKS, help="inference framework")
    package.add_argument("--framework-version", required=True, help="exact framework version")
    package.add_argument(
        "--server-description",
        help=(
            "free-text description of the OpenAI-compatible server; "
            f"required when --framework is {OTHER_SERVER_FRAMEWORK}"
        ),
    )
    package.add_argument(
        "--execution-device",
        required=True,
        choices=["cpu", "gpu"],
        help=(
            "what performed inference for this run; both cpu and gpu are publishable "
            "OmoBench results, and this is a contributor declaration, not independent proof"
        ),
    )
    package.add_argument("--runtime-arg", action="append", default=[], help="one normalized framework startup argument; repeat as needed")
    package.add_argument("--profile", required=True, choices=sorted(PROFILE_SPECS), help="versioned OmoBench profile")
    package.add_argument("--submitter", required=True, help="GitHub username or organization submitting the run")
    package.add_argument("--run-id", help="override the staging directory name as the run ID")
    package.add_argument("--run-kind", choices=["initial", "repeat", "replication"], default="initial")
    package.add_argument("--repeat-of-run-id", help="previous run ID when this is a repeat")
    package.add_argument("--cpu", help="override the detected CPU description")
    package.add_argument("--ram-gb", type=float, help="override detected RAM in gigabytes")
    package.add_argument(
        "--gpu-memory-gb",
        type=float,
        help=(
            "VRAM per GPU, in gigabytes, for a detected GPU whose memory could not be safely "
            "determined (for example the Windows CIM fallback). Confirm this value in the "
            "serving runtime's or hardware panel's own VRAM report first; it only fills in "
            "memory for an already-detected GPU and does not invent a GPU model or vendor."
        ),
    )
    package.add_argument("--force", action="store_true", help="allow replacement of an existing local result.json")

    prepare = subcommands.add_parser(
        "prepare", help="review or install a reviewed managed CPU download recipe (does not benchmark)"
    )
    prepare.add_argument("--list", action="store_true", help="list reviewed managed recipes and exit")
    prepare.add_argument("--recipe", help="managed recipe id, for example qwen3-4b-cpu-v1")
    prepare.add_argument(
        "--storage-dir",
        help="absolute storage folder for managed downloads, for example /mnt/d/OmoBench in WSL/Linux",
    )
    prepare.add_argument(
        "--download",
        action="store_true",
        help="perform the verified download and installation; omit to only review",
    )
    prepare.add_argument("--json", action="store_true", help="print machine-readable review output")

    launch = subcommands.add_parser(
        "launch", help="start the verified managed CPU server, then smoke-test or run the existing benchmark"
    )
    launch.add_argument("--recipe", required=True, help="managed recipe id, for example qwen3-4b-cpu-v1")
    launch.add_argument(
        "--storage-dir",
        required=True,
        help="absolute storage folder used by 'omobench prepare --download', for example /mnt/d/OmoBench",
    )
    launch_mode = launch.add_mutually_exclusive_group(required=True)
    launch_mode.add_argument(
        "--smoke", action="store_true", help="only check server readiness and the model alias; no inference"
    )
    launch_mode.add_argument(
        "--execute", action="store_true", help="run the existing, unchanged benchmark profile against the server"
    )
    launch.add_argument("--output-dir", default=".omobench-runs", help="local staging directory, relative to repository root")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "ui":
        from .ui import serve_ui

        return serve_ui(args.port, open_browser=not args.no_browser)
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
    if args.command == "prepare":
        return run_prepare_command(args)
    if args.command == "launch":
        from .launcher import run_launch_command

        return run_launch_command(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
