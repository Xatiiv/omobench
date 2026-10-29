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
import json
import os
import re
import secrets
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .ui import JOB_ID, UIError, text_field


MANAGED_JOB_ACTIVE = {"starting_server", "waiting_ready", "running_benchmark", "preparing", "cancelling"}
MANAGED_RUN_ID = re.compile(r"mrun-[a-f0-9]{16}\Z")
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class ManagedController:
    def __init__(self, root: Path, benchmark_controller: Any):
        self.root = root.resolve()
        self.benchmark_controller = benchmark_controller
        self.storage = self.root / ".omobench-runs" / "ui" / "managed"
        if self.storage.is_symlink() or not self.storage.resolve().is_relative_to(self.root):
            raise UIError("Managed history must stay inside the checkout.")
        self.storage.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.setup_jobs: dict[str, dict] = {}
        self.run_jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.processes: dict[str, Any] = {}
        self.closing = False
        self._recover_runs()

    def _run_directory(self, job_id: str) -> Path:
        if not MANAGED_RUN_ID.fullmatch(job_id):
            raise UIError("Unknown managed run.", 404)
        directory = self.storage / job_id
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.storage.resolve()):
            raise UIError("Managed history must stay inside its staging directory.")
        return directory

    def _save_run(self, job: dict, *, required: bool = False) -> None:
        """Only UI bookkeeping, never evidence, credentials or process ownership."""
        temporary = None
        created = False
        try:
            directory = self._run_directory(job["id"])
            directory.mkdir(exist_ok=True)
            path = directory / "job.json"
            if path.is_symlink():
                raise UIError("Managed history cannot replace a symbolic link.")
            record = {key: job.get(key) for key in (
                "id", "kind", "recipe", "storage_dir", "status", "error",
                "benchmark_job_id", "created_at", "finished_at",
            )}
            record["schema_version"] = 1
            temporary = directory / ("job-" + secrets.token_hex(8) + ".tmp")
            with temporary.open("x", encoding="utf-8") as stream:
                created = True
                json.dump(record, stream, indent=2)
                stream.write("\n")
            temporary.replace(path)
        except (OSError, UIError) as error:
            if required:
                raise UIError("Could not save the managed run history; no run was started.") from error
            job["history_warning"] = "Could not save the latest managed history. Benchmark evidence is stored separately."
        finally:
            if temporary is not None and created:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def _recover_runs(self) -> None:
        # Read only: restarting never rewrites old records or resumes a process.
        for directory in self.storage.glob("mrun-*"):
            try:
                path = self._run_directory(directory.name) / "job.json"
                if path.is_symlink() or path.stat().st_size > 64 * 1024:
                    continue
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or type(record.get("schema_version")) is not int or record["schema_version"] != 1:
                    continue
                if record.get("id") != directory.name or not isinstance(record.get("kind"), str) or record["kind"] not in {"execute", "smoke"}:
                    continue
                if not isinstance(record.get("status"), str) or record["status"] not in TERMINAL | MANAGED_JOB_ACTIVE:
                    continue
                if any(not isinstance(record.get(key), str) for key in ("recipe", "storage_dir", "created_at")):
                    continue
                datetime.fromisoformat(record["created_at"])
                if any(record.get(key) is not None and not isinstance(record[key], str) for key in ("error", "finished_at")):
                    continue
                if record.get("finished_at"):
                    datetime.fromisoformat(record["finished_at"])
                child = record.get("benchmark_job_id")
                if child is not None and (not isinstance(child, str) or not JOB_ID.fullmatch(child)):
                    continue
                job = {key: record.get(key) for key in (
                    "id", "kind", "recipe", "storage_dir", "status", "error",
                    "benchmark_job_id", "created_at", "finished_at",
                )}
                job.update(result=None, recovered=True)
                if job["status"] in MANAGED_JOB_ACTIVE:
                    job["status"] = "interrupted"
                    job["finished_at"] = None  # no invented completion time
                    job["error"] = "The panel stopped before this managed run finished. It has not been resumed; inspect the saved logs."
                self.run_jobs[job["id"]] = job
            except (OSError, ValueError, UIError):
                continue

    def _history_jobs(self) -> dict[str, dict]:
        """Join by saved job ID only. Old log-only mrun directories are not guessed."""
        benchmarks = {job["id"]: job for job in self.benchmark_controller.listing()}
        jobs = {key: dict(value) for key, value in self.run_jobs.items()}
        linked = {job.get("benchmark_job_id") for job in jobs.values()}
        for job in jobs.values():
            child = job.get("benchmark_job_id")
            if child:
                bench = benchmarks.get(child)
                if bench is None:
                    job.update(status="unavailable", result=None,
                               error="The saved benchmark job is missing. Its results cannot be reopened.")
                else:
                    job.update(model=bench.get("model"), profile=bench.get("profile"),
                               execution_device=bench.get("execution_device"))
                    if job["status"] == "completed":
                        job["result"] = bench.get("summary")
            if job["status"] == "completed" and job["kind"] == "execute" and not job.get("result"):
                job.update(status="unavailable", error="No completed measurements are available for this saved run.")
        for child, bench in benchmarks.items():
            if child in linked or not JOB_ID.fullmatch(child) or bench.get("status") not in TERMINAL:
                continue
            job_id = "saved-" + child
            jobs[job_id] = {
                "id": job_id, "kind": "execute", "recipe": None, "storage_dir": "",
                "status": bench["status"], "error": bench.get("error"),
                "benchmark_job_id": child, "result": bench.get("summary"),
                "created_at": bench["created_at"], "finished_at": bench.get("finished_at"),
                "model": bench.get("model"), "profile": bench.get("profile"),
                "execution_device": bench.get("execution_device"), "saved_local": True,
            }
            if bench["status"] == "completed" and not bench.get("summary"):
                jobs[job_id].update(status="unavailable", error="No completed measurements are available for this saved run.")
        return jobs

    # -- recipes and defaults -------------------------------------------------

    def _profile_definition(self, profile_id: str) -> dict[str, Any]:
        """The profile's own declared workload (benchmarks/profiles/<id>.yaml),
        read for display only -- never fed into command construction or
        acceptance checks, which continue to come from cli.PROFILE_SPECS (the
        runner mapping) and the profile's own acceptance section, unchanged."""
        import yaml

        path = self.root / "benchmarks" / "profiles" / f"{profile_id}.yaml"
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError):
            return {}
        return document if isinstance(document, dict) else {}

    def recipes(self) -> list[dict]:
        from . import cli as omobench_cli
        from . import managed_setup

        try:
            loaded = managed_setup.load_recipes(self.root)
        except managed_setup.ManagedSetupError as error:
            raise UIError(str(error)) from error
        results = []
        for recipe in loaded:
            spec = omobench_cli.PROFILE_SPECS.get(recipe["benchmark_profile"], {})
            execution = self._profile_definition(recipe["benchmark_profile"]).get("execution", {})
            results.append(
                {
                    "id": recipe["id"],
                    "title": recipe["title"],
                    "model_id": recipe["model_id"],
                    "quantization": recipe["quantization"],
                    "benchmark_profile": recipe["benchmark_profile"],
                    "execution_device": recipe["execution_device"],
                    "supported_environment": recipe["supported_environment"],
                    # From the profile the recipe actually runs, never invented here:
                    # lets the guided page explain the duration ceiling and pick a
                    # "recommended for slower hardware" recipe from real data (the
                    # longest ceiling among same-device recipes) instead of a
                    # hard-coded recipe id.
                    "max_duration_seconds": spec.get("max_duration_seconds"),
                    # The runner's own request budget (cli.PROFILE_SPECS), which
                    # includes warmup/cooldown and so can exceed the profile's
                    # declared measured-request target -- kept for anything that
                    # needs the literal --constraint value, but the UI must not
                    # display this as "measured requests"; see measured_requests.
                    "max_requests": spec.get("max_requests"),
                    # The profile's own declared measured-request target
                    # (benchmarks/profiles/<id>.yaml execution.measured_requests),
                    # excluding warmup/cooldown -- what the guided page shows as
                    # "measured requests". For chat-small-extended-v1 this is 50,
                    # even though its runner budget (max_requests, above) is 60.
                    "measured_requests": execution.get("measured_requests"),
                }
            )
        return results

    def _state_path(self) -> Path:
        return self.storage / "preferences.json"

    def _read_state(self) -> dict[str, Any]:
        try:
            return json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember_selection(self, recipe_id: str, storage_dir: Path) -> None:
        """Reuse this storage folder (and recipe) as the default next time.

        Best-effort local UI preference, not benchmark evidence -- stays beside
        this checkout's other local panel state in .omobench-runs/ui/managed/.
        """
        state = {
            "last_recipe": recipe_id,
            "last_storage_dir": str(storage_dir),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        path = self._state_path()
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass  # Remembering the last selection is a convenience, not required.

    def _writable_ancestor(self, path: Path) -> bool:
        current = path
        while not current.exists():
            if current.parent == current:
                return False
            current = current.parent
        return os.access(current, os.W_OK)

    def suggested_storage_dir(self) -> str | None:
        """Suggest a storage folder without assuming either developer's own path.

        Order: (1) reuse whatever this checkout's panel last used, if it still
        exists; (2) /mnt/d/OmoBench, but only when that exact path already
        exists (a common WSL2 convention, never assumed); (3) a per-user
        folder under the current environment's home directory, offered only
        when its nearest existing ancestor is actually writable. Always a
        suggestion the storage-folder field can be edited away from, never
        created here.
        """
        previous = self._read_state().get("last_storage_dir")
        if previous and Path(previous).exists():
            return previous
        wsl_convention = Path("/mnt/d/OmoBench")
        if wsl_convention.exists():
            return str(wsl_convention)
        home_candidate = Path.home() / "OmoBench"
        if self._writable_ancestor(home_candidate):
            return str(home_candidate)
        return None

    def default_storage_dir(self) -> str | None:
        # Kept for backward compatibility; suggested_storage_dir() is the
        # current, fuller suggestion (previous selection, then WSL convention,
        # then a per-user default) that /api/managed/recipes now returns.
        return self.suggested_storage_dir()

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
        self._remember_selection(recipe_id, storage_dir)
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
        directory = self._run_directory(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "server.log"

    def start_run(self, data: dict, *, mode: str) -> dict:
        recipe_id = text_field(data, "recipe")
        storage_dir = self._resolve_storage_dir(data)
        if mode == "execute":
            # Fail fast, before spending any time on the managed server:
            # loading a model can take the better part of a minute, and a
            # runner that will never be reachable should never cost the
            # contributor that wait. Smoke runs never touch GuideLLM at
            # all (see the mode == "smoke" branch in _run_managed below),
            # so this check is deliberately execute-only.
            from . import cli as omobench_cli

            preflight = omobench_cli.guidellm_runner_preflight()
            if not preflight.ok:
                raise UIError(preflight.message)
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
            self._save_run(job, required=True)
            self.run_jobs[job_id] = job
        self._remember_selection(recipe_id, storage_dir)
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
                    self._save_run(job)
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
                    self._save_run(job)
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
                    self._save_run(job)
                # Fill in every field the reviewed recipe already establishes with
                # certainty, so packaging this run (from the guided page's "Share
                # results" screen or the advanced panel) never requires typing a
                # model identity, weight source, revision, quantization, runtime
                # version, or startup arguments by hand -- only genuinely unknown
                # information (the submitter identity) is left for a person to
                # enter. Never invents hardware or provenance: everything here
                # comes from the reviewed recipe or the fixed managed server
                # command this launcher itself built.
                prefill: dict[str, Any] = {"framework": "llama.cpp", "runtime_args": list(launcher.CPU_ONLY_SERVER_ARGS)}
                model_source = recipe.get("model", {}).get("url")
                if model_source:
                    prefill["model_source"] = model_source
                model_revision = recipe.get("model", {}).get("revision")
                if model_revision:
                    prefill["model_revision"] = model_revision
                if recipe.get("quantization"):
                    prefill["quantization"] = recipe["quantization"]
                runtime_version = recipe.get("runtime", {}).get("version")
                if runtime_version:
                    prefill["framework_version"] = runtime_version
                self.benchmark_controller.set_prefill(benchmark_job["id"], **prefill)
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
                self._save_run(job)

    def run_job_detail(self, job_id: str) -> dict:
        with self.lock:
            job = self._history_jobs().get(job_id)
            if job is None:
                raise UIError("Unknown managed run.", 404)
            job = dict(job)
        log_path = self._run_directory(job_id) / "server.log" if MANAGED_RUN_ID.fullmatch(job_id) else None
        if log_path is not None and not log_path.is_symlink() and log_path.is_file():
            with contextlib.suppress(OSError):
                with log_path.open("rb") as stream:
                    stream.seek(max(0, log_path.stat().st_size - 8000))
                    job["server_log"] = stream.read(8000).decode("utf-8", errors="replace")
        return job

    def run_job_list(self) -> list[dict]:
        with self.lock:
            return sorted(self._history_jobs().values(), key=lambda j: (j["created_at"], j["id"]), reverse=True)

    def cancel_run(self, job_id: str) -> dict:
        with self.lock:
            job = self.run_jobs.get(job_id)
            if job is None:
                raise UIError("Unknown managed run.", 404)
            if job["status"] not in MANAGED_JOB_ACTIVE:
                raise UIError("Only an active managed run can be cancelled.", 409)
            job["status"] = "cancelling"
            self._save_run(job)
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
