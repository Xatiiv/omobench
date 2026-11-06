"""Bounded execution and interruption evidence, driven by fixtures.

Every failure mode here was found by a real benchmark and none of them is
re-run to test it: a stream that never ends, a backend that goes quiet, a
runner that ignores a polite stop, a machine that was suspended, a machine
that was merely busy, and a power request that could not be had. Each is a
controlled input to the code that has to answer for it.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from omobench.execution import (
    Deadlines,
    ClockWatch,
    ExecutionRecord,
    ExecutionSupervisor,
    KeepAwakeSession,
    keep_the_machine_awake,
    sleep_prevention_availability,
)


class FakeClocks:
    """Three clocks a test can move independently, which is the whole point.

    A suspended machine is exactly the case where wall time and monotonic
    time stop agreeing, so a test that cannot separate them cannot test it.
    """

    def __init__(self, wall: float = 1_793_000_000.0) -> None:
        self.wall, self.mono, self.boot = wall, 1000.0, 2000.0

    def ran(self, seconds: float) -> None:
        """Time in which the machine was executing."""
        self.wall += seconds
        self.mono += seconds
        self.boot += seconds

    def suspended(self, seconds: float, *, kernel_noticed: bool = False) -> None:
        """Time that passed while the machine was not executing.

        A Linux host that suspends tells its own kernel, and CLOCK_BOOTTIME
        counts it. A WSL distribution paused by Hyper-V is not told anything:
        from inside, only the wall clock jumps when it comes back. Both are
        covered, because both have happened.
        """
        self.wall += seconds
        if kernel_noticed:
            self.boot += seconds


def watch_over(clocks: FakeClocks, **kwargs) -> ClockWatch:
    return ClockWatch(wall=lambda: clocks.wall, monotonic=lambda: clocks.mono,
                      boot=lambda: clocks.boot, **kwargs)


class ClockWatchTests(unittest.TestCase):
    def test_a_quiet_run_reports_no_interruption(self) -> None:
        clocks = FakeClocks()
        watch = watch_over(clocks)
        for _ in range(10):
            watch.sample()
            clocks.ran(1.0)
        document = watch.document()
        self.assertEqual(document["state"], "none")
        self.assertEqual(document["events"], [])
        self.assertEqual(document["observed_seconds"], 0)

    def test_only_the_kernels_own_suspend_record_is_called_confirmed(self) -> None:
        """CLOCK_BOOTTIME counts time spent suspended; CLOCK_MONOTONIC does
        not. Their divergence is the system saying it slept, and nothing else
        earns the word."""
        clocks = FakeClocks()
        watch = watch_over(clocks)
        watch.sample()
        clocks.suspended(600.0, kernel_noticed=True)
        watch.sample()
        document = watch.document()
        self.assertEqual(document["state"], "suspended")
        self.assertEqual(document["events"][0]["kind"], "suspended")
        self.assertAlmostEqual(document["observed_seconds"], 600.0, places=1)

    def test_a_virtual_machine_paused_without_being_told_is_not_confirmed(self) -> None:
        """Measured on this project's own WSL2 distribution: its
        CLOCK_BOOTTIME equals CLOCK_MONOTONIC exactly, having been up through
        several host sleep cycles. A Hyper-V pause is invisible to it, so the
        wall clock is the only signal -- and that signal has another cause.
        """
        clocks = FakeClocks()
        watch = watch_over(clocks)
        watch.sample()
        clocks.ran(1.0)
        watch.sample()
        clocks.suspended(4296.0)  # kernel_noticed=False: WSL2's actual behaviour
        watch.sample()
        document = watch.document()
        self.assertEqual(document["state"], "clock-jump")
        self.assertNotEqual(document["state"], "suspended")
        self.assertAlmostEqual(document["observed_seconds"], 4296.0, places=1)

    def test_a_wall_clock_adjustment_is_the_same_evidence_and_says_so(self) -> None:
        """NTP is active on this distribution, so a step is possible. From
        inside, a step and a pause look identical; the state says that rather
        than guessing."""
        clocks = FakeClocks()
        watch = watch_over(clocks)
        watch.sample()
        clocks.ran(1.0)
        clocks.wall += 120.0  # a forward step, nothing suspended
        watch.sample()
        self.assertEqual(watch.document()["state"], "clock-jump")

    def test_a_backwards_adjustment_is_recorded_rather_than_ignored(self) -> None:
        """You cannot un-run time, so this is definitely not a suspension --
        but the run's own timestamps moved, which is worth saying."""
        clocks = FakeClocks()
        watch = watch_over(clocks)
        watch.sample()
        clocks.ran(1.0)
        clocks.wall -= 90.0
        watch.sample()
        document = watch.document()
        self.assertEqual(document["state"], "clock-jump")
        self.assertAlmostEqual(document["observed_seconds"], 90.0, places=1)
        self.assertLess(document["events"][0]["wall_seconds"], 0)

    def test_a_confirmed_suspension_outranks_an_ambiguous_one(self) -> None:
        clocks = FakeClocks()
        watch = watch_over(clocks)
        watch.sample()
        clocks.suspended(300.0)                        # ambiguous
        watch.sample()
        clocks.suspended(600.0, kernel_noticed=True)   # confirmed
        watch.sample()
        document = watch.document()
        self.assertEqual(document["state"], "suspended")
        self.assertAlmostEqual(document["observed_seconds"], 600.0, places=1)
        self.assertEqual([event["kind"] for event in document["events"]],
                         ["clock-jump", "suspended"])

    def test_a_starved_sampler_is_a_gap_and_not_a_suspension(self) -> None:
        """Both clocks agreed. Something did not get to run; nothing slept."""
        clocks = FakeClocks()
        watch = watch_over(clocks)
        watch.sample()
        clocks.ran(120.0)
        watch.sample()
        document = watch.document()
        self.assertEqual(document["state"], "gap")
        self.assertEqual(document["events"][0]["kind"], "gap")
        self.assertAlmostEqual(document["observed_seconds"], 119.0, places=1)

    def test_an_ambiguous_jump_outranks_a_gap(self) -> None:
        """The state reports the worst thing seen, not the latest."""
        clocks = FakeClocks()
        watch = watch_over(clocks)
        watch.sample()
        clocks.ran(120.0)
        watch.sample()
        clocks.suspended(900.0)
        watch.sample()
        clocks.ran(120.0)
        watch.sample()
        document = watch.document()
        self.assertEqual(document["state"], "clock-jump")
        # Only events of the reported kind are counted into the headline
        # number: adding a scheduling delay to a clock jump would be two
        # different claims in one figure.
        self.assertAlmostEqual(document["observed_seconds"], 900.0, places=1)
        self.assertEqual([event["kind"] for event in document["events"]],
                         ["gap", "clock-jump", "gap"])

    def test_the_clocks_this_machine_actually_has_stay_in_lockstep(self) -> None:
        """Not a fixture: the real clocks, read twice a second apart.

        The thresholds are only defensible if ordinary drift is nowhere near
        them. Measured here at 0.002 ms per second.
        """
        if sys.platform != "linux":
            self.skipTest("there is no /proc clock set to read")
        watch = ClockWatch()
        self.assertIsNone(watch.sample())
        time.sleep(1.0)
        self.assertIsNone(watch.sample(), "an idle second produced an event")
        self.assertEqual(watch.document()["state"], "none")

    def test_ordinary_jitter_is_not_an_event(self) -> None:
        clocks = FakeClocks()
        watch = watch_over(clocks)
        watch.sample()
        clocks.ran(1.0)
        clocks.wall += 0.4  # a small clock correction, not a suspension
        watch.sample()
        self.assertEqual(watch.document()["state"], "none")

    def test_nothing_is_ever_subtracted_from_anything(self) -> None:
        """The record reports suspended time. It does not correct a latency."""
        clocks = FakeClocks()
        watch = watch_over(clocks)
        watch.sample()
        clocks.suspended(300.0)
        watch.sample()
        document = watch.document()
        self.assertNotIn("corrected", json.dumps(document))
        self.assertNotIn("adjusted", json.dumps(document))
        self.assertEqual(set(document) - {"state", "observed_seconds", "longest_seconds",
                                          "events", "method"}, set())


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


