"""Loopback browser controller. All benchmark and package operations use the CLI."""

from __future__ import annotations

import contextlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import cli


JOB_ID = re.compile(r"^job-[a-f0-9]{16}$")
ACTIVE = {"running", "cancelling", "packaging", "preparing", "submitting"}
MAX_LOG_BYTES = 24_000


class UIError(Exception):
    def __init__(self, message: str, status: int = 400, reason: str | None = None):
        super().__init__(message)
        self.status = status
        # Stable, machine-readable cause for the few startup failures a
        # non-interactive launcher has to tell apart (see STATUS_FILE_SCHEMA).
        # Never shown to the browser; the human-readable message still is.
        self.reason = reason


@contextmanager
def panel_lock(root: Path):
    """One panel per checkout, even when a second invocation uses another port."""
    path = root / ".omobench-runs" / "ui.lock"
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise UIError("The panel lock must stay inside the checkout.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                if path.stat().st_size == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise UIError(
                "A control panel is already running for this checkout. Use its browser window or stop it first.",
                reason="already_running",
            ) from error
        yield


def text_field(data: dict, key: str, *, optional: bool = False) -> str:
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise UIError(f"Invalid {key.replace('_', ' ')}.")
    value = value.strip()
    if not value and not optional:
        raise UIError(f"Enter {key.replace('_', ' ')}.")
    return value


def endpoint_origin(endpoint: str) -> str:
    """V1 collects hardware on this machine, so only same-machine servers are valid."""
    try:
        url = urlsplit(endpoint)
        port = url.port
    except ValueError as error:
        raise UIError("Enter a valid local HTTP server address.") from error
    if (url.scheme not in {"http", "https"}
            or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.username is not None or url.password is not None
            or url.query or url.fragment or url.path.rstrip("/") not in {"", "/v1"}
            or port == 0):
        raise UIError(
            "Enter the loopback address your model server listens on, such as "
            "http://127.0.0.1:<port> (an optional /v1 suffix is accepted). This panel only "
            "contacts loopback addresses on the machine the benchmark runner runs on: it "
            "records that machine's hardware as the result's hardware, and it must not be "
            "usable to reach arbitrary hosts. A server on another machine, or on the Windows "
            "side of a WSL runner, needs a loopback forwarder you set up yourself.")
    return f"{url.scheme}://{url.netloc}"


def run_config(data: dict) -> dict:
    if type(data.get("windows_bridge", False)) is not bool:
        raise UIError("Windows bridge selection must be a boolean.")
    endpoint = endpoint_origin(text_field(data, "endpoint"))
    model = text_field(data, "model")
    if "," in model:
        raise UIError("Model identifiers cannot contain commas in the current GuideLLM backend.")
    profile = text_field(data, "profile")
    if profile not in cli.PROFILE_SPECS:
        raise UIError("Choose an existing OmoBench profile.")
    device = text_field(data, "execution_device")
    if device not in {"cpu", "gpu"}:
        raise UIError("Choose CPU or GPU execution.")
    if data.get("device_confirmed") is not True:
        raise UIError("Confirm the execution device in your model server first.")
    # Two separate identities. `model` is the exact string the server's API
    # accepts; `tokenizer` is the repository or folder whose tokenizer builds
    # and counts the workload. Blank keeps GuideLLM's existing default, which
    # is to use `model` for both.
    tokenizer = text_field(data, "tokenizer", optional=True)
    if tokenizer:
        try:
            tokenizer = cli.validate_tokenizer_source(tokenizer)
        except ValueError as error:
            raise UIError(str(error)) from error
    return {"endpoint": endpoint, "model": model, "profile": profile,
            "execution_device": device, "tokenizer": tokenizer or None, "windows_bridge": data.get("windows_bridge", False)}


def safe_log(value: str) -> str:
    # The CLI redacts its completed log. Apply redaction during live display too.
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = re.sub(r"https?://[^\s\"'<>]+", "<ENDPOINT>", value)
    value = re.sub(r"(?im)(authorization\s*[:=]\s*)[^\r\n]+", r"\1<REDACTED>", value)
    return re.sub(r"(?i)(api[_-]?key|token)(\s*[:=]\s*)\S+", r"\1\2<REDACTED>", value)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UIError("The model server redirected the request. Enter its direct localhost address.")


def streaming_probe(endpoint: str, model: str, *, _headers=None) -> tuple[str, str]:
    """One small streaming request, reported rather than raised.

    This is the check that matters: OmoBench's workload is streaming
    `/v1/chat/completions`, so a server that answers /v1/models but cannot
    stream chat completions cannot run a benchmark.
    """
    try:
        probe_connection({"endpoint": endpoint, "model": model}, **({"_headers": _headers} if _headers else {}))
    except UIError as error:
        message = str(error)
        if "redirected" in message or "streaming" in message or "chunk" in message:
            return "failed", (
                message + " OmoBench's workload is streaming /v1/chat/completions; a server that "
                "cannot stream it cannot run this benchmark, whatever else it supports.")
        return "failed", message
    return "ok", ("One small streaming chat completion arrived, so this server accepts this identifier "
                  "and streams responses. The test request is not benchmark evidence.")


def probe_connection(data: dict, *, _headers=None) -> dict:
    endpoint = endpoint_origin(text_field(data, "endpoint"))
    model = text_field(data, "model")
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply OK."}],
               "max_tokens": 1, "temperature": 0, "stream": True}
    request = Request(endpoint + "/v1/chat/completions", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json", "Accept": "text/event-stream", **(_headers or {})})
    try:
        # Ignore ambient HTTP proxies: this operation must stay on loopback.
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=15) as response:
            if response.status != 200 or "text/event-stream" not in response.headers.get("Content-Type", ""):
                raise UIError("The server did not return a streaming chat response.")
            deadline = time.monotonic() + 20
            for _ in range(64):
                if time.monotonic() > deadline:
                    break
                line = response.readline(4096).decode("utf-8", errors="replace").strip()
                if line.startswith("data:") and line[5:].strip() != "[DONE]":
                    chunk = json.loads(line[5:])
                    if isinstance(chunk, dict) and isinstance(chunk.get("choices"), list) and chunk["choices"]:
                        return {"message": "Streaming connection works. The test request is not benchmark evidence."}
            raise UIError("No valid streaming chat chunk arrived.")
    except HTTPError as error:
        raise UIError(
            f"The model server answered HTTP {error.code} for a streaming chat completion. "
            "That usually means the model identifier is not the one it serves, or the server "
            "rejects this request shape.") from error
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise UIError("Could not read a streaming response. Check that the model is loaded and the server is running.") from error


def served_models(endpoint: str, *, timeout: float = 8.0, _headers=None) -> dict:
    """Ask the server which models it serves, if it implements that endpoint.

    Only the endpoint the contributor entered is contacted, and only on
    loopback; nothing is discovered, scanned or guessed. A server that does not
    implement /v1/models is normal, not a failure.
    """
    request = Request(endpoint + "/v1/models", headers={"Accept": "application/json", **(_headers or {})})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
            document = json.loads(response.read(1024 * 1024))
    except HTTPError as error:
        code = error.code
        error.close()
        # The server answered, so it is reachable; it simply does not offer a listing.
        return {"models": None, "reachable": True, "detail":
                f"This server answered HTTP {code} for /v1/models, so its served identifiers could not be listed."}
    except UIError as error:
        return {"models": None, "reachable": True, "detail": str(error)}
    except (URLError, TimeoutError, OSError) as error:
        return {"models": None, "reachable": False, "detail": f"No response from {endpoint}: {type(error).__name__}."}
    except ValueError:
        return {"models": None, "reachable": True,
                "detail": "This server's /v1/models response was not readable JSON."}
    entries = document.get("data") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return {"models": None, "reachable": True,
                "detail": "This server's /v1/models response did not contain a model list."}
    identifiers = [entry["id"] for entry in entries
                   if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]]
    if not identifiers:
        return {"models": [], "reachable": True,
                "detail": "This server listed no models. Load a model in the server first."}
    return {"models": identifiers, "reachable": True,
            "detail": "Served identifiers listed by the server itself."}


