from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from omobench.ui import Controller

from test_telemetry import FIXTURE_MODEL_ID, _require_offline_tokenizer, _skip_unless_guidellm_pinned, _start_fixture_server
from test_ui import RUN_CONFIG, ROOT, make_root, wait_for


class ControllerTelemetryEndpointTests(unittest.TestCase):
    """Controller.telemetry() is the glue a live dashboard polls: it must
    read whatever telemetry.py actually wrote, and say so plainly, honestly,
    when there is nothing (yet) to read -- never fabricate progress."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        make_root(self.root)
        self.controller = Controller(self.root)

    def tearDown(self):
        self.controller.close()
        self.temporary.cleanup()

    def test_unknown_job_id_is_a_404(self):
        with self.assertRaises(Exception):
            self.controller.telemetry("job-0000000000000000")

    def test_a_job_with_no_telemetry_file_yet_reports_unavailable(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        script = bin_dir / "guidellm"
        script.write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            "if '--version' in sys.argv:\n print('GuideLLM 0.7.3'); sys.exit(0)\n"
            "output = pathlib.Path(sys.argv[sys.argv.index('--output') + 1].split('path=', 1)[1])\n"
            "output.write_text(json.dumps({'metadata': {'version': 2}}))\n"
        )
        script.chmod(0o755)
        original_path = os.environ["PATH"]
        os.environ["PATH"] = str(bin_dir) + os.pathsep + original_path
        self.controller.environment["PYTHONPATH"] = str(ROOT / "cli" / "src")
        self.controller.environment["PATH"] = os.environ["PATH"]
        try:
            job = self.controller.start(RUN_CONFIG)
            wait_for(lambda: not self.controller.busy)
        finally:
            os.environ["PATH"] = original_path
        # The fake executable shadows guidellm on PATH, so the environment
        # gate in run_guidellm_command() correctly declines the in-process
        # telemetry path here -- no telemetry.json was ever written.
        detail = self.controller.telemetry(job["id"])
        self.assertEqual(detail, {"available": False})

    def test_reads_a_real_snapshot_once_one_exists(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        script = bin_dir / "guidellm"
        script.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "if '--version' in sys.argv:\n print('GuideLLM 0.7.3'); sys.exit(0)\n"
            "sys.exit(1)\n"
        )
        script.chmod(0o755)
        original_path = os.environ["PATH"]
        os.environ["PATH"] = str(bin_dir) + os.pathsep + original_path
        self.controller.environment["PYTHONPATH"] = str(ROOT / "cli" / "src")
        self.controller.environment["PATH"] = os.environ["PATH"]
        try:
            job = self.controller.start(RUN_CONFIG)
            wait_for(lambda: not self.controller.busy)
        finally:
            os.environ["PATH"] = original_path

        telemetry_dir = self.controller._directory(job["id"]) / "telemetry"
        telemetry_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {"schema": 1, "stage": "benchmarking", "elapsed_seconds": 12.0, "chart_history": []}
        (telemetry_dir / "telemetry.json").write_text(json.dumps(snapshot), encoding="utf-8")

        detail = self.controller.telemetry(job["id"])
        self.assertTrue(detail["available"])
        self.assertEqual(detail["stage"], "benchmarking")
        self.assertEqual(detail["elapsed_seconds"], 12.0)

    def test_stale_telemetry_is_still_returned_honestly_not_hidden(self):
        """Staleness (no recent runner update) is a dashboard-side judgment
        call -- from a real timestamp the backend always reports as-is, and
        never silently expires or drops. Also doubles as the reconnection
        contract: a page that reconnects reads exactly this same file."""
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        script = bin_dir / "guidellm"
        script.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "if '--version' in sys.argv:\n print('GuideLLM 0.7.3'); sys.exit(0)\n"
            "sys.exit(1)\n"
        )
        script.chmod(0o755)
        original_path = os.environ["PATH"]
        os.environ["PATH"] = str(bin_dir) + os.pathsep + original_path
        self.controller.environment["PYTHONPATH"] = str(ROOT / "cli" / "src")
        self.controller.environment["PATH"] = os.environ["PATH"]
        try:
            job = self.controller.start(RUN_CONFIG)
            wait_for(lambda: not self.controller.busy)
        finally:
            os.environ["PATH"] = original_path

        telemetry_dir = self.controller._directory(job["id"]) / "telemetry"
        telemetry_dir.mkdir(parents=True, exist_ok=True)
        old_timestamp = 1000.0  # long, long ago -- the dashboard's own staleness call, not the backend's
        snapshot = {"schema": 1, "stage": "benchmarking", "updated_at": old_timestamp, "chart_history": []}
        (telemetry_dir / "telemetry.json").write_text(json.dumps(snapshot), encoding="utf-8")

        detail = self.controller.telemetry(job["id"])
        self.assertTrue(detail["available"])
        self.assertEqual(detail["updated_at"], old_timestamp)

        # Reconnecting (a fresh Controller instance, as a restarted panel
        # process would create) reads the identical persisted file.
        reconnected = Controller(self.root)
        try:
            self.assertEqual(reconnected.telemetry(job["id"]), detail)
        finally:
            reconnected.close()


@unittest.skipIf(os.name == "nt", "The fixture server uses POSIX process/signal semantics")
class RealTelemetryThroughControllerTests(unittest.TestCase):
    """End-to-end proof that the Controller's own subprocess -- unchanged --
    reaches the new in-process telemetry path for real, when (and only
    when) `guidellm` on PATH is confirmed to be this same environment's own
    pinned install; not a fake test double."""

    def setUp(self):
        _skip_unless_guidellm_pinned()
        _require_offline_tokenizer(self)
        guidellm_executable = shutil.which("guidellm")
        if guidellm_executable is None or (
            Path(guidellm_executable).parent != Path(sys.executable).parent
        ):
            raise unittest.SkipTest(
                "This test needs guidellm's real console script in the same environment as this "
                "interpreter (not shadowed on PATH), so the Controller's subprocess reaches the "
                "real in-process telemetry path rather than falling back."
            )
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        make_root(self.root)
        self.controller = Controller(self.root)
        self.controller.environment["PYTHONPATH"] = str(ROOT / "cli" / "src")

    def tearDown(self):
        self.controller.close()
        self.temporary.cleanup()

    def test_a_real_managed_run_produces_live_telemetry_and_a_normal_result(self):
        server = _start_fixture_server()
        try:
            config = {
                "endpoint": f"http://127.0.0.1:{server.server_port}",
                "model": FIXTURE_MODEL_ID,
                # chat-small-extended-v1, not chat-small-v1, purely for test speed: its
                # warmup/cooldown are request-count-based (5 requests), not a fixed
                # fraction of its duration ceiling, so a fast fixture backend finishes
                # it in well under a second. chat-small-v1's warmup=0.1 in its default
                # prefer_duration mode reserves a *fixed* 90s (10% of its 900s ceiling)
                # regardless of backend speed -- exactly the behavior
                # chat-small-extended-v1 exists to avoid; using it here is a test-speed
                # choice, not a claim that the two profiles are interchangeable.
                "profile": "chat-small-extended-v1",
                "execution_device": "cpu",
                "device_confirmed": True,
            }
            job = self.controller.start(config)

            def has_live_telemetry():
                detail = self.controller.telemetry(job["id"])
                return detail if detail.get("available") else None

            # Generous timeouts: this is a real subprocess doing real cold
            # imports and real (cached) tokenizer loading, which can take a
            # while on a loaded machine -- not a hang to chase tighter.
            wait_for(has_live_telemetry, timeout=150)
            wait_for(lambda: not self.controller.busy, timeout=180)
        finally:
            server.shutdown()

        final = self.controller.detail(job["id"])
        self.assertEqual(final["status"], "completed", final)
        # Not asserting exactly 50: this is a real run against a real (if
        # trivial) backend, and minor timing variance can shift the boundary
        # by one request -- that exact accounting is already covered by
        # ChatSmallExtendedBoundaryTests in test_cli.py. This test's job is
        # to prove the pipeline (Controller -> subprocess -> in-process
        # telemetry -> real GuideLLM -> normal result) works end to end.
        self.assertGreaterEqual(final["summary"]["results"]["requests"]["succeeded"], 45)

        telemetry_detail = self.controller.telemetry(job["id"])
        self.assertTrue(telemetry_detail["available"])
        self.assertEqual(telemetry_detail["stage"], "saving")
        self.assertEqual(telemetry_detail["remaining_fraction"], 0.0)


if __name__ == "__main__":
    unittest.main()