class SupervisorTests(unittest.TestCase):
    """The limits the pinned runner cannot enforce for itself."""

    def supervisor(self, deadlines: Deadlines, **kwargs) -> tuple[ExecutionSupervisor, list, FakeMonotonic]:
        clock = FakeMonotonic()
        calls: list[tuple[str, str]] = []
        supervisor = ExecutionSupervisor(
            deadlines,
            stop=lambda reason: calls.append(("stop", reason)),
            escalate=lambda reason: calls.append(("escalate", reason)),
            monotonic=clock,
            **kwargs)
        supervisor._started_at = clock.now
        supervisor._progress_at = clock.now
        return supervisor, calls, clock

    def test_a_stream_that_never_ends_hits_the_request_deadline(self) -> None:
        """Lively but endless: every chunk arrives, the request never does."""
        supervisor, calls, clock = self.supervisor(
            Deadlines(inactivity_seconds=300, request_seconds=600, window_seconds=5400))
        clock.now += 599
        self.assertIsNone(supervisor.check())
        clock.now += 2
        self.assertEqual(supervisor.check(), ExecutionSupervisor.REQUEST)
        self.assertEqual(calls, [("stop", ExecutionSupervisor.REQUEST)])
        self.assertIn("No request completed for 601s", supervisor.outcome.detail)

    def test_a_backend_that_stops_emitting_hits_the_same_deadline(self) -> None:
        """Silence and endless chatter are one deadline, by design.

        The inactivity timeout is the runner's and bounds the gap between two
        chunks; this bounds the request. A backend that goes quiet trips
        whichever arrives first, and both are recorded.
        """
        supervisor, calls, clock = self.supervisor(
            Deadlines(request_seconds=600, window_seconds=5400))
        clock.now += 601
        self.assertEqual(supervisor.check(), ExecutionSupervisor.REQUEST)
        self.assertEqual(calls[0][0], "stop")

    def test_progress_resets_the_request_deadline(self) -> None:
        supervisor, calls, clock = self.supervisor(
            Deadlines(request_seconds=600, window_seconds=5400))
        # Eight requests of 500s each: well inside the 5,400s window, and each
        # one on its own well inside the 600s a request is allowed.
        for _ in range(8):
            clock.now += 500
            self.assertIsNone(supervisor.check())
            supervisor.note_progress()
        self.assertEqual(calls, [])

    def test_progress_is_read_from_outside_the_runner(self) -> None:
        """The supervisor's purpose is to be outside what it is watching."""
        processed = {"count": 0}
        supervisor, calls, clock = self.supervisor(
            Deadlines(request_seconds=600, window_seconds=5400),
            progress=lambda: processed["count"])
        for _ in range(5):
            clock.now += 500
            processed["count"] += 1
            self.assertIsNone(supervisor.check())
        clock.now += 601
        self.assertEqual(supervisor.check(), ExecutionSupervisor.REQUEST)

    def test_a_progress_source_that_is_not_there_yet_is_not_an_error(self) -> None:
        supervisor, calls, clock = self.supervisor(
            Deadlines(request_seconds=600), progress=lambda: (_ for _ in ()).throw(OSError()))
        clock.now += 10
        self.assertIsNone(supervisor.check())

    def test_the_window_backstop_allows_the_whole_ceiling_plus_its_grace(self) -> None:
        """A supervisor that fired first would shorten every profile."""
        deadlines = Deadlines(window_seconds=5400, shutdown_grace_seconds=60)
        supervisor, calls, clock = self.supervisor(deadlines)
        self.assertEqual(supervisor.window_deadline, 5460)
        clock.now += 5459
        self.assertIsNone(supervisor.check())
        clock.now += 2
        self.assertEqual(supervisor.check(), ExecutionSupervisor.WINDOW)
        self.assertIn("past its 5400s ceiling", supervisor.outcome.detail)

    def test_it_fires_once_and_then_leaves_the_stop_alone(self) -> None:
        supervisor, calls, clock = self.supervisor(Deadlines(window_seconds=10))
        clock.now += 1000
        supervisor.check()
        supervisor.check()
        supervisor.check()
        self.assertEqual(len(calls), 1)

    def test_a_run_that_already_ended_is_never_stopped(self) -> None:
        supervisor, calls, clock = self.supervisor(
            Deadlines(window_seconds=10), finished=lambda: True)
        clock.now += 1000
        self.assertIsNone(supervisor.check())
        self.assertEqual(calls, [])

    def test_nothing_declared_means_nothing_enforced(self) -> None:
        supervisor, calls, clock = self.supervisor(Deadlines())
        clock.now += 1_000_000
        self.assertIsNone(supervisor.check())

    def test_the_outcome_says_what_it_did(self) -> None:
        supervisor, _calls, clock = self.supervisor(Deadlines(window_seconds=10))
        clock.now += 100
        supervisor.check()
        document = supervisor.outcome.document()
        self.assertTrue(document["fired"])
        self.assertEqual(document["reason"], "measurement-window-overrun")
        self.assertFalse(document["escalated"])
        self.assertAlmostEqual(document["at_seconds"], 100.0, places=1)