def server_check(data: dict, *, can_execute: bool = True, runner_message: str = "",
                 runner_health: Any = None, _headers=None) -> dict:
    """What can actually be established about a contributor's own server.

    Deliberately staged rather than a single pass/fail: "the server answered"
    and "this benchmark can run" are different claims, and a working
    /v1/models response establishes neither streaming support nor a usable
    tokenizer. Every stage says what it checked, and the report ends by naming
    what it did not establish.

    Runs in this process, which is the same environment the benchmark runner is
    launched from, so a reachability result here is a reachability result for
    the run.
    """
    stages: list[dict] = []
    model = text_field(data, "model", optional=True)
    tokenizer = text_field(data, "tokenizer", optional=True)

    def stage(identifier, title, state, detail, **extra):
        stages.append({"id": identifier, "title": title, "state": state, "detail": detail, **extra})

    try:
        endpoint = endpoint_origin(text_field(data, "endpoint"))
        stage("endpoint", "Server address accepted", "ok",
              f"{endpoint} is a loopback address on this machine, which is where the runner runs.")
    except UIError as error:
        stage("endpoint", "Server address accepted", "failed", str(error))
        for identifier, title in (("runner", "Benchmark runner available"),
                                  ("reachable", "Server reachable"),
                                  ("model", "Served model identifier"),
                                  ("streaming", "Streaming chat completions"),
                                  ("tokenizer", "Tokenizer source")):
            stage(identifier, title, "skipped", "Not attempted: enter a usable server address first.")
        return {"stages": stages, "models": None, "ready": False,
                "checked_from": checked_from(can_execute),
                "not_established": NOT_ESTABLISHED}

    # Installed metadata is not health. When a real import was attempted, that
    # is what this stage reports; otherwise it says only what it checked.
    if runner_health is not None:
        stage("runner", "Benchmark runner imports", "ok" if runner_health.ok else "failed",
              runner_health.message, detail_extra=runner_health.detail or None,
              checked="the runner was imported in a separate, bounded process; "
                      "nothing was downloaded and no inference ran")
    else:
        stage("runner", "Benchmark runner available", "ok" if can_execute else "failed",
              (runner_message or ("This environment can run the benchmark." if can_execute
                                  else "This environment cannot run the benchmark."))
              + " This checked installed metadata only, not whether the runner imports.")

    listing = served_models(endpoint, **({"_headers": _headers} if _headers else {}))
    identifiers, listing_detail = listing["models"], listing["detail"]
    if not listing["reachable"]:
        stage("reachable", "Server reachable", "failed",
              listing_detail + " Start the model server, confirm its port, and confirm it listens on "
              "loopback on this machine rather than only on another interface or another host.")
        stage("model", "Served model identifier", "skipped", "Not attempted: the server did not answer.")
        stage("streaming", "Streaming chat completions", "skipped", "Not attempted: the server did not answer.")
    else:
        stage("reachable", "Server reachable", "ok", "The server answered this panel's request.")
        if identifiers is None:
            stage("model", "Served model identifier", "unknown",
                  listing_detail + " Enter the identifier your server documents; the streaming check below "
                  "is what actually proves the identifier works.")
        elif not model:
            stage("model", "Served model identifier", "unknown",
                  "Choose one of the identifiers this server listed.", options=identifiers)
        elif model in identifiers:
            stage("model", "Served model identifier", "ok",
                  f"{model} is served by this server.", options=identifiers)
        else:
            stage("model", "Served model identifier", "failed",
                  f"This server does not list {model}. It serves: {', '.join(identifiers)}. "
                  "Use the identifier exactly as the server reports it, including any suffix.",
                  options=identifiers)
        if model:
            state, detail = streaming_probe(endpoint, model, **({"_headers": _headers} if _headers else {}))
            stage("streaming", "Streaming chat completions", state, detail)
        else:
            stage("streaming", "Streaming chat completions", "skipped",
                  "Not attempted: enter or choose a served model identifier first.")

    report = cli.tokenizer_source_report(tokenizer, served_model=model or None)
    stage("tokenizer", "Tokenizer source", "ok" if report["ok"] else "failed", report["message"],
          cached=report.get("cached"), kind=report["kind"], source=report["source"],
          checks=report.get("checks", []))

    # "Ready" means every check passed, except that a server which does not
    # publish a model list leaves that one unconfirmable -- which must not block
    # a run the server itself accepted.
    ready = bool(model) and all(
        entry["state"] == "ok" or (entry["id"] == "model" and entry["state"] == "unknown")
        for entry in stages)
    return {"stages": stages, "models": identifiers, "ready": ready,
            "checked_from": checked_from(can_execute),
            "not_established": NOT_ESTABLISHED}


def checked_from(can_execute: bool) -> str:
    return ("Checked from the environment that launches the benchmark runner"
            if can_execute else
            "Checked from this panel process, which cannot run the benchmark itself")


# Things no reachability or syntax check can establish, stated rather than implied.
NOT_ESTABLISHED = [
    "That this tokenizer matches the weights the server actually loaded. Loading a tokenizer "
    "proves only that its files parse.",
    "Which device performed inference. CPU or GPU is your declaration about the server.",
    "The throughput or latency of a full profile run, which only a benchmark measures.",
    "That the result qualifies for the community registry, which is a separate check.",
]


