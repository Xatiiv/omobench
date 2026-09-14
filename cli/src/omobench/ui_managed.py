"""Guided managed-CPU-recipe endpoints for the local browser panel.

This never runs GuideLLM itself: a managed benchmark run starts the verified
llama-server, then delegates to the existing benchmark `Controller` (same CLI
run command, same profile, same normalizer) so its job store, live log, and
local-summary download all keep working unchanged. Downloads never write
outside the contributor-chosen storage folder; this module's own bookkeeping
(server diagnostic logs, job state) stays inside the checkout's
`.omobench-runs/ui/managed/`, the same place the rest of the panel keeps
local, non-evidence UI state.
"""

from __future__ import annotations

import contextlib
import secrets
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .ui import UIError, text_field


MANAGED_JOB_ACTIVE = {"starting_server", "waiting_ready", "running_benchmark", "preparing", "cancelling"}
DEFAULT_STORAGE_SUGGESTIONS = ("/mnt/d/OmoBench", "D:\\OmoBench")


class ManagedController:
    def __init__(self, root: Path, benchmark_controller: Any):
        self.root = root
        self.benchmark_controller = benchmark_controller
        self.storage = root / ".omobench-runs" / "ui" / "managed"
        self.storage.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.setup_jobs: dict[str, dict] = {}
        self.run_jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.processes: dict[str, Any] = {}
        self.closing = False

    # -- recipes and defaults -------------------------------------------------

    def recipes(self) -> list[dict]:
        from . import managed_setup

        try:
            loaded = managed_setup.load_recipes(self.root)
        except managed_setup.ManagedSetupError as error:
            raise UIError(str(error)) from error
        return [
            {
                "id": recipe["id"],
                "title": recipe["title"],
                "model_id": recipe["model_id"],
                "quantization": recipe["quantization"],
                "benchmark_profile": recipe["benchmark_profile"],
                "execution_device": recipe["execution_device"],
                "supported_environment": recipe["supported_environment"],
            }
            for recipe in loaded
        ]

    def default_storage_dir(self) -> str | None:
        """Suggest /mnt/d/OmoBench only when that exact path already exists."""
        candidate = Path("/mnt/d/OmoBench")
        if candidate.exists():
            return str(candidate)
        return None

    # -- review (read-only) ---------------------------------------------------

    def _resolve_storage_dir(self, data: dict) -> Path:
        from . import managed_setup

        storage_raw = text_field(data, "storage_dir")
        try:
            return managed_setup.normalize_storage_dir(storage_raw)
        except managed_setup.ManagedSetupError as error:
            raise UIError(str(error)) from error

    def review(self, data: dict) -> dict:
        from . import managed_setup

        recipe_id = text_field(data, "recipe")
        storage_dir = self._resolve_storage_dir(data)
        try:
            return managed_setup.review(self.root, recipe_id, storage_dir)
        except managed_setup.ManagedSetupError as error:
            raise UIError(str(error)) from error

    # -- prepare (download + install) ----------------------------------------

    def prepare(self, data: dict) -> dict:
        if data.get("confirmed") is not True:
            raise UIError("Review the downloads, sizes, licences, and hashes, then confirm preparation.")
        recipe_id = text_field(data, "recipe")
        storage_dir = self._resolve_storage_dir(data)
        with self.lock:
            if self.closing:
                raise UIError("The panel is shutting down.")
            job_id = "mjob-" + secrets.token_hex(8)
            job = {
                "id": job_id,
                "kind": "setup",
                "recipe": recipe_id,
                "storage_dir": str(storage_dir),
                "status": "preparing",
                "progress": {},
                "error": None,
                "created_at": datetime.now(UTC).isoformat(),
                "finished_at": None,
            }
            self.setup_jobs[job_id] = job
            cancel_event = threading.Event()
            self.cancel_events[job_id] = cancel_event
        threading.Thread(target=self._run_prepare, args=(job, storage_dir, cancel_event), daemon=True).start()
        return dict(job)

    def _run_prepare(self, job: dict, storage_dir: Path, cancel_event: threading.Event) -> None:
        from . import managed_setup

        def progress(label: str, done: int, total: int) -> None:
            with self.lock:
                job["progress"][label] = {"done": done, "total": total}

        try:
            managed_setup.download_and_install(self.root, job["recipe"], storage_dir, progress=progress, cancel_event=cancel_event)
            with self.lock:
                job["status"] = "cancelled" if cancel_event.is_set() else "completed"
        except managed_setup.ManagedSetupError as error:
            with self.lock:
                job["status"] = "cancelled" if cancel_event.is_set() else "failed"
                job["error"] = str(error)
        finally:
            with self.lock:
                job["finished_at"] = datetime.now(UTC).isoformat()
                self.cancel_events.pop(job["id"], None)

    def setup_job_detail(self, job_id: str) -> dict:
        with self.lock:
            job = self.setup_jobs.get(job_id)
            if job is None:
                raise UIError("Unknown managed setup job.", 404)
            return dict(job)

    def setup_job_list(self) -> list[dict]:
        with self.lock:
            return [dict(job) for job in sorted(self.setup_jobs.values(), key=lambda j: j["created_at"], reverse=True)]

    def cancel_setup(self, job_id: str) -> dict:
        with self.lock:
            job = self.setup_jobs.get(job_id)
            if job is None:
                raise UIError("Unknown managed setup job.", 404)
            if job["status"] != "preparing":
                raise UIError("Only an active download can be cancelled.", 409)
            job["status"] = "cancelling"
            event = self.cancel_events.get(job_id)
        if event is not None:
            event.set()
        return self.setup_job_detail(job_id)

    # -- smoke / execute -------------------------------------------------------

    def _log_path(self, job_id: str) -> Path:
        directory = self.storage / job_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "server.log"

    def start_run(self, data: dict, *, mode: str) -> dict:
        recipe_id = text_field(data, "recipe")
        storage_dir = self._resolve_storage_dir(data)
        with self.lock:
            if self.closing:
                raise UIError("The panel is shutting down.")
            if self.benchmark_controller.busy:
                raise UIError("Wait for the active operation before starting the managed run.", 409)
            job_id = "mrun-" + secrets.token_hex(8)
            job = {
                "id": job_id,
                "kind": mode,
                "recipe": recipe_id,
                "storage_dir": str(storage_dir),
                "status": "starting_server",
                "error": None,
                "benchmark_job_id": None,
                "result": None,
                "created_at": datetime.now(UTC).isoformat(),
                "finished_at": None,
            }
            self.run_jobs[job_id] = job
        threading.Thread(target=self._run_managed, args=(job, storage_dir, mode), daemon=True).start()
        return dict(job)

    def _run_managed(self, job: dict, storage_dir: Path, mode: str) -> None:
        from . import launcher, managed_setup

        try:
            with launcher.managed_server(self.root, job["recipe"], storage_dir, log_path=self._log_path(job["id"])) as (
                process,
                endpoint,
                recipe,
            ):
                with self.lock:
                    if job["status"] == "cancelling":
                        raise UIError("The managed run was cancelled before the server became ready.")
                    self.processes[job["id"]] = process
                    job["status"] = "waiting_ready"
                launcher.wait_for_ready(endpoint, recipe["model_id"], process)

                if mode == "smoke":
                    with self.lock:
                        job["status"] = "cancelled" if job["status"] == "cancelling" else "completed"
                        job["result"] = {
                            "model_id": recipe["model_id"],
                            "benchmark_profile": recipe["benchmark_profile"],
                            "message": "The managed server started, loaded the model, and reported the "
                            "expected alias. No inference was performed and no benchmark result was produced.",
                        }
                    return

                with self.lock:
                    if job["status"] == "cancelling":
                        raise UIError("The managed run was cancelled before the benchmark started.")
                    job["status"] = "running_benchmark"
                benchmark_job = self.benchmark_controller.start(
                    {
                        "endpoint": endpoint,
                        "model": recipe["model_id"],
                        "profile": recipe["benchmark_profile"],
                        "execution_device": "cpu",
                        "device_confirmed": True,
                    }
                )
                with self.lock:
                    job["benchmark_job_id"] = benchmark_job["id"]
                while True:
                    detail = self.benchmark_controller.detail(benchmark_job["id"])
                    if detail["status"] not in {"running", "cancelling"}:
                        break
                    time.sleep(1)
                with self.lock:
                    job["status"] = "cancelled" if job["status"] == "cancelling" else detail["status"]
                    job["result"] = detail.get("summary")
                    if detail.get("error"):
                        job["error"] = detail["error"]
        except (managed_setup.ManagedSetupError, launcher.LauncherError, UIError) as error:
            with self.lock:
                job["status"] = "cancelled" if job["status"] == "cancelling" else "failed"
                job["error"] = str(error)
        finally:
            with self.lock:
                self.processes.pop(job["id"], None)
                job["finished_at"] = datetime.now(UTC).isoformat()

    def run_job_detail(self, job_id: str) -> dict:
        with self.lock:
            job = self.run_jobs.get(job_id)
            if job is None:
                raise UIError("Unknown managed run.", 404)
            job = dict(job)
        log_path = self.storage / job_id / "server.log"
        if log_path.is_file():
            with contextlib.suppress(OSError):
                job["server_log"] = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        return job

    def run_job_list(self) -> list[dict]:
        with self.lock:
            return [dict(job) for job in sorted(self.run_jobs.values(), key=lambda j: j["created_at"], reverse=True)]

    def cancel_run(self, job_id: str) -> dict:
        with self.lock:
            job = self.run_jobs.get(job_id)
            if job is None:
                raise UIError("Unknown managed run.", 404)
            if job["status"] not in MANAGED_JOB_ACTIVE:
                raise UIError("Only an active managed run can be cancelled.", 409)
            job["status"] = "cancelling"
            benchmark_job_id = job.get("benchmark_job_id")
            process = self.processes.get(job_id)
        if benchmark_job_id:
            with contextlib.suppress(UIError):
                self.benchmark_controller.cancel(benchmark_job_id)
        if process is not None:
            with contextlib.suppress(Exception):
                process.terminate()
        return self.run_job_detail(job_id)

    def close(self) -> None:
        with self.lock:
            self.closing = True
            for event in self.cancel_events.values():
                event.set()
            processes = list(self.processes.values())
        for process in processes:
            with contextlib.suppress(Exception):
                process.terminate()
