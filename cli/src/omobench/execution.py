"""Bounded execution, and evidence that a measurement was interrupted.

Two problems this exists for, both found by a real benchmark rather than
imagined.

**A run that would not stop.** The pinned runner's duration constraint is a
function the scheduler evaluates while handling a request update, not a timer.
Its evaluation sets both `request_queuing="stop"` and
`request_processing="stop_local"`, and the worker does cancel what it is
processing -- in the run that overran, the scheduler exited 0.1 s after the
constraint fired. The problem is when it fires. With a concurrency of one, a
single request that streams for ninety-six minutes offers no update to
evaluate it on, and a suspended virtual machine evaluates nothing at all: the
constraint's own recorded elapsed time when it finally fired was 9,101 seconds
against a 5,400-second ceiling. Nor does the profile's
`request_timeout_seconds` help, because the runner passes it to httpx as a
*read* timeout, which bounds the gap between two chunks and not the request.

So four limits have to be separate things, and this module owns the two the
runner does not:

    inactivity     the gap between two chunks of one response -- httpx's read
                   timeout, the runner's own, unchanged here
    request        one request end to end, however lively its stream
    window         when the run stops starting new work -- the runner's
                   constraint, with the supervisor as a backstop
    shutdown       how long a stop may take before the process group is killed

**A run that was not running.** Wall-clock time can pass while this
environment does not execute: the Windows host that a WSL distribution lives
in can enter Modern Standby, and the distribution goes with it. The benchmark
cannot tell that apart from slow inference -- it records the wait as the
model's latency -- so the only honest answer is to watch the clocks and say
so. `ClockWatch` compares wall time against monotonic time and reports the
difference as evidence, never as a correction: nothing here subtracts a
suspension and publishes a tidier number.

Nothing in this module infers an interruption from a slow model response. A
slow response is a measurement; only a clock disagreeing with another clock is
evidence about the machine.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

# Bumped only for a change that alters what a run does. A result that records
# it ran under this contract is saying which rules bounded it; a result with
# no contract at all ran before there were any.
EXECUTION_CONTRACT = "bounded-execution-v1"

# How long a stop may take before the process group is killed. Generous
# because a graceful stop is what writes the evidence: the runner has to
# unwind its scheduler, compile its report and flush it.
DEFAULT_SHUTDOWN_GRACE_SECONDS = 60.0

# How often the supervisor and the clock watch look. One second is far below
# any deadline worth enforcing and costs nothing measurable; the sampling
# interval is also the resolution at which a scheduling gap can be seen.
DEFAULT_POLL_SECONDS = 1.0

# Divergence between two clocks before it is called an event rather than
# drift. Measured on this project's own WSL2 distribution: wall and monotonic
# stay in lockstep to 0.002 ms per second, so seconds of divergence are an
# event and not noise.
CLOCK_THRESHOLD_SECONDS = 5.0

# A gap in monotonic time, with the clocks agreeing, that is too long to be a
# poll interval and too short to explain. Not called a suspension, because
# nothing observed one.
GAP_THRESHOLD_SECONDS = 30.0

# Ordered by how much each one claims. `suspended` is the only confirmed
# state; `clock-jump` is deliberately not promoted to it, for the reason
# measured below.
NONE, GAP, CLOCK_JUMP, SUSPENDED = "none", "gap", "clock-jump", "suspended"
SEVERITY = (NONE, GAP, CLOCK_JUMP, SUSPENDED)


def _utc(when: float) -> str:
    return datetime.fromtimestamp(when, timezone.utc).isoformat()


def boottime() -> float | None:
    """Seconds since boot *including* time suspended, where the kernel says so.

    Linux keeps CLOCK_BOOTTIME advancing across a suspend while
    CLOCK_MONOTONIC stops, so their difference is the kernel's own record of
    how long it was asleep. Returns None where there is no such clock, in
    which case the wall-against-monotonic comparison stands alone -- it
    catches the same thing from outside, which is what a WSL distribution
    paused by its host looks like anyway.
    """
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        return None


@dataclass
class Interruption:
    """One stretch of time this environment did not execute through."""

    kind: str
    seconds: float
    started_at: str
    ended_at: str
    # What the clocks said, kept so a reader can check the conclusion rather
    # than take it. A suspension is a disagreement between two clocks; a gap
    # is both clocks agreeing on a jump nobody can account for.
    wall_seconds: float
    monotonic_seconds: float
    boottime_seconds: float | None

    def document(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "seconds": round(self.seconds, 3),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "wall_seconds": round(self.wall_seconds, 3),
            "monotonic_seconds": round(self.monotonic_seconds, 3),
            "boottime_seconds": (None if self.boottime_seconds is None
                                 else round(self.boottime_seconds, 3)),
        }


class ClockWatch:
    """Wall time against monotonic time, sampled while a benchmark runs.

    The two advance together on a machine that is running. They diverge when
    the machine is not: wall time is corrected from the host on resume, while
    monotonic time does not count what was never executed. That difference is
    direct evidence about the environment, and it is the only kind this takes.

    A long monotonic gap *with* the clocks still agreeing is a different thing
    -- the sampler itself was starved, by paging or contention -- and is
    reported as a gap, which is a question rather than an answer.
    """

    def __init__(
        self,
        *,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        clock_threshold: float = CLOCK_THRESHOLD_SECONDS,
        gap_threshold: float = GAP_THRESHOLD_SECONDS,
        wall: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        boot: Callable[[], float | None] = boottime,
    ) -> None:
        self.poll_seconds = poll_seconds
        self.clock_threshold = clock_threshold
        self.gap_threshold = gap_threshold
        self._wall, self._monotonic, self._boot = wall, monotonic, boot
        self._lock = threading.Lock()
        self._events: list[Interruption] = []
        self._last: tuple[float, float, float | None] | None = None
        self._started: float | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- sampling ------------------------------------------------------------

    def sample(self) -> Interruption | None:
        """Take one reading, and say what it means against the last one.

        Three outcomes, and the difference between the second and the third
        is the whole honesty of this module.

        `suspended` is the kernel's own record: CLOCK_BOOTTIME counts time
        spent suspended and CLOCK_MONOTONIC does not, so their divergence is
        the system saying it slept. Nothing else is called confirmed.

        `clock-jump` is the wall clock disagreeing with the monotonic clock
        while the kernel says nothing happened. From inside, that has two
        causes and they cannot be told apart: the environment was paused
        without being told, or the wall clock was stepped. Measured on this
        project's own WSL2 distribution, both are live possibilities -- its
        CLOCK_BOOTTIME equals CLOCK_MONOTONIC exactly, through host sleep
        cycles, so a Hyper-V pause is invisible to it; and `timedatectl`
        reports NTP active, so a step is possible too. Calling that a
        confirmed suspension would be claiming to know which.
        """
        now = (self._wall(), self._monotonic(), self._boot())
        previous, self._last = self._last, now
        if self._started is None:
            self._started = now[0]
        if previous is None:
            return None
        wall_delta = now[0] - previous[0]
        monotonic_delta = now[1] - previous[1]
        boot_delta = (None if previous[2] is None or now[2] is None
                      else now[2] - previous[2])

        suspended = 0.0 if boot_delta is None else boot_delta - monotonic_delta
        if suspended >= self.clock_threshold:
            return self._record(SUSPENDED, suspended, previous, now,
                                wall_delta, monotonic_delta, boot_delta)
        # Either direction. A wall clock that went backwards was adjusted,
        # which says the run's own timestamps moved under it -- not a
        # suspension, and not nothing.
        drift = abs(wall_delta - monotonic_delta)
        if drift >= self.clock_threshold:
            return self._record(CLOCK_JUMP, drift, previous, now,
                                wall_delta, monotonic_delta, boot_delta)
        if monotonic_delta - self.poll_seconds >= self.gap_threshold:
            return self._record(GAP, monotonic_delta - self.poll_seconds, previous, now,
                                wall_delta, monotonic_delta, boot_delta)
        return None

    def _record(self, kind, seconds, previous, now, wall_delta, monotonic_delta,
                boot_delta) -> Interruption:
        event = Interruption(
            kind=kind, seconds=seconds,
            started_at=_utc(previous[0]), ended_at=_utc(now[0]),
            wall_seconds=wall_delta, monotonic_seconds=monotonic_delta,
            boottime_seconds=boot_delta)
        with self._lock:
            self._events.append(event)
        return event

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> ClockWatch:
        self.sample()
        self._thread = threading.Thread(target=self._loop, name="omobench-clockwatch",
                                        daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            self.sample()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_seconds * 3)
            self._thread = None
        self.sample()

    def __enter__(self) -> ClockWatch:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- what it found -------------------------------------------------------

    @property
    def events(self) -> list[Interruption]:
        with self._lock:
            return list(self._events)

    def document(self) -> dict[str, Any]:
        """The interruption record, in the shape a result records.

        `state` is the worst thing seen, and the three values are ordered by
        how much they claim: nothing observed, a gap nobody can account for,
        and a stretch of time this environment demonstrably did not execute
        through. `observed_seconds` is how much of it there was -- reported,
        never subtracted from anything.
        """
        events = self.events
        kinds = {item.kind for item in events}
        state = max((kind for kind in SEVERITY if kind in kinds),
                    key=SEVERITY.index, default=NONE)
        # Only the events of the reported kind are counted into the headline
        # number: adding a scheduling delay to a clock jump would be two
        # different claims in one figure.
        counted = [item for item in events if item.kind == state]
        return {
            "state": state,
            "observed_seconds": round(sum(item.seconds for item in counted), 3),
            "longest_seconds": round(max((item.seconds for item in counted), default=0.0), 3),
            "events": [item.document() for item in events],
            "method": "CLOCK_BOOTTIME and the wall clock against CLOCK_MONOTONIC, sampled "
                      f"every {self.poll_seconds:g}s while measuring",
        }


# -- bounded execution -------------------------------------------------------


@dataclass
class Deadlines:
    """What may bound a run, each named for what it actually bounds."""

    # The gap between two chunks of one response. Enforced by the runner's own
    # HTTP client; recorded here so a result says what it was.
    inactivity_seconds: float | None = None
    # One request end to end. Nothing in the pinned runner enforces this.
    request_seconds: float | None = None
    # When the run must stop starting new work. The runner's constraint is the
    # primary; the supervisor is the backstop for when it is never evaluated.
    window_seconds: float | None = None
    # How long a stop may take before the process group is killed.
    shutdown_grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS

    def document(self) -> dict[str, Any]:
        return {
            "inactivity_timeout_seconds": self.inactivity_seconds,
            "request_deadline_seconds": self.request_seconds,
            "measurement_ceiling_seconds": self.window_seconds,
            "shutdown_grace_seconds": self.shutdown_grace_seconds,
        }


@dataclass
class SupervisorOutcome:
    """Whether the supervisor acted, and how far it had to go."""

    fired: bool = False
    reason: str | None = None
    escalated: bool = False
    at_seconds: float | None = None
    detail: str = ""
    # Which moment the measurement window was counted from. Recorded because
    # a deadline is meaningless without saying what it was measured from.
    anchor: str = "run-start"

    def document(self) -> dict[str, Any]:
        return {
            "fired": self.fired,
            "reason": self.reason,
            "escalated": self.escalated,
            "at_seconds": None if self.at_seconds is None else round(self.at_seconds, 3),
            "anchor": self.anchor,
            "detail": self.detail,
        }


class ExecutionSupervisor:
    """An independent timer for the limits the runner cannot enforce itself.

    It is deliberately outside the runner's own control flow, because the
    thing it guards against is the runner's control flow never reaching the
    check. It asks for a graceful stop first -- that is what writes the
    evidence -- and escalates only after the grace period, so a runner that
    ignores the polite signal cannot hold a profile open indefinitely.

    What it cannot promise, and does not: a wall-clock guarantee while the
    machine is suspended. A supervisor inside a paused virtual machine does
    not execute either. It fires on resume, having measured the time it was
    not running, and the run is marked interrupted rather than pretended
    about.
    """

    WINDOW = "measurement-window-overrun"
    REQUEST = "request-deadline-exceeded"

    def __init__(
        self,
        deadlines: Deadlines,
        *,
        stop: Callable[[str], None],
        escalate: Callable[[str], None] | None = None,
        finished: Callable[[], bool] = lambda: False,
        progress: Callable[[], Any] | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.deadlines = deadlines
        self._stop, self._escalate, self._finished = stop, escalate, finished
        # How the supervisor learns a request finished, without reaching into
        # the runner: whatever this returns, when it changes, is progress.
        self._progress_source, self._last_progress = progress, None
        self.poll_seconds = poll_seconds
        self._monotonic = monotonic
        self.outcome = SupervisorOutcome()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started_at: float | None = None
        self._progress_at: float | None = None
        # When the run itself says it began measuring. None until it says.
        self._measurement_at: float | None = None

    # The window is a backstop, not a second ceiling: the runner is given its
    # whole ceiling plus the grace a stop is allowed to take, and only then is
    # it stopped from outside. A supervisor that fired first would be silently
    # shortening every profile.
    @property
    def window_deadline(self) -> float | None:
        if self.deadlines.window_seconds is None:
            return None
        return self.deadlines.window_seconds + self.deadlines.shutdown_grace_seconds

    @property
    def anchor(self) -> float | None:
        """The moment the window is counted from.

        The runner's own ceiling starts at its first request, not at process
        start, and everything before that -- loading weights, building the
        dataset, the tokenizer -- belongs to neither the ceiling nor the
        grace. Counting from process start would quietly shorten every
        profile by however long its setup took.

        So the window is anchored to measurement start once the run reports
        one, and to supervisor start until then. Measurement start is never
        earlier than supervisor start, so this only ever moves the deadline
        later; and a run whose setup wedges before any measurement begins is
        still bounded, by the fallback.
        """
        return self._measurement_at if self._measurement_at is not None else self._started_at

    @property
    def anchored_to(self) -> str:
        return "measurement-start" if self._measurement_at is not None else "run-start"

    def note_progress(self) -> None:
        """Called when a request finishes. Resets the per-request deadline."""
        with self._lock:
            self._progress_at = self._monotonic()

    def _poll_progress(self) -> None:
        """Read the run's own account of itself: what it has done, and when
        it started doing it.

        The progress source returns `(processed_requests, measured_seconds)`.
        A change in the first is a request finishing, which resets the
        per-request deadline. The second anchors the window, and is read
        every time rather than once, so a source that only becomes available
        partway through still anchors correctly.
        """
        if self._progress_source is None:
            return
        try:
            current = self._progress_source()
        except Exception:  # noqa: BLE001 -- a progress source may not be there yet
            return
        if current is None:
            return
        processed, measured = current if isinstance(current, tuple) else (current, None)
        if processed is not None and processed != self._last_progress:
            self._last_progress = processed
            self.note_progress()
        if measured:
            implied = self._monotonic() - measured
            with self._lock:
                # The earliest implied start wins, and never moves again. A
                # snapshot that stops being written -- or one written by a
                # process that stalled -- would otherwise imply a later and
                # later start as real time went on, buying the run more
                # deadline the less it was doing.
                self._measurement_at = (implied if self._measurement_at is None
                                        else min(self._measurement_at, implied))

    def check(self) -> str | None:
        """One evaluation. Returns the reason it fired, or None."""
        if self._started_at is None or self.outcome.fired or self._finished():
            return None
        self._poll_progress()
        now = self._monotonic()
        elapsed = now - self.anchor
        window = self.window_deadline
        if window is not None and elapsed >= window:
            return self._fire(self.WINDOW, elapsed,
                              f"The run was still going {elapsed:.0f}s after measurement began, "
                              f"past its {self.deadlines.window_seconds:.0f}s ceiling and the "
                              f"{self.deadlines.shutdown_grace_seconds:.0f}s a stop is allowed "
                              "to take. Measured from "
                              + ("the run's own first request."
                                 if self._measurement_at is not None else
                                 "this process starting, because the run never reported a "
                                 "first request."))
        if self.deadlines.request_seconds is not None:
            with self._lock:
                since = now - (self._progress_at or self._started_at)
            if since >= self.deadlines.request_seconds:
                return self._fire(self.REQUEST, elapsed,
                                  f"No request completed for {since:.0f}s, past the "
                                  f"{self.deadlines.request_seconds:.0f}s a single request is "
                                  "allowed to take.")
        return None

    def _fire(self, reason: str, elapsed: float, detail: str) -> str:
        self.outcome = SupervisorOutcome(fired=True, reason=reason, at_seconds=elapsed,
                                         detail=detail, anchor=self.anchored_to)
        self._stop(reason)
        return reason

    def _loop(self) -> None:
        while not self._stop_event.wait(self.poll_seconds):
            if self.outcome.fired:
                self._wait_out_the_grace()
                return
            self.check()

    def _wait_out_the_grace(self) -> None:
        """Give the graceful stop its grace, then stop being polite."""
        deadline = self._monotonic() + self.deadlines.shutdown_grace_seconds
        while self._monotonic() < deadline:
            if self._finished() or self._stop_event.wait(self.poll_seconds):
                return
        if self._finished() or self._escalate is None:
            return
        self.outcome.escalated = True
        self._escalate(self.outcome.reason or "")

    def start(self) -> ExecutionSupervisor:
        self._started_at = self._monotonic()
        self._progress_at = self._started_at
        self._thread = threading.Thread(target=self._loop, name="omobench-supervisor",
                                        daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.deadlines.shutdown_grace_seconds + 5)
            self._thread = None
        # A supervisor that never fires still has to say what it was counting
        # from, or the field would report the default for every healthy run
        # and mean nothing. The first real smoke run recorded "run-start" on a
        # run that had anchored to its measurement start an hour earlier.
        self.outcome.anchor = self.anchored_to

    def __enter__(self) -> ExecutionSupervisor:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.close()


# -- keeping the machine awake ----------------------------------------------

KEEP_AWAKE_KIND = "omobench-keep-awake-v1"
KEEP_AWAKE_MECHANISM = "windows-power-request-v1"
KEEP_AWAKE_START_TIMEOUT_SECONDS = 15.0


def _unavailable(detail: str) -> dict[str, Any]:
    return {"requested": False, "held": False, "mechanism": KEEP_AWAKE_MECHANISM,
            "request": None, "detail": detail}


@dataclass
class KeepAwakeSession:
    """A Windows power request, held for exactly as long as this is open.

    The panel runs inside a WSL distribution, which cannot ask Windows for
    anything. The companion can, so this starts it in a mode that acquires a
    power request, prints what it got, and then holds it until its standard
    input reaches end of file.

    That last part is the whole lifetime design. There is no timeout to get
    wrong and no signal to miss: closing the pipe releases the request, and a
    panel that is killed outright closes the pipe by dying, so the request
    cannot outlive the run under any of success, failure, cancellation or
    termination.
    """

    process: Any = None
    status: dict[str, Any] = field(default_factory=lambda: _unavailable("not requested"))

    @property
    def held(self) -> bool:
        return bool(self.status.get("held"))

    def close(self) -> None:
        if self.process is None:
            return
        process, self.process = self.process, None
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=10)
        except Exception:  # noqa: BLE001 -- releasing must never fail a run
            with_suppressed(process.kill)
            with_suppressed(lambda: process.wait(timeout=5))

    def __enter__(self) -> KeepAwakeSession:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def with_suppressed(work: Callable[[], Any]) -> None:
    try:
        work()
    except Exception:  # noqa: BLE001
        pass


def sleep_prevention_availability(*, helper: Callable[[], str] | None = None) -> dict[str, Any]:
    """Whether anything here could ask Windows to stay awake, asked in advance.

    Deliberately does not acquire one. Holding a power request to find out
    whether a power request can be held would keep a laptop awake for as long
    as somebody left the page open, which is exactly the kind of thing a
    benchmarking tool should not do to a machine.
    """
    if helper is None:
        def helper():  # noqa: E306 -- the lazy import belongs here, not at module scope
            from .bridge import helper_path

            return helper_path()
    try:
        helper()
    except Exception as error:  # noqa: BLE001
        return {"available": False, "detail": str(error)}
    return {"available": True, "detail": ""}


def keep_the_machine_awake(*, helper: str | None = None,
                           popen: Callable[..., Any] | None = None) -> KeepAwakeSession:
    """Ask Windows to keep this computer awake while a benchmark measures.

    Returns a session whose `status` says what happened, because "the machine
    will stay awake", "we asked and Windows refused" and "there is nobody here
    to ask" are three different things a contributor may need to act on -- and
    a run proceeds under all three. Nothing here ever stops a benchmark.
    """
    import json
    import subprocess

    if helper is None:
        try:
            from .bridge import helper_path

            helper = helper_path()
        except Exception as error:  # noqa: BLE001
            return KeepAwakeSession(status=_unavailable(
                "This panel was not opened through the Windows companion, so nothing here can "
                f"ask Windows to stay awake ({error}). Leave this computer awake and on power "
                "for the whole run, or the measurement will record its sleep as slow inference."))

    runner = popen or subprocess.Popen
    try:
        process = runner([helper, "--keep-awake-v1"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError as error:
        return KeepAwakeSession(status=_unavailable(f"The companion did not start ({error})."))

    session = KeepAwakeSession(process=process)
    try:
        line = process.stdout.readline()
        document = json.loads(line)
        if not isinstance(document, dict) or document.get("kind") != KEEP_AWAKE_KIND:
            raise ValueError("unrecognised answer")
    except (OSError, ValueError, AttributeError) as error:
        session.close()
        return KeepAwakeSession(status=_unavailable(
            f"The companion did not say whether it could keep this computer awake ({error}). "
            "Leave it awake and on power for the whole run."))

    session.status = {
        "requested": True,
        "held": bool(document.get("held")),
        "mechanism": KEEP_AWAKE_MECHANISM,
        "request": document.get("request"),
        "detail": str(document.get("detail") or ""),
    }
    if not session.held:
        session.close()
    return session


@dataclass
class ExecutionRecord:
    """Everything a result says about how its run was bounded and interrupted."""

    deadlines: Deadlines
    interruption: dict[str, Any] = field(default_factory=lambda: {"state": NONE})
    supervisor: SupervisorOutcome = field(default_factory=SupervisorOutcome)
    sleep_prevention: dict[str, Any] | None = None

    @property
    def interrupted(self) -> bool:
        """Confirmed only. A gap nobody can account for is not a conclusion."""
        return self.interruption.get("state") == SUSPENDED

    @property
    def unverified(self) -> bool:
        """The machine cannot be shown to have been running throughout.

        Weaker than `interrupted` and separate from it, because the thing it
        reports is an absence of evidence rather than evidence of an absence.
        It is still enough to keep a measurement out of the registry.
        """
        return self.interruption.get("state") in {SUSPENDED, CLOCK_JUMP}

    def document(self) -> dict[str, Any]:
        return {
            "contract": EXECUTION_CONTRACT,
            "limits": self.deadlines.document(),
            "supervisor": self.supervisor.document(),
            "interruption": self.interruption,
            **({"sleep_prevention": self.sleep_prevention}
               if self.sleep_prevention is not None else {}),
        }
