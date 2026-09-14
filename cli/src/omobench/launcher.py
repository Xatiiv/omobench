"""Managed CPU launcher: starts a verified llama-server and smoke-tests or runs it.

This module never changes the benchmark engine: `run_execute` builds the same
argparse.Namespace the `omobench run --execute` command builds and hands it to
`cli.write_local_run`, so the GuideLLM command construction, profile, and
evidence layout are exactly the existing ones. It only adds process lifecycle
management for a server this module starts itself.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import cli, managed_setup


class LauncherError(Exception):
    """Raised for any managed-launch problem: verification, startup, or readiness."""


CPU_ONLY_SERVER_ARGS = ["--device", "none", "--n-gpu-layers", "0", "--ctx-size", "2048", "--parallel", "1", "--reasoning", "off"]
READY_TIMEOUT_SECONDS = 180
READY_POLL_INTERVAL_SECONDS = 1.0


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def build_server_command(executable: Path, model_path: Path, alias: str, port: int) -> list[str]:
    """CPU-only, localhost-only server startup command for the managed recipe.

    Uses the canonical model identifier (alias) as the server alias so the
    existing run/profile flow addresses it exactly as the model catalog does.
    """
    return [
        str(executable),
        "-m",
        str(model_path),
        "--alias",
        alias,
        *CPU_ONLY_SERVER_ARGS,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def _stop_process(process: subprocess.Popen) -> None:
    """Stop only the process (and its descendants) this launcher started."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, OSError):
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


def wait_for_ready(endpoint: str, alias: str, process: subprocess.Popen, *, timeout: float = READY_TIMEOUT_SECONDS) -> None:
    """Poll /v1/models until the managed server reports the expected alias.

    Raises LauncherError (without performing any inference) if the server
    exits early or never becomes ready in time.
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LauncherError(f"The managed model server exited early with code {process.returncode}.")
        try:
            with urlopen(Request(endpoint + "/v1/models"), timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            ids = {entry.get("id") for entry in payload.get("data", []) if isinstance(entry, dict)}
            if alias in ids:
                return
        except (URLError, OSError, ValueError) as error:
            last_error = error
        time.sleep(READY_POLL_INTERVAL_SECONDS)
    raise LauncherError(
        f"The managed model server did not report alias {alias!r} as ready within {timeout:.0f}s."
    ) from last_error


@contextlib.contextmanager
def managed_server(root: Path, recipe_id: str, storage_dir: Path, *, log_path: Path | None = None):
    """Re-verify, start, and later stop the verified managed llama-server.

    Yields (process, endpoint, recipe). Only ever stops the process this
    context manager started; never touches WSL or any other server.
    """
    platform_status = managed_setup.check_platform()
    if not platform_status.ok:
        raise LauncherError(platform_status.message)
    recipe = managed_setup.get_recipe(root, recipe_id)
    installed = managed_setup.verify_installed(root, recipe, storage_dir)

    port = free_loopback_port()
    command = build_server_command(installed["executable_path"], installed["model_path"], recipe["model_id"], port)

    log_file = open(log_path, "w", encoding="utf-8") if log_path is not None else subprocess.DEVNULL
    popen_kwargs: dict[str, Any] = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT, **popen_kwargs)
    except OSError as error:
        if hasattr(log_file, "close"):
            log_file.close()
        raise LauncherError(f"Could not start the managed model server: {error}") from error

    try:
        yield process, f"http://127.0.0.1:{port}", recipe
    finally:
        _stop_process(process)
        if hasattr(log_file, "close"):
            log_file.close()


def run_smoke(root: Path, recipe_id: str, storage_dir: Path, *, log_path: Path | None = None) -> dict[str, Any]:
    """Start the managed server, confirm readiness and alias, then stop it.

    Never performs inference and never reports a performance result.
    """
    with managed_server(root, recipe_id, storage_dir, log_path=log_path) as (process, endpoint, recipe):
        wait_for_ready(endpoint, recipe["model_id"], process)
        return {
            "status": "ready",
            "model_id": recipe["model_id"],
            "benchmark_profile": recipe["benchmark_profile"],
            "message": "The managed server started, loaded the model, and reported the expected alias. "
            "No inference was performed and no benchmark result was produced.",
        }


def run_execute(
    root: Path,
    recipe_id: str,
    storage_dir: Path,
    *,
    output_dir: str = ".omobench-runs",
    log_path: Path | None = None,
) -> int:
    """Start the managed server, then run the existing, unchanged CLI benchmark against it."""
    with managed_server(root, recipe_id, storage_dir, log_path=log_path) as (process, endpoint, recipe):
        wait_for_ready(endpoint, recipe["model_id"], process)
        args = argparse.Namespace(
            endpoint=endpoint,
            model=recipe["model_id"],
            profile=recipe["benchmark_profile"],
            output_dir=output_dir,
            execute=True,
        )
        return cli.write_local_run(args)


def install_sigterm_as_keyboard_interrupt() -> Any:
    """Let Ctrl+C and SIGTERM both reach the same cleanup path; return the previous handler."""
    previous = signal.getsignal(signal.SIGTERM)

    def _raise(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _raise)
    return previous


def run_launch_command(args: argparse.Namespace) -> int:
    root = cli.find_repository_root()
    if root is None:
        print("Could not find an OmoBench checkout.", file=sys.stderr)
        return 2
    try:
        storage_dir = managed_setup.normalize_storage_dir(args.storage_dir)
    except managed_setup.ManagedSetupError as error:
        print(str(error), file=sys.stderr)
        return 2

    run_directory = (root / args.output_dir).resolve()
    run_directory.mkdir(parents=True, exist_ok=True)
    log_path = run_directory / f"managed-launch-{args.recipe}.log"

    previous_handler = install_sigterm_as_keyboard_interrupt()
    try:
        if args.smoke:
            result = run_smoke(root, args.recipe, storage_dir, log_path=log_path)
            print(f"Managed server ready: {result['message']}")
            print(f"Model: {result['model_id']} · Profile: {result['benchmark_profile']}")
            print(f"Server diagnostic log: {log_path}")
            return 0
        returncode = run_execute(root, args.recipe, storage_dir, output_dir=args.output_dir, log_path=log_path)
        print(f"Server diagnostic log: {log_path}")
        return returncode
    except (managed_setup.ManagedSetupError, LauncherError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Stopping the managed model server…", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