class DeadlineAnchorTests(unittest.TestCase):
    """The overall deadline cannot be reset or pushed out by anything the run
    does inside it.

    The failure this guards against is specific: a request that begins just
    before the ceiling, streams happily, and never finishes. Its own deadline
    is reset the moment it starts, and if that reset reached the window the
    run would get a second ceiling's worth of time for free -- which is how a
    ninety-minute profile became two and a half hours.
    """

    CEILING, GRACE, REQUEST = 5400.0, 60.0, 600.0

    def supervisor(self, *, progress=None, request_seconds=REQUEST):
        """`request_seconds=None` isolates the window: these tests advance the
        clock without any request finishing, which is itself a request-deadline
        breach, and two deadlines firing at once would prove neither."""
        clock = FakeMonotonic()
        calls: list[tuple[str, str]] = []
        supervisor = ExecutionSupervisor(
            Deadlines(inactivity_seconds=300, request_seconds=request_seconds,
                      window_seconds=self.CEILING, shutdown_grace_seconds=self.GRACE),
            stop=lambda reason: calls.append(("stop", reason)),
            monotonic=clock, progress=progress)
        supervisor._started_at = clock.now
        supervisor._progress_at = clock.now
        return supervisor, calls, clock

    def test_a_request_starting_near_the_ceiling_cannot_extend_the_window(self) -> None:
        supervisor, calls, clock = self.supervisor()
        # Requests tick along, each resetting the per-request deadline, right
        # up to the ceiling. The last one starts with seconds to spare.
        while clock.now - supervisor._started_at < self.CEILING - 1:
            clock.now += 100
            self.assertIsNone(supervisor.check())
            supervisor.note_progress()
        # ...and then streams forever. The window still fires on time.
        clock.now += self.GRACE + 2
        self.assertEqual(supervisor.check(), ExecutionSupervisor.WINDOW)
        self.assertLess(supervisor.outcome.at_seconds, self.CEILING + self.GRACE + 120)

    def test_a_stream_that_never_completes_is_stopped_by_whichever_comes_first(self) -> None:
        """Regularly producing output is not progress: no request finished."""
        supervisor, calls, clock = self.supervisor()
        clock.now += self.REQUEST + 1
        self.assertEqual(supervisor.check(), ExecutionSupervisor.REQUEST)
        self.assertEqual(len(calls), 1)

    def test_the_window_is_anchored_to_measurement_start_not_to_this_process(self) -> None:
        """Loading weights is nobody's ninety minutes.

        The runner's own ceiling starts at its first request. Counting from
        process start would charge every profile for its setup.
        """
        measured = {"seconds": None}
        supervisor, calls, clock = self.supervisor(
            progress=lambda: (1, measured["seconds"]), request_seconds=None)
        # Four minutes of startup: no measurement reported yet.
        clock.now += 240
        self.assertEqual(supervisor.anchored_to, "run-start")
        # The run now says it has been measuring for a second.
        measured["seconds"] = 1.0
        supervisor.check()
        self.assertEqual(supervisor.anchored_to, "measurement-start")
        # The deadline now falls 240 seconds later than it otherwise would.
        clock.now += self.CEILING + self.GRACE - 2
        self.assertIsNone(supervisor.check())
        clock.now += 4
        self.assertEqual(supervisor.check(), ExecutionSupervisor.WINDOW)
        self.assertEqual(supervisor.outcome.anchor, "measurement-start")
        self.assertIn("the run's own first request", supervisor.outcome.detail)

    def test_a_run_that_never_starts_measuring_is_still_bounded(self) -> None:
        """The fallback has to exist, or a wedged setup runs forever."""
        supervisor, calls, clock = self.supervisor(progress=lambda: (None, None),
                                                   request_seconds=None)
        clock.now += self.CEILING + self.GRACE + 1
        self.assertEqual(supervisor.check(), ExecutionSupervisor.WINDOW)
        self.assertEqual(supervisor.outcome.anchor, "run-start")
        self.assertIn("never reported a first request", supervisor.outcome.detail)

    def test_warmup_counts_inside_the_window_and_grace_is_added_outside_it(self) -> None:
        """Three different spans, and only two of them are the profile's.

        Warmup requests are part of the run's own request budget and sit
        inside the ceiling. The grace is what a stop is allowed to take and
        sits outside it. Startup sits before both.
        """
        supervisor, calls, clock = self.supervisor(progress=lambda: (1, 1.0),
                                                   request_seconds=None)
        supervisor.check()
        anchor = supervisor.anchor
        self.assertEqual(supervisor.window_deadline, self.CEILING + self.GRACE)
        clock.now = anchor + self.CEILING - 0.5
        self.assertIsNone(supervisor.check(), "the ceiling itself is the run's to use")
        clock.now = anchor + self.CEILING + self.GRACE - 0.5
        self.assertIsNone(supervisor.check(), "the grace is the stop's to use")
        clock.now = anchor + self.CEILING + self.GRACE + 0.5
        self.assertEqual(supervisor.check(), ExecutionSupervisor.WINDOW)

    def test_progress_never_moves_the_anchor_backwards_or_forwards(self) -> None:
        """A later reading of the same run reports a larger elapsed, so the
        anchor it implies stays put."""
        elapsed = {"seconds": 10.0}
        supervisor, _calls, clock = self.supervisor(
            progress=lambda: (1, elapsed["seconds"]), request_seconds=None)
        supervisor.check()
        first = supervisor.anchor
        for step in (100, 200, 300):
            clock.now += step
            elapsed["seconds"] += step
            supervisor.check()
            self.assertAlmostEqual(supervisor.anchor, first, places=6)

    def test_a_supervisor_that_never_fires_still_says_what_it_counted_from(self) -> None:
        """Found by the first real smoke run, which recorded 'run-start' on a
        run that had anchored to its measurement start fifteen minutes
        earlier. A field that reports its own default for every healthy run
        is not reporting anything."""
        supervisor = ExecutionSupervisor(
            Deadlines(window_seconds=5400, shutdown_grace_seconds=60),
            stop=lambda _reason: None, progress=lambda: (3, 120.0), poll_seconds=0.02)
        with supervisor:
            for _ in range(100):
                if supervisor.anchored_to == "measurement-start":
                    break
                time.sleep(0.02)
        self.assertEqual(supervisor.outcome.anchor, "measurement-start")
        self.assertFalse(supervisor.outcome.fired)
        self.assertEqual(supervisor.outcome.document()["anchor"], "measurement-start")

    def test_a_run_that_never_measured_records_that_instead(self) -> None:
        supervisor = ExecutionSupervisor(
            Deadlines(window_seconds=5400), stop=lambda _reason: None,
            progress=lambda: (None, None), poll_seconds=0.02)
        with supervisor:
            time.sleep(0.1)
        self.assertEqual(supervisor.outcome.anchor, "run-start")

    def test_a_snapshot_that_stops_being_written_buys_no_extra_time(self) -> None:
        """The earliest implied start wins and never moves.

        A telemetry file frozen at ten seconds would otherwise imply a later
        measurement start on every poll, pushing the deadline out exactly as
        the run did less and less.
        """
        supervisor, _calls, clock = self.supervisor(
            progress=lambda: (1, 10.0), request_seconds=None)
        supervisor.check()
        first = supervisor.anchor
        for _ in range(12):   # 6,000s, past the 5,460s the window allows
            clock.now += 500
            supervisor.check()
        self.assertAlmostEqual(supervisor.anchor, first, places=6)
        self.assertTrue(supervisor.outcome.fired)
        self.assertEqual(supervisor.outcome.reason, ExecutionSupervisor.WINDOW)


