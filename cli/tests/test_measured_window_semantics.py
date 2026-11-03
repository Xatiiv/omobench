"""When does the pinned runner establish a measured window, and when not?

`cli.measured_window_integrity()` decides whether a report's measured window is
real or the runner's fallback. That decision rests on a claim about GuideLLM
0.7.3's behaviour, so the claim is established here against GuideLLM's own
accumulator -- the production `update_estimate` path, driven through the real
five-event request lifecycle -- rather than asserted in a docstring.

Two things are proved:

  * the boundary is set on one exact event, which needs both
    `processed_requests == warmup.value + 1` and that request's own
    `started_at`. It can therefore be *missed*, not merely not-yet-reached: a
    run cancelled at its ceiling can sail past the counter and still have no
    boundary;
  * when it is missed, `finalized_measure_start` equals
    `finalized_request_start` exactly, which is what the guard reads.

No network, no model, no inference.
"""
from __future__ import annotations

import math
import unittest
from pathlib import Path

from omobench import cli
from test_cli import ChatSmallExtendedBoundaryTests


ROOT = Path(__file__).resolve().parents[2]

# A plausible 2026 timestamp, used wherever the test needs realistic float
# magnitudes rather than the replay harness's zero-based clock.
EPOCH = 1793635831.578698


def benchmark(*, warmup=None, metrics=None, warmup_duration=None, state=None, requests=None):
    """The subset of a GuideLLM report the guard reads."""
    document: dict = {"config": {"warmup": warmup if warmup is not None else {}}}
    if metrics is not None:
        document["scheduler_metrics"] = metrics
    if warmup_duration is not None:
        document["warmup_duration"] = warmup_duration
    if state is not None:
        document["scheduler_state"] = state
    if requests is not None:
        document["requests"] = requests
    return document


REQUEST_WARMUP = {"percent": None, "value": 4, "mode": "requests"}
DURATION_WARMUP = {"percent": 0.1, "value": None, "mode": "prefer_duration"}
NO_WARMUP = {"percent": None, "value": None, "mode": "requests"}


class FiniteNumberTests(unittest.TestCase):
    """What counts as a timing value at all."""

    def test_real_numbers_pass_through(self):
        for value in (0.0, -1.0, 1793635831.578698, 3, 0.0865):
            with self.subTest(value=value):
                self.assertEqual(cli.finite_number(value), float(value))

    def test_nothing_else_is_a_number(self):
        for value in (None, "1.0", "", [], {}, True, False, object()):
            with self.subTest(value=repr(value)):
                self.assertIsNone(cli.finite_number(value))

    def test_non_finite_values_are_rejected_rather_than_compared(self):
        """nan == 0.0 is False, so a corrupt field must not read as healthy."""
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertIsNone(cli.finite_number(value))