class Controller:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.storage = self.root / ".omobench-runs" / "ui"
        if not self.storage.resolve().is_relative_to(self.root):
            raise UIError("The UI staging directory must stay inside the checkout.")
        self.storage.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.process: subprocess.Popen | None = None
        self.worker: threading.Thread | None = None
        self.busy = False
        self.closing = False
        # What the companion last read on the serving machine. Session-only: a
        # detection is a reading of a machine at a time, not a saved fact.
        self.last_serving_probe: dict | None = None
        from .github_auth import DeviceSession
        self.github_auth = DeviceSession()
        self.community_reviews: dict[str, dict] = {}
        self.environment = os.environ.copy()
        self.environment["PYTHONPATH"] = str(self.root / "cli" / "src") + os.pathsep + self.environment.get("PYTHONPATH", "")
        self.environment["PYTHONUNBUFFERED"] = "1"
        self.environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self._recover()

    def _inside(self, path: Path) -> Path:
        if not path.resolve().is_relative_to(self.storage.resolve()) or path.is_symlink():
            raise UIError("Run files must stay inside the UI staging directory.")
        return path

    def _directory(self, job_id: str) -> Path:
        if not JOB_ID.fullmatch(job_id):
            raise UIError("Unknown run.", 404)
        return self._inside(self.storage / job_id)

    def _get(self, job_id: str) -> dict:
        self._directory(job_id)
        if job_id not in self.jobs:
            raise UIError("Unknown run.", 404)
        return self.jobs[job_id]

    def _save(self, job: dict) -> None:
        path = self._directory(job["id"]) / "job.json"
        temporary = self._inside(path.with_suffix(".tmp"))
        temporary.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self._inside(path))

    def _recover(self) -> None:
        for directory in self.storage.glob("job-*"):
            if not JOB_ID.fullmatch(directory.name):
                continue
            try:
                path = self._inside(directory / "job.json")
                job = json.loads(path.read_text(encoding="utf-8"))
                if job.get("id") != directory.name:
                    continue
                if job.get("status") in ACTIVE:
                    previous = job["status"]
                    job["status"] = "completed" if previous not in {"running", "cancelling"} and job.get("summary") else "interrupted"
                    if previous == "packaging":
                        job["validation"] = None
                    job["error"] = "The controller stopped before this operation completed. Inspect the evidence before starting a new run."
                    self._save(job)
                self.jobs[job["id"]] = job
            except (OSError, ValueError, AttributeError, UIError):
                continue

    def setup(self) -> dict:
        report = cli.doctor_report()
        preflight = cli.guidellm_runner_preflight()
        native_windows = sys.platform == "win32"
        return {"environment": report, "profiles": cli.PROFILE_SPECS,
                # How each profile's phases really resolve, so a page can mark a
                # profile whose declaration its runner does not enforce.
                "profile_contracts": {profile_id: cli.profile_phase_contract(self.root, profile_id)
                                      for profile_id in cli.PROFILE_SPECS},
                "frameworks": cli.RUNTIME_FRAMEWORKS, "catalog": self.catalog_models(),
                "can_execute": not native_windows and preflight.ok,
                "runner_message": ("Start this panel from WSL2 or Linux to execute benchmarks." if native_windows else
                    preflight.message),
                "can_validate": importlib.util.find_spec("jsonschema") is not None}

    def _cli_command(self, arguments: list[str]) -> list[str]:
        return [sys.executable, "-u", "-m", "omobench.cli", *arguments]

    @contextmanager
    def _bridge_request(self, data):
        if type(data.get("windows_bridge", False)) is not bool:
            raise UIError("Windows bridge selection must be a boolean.")
        if not data.get("windows_bridge"):
            yield data, {}
            return
        from .bridge import BridgeSession
        port = self._bridge_port(data)
        try:
            with BridgeSession(port) as session:
                yield {**data, "endpoint": session.endpoint}, {"X-OmoBench-Bridge-Token": session.credentials["token"]}
        except (ValueError, OSError) as error:
            raise UIError("Windows bridge unavailable. Open the updated companion and check that the Windows server is running.") from error

    def _bridge_port(self, data):
        from .bridge import helper_path
        from .bridge_http import Destination
        try:
            target = Destination.parse(endpoint_origin(text_field(data, "endpoint")))
            if target.host != "127.0.0.1":
                raise ValueError("IPv4 required")
            helper_path()
            return target.port
        except (ValueError, OSError) as error:
            raise UIError("Windows bridge needs an explicit IPv4 loopback port and an updated companion-managed WSL panel.") from error

    def connection(self, data: dict) -> dict:
        with self.lock:
            if self.busy or self.closing:
                raise UIError("Wait for the active operation before testing the connection.", 409)
            self.busy = True
        try:
            with self._bridge_request(data) as (checked, headers):
                return probe_connection(checked, **({"_headers": headers} if headers else {}))
        finally:
            with self.lock:
                self.busy = False

    def start(self, data: dict) -> dict:
        config = run_config(data)
        transport = None
        if config["windows_bridge"]:
            from .bridge import provenance
            transport = provenance(self._bridge_port(data))
        if sys.platform == "win32":
            raise UIError("The current GuideLLM runner needs WSL2, Linux, or macOS. Start omobench ui there.")
        preflight = cli.guidellm_runner_preflight()
        if not preflight.ok:
            raise UIError(preflight.message)
        with self.lock:
            if self.busy or self.closing:
                raise UIError("Another operation is active. Wait or cancel the current benchmark.", 409)
            self.busy = True
            job_id = "job-" + secrets.token_hex(8)
            directory = self._directory(job_id)
            directory.mkdir()
            job = {"id": job_id, "created_at": datetime.now(UTC).isoformat(), "status": "running",
                   "model": config["model"], "profile": config["profile"], "execution_device": config["execution_device"],
                   "tokenizer": config.get("tokenizer"),
                   "summary": None, "error": None, "validation": None, "submission": None}
            if transport:
                job["transport"] = transport
            self.jobs[job_id] = job
            self._save(job)
            telemetry_dir = directory.relative_to(self.root).as_posix() + "/telemetry"
            arguments = ["run", f"--endpoint={config['endpoint']}", f"--model={config['model']}",
                         f"--profile={config['profile']}", f"--output-dir={directory.relative_to(self.root).as_posix()}",
                         f"--telemetry-dir={telemetry_dir}", "--execute"]
            if config["windows_bridge"]:
                arguments.append("--windows-bridge")
            if config.get("tokenizer"):
                arguments.append(f"--tokenizer={config['tokenizer']}")
            self.worker = threading.Thread(target=self._execute, args=(job, arguments), daemon=True)
            self.worker.start()
            return dict(job)

    def _execute(self, job: dict, arguments: list[str]) -> None:
        try:
            with self._inside(self._directory(job["id"]) / "cli.log").open("w", encoding="utf-8") as log:
                with self.lock:
                    if job["status"] == "cancelling" or self.closing:
                        job["status"] = "cancelled"
                        return
                    self.process = subprocess.Popen(self._cli_command(arguments), cwd=self.root, env=self.environment,
                                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                returncode = self.process.wait()
            with self.lock:
                if job["status"] == "cancelling":
                    job["status"] = "cancelled"
                elif returncode != 0:
                    job["status"] = "failed"
                    job["error"] = f"The benchmark exited with code {returncode}. See the log below."
                else:
                    run = self.run_directory(job["id"])
                    report = cli.load_gzip_json(self._inside(run / "guidellm-benchmarks.json.gz"))
                    job["summary"] = cli.normalize_guidellm_report(report)
                    job["status"] = "completed"
        except (OSError, ValueError, UIError) as error:
            with self.lock:
                job["status"] = "cancelled" if job["status"] == "cancelling" else "failed"
                job["error"] = safe_log(str(error))
        finally:
            with self.lock:
                self.process = None
                self.busy = False
                job["finished_at"] = datetime.now(UTC).isoformat()
                self._save(job)

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self._get(job_id)
            if job["status"] not in {"running", "cancelling"}:
                raise UIError("Only a running benchmark can be cancelled.", 409)
            job["status"] = "cancelling"
            self._save(job)
            process = self.process
        if process is not None:
            # CLI and GuideLLM share this process group. Never stop the separately owned model server.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                # Also clean up descendants if the CLI exited before a child did.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass
        return self.detail(job_id)

    def close(self) -> None:
        with self.lock:
            self.closing = True
            active = next((j["id"] for j in self.jobs.values() if j["status"] in {"running", "cancelling"}), None)
            self.community_reviews.clear()
        self.github_auth.close()
        if active:
            self.cancel(active)
        if self.worker:
            self.worker.join(timeout=10)

    def run_directory(self, job_id: str) -> Path:
        directory = self._directory(job_id)
        runs = [p for p in directory.glob("run-*") if cli.RUN_ID_PATTERN.fullmatch(p.name) and p.is_dir()]
        if len(runs) != 1:
            raise UIError("This operation has no single completed evidence directory.")
        return self._inside(runs[0])

    def telemetry(self, job_id: str) -> dict:
        """The job's live telemetry, if any -- a small, bounded snapshot the
        in-process GuideLLM adapter (telemetry.py) writes to
        <job>/telemetry/telemetry.json, kept outside the run's own evidence
        directory. Never required: an older job, one run without telemetry
        attached, or one where the adapter disabled itself all just report
        "available": false, honestly, rather than fabricating progress."""
        directory = self._directory(job_id)
        if job_id not in self.jobs:
            raise UIError("Unknown run.", 404)
        snapshot_path = self._inside(directory / "telemetry" / "telemetry.json")
        if not snapshot_path.is_file():
            return {"available": False}
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"available": False}
        if not isinstance(snapshot, dict):
            return {"available": False}
        snapshot["available"] = True
        return snapshot

    def set_prefill(self, job_id: str, **fields: Any) -> None:
        """Attach optional, editable prefill metadata to a job (for example a managed
        recipe's own pinned download URL as model_source), so the package form can
        display it without requiring the operator to type it. Never affects packaging
        by itself: the operator still submits (and may change) the package form."""
        with self.lock:
            job = self._get(job_id)
            job.update(fields)
            self._save(job)

    def detail(self, job_id: str) -> dict:
        with self.lock:
            job = dict(self._get(job_id))
        logs = []
        paths = [self._directory(job_id) / "cli.log"]
        try:
            run = self.run_directory(job_id)
            paths.append(run / "guidellm.stdout.log")
            job["run_path"] = run.relative_to(self.root).as_posix()
        except UIError:
            pass
        for path in paths:
            if self._inside(path).is_file():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - MAX_LOG_BYTES))
                    logs.append(safe_log(stream.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")))
        job["log"] = "\n".join(logs)
        return job

    def catalog_models(self) -> list[str]:
        """The catalogued model IDs, the registry's own source of truth."""
        return cli.catalog_model_ids(self.root)

    def local_report(self, job_id: str) -> dict:
        """A local record of what this machine measured.

        Deliberately not the public result.json contract: it needs no
        publication metadata, works for an uncatalogued model and an unknown
        revision, and says "unknown" instead of inventing anything. It carries
        measurements and the identities OmoBench itself recorded -- never the
        runner log, the environment snapshot, the endpoint, or anything else
        that could hold a secret.
        """
        with self.lock:
            job = dict(self._get(job_id))
        if not job.get("summary"):
            raise UIError("This run has no completed measurements to export.")
        summary = job["summary"]
        tokenizer = job.get("tokenizer")

        def declared(value):
            return value if value else "unknown"

        return {
            "kind": "omobench-local-report",
            "format_version": 1,
            "note": (
                "A local record of measurements taken on this machine. This is not an OmoBench "
                "public result: the registry's contract is result.json, produced by packaging, "
                "which additionally requires catalogued model identity, an immutable revision, "
                "exact runtime provenance and the profile's acceptance thresholds. Completing a "
                "benchmark and being eligible for publication are separate things."
            ),
            "run": {
                "id": job["id"],
                "status": job["status"],
                "created_at": job["created_at"],
                "finished_at": job.get("finished_at"),
                "profile_id": job["profile"],
                "runner": "guidellm",
                "runner_version": summary.get("runner_version", "unknown"),
                "started_at": summary.get("started_at"),
            },
            "server": {
                "model_identifier": job["model"],
                "identifier_is": "the string this run sent to the server's API, not a catalog identity",
                "execution_device": job["execution_device"],
                "execution_device_source": "declared by the person running the benchmark",
            },
            "workload": {
                "tokenizer_source": tokenizer or job["model"],
                "tokenizer_source_explicit": bool(tokenizer),
                "tokenizer_note": (
                    "An explicit tokenizer source was used to build and count the workload."
                    if tokenizer else
                    "No override was given, so the server model identifier was also used as the tokenizer source."
                ),
            },
            "recorded_provenance": {
                "observed_by_omobench": {
                    "profile_id": job["profile"],
                    "runner_version": summary.get("runner_version", "unknown"),
                    "measurements": "produced by this run",
                },
                "declared_or_prefilled": {
                    "model_revision": declared(job.get("model_revision")),
                    "model_source": declared(job.get("model_source")),
                    "quantization": declared(job.get("quantization")),
                    "runtime_framework": declared(job.get("framework")),
                    "runtime_version": declared(job.get("framework_version")),
                    "runtime_arguments": job.get("runtime_args") or [],
                },
            },
            "measurements": summary.get("results"),
            # The context a comparison needs, in the three groups that answer
            # different questions. Hardware and CPU/GPU are the *point* of many
            # comparisons, so they are an execution dimension rather than a
            # reason two results cannot be compared; the workload definition is
            # what has to match for a comparison to be controlled.
            "transport": job.get("transport") or {"kind": "direct-loopback"},
            "comparability": self.comparison_context(job, tokenizer),
            "publication": self.publication_requirements(job_id),
        }

    def comparison_context(self, job: dict, tokenizer: str | None) -> dict:
        """What a reader needs to know before putting two of these side by side.

        Three groups, because they answer different questions. The *workload
        definition* is what must match for a comparison to be controlled: the
        same profile bytes, request format, concurrency and token targets.
        *Model configuration* decides whether the same thing is being served.
        *Execution configuration* -- hardware, the CPU/GPU declaration, the
        serving runtime -- is usually the dimension under study, so a difference
        there invites a comparison rather than forbidding one.

        Nothing here grants or withholds permission to compare. It states what
        differs so a reader can judge, and marks unknowns as unknown rather than
        as either compatible or disqualifying.
        """
        from . import cli as omobench_cli

        profile_id = job["profile"]
        spec = omobench_cli.PROFILE_SPECS.get(profile_id, {})
        profile_path = self.root / "benchmarks" / "profiles" / f"{profile_id}.yaml"
        try:
            profile_sha256 = omobench_cli.sha256_file(profile_path)
        except OSError:
            profile_sha256 = None

        def declared(value):
            return value if value else "unknown"

        workload = {
            "profile_id": profile_id,
            "profile_sha256": profile_sha256 or "unknown",
            "request_format": "streaming OpenAI-compatible /v1/chat/completions",
            "concurrency": spec.get("profile"),
            "input_tokens": spec.get("input_tokens"),
            "requested_output_tokens": spec.get("output_tokens"),
            "phase_contract": omobench_cli.profile_phase_contract(self.root, profile_id),
        }
        model = {
            "server_model_identifier": job["model"],
            "canonical_model_id": declared(job.get("canonical_model")),
            "model_revision": declared(job.get("model_revision")),
            "quantization": declared(job.get("quantization")),
            "tokenizer_source": tokenizer or job["model"],
            "tokenizer_source_explicit": bool(tokenizer),
        }
        execution = {
            "execution_device_declared": job["execution_device"],
            "serving_runtime": declared(job.get("framework")),
            "serving_runtime_version": declared(job.get("framework_version")),
            "runtime_arguments": job.get("runtime_args") or [],
            "runner_host": "this machine; see the environment snapshot in the evidence bundle",
        }
        if job.get("transport"):
            execution.update(transport=job["transport"]["kind"], runner_host="WSL2",
                             serving_host="Windows loopback", serving_hardware="unknown",
                             transport_overhead="included, not independently calibrated")
        unknowns = sorted(
            key for group in (model, execution)
            for key, value in group.items() if value == "unknown"
        )
        return {
            "workload_definition": workload,
            "model_configuration": model,
            "execution_configuration": execution,
            "controlled_comparison_requires": [
                "the same workload_definition (profile id and bytes, request format, "
                "concurrency and token targets)",
                "the same model_configuration (identity, revision, quantization and tokenizer source)",
            ],
            "intended_comparison_dimensions": [
                "execution_configuration: hardware, the CPU/GPU declaration and the serving "
                "runtime and its settings. Comparing CPU against GPU, or one machine against "
                "another, on an otherwise identical workload is a controlled comparison, not a "
                "mismatch.",
            ],
            "exploratory_when": [
                "the model, its quantization or its tokenizer source differs: still informative, "
                "but it is a comparison between different things, not a ranking of one thing",
                "the profile or its bytes differ: a different workload definition entirely",
            ],
            "unknown_fields": unknowns,
            "comparability_established": not unknowns,
            "notes": [
                "Token counts are produced by the tokenizer named above. Two results that report "
                "the same token count under different tokenizers have not necessarily processed "
                "the same text or done the same amount of work.",
                "The execution device is the contributor's declaration about their own server. "
                "OmoBench does not detect or verify it, and OmoBench's own Python dependencies "
                "say nothing about which device that server used.",
                "Unknown fields mean comparability is not fully established for those fields. "
                "They neither certify a comparison nor forbid one.",
                "Comparison context is not publication eligibility. A result can be perfectly "
                "comparable and still ineligible for the registry, and the registry's schema, "
                "catalog, provenance and acceptance checks are unchanged by anything here.",
            ],
        }

    def publication_requirements(self, job_id: str, data: dict | None = None) -> dict:
        """Everything the public registry needs that this run does not have yet.

        A readable list, computed before packaging is attempted, so the whole
        set of missing requirements is visible at once instead of one native
        form popup at a time. It never relaxes anything: packaging and the
        registry validator remain the authority, and this only reports.
        """
        with self.lock:
            job = dict(self._get(job_id))
        values = data or {}
        catalog = self.catalog_models()
        model = (values.get("model") if isinstance(values.get("model"), str) else None) or job["model"]
        revision = values.get("model_revision") if isinstance(values.get("model_revision"), str) else job.get("model_revision")
        framework = values.get("framework") if isinstance(values.get("framework"), str) else job.get("framework")
        version = values.get("framework_version") if isinstance(values.get("framework_version"), str) else job.get("framework_version")
        submitter = values.get("submitter") if isinstance(values.get("submitter"), str) else None
        mutable = {"main", "master", "latest", "head"}

        items = [{
            "field": None,
            "label": "Benchmark completed with measurements",
            "ok": bool(job.get("summary")),
            "detail": "This run produced measurements." if job.get("summary")
                      else "Only a completed run can be packaged.",
        }, {
            "field": "model",
            "label": "Canonical model ID is in the OmoBench catalog",
            "ok": model in catalog,
            "detail": (f"{model} is catalogued." if model in catalog else
                       f"{model} is not currently eligible for the community registry: the catalog "
                       f"({', '.join(catalog)}) is the registry's source of truth, and a model is added "
                       "to it by a separate, reviewed change. You can still view and export this run's "
                       "local report. Never substitute a different, catalogued model for the one you ran."),
        }, {
            "field": "model_revision",
            "label": "Immutable revision of the actual weights source",
            "ok": bool(revision) and revision.strip().lower() not in mutable,
            "detail": ("Recorded." if revision and revision.strip().lower() not in mutable else
                       "A commit SHA or other immutable revision of the repository the weights really "
                       "came from. 'main', 'master' and 'latest' move, so they identify nothing. This is "
                       "not the file's SHA-256 checksum and not the OmoBench source commit. When the "
                       "weights come from a quantization repository, record that repository as the "
                       "weights source and pin its own revision."),
        }, {
            "field": "framework_version",
            "label": "Exact serving runtime version",
            "ok": bool(framework) and bool(version),
            "detail": ("Recorded." if framework and version else
                       "The inference runtime and its exact version, for example llama.cpp build b10621. "
                       "An application version (the LM Studio release, say) is a different thing from the "
                       "llama.cpp build it embeds; record the runtime build, and do not guess one."),
        }, {
            "field": "submitter",
            # Not yet typed is not the same as satisfied: with no value to look
            # at, the honest answer is that this has not been checked.
            "label": "Submitter identity",
            "ok": None if submitter is None else bool(submitter.strip()),
            "detail": ("Your GitHub username or organization, recorded in the bundle."
                       if submitter is None or submitter.strip()
                       else "Enter the GitHub username or organization to record in the bundle."),
        }, {
            "field": None,
            "label": "Profile acceptance",
            "ok": (None if not job.get("validation") else
                   not any("acceptance:" in message for message in job["validation"].get("errors", []))),
            "detail": ("Checked when you package this run."
                       if not job.get("validation") else
                       " ".join(message for message in job["validation"].get("errors", []) if "acceptance:" in message)
                       or "Meets this profile's minimum successful requests and maximum error rate."),
        }]
        if job.get("transport"):
            # Previously this was the end of the road. It is now a requirement
            # like the others: the schema can describe a Windows server and a
            # WSL runner, and what it needs is the serving machine's own facts,
            # which nothing visible from WSL can supply.
            serving = (values.get("serving") if isinstance(values.get("serving"), dict) else None)
            declared = bool(serving and (serving.get("host") or {}).get("cpu"))
            items.insert(0, {
                "field": "serving",
                "label": "Serving environment is described separately from the runner",
                "ok": None if serving is None else declared,
                "detail": (
                    "This run's model server and its benchmark runner were different "
                    "environments. Describe the machine that served the model -- its operating "
                    "system, processor, memory, accelerator and serving runtime. What WSL can "
                    "see of the Windows host is not evidence about what served the model, so "
                    "none of it is filled in for you."
                    if not declared else
                    "Recorded, and marked as declared by you rather than detected here."),
            })
            items.insert(1, {
                "field": None,
                "label": "Bridge latency is inside the measurements",
                "ok": True,
                "detail": ("Every number this run reports includes the loopback bridge's own "
                           "latency. It is recorded as included and is never subtracted, so a "
                           "bridged result is comparable with other bridged results and is "
                           "labelled wherever it is shown."),
            })
        return {
            "model": model,
            "catalog": catalog,
            "catalogued": model in catalog,
            "items": items,
            "ready": all(item["ok"] is not False for item in items),
            "local_export_available": bool(job.get("summary")),
        }

    def tokenizer_check(self, data: dict) -> dict:
        """Inspect a tokenizer source offline, before anything is downloaded."""
        return cli.tokenizer_source_report(
            text_field(data, "tokenizer", optional=True),
            served_model=text_field(data, "model", optional=True) or None,
        )

    def tokenizer_preflight(self, data: dict) -> dict:
        """Actually load the tokenizer, in the interpreter the benchmark uses.

        The offline check above answers whether the files are where the loader
        will look. This answers whether the loader can build a tokenizer from
        them, which is the question a run dies on. It is a separate action
        because it can take time and, above the lowest level, can reach the
        network; the level is chosen here and reported back.
        """
        from . import tokenizer_preflight as preflight

        network = data.get("network", preflight.NETWORK_NONE)
        if network not in preflight.NETWORK_LEVELS:
            raise UIError("Choose a supported tokenizer check level.")
        source = text_field(data, "tokenizer", optional=True) or text_field(
            data, "model", optional=True)
        if not source:
            raise UIError("Enter a tokenizer source, or the identifier your server answers to.")
        with self.lock:
            if self.busy or self.closing:
                raise UIError("Wait for the active operation before checking the tokenizer.", 409)
            self.busy = True
        try:
            report = preflight.preflight(source, network=network, timeout=300.0,
                                         cancel=lambda: self.closing)
        finally:
            with self.lock:
                self.busy = False
        return report.as_dict()

    def tokenizer_download_plan(self, data: dict) -> dict:
        """What an authorized tokenizer-only download would fetch, and where to.

        Shown before anything is fetched. A download is a deliberate act with a
        visible source and a visible destination, not something a check does on
        the way past.
        """
        from . import tokenizer_preflight as preflight

        source = text_field(data, "tokenizer")
        try:
            return preflight.download_plan(source)
        except ValueError as error:
            raise UIError(str(error)) from error

    def publication_outlook(self, data: dict) -> dict:
        """What a public submission will need, said before the run rather than after.

        Running and publishing are two different things with two different
        requirement lists, and the second one used to appear only once the
        benchmark had finished -- which is how somebody spends an hour
        measuring a model and then learns there is no route for it.

        This blocks nothing. A local-only run needs none of it, and that is the
        first thing it says.
        """
        from .model_identity import suggest_catalog_models

        served = text_field(data, "model", optional=True) or ""
        tokenizer = text_field(data, "tokenizer", optional=True)
        bridged = bool(data.get("windows_bridge"))
        if type(data.get("windows_bridge", False)) is not bool:
            raise UIError("Windows bridge selection must be a boolean.")
        catalog = self.catalog_models()
        suggestion = suggest_catalog_models(served, catalog)

        items = [{
            "id": "local",
            "label": "Saving a local report needs none of this",
            "state": "ok",
            "detail": ("Any model, any hardware, no sign-in. It records the measurements this "
                       "machine made along with the workload, model and execution configuration "
                       "that produced them. Everything below is about the public registry only."),
        }, {
            "id": "model",
            "label": "A catalogued model, or a proposal for one",
            "state": ("ok" if served in catalog else
                      "attention" if suggestion["candidates"] else "action"),
            "detail": (f"{served} is catalogued." if served in catalog else
                       (f"{served!r} is the identifier your server answers to, which is a serving "
                        "alias rather than a catalog identity. " + suggestion["message"])
                       if suggestion["candidates"] else
                       (f"No catalog entry matches {served!r}. You can still run this benchmark, "
                        "and submit it with a proposal asking for the model to be reviewed for "
                        "the catalog. A proposal needs its family, parameter class, publisher "
                        "page and licence." if served else
                        "Enter the identifier your server answers to.")),
        }, {
            "id": "revision",
            "label": "An immutable revision of the weights you actually ran",
            "state": "action",
            "detail": ("A commit of the repository the weights really came from. 'main' and "
                       "'latest' move, so they identify nothing. Weights with no public immutable "
                       "revision can be identified by their bytes instead: `omobench weights` "
                       "hashes the files you name. Find this before you start, not afterwards."),
        }, {
            "id": "runtime",
            "label": "The serving runtime's own build",
            "state": "action",
            "detail": ("The inference engine's version, such as llama.cpp b10621. An application "
                       "release -- the LM Studio version, say -- is a different thing, and both "
                       "are recorded separately."),
        }, {
            "id": "tokenizer",
            "label": "A tokenizer the runner can load",
            "state": "attention" if not tokenizer else "ok",
            "detail": ("The workload's prompts are built and counted with this, and the runner "
                       "resolves it before sending a single request -- so a tokenizer it cannot "
                       "load ends the run with nothing measured. A GGUF repository is the usual "
                       "case: it serves perfectly well and contains no Transformers tokenizer. "
                       "Use 'Load tokenizer now' before starting."
                       if not tokenizer else
                       f"{tokenizer} will be used. 'Load tokenizer now' confirms the runner can "
                       "actually load it.")},
        ]
        if bridged:
            items.append({
                "id": "serving",
                "label": "A description of the machine that served the model",
                "state": "action",
                "detail": ("Your server runs on Windows and this runner is in WSL, so they are "
                           "two environments and the result records them separately. You will be "
                           "asked for the serving machine's operating system, processor, memory, "
                           "accelerators and runtime: nothing WSL can see of the Windows host "
                           "counts as evidence about it. The companion can read them for you. "
                           "The bridge's own latency is inside every measurement and is never "
                           "subtracted."),
            })
        return {
            "served_model_id": served, "catalog": catalog, "catalogued": served in catalog,
            "suggestion": suggestion, "items": items,
            "summary": ("Nothing here stops the benchmark. It is what a public submission will "
                        "additionally need, so you can gather it now rather than after the run."),
        }

    def serving_probe(self, data: dict) -> dict:
        """Read the Windows serving machine's hardware, on that machine.

        This exists for one arrangement: the model server on Windows, the
        benchmark runner in WSL2. From in here the host's processor, memory and
        accelerators are invisible or wrong, and publishing what WSL can see as
        though it described the server is the mistake the whole schema exists
        to prevent. The companion reads them where they live.

        Finding a card is not evidence that inference used it, so nothing here
        claims a role for one; and video memory is deliberately absent, because
        Windows reports it capped and wrong for exactly the cards this matters
        for.
        """
        from .bridge import helper_path
        from .serving_metadata import HARDWARE_PROBE_KIND

        if type(data.get("windows_bridge", False)) is not bool:
            raise UIError("Windows bridge selection must be a boolean.")
        try:
            helper = helper_path()
        except ValueError as error:
            raise UIError(str(error)) from error
        with self.lock:
            if self.busy or self.closing:
                raise UIError("Wait for the active operation before reading the serving hardware.", 409)
            self.busy = True
        try:
            completed = subprocess.run([helper, "--hardware-probe-v1"],
                                       capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UIError("The companion did not answer. Open this panel through the updated "
                          "Windows companion and try again.") from error
        finally:
            with self.lock:
                self.busy = False
        if completed.returncode != 0:
            raise UIError("The companion could not read this machine's hardware. Enter the "
                          "serving machine's details yourself instead.")
        try:
            document = json.loads(completed.stdout)
        except ValueError as error:
            raise UIError("The companion returned something this panel cannot read.") from error
        if not isinstance(document, dict) or document.get("kind") != HARDWARE_PROBE_KIND:
            raise UIError("The companion returned a document this panel does not recognise.")
        with self.lock:
            self.last_serving_probe = {key: document[key]
                                       for key in ("kind", "os", "cpu", "ram_gb", "accelerators")
                                       if key in document}
        document["note"] = (
            "Read on the Windows machine itself, so these are detected facts. Video memory is "
            "not among them: Windows reports it capped and wrong for larger cards, so enter it "
            "from the serving runtime's own hardware panel. Finding a card is not evidence that "
            "inference used it; say which one served.")
        return document

    def model_suggestions(self, data: dict) -> dict:
        """Catalog entries a served alias may mean. It never picks one."""
        from .model_identity import suggest_catalog_models

        catalog = self.catalog_models()
        suggestion = suggest_catalog_models(
            text_field(data, "served_model_id", optional=True) or "", catalog)
        suggestion["catalog"] = catalog
        suggestion["proposal_available"] = not suggestion["candidates"]
        return suggestion

    def weight_identity(self, data: dict) -> dict:
        """Hash the weight files the contributor named, after listing them.

        Listing reads no file contents. Hashing happens only once a person has
        named the files, because a filename is not an identity and OmoBench
        will not decide which bytes somebody ran.
        """
        from .model_identity import ModelIdentityError, manifest_revision, weight_files_in, weight_manifest

        directory = Path(text_field(data, "directory")).expanduser()
        names = data.get("files")
        try:
            if not names:
                return {"directory": str(directory), "hashed": False,
                        "candidates": [{"name": path.name, "bytes": path.stat().st_size}
                                       for path in weight_files_in(directory)],
                        "message": ("Nothing has been read yet. Select every file this model is "
                                    "made of; a sharded model needs all of its shards, because "
                                    "the identity covers the whole set.")}
            if not isinstance(names, list) or len(names) > 128 or any(
                    not isinstance(name, str) or not name or "/" in name or "\\" in name
                    for name in names):
                raise UIError("Select weight files by name, inside the folder you chose.")
            manifest = weight_manifest([directory / name for name in names], root=directory)
        except ModelIdentityError as error:
            raise UIError(str(error)) from error
        return {"directory": str(directory), "hashed": True, "manifest": manifest,
                "revision": manifest_revision(manifest),
                "message": (f"Hashed {len(manifest['files'])} file(s). Record this revision; it "
                            "identifies these exact bytes, not their names.")}

    def listing(self) -> list[dict]:
        with self.lock:
            return [dict(j) for j in sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)]

    def _begin_action(self, job_id: str, status: str) -> dict:
        with self.lock:
            job = self._get(job_id)
            if self.closing or self.busy or job["status"] != "completed":
                raise UIError("Wait for a successful run before preparing a submission.", 409)
            submission = job.get("submission") or {}
            if (submission.get("commit_sha") or submission.get("started")) and status == "packaging":
                raise UIError("Submission has started for this bundle. Retry submission or start a new run to change its evidence.")
            self.busy = True
            job["status"] = status
            self._save(job)
            return job

    def serving_declaration(self, job: dict, data: dict) -> dict | None:
        """Build the provenance declaration the packager reads, from the form.

        Returns None when there is nothing to declare and the run was a plain
        single-host one, so the existing schema 1.0 output stays byte for byte
        what it was. A bridged run has no such option: it is only describable
        with this, and the packager says so if it is missing.
        """
        declaration: dict = {}
        for name, checker in (("serving", dict), ("weights", dict), ("tokenizer", dict),
                              ("proposal", dict)):
            value = data.get(name)
            if value is None:
                continue
            if not isinstance(value, checker):
                raise UIError(f"{name} must be an object.")
            declaration[name] = value
        served = text_field(data, "served_model_id", optional=True)
        if served:
            declaration["served_model_id"] = served
        if data.get("probe"):
            # The page asks for the probe by name; it never supplies one. What
            # is recorded as detected is what this panel read for itself, not a
            # document that made a round trip through a browser.
            probe = self.last_serving_probe
            if not probe:
                raise UIError("No serving hardware has been read on this machine yet. Use "
                              "'Read the serving hardware' first, or describe it yourself.")
            declaration.setdefault("serving", {})["probe"] = dict(probe)
        status = data.get("catalog_status")
        if status is not None:
            if status not in {"catalogued", "proposed"}:
                raise UIError("Catalog status must be 'catalogued' or 'proposed'.")
            declaration["catalog_status"] = status
        if not declaration:
            # Nothing was declared at all. A direct run keeps its existing
            # schema 1.0 output; a bridged one reaches the packager without a
            # declaration, which refuses it and says what it needs and why.
            return None
        if job.get("transport"):
            declaration.setdefault("serving", {})
            declaration["serving"].setdefault("relationship", "windows-host-wsl-runner")
        else:
            declaration.setdefault("serving", {})
            declaration["serving"].setdefault("relationship", "same-host")
        if job.get("tokenizer") and "tokenizer" not in declaration:
            declaration["tokenizer"] = {
                "source": job["tokenizer"], "kind": "huggingface", "loaded": "not-checked"}
        return declaration

    def package(self, job_id: str, data: dict) -> dict:
        from .ui_submission import validate_bundle
        # Reject malformed arguments before claiming the action lock.
        names = ["model", "model_revision", "framework", "framework_version", "submitter"]
        values = {name: text_field(data, name) for name in names}
        if values["framework"] not in cli.RUNTIME_FRAMEWORKS:
            raise UIError("Choose a supported framework.")
        optional = {
            name: text_field(data, name, optional=True)
            for name in ["quantization", "server_description", "cpu", "model_source"]
        }
        runtime_args = data.get("runtime_args", [])
        if not isinstance(runtime_args, list) or len(runtime_args) > 100:
            raise UIError("Enter one runtime argument per line.")
        for value in runtime_args:
            text_field({"argument": value}, "argument")
        memory = data.get("gpu_memory_gb")
        if memory is not None:
            if isinstance(memory, bool) or not isinstance(memory, (int, float)) or not 0 < memory < 100_000:
                raise UIError("GPU memory must be a positive number.")
        job = self._begin_action(job_id, "packaging")
        try:
            if memory is not None and job["execution_device"] == "cpu":
                raise UIError("GPU memory does not apply to a CPU run; no GPU performed inference.")
            job["validation"] = None
            self._save(job)
            run = self.run_directory(job_id)
            arguments = [
                "package",
                f"--run-dir={run}",
                f"--profile={job['profile']}",
                f"--execution-device={job['execution_device']}",
            ]
            declaration = self.serving_declaration(job, data)
            if declaration is not None:
                # Kept beside the evidence so the same declaration can be read
                # back, reviewed, and reused if this run is packaged again.
                path = run / "provenance.json"
                path.write_text(json.dumps(declaration, indent=2) + "\n", encoding="utf-8")
                arguments.append(f"--provenance={path}")
                # These describe the machine the runner's snapshot came from.
                # Under a declaration the serving machine is described there
                # instead, so accepting both would leave two answers to one
                # question; refuse rather than quietly drop one.
                if optional.get("cpu") or memory is not None:
                    raise UIError(
                        "CPU description and GPU memory under 'Hardware details' describe the "
                        "machine this panel detected. You are describing the serving machine "
                        "above, so put its processor and accelerator memory there and leave "
                        "these blank.")
            arguments.extend(f"--{key.replace('_', '-')}={value}" for key, value in {**values, **optional}.items() if value)
            arguments.extend(f"--runtime-arg={value}" for value in runtime_args)
            if memory is not None:
                arguments.append(f"--gpu-memory-gb={memory}")
            if (run / "result.json").exists():
                arguments.append("--force")
            completed = subprocess.run(self._cli_command(arguments), cwd=self.root, env=self.environment,
                                       capture_output=True, text=True, timeout=60)
            if completed.returncode:
                raise UIError(safe_log(completed.stderr or completed.stdout or "Packaging failed."))
            errors = validate_bundle(self.root, run)
            job["validation"] = {"valid": not errors, "errors": errors}
            job["submission"] = None
            job["error"] = None
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UIError("Packaging did not complete. Check the CLI installation.") from error
        finally:
            with self.lock:
                job["status"] = "completed"
                self.busy = False
                self._save(job)
        return self.detail(job_id)

    def server_check(self, data: dict) -> dict:
        """Staged check against a server the contributor runs themselves."""
        with self.lock:
            if self.busy or self.closing:
                raise UIError("Wait for the active operation before checking the server.", 409)
            self.busy = True
        try:
            setup = cli.guidellm_runner_preflight()
            native_windows = sys.platform == "win32"
            with self._bridge_request(data) as (checked, headers):
                return server_check(
                    checked,
                    can_execute=not native_windows and setup.ok,
                    runner_message=("Start this panel from WSL2, Linux or macOS to execute benchmarks. "
                                    "The panel itself works here for setup and review."
                                    if native_windows else setup.message),
                    # Only worth importing when metadata already agrees, and never
                    # on a platform that cannot run a benchmark at all.
                    **({"_headers": headers} if headers else {}),
                    runner_health=(cli.runner_health_check(timeout=120, cancel=lambda: self.closing)
                                   if setup.ok and not native_windows else None),
                )
        finally:
            with self.lock:
                self.busy = False

    def github_action(self, action: str) -> dict:
        with self.lock:
            if self.closing or self.busy:
                raise UIError("Wait for the current operation before changing GitHub sign-in.", 409)
            self.community_reviews.clear()
            if action == "start":
                return self.github_auth.start()
            return self.github_auth.sign_out()

    def prepare(self, job_id: str, data: dict | None = None) -> dict:
        from .ui_submission import prepare_submission
        mode = (data or {}).get("mode", "origin")
        if mode not in {"origin", "community"}:
            raise UIError("Choose a supported submission route.")
        job = self._begin_action(job_id, "preparing")
        try:
            if mode == "community":
                from .community_submission import prepare_community
                preview, review = prepare_community(self.root, self.run_directory(job_id), self.github_auth,
                                                    job.get("submission") or {})
                with self.lock:
                    # One current review per job; never persist OAuth session data.
                    self.community_reviews.pop(job_id, None)
                    if review:
                        review.update(review_id=secrets.token_urlsafe(24), expires=time.monotonic() + 900)
                        self.community_reviews[job_id] = review
                        preview["review_id"] = review["review_id"]
                return preview
            if (job.get("submission") or {}).get("mode") == "community":
                raise UIError("This run has a community submission. Select that route to retry.")
            return prepare_submission(self.root, self.run_directory(job_id))
        finally:
            with self.lock:
                self.busy = False
                job["status"] = "completed"
                self._save(job)

    def bundle(self, job_id: str) -> bytes:
        from .ui_submission import bundle_archive
        job = self._begin_action(job_id, "preparing")
        try:
            return bundle_archive(self.root, self.run_directory(job_id))
        finally:
            with self.lock:
                self.busy = False
                job["status"] = "completed"
                self._save(job)

    def submit(self, job_id: str, data: dict) -> dict:
        from .ui_submission import submit_pull_request
        if data.get("confirmed") is not True:
            raise UIError("Review the complete bundle and confirm submission first.")
        repository = text_field(data, "repository")
        bundle_digest = text_field(data, "bundle_digest")
        mode = data.get("mode", "origin")
        if mode not in {"origin", "community"}:
            raise UIError("Choose a supported submission route.")
        job = self._begin_action(job_id, "submitting")
        try:
            state = job.get("submission") or {}
            job["submission"] = state
            if mode == "community":
                from .community_submission import submit_community
                review = self.community_reviews.get(job_id)
                if (not review or review["review_id"] != data.get("review_id") or time.monotonic() >= review["expires"]
                        or review["repository"] != repository or review["bundle_digest"] != bundle_digest):
                    raise UIError("The submission review is missing, expired or changed. Review the files again.")
                submit_community(self.root, self.run_directory(job_id), self.github_auth, review,
                                 state, lambda: self._save(job))
            else:
                if state.get("mode") == "community":
                    raise UIError("This run has a community submission. Select that route to retry.")
                submit_pull_request(self.root, self.run_directory(job_id), repository, bundle_digest,
                                    state, lambda: self._save(job))
        finally:
            with self.lock:
                self.busy = False
                job["status"] = "completed"
                self._save(job)
        return self.detail(job_id)


class UIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, root: Path):
        super().__init__(address, UIHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.token = secrets.token_urlsafe(32)
        self.controller = Controller(root)
        from .ui_managed import ManagedController

        self.managed = ManagedController(root, self.controller)


class UIHandler(BaseHTTPRequestHandler):
    server: UIServer

    def log_message(self, format, *args):
        pass  # Avoid printing request paths, credentials, or browser tokens.

    def _send(self, status: int, content: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, value, status=200):
        self._send(status, json.dumps(value, allow_nan=False).encode())

    def _authorize(self) -> None:
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            raise UIError("Use the localhost URL printed by omobench ui.", 403)
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            raise UIError("Cross-origin requests are not allowed.", 403)
        if self.path.startswith("/api/"):
            token = self.headers.get("X-OmoBench-Token", "")
            if not hmac.compare_digest(token.encode(), self.server.token.encode()):
                raise UIError("Open the new connection link printed in your terminal.", 401)

    def do_GET(self):
        try:
            self._authorize()
            # The guided experience is the default entry point; the original
            # "connect to a server you already run yourself" panel stays fully
            # available at /advanced (and every existing CLI command keeps working
            # unchanged). /guided is kept as an alias to the same page so existing
            # bookmarks/docs links and the previous default keep working.
            static = {"/": ("guided.html", "text/html; charset=utf-8"),
                      "/guided": ("guided.html", "text/html; charset=utf-8"),
                      "/guided.js": ("guided.js", "text/javascript; charset=utf-8"),
                      "/community.js": ("community.js", "text/javascript; charset=utf-8"),
                      "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
                      "/guided.css": ("guided.css", "text/css; charset=utf-8"),
                      "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
                      "/favicon.svg": ("favicon.svg", "image/svg+xml"),
                      "/advanced": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if self.path in static:
                name, mime = static[self.path]
                return self._send(200, files("omobench").joinpath("web", name).read_bytes(), mime)
            controller = self.server.controller
            if self.path == "/api/github/auth":
                return self._json(controller.github_auth.snapshot())
            if self.path == "/api/setup":
                return self._json(controller.setup())
            if self.path == "/api/runs":
                return self._json(controller.listing())
            match = re.fullmatch(r"/api/runs/(job-[a-f0-9]{16})(/bundle)?", self.path)
            if match:
                if match[2]:
                    return self._send(200, controller.bundle(match[1]), "application/zip")
                return self._json(controller.detail(match[1]))
            match = re.fullmatch(r"/api/runs/(job-[a-f0-9]{16})/telemetry", self.path)
            if match:
                return self._json(controller.telemetry(match[1]))
            match = re.fullmatch(r"/api/runs/(job-[a-f0-9]{16})/local-report", self.path)
            if match:
                return self._json(controller.local_report(match[1]))
            # Read-only, from what the run already recorded. The POST form of
            # this path exists only to check values typed but not yet saved.
            match = re.fullmatch(r"/api/runs/(job-[a-f0-9]{16})/requirements", self.path)
            if match:
                return self._json(controller.publication_requirements(match[1]))
            managed = self.server.managed
            if self.path == "/api/managed/recipes":
                return self._json({"recipes": managed.recipes(), "default_storage_dir": managed.default_storage_dir()})
            if self.path == "/api/managed/setup-jobs":
                return self._json(managed.setup_job_list())
            match = re.fullmatch(r"/api/managed/setup-jobs/(mjob-[a-f0-9]{16})", self.path)
            if match:
                return self._json(managed.setup_job_detail(match[1]))
            if self.path == "/api/managed/runs":
                return self._json(managed.run_job_list())
            match = re.fullmatch(r"/api/managed/runs/(mrun-[a-f0-9]{16}|saved-job-[a-f0-9]{16})", self.path)
            if match:
                return self._json(managed.run_job_detail(match[1]))
            raise UIError("Not found.", 404)
        except UIError as error:
            self._json({"error": str(error)}, error.status)
        except Exception:
            self._json({"error": "Could not read local run data. Check that the files and UI dependencies are available."}, 500)

    def do_POST(self):
        try:
            self._authorize()
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise UIError("Send JSON data.", 415)
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 32_768:
                raise UIError("Invalid request size.", 413)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise UIError("Request must be a JSON object.")
            controller = self.server.controller
            auth = re.fullmatch(r"/api/github/auth/(start|cancel|sign-out)", self.path)
            if auth:
                return self._json(controller.github_action(auth[1]))
            if self.path == "/api/connection":
                return self._json(controller.connection(data))
            if self.path == "/api/tokenizer-check":
                return self._json(controller.tokenizer_check(data))
            if self.path == "/api/tokenizer-preflight":
                return self._json(controller.tokenizer_preflight(data))
            if self.path == "/api/tokenizer-download-plan":
                return self._json(controller.tokenizer_download_plan(data))
            if self.path == "/api/model-suggestions":
                return self._json(controller.model_suggestions(data))
            if self.path == "/api/serving-probe":
                return self._json(controller.serving_probe(data))
            if self.path == "/api/publication-outlook":
                return self._json(controller.publication_outlook(data))
            if self.path == "/api/weight-identity":
                return self._json(controller.weight_identity(data))
            if self.path == "/api/server-check":
                return self._json(controller.server_check(data))
            if self.path == "/api/runs":
                return self._json(controller.start(data), 202)
            match = re.fullmatch(r"/api/runs/(job-[a-f0-9]{16})/requirements", self.path)
            if match:
                return self._json(controller.publication_requirements(match[1], data))
            match = re.fullmatch(r"/api/runs/(job-[a-f0-9]{16})/(cancel|package|prepare|submit)", self.path)
            if match:
                job_id, action = match.groups()
                if action == "cancel":
                    return self._json(getattr(controller, action)(job_id))
                return self._json(getattr(controller, action)(job_id, data))
            managed = self.server.managed
            if self.path == "/api/managed/review":
                return self._json(managed.review(data))
            if self.path == "/api/managed/setup-jobs":
                return self._json(managed.prepare(data), 202)
            match = re.fullmatch(r"/api/managed/setup-jobs/(mjob-[a-f0-9]{16})/cancel", self.path)
            if match:
                return self._json(managed.cancel_setup(match[1]))
            if self.path == "/api/managed/runs":
                mode = data.get("mode")
                if mode not in {"smoke", "execute"}:
                    raise UIError("Choose managed run mode 'smoke' or 'execute'.")
                return self._json(managed.start_run(data, mode=mode), 202)
            match = re.fullmatch(r"/api/managed/runs/(mrun-[a-f0-9]{16})/cancel", self.path)
            if match:
                return self._json(managed.cancel_run(match[1]))
            raise UIError("Not found.", 404)
        except UIError as error:
            self._json({"error": str(error)}, error.status)
        except (ValueError, TypeError):
            self._json({"error": "Invalid request data."}, 400)
        except Exception:
            self._json({"error": "The operation failed. Evidence has been kept locally; check the run log or retry."}, 500)


# Version of the --status-file document below. Bump only for an incompatible
# change; a reader must refuse a schema it does not know.
STATUS_FILE_SCHEMA = 1


def write_status_file(path: Path, payload: dict) -> None:
    """Publish one startup status document for a non-interactive launcher.

    This is the panel's private channel to whatever started it (the Windows
    companion, a future service wrapper): a reader gets the connection details
    as data instead of scraping the explanatory text on stdout.

    The ready document contains the connection token, so it is created with a
    0600 mode and replaced atomically, and it refuses to follow a symlink at
    the path it is told to write.

    The mode is not the whole story, and callers should not treat it as such.
    A Windows launcher points this at a /mnt/... path, where the default DrvFs
    mount discards POSIX modes outright (the file reads back as 0777); there,
    privacy comes from the directory's own access list, which the Windows
    companion sets explicitly. Point this at a directory only this user can
    read -- never inside the checkout, a log directory, or an evidence bundle.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    # O_NOFOLLOW: if something has planted a symlink at the temporary path,
    # fail rather than write a token through it. O_NOFOLLOW is POSIX-only;
    # Windows-hosted panels are not a supported execution path.
    flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"schema": STATUS_FILE_SCHEMA, **payload}, stream)
            stream.write("\n")
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)


def _publish(status_path: Path | None, payload: dict) -> None:
    """Best-effort status publication: never let it break the panel itself."""
    if status_path is None:
        return
    try:
        write_status_file(status_path, payload)
    except OSError as error:
        print(f"Could not write the startup status file: {error}", file=sys.stderr)


def serve_ui(port: int = 8765, *, open_browser: bool = True, status_file: str | None = None) -> int:
    status_path = Path(status_file) if status_file else None
    root = cli.find_repository_root()
    if root is None:
        message = "Run omobench ui from an OmoBench checkout."
        print(message, file=sys.stderr)
        _publish(status_path, {"state": "failed", "reason": "not_a_checkout", "message": message})
        return 2
    if not 1 <= port <= 65535:
        message = "Choose a port between 1 and 65535."
        print(message, file=sys.stderr)
        _publish(status_path, {"state": "failed", "reason": "invalid_port", "message": message})
        return 2
    try:
        with panel_lock(root):
            return _serve_ui(root, port, open_browser, status_path)
    except (OSError, UIError) as error:
        print(f"Could not start the local panel: {error}", file=sys.stderr)
        _publish(
            status_path,
            {
                "state": "failed",
                "reason": getattr(error, "reason", None) or "startup_failed",
                "message": str(error),
            },
        )
        return 2


def _serve_ui(root: Path, port: int, open_browser: bool, status_path: Path | None = None) -> int:
    try:
        server = UIServer(("127.0.0.1", port), root)
    except OSError as error:
        print(f"Could not start the local panel: {error}", file=sys.stderr)
        _publish(
            status_path,
            {"state": "failed", "reason": "port_unavailable", "port": port, "message": str(error)},
        )
        return 2
    except UIError as error:
        print(f"Could not start the local panel: {error}", file=sys.stderr)
        _publish(
            status_path,
            {"state": "failed", "reason": error.reason or "startup_failed", "message": str(error)},
        )
        return 2
    url = f"{server.origin}/#token={server.token}"
    print(f"OmoBench local control panel\n\n{url}\n\nKeep this terminal open. Ctrl+C stops the panel and its active benchmark.", flush=True)
    _publish(
        status_path,
        {
            "state": "ready",
            "url": url,
            "origin": server.origin,
            "port": server.server_port,
            "token": server.token,
            "pid": os.getpid(),
            "root": str(root),
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    previous = signal.getsignal(signal.SIGTERM)

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.managed.close()
        server.controller.close()
        server.server_close()
        signal.signal(signal.SIGTERM, previous)
        # The document carries the connection token: remove it as soon as the
        # panel it describes is gone, so a stale file can never hand a live
        # token to a later reader.
        if status_path is not None:
            with contextlib.suppress(OSError):
                status_path.unlink(missing_ok=True)
    return 0