class EscalationTests(unittest.TestCase):
    """A runner that ignores the polite signal cannot hold a profile open."""

    def test_a_runner_that_ignores_sigterm_is_killed_after_the_grace(self) -> None:
        if sys.platform == "win32":
            self.skipTest("POSIX process groups")
        events: list[str] = []
        done = threading.Event()
        supervisor = ExecutionSupervisor(
            Deadlines(window_seconds=0.05, shutdown_grace_seconds=0.3),
            stop=lambda _reason: events.append("stop"),
            escalate=lambda _reason: (events.append("escalate"), done.set()),
            finished=lambda: False,
            poll_seconds=0.02)
        with supervisor:
            self.assertTrue(done.wait(5), "the supervisor never escalated")
        self.assertEqual(events, ["stop", "escalate"])
        self.assertTrue(supervisor.outcome.escalated)

    def test_a_runner_that_stops_politely_is_not_killed(self) -> None:
        events: list[str] = []
        stopped = threading.Event()
        supervisor = ExecutionSupervisor(
            Deadlines(window_seconds=0.05, shutdown_grace_seconds=0.4),
            stop=lambda _reason: (events.append("stop"), stopped.set()),
            escalate=lambda _reason: events.append("escalate"),
            finished=stopped.is_set,
            poll_seconds=0.02)
        with supervisor:
            self.assertTrue(stopped.wait(5))
            time.sleep(0.6)
        self.assertEqual(events, ["stop"])
        self.assertFalse(supervisor.outcome.escalated)


