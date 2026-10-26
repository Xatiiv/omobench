"""guided.js progress reporting: 100% is reserved for a finished run.

Drives the real `cli/src/omobench/web/guided.js` through Node, in a VM context
with a minimal DOM stand-in (guided_progress_harness.mjs), and asserts on the
progress bar it actually renders. Skipped where Node is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUIDED_JS = ROOT / "cli" / "src" / "omobench" / "web" / "guided.js"
HARNESS = Path(__file__).resolve().parent / "guided_progress_harness.mjs"


def _render_states() -> dict:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node is not available in this environment")
    completed = subprocess.run(
        [node, str(HARNESS), str(GUIDED_JS)],
        capture_output=True, text=True, timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(f"guided.js harness failed:\n{completed.stderr}")
    return json.loads(completed.stdout)


class GuidedProgressBarTests(unittest.TestCase):
    """A 100% bar is a claim that the run finished and its evidence was
    written. Nothing short of that may display it."""

    @classmethod
    def setUpClass(cls):
        cls.states = _render_states()

    def test_evidence_finalization_is_not_reported_as_complete(self):
        """"saving" means GuideLLM stopped but compression, environment.json,
        and reproduce.md are still being written -- and can still fail."""
        saving = self.states["saving"]
        self.assertEqual(saving["ariaValueNow"], "99")
        self.assertEqual(saving["width"], "99%")
        self.assertNotIn("100", saving["label"])
        self.assertIn("evidence", saving["label"].lower())

    def test_an_active_run_near_completion_never_rounds_up_to_one_hundred(self):
        """99.6% is still running: rounding it to 100 would claim a finished
        run while requests are in flight."""
        active = self.states["activeNearComplete"]
        self.assertEqual(active["ariaValueNow"], "99")
        self.assertEqual(active["label"], "99%")

    def test_the_primitive_caps_an_unflagged_value_below_one_hundred(self):
        primitive = self.states["primitiveNearComplete"]
        self.assertEqual(primitive["ariaValueNow"], "99")
        self.assertEqual(primitive["label"], "99%")

    def test_a_successful_run_is_the_only_state_that_reaches_one_hundred(self):
        completed = self.states["completed"]
        self.assertEqual(completed["ariaValueNow"], "100")
        self.assertEqual(completed["width"], "100%")
        self.assertEqual(completed["label"], "100%")

    def test_a_failure_during_finalization_freezes_below_one_hundred(self):
        """The regression this guards: with "saving" rendered as 100%, a run
        that failed while writing its evidence froze at a finished-looking
        100% bar."""
        failed = self.states["failedDuringFinalization"]
        self.assertEqual(failed["ariaValueNow"], "99")
        self.assertFalse(failed["indeterminate"])
        self.assertIn("stopped here", failed["label"])
        self.assertNotIn("100%", failed["label"])

    def test_the_overrun_message_is_preserved(self):
        """Unchanged by the 100% fix: past the time budget, the bar still holds
        at 99 and explains that the runner is finishing its current request."""
        overrunning = self.states["overrunning"]
        self.assertEqual(overrunning["ariaValueNow"], "99")
        self.assertIn("Time limit reached", overrunning["label"])


class GuidedPerformanceDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.states = _render_states()

    def test_missing_or_unavailable_telemetry_never_becomes_a_zero_score(self):
        for key in ("missingMetrics", "unavailableMetrics"):
            with self.subTest(key=key):
                state = self.states[key]
                self.assertEqual(state["throughput"], "Pending")
                self.assertTrue(state["hidden"])
                self.assertTrue(state["inspectorHidden"])
                self.assertEqual(state["points"], "")
                self.assertEqual(state["area"], "")

    def test_live_metrics_use_observed_values_including_zero(self):
        state = self.states["observedMetrics"]
        self.assertEqual(state["throughput"], "7.25")
        self.assertEqual(state["ttft"], "0 ms")
        self.assertEqual(state["requestTime"], "12.5 s")
        self.assertEqual(state["successful"], "0")
        zero = self.states["zeroMetrics"]
        self.assertEqual(zero["throughput"], "0")
        self.assertFalse(zero["hidden"])
        self.assertEqual(len(zero["points"].split()), 1)

    def test_chart_uses_only_valid_observations_and_preserves_input(self):
        state = self.states["observedMetrics"]
        self.assertEqual(len(state["points"].split()), 2)
        self.assertNotIn("NaN", state["points"])
        self.assertTrue(self.states["historyPreserved"])
        coordinates = [float(pair.split(",")[0]) for pair in state["points"].split()]
        self.assertLess(coordinates[0], coordinates[1])

    def test_sample_inspection_is_accessible_and_survives_polling(self):
        state = self.states["inspectedSample"]
        self.assertEqual(state["readout"], "0m 10s · 5 tok/s")
        self.assertEqual(state["accessibleReadout"], state["readout"])
        self.assertEqual(self.states["retainedSelection"]["readout"], state["readout"])


if __name__ == "__main__":
    unittest.main()
