"""Restart recovery uses UI records, never re-executes or edits evidence."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omobench.ui import Controller, UIError
from omobench.ui_managed import ManagedController, MANAGED_JOB_ACTIVE


class ManagedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bench = Controller(self.root)
        self.addCleanup(self.bench.close)
        self.managed = ManagedController(self.root, self.bench)
        self.addCleanup(self.managed.close)

    def benchmark(self, status="completed", number=1):
        job = dict(id=f"job-{number:016x}", status=status,
                   created_at="2026-10-26T10:00:00+00:00", finished_at="2026-10-26T11:00:00+00:00",
                   model="Fixture/Model", profile="chat-small-extended-v1", execution_device="cpu",
                   framework="llama.cpp", framework_version="fixture", model_revision="pinned-fixture",
                   summary={"results": {"requests": {"succeeded": 29}}}, error=None,
                   validation={"valid": False, "errors": ["acceptance: minimum successful requests"]},
                   submission=None)
        self.bench._directory(job["id"]).mkdir()
        self.bench.jobs[job["id"]] = job
        self.bench._save(job)
        return job

    def record(self, child=None, **changes):
        job = dict(id="mrun-" + "a" * 16, kind="execute", recipe="removed-recipe",
                   storage_dir="/old/storage", status="completed", benchmark_job_id=child,
                   result=None, error=None, created_at="2026-10-26T10:00:00+00:00",
                   finished_at="2026-10-26T11:00:00+00:00")
        job.update(changes)
        self.managed._save_run(job, required=True)
        return job

    def reopen(self):
        with patch("omobench.launcher.managed_server", side_effect=AssertionError("must not launch")), \
             patch.object(self.bench, "start", side_effect=AssertionError("must not benchmark")):
            recovered = ManagedController(self.root, self.bench)
            self.addCleanup(recovered.close)
            return recovered

    def test_completed_run_restores_link_summary_metadata_without_rewriting_any_files(self):
        bench = self.benchmark()
        job = self.record(bench["id"])
        directory = self.managed._run_directory(job["id"])
        (directory / "server.log").write_bytes(b"fixture diagnostic only")
        evidence = self.bench._directory(bench["id"]) / "run-fixture"
        evidence.mkdir()
        (evidence / "evidence.bin").write_bytes(b"preserve original bytes")
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()}
        recovered = self.reopen()
        detail = recovered.run_job_detail(job["id"])
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["result"], bench["summary"])
        self.assertEqual(detail["model"], bench["model"])
        self.assertEqual(detail["server_log"], "fixture diagnostic only")
        self.assertEqual(len(recovered.run_job_list()), 1)  # linked child is not duplicated
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()})
        self.assertFalse(self.bench.detail(bench["id"])["validation"]["valid"])

    def test_old_unlinked_benchmark_is_available_without_inventing_a_recipe_or_log(self):
        bench = self.benchmark()
        directory = self.managed.storage / ("mrun-" + "b" * 16)
        directory.mkdir()
        (directory / "server.log").write_text("cannot identify this log")
        recovered = self.reopen()
        listing = recovered.run_job_list()
        self.assertEqual(len(listing), 1)
        detail = recovered.run_job_detail("saved-" + bench["id"])
        self.assertTrue(detail["saved_local"])
        self.assertIsNone(detail["recipe"])
        self.assertEqual(detail["benchmark_job_id"], bench["id"])
        self.assertNotIn("server_log", detail)
        self.assertEqual(detail["result"], bench["summary"])

    def test_active_records_recover_as_interrupted_without_fake_finish_time_or_ownership(self):
        for state in MANAGED_JOB_ACTIVE:
            with self.subTest(state=state):
                job = self.record(status=state, finished_at=None)
                recovered = self.reopen()
                detail = recovered.run_job_detail(job["id"])
                self.assertEqual(detail["status"], "interrupted")
                self.assertIsNone(detail["finished_at"])
                self.assertEqual(recovered.processes, {})
                with self.assertRaises(UIError):
                    recovered.cancel_run(job["id"])
                self.assertEqual(json.loads((recovered.storage / job["id"] / "job.json").read_text())["status"], state)

    def test_failed_cancelled_and_interrupted_jobs_keep_their_terminal_status(self):
        for number, status in enumerate(("failed", "cancelled", "interrupted"), 1):
            self.benchmark(status, number)
        self.assertEqual({j["status"] for j in self.reopen().run_job_list()}, {"failed", "cancelled", "interrupted"})

    def test_missing_child_does_not_offer_completed_results(self):
        job = self.record("job-" + "1" * 16)
        detail = self.reopen().run_job_detail(job["id"])
        self.assertEqual(detail["status"], "unavailable")
        self.assertIsNone(detail["result"])

    def test_corrupt_unknown_schema_mismatched_id_and_unsafe_child_are_skipped(self):
        job = self.record()
        path = self.managed.storage / job["id"] / "job.json"
        for content in ("not json", "[]", json.dumps({**job, "schema_version": 2}),
                        json.dumps({**job, "schema_version": 1, "status": {}}),
                        json.dumps({**job, "schema_version": 1, "kind": []}),
                        json.dumps({**job, "schema_version": 1, "id": "wrong"}),
                        json.dumps({**job, "schema_version": 1, "benchmark_job_id": "../../outside"})):
            with self.subTest(content=content):
                path.write_text(content)
                self.assertEqual(self.reopen().run_job_list(), [])
                self.assertEqual(path.read_text(), content)

    def test_symlink_records_directories_and_logs_are_never_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "secret"
        sentinel.write_text("must not read")
        job = self.record(kind="smoke")
        directory = self.managed.storage / job["id"]
        (directory / "server.log").symlink_to(sentinel)
        self.assertNotIn("server_log", self.reopen().run_job_detail(job["id"]))
        (directory / "job.json").unlink()
        (directory / "job.json").symlink_to(sentinel)
        self.assertEqual(self.reopen().run_job_list(), [])
        linked = self.managed.storage / ("mrun-" + "c" * 16)
        linked.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.reopen().run_job_list(), [])
        self.assertEqual(sentinel.read_text(), "must not read")

    def test_smoke_check_is_not_a_benchmark_result(self):
        job = self.record(kind="smoke")
        detail = self.reopen().run_job_detail(job["id"])
        self.assertEqual(detail["kind"], "smoke")
        self.assertIsNone(detail["result"])
        self.assertIsNone(detail["benchmark_job_id"])

    def test_history_failure_before_launch_refuses_without_starting_work(self):
        with patch.object(self.managed, "_run_directory", side_effect=UIError("unwritable")), \
             patch("omobench.ui_managed.threading.Thread") as thread:
            with self.assertRaisesRegex(UIError, "no run was started"):
                self.managed.start_run({"recipe": "fixture", "storage_dir": str(self.root)}, mode="smoke")
        thread.assert_not_called()
        self.assertEqual(self.managed.run_jobs, {})

    def test_later_history_write_failure_does_not_fail_the_benchmark(self):
        job = self.record(kind="smoke")
        with patch.object(self.managed, "_run_directory", side_effect=UIError("unwritable")):
            self.managed._save_run(job)
        self.assertEqual(job["status"], "completed")
        self.assertIn("stored separately", job["history_warning"])


if __name__ == "__main__":
    unittest.main()
