"""Small, dependency-free starting point for the OmoBench CLI."""

from __future__ import annotations

import argparse
import gzip
import json
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
            timeout=10,
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


def doctor_report() -> dict[str, Any]:
    return {
        "omobench_cli_version": __version__,
        "python": sys.version.split()[0],
        "operating_system": platform.platform(),
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
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
