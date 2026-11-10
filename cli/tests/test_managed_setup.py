"""Managed preparation, as a component of the one panel.

It used to be a page with its own model chooser, stepper and dashboard. What
survives is the part that was unique: verifying, downloading, cancelling,
resuming and recovering a pinned model and runtime. These drive that
component inside the panel's DOM.

Everything slow about managed setup is the download, so that is what is
exercised -- progress, cancellation, a rejected checksum and recovery -- with
fixtures whose byte counts are small enough to assert on.

This file is about the component. The downloader itself -- resume after a
dropped connection, quarantine on a checksum mismatch, cancellation partway
through -- is exercised over loopback in test_managed_downloads.py. **Neither
establishes that a real multi-gigabyte download resumes correctly over a real
network.** It is the same code path and a different network, and it remains
untested.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "cli" / "src" / "omobench" / "web" / "managed-setup.js"
HARNESS = Path(__file__).resolve().parent / "managed_setup_harness.mjs"


def _render() -> dict:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node is not available in this environment")
    completed = subprocess.run([node, str(HARNESS), str(COMPONENT)], capture_output=True,
                               text=True, encoding="utf-8", timeout=120)
    if completed.returncode != 0:
        raise AssertionError(f"managed-setup harness failed:\n{completed.stderr}")
    return json.loads(completed.stdout)


class ManagedSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.states = _render()

    # -- one selector, one flow ----------------------------------------------

    def test_it_takes_its_workload_from_the_panel(self) -> None:
        """No second model list for the two to disagree about."""
        state = self.states["usesPanelSelection"]
        self.assertEqual(state["reviewRequestedFor"]["recipe"], "fixture-cpu-v1")
        self.assertEqual(state["reviewRequestedFor"]["storage_dir"], "/mnt/d/OmoBench")
        self.assertFalse(state["visible"] is False)
        self.assertEqual(state["files"], 2, "the exact files, before anything is downloaded")

    def test_it_owns_no_selector_stepper_or_dashboard(self) -> None:
        state = self.states["noDuplicateUi"]
        self.assertFalse(state["mentionsWorkloadSelect"])
        self.assertFalse(state["mentionsStepper"])
        self.assertFalse(state["mountsDashboard"])

    def test_a_workload_with_no_recipe_hides_it_and_asks_for_nothing(self) -> None:
        state = self.states["hiddenForCustom"]
        self.assertTrue(state["hidden"])
        self.assertEqual(state["calls"], 0, "a custom model triggers no managed request")

    # -- review: read-only, and exact ----------------------------------------

    def test_review_shows_what_would_be_downloaded_and_downloads_nothing(self) -> None:
        state = self.states["usesPanelSelection"]
        self.assertIn("Download and verify", state["startLabel"])
        self.assertFalse(state["startDisabled"])
        self.assertIn("enough space", state["space"])

    def test_verified_files_are_never_fetched_again(self) -> None:
        state = self.states["alreadyInstalled"]
        self.assertEqual(state["flag"], "1")
        self.assertEqual(state["label"], "Start benchmark")

    def test_unverifiable_free_space_is_neither_enough_nor_not_enough(self) -> None:
        """WSL reports its virtual disk, which can exceed the drive behind it."""
        text = self.states["unverifiableSpace"]
        self.assertIn("Virtual filesystem free space", text)
        self.assertNotIn("enough space.", text)

    def test_a_folder_that_cannot_be_used_says_so_and_refuses_to_start(self) -> None:
        state = self.states["badFolder"]
        self.assertFalse(state["hidden"])
        self.assertIn("not writable", state["error"])
        self.assertTrue(state["startDisabled"])

    # -- download: progress, completion, and the run that follows ------------

    def test_a_download_reports_real_byte_counts_then_starts_the_run(self) -> None:
        state = self.states["downloadThenRun"]
        self.assertTrue(state["sawPartialProgress"])
        self.assertIn("preparing:true", state["stages"])
        self.assertTrue(state["startedRunAfterDownload"])
        self.assertTrue(state["outcome"]["success"])
        self.assertEqual(state["outcome"]["benchmarkJobId"], "job-abc")

    def test_cancelling_a_download_cancels_the_download(self) -> None:
        """And not a run, which at that point does not exist."""
        state = self.states["cancelDownload"]
        self.assertTrue(state["cancelled"])
        self.assertFalse(state["cancelledTheRun"])

    def test_a_rejected_checksum_never_becomes_a_benchmark(self) -> None:
        state = self.states["checksumRejected"]
        self.assertFalse(state["success"])
        self.assertIn("sha256 mismatch", state["message"])
        self.assertFalse(state["startedARun"],
                         "a failed verification must not fall through into a run")

    # -- recovery -------------------------------------------------------------

    def test_a_download_still_running_is_adopted_rather_than_restarted(self) -> None:
        self.assertEqual(self.states["recoversDownload"], {"kind": "setup", "id": "mjob-4"})

    def test_a_run_still_going_is_adopted_in_preference_to_a_finished_download(self) -> None:
        self.assertEqual(self.states["recoversRun"], {"kind": "run", "id": "mrun-5"})

    def test_nothing_to_recover_is_not_an_error(self) -> None:
        self.assertIsNone(self.states["recoversNothing"])


if __name__ == "__main__":
    unittest.main()