class ProcessGroupTests(unittest.TestCase):
    """Cancellation stays bounded, and takes the runner's children with it."""

    def test_a_child_that_ignores_sigterm_is_killed_with_its_group(self) -> None:
        if sys.platform == "win32":
            self.skipTest("POSIX process groups")
        from omobench.cli import signal_process_group

        stubborn = (
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "sys.stdout.write('up\\n'); sys.stdout.flush()\n"
            "time.sleep(300)\n"
        )
        process = subprocess.Popen([sys.executable, "-c", stubborn],
                                   stdout=subprocess.PIPE, text=True, start_new_session=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "up")
            signal_process_group(process, signal.SIGTERM)
            with self.assertRaises(subprocess.TimeoutExpired):
                process.wait(timeout=0.6)
            signal_process_group(process, signal.SIGKILL)
            self.assertEqual(process.wait(timeout=5), -signal.SIGKILL)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def test_signalling_a_process_that_already_exited_does_nothing(self) -> None:
        from omobench.cli import signal_process_group

        process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        process.wait(timeout=10)
        signal_process_group(process, signal.SIGTERM)  # must not raise


class ForcedTerminationTests(unittest.TestCase):
    """What a hard stop has to take with it: the runner's descendants, and
    the power request holding the machine awake.

    `abandon_run` is not called in-process here for the obvious reason, so it
    is exercised in a child that stands in for the CLI the panel starts --
    same shape, same session leadership, same grandchild to orphan.
    """

    CHILD = """
import os, signal, subprocess, sys, time
sys.path.insert(0, {source!r})
from omobench.cli import abandon_run

worker = subprocess.Popen([sys.executable, "-c",
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)"])
sys.stdout.write(str(worker.pid) + chr(10))
sys.stdout.flush()
time.sleep(0.3)
abandon_run(75)
"""

    def run_child(self, *, own_session: bool):
        source = str(Path(__file__).resolve().parents[1] / "src")
        process = subprocess.Popen(
            [sys.executable, "-c", self.CHILD.format(source=source)],
            stdout=subprocess.PIPE, text=True, start_new_session=own_session)
        worker_pid = int(process.stdout.readline().strip())
        process.wait(timeout=30)
        return process, worker_pid

    @staticmethod
    def alive(pid: int) -> bool:
        output = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                capture_output=True, text=True).stdout.strip()
        return bool(output) and not output.startswith("Z")

    def test_a_forced_stop_takes_the_runners_descendants_with_it(self) -> None:
        if sys.platform == "win32":
            self.skipTest("POSIX process groups")
        process, worker = self.run_child(own_session=True)
        self.addCleanup(lambda: self.alive(worker) and os.kill(worker, signal.SIGKILL))
        for _ in range(100):
            if not self.alive(worker):
                break
            time.sleep(0.05)
        self.assertFalse(self.alive(worker),
                         "a worker that ignores SIGTERM outlived the run that started it")
        self.assertEqual(process.returncode, -signal.SIGKILL)

    def test_it_does_not_reach_outside_the_run_it_leads(self) -> None:
        """Run from a shell rather than the panel, the group is the
        contributor's terminal. Killing that would be unforgivable."""
        if sys.platform == "win32":
            self.skipTest("POSIX process groups")
        process, worker = self.run_child(own_session=False)
        self.addCleanup(lambda: self.alive(worker) and os.kill(worker, signal.SIGKILL))
        self.assertEqual(process.returncode, 75, "it should have exited alone, with its code")
        self.assertTrue(self.alive(worker), "it killed a group it did not lead")

    def test_the_power_request_is_released_before_the_hard_exit(self) -> None:
        """Explicitly, rather than relying on the pipe closing as the process
        dies. That fallback is real and covered below, but a request released
        deliberately is released sooner."""
        released: list[str] = []
        session, _ = SleepPreventionTests().start(json.dumps({
            "kind": "omobench-keep-awake-v1", "held": True,
            "request": "execution-required", "detail": "ok"}))
        process = session.process
        original = session.close

        def close():
            released.append("closed")
            original()

        session.close = close
        # The escalation path in cli.py does exactly this, then exits.
        session.close()
        self.assertEqual(released, ["closed"])
        self.assertTrue(process.stdin.closed)

    def test_a_killed_panel_releases_it_through_the_pipe(self) -> None:
        """The helper reads standard input to end of file, so a parent that
        dies without closing anything still releases the request."""
        if sys.platform == "win32":
            self.skipTest("POSIX signals")
        helper = (
            "import sys\n"
            "sys.stdout.write('held\\n'); sys.stdout.flush()\n"
            "sys.stdin.read()\n"                    # exactly what KeepAwake.cs does
            "open(%r, 'w').write('released')\n")
        with tempfile.TemporaryDirectory() as directory:
            marker = str(Path(directory) / "released")
            child = subprocess.Popen([sys.executable, "-c", helper % marker],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            self.assertEqual(child.stdout.readline().strip(), "held")
            # The owner dies without a chance to tidy up.
            child.stdin.close()
            child.wait(timeout=10)
            self.assertEqual(Path(marker).read_text(encoding="utf-8"), "released")


class SleepPreventionTests(unittest.TestCase):
    """Asking Windows to stay awake, and what happens when it will not."""

    HELPER = "/mnt/c/Users/x/AppData/Local/Programs/OmoBench/OmoBenchCompanion.exe"

    class FakeProcess:
        def __init__(self, line: str) -> None:
            import io

            self.stdout = io.StringIO(line)
            self.stdin = io.StringIO()
            self.killed = False
            self.waited = False

        def wait(self, timeout=None):
            self.waited = True
            return 0

        def kill(self):
            self.killed = True

    def start(self, line: str):
        made: list = []

        def popen(command, **_kwargs):
            made.append(command)
            return self.FakeProcess(line)

        return keep_the_machine_awake(helper=self.HELPER, popen=popen), made

    def test_a_granted_request_is_reported_as_granted(self) -> None:
        session, made = self.start(json.dumps({
            "kind": "omobench-keep-awake-v1", "held": True,
            "request": "execution-required", "detail": "Windows granted it."}))
        self.assertTrue(session.held)
        self.assertEqual(session.status["request"], "execution-required")
        self.assertEqual(session.status["mechanism"], "windows-power-request-v1")
        self.assertEqual(made[0][1], "--keep-awake-v1")

    def test_the_weaker_request_is_not_reported_as_the_stronger_one(self) -> None:
        """A Modern Standby machine can transition past system-required."""
        session, _ = self.start(json.dumps({
            "kind": "omobench-keep-awake-v1", "held": True,
            "request": "system-required", "detail": "Windows refused the stronger one."}))
        self.assertTrue(session.held)
        self.assertEqual(session.status["request"], "system-required")

    def test_a_refusal_is_reported_and_the_helper_is_released(self) -> None:
        session, _ = self.start(json.dumps({
            "kind": "omobench-keep-awake-v1", "held": False,
            "request": None, "detail": "Windows refused the request (error 5)."}))
        self.assertFalse(session.held)
        self.assertTrue(session.status["requested"])
        self.assertIn("error 5", session.status["detail"])
        self.assertIsNone(session.process)

    def test_a_companion_that_says_nothing_is_not_taken_as_success(self) -> None:
        session, _ = self.start("")
        self.assertFalse(session.held)
        self.assertFalse(session.status["requested"])
        self.assertIn("Leave it awake", session.status["detail"])

    def test_an_answer_from_something_else_is_refused(self) -> None:
        session, _ = self.start(json.dumps({"kind": "something-else", "held": True}))
        self.assertFalse(session.held)

    def test_a_helper_that_will_not_start_never_stops_a_run(self) -> None:
        def popen(_command, **_kwargs):
            raise OSError("no such file")

        session = keep_the_machine_awake(helper=self.HELPER, popen=popen)
        self.assertFalse(session.held)
        self.assertIn("did not start", session.status["detail"])

    def test_no_companion_at_all_is_actionable_rather_than_fatal(self) -> None:
        session = keep_the_machine_awake(helper=None, popen=lambda *a, **k: None)
        self.assertFalse(session.status["requested"])
        self.assertIn("Leave this computer awake", session.status["detail"])

    def test_closing_the_session_closes_the_pipe(self) -> None:
        """The pipe is the lifetime: closing it is what releases the request."""
        session, _ = self.start(json.dumps({
            "kind": "omobench-keep-awake-v1", "held": True,
            "request": "execution-required", "detail": "ok"}))
        process = session.process
        session.close()
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.waited)
        self.assertIsNone(session.process)

    def test_closing_twice_is_harmless(self) -> None:
        session, _ = self.start(json.dumps({
            "kind": "omobench-keep-awake-v1", "held": True,
            "request": "execution-required", "detail": "ok"}))
        session.close()
        session.close()

    def test_availability_is_asked_without_acquiring_anything(self) -> None:
        asked: list[int] = []

        def helper():
            asked.append(1)
            return self.HELPER

        self.assertEqual(sleep_prevention_availability(helper=helper),
                         {"available": True, "detail": ""})
        self.assertEqual(len(asked), 1)

    def test_availability_reports_why_not(self) -> None:
        def helper():
            raise ValueError("Open this panel through the updated Windows companion")

        answer = sleep_prevention_availability(helper=helper)
        self.assertFalse(answer["available"])
        self.assertIn("updated Windows companion", answer["detail"])


