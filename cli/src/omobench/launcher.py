"""Experimental CPU launcher. The existing CLI remains the benchmark engine.

This milestone uses files already installed by the user. It does not install
dependencies, download weights, qualify a GPU backend, or publish evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import uuid4

from . import cli


@dataclass(frozen=True)
class LaunchPlan:
    root: Path
    server: Path
    model_file: Path
    model: str
    port: int
    startup_timeout: float

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def server_command(self) -> list[str]:
        return [str(self.server), "--model", str(self.model_file), "--alias", self.model,
                "--host", "127.0.0.1", "--port", str(self.port), "--device", "none",
                "--n-gpu-layers", "0", "--ctx-size", "2048", "--parallel", "1",
                "--reasoning", "off"]

    def benchmark_command(self, directory: Path) -> list[str]:
        return [sys.executable, "-u", "-m", "omobench.cli", "run",
                f"--endpoint={self.endpoint}", f"--model={self.model}",
                "--profile=chat-small-v1",
                f"--output-dir={directory.relative_to(self.root).as_posix()}", "--execute"]


def add_launch_parser(subcommands) -> None:
    parser = subcommands.add_parser("launch", help="experimental managed CPU launch (existing local files)")
    parser.add_argument("--server", type=Path, help="path to an installed llama-server executable")
    parser.add_argument("--model-file", type=Path, help="path to an existing GGUF model; it may be on another drive")
    parser.add_argument("--model", help="model identifier to assign to the server")
    parser.add_argument("--port", type=int, default=8092, help="model-server localhost port (default: 8092)")
    parser.add_argument("--startup-timeout", type=float, default=120, help="seconds allowed for loading (default: 120)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="inspect runner/OS readiness; do not start a model")
    mode.add_argument("--smoke", action="store_true", help="start, check model readiness, and stop; no benchmark")
    mode.add_argument("--execute", action="store_true", help="start the CPU server and run unchanged chat-small-v1")


def readiness() -> dict:
    version = cli.parsed_guidellm_version(cli.executable_version("guidellm"))
    native_windows = sys.platform == "win32"
    report = {"platform": sys.platform, "runner_version": version,
              "required_runner_version": cli.GUIDELLM_VERSION,
              "can_execute_here": not native_windows and version == cli.GUIDELLM_VERSION,
              "managed_gpu_support": False}
    if native_windows:
        # Decode Windows WSL output explicitly; a healthy WSL installation still
        # does not establish that its runner or a GPU backend is ready.
        try:
            process = subprocess.run(["wsl.exe", "--list", "--quiet"], capture_output=True, timeout=15)
            output = process.stdout
            encoding = "utf-16-le" if b"\x00" in output else "utf-8-sig"
            report["wsl_distributions"] = [line.strip().lstrip("\ufeff") for line in output.decode(encoding).splitlines()
                                            if line.strip()]
            report["wsl_probe_ok"] = process.returncode == 0
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            report["wsl_distributions"] = []
            report["wsl_probe_ok"] = False
        report["next_step"] = "Use the existing WSL2 environment for this experiment. Automatic Windows setup is not implemented."
    else:
        report["next_step"] = ("Runner ready. A compatible local llama-server and GGUF file are still required."
                               if report["can_execute_here"] else "Install the pinned runner in this Python environment using ./cli[runner].")
    return report


def make_plan(args: argparse.Namespace, root: Path) -> LaunchPlan:
    if not args.server or not args.model_file or not args.model:
        raise ValueError("Provide --server, --model-file, and --model, or use --check for readiness only.")
    server = args.server.expanduser().resolve()
    model_file = args.model_file.expanduser().resolve()
    if not server.is_file() or not os.access(server, os.X_OK):
        raise ValueError("--server must be an executable llama-server file in this operating system.")
    if sys.platform != "win32" and server.suffix.lower() == ".exe":
        raise ValueError("Use a Linux llama-server inside WSL. Windows-server/WSL-runner bridging is not qualified yet.")
    if not model_file.is_file():
        raise ValueError("--model-file must refer to an existing local GGUF file.")
    with model_file.open("rb") as model:
        if model.read(4) != b"GGUF":
            raise ValueError("The model file does not have a GGUF header.")
    name = cli.required_text(args.model, "Model identifier")
    if "," in name or any(ord(character) < 32 for character in name):
        raise ValueError("The model identifier cannot contain commas or control characters.")
    if not 1024 <= args.port <= 65535:
        raise ValueError("Choose a model-server port between 1024 and 65535.")
    if not math.isfinite(args.startup_timeout) or not 1 <= args.startup_timeout <= 600:
        raise ValueError("--startup-timeout must be between 1 and 600 seconds.")
    return LaunchPlan(root.resolve(), server, model_file, name, args.port, args.startup_timeout)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def wait_for_server(process: subprocess.Popen, plan: LaunchPlan) -> None:
    opener = build_opener(ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + plan.startup_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Model server exited with code {process.returncode}. Inspect server.stdout.log.")
        try:
            timeout = min(1, max(0.01, deadline - time.monotonic()))
            with opener.open(plan.endpoint + "/health", timeout=timeout) as response:
                healthy = response.status == 200 and json.loads(response.read(65536)).get("status") == "ok"
            if healthy:
                with opener.open(plan.endpoint + "/v1/models", timeout=timeout) as response:
                    models = json.loads(response.read(65536)).get("data", [])
                if not isinstance(models, list):
                    raise ValueError("Invalid model list")
                if any(isinstance(model, dict) and model.get("id") == plan.model for model in models):
                    if process.poll() is None:
                        return
        except HTTPError as error:
            if error.code != 503:
                raise RuntimeError(f"Unexpected server response HTTP {error.code}; check runtime compatibility.") from error
        except (URLError, TimeoutError, ValueError, AttributeError, OSError):
            pass
        time.sleep(0.1)
    raise RuntimeError("The model did not become ready before the startup timeout. Inspect server.stdout.log.")


def stop_owned_process(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        # Also reap descendants after the group leader exits.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass
    finally:
        process.wait()


def run_plan(plan: LaunchPlan, *, smoke: bool = False) -> int:
    if sys.platform == "win32":
        raise ValueError("This launch experiment runs in WSL2/Linux/macOS. Run --check for Windows readiness.")
    if not smoke and not readiness()["can_execute_here"]:
        raise ValueError(f"GuideLLM {cli.GUIDELLM_VERSION} is required in this environment before starting the model.")
    with socket.socket() as port_check:
        try:
            port_check.bind(("127.0.0.1", plan.port))
        except OSError as error:
            raise ValueError("The model-server port is occupied. Choose another port; existing servers are left running.") from error
    storage = plan.root / ".omobench-runs" / "launcher"
    if not storage.resolve().is_relative_to(plan.root):
        raise ValueError("Launcher records must stay inside the checkout.")
    directory = storage / ("job-" + uuid4().hex[:16])
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {"kind": "launcher-experiment", "execution_device": "cpu", "profile": "chat-small-v1",
                "created_at": datetime.now(UTC).isoformat(), "model": plan.model,
                "server_command": plan.server_command, "benchmark_command": plan.benchmark_command(directory),
                "smoke_only": smoke, "status": "preparing"}

    def stage(status: str, message: str) -> None:
        metadata["status"] = status
        (directory / "launcher.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(message, flush=True)

    server = benchmark = None
    previous = signal.getsignal(signal.SIGTERM)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        stage("preparing", f"Preparing CPU launch. Local diagnostics: {directory}")
        metadata["model_sha256"] = cli.sha256_file(plan.model_file)
        metadata["server_sha256"] = cli.sha256_file(plan.server)
        version = subprocess.run([str(plan.server), "--version"], capture_output=True, text=True, timeout=15, check=True)
        metadata["server_version"] = (version.stdout + version.stderr).strip()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(plan.root / "cli" / "src") + os.pathsep + environment.get("PYTHONPATH", "")
        # A managed launch must not inherit hidden model/host/device overrides.
        server_environment = {key: value for key, value in environment.items() if not key.startswith("LLAMA_ARG_")}
        with (directory / "server.stdout.log").open("w", encoding="utf-8") as server_log:
            stage("loading", "Loading the model on CPU…")
            server = subprocess.Popen(plan.server_command, stdout=server_log, stderr=subprocess.STDOUT,
                                      cwd=plan.root, env=server_environment, start_new_session=True)
            wait_for_server(server, plan)
            if smoke:
                stage("smoke_completed", "Server readiness check passed. No inference benchmark was run.")
                return 0
            stage("benchmarking", "Running the existing chat-small-v1 workload. Logs may be quiet for several minutes.")
            # Readiness checks stop here: no monitoring inference traffic is added.
            with (directory / "cli.stdout.log").open("w", encoding="utf-8") as benchmark_log:
                benchmark = subprocess.Popen(plan.benchmark_command(directory), cwd=plan.root, env=environment,
                                             stdout=benchmark_log, stderr=subprocess.STDOUT, start_new_session=True)
                heartbeat = time.monotonic()
                while benchmark.poll() is None:
                    if server.poll() is not None:
                        raise RuntimeError("The model server stopped during the benchmark. Partial output is preserved.")
                    if time.monotonic() - heartbeat >= 30:
                        print("Runner process is still active. Detailed progress: guidellm.stdout.log inside the run directory.", flush=True)
                        heartbeat = time.monotonic()
                    time.sleep(0.2)
            if benchmark.returncode != 0:
                raise RuntimeError(f"The benchmark exited with code {benchmark.returncode}; inspect cli.stdout.log.")
            runs = list(directory.glob("run-*"))
            required = ("guidellm-benchmarks.json.gz", "guidellm.stdout.log", "environment.json", "reproduce.md")
            if len(runs) != 1 or not all((runs[0] / name).is_file() for name in required):
                raise RuntimeError("The CLI did not produce a complete local run. Inspect the preserved logs.")
            metadata["run_directory"] = str(runs[0].relative_to(plan.root))
            metadata["summary"] = cli.normalize_guidellm_report(cli.load_gzip_json(runs[0] / required[0]))
            stage("completed", f"Benchmark completed. Local CPU measurements: {runs[0]}")
            return 0
    except KeyboardInterrupt:
        stage("interrupted", "Launch cancelled. Partial files are preserved locally.")
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        metadata["error"] = str(error)
        stage("failed", f"Launch failed: {error}")
        return 1
    finally:
        stop_owned_process(benchmark)
        stop_owned_process(server)
        metadata["finished_at"] = datetime.now(UTC).isoformat()
        (directory / "launcher.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        signal.signal(signal.SIGTERM, previous)


def launch(args: argparse.Namespace) -> int:
    try:
        if args.check:
            print(json.dumps(readiness(), indent=2))
            return 0
        root = cli.find_repository_root()
        if root is None:
            raise ValueError("Run this experiment from an OmoBench checkout.")
        plan = make_plan(args, root)
        if not args.execute and not args.smoke:
            print(json.dumps({"dry_run": True, "execution_device": "cpu", "profile": "chat-small-v1",
                              "server_command": plan.server_command,
                              "benchmark_command": plan.benchmark_command(root / ".omobench-runs" / "launcher" / "job-PREVIEW"),
                              "note": "Nothing was started or downloaded. Use --smoke for readiness or --execute to benchmark."}, indent=2))
            return 0
        return run_plan(plan, smoke=args.smoke)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
