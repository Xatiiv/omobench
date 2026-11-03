"""Small, dependency-free starting point for the OmoBench CLI."""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
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
    # Same chat-small workload; a longer duration ceiling so slower hardware
    # (chiefly CPU) can collect enough measured samples. Uses explicit
    # request-count warmup/cooldown (mode=requests), not chat-small-v1's
    # percentage/prefer_duration scheme, so the sample size holds regardless
    # of how long each request takes.
    #
    # warmup.value=4 and cooldown.value=4, not 5: GuideLLM 0.7.3's own
    # request-count phase boundaries each exclude phase_requests + 1 requests,
    # not phase_requests. Both are compensated here so the *actual* excluded
    # counts match this profile's declared contract exactly: 5 warmup +
    # 50 measured + 5 cooldown = 60 total.
    #
    # Verified by driving the real scheduler-facing accumulator
    # (GenerativeBenchmarkAccumulator.update_estimate) through the real
    # per-request event sequence -- queued, pending, in_progress, first_token,
    # terminal -- and reading GenerativeMetrics.compile()'s final
    # request_totals. The full lifecycle matters: GenerativeBenchmarkTimings
    # re-evaluates TransientPhaseConfig.compute_transition_time on *every*
    # scheduler event, keyed on info.started_at, which is only populated once a
    # request reaches "in_progress". An earlier completion-only simulation
    # (one update_estimate call per request) made warmup.value=5 look correct
    # and shipped it; a real extended CPU run then recorded 24 successful
    # requests with six warmup requests excluded instead of the declared five.
    # See ChatSmallExtendedBoundaryTests in cli/tests/test_cli.py and
    # docs/proposals/chat-small-extended-v1.md.
    # The corrected short profile. Same workload as chat-small-v1, but its
    # phases are counted in requests instead of being taken as a fraction of the
    # duration ceiling -- which is what chat-small-v1's `warmup=0.1` actually
    # does, because GuideLLM 0.7.3 resolves a fraction against duration whenever
    # a duration budget exists (TransientPhaseConfig.compute_limits, mode
    # `prefer_duration`). That made chat-small-v1's measured window a fixed
    # 720-second slice of the clock: a host answering in a second measured
    # nothing at all, and the profile could only satisfy its own acceptance
    # floor at roughly 9-18 seconds per request.
    #
    # warmup.value=4/cooldown.value=4, not 5: each of GuideLLM 0.7.3's
    # request-count phase boundaries excludes one more request than it is given.
    # The same compensation chat-small-extended-v1 ships, and verified here the
    # same way plus end to end -- the real scheduler, accumulator and report
    # against a streaming fixture server, with the server's own request log
    # naming which requests were excluded: requests 1-5 warmup, 6-55 measured,
    # 56-60 cooldown, identical at 0.02 s and 0.5 s per request. See
    # cli/tests/test_chat_small_v2.py and docs/proposals/profile-phase-mapping-v2.md.
    "chat-small-v2": {
        "input_tokens": 1024,
        "output_tokens": 256,
        "profile": "kind=synchronous,warmup.value=4,warmup.mode=requests,cooldown.value=4,cooldown.mode=requests",
        "max_requests": 60,
        "max_duration_seconds": 900,
    },
    "chat-small-extended-v1": {
        "input_tokens": 1024,
        "output_tokens": 256,
        "profile": "kind=synchronous,warmup.value=4,warmup.mode=requests,cooldown.value=4,cooldown.mode=requests",
        "max_requests": 60,
        "max_duration_seconds": 5400,
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


def guidellm_installed_version() -> str | None:
    """The installed GuideLLM distribution's version, from package metadata
    only -- no subprocess, no import of guidellm itself. Cheap (a metadata
    file read, not a process spawn) and safe to call at any time, including
    while a model server is loaded: unlike `executable_version("guidellm")`
    (which runs the real "guidellm --version" subprocess -- itself a cold
    import of GuideLLM's full dependency tree, observed to occasionally
    exceed command_output()'s 60s timeout under real load), this cannot
    hang or time out.

    Reflects *this* interpreter's own environment (sys.executable), which
    is exactly the Python binary `omobench run --execute` launches as a
    subprocess (see Controller._cli_command in ui.py) -- so this is an
    accurate proxy for what that subprocess will actually have available,
    not a possibly-different "guidellm" resolved elsewhere on PATH.
    """
    try:
        return importlib.metadata.version("guidellm")
    except importlib.metadata.PackageNotFoundError:
        return None


@dataclass
class RunnerPreflight:
    """Result of guidellm_runner_preflight(): whether the pinned GuideLLM
    version is installed in this environment, and a specific, actionable
    reason when it is not. `kind` distinguishes the two things package
    metadata can actually tell us apart -- "missing" and "version_mismatch"
    -- from any later, separate runtime problem (a broken install, or an
    import/initialization failure), which this never claims to detect; see
    guidellm_runner_preflight()'s docstring.
    """

    ok: bool
    kind: str  # "ok" | "missing" | "version_mismatch"
    version: str | None
    message: str


def guidellm_runner_preflight() -> RunnerPreflight:
    """Fast, cheap, execute-mode preflight check: is the pinned GuideLLM
    version installed in this same environment? Meant to run before any
    expensive managed preparation or model startup, so a real problem is
    reported in milliseconds instead of after loading a model server (the
    slow, subprocess-based version check this replaces could take up to a
    minute -- long enough that a user could see a fully-loaded model server
    followed by a generic "GuideLLM is required" failure with no model ever
    benchmarked).

    This verifies installation and version only, via package metadata --
    never runtime health. A package that is installed at the right version
    can still fail to import or initialize later (a corrupted install, a
    missing native dependency, and so on); that is a genuinely different
    problem, already handled where it actually happens: a nonzero GuideLLM
    exit code in write_local_run(), or telemetry.TelemetryUnavailable for
    the in-process telemetry adapter. This function does not attempt to
    predict or replace that handling.
    """
    version = guidellm_installed_version()
    if version is None:
        return RunnerPreflight(
            ok=False,
            kind="missing",
            version=None,
            message=(
                f"GuideLLM is not installed in this environment ({sys.executable}). "
                "Run: python -m pip install -e './cli[ui,runner]'"
            ),
        )
    if version != GUIDELLM_VERSION:
        return RunnerPreflight(
            ok=False,
            kind="version_mismatch",
            version=version,
            message=(
                f"OmoBench v1 requires GuideLLM {GUIDELLM_VERSION}; found {version} installed in "
                f"{sys.executable}. Run: python -m pip install -e './cli[ui,runner]'"
            ),
        )
    return RunnerPreflight(ok=True, kind="ok", version=version, message=f"GuideLLM {GUIDELLM_VERSION} ready.")


def sanitize_diagnostics(text: str, *, limit: int = 2000) -> str:
    """Keep a diagnostic useful without carrying a secret or a wall of output."""
    cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text or "")
    cleaned = re.sub(r"(?i)(api[_-]?key|token|authorization|secret|password)(\s*[:=]\s*)\S+",
                     r"\1\2<REDACTED>", cleaned)
    cleaned = re.sub(r"https?://[^\s\"\'<>]+", "<URL>", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > limit:
        # The end of a traceback is the part that names the failure.
        cleaned = "…\n" + cleaned[-limit:]
    return cleaned


# What the runner must be able to import before it can start at all: its own
# entry point, the OpenAI-compatible backend OmoBench uses, and the tokenizer
# implementation that builds the workload. Nothing here downloads a model or a
# tokenizer, performs inference, or executes code from a model repository --
# these are library imports only.
RUNNER_HEALTH_PROBE = (
    "import json, sys;"
    "import guidellm;"
    "import guidellm.__main__;"
    "import guidellm.backends.openai.http;"
    "import guidellm.data.tokenizers.huggingface;"
    "import importlib.metadata as md;"
    "print('OMOBENCH_RUNNER_HEALTH ' + json.dumps({"
    "'guidellm': md.version('guidellm'),"
    "'python': sys.version.split()[0],"
    "'executable': sys.executable}))"
)

RUNNER_HEALTH_ENVIRONMENT = {
    # Belt and braces: the probe imports libraries only, and these make a
    # download impossible rather than merely unlikely.
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "TOKENIZERS_PARALLELISM": "false",
}


@dataclass
class RunnerHealth:
    """The outcome of actually importing the runner, not of reading metadata.

    `kind` is one of: ok, missing, version_mismatch, import_failed, crashed,
    timeout, cancelled, unavailable.
    """

    ok: bool
    kind: str
    message: str
    detail: str = ""
    seconds: float = 0.0
    version: str | None = None


def runner_health_check(
    *,
    interpreter: str | None = None,
    timeout: float = 120.0,
    cancel: Any = None,
    probe: str | None = None,
) -> RunnerHealth:
    """Import the pinned runner in a bounded child process and report what happened.

    Installed metadata is not health: a distribution can be present at exactly
    the pinned version and still fail to import, which is how a working-looking
    environment produced a SIGBUS from torch on a contributor's machine. This
    runs the import in a separate process so a crash is reported rather than
    taken down with it, bounded by `timeout`, cancellable through `cancel`
    (any callable returning truthy, polled while waiting), and never falling
    back to launching a benchmark.

    It downloads nothing, runs no inference, and enables no remote code.
    """
    executable = interpreter or sys.executable
    source = probe if probe is not None else RUNNER_HEALTH_PROBE
    environment = {**os.environ, **RUNNER_HEALTH_ENVIRONMENT}
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            # -I: isolated, so a stray PYTHONPATH or user site-packages cannot
            # make this answer about a different installation than the one the
            # benchmark will use.
            [executable, "-I", "-c", source],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=environment, start_new_session=os.name != "nt",
        )
    except OSError as error:
        return RunnerHealth(
            ok=False, kind="unavailable",
            message=f"Could not start {executable} to check the runner.",
            detail=sanitize_diagnostics(str(error)),
            seconds=time.monotonic() - started,
        )

    cancelled = False
    deadline = started + max(1.0, timeout)
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel():
            cancelled = True
        if cancelled or time.monotonic() > deadline:
            _terminate_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            elapsed = time.monotonic() - started
            if cancelled:
                return RunnerHealth(ok=False, kind="cancelled", seconds=elapsed,
                                    message="The runner check was cancelled. Nothing was started.")
            return RunnerHealth(
                ok=False, kind="timeout", seconds=elapsed,
                message=(f"Importing the runner did not finish within {int(timeout)} s. "
                         "That usually means the environment is unusable rather than slow; "
                         "check the storage holding it and try again."),
                detail=sanitize_diagnostics(stderr),
            )

    elapsed = time.monotonic() - started
    combined = sanitize_diagnostics((stderr or "") + ("\n" + stdout if stdout else ""))
    code = process.returncode
    if code == 0 and "OMOBENCH_RUNNER_HEALTH" in (stdout or ""):
        document = {}
        with contextlib.suppress(ValueError):
            document = json.loads((stdout or "").split("OMOBENCH_RUNNER_HEALTH", 1)[1].strip().splitlines()[0])
        version = document.get("guidellm") if isinstance(document, dict) else None
        if version and version != GUIDELLM_VERSION:
            return RunnerHealth(
                ok=False, kind="version_mismatch", version=version, seconds=elapsed,
                message=(f"The runner imports, but it is GuideLLM {version}; OmoBench v1 pins "
                         f"{GUIDELLM_VERSION}."),
            )
        return RunnerHealth(
            ok=True, kind="ok", version=version, seconds=elapsed,
            message=(f"The runner imported successfully in {elapsed:.1f} s"
                     + (f" (GuideLLM {version}" if version else "")
                     + (f", Python {document.get('python')})" if isinstance(document, dict)
                        and document.get("python") else ")" if version else "")
                     + ". Nothing was downloaded and no inference ran."),
        )
    if code is not None and code < 0:
        name = _signal_name(-code)
        return RunnerHealth(
            ok=False, kind="crashed", seconds=elapsed,
            message=(f"Importing the runner crashed ({name}) after {elapsed:.1f} s. The packages are "
                     "installed, but this environment cannot load them. A crash on import is "
                     "commonly a damaged or partially written install, or storage that cannot "
                     "serve the files; OmoBench cannot tell which from here, and does not guess."),
            detail=combined,
        )
    if "ModuleNotFoundError" in combined or "No module named" in combined:
        return RunnerHealth(
            ok=False, kind="missing", seconds=elapsed,
            message=("The runner is not fully installed in this environment. Install it with: "
                     "python -m pip install -e './cli[ui,runner]'"),
            detail=combined,
        )
    return RunnerHealth(
        ok=False, kind="import_failed", seconds=elapsed,
        message=(f"Importing the runner failed with exit code {code} after {elapsed:.1f} s. "
                 "The reported error below is the runner's own."),
        detail=combined,
    )


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Stop the probe and anything it started, without touching this process."""
    with contextlib.suppress(Exception):
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    with contextlib.suppress(Exception):
        process.wait(timeout=5)
    if process.poll() is None:
        with contextlib.suppress(Exception):
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()


def _signal_name(number: int) -> str:
    with contextlib.suppress(ValueError):
        return signal.Signals(number).name
    return f"signal {number}"


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
        "guidellm": (
            f"guidellm version: {version}"
            if (version := guidellm_installed_version()) is not None
            else None
        ),
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


def profile_request_timeout_seconds(root: Path, profile_id: str) -> float | None:
    """The profile's own declared execution.request_timeout_seconds, from
    benchmarks/profiles/<id>.yaml.

    Unlike profile_measured_requests() this IS fed into the runner: it becomes
    the openai_http backend's `timeout`. Returns None when the profile declares
    no timeout or cannot be read, in which case the backend keeps GuideLLM's
    own default rather than OmoBench inventing a value."""
    try:
        import yaml

        path = root / "benchmarks" / "profiles" / f"{profile_id}.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return None
    if not isinstance(document, dict):
        return None
    value = document.get("execution", {}).get("request_timeout_seconds")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if value > 0 else None


# A served model identifier and a tokenizer are two different identities. The
# first is whatever string the inference API accepts ("qwen/qwen3-vl-4b:3" from
# LM Studio, say); the second is the Hugging Face repository or local directory
# whose tokenizer builds and counts the synthetic workload. GuideLLM 0.7.3
# defaults the second to the first, which is correct only when the served alias
# happens to also be a repository id -- otherwise the run fails while resolving
# the tokenizer, before a single measurement.
HF_REPO_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z")
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "vocab.json", "spiece.model", "tokenizer.model")


def validate_tokenizer_source(value: str) -> str:
    """Accept a Hugging Face repository id or an existing local directory.

    Deliberately narrow: the value is passed to GuideLLM as part of a
    comma-separated `kind=...` argument, and it must never be able to inject a
    second setting (remote code execution, a different tokenizer kind) into it.
    """
    source = value.strip()
    if not source:
        raise ValueError("Tokenizer source cannot be empty.")
    if any(character in source for character in ",="):
        raise ValueError("A tokenizer source cannot contain ',' or '='.")
    if any(ord(character) < 32 for character in source) or len(source) > 512:
        raise ValueError("Tokenizer source is not a usable repository id or path.")
    if HF_REPO_ID.fullmatch(source):
        return source
    path = Path(source).expanduser()
    if path.is_dir():
        return str(path)
    raise ValueError(
        "Enter a Hugging Face repository id such as Qwen/Qwen3-VL-4B-Instruct, "
        "or the path of a local folder containing tokenizer files."
    )


def huggingface_cache_directories() -> list[Path]:
    """Where the Hugging Face hub cache would be, without importing it."""
    explicit = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.environ.get("HF_HUB_CACHE")
    if explicit:
        return [Path(explicit)]
    home = os.environ.get("HF_HOME")
    if home:
        return [Path(home) / "hub"]
    return [Path.home() / ".cache" / "huggingface" / "hub"]


# The four separate things a tokenizer check can be asked about. Only the first
# two are ever decided here: this check stays offline, so it neither loads a
# tokenizer nor compares it against the served weights, and it says so rather
# than letting a passing syntax check imply either.
TOKENIZER_NOT_LOADED = (
    "Not loaded here. This check stays offline and does not import the tokenizer, so "
    "\"the files are present\" is all it can tell you; the runner loads it when the benchmark starts."
)
TOKENIZER_NOT_COMPARED = (
    "Not verified, and not verifiable from here. A tokenizer that parses is not thereby the "
    "tokenizer your server's weights were trained with. Choosing the wrong one changes how prompts "
    "are built and how tokens are counted, which changes the measurements -- so pick the repository "
    "the model card names, not merely one that loads."
)


def tokenizer_source_report(value: str, *, served_model: str | None = None) -> dict[str, Any]:
    """Describe a tokenizer source without touching the network.

    Says what the runner would do and whether files still have to be
    downloaded, so the panel can explain that before anything is fetched. It
    never downloads, never imports transformers, and never claims a local copy
    it has not seen on disk -- nor that the tokenizer matches the weights.
    """
    source = (value or "").strip() or (served_model or "")
    effective_default = not (value or "").strip()
    try:
        resolved = validate_tokenizer_source(source)
    except ValueError as error:
        message = (
            f"The server model identifier {source!r} is not a Hugging Face repository id, "
            "so the runner cannot use it to build the workload. Enter a tokenizer source."
            if effective_default else str(error)
        )
        return {
            "ok": False,
            "kind": "invalid",
            "source": source,
            "default": effective_default,
            "message": message,
            "checks": [
                {"id": "syntax", "state": "failed", "detail": message},
                {"id": "files", "state": "skipped",
                 "detail": "Not looked for: there is no usable repository id or path to look for."},
                {"id": "loaded", "state": "untested", "detail": TOKENIZER_NOT_LOADED},
                {"id": "compatibility", "state": "unverified", "detail": TOKENIZER_NOT_COMPARED},
            ],
        }
    if not HF_REPO_ID.fullmatch(resolved):
        present = sorted(name for name in TOKENIZER_FILES if (Path(resolved) / name).is_file())
        message = (
            f"Local tokenizer folder, containing {', '.join(present)}. Nothing is downloaded."
            if present else
            "That folder exists but contains no recognizable tokenizer file "
            f"({', '.join(TOKENIZER_FILES)})."
        )
        return {
            "ok": bool(present),
            "kind": "local",
            "source": resolved,
            "default": effective_default,
            "cached": True,
            "files": present,
            "message": message,
            "checks": [
                {"id": "syntax", "state": "ok", "detail": "Accepted as a local directory path."},
                {"id": "files", "state": "ok" if present else "missing", "detail": message},
                {"id": "loaded", "state": "untested", "detail": TOKENIZER_NOT_LOADED},
                {"id": "compatibility", "state": "unverified", "detail": TOKENIZER_NOT_COMPARED},
            ],
        }
    flattened = "models--" + resolved.replace("/", "--")
    cached = any((directory / flattened).is_dir() for directory in huggingface_cache_directories())
    message = (
        f"Hugging Face repository {resolved}, already in this machine's local cache; "
        "no download is needed." if cached else
        f"Hugging Face repository {resolved}. Its tokenizer files are not in this machine's "
        "cache yet, so the runner will download them from huggingface.co when the benchmark "
        "starts. Model weights are not downloaded."
    )
    return {
        "ok": True,
        "kind": "huggingface",
        "source": resolved,
        "default": effective_default,
        "cached": cached,
        "message": message,
        "checks": [
            {"id": "syntax", "state": "ok",
             "detail": f"Accepted as a Hugging Face repository id: {resolved}."},
            {"id": "files", "state": "ok" if cached else "missing", "detail": message},
            {"id": "loaded", "state": "untested", "detail": TOKENIZER_NOT_LOADED},
            {"id": "compatibility", "state": "unverified", "detail": TOKENIZER_NOT_COMPARED},
        ],
    }


def build_guidellm_command(
    *,
    root: Path,
    endpoint: str,
    model: str,
    profile_id: str,
    raw_output_path: Path,
    tokenizer: str | None = None,
) -> list[str]:
    spec = PROFILE_SPECS[profile_id]
    target = endpoint.rstrip("/")
    backend = f"kind=openai_http,target={target},model={model},request_format=/v1/chat/completions"
    # The benchmark must measure the destination that was selected, and only
    # that one. OpenAIHTTPBackendArgs.follow_redirects defaults to *True* in
    # GuideLLM 0.7.3 (guidellm/backends/openai/http.py), so without this a
    # loopback server answering 307 sends every benchmark request -- prompts
    # included -- to whatever host it names, while the result still records the
    # loopback target as the thing that was measured. The panel's own
    # connection checks already refuse a redirect (ui.NoRedirect); this is the
    # same rule for the requests that actually produce the measurements.
    # Demonstrated both ways against a redirecting fixture in
    # cli/tests/test_redirect_destination.py.
    backend += ",follow_redirects=false"
    # execution.request_timeout_seconds -> OpenAIHTTPBackendArgs.timeout, which
    # GuideLLM 0.7.3 passes to httpx as its READ timeout
    # (httpx.Timeout(read=args.timeout, connect=args.timeout_connect, ...), see
    # guidellm/backends/openai/http.py). That bounds how long a request may wait
    # for its NEXT response chunk -- not the total duration of a streaming
    # request. A slow-but-steadily-streaming response can therefore still run
    # far longer than this value, and legitimately does on slow CPU hosts; this
    # alone does not cap a long request at the profile's timeout, nor prevent a
    # single request from overrunning the max_duration ceiling. What it does
    # bound is a server that stops producing: a stall of more than this many
    # seconds between chunks (or before any first byte) now fails that request
    # instead of hanging until the duration constraint fires.
    timeout_seconds = profile_request_timeout_seconds(root, profile_id)
    if timeout_seconds is not None:
        backend += f",timeout={timeout_seconds}"
    # An explicit tokenizer identity, only when one was supplied: the default
    # (GuideLLM using the served model identifier) is left exactly as it was.
    tokenizer_arguments = ["--tokenizer", f"kind=huggingface_auto,model={validate_tokenizer_source(tokenizer)}"] if tokenizer else []
    return [
        "guidellm",
        "run",
        "--backend",
        backend,
        *tokenizer_arguments,
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


def profile_phase_contract(root: Path, profile_id: str) -> dict[str, Any]:
    """How this profile's warmup and cooldown phases actually resolve, beside
    what its YAML declares.

    GuideLLM 0.7.3's TransientPhaseConfig resolves a fractional phase value
    against *duration* whenever a duration budget exists, because its default
    mode is `prefer_duration`: `compute_limits()` computes both a duration and a
    request count and then discards the request count. So
    `kind=synchronous,warmup=0.1` with a 900 s ceiling is a 90-second warmup,
    not a five-request warmup -- regardless of what the profile's
    `warmup_requests` says. Only an explicit `mode=requests` phase counts
    requests. Established against the pinned runner's own accumulator in
    cli/tests/test_profile_phase_semantics.py.

    This reads OmoBench's own runner mapping and the profile's own declaration;
    it does not reimplement the runner's resolution, which the tests pin.
    """
    spec = PROFILE_SPECS.get(profile_id)
    if spec is None:
        return {}
    mapping = spec["profile"]
    max_duration = spec.get("max_duration_seconds")
    max_requests = spec.get("max_requests")

    def phase(name: str) -> tuple[str | None, float | None]:
        fraction = re.search(rf"{name}=([0-9.]+)", mapping)
        explicit = re.search(rf"{name}\.value=([0-9.]+)", mapping)
        requests_mode = f"{name}.mode=requests" in mapping
        if fraction and not explicit:
            value = float(fraction.group(1))
            if value < 1.0 and max_duration is not None:
                # percent + a duration budget -> duration, via prefer_duration
                return "duration", value * max_duration
            if value < 1.0 and max_requests is not None:
                return "requests", float(int(value * max_requests))
            return "requests", value
        if explicit:
            value = float(explicit.group(1))
            return ("requests" if requests_mode else "duration"), value
        return None, None

    warmup_basis, warmup_value = phase("warmup")
    cooldown_basis, cooldown_value = phase("cooldown")
    declared = {}
    try:
        import yaml

        document = yaml.safe_load((root / "benchmarks" / "profiles" / f"{profile_id}.yaml").read_text(encoding="utf-8"))
        declared = (document or {}).get("execution", {}) if isinstance(document, dict) else {}
    except (OSError, ValueError, ImportError):
        declared = {}

    basis = warmup_basis if warmup_basis == cooldown_basis else "mixed"

    def excluded(phase_basis, value):
        """What the runner really excludes for this phase.

        Each of GuideLLM 0.7.3's request-count boundaries excludes one request
        more than the value it is given, which the shipped mappings compensate
        for; the profile's declared counts are therefore the *actual* counts,
        and these are what a reader should be shown.
        """
        if value is None:
            return None
        return value + 1 if phase_basis == "requests" else value

    warmup_actual = excluded(warmup_basis, warmup_value)
    cooldown_actual = excluded(cooldown_basis, cooldown_value)
    declares_request_phases = any(declared.get(key) for key in ("warmup_requests", "cooldown_requests"))
    return {
        "profile_id": profile_id,
        # What the runner enforces, from the command OmoBench itself builds.
        "enforced_max_requests": max_requests,
        "enforced_max_duration_seconds": max_duration,
        "phase_basis": basis,
        # Seconds for a duration phase; requests actually excluded for a
        # request phase. `*_configured` is the raw value on the command line.
        "warmup": warmup_actual,
        "cooldown": cooldown_actual,
        "warmup_configured": warmup_value,
        "cooldown_configured": cooldown_value,
        # What the profile's own YAML declares.
        "declared_measured_requests": declared.get("measured_requests"),
        "declared_warmup_requests": declared.get("warmup_requests"),
        "declared_cooldown_requests": declared.get("cooldown_requests"),
        # A declared request-count phase that the runner resolves as a duration
        # window is a real mismatch: the measured window is then a fixed slice
        # of the clock, and how many requests land inside it depends entirely on
        # per-request latency.
        "declaration_matches_execution": not (declares_request_phases and basis == "duration"),
        "measured_window_seconds": (
            None if basis != "duration" or max_duration is None or warmup_actual is None
            or cooldown_actual is None else max_duration - warmup_actual - cooldown_actual
        ),
        # For a request-count profile, the measured window of a complete run.
        "measured_window_requests": (
            None if basis != "requests" or max_requests is None or warmup_actual is None
            or cooldown_actual is None else int(max_requests - warmup_actual - cooldown_actual)
        ),
    }


def profile_measured_requests(root: Path, profile_id: str) -> int | None:
    """The profile's own declared measured-request target
    (execution.measured_requests in benchmarks/profiles/<id>.yaml),
    display-only: never fed into build_guidellm_command() or any stopping
    condition, which continue to come from PROFILE_SPECS unchanged."""
    try:
        import yaml

        path = root / "benchmarks" / "profiles" / f"{profile_id}.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return None
    if not isinstance(document, dict):
        return None
    value = document.get("execution", {}).get("measured_requests")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# A benchmark that ran, but whose required evidence log could not be
# finalized (flushed and closed): the measurements exist, but the bundle is
# incomplete, so the run is not a completed result. Value borrowed from
# sysexits' EX_IOERR.
#
# The code alone is never used to *identify* this case -- a runner is free to
# exit 74 for its own reasons -- so RunnerOutcome below carries the fact
# explicitly, reported only by the code that actually observed it.
EVIDENCE_FINALIZATION_EXIT_CODE = 74


@dataclass
class RunnerOutcome:
    """How a benchmark invocation ended.

    `returncode` is the runner's own exit status (or
    EVIDENCE_FINALIZATION_EXIT_CODE when an otherwise successful run could not
    have its required log finalized). `evidence_log_finalized` records whether
    guidellm.stdout.log -- required evidence, unlike the optional telemetry
    snapshot -- was actually flushed and closed, so callers never have to infer
    that from a number that could mean something else.
    """

    returncode: int
    evidence_log_finalized: bool = True


def guidellm_runner_invocation(command: list[str]) -> list[str]:
    """Rewrite a canonical `["guidellm", "run", ...]` command so it executes
    the GuideLLM installation `guidellm_runner_preflight()` actually verified.

    Preflight reads `importlib.metadata` in *this* interpreter, so the only
    runner it can vouch for is this interpreter's own. A bare "guidellm" is
    resolved by PATH, which can point at an entirely different environment
    (a second virtual environment, a pipx shim, a stale global install): the
    version check would pass against one install while a different one ran
    the benchmark -- discovered, if at all, only after the full run, when
    normalization rejects the report's `guidellm_version`.

    `python -m guidellm` is the *same entry point* as the console script --
    GuideLLM 0.7.3 declares `guidellm = guidellm.__main__:cli` -- just
    resolved through `sys.executable` instead of PATH, so this changes which
    install runs, never what it does.

    The canonical form is deliberately kept in `build_guidellm_command()`:
    it is what reproduce.md publishes, and an interpreter path
    (`/home/<user>/.venvs/...`) does not belong in a public evidence bundle.
    """
    if not command or command[0] != "guidellm":
        raise ValueError(f"Expected a 'guidellm ...' command, got: {command[:1]!r}")
    return [sys.executable, "-m", "guidellm", *command[1:]]


def run_guidellm_command(command, *, log_path, root, profile_id, telemetry_dir, bridge_credentials=None):
    kwargs = dict(log_path=log_path, root=root, profile_id=profile_id, telemetry_dir=telemetry_dir)
    if bridge_credentials is None:
        return _run_guidellm_command(command, **kwargs)
    from .bridge_runner import authenticated_backend
    with authenticated_backend(bridge_credentials):
        return _run_guidellm_command(command, bridge_credentials=bridge_credentials, **kwargs)


def _run_guidellm_command(
    command: list[str],
    *,
    log_path: Path,
    root: Path,
    profile_id: str,
    telemetry_dir: Path | None,
    bridge_credentials: dict | None = None,
) -> RunnerOutcome:
    """Run `command` (as built by build_guidellm_command) exactly as the
    plain subprocess path always has -- unless `telemetry_dir` is given, in
    which case this additionally tries an in-process run with a live
    telemetry observer attached (see telemetry.py), producing the identical
    resolved configuration and the same evidence artifacts.

    Fallback to the plain subprocess path is restricted to failures that
    happen *before* the benchmark is invoked, so a telemetry-adapter problem
    can disable live monitoring but never prevent the benchmark from running
    -- and, equally important, never cause a second one. Once execution has
    begun, its outcome is reported as-is: nothing here may re-run it or
    truncate the evidence it already wrote.
    """
    invocation = guidellm_runner_invocation(command)
    private_input = {}
    if bridge_credentials is not None:
        invocation = [sys.executable, "-m", "omobench.bridge_runner", *command[1:]]
        private_input = {"input": json.dumps(bridge_credentials) + "\n"}

    def plain_subprocess(note: str | None = None) -> RunnerOutcome:
        log_file = log_path.open("w", encoding="utf-8")
        try:
            if note is not None:
                log_file.write(note)
                log_file.flush()
            returncode = subprocess.run(invocation, stdout=log_file, stderr=subprocess.STDOUT, text=True, **private_input).returncode
        except BaseException:
            with contextlib.suppress(OSError, ValueError):
                log_file.close()
            raise
        # Same contract as the telemetry path: the benchmark has already run,
        # so a failure finalizing its required log never re-runs it and never
        # touches what was written -- but it does stop the run being reported
        # as completed. See EVIDENCE_FINALIZATION_EXIT_CODE.
        try:
            log_file.flush()
            log_file.close()
        except (OSError, ValueError) as error:
            print(f"Could not finalize the required evidence log {log_path}: {error}", file=sys.stderr)
            return RunnerOutcome(returncode or EVIDENCE_FINALIZATION_EXIT_CODE, evidence_log_finalized=False)
        return RunnerOutcome(returncode)

    if telemetry_dir is None:
        return plain_subprocess()

    from . import telemetry as telemetry_module

    # Pre-invocation setup. A failure here means nothing has been sent and
    # the log is still untouched, so the plain subprocess path can safely
    # (re)open it and run the benchmark for the first time.
    try:
        telemetry_dir.mkdir(parents=True, exist_ok=True)
        argv = telemetry_module.argv_for_run_command(command)
    except (OSError, ValueError) as error:
        return plain_subprocess(f"OmoBench: live telemetry unavailable ({error}); running without it.\n")

    # The benchmark itself. TelemetryUnavailable is contractually raised only
    # from this function's own setup block, before the entrypoint is invoked
    # (see run_generative_benchmark_with_telemetry's docstring), so it is the
    # ONLY exception that may start a second attempt here. Anything else --
    # notably an OSError from writing, flushing, or closing the run's own log
    # -- happens after execution began: catching it would reopen the log with
    # "w", destroying the finished run's output, and launch a duplicate
    # benchmark. It is allowed to propagate instead.
    try:
        returncode = telemetry_module.run_generative_benchmark_with_telemetry(
            argv,
            snapshot_path=telemetry_dir / "telemetry.json",
            history_path=telemetry_dir / "telemetry-history.json",
            stdout_log_path=log_path,
            run_label=profile_id,
            total_measured_requests=profile_measured_requests(root, profile_id),
            configured_max_requests=PROFILE_SPECS[profile_id].get("max_requests"),
            phase_contract=profile_phase_contract(root, profile_id),
        )
    except telemetry_module.TelemetryUnavailable as error:
        return plain_subprocess(f"OmoBench: live telemetry unavailable ({error}); running without it.\n")
    # telemetry.py reports an unfinalized required log through this code, and
    # reports it only when it actually observed the failure itself.
    return RunnerOutcome(
        returncode,
        evidence_log_finalized=returncode != EVIDENCE_FINALIZATION_EXIT_CODE,
    )


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


def finite_number(value: Any) -> float | None:
    """`value` as a float when it is one, and finite. Otherwise None.

    NaN and the infinities are rejected rather than compared: `nan == 0.0` is
    False, so a corrupt timestamp would otherwise read as healthy evidence
    instead of as evidence that could not be read.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def measured_window_integrity(benchmark: dict[str, Any]) -> dict[str, Any]:
    """Did this run ever establish a measured window, or is it the fallback?

    GuideLLM 0.7.3 sets `measure_start` only on the single scheduler event
    where the warmup boundary is recognised. For a request-counted phase that
    event must satisfy *both* halves of
    `TransientPhaseConfig.compute_transition_time(period="start")`:
    `state.processed_requests == warmup.value + 1` **and** that request's own
    `info.started_at is not None`. The equality is exact and is evaluated per
    event, so the boundary is not merely "reached once enough requests have
    run" -- the counter can go past it unset. When a duration ceiling expires
    the scheduler abandons everything still queued, and a request cancelled
    before it ever reached `in_progress` has no `started_at`; a burst of those
    carries the counter beyond `value + 1` with no event left that could set
    the boundary. Observed for real: a run with `warmup.value=4` finishing at
    `processed_requests=9` and no boundary at all. (A single such cancellation
    is not enough -- anything that starts while the counter still equals
    `value + 1` sets it, one request late. Both are pinned in
    cli/tests/test_measured_window_semantics.py.)

    When `measure_start` stays None,
    `GenerativeBenchmarkTimings.finalized_measure_start` falls back to
    `finalized_request_start` -- the start of the run. The measured window
    becomes the *whole run*, every warmup request lands inside it, and
    `metrics.request_totals` reports warmup requests as measured. It is silent:
    the counts look like a short but ordinary measurement.

    The fallback is detected on the two fields it is made of rather than on a
    derived one: `scheduler_metrics.measure_start_time` and
    `request_start_time` are `finalized_measure_start` and
    `finalized_request_start`, so on the fallback path they are *the same
    value*. `warmup_duration` (their difference) is used only when those
    fields are absent.

    Equality has exactly two possible causes: the fallback, or a genuine
    boundary landing within one float ULP (~2.4e-7 s at a 2026 timestamp) of
    the run's first request start -- which would require the warmup requests to
    complete in under a quarter of a microsecond. Across every fixture run
    recorded here the smallest genuine warmup was 0.0865 s, some 360,000x that
    resolution. The remaining doubt is stated, not hidden: `basis` says which
    evidence was used and `checked` says whether any was conclusive.

    `scheduler_state.start_requests_time` and the per-request records, when the
    report carries them, are reported as corroboration. They are never required:
    a report that omits them is unverifiable, not broken.

    Established against the pinned runner's own accumulator in
    cli/tests/test_measured_window_semantics.py and end to end by request
    identity in cli/tests/test_measured_window_identity.py.
    """
    configuration = benchmark.get("config")
    warmup = configuration.get("warmup") if isinstance(configuration, dict) else None
    warmup = warmup if isinstance(warmup, dict) else {}
    percent, value = finite_number(warmup.get("percent")), finite_number(warmup.get("value"))
    configured = bool((percent is not None and percent > 0) or (value is not None and value > 0))

    metrics = benchmark.get("scheduler_metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    measure_start = finite_number(metrics.get("measure_start_time"))
    request_start = finite_number(metrics.get("request_start_time"))
    recorded_duration = finite_number(benchmark.get("warmup_duration"))

    if not configured:
        basis, checked, fallback = "no warmup phase is configured", True, False
    elif measure_start is not None and request_start is not None:
        basis = "scheduler_metrics.measure_start_time == request_start_time"
        checked, fallback = True, measure_start == request_start
    elif recorded_duration is not None:
        basis = "warmup_duration == 0 (measure/request start times absent)"
        checked, fallback = True, recorded_duration == 0.0
    else:
        basis, checked, fallback = "no usable timing fields in this report", False, False

    state = benchmark.get("scheduler_state")
    state = state if isinstance(state, dict) else {}
    start_requests_time = finite_number(state.get("start_requests_time"))
    corroboration: dict[str, Any] = {
        "measure_start_equals_start_requests_time":
            None if (measure_start is None or start_requests_time is None)
            else measure_start == start_requests_time,
        "requests_recorded_before_the_window": None,
    }
    records = benchmark.get("requests")
    if isinstance(records, dict) and measure_start is not None:
        before = 0
        seen = False
        for bucket in ("successful", "errored", "incomplete"):
            for record in records.get(bucket) or []:
                started = finite_number((record or {}).get("request_start_time"))
                if started is None:
                    continue
                seen = True
                if started < measure_start:
                    before += 1
        if seen:
            corroboration["requests_recorded_before_the_window"] = before

    return {
        "warmup_configured": configured,
        "warmup_mode": warmup.get("mode") if configured else None,
        "warmup_value": value if configured else None,
        "warmup_percent": percent if configured else None,
        "warmup_duration_seconds": recorded_duration,
        "measure_start_time": measure_start,
        "request_start_time": request_start,
        "basis": basis,
        "checked": checked,
        "warmup_boundary_crossed": not fallback,
        "measured_window_is_the_whole_run": fallback,
        "corroboration": corroboration,
    }


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

    # scheduler_metrics.requests_made spans the entire scheduler lifecycle, including
    # warmup and cooldown; the profile's report.exclude_warmup_and_cooldown requires
    # excluding both. metrics.request_totals is GuideLLM's own measured-window-only
    # count (filtered by measure_start_time/measure_end_time via get_within_range),
    # the same window the percentile/throughput metrics below already come from.
    successful = report_count(benchmark, "metrics", "request_totals", "successful")
    incomplete = report_count(benchmark, "metrics", "request_totals", "incomplete")
    errored = report_count(benchmark, "metrics", "request_totals", "errored")
    attempted = report_count(benchmark, "metrics", "request_totals", "total")
    failed = incomplete + errored
    if attempted != successful + failed:
        raise ValueError("GuideLLM request counts are inconsistent.")
    if attempted == 0:
        raise ValueError("GuideLLM report contains no attempted requests.")
    if successful == 0:
        raise ValueError("GuideLLM report contains no successful requests.")

    # A run whose warmup boundary was never established has no measured window
    # at all, only a fallback spanning the whole run. Its request_totals are
    # warmup requests wearing a measured label, and every latency and
    # throughput figure below is computed from that same window. Reporting
    # them as a short measurement would be a fabricated result, so this is
    # refused in the same way an empty report already is.
    integrity = measured_window_integrity(benchmark)
    if not integrity["warmup_boundary_crossed"]:
        raise ValueError(
            "This run never left its warmup phase, so it has no measured window: the "
            "runner's measurement start fell back to the start of the run, which means the "
            f"{attempted} request(s) it reports are warmup requests the profile excludes "
            "from measurement. Nothing here is a measurement. This normally means the run "
            "ended -- or was cancelled -- before its warmup finished; give it more time, or "
            "use a profile with a longer limit."
        )

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
    if getattr(args, "windows_bridge", False) and args.execute:
        from .bridge import BridgeSession, provenance
        from .bridge_http import Destination
        try:
            target = Destination.parse(args.endpoint.removesuffix("/v1").rstrip("/"))
            if target.host != "127.0.0.1":
                raise ValueError("The first Windows bridge supports IPv4 loopback only.")
            with BridgeSession(target.port) as session:
                bridged = argparse.Namespace(**vars(args))
                bridged.endpoint = session.endpoint
                bridged._bridge_credentials = session.credentials
                bridged._bridge_provenance = provenance(target.port)
                return _write_local_run(bridged)
        except (ValueError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 2
    return _write_local_run(args)


def _write_local_run(args: argparse.Namespace) -> int:
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
    tokenizer = getattr(args, "tokenizer", None)
    try:
        tokenizer = validate_tokenizer_source(tokenizer) if tokenizer else None
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    command = build_guidellm_command(
        root=root,
        endpoint=args.endpoint,
        model=args.model,
        profile_id=args.profile,
        raw_output_path=raw_output_path,
        tokenizer=tokenizer,
    )
    if getattr(args, "_bridge_credentials", None):
        command[command.index("--backend") + 1] += ",http2=false"
    safe_command = redact_endpoint(shlex.join(command), args.endpoint)

    if not args.execute:
        print("Dry run — this command has not contacted the endpoint:\n")
        print(safe_command)
        print("\nAdd --execute to create a local evidence bundle under .omobench-runs/.")
        return 0

    preflight = guidellm_runner_preflight()
    if not preflight.ok:
        print(preflight.message, file=sys.stderr)
        return 2

    run_directory.mkdir(parents=True, exist_ok=False)
    log_path = run_directory / "guidellm.stdout.log"
    telemetry_dir = getattr(args, "telemetry_dir", None)
    outcome = run_guidellm_command(
        command,
        log_path=log_path,
        root=root,
        profile_id=args.profile,
        telemetry_dir=(root / telemetry_dir).resolve() if telemetry_dir else None,
        **({"bridge_credentials": args._bridge_credentials} if getattr(args, "_bridge_credentials", None) else {}),
    )
    returncode = outcome.returncode

    log_path.write_text(
        redact_endpoint(log_path.read_text(encoding="utf-8"), args.endpoint),
        encoding="utf-8",
    )
    environment_path = run_directory / "environment.json"
    environment = doctor_report()
    if getattr(args, "_bridge_provenance", None):
        environment["transport"] = args._bridge_provenance
    environment_path.write_text(json.dumps(environment, indent=2) + "\n", encoding="utf-8")
    reproduce_path = run_directory / "reproduce.md"
    reproduce_path.write_text(
        "# Reproduce this local staging run\n\n"
        f"GuideLLM version: `{GUIDELLM_VERSION}`\n\n"
        + ("Transport: experimental Windows stdio bridge, HTTP/1.1, local reports only. "
           "Its latency is included. Reopen through the updated companion and select Windows bridge "
           f"with Windows port {args._bridge_provenance['windows_port']}; the private ephemeral endpoint "
           "below cannot be reused directly.\n\n" if getattr(args, "_bridge_provenance", None) else "")
        # Two identities, recorded separately: the string the server answered to,
        # and the tokenizer that built and counted the workload.
        + (f"Server model identifier: `{args.model}`\n\nTokenizer source (explicit override): `{tokenizer}`\n\n"
           if tokenizer else
           f"Server model identifier: `{args.model}`, also used as the tokenizer source.\n\n")
        + "Replace `<LOCAL_ENDPOINT>` with the local OpenAI-compatible endpoint.\n\n"
        "```bash\n"
        f"{safe_command}\n"
        "```\n",
        encoding="utf-8",
    )

    if raw_output_path.exists():
        compress_file(raw_output_path)

    if not outcome.evidence_log_finalized:
        # The benchmark itself ran. Everything it produced above -- the
        # compressed runner output, environment.json, reproduce.md, and the
        # partial log -- is preserved and left untouched; the run is simply
        # not a complete result, because its required runner log is not.
        # Driven by the explicit flag, never by the exit code alone: a runner
        # is free to exit 74 for reasons of its own.
        print(
            f"The benchmark ran, but its required evidence log could not be finalized: {log_path}\n"
            f"The measurements and files already written in {run_directory} are preserved. "
            "This run is not a completed result: check the disk and re-run when it can be written in full.",
            file=sys.stderr,
        )
        return returncode
    if returncode != 0:
        print(f"GuideLLM failed; inspect {log_path}", file=sys.stderr)
        return returncode

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
        if environment.get("transport") is not None:
            raise ValueError("Bridged measurements are local-only: the public schema cannot represent both serving and runner hosts.")
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
        # Say now whether that executable can actually load. Finding out at
        # launch instead means a contributor downloads several gigabytes, sees
        # "installed", and only then learns the machine is missing a system
        # library. Reported, never installed.
        libraries = managed_setup.check_runtime_libraries(
            manifest["runtime"]["executable_path"],
            expected_sha256=manifest["runtime"]["executable_sha256"])
        if libraries.missing:
            print(file=sys.stderr)
            print(libraries.message, file=sys.stderr)
            if libraries.detail:
                print(libraries.detail, file=sys.stderr)
            print("\nThe download is complete and verified; only this prerequisite is missing.",
                  file=sys.stderr)
            return 2
        if libraries.checked:
            print("Shared libraries: every library the runtime needs is present.")
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
    ui.add_argument(
        "--status-file",
        help=(
            "write a small JSON startup document (connection URL, port, token, pid) to this path "
            "for a non-interactive launcher, instead of it parsing this command's console output. "
            "The file contains the connection token: choose a user-private path outside the "
            "checkout. It is removed when the panel stops."
        ),
    )

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
    run.add_argument("--windows-bridge", action="store_true", help="explicit Windows loopback bridge, local reports only")
    run.add_argument("--execute", action="store_true", help="actually invoke GuideLLM; omit for a safe dry run")
    run.add_argument(
        "--tokenizer",
        default=None,
        help=(
            "optional tokenizer source for building and counting the synthetic workload: a "
            "Hugging Face repository id or a local tokenizer folder. Use it when --model is a "
            "serving alias rather than a repository id. Omit to keep the existing default, which "
            "is the --model value itself. This never changes which model the server serves."
        ),
    )
    run.add_argument(
        "--telemetry-dir",
        default=None,
        help=(
            "optional directory for a live-progress snapshot/history, read by the local panel's dashboard; "
            "additive only -- omit for the exact existing behavior. Relative to repository root."
        ),
    )

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

        return serve_ui(args.port, open_browser=not args.no_browser, status_file=args.status_file)
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