class RecordTests(unittest.TestCase):
    def test_only_a_confirmed_suspension_makes_a_run_interrupted(self) -> None:
        for state, interrupted in (("none", False), ("gap", False),
                                   ("clock-jump", False), ("suspended", True)):
            record = ExecutionRecord(deadlines=Deadlines(), interruption={"state": state})
            self.assertEqual(record.interrupted, interrupted, state)

    def test_an_ambiguous_jump_still_leaves_the_run_unverified(self) -> None:
        """Absence of evidence, not evidence of absence -- and still enough
        to keep a measurement out of the registry."""
        for state, unverified in (("none", False), ("gap", False),
                                  ("clock-jump", True), ("suspended", True)):
            record = ExecutionRecord(deadlines=Deadlines(), interruption={"state": state})
            self.assertEqual(record.unverified, unverified, state)

    def test_the_document_names_its_contract(self) -> None:
        document = ExecutionRecord(deadlines=Deadlines(window_seconds=900)).document()
        self.assertEqual(document["contract"], "bounded-execution-v1")
        self.assertEqual(document["limits"]["measurement_ceiling_seconds"], 900)
        self.assertNotIn("sleep_prevention", document)

    def test_sleep_prevention_appears_only_when_there_was_something_to_say(self) -> None:
        document = ExecutionRecord(
            deadlines=Deadlines(),
            sleep_prevention={"requested": True, "held": True}).document()
        self.assertEqual(document["sleep_prevention"]["held"], True)


