"""The advanced panel renders the same dashboard as the guided recipes.

Drives the real cli/src/omobench/web/app.js through Node, in a VM context with
the shared DOM stand-in (advanced_dashboard_harness.mjs), and asserts on what a
run against a contributor's own model server actually displays. Skipped where
Node is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "cli" / "src" / "omobench" / "web" / "app.js"
HARNESS = Path(__file__).resolve().parent / "advanced_dashboard_harness.mjs"


def _render_states() -> dict:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node is not available in this environment")
    completed = subprocess.run(
        [node, str(HARNESS), str(APP_JS)], capture_output=True, text=True, timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(f"app.js harness failed:\n{completed.stderr}")
    return json.loads(completed.stdout)


class AdvancedDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.states = _render_states()

    # -- one shared component --------------------------------------------------

    def test_the_panel_mounts_the_shared_dashboard_not_a_copy(self):
        mounted = self.states["mounted"]
        self.assertTrue(mounted["isSharedComponent"])
        self.assertEqual(len(mounted["ids"]), 15)

    def test_an_external_server_run_shows_only_stages_omobench_observes(self):
        self.assertEqual(self.states["externalStages"],
                         ["Starting the runner", "Warming up", "Running the benchmark",
                          "Cooling down", "Saving results"])
        # It did not download the weights, start the server or load the model.
        for label in ("Downloading verified files", "Verifying files", "Loading the model"):
            self.assertIn(label, self.states["managedStages"])
            self.assertNotIn(label, self.states["externalStages"])
        self.assertFalse(self.states["mounted"]["mentionsDownloading"])
        self.assertFalse(self.states["mounted"]["hasManagedServerLog"])
        self.assertFalse(self.states["mounted"]["hasDownloadProgress"])

    def test_the_full_metric_set_is_shown_with_units(self):
        warmup = self.states["warmup"]
        self.assertEqual(warmup["live"]["ttft"], "1,200 ms")
        self.assertEqual(warmup["live"]["requestLatency"], "44 s")
        self.assertEqual(warmup["live"]["successful"], "11")
        self.assertEqual(warmup["live"]["errored"], "1")
        self.assertEqual(warmup["live"]["cancelled"], "0")
        # "of at most N" comes only from the limit OmoBench configured and the
        # runner enforces; the declared measured target is reported separately,
        # as a declaration.
        self.assertEqual(warmup["facts"]["requests"], "12 finished, of at most 50 configured")
        self.assertEqual(warmup["live"]["measuredTarget"], "50")
        self.assertEqual(self.states["completed"]["metricCount"], 8)

    # -- lifecycle -------------------------------------------------------------

    def test_each_reported_stage_is_reached_in_order(self):
        expected = {
            "starting": ["active", "pending", "pending", "pending", "pending"],
            "warmup": ["done", "active", "pending", "pending", "pending"],
            "measuring": ["done", "done", "active", "pending", "pending"],
            "cooldown": ["done", "done", "done", "active", "pending"],
            "saving": ["done", "done", "done", "done", "active"],
            "completed": ["done", "done", "done", "done", "done"],
        }
        for key, states in expected.items():
            with self.subTest(stage=key):
                self.assertEqual([entry.split(":")[1] for entry in self.states[key]["stages"]], states)

    def test_before_the_runner_reports_anything_nothing_is_claimed(self):
        starting = self.states["starting"]
        self.assertTrue(starting["bar"]["indeterminate"])
        self.assertEqual(starting["facts"]["eta"], "N/A")
        self.assertEqual(starting["facts"]["requests"], "N/A")
        self.assertEqual(starting["live"]["throughput"], "Pending")
        self.assertTrue(starting["chartHidden"])

    def test_progress_comes_from_the_runner_not_from_elapsed_time(self):
        self.assertEqual(self.states["warmup"]["bar"]["label"], "5%")
        self.assertEqual(self.states["measuring"]["bar"]["label"], "40%")
        self.assertEqual(self.states["cooldown"]["bar"]["label"], "95%")

    def test_evidence_finalization_holds_at_99_until_it_succeeds(self):
        saving = self.states["saving"]
        self.assertEqual(saving["bar"]["ariaValueNow"], "99")
        self.assertIn("writing and verifying the evidence files", saving["bar"]["label"])
        self.assertEqual(self.states["completed"]["bar"]["ariaValueNow"], "100")

    def test_a_spent_time_budget_is_explained_without_claiming_completion(self):
        overrunning = self.states["overrunning"]
        self.assertEqual(overrunning["bar"]["ariaValueNow"], "99")
        self.assertIn("Time limit reached", overrunning["bar"]["label"])
        self.assertIn("finishing current request", overrunning["facts"]["eta"])
        self.assertTrue(overrunning["metricsHidden"])  # not presented as a finished run

    def test_a_completed_run_stops_estimating_remaining_time(self):
        self.assertEqual(self.states["completed"]["facts"]["eta"], "Completed")

    def test_an_unproven_request_total_is_not_presented_as_a_limit(self):
        # Without a configured limit in the snapshot, the panel states only what
        # it can: how many requests finished.
        self.assertEqual(self.states["withoutConfiguredLimit"]["facts"]["requests"], "12 finished")
        self.assertNotIn("at most", self.states["withoutConfiguredLimit"]["facts"]["requests"])
        self.assertTrue(self.states["withoutConfiguredLimit"]["measuredWindowHidden"])

    def test_a_clock_based_measured_window_is_described_as_one(self):
        note = self.states["measuring"]["measuredWindowNote"]
        self.assertIn("fixed slice of the clock", note)
        self.assertIn("first 90 s", note)
        self.assertIn("last 90 s", note)
        self.assertIn("720 s", note)
        # And the declared request counts are named as not enforced.
        self.assertIn("declares 50 measured", note)
        self.assertIn("not what is enforced", note)

    # -- telemetry that is missing, stale, malformed or interrupted ------------

    def test_missing_telemetry_is_reported_as_unavailable_not_as_zero(self):
        missing = self.states["missingTelemetry"]
        self.assertIn("Not available for this run.", missing["telemetryFacts"])
        self.assertEqual(missing["live"]["throughput"], "Pending")
        self.assertEqual(missing["facts"]["requests"], "N/A")
        self.assertFalse(missing["metricsHidden"])  # the saved result is still shown

    def test_stale_telemetry_is_named_as_stale_while_the_run_continues(self):
        self.assertIn("no runner update in 300s", self.states["stale"]["logStatus"])

    def test_malformed_telemetry_degrades_to_unknown_without_inventing_values(self):
        malformed = self.states["malformed"]
        self.assertEqual(malformed["facts"]["requests"], "N/A")
        self.assertEqual(malformed["live"]["throughput"], "Pending")
        self.assertTrue(malformed["bar"]["indeterminate"])
        facts = dict(zip(malformed["telemetryFacts"][::2], malformed["telemetryFacts"][1::2]))
        self.assertEqual(facts["Reported stage"], "unknown")
        self.assertEqual(facts["Remaining fraction"], "unknown")
        self.assertEqual(facts["Last update"], "unknown")

    # -- failure, cancellation -------------------------------------------------

    def test_failure_before_any_measurement_says_so(self):
        failed = self.states["failedBeforeMeasurements"]
        self.assertEqual(failed["heading"], "Benchmark failed")
        self.assertEqual(failed["bar"]["ariaValueNow"], "0")
        self.assertIn("No progress was recorded", failed["bar"]["label"])
        self.assertIn("No measurements were recorded", failed["chartEmpty"])
        self.assertIn("exited with code 1", failed["error"])
        self.assertTrue(failed["metricsHidden"])

    def test_failure_during_finalization_freezes_at_99_never_at_100(self):
        failed = self.states["failedDuringFinalization"]
        self.assertEqual(failed["heading"], "Benchmark failed")
        self.assertEqual(failed["bar"]["ariaValueNow"], "99")
        self.assertEqual(failed["bar"]["label"], "99%, stopped here")
        self.assertIn("could not be finalized", failed["error"])

    def test_cancellation_keeps_the_partial_metrics_it_really_observed(self):
        cancelled = self.states["cancelledWithPartialMetrics"]
        self.assertEqual(cancelled["heading"], "Benchmark cancelled")
        self.assertEqual(cancelled["bar"]["label"], "40%, stopped here")
        self.assertEqual(cancelled["facts"]["eta"], "Cancelled")
        self.assertEqual(cancelled["live"]["successful"], "11")
        self.assertIn("Runner process stopped (cancelled)", cancelled["logStatus"])

    def test_cancelling_asks_only_for_omobenchs_own_runner_to_stop(self):
        # One request, to this job's cancel endpoint. The panel has no concept
        # of the contributor's server process and never signals it.
        self.assertEqual(len(self.states["cancelRequests"]), 1)
        self.assertRegex(self.states["cancelRequests"][0], r"^POST /api/runs/job-[a-f0-9]{16}/cancel$")

    # -- run switching ---------------------------------------------------------

    def test_selecting_a_run_starts_no_second_polling_loop(self):
        self.assertEqual(self.states["selectTimers"], 0)
        self.assertEqual(self.states["pollTimersPerTick"], 1)

    def test_a_late_response_cannot_overwrite_the_run_now_selected(self):
        after = self.states["afterStaleResponse"]
        self.assertEqual(after["title"], "second/model")
        self.assertIn("second/model", after["subtitle"])
        self.assertEqual(after["log"], "second run log")
        self.assertNotIn("STALE", json.dumps(after))

    # -- declarations and identities -------------------------------------------

    def test_the_cpu_declaration_stays_cpu_throughout(self):
        for key in ("selected", "measuring", "completed", "cancelledWithPartialMetrics"):
            with self.subTest(state=key):
                self.assertIn("CPU declared", self.states[key]["subtitle"])
                self.assertTrue(self.states[key]["runStatus"].startswith("CPU · "))

    def test_an_alias_and_its_tokenizer_source_are_both_shown_separately(self):
        subtitle = self.states["measuring"]["subtitle"]
        self.assertIn("qwen/qwen3-vl-4b:3", subtitle)
        self.assertIn("tokenizer Qwen/Qwen3-VL-4B-Instruct", subtitle)

    def test_the_tokenizer_check_is_its_own_action(self):
        self.assertEqual(self.states["tokenizerCheck"], "fixture tokenizer message")
        self.assertIn("POST /api/tokenizer-check", self.states["allRequests"])

    # -- local export and publication ------------------------------------------

    def test_local_export_asks_the_panel_for_the_saved_report_only(self):
        export = self.states["localExport"]
        self.assertEqual(len(export["requests"]), 1)
        self.assertTrue(export["requests"][0].startswith("GET "))
        self.assertTrue(export["requests"][0].endswith("/local-report"))
        self.assertTrue(export["name"].endswith("-local-report.json"))
        self.assertEqual(export["content"]["kind"], "omobench-local-report")

    def test_every_missing_publication_requirement_is_listed_at_once(self):
        checklist = self.states["checklist"]
        self.assertEqual(len([item for item in checklist["items"] if item.startswith("fail:")]), 3)
        self.assertIn("fail:Canonical model ID is in the OmoBench catalog", checklist["items"])
        self.assertIn("fail:Immutable revision of the actual weights source", checklist["items"])
        self.assertIn("3 requirements still missing", checklist["summary"])
        self.assertIn("Save local report", checklist["summary"])

    def test_viewing_a_run_never_packages_uploads_or_executes(self):
        # Every one of these is a read. /api/workflow takes a body because it
        # reads the form's current values, and computes; it writes nothing.
        allowed_posts = {"POST /api/runs/<job>/cancel", "POST /api/runs/<job>/requirements",
                         "POST /api/tokenizer-check", "POST /api/workflow"}
        posts = {entry for entry in self.states["allRequests"] if entry.startswith("POST")}
        self.assertTrue(posts <= allowed_posts, posts)


if __name__ == "__main__":
    unittest.main()