class GuardRuleTests(unittest.TestCase):
    """Every shape of timing evidence the guard can be handed."""

    def verdict(self, **kwargs):
        return cli.measured_window_integrity(benchmark(**kwargs))

    # -- nothing to judge ----------------------------------------------------

    def test_no_warmup_configured_is_never_a_fallback(self):
        for warmup in (NO_WARMUP, {}, {"percent": 0, "value": 0, "mode": "requests"}):
            with self.subTest(warmup=warmup):
                result = self.verdict(warmup=warmup, warmup_duration=0.0,
                                      metrics={"measure_start_time": 5.0, "request_start_time": 5.0})
                self.assertFalse(result["warmup_configured"])
                self.assertTrue(result["warmup_boundary_crossed"])
                self.assertTrue(result["checked"])
                self.assertIn("no warmup phase is configured", result["basis"])

    def test_a_report_with_no_timing_fields_is_unchecked_not_rejected(self):
        result = self.verdict(warmup=REQUEST_WARMUP)
        self.assertFalse(result["checked"])
        self.assertTrue(result["warmup_boundary_crossed"])
        self.assertIn("no usable timing", result["basis"])

    def test_a_missing_measure_start_falls_back_to_the_duration_field(self):
        result = self.verdict(warmup=REQUEST_WARMUP, metrics={"request_start_time": EPOCH},
                              warmup_duration=0.0)
        self.assertTrue(result["checked"])
        self.assertFalse(result["warmup_boundary_crossed"])
        self.assertIn("warmup_duration == 0", result["basis"])

    def test_null_and_malformed_timings_are_unchecked(self):
        for metrics, duration in (
            ({"measure_start_time": None, "request_start_time": None}, None),
            ({"measure_start_time": "x", "request_start_time": "x"}, "x"),
            ({"measure_start_time": [], "request_start_time": {}}, []),
            ({}, None),
        ):
            with self.subTest(metrics=metrics):
                result = self.verdict(warmup=REQUEST_WARMUP, metrics=metrics,
                                      warmup_duration=duration)
                self.assertFalse(result["checked"])
                self.assertTrue(result["warmup_boundary_crossed"])

    def test_non_finite_timings_are_unchecked_and_never_verified(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                result = self.verdict(
                    warmup=REQUEST_WARMUP,
                    metrics={"measure_start_time": bad, "request_start_time": bad},
                    warmup_duration=bad)
                self.assertFalse(result["checked"])
                self.assertTrue(result["warmup_boundary_crossed"])
                self.assertIsNone(result["warmup_duration_seconds"])

    # -- the decision itself -------------------------------------------------

    def test_identical_start_timestamps_are_the_fallback(self):
        result = self.verdict(
            warmup=REQUEST_WARMUP,
            metrics={"measure_start_time": EPOCH, "request_start_time": EPOCH})
        self.assertTrue(result["checked"])
        self.assertFalse(result["warmup_boundary_crossed"])
        self.assertTrue(result["measured_window_is_the_whole_run"])
        self.assertIn("measure_start_time == request_start_time", result["basis"])

    def test_a_duration_based_warmup_is_judged_the_same_way(self):
        self.assertFalse(self.verdict(
            warmup=DURATION_WARMUP,
            metrics={"measure_start_time": EPOCH, "request_start_time": EPOCH},
        )["warmup_boundary_crossed"])
        self.assertTrue(self.verdict(
            warmup=DURATION_WARMUP,
            metrics={"measure_start_time": EPOCH + 90.0, "request_start_time": EPOCH},
        )["warmup_boundary_crossed"])

    def test_the_primary_fields_win_over_a_contradictory_duration_field(self):
        """warmup_duration is derived; the two timestamps are the evidence."""
        result = self.verdict(
            warmup=REQUEST_WARMUP,
            metrics={"measure_start_time": EPOCH + 0.1, "request_start_time": EPOCH},
            warmup_duration=0.0)
        self.assertTrue(result["warmup_boundary_crossed"])
        self.assertIn("measure_start_time == request_start_time", result["basis"])

    def test_a_negative_warmup_duration_is_not_the_fallback(self):
        """Seen for real on clock-windowed profiles; it is not this defect."""
        result = self.verdict(
            warmup=DURATION_WARMUP,
            metrics={"measure_start_time": EPOCH, "request_start_time": EPOCH + 16.8})
        self.assertTrue(result["warmup_boundary_crossed"])

    # -- precision -----------------------------------------------------------

    def test_the_smallest_distinguishable_warmup_is_one_float_ulp(self):
        """The only non-fallback way to reach equality, quantified.

        At a 2026 timestamp a double resolves about 2.4e-7 s. Two genuinely
        different timestamps closer than that subtract to exactly zero, so the
        guard could in principle mistake such a boundary for the fallback. The
        smallest genuine warmup observed against the fixture server is 0.0865 s
        -- about 360,000 ULPs -- and it covers five HTTP round trips, which
        cannot happen inside a quarter of a microsecond.
        """
        resolution = math.ulp(EPOCH)
        self.assertLess(resolution, 1e-6)
        self.assertGreater(0.0865 / resolution, 100_000)

        # One ULP apart: distinguishable, and reported as a real boundary.
        self.assertTrue(self.verdict(
            warmup=REQUEST_WARMUP,
            metrics={"measure_start_time": EPOCH + resolution, "request_start_time": EPOCH},
        )["warmup_boundary_crossed"])

        # Well inside one ULP: not representable, so the two values *are* the
        # same float and the guard calls it the fallback. That is the one false
        # positive this evidence admits; it is stated rather than hidden.
        self.assertEqual(EPOCH + resolution / 8, EPOCH)
        self.assertFalse(self.verdict(
            warmup=REQUEST_WARMUP,
            metrics={"measure_start_time": EPOCH + resolution / 8, "request_start_time": EPOCH},
        )["warmup_boundary_crossed"])

    def test_a_small_positive_warmup_is_accepted(self):
        for seconds in (0.0865, 0.001, 1e-5):
            with self.subTest(seconds=seconds):
                self.assertTrue(self.verdict(
                    warmup=REQUEST_WARMUP,
                    metrics={"measure_start_time": EPOCH + seconds, "request_start_time": EPOCH},
                )["warmup_boundary_crossed"])

    # -- corroboration -------------------------------------------------------

    def test_start_requests_time_is_reported_as_corroboration(self):
        result = self.verdict(
            warmup=REQUEST_WARMUP,
            metrics={"measure_start_time": EPOCH, "request_start_time": EPOCH},
            state={"start_requests_time": EPOCH})
        self.assertTrue(result["corroboration"]["measure_start_equals_start_requests_time"])

    def test_request_records_before_the_window_are_counted_when_present(self):
        records = {"successful": [{"request_start_time": EPOCH + n} for n in range(10)],
                   "errored": [], "incomplete": []}
        result = self.verdict(
            warmup=REQUEST_WARMUP,
            metrics={"measure_start_time": EPOCH + 5, "request_start_time": EPOCH},
            requests=records)
        self.assertEqual(result["corroboration"]["requests_recorded_before_the_window"], 5)

    def test_absent_corroboration_is_null_rather_than_assumed(self):
        result = self.verdict(
            warmup=REQUEST_WARMUP,
            metrics={"measure_start_time": EPOCH + 1, "request_start_time": EPOCH})
        self.assertIsNone(result["corroboration"]["measure_start_equals_start_requests_time"])
        self.assertIsNone(result["corroboration"]["requests_recorded_before_the_window"])


class ProductionBoundaryTests(ChatSmallExtendedBoundaryTests):
    """GuideLLM's own accumulator: when is measure_start actually set?"""

    def accumulate(self, *, warmup, cooldown, configured_total, configured_duration=None):
        config = self.g["BenchmarkConfig"](
            run_id="window-semantics", run_index=0, strategy=self.g["SynchronousStrategy"](),
            constraints={}, warmup=warmup, cooldown=cooldown,
            profile={}, requests={}, backend={}, environment={},
        )
        return self.g["GenerativeBenchmarkAccumulator"](config=config)

    def request_phases(self, value=4):
        return (self.g["TransientPhaseConfig"](value=value, mode="requests"),
                self.g["TransientPhaseConfig"](value=value, mode="requests"))

    def test_the_boundary_is_set_on_the_fifth_processed_request(self):
        warmup, cooldown = self.request_phases()
        accumulator = self.accumulate(warmup=warmup, cooldown=cooldown, configured_total=60)
        for index in range(1, 11):
            self._replay_request(accumulator, 60, f"req-{index}", float(index - 1), 1.0)
            if index < 5:
                self.assertIsNone(accumulator.timings.measure_start,
                                  f"boundary set too early, at request {index}")
        self.assertIsNotNone(accumulator.timings.measure_start)
        timings = accumulator.timings
        self.assertNotEqual(timings.finalized_measure_start, timings.finalized_request_start)

    def test_stopping_before_the_boundary_leaves_the_fallback_exactly_equal(self):
        """Four requests against a five-request warmup: no boundary, and the
        two finalized timestamps the guard compares are the same value."""
        warmup, cooldown = self.request_phases()
        accumulator = self.accumulate(warmup=warmup, cooldown=cooldown, configured_total=60)
        for index in range(1, 5):
            self._replay_request(accumulator, 60, f"req-{index}", float(index - 1), 1.0)
        self.assertIsNone(accumulator.timings.measure_start)
        timings = accumulator.timings
        self.assertEqual(timings.finalized_measure_start, timings.finalized_request_start)
        # Which is what the serialized report shows, and what the guard reads.
        self.assertFalse(cli.measured_window_integrity(benchmark(
            warmup=REQUEST_WARMUP,
            metrics={"measure_start_time": timings.finalized_measure_start,
                     "request_start_time": timings.finalized_request_start},
        ))["warmup_boundary_crossed"])

    def _abandon_queued_request(self, accumulator, configured_total, request_id, start,
                                configured_duration=None):
        """A request the scheduler gives up on at shutdown: queued, pending,
        cancelled -- never `in_progress`, so it never acquires a `started_at`."""
        counters = self._counters
        request = self.g["GenerationRequest"](request_id=request_id)
        info = self.g["RequestInfo"](request_id=request_id, status="queued")
        counters["created_requests"] += 1
        counters["queued_requests"] += 1
        info.timings.queued = start
        accumulator.update_estimate(None, request, info,
                                    self._state(configured_total, configured_duration, start))
        counters["queued_requests"] -= 1
        counters["pending_requests"] += 1
        info.status = "pending"
        info.timings.dequeued = start
        accumulator.update_estimate(None, request, info,
                                    self._state(configured_total, configured_duration, start))
        counters["pending_requests"] -= 1
        counters["cancelled_requests"] += 1
        counters["processed_requests"] += 1
        info.status = "cancelled"
        accumulator.update_estimate(None, request, info,
                                    self._state(configured_total, configured_duration, start))

    def test_a_cancelled_request_that_never_started_cannot_set_the_boundary(self):
        """Both halves of the transition are required, not just the counter."""
        warmup, cooldown = self.request_phases()
        accumulator = self.accumulate(warmup=warmup, cooldown=cooldown, configured_total=60)
        for index in range(1, 5):
            self._replay_request(accumulator, 60, f"req-{index}", float(index - 1), 1.0)
        self.assertIsNone(accumulator.timings.measure_start)
        self._abandon_queued_request(accumulator, 60, "req-5", 4.0)
        self.assertEqual(self._counters["processed_requests"], 5)
        self.assertIsNone(accumulator.timings.measure_start,
                          "a request with no started_at must not set the boundary")

    def test_a_shutdown_burst_carries_the_counter_past_the_boundary_unset(self):
        """What a ceiling expiring actually does, and why the guard is needed.

        Four requests succeed; the ceiling then expires and the scheduler
        abandons everything still queued. Those cancellations drive
        processed_requests from four to nine -- well past the five the
        transition tests for -- without a single further event carrying a
        `started_at`. The boundary is never set, so this is not "the run
        stopped before reaching it": the counter went past it.

        That is why the guard reports a window that was never established
        rather than a run that stopped early. The report cannot distinguish
        them, and both are equally unusable as measurement.
        """
        warmup, cooldown = self.request_phases()
        accumulator = self.accumulate(warmup=warmup, cooldown=cooldown, configured_total=60)
        for index in range(1, 5):
            self._replay_request(accumulator, 60, f"req-{index}", float(index - 1), 1.0)
        for index in range(5, 10):
            self._abandon_queued_request(accumulator, 60, f"req-{index}", 4.0)

        self.assertEqual(self._counters["processed_requests"], 9)
        self.assertGreater(self._counters["processed_requests"], 5)
        self.assertIsNone(accumulator.timings.measure_start)
        timings = accumulator.timings
        self.assertEqual(timings.finalized_measure_start, timings.finalized_request_start)
        self.assertFalse(cli.measured_window_integrity(benchmark(
            warmup=REQUEST_WARMUP,
            metrics={"measure_start_time": timings.finalized_measure_start,
                     "request_start_time": timings.finalized_request_start},
        ))["warmup_boundary_crossed"])

    def test_a_later_started_request_still_sets_the_boundary_while_the_counter_matches(self):
        """The counterpart, so the rule above is not overstated.

        If anything does start while processed_requests is still exactly
        five, that event sets the boundary -- one request later than the
        profile nominally intends, but set. Only a burst with nothing left to
        start loses it.
        """
        warmup, cooldown = self.request_phases()
        accumulator = self.accumulate(warmup=warmup, cooldown=cooldown, configured_total=60)
        for index in range(1, 5):
            self._replay_request(accumulator, 60, f"req-{index}", float(index - 1), 1.0)
        self._abandon_queued_request(accumulator, 60, "req-5", 4.0)
        self.assertIsNone(accumulator.timings.measure_start)
        self._replay_request(accumulator, 60, "req-6", 5.0, 1.0)
        self.assertIsNotNone(accumulator.timings.measure_start)

    def test_a_duration_warmup_sets_the_boundary_without_waiting_for_it(self):
        """Clock phases are computed, not observed, so they are never missed."""
        warmup = self.g["TransientPhaseConfig"](percent=0.1)
        cooldown = self.g["TransientPhaseConfig"](percent=0.1)
        accumulator = self.accumulate(warmup=warmup, cooldown=cooldown,
                                      configured_total=50, configured_duration=900.0)
        self._replay_request(accumulator, 50, "req-1", 0.0, 1.0, configured_duration=900.0)
        self.assertEqual(accumulator.timings.measure_start, 90.0)
        timings = accumulator.timings
        self.assertNotEqual(timings.finalized_measure_start, timings.finalized_request_start)


class ShippedProfileMatrixTests(ChatSmallExtendedBoundaryTests):
    """Every shipped profile, through the same production logic."""

    def phases(self, profile_id):
        """Build the runner's phase configs from OmoBench's own mapping string."""
        spec = cli.PROFILE_SPECS[profile_id]
        fields: dict[str, dict] = {"warmup": {}, "cooldown": {}}
        for item in spec["profile"].split(","):
            key, _, value = item.partition("=")
            if key == "kind":
                continue
            name, _, attribute = key.partition(".")
            if name not in fields:
                continue
            fields[name][attribute or "percent"] = value
        made = {}
        for name, raw in fields.items():
            if "mode" in raw:
                made[name] = self.g["TransientPhaseConfig"](
                    value=float(raw["value"]), mode=raw["mode"])
            else:
                made[name] = self.g["TransientPhaseConfig"](percent=float(raw["percent"]))
        return made["warmup"], made["cooldown"], spec

    def accumulator_for(self, profile_id):
        warmup, cooldown, spec = self.phases(profile_id)
        config = self.g["BenchmarkConfig"](
            run_id="matrix", run_index=0, strategy=self.g["SynchronousStrategy"](),
            constraints={}, warmup=warmup, cooldown=cooldown,
            profile={}, requests={}, backend={}, environment={},
        )
        return self.g["GenerativeBenchmarkAccumulator"](config=config), spec, warmup

    def report_for(self, timings, warmup_config):
        """The guard's inputs, taken from the accumulator's own finalized values."""
        return benchmark(
            warmup={"percent": warmup_config.percent, "value": warmup_config.value,
                    "mode": warmup_config.mode},
            metrics={"measure_start_time": timings.finalized_measure_start,
                     "request_start_time": timings.finalized_request_start})

    def test_every_profile_crosses_its_boundary_on_a_complete_run(self):
        for profile_id in sorted(cli.PROFILE_SPECS):
            with self.subTest(profile_id=profile_id):
                self.setUp()
                accumulator, spec, warmup = self.accumulator_for(profile_id)
                duration = float(spec["max_duration_seconds"])
                total = spec["max_requests"]
                # Enough requests, spread far enough apart, that a clock-based
                # warmup elapses as well as a counted one.
                seconds = duration / max(total, 1)
                for index in range(1, min(total, 40) + 1):
                    self._replay_request(accumulator, total, f"r-{index}",
                                         float(index - 1) * seconds, seconds,
                                         configured_duration=duration)
                verdict = cli.measured_window_integrity(
                    self.report_for(accumulator.timings, warmup))
                self.assertTrue(verdict["warmup_configured"], profile_id)
                self.assertTrue(verdict["checked"], profile_id)
                self.assertTrue(verdict["warmup_boundary_crossed"],
                                f"{profile_id}: {verdict}")

    def test_only_the_request_counted_profiles_can_lose_their_boundary(self):
        """A truncated run, per profile.

        Request-counted phases (chat-small-v2, chat-small-extended-v1) have no
        boundary at all after two requests, so the guard refuses them. The
        clock-windowed profiles compute their boundary up front, so it exists
        even though nothing lands inside it -- those runs are refused one step
        later, for measuring zero requests, exactly as they always were.
        """
        expected_fallback = {"chat-small-v2", "chat-small-extended-v1"}
        for profile_id in sorted(cli.PROFILE_SPECS):
            with self.subTest(profile_id=profile_id):
                self.setUp()
                accumulator, spec, warmup = self.accumulator_for(profile_id)
                duration = float(spec["max_duration_seconds"])
                for index in (1, 2):
                    self._replay_request(accumulator, spec["max_requests"], f"r-{index}",
                                         float(index - 1), 1.0, configured_duration=duration)
                verdict = cli.measured_window_integrity(
                    self.report_for(accumulator.timings, warmup))
                self.assertEqual(
                    not verdict["warmup_boundary_crossed"],
                    profile_id in expected_fallback,
                    f"{profile_id}: {verdict}")


if __name__ == "__main__":
    unittest.main()