class ProfileDeadlineTests(unittest.TestCase):
    """What the shipped profiles declare, and what is derived from it."""

    ROOT = Path(__file__).resolve().parents[2]

    def test_the_request_deadline_defaults_to_the_ceiling(self) -> None:
        """Not a new limit: a request cannot outlive the window it runs in.

        Defaulting it this way is what lets the supervisor exist without
        changing what any shipped profile measures.
        """
        from omobench.cli import profile_deadlines

        for profile in ("chat-small-v2", "chat-small-extended-v1"):
            deadlines = profile_deadlines(self.ROOT, profile)
            self.assertEqual(deadlines.request_seconds, deadlines.window_seconds, profile)

    def test_the_inactivity_timeout_is_the_profiles_own(self) -> None:
        from omobench.cli import profile_deadlines

        self.assertEqual(
            profile_deadlines(self.ROOT, "chat-small-v2").inactivity_seconds, 300)

    def test_no_shipped_profile_declares_a_tighter_request_deadline(self) -> None:
        """One that did would be a different execution contract and would
        have to be versioned, so this is the guard that says so out loud."""
        import yaml

        for path in (self.ROOT / "benchmarks" / "profiles").glob("*.yaml"):
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            self.assertNotIn("request_deadline_seconds",
                             document.get("execution", {}), path.name)


