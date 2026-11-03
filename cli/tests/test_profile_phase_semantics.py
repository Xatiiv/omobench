"""What warmup and cooldown actually exclude, established against the pinned runner.

Three of the four shipped profiles map warmup and cooldown to a *fraction*
(`warmup=0.1,cooldown=0.1`). GuideLLM 0.7.3's TransientPhaseConfig defaults to
`prefer_duration`: `compute_limits()` computes both a duration and a request
count from that fraction and then discards the request count whenever a duration
budget exists. So those phases are clock windows, not request counts, however
many requests the profile's YAML declares.

These tests drive the pinned runner's own accumulator and final metric compiler
with synthetic request events -- no inference, no network, no model -- so they
establish semantics rather than asserting the shape of a command line.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from omobench import cli
from test_cli import ChatSmallExtendedBoundaryTests


ROOT = Path(__file__).resolve().parents[2]


class PercentagePhaseSemanticsTests(ChatSmallExtendedBoundaryTests):
    """Reuses the real five-event replay harness; adds a duration budget."""

    def percent_pipeline(self, *, configured_total, configured_duration, request_seconds,
                         completed_count, percent=0.1):
        warmup = self.g["TransientPhaseConfig"](percent=percent)
        cooldown = self.g["TransientPhaseConfig"](percent=percent)
        config = self.g["BenchmarkConfig"](
            run_id="phase-semantics", run_index=0, strategy=self.g["SynchronousStrategy"](),
            constraints={}, warmup=warmup, cooldown=cooldown,
            profile={}, requests={}, backend={}, environment={},
        )
        accumulator = self.g["GenerativeBenchmarkAccumulator"](config=config)
        for index in range(1, completed_count + 1):
            self._replay_request(accumulator, configured_total, f"req-{index}",
                                 float((index - 1) * request_seconds), float(request_seconds),
                                 configured_duration=configured_duration)
        return self.g["GenerativeMetrics"].compile(accumulator).request_totals, accumulator.timings

    # -- the resolution rule itself -------------------------------------------

    def test_a_fraction_resolves_against_duration_whenever_a_duration_budget_exists(self):
        config = self.g["TransientPhaseConfig"].create_from_value(0.1)
        self.assertEqual(config.mode, "prefer_duration")
        # Both budgets present: duration wins and the request count is discarded.
        self.assertEqual(config.compute_limits(max_requests=50, max_seconds=900), (90.0, None))
        self.assertEqual(config.compute_limits(max_requests=30, max_seconds=1800), (180.0, None))
        # Only without a duration budget does the fraction become a request count.
        self.assertEqual(config.compute_limits(max_requests=50, max_seconds=None), (None, 5))

    def test_an_explicit_request_mode_phase_ignores_the_duration_budget(self):
        config = self.g["TransientPhaseConfig"](value=4, mode="requests")
        for seconds in (None, 900, 5400):
            with self.subTest(max_seconds=seconds):
                self.assertEqual(config.compute_limits(max_requests=60, max_seconds=seconds), (None, 4))

    def test_a_fraction_truncates_rather_than_rounds_when_it_does_count_requests(self):
        config = self.g["TransientPhaseConfig"].create_from_value(0.1)
        # int(0.1 * 55) == 5, not 6: truncation, not rounding.
        self.assertEqual(config.compute_limits(max_requests=55, max_seconds=None), (None, 5))
        self.assertEqual(config.compute_limits(max_requests=9, max_seconds=None), (None, 0))

    # -- what that means through the real accumulator --------------------------

    def test_the_measured_window_is_a_clock_slice_fixed_from_the_first_update(self):
        """chat-small-v1's mapping: the window is [start+90 s, start+810 s] of its
        900 s limit, whatever the requests do."""
        for request_seconds, completed in ((1, 50), (10, 50), (45, 18)):
            with self.subTest(request_seconds=request_seconds):
                self.setUp()
                _, timings = self.percent_pipeline(
                    configured_total=50, configured_duration=900.0,
                    request_seconds=request_seconds, completed_count=completed)
                self.assertEqual(timings.measure_start, 90.0)
                self.assertEqual(timings.measure_end, 810.0)

    def test_a_host_fast_enough_to_finish_inside_warmup_measures_nothing(self):
        """The sharp end of the mismatch: 50 requests at 1 s each all land inside
        the 90-second warmup window, so the measured window is empty."""
        totals, _ = self.percent_pipeline(configured_total=50, configured_duration=900.0,
                                          request_seconds=1, completed_count=50)
        self.assertEqual(totals.total, 0)
        self.assertEqual(totals.successful, 0)

    def test_omobench_refuses_to_normalize_a_run_with_no_measured_requests(self):
        """And such a run cannot become a result: normalization rejects it rather
        than publishing an empty measured window."""
        import copy

        from test_cli import GUIDELLM_REPORT_V2

        report = copy.deepcopy(GUIDELLM_REPORT_V2)
        report["benchmarks"][0]["metrics"]["request_totals"] = {
            "successful": 0, "incomplete": 0, "errored": 0, "total": 0,
        }
        with self.assertRaisesRegex(ValueError, "no attempted requests"):
            cli.normalize_guidellm_report(report)

    def test_how_many_requests_are_measured_depends_entirely_on_latency(self):
        observed = {}
        for request_seconds in (1, 5, 10, 16, 45):
            self.setUp()
            completed = min(50, int(900 // request_seconds))
            totals, _ = self.percent_pipeline(configured_total=50, configured_duration=900.0,
                                              request_seconds=request_seconds, completed_count=completed)
            observed[request_seconds] = totals.total
        # Same profile, same request limit, wildly different measured windows.
        self.assertEqual(observed[1], 0)
        self.assertLess(observed[5], 40)
        self.assertGreaterEqual(observed[10], 40)
        self.assertGreaterEqual(observed[16], 40)
        self.assertLess(observed[45], 40)

    def test_chat_small_v1_can_only_satisfy_its_own_acceptance_in_a_latency_band(self):
        """Its acceptance floor is 40 measured successes. Both a fast host and a
        slow one fall below it, for opposite reasons."""
        floor = 40
        passing = []
        for request_seconds in (1, 5, 8, 9, 12, 16, 18, 20, 30, 45):
            self.setUp()
            completed = min(50, int(900 // request_seconds))
            totals, _ = self.percent_pipeline(configured_total=50, configured_duration=900.0,
                                              request_seconds=request_seconds, completed_count=completed)
            if totals.successful >= floor:
                passing.append(request_seconds)
        self.assertEqual(passing, [9, 12, 16, 18])

    def test_request_mode_phases_are_unaffected_by_the_duration_ceiling(self):
        """The one profile whose declaration matches execution: counts, not clock."""
        self.setUp()
        warmup = self.g["TransientPhaseConfig"](value=4, mode="requests")
        cooldown = self.g["TransientPhaseConfig"](value=4, mode="requests")
        config = self.g["BenchmarkConfig"](
            run_id="phase-semantics", run_index=0, strategy=self.g["SynchronousStrategy"](),
            constraints={}, warmup=warmup, cooldown=cooldown,
            profile={}, requests={}, backend={}, environment={},
        )
        accumulator = self.g["GenerativeBenchmarkAccumulator"](config=config)
        for index in range(1, 61):
            self._replay_request(accumulator, 60, f"req-{index}", float(index - 1), 1.0,
                                 configured_duration=5400.0)
        totals = self.g["GenerativeMetrics"].compile(accumulator).request_totals
        self.assertEqual(totals.total, 50)  # 60 - 5 warmup - 5 cooldown, as declared


class EnforcedLimitTests(ChatSmallExtendedBoundaryTests):
    """A configured request limit is enforced; the remaining count is an estimate."""

    def constraints(self):
        from guidellm.scheduler.constraints.request import MaxDurationConstraint, MaxNumberConstraint

        return MaxNumberConstraint(args={"count": 50}), MaxDurationConstraint(args={"seconds": 900})

    def state(self, *, processed):
        return self.g["SchedulerState"](
            node_id=0, num_processes=1, start_time=0.0, start_requests_time=0.0,
            created_requests=processed, processed_requests=processed,
        )

    def test_a_configured_request_limit_stops_the_run_and_is_reported_as_the_total(self):
        requests, _ = self.constraints()
        info = self.g["RequestInfo"](request_id="r", status="completed")
        midway = requests(self.state(processed=10), info)
        self.assertEqual(midway.progress.total_requests, 50)
        self.assertEqual(midway.request_queuing, "continue")
        at_limit = requests(self.state(processed=50), info)
        self.assertEqual(at_limit.request_queuing, "stop")
        self.assertEqual(at_limit.request_processing, "stop_local")
        self.assertEqual(at_limit.progress.remaining_requests, 0)

    def test_the_duration_limit_reports_its_own_total_separately(self):
        _, duration = self.constraints()
        info = self.g["RequestInfo"](request_id="r", status="completed")
        action = duration(self.state(processed=1), info)
        self.assertEqual(action.progress.total_duration, 900.0)
        self.assertIsNone(action.progress.total_requests)

    def test_remaining_requests_is_documented_as_an_estimate(self):
        field = self.g["SchedulerProgress"].model_fields["remaining_requests"]
        self.assertIn("Estimated", field.description)
        total = self.g["SchedulerProgress"].model_fields["total_requests"]
        self.assertIn("Total number of requests", total.description)


class DeclaredVersusActualTests(unittest.TestCase):
    """One place that records, per profile, what is declared and what is enforced.

    This pins today's reality. A future profile revision must update it
    deliberately rather than quietly changing what a published result means.
    """

    EXPECTED = {
        "chat-small-v1": {"phase_basis": "duration", "warmup": 90.0, "cooldown": 90.0,
                          "measured_window_seconds": 720.0, "declaration_matches_execution": False},
        "chat-long-v1": {"phase_basis": "duration", "warmup": 180.0, "cooldown": 180.0,
                         "measured_window_seconds": 1440.0, "declaration_matches_execution": False},
        "throughput-v1": {"phase_basis": "duration", "warmup": 90.0, "cooldown": 90.0,
                          "measured_window_seconds": 720.0, "declaration_matches_execution": False},
        # Request phases report what they actually exclude (the configured value
        # plus GuideLLM 0.7.3's documented extra request), not what was asked for.
        "chat-small-extended-v1": {"phase_basis": "requests", "warmup": 5.0, "cooldown": 5.0,
                                   "measured_window_seconds": None, "measured_window_requests": 50,
                                   "declaration_matches_execution": True},
        "chat-small-v2": {"phase_basis": "requests", "warmup": 5.0, "cooldown": 5.0,
                          "measured_window_seconds": None, "measured_window_requests": 50,
                          "declaration_matches_execution": True},
    }

    def test_every_shipped_profile_is_accounted_for(self):
        self.assertEqual(set(cli.PROFILE_SPECS), set(self.EXPECTED))

    def test_each_profiles_phases_resolve_as_recorded(self):
        for profile_id, expected in self.EXPECTED.items():
            with self.subTest(profile=profile_id):
                contract = cli.profile_phase_contract(ROOT, profile_id)
                for key, value in expected.items():
                    self.assertEqual(contract[key], value, f"{profile_id}.{key}")

    def test_the_enforced_limits_are_the_ones_the_command_line_carries(self):
        for profile_id, spec in cli.PROFILE_SPECS.items():
            with self.subTest(profile=profile_id):
                contract = cli.profile_phase_contract(ROOT, profile_id)
                self.assertEqual(contract["enforced_max_requests"], spec["max_requests"])
                self.assertEqual(contract["enforced_max_duration_seconds"], spec["max_duration_seconds"])
                command = cli.build_guidellm_command(
                    root=ROOT, endpoint="http://127.0.0.1:1234", model="fixture/model",
                    profile_id=profile_id, raw_output_path=Path("/fixture/raw.json"))
                self.assertIn(f"kind=max_requests,count={spec['max_requests']}", command)
                self.assertIn(f"kind=max_duration,seconds={spec['max_duration_seconds']}", command)

    def test_three_profiles_declare_request_phases_they_do_not_enforce(self):
        mismatched = [profile_id for profile_id in cli.PROFILE_SPECS
                      if not cli.profile_phase_contract(ROOT, profile_id)["declaration_matches_execution"]]
        self.assertEqual(sorted(mismatched), ["chat-long-v1", "chat-small-v1", "throughput-v1"])

    def test_an_unknown_profile_yields_no_contract_rather_than_a_guess(self):
        self.assertEqual(cli.profile_phase_contract(ROOT, "not-a-profile"), {})


if __name__ == "__main__":
    unittest.main()
