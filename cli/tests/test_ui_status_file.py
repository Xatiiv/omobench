"""`omobench ui --status-file`: the panel's private startup channel.

A non-interactive launcher (the Windows companion) needs the connection
details as data, not as text scraped from the console. These tests cover the
document it reads, the failure reasons it must tell apart, and the handling of
the token that document carries.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omobench import ui
from omobench.cli import build_parser

from test_ui import ROOT, make_root, wait_for


class StatusFileDocumentTests(unittest.TestCase):
    def test_parser_accepts_the_flag_and_defaults_to_none(self):
        parsed = build_parser().parse_args(["ui", "--no-browser"])
        self.assertIsNone(parsed.status_file)
        parsed = build_parser().parse_args(["ui", "--status-file", "/tmp/panel.json"])
        self.assertEqual(parsed.status_file, "/tmp/panel.json")

    def test_a_path_with_spaces_round_trips(self):
        """Windows checkouts and user profiles routinely contain spaces."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "My Panel Runs" / "status file.json"
            ui.write_status_file(path, {"state": "ready", "port": 8765})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["port"], 8765)

    def test_the_document_is_written_atomically_and_carries_its_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "panel.json"
            ui.write_status_file(path, {"state": "ready"})
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema"], ui.STATUS_FILE_SCHEMA)
            self.assertEqual(document["state"], "ready")
            # No temporary files left behind for a reader to trip over.
            self.assertEqual([p.name for p in Path(temporary).iterdir()], ["panel.json"])

    @unittest.skipIf(os.name == "nt", "POSIX file modes; the panel runs under WSL/Linux")
    def test_the_document_is_user_private_because_it_contains_a_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "panel.json"
            ui.write_status_file(path, {"state": "ready", "token": "secret-token"})
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600, f"expected 0600, got {mode:o}")

    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
    def test_a_symlink_at_the_write_path_is_refused_rather_than_followed(self):
        """A planted symlink must not become a way to write this token
        somewhere else, or to clobber a file through it."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / "victim.txt"
            victim.write_text("untouched", encoding="utf-8")
            target = root / "panel.json"
            # write_status_file writes "<name>.<pid>.tmp" first; plant the
            # symlink exactly there.
            (root / f"panel.json.{os.getpid()}.tmp").symlink_to(victim)
            with self.assertRaises(OSError):
                ui.write_status_file(target, {"state": "ready", "token": "secret-token"})
            self.assertEqual(victim.read_text(encoding="utf-8"), "untouched")
            self.assertFalse(target.exists())

    def test_an_existing_document_is_replaced_not_appended(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "panel.json"
            ui.write_status_file(path, {"state": "failed", "reason": "port_unavailable"})
            ui.write_status_file(path, {"state": "ready", "token": "t", "port": 1234})
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["state"], "ready")
            self.assertNotIn("reason", document)

    def test_a_write_failure_never_stops_the_panel(self):
        """The status file is a convenience for a launcher; the panel itself
        must still run for someone using the printed URL."""
        with patch("omobench.ui.write_status_file", side_effect=OSError("read-only filesystem")):
            ui._publish(Path("/unwritable/panel.json"), {"state": "ready"})  # must not raise

    def test_publishing_is_skipped_entirely_without_a_path(self):
        with patch("omobench.ui.write_status_file") as writer:
            ui._publish(None, {"state": "ready"})
            writer.assert_not_called()


class StatusFileFailureReasonTests(unittest.TestCase):
    """Each failure a launcher must handle differently gets its own stable
    reason, so it never has to match on human-readable wording."""

    def _run_and_read(self, status_path: Path, **kwargs) -> dict:
        returncode = ui.serve_ui(status_file=str(status_path), open_browser=False, **kwargs)
        self.assertEqual(returncode, 2)
        return json.loads(status_path.read_text(encoding="utf-8"))

    def test_running_outside_a_checkout_is_reported_as_such(self):
        with tempfile.TemporaryDirectory() as temporary:
            status_path = Path(temporary) / "panel.json"
            with patch("omobench.ui.cli.find_repository_root", return_value=None):
                document = self._run_and_read(status_path)
            self.assertEqual(document["state"], "failed")
            self.assertEqual(document["reason"], "not_a_checkout")
            self.assertIn("checkout", document["message"])

    def test_an_invalid_port_is_reported_as_such(self):
        with tempfile.TemporaryDirectory() as temporary:
            status_path = Path(temporary) / "panel.json"
            with patch("omobench.ui.cli.find_repository_root", return_value=Path(temporary)):
                document = self._run_and_read(status_path, port=70000)
            self.assertEqual(document["reason"], "invalid_port")

    def test_an_already_running_panel_is_distinguishable_from_other_failures(self):
        """The one-panel-per-checkout lock is unchanged; a launcher just needs
        to recognise it, because the remedy is "use the existing panel", not
        "retry on another port"."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            status_path = root / "panel.json"
            with ui.panel_lock(root):  # somebody else's panel already holds it
                with patch("omobench.ui.cli.find_repository_root", return_value=root):
                    document = self._run_and_read(status_path)
            self.assertEqual(document["reason"], "already_running")
            self.assertIn("already running", document["message"])

    def test_an_occupied_port_is_distinguishable_too(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            status_path = root / "panel.json"
            with patch("omobench.ui.cli.find_repository_root", return_value=root), patch(
                "omobench.ui.UIServer", side_effect=OSError("address already in use")
            ):
                document = self._run_and_read(status_path, port=8765)
            self.assertEqual(document["reason"], "port_unavailable")
            self.assertEqual(document["port"], 8765)


@unittest.skipIf(os.name == "nt", "The panel runs under WSL/Linux and is stopped by SIGTERM")
class ReadyDocumentTests(unittest.TestCase):
    """The ready document against a real panel subprocess, started and stopped
    exactly the way the Windows companion does it: `--no-browser
    --status-file`, then SIGTERM."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        make_root(self.root)
        self.status_path = self.root / "private" / "panel.json"

    @staticmethod
    def _free_port() -> int:
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def _start_panel(self, port: int):
        import subprocess

        environment = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "cli" / "src"),
            "PYTHONUNBUFFERED": "1",
        }
        process = subprocess.Popen(
            [
                sys.executable, "-m", "omobench.cli", "ui",
                "--no-browser", "--port", str(port), "--status-file", str(self.status_path),
            ],
            cwd=self.root, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.addCleanup(self._stop_panel, process)
        return process

    def _stop_panel(self, process) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except Exception:  # noqa: BLE001 - last resort in cleanup only
                process.kill()
                process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()

    def test_a_running_panel_publishes_connection_details_and_removes_them_on_stop(self):
        port = self._free_port()
        process = self._start_panel(port)

        wait_for(self.status_path.exists, timeout=60)
        document = json.loads(self.status_path.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], ui.STATUS_FILE_SCHEMA)
        self.assertEqual(document["state"], "ready")
        self.assertEqual(document["root"], str(self.root))
        self.assertEqual(document["pid"], process.pid)
        self.assertEqual(document["port"], port)
        # Loopback only, and the token is handed over as data here rather than
        # being scraped out of the console banner.
        self.assertEqual(document["origin"], f"http://127.0.0.1:{port}")
        self.assertEqual(document["url"], f"{document['origin']}/#token={document['token']}")
        self.assertTrue(document["token"])

        # The panel really is serving on that port, with that token.
        import urllib.request

        request = urllib.request.Request(
            f"{document['origin']}/api/runs", headers={"X-OmoBench-Token": document["token"]}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)

        # SIGTERM is how the companion stops the panel it owns.
        self._stop_panel(process)
        self.assertFalse(
            self.status_path.exists(), "the token document must not outlive the panel it describes"
        )


if __name__ == "__main__":
    unittest.main()