class TelemetryProgressTests(unittest.TestCase):
    def test_progress_is_the_processed_count_the_run_already_writes(self) -> None:
        from omobench.cli import telemetry_progress

        with tempfile.TemporaryDirectory() as directory:
            telemetry_dir = Path(directory)
            read = telemetry_progress(telemetry_dir)
            self.assertIsNone(read())  # nothing written yet
            (telemetry_dir / "telemetry.json").write_text(
                json.dumps({"requests": {"processed": 7}}), encoding="utf-8")
            self.assertEqual(read(), (7, None))
            (telemetry_dir / "telemetry.json").write_text(
                json.dumps({"requests": {"processed": 9}, "elapsed_seconds": 42.5}),
                encoding="utf-8")
            self.assertEqual(read(), (9, 42.5), "the run's own measured elapsed anchors the window")
            (telemetry_dir / "telemetry.json").write_text(
                json.dumps({"requests": {"processed": 9}, "elapsed_seconds": 0.0}),
                encoding="utf-8")
            self.assertEqual(read(), (9, None), "zero elapsed means measurement has not begun")
            (telemetry_dir / "telemetry.json").write_text("{not json", encoding="utf-8")
            self.assertIsNone(read())

    def test_no_telemetry_directory_means_no_progress_source(self) -> None:
        from omobench.cli import telemetry_progress

        self.assertIsNone(telemetry_progress(None))


if __name__ == "__main__":
    unittest.main()
