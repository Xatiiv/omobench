"""What the panel offers for each kind of tokenizer failure.

Drives the real cli/src/omobench/web/app.js through Node with the shared DOM
stand-in (see tokenizer_panel_harness.mjs). The backend's classification is
tested in test_tokenizer_preflight.py; this covers the other half: a download
is offered only where one can help, the person is pointed at Advanced:
tokenizer when the source itself is wrong, and Run stays disabled until the
selected tokenizer has loaded and encoded a sample.

Skipped where Node is unavailable.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "cli" / "src" / "omobench" / "web" / "app.js"
HARNESS = Path(__file__).resolve().parent / "tokenizer_panel_harness.mjs"


def _states() -> dict:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node is not available in this environment")
    completed = subprocess.run([node, str(HARNESS), str(APP_JS)], capture_output=True, text=True,
                               encoding="utf-8", timeout=120)
    if completed.returncode != 0:
        raise AssertionError(f"tokenizer panel harness failed:\n{completed.stderr}")
    return json.loads(completed.stdout)


class TokenizerPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.states = _states()

    def test_files_absent_from_the_cache_offer_the_download(self) -> None:
        state = self.states["notCached"]
        self.assertTrue(state["downloadOffered"])
        self.assertEqual(state["downloadLabel"], "Download the tokenizer files")
        self.assertIn("not on this machine yet", state["status"])
        self.assertFalse(state["runEnabled"])

    def test_a_network_failure_during_the_download_offers_a_retry(self) -> None:
        state = self.states["afterNetworkFailure"]
        self.assertTrue(state["downloadOffered"])
        self.assertEqual(state["downloadLabel"], "Try the download again")
        self.assertIn("could not be reached", state["status"])
        self.assertFalse(state["runEnabled"])

    def test_a_successful_retry_enables_run(self) -> None:
        state = self.states["afterRetrySucceeded"]
        self.assertIn("Tokenizer ready", state["status"])
        self.assertFalse(state["downloadOffered"])
        self.assertTrue(state["runEnabled"])
        self.assertEqual(self.states["downloadRequests"][:3], ["none", "download", "download"])

    def test_changing_the_model_disables_run_at_once(self) -> None:
        self.assertFalse(self.states["afterModelChange"]["runEnabled"])

    def test_failures_a_download_cannot_fix_offer_no_download(self) -> None:
        for name, phrase in (("ggufOnly", "GGUF"), ("remoteCode", "own tokenizer code"),
                             ("cachedUnloadable", "could not be determined"),
                             ("notFound", "no public repository"),
                             ("encodeFailed", "could not encode")):
            with self.subTest(case=name):
                state = self.states[name]
                self.assertFalse(state["downloadOffered"])
                self.assertNotIn("not on this machine yet", state["status"])
                self.assertIn(phrase, state["status"])
                self.assertTrue(state["advancedOpen"], "points at Advanced: tokenizer")
                self.assertFalse(state["runEnabled"])

    def test_an_access_problem_explains_access_and_allows_a_later_retry(self) -> None:
        state = self.states["gated"]
        self.assertIn("accepted its terms", state["status"])
        self.assertTrue(state["downloadOffered"])
        self.assertEqual(state["downloadLabel"], "Try the download again")
        self.assertFalse(state["advancedOpen"])
        self.assertFalse(state["runEnabled"])

    def test_a_broken_runner_is_not_offered_a_download(self) -> None:
        state = self.states["runnerUnusable"]
        self.assertFalse(state["downloadOffered"])
        self.assertFalse(state["runEnabled"])

    def test_a_cached_tokenizer_is_ready_offline(self) -> None:
        state = self.states["loadedOffline"]
        self.assertIn("Tokenizer ready", state["status"])
        self.assertTrue(state["runEnabled"])


if __name__ == "__main__":
    unittest.main()
