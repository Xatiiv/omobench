"""Loopback browser controller. All benchmark and package operations use the CLI."""

from __future__ import annotations

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
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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
            raise UIError("A control panel is already running for this checkout. Use its browser window or stop it first.") from error
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
        raise UIError("Use a localhost model server, such as http://127.0.0.1:8092. "
                      "Remote servers and credentials in URLs are not supported by this local panel.")
    return f"{url.scheme}://{url.netloc}"


def run_config(data: dict) -> dict:
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
    return {"endpoint": endpoint, "model": model, "profile": profile, "execution_device": device}


def safe_log(value: str) -> str:
    # The CLI redacts its completed log. Apply redaction during live display too.
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = re.sub(r"https?://[^\s\"'<>]+", "<ENDPOINT>", value)
    value = re.sub(r"(?im)(authorization\s*[:=]\s*)[^\r\n]+", r"\1<REDACTED>", value)
    return re.sub(r"(?i)(api[_-]?key|token)(\s*[:=]\s*)\S+", r"\1\2<REDACTED>", value)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UIError("The model server redirected the request. Enter its direct localhost address.")


def probe_connection(data: dict) -> dict:
    endpoint = endpoint_origin(text_field(data, "endpoint"))
    model = text_field(data, "model")
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply OK."}],
               "max_tokens": 1, "temperature": 0, "stream": True}
    request = Request(endpoint + "/v1/chat/completions", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
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
        raise UIError(f"The model server returned HTTP {error.code}. Check the model identifier and server settings.") from error
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise UIError("Could not read a streaming response. Check that the model is loaded and the server is running.") from error


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
        version = cli.parsed_guidellm_version(report.get("guidellm"))
        native_windows = sys.platform == "win32"
        catalog = []
        import yaml
        for path in sorted((self.root / "catalog" / "models").glob("*.yaml")):
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
                catalog.extend(entry["id"] for entry in document.get("models", [])
                               if isinstance(entry, dict) and entry.get("modality") == "text-generation" and isinstance(entry.get("id"), str))
            except (OSError, ValueError, AttributeError, yaml.YAMLError):
                continue
        return {"environment": report, "profiles": cli.PROFILE_SPECS,
                "frameworks": cli.RUNTIME_FRAMEWORKS, "catalog": sorted(set(catalog)),
                "can_execute": not native_windows and version == cli.GUIDELLM_VERSION,
                "runner_message": ("Start this panel from WSL2 or Linux to execute benchmarks." if native_windows else
                    f"GuideLLM {cli.GUIDELLM_VERSION} ready." if version == cli.GUIDELLM_VERSION else
                    f"Install the pinned runner in this environment: python -m pip install -e './cli[ui,runner]'"),
                "can_validate": importlib.util.find_spec("jsonschema") is not None}

    def _cli_command(self, arguments: list[str]) -> list[str]:
        return [sys.executable, "-u", "-m", "omobench.cli", *arguments]

    def connection(self, data: dict) -> dict:
        with self.lock:
            if self.busy or self.closing:
                raise UIError("Wait for the active operation before testing the connection.", 409)
            self.busy = True
        try:
            return probe_connection(data)
        finally:
            with self.lock:
                self.busy = False

    def start(self, data: dict) -> dict:
        config = run_config(data)
        if sys.platform == "win32":
            raise UIError("The current GuideLLM runner needs WSL2, Linux, or macOS. Start omobench ui there.")
        version = cli.parsed_guidellm_version(cli.executable_version("guidellm"))
        if version != cli.GUIDELLM_VERSION:
            raise UIError(f"GuideLLM {cli.GUIDELLM_VERSION} is required. Install './cli[ui,runner]' in this environment.")
        with self.lock:
            if self.busy or self.closing:
                raise UIError("Another operation is active. Wait or cancel the current benchmark.", 409)
            self.busy = True
            job_id = "job-" + secrets.token_hex(8)
            directory = self._directory(job_id)
            directory.mkdir()
            job = {"id": job_id, "created_at": datetime.now(UTC).isoformat(), "status": "running",
                   "model": config["model"], "profile": config["profile"], "execution_device": config["execution_device"],
                   "summary": None, "error": None, "validation": None, "submission": None}
            self.jobs[job_id] = job
            self._save(job)
            arguments = ["run", f"--endpoint={config['endpoint']}", f"--model={config['model']}",
                         f"--profile={config['profile']}", f"--output-dir={directory.relative_to(self.root).as_posix()}", "--execute"]
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

    def listing(self) -> list[dict]:
        with self.lock:
            return [dict(j) for j in sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)]

    def _begin_action(self, job_id: str, status: str) -> dict:
        with self.lock:
            job = self._get(job_id)
            if self.busy or job["status"] != "completed":
                raise UIError("Wait for a successful run before preparing a submission.", 409)
            submission = job.get("submission") or {}
            if submission.get("commit_sha") and status == "packaging":
                raise UIError("Submission has started for this bundle. Retry submission or start a new run to change its evidence.")
            self.busy = True
            job["status"] = status
            self._save(job)
            return job

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

    def prepare(self, job_id: str) -> dict:
        from .ui_submission import prepare_submission
        job = self._begin_action(job_id, "preparing")
        try:
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
        job = self._begin_action(job_id, "submitting")
        try:
            state = job.get("submission") or {}
            job["submission"] = state
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
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8"),
                      "/guided": ("guided.html", "text/html; charset=utf-8"),
                      "/guided.js": ("guided.js", "text/javascript; charset=utf-8"),
                      "/guided.css": ("guided.css", "text/css; charset=utf-8")}
            if self.path in static:
                name, mime = static[self.path]
                return self._send(200, files("omobench").joinpath("web", name).read_bytes(), mime)
            controller = self.server.controller
            if self.path == "/api/setup":
                return self._json(controller.setup())
            if self.path == "/api/runs":
                return self._json(controller.listing())
            match = re.fullmatch(r"/api/runs/(job-[a-f0-9]{16})(/bundle)?", self.path)
            if match:
                if match[2]:
                    return self._send(200, controller.bundle(match[1]), "application/zip")
                return self._json(controller.detail(match[1]))
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
            match = re.fullmatch(r"/api/managed/runs/(mrun-[a-f0-9]{16})", self.path)
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
            if self.path == "/api/connection":
                return self._json(controller.connection(data))
            if self.path == "/api/runs":
                return self._json(controller.start(data), 202)
            match = re.fullmatch(r"/api/runs/(job-[a-f0-9]{16})/(cancel|package|prepare|submit)", self.path)
            if match:
                job_id, action = match.groups()
                if action in {"cancel", "prepare"}:
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


def serve_ui(port: int = 8765, *, open_browser: bool = True) -> int:
    root = cli.find_repository_root()
    if root is None:
        print("Run omobench ui from an OmoBench checkout.", file=sys.stderr)
        return 2
    if not 1 <= port <= 65535:
        print("Choose a port between 1 and 65535.", file=sys.stderr)
        return 2
    try:
        with panel_lock(root):
            return _serve_ui(root, port, open_browser)
    except (OSError, UIError) as error:
        print(f"Could not start the local panel: {error}", file=sys.stderr)
        return 2


def _serve_ui(root: Path, port: int, open_browser: bool) -> int:
    try:
        server = UIServer(("127.0.0.1", port), root)
    except (OSError, UIError) as error:
        print(f"Could not start the local panel: {error}", file=sys.stderr)
        return 2
    url = f"{server.origin}/#token={server.token}"
    print(f"OmoBench local control panel\n\n{url}\n\nKeep this terminal open. Ctrl+C stops the panel and its active benchmark.", flush=True)
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
    return 0
