"""Live telemetry for a running GuideLLM benchmark, for the UI dashboard only.

This module never changes what gets measured. It resolves the *exact* same
configuration `build_guidellm_command()` already builds -- backend, workload,
profile, constraints, seed, metrics, and output behavior -- through GuideLLM
0.7.3's own CLI argument-resolution code (no re-implementation of that
parsing), then runs its official `benchmark_generative_text()` entrypoint
in-process instead of as a subprocess, purely so a `BenchmarkerProgress`
observer (a public, documented GuideLLM interface) can be attached. The
observer only reads already-computed, cheap running statistics; it never
drives the scheduler, alters constraints, or changes what gets measured.

Final results are unaffected: `write_local_run()` still normalizes the exact
same `guidellm-benchmarks.json.gz` report through the existing, unchanged
`normalize_guidellm_report()`. This module only adds a second, small,
best-effort side channel (a bounded snapshot + history file) that a live
dashboard can poll; if anything here fails, the caller falls back to the
plain subprocess path GuideLLM's own CLI would take, so a telemetry problem
can disable live monitoring but never the benchmark itself.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# GuideLLM stage names, mapped to the guided page's own vocabulary. GuideLLM's
# own accumulator.timings.status only distinguishes these four; "starting"
# covers the moment before the first request is even sent.
STAGE_BY_TIMINGS_STATUS = {
    "pending": "starting",
    "warmup": "warmup",
    "active": "benchmarking",
    "cooldown": "cooldown",
}
TELEMETRY_SCHEMA_VERSION = 1
DEFAULT_MIN_EMIT_INTERVAL_SECONDS = 1.0
DEFAULT_HISTORY_MAXLEN = 1800  # ~30 minutes at one accepted sample/second


def argv_for_run_command(command: list[str]) -> list[str]:
    """Strip the leading "guidellm run" from `build_guidellm_command()`'s
    output, leaving exactly the option list `resolve_benchmark_scenario`
    (and GuideLLM's own `run` Click command) expects. Kept as one small,
    explicit function so the "same argv" relationship between the plain
    subprocess path and this module's in-process path is not left implicit
    in test code or call sites."""
    if command[:2] != ["guidellm", "run"]:
        raise ValueError(f"Expected a 'guidellm run ...' command, got: {command[:2]!r}")
    return command[2:]


class TelemetryUnavailable(Exception):
    """Raised when the pinned GuideLLM package cannot support telemetry here.

    Never raised for a problem with the benchmark itself -- only for a setup
    problem with attaching the observer (missing/incompatible GuideLLM
    internals). The caller is expected to fall back to the plain subprocess
    path, so this never blocks a benchmark from running.
    """


# --------------------------------------------------------------------------
# Configuration resolution: the same argv, resolved through GuideLLM's own
# CLI code, never OmoBench's own re-implementation of it.
# --------------------------------------------------------------------------


def resolve_benchmark_scenario(argv: list[str]) -> Any:
    """Resolve `argv` (the same list `build_guidellm_command()` builds, minus
    its leading "guidellm"/"run") into GuideLLM's own `BenchmarkScenario`.

    Runs GuideLLM 0.7.3's real `guidellm run` Click command up to -- but not
    including -- the point where it would start executing, by intercepting
    its call to `benchmark_generative_text` and capturing the arguments it
    was about to pass. This reuses GuideLLM's own option parsing, registry
    resolution, and scenario construction verbatim: nothing about workload,
    backend, profile, constraints, seed, metrics, or output resolution is
    re-implemented here. Raises TelemetryUnavailable if the pinned package's
    internals do not match what this function depends on.
    """
    try:
        import sys as _sys

        import guidellm.cli  # noqa: F401  (populates guidellm.cli.run below)

        run_command = guidellm.cli.run
        run_submodule = _sys.modules["guidellm.cli.run"]
    except (ImportError, AttributeError, KeyError) as error:
        raise TelemetryUnavailable(f"Could not import GuideLLM's CLI internals: {error}") from error

    captured: dict[str, Any] = {}

    class _Captured(Exception):
        pass

    def _capture(*, args: Any, **_ignored: Any) -> None:
        captured["args"] = args
        raise _Captured()

    original = getattr(run_submodule, "benchmark_generative_text", None)
    if original is None:
        raise TelemetryUnavailable("guidellm.cli.run no longer references benchmark_generative_text.")

    try:
        context = run_command.make_context("run", list(argv), resilient_parsing=False)
    except Exception as error:  # noqa: BLE001 - any Click parsing failure means "can't resolve"
        raise TelemetryUnavailable(f"GuideLLM could not parse its own arguments: {error}") from error

    run_submodule.benchmark_generative_text = _capture
    try:
        context.invoke(run_command.callback, **context.params)
    except _Captured:
        pass
    except Exception as error:  # noqa: BLE001
        raise TelemetryUnavailable(f"GuideLLM's own argument resolution failed: {error}") from error
    finally:
        run_submodule.benchmark_generative_text = original

    if "args" not in captured:
        raise TelemetryUnavailable("GuideLLM did not reach benchmark_generative_text during resolution.")
    return captured["args"]


def scenario_equivalence_summary(scenario: Any) -> dict[str, Any]:
    """A small, comparable summary of a resolved BenchmarkScenario's
    meaningful fields, for the configuration-equivalence test -- never used
    to construct or alter a real command."""
    benchmarks = scenario.get_benchmarks()
    if len(benchmarks) != 1:
        raise TelemetryUnavailable(f"Expected exactly one resolved benchmark, got {len(benchmarks)}.")
    benchmark = benchmarks[0]
    backend = benchmark.backend
    data = benchmark.data[0] if benchmark.data else None
    profile = benchmark.profile
    return {
        "backend_target": backend.target,
        "backend_model": backend.model,
        "backend_request_format": backend.request_format,
        "prompt_tokens": getattr(data, "prompt_tokens", None),
        "output_tokens": getattr(data, "output_tokens", None),
        "profile_kind": profile.kind,
        "warmup": profile.warmup.model_dump() if profile.warmup else None,
        "cooldown": profile.cooldown.model_dump() if profile.cooldown else None,
        "constraints": sorted(
            (constraint.kind, json.dumps(constraint.model_dump(), sort_keys=True, default=str, separators=(",", ":")))
            for constraint in benchmark.constraints
        ),
        "seed": benchmark.seed.model_dump() if benchmark.seed else None,
        "metrics_kind": benchmark.metrics.kind,
        "outputs": sorted(
            (output.kind, str(getattr(output, "path", ""))) for output in benchmark.outputs
        ),
    }


# --------------------------------------------------------------------------
# Snapshot + bounded history I/O
# --------------------------------------------------------------------------


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@dataclass
class TelemetryWriter:
    """Rate-limited, bounded, atomic telemetry sink.

    Writes at most once per `min_interval` seconds (except the very first
    sample, emitted immediately so the dashboard is never stuck blank), and
    keeps only the last `history_maxlen` accepted samples -- small and
    bounded, never a full accumulator dump. A write failure here is caught
    and recorded, never raised into the caller: see `disabled`.
    """

    snapshot_path: Path
    history_path: Path | None = None
    min_interval: float = DEFAULT_MIN_EMIT_INTERVAL_SECONDS
    history_maxlen: int = DEFAULT_HISTORY_MAXLEN
    _last_emit_monotonic: float | None = field(default=None, init=False)
    _last_elapsed_seen: float | None = field(default=None, init=False)
    _history: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_HISTORY_MAXLEN), init=False)
    disabled: bool = field(default=False, init=False)
    disabled_reason: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self._history.maxlen != self.history_maxlen:
            self._history = deque(self._history, maxlen=self.history_maxlen)

    def should_emit(self, *, force: bool = False) -> bool:
        if force or self._last_emit_monotonic is None:
            return True
        return (time.monotonic() - self._last_emit_monotonic) >= self.min_interval

    def record(self, snapshot: dict[str, Any], *, chart_sample: dict[str, Any] | None = None, force: bool = False) -> None:
        """Write `snapshot` (the latest-state file) and, if given and not a
        duplicate elapsed timestamp, append `chart_sample` to the bounded
        history. Never raises: sets `disabled`/`disabled_reason` instead."""
        if self.disabled:
            return
        if not self.should_emit(force=force):
            return
        try:
            if chart_sample is not None:
                sample_elapsed = chart_sample.get("elapsed_seconds")
                if sample_elapsed is None or sample_elapsed != self._last_elapsed_seen:
                    self._history.append(chart_sample)
                    self._last_elapsed_seen = sample_elapsed
            snapshot = {**snapshot, "chart_history": list(self._history)}
            _atomic_write_json(self.snapshot_path, snapshot)
            if self.history_path is not None:
                _atomic_write_json(self.history_path, {"schema": TELEMETRY_SCHEMA_VERSION, "samples": list(self._history)})
            self._last_emit_monotonic = time.monotonic()
        except OSError as error:
            self.disabled = True
            self.disabled_reason = f"Could not write telemetry: {error}"

    def record_failure(self, reason: str) -> None:
        """Best-effort: tell the dashboard live monitoring stopped, without
        ever raising back into GuideLLM's own execution loop."""
        self.disabled = True
        self.disabled_reason = reason
        try:
            _atomic_write_json(
                self.snapshot_path,
                {
                    "schema": TELEMETRY_SCHEMA_VERSION,
                    "observer_error": reason,
                    "updated_at": time.time(),
                },
            )
        except OSError:
            pass


# --------------------------------------------------------------------------
# ETA estimation -- a pure function so it is independently testable.
# --------------------------------------------------------------------------


def compute_eta_seconds(
    *,
    processed_requests: float | None,
    elapsed_since_first_request: float | None,
    remaining_requests: float | None,
    remaining_duration: float | None,
    min_observations: int = 3,
    min_elapsed_seconds: float = 2.0,
) -> float | None:
    """Estimate seconds remaining from the *observed* completion rate,
    capped by the remaining time-limit estimate GuideLLM itself reports.

    Returns None ("Estimating...") until there are enough real observations
    -- never extrapolates from a single sample, never returns a negative or
    infinite value, and never reports the duration ceiling alone as the
    estimate (a report of "5400s remaining" the instant a 90-minute-ceiling
    run starts would just be restating the ceiling, not an estimate).
    """
    if (
        processed_requests is None
        or elapsed_since_first_request is None
        or remaining_requests is None
        or remaining_requests < 0
        or processed_requests < min_observations
        or elapsed_since_first_request < min_elapsed_seconds
    ):
        return None
    rate = processed_requests / elapsed_since_first_request
    if rate <= 0:
        return None
    eta = remaining_requests / rate
    if remaining_duration is not None and remaining_duration >= 0:
        eta = min(eta, remaining_duration)
    return max(0.0, eta)


# --------------------------------------------------------------------------
# The observer itself
# --------------------------------------------------------------------------


class OmoBenchTelemetryProgress:
    """Observe a running GuideLLM benchmark; never control it.

    Implements GuideLLM's public `BenchmarkerProgress` interface (subclassed
    lazily in `_progress_class()` below, once GuideLLM is confirmed
    importable) by reading only already-computed, O(1) running statistics
    already exposed on `SchedulerState`/`GenerativeBenchmarkAccumulator` --
    never calling any "compile final metrics" method, never serializing the
    full accumulator, and never touching scheduler control fields. Every
    hook is wrapped so an internal failure here disables further telemetry
    (visibly, via TelemetryWriter.record_failure) without ever raising back
    into GuideLLM's own execution loop.
    """

    def __init__(self, writer: TelemetryWriter, *, run_label: str, total_measured_requests: int | None):
        self.writer = writer
        self.run_label = run_label
        self.total_measured_requests = total_measured_requests
        self._started_monotonic: float | None = None
        self._last_requests: dict[str, Any] | None = None

    def _safe(self, work: Callable[[], None]) -> None:
        if self.writer.disabled:
            return
        try:
            work()
        except Exception as error:  # noqa: BLE001 - must never propagate into GuideLLM
            self.writer.record_failure(f"Live telemetry stopped: {error}")

    async def on_initialize(self, profile: Any) -> None:
        self._safe(lambda: self._emit_initialize())

    def _emit_initialize(self) -> None:
        self._started_monotonic = time.monotonic()
        self.writer.record(
            {
                "schema": TELEMETRY_SCHEMA_VERSION,
                "run_id": self.run_label,
                "updated_at": time.time(),
                "stage": "starting",
                "elapsed_seconds": 0.0,
                "requests": {"processed": 0, "successful": 0, "errored": 0, "cancelled": 0, "total_budget": None},
                "measured_requests_target": self.total_measured_requests,
                "remaining_fraction": None,
                "remaining_seconds_duration_limit": None,
                "eta_seconds": None,
                "observer_error": None,
            },
            force=True,
        )

    async def on_benchmark_start(self, strategy: Any) -> None:
        self._safe(lambda: self.writer.record(self._current_snapshot_locked(stage="starting"), force=True))

    async def on_benchmark_update(self, accumulator: Any, scheduler_state: Any) -> None:
        self._safe(lambda: self._emit_update(accumulator, scheduler_state))

    def _emit_update(self, accumulator: Any, scheduler_state: Any) -> None:
        if not self.writer.should_emit():
            return
        timings = accumulator.timings
        stage = STAGE_BY_TIMINGS_STATUS.get(timings.status, "benchmarking")
        elapsed = timings.duration
        progress = scheduler_state.progress

        output_tokens_stats = accumulator.completed_metrics.output_tokens
        throughput = output_tokens_stats.rate_per_second

        eta = compute_eta_seconds(
            processed_requests=scheduler_state.processed_requests,
            elapsed_since_first_request=elapsed,
            remaining_requests=progress.remaining_requests,
            remaining_duration=progress.remaining_duration,
        )

        chart_sample = None
        if throughput is not None:
            chart_sample = {"elapsed_seconds": round(elapsed, 2), "output_tokens_per_second": throughput}

        self._last_requests = {
            "processed": scheduler_state.processed_requests,
            "successful": scheduler_state.successful_requests,
            "errored": scheduler_state.errored_requests,
            "cancelled": scheduler_state.cancelled_requests,
            "total_budget": progress.total_requests,
        }
        self.writer.record(
            {
                "schema": TELEMETRY_SCHEMA_VERSION,
                "run_id": self.run_label,
                "updated_at": time.time(),
                "stage": stage,
                "elapsed_seconds": elapsed,
                "requests": self._last_requests,
                "measured_requests_target": self.total_measured_requests,
                "remaining_fraction": progress.remaining_fraction,
                "remaining_seconds_duration_limit": progress.remaining_duration,
                "eta_seconds": eta,
                "latest_metrics": {
                    "output_tokens_per_second": throughput,
                    "time_to_first_token_ms": accumulator.completed_metrics.time_to_first_token_ms.mean,
                    "request_latency_seconds": accumulator.completed_metrics.request_latency.mean,
                },
                "observer_error": None,
            },
            chart_sample=chart_sample,
        )

    async def on_benchmark_complete(self, benchmark: Any) -> None:
        def _work() -> None:
            # Read from the already-compiled final benchmark, once per
            # strategy (not per-request), so this never adds hot-path cost:
            # benchmark.scheduler_state for the final full-lifecycle counts
            # (in case rate-limiting meant the last update() call was stale),
            # and benchmark.metrics.request_totals for the real measured-
            # window figures the existing normalizer itself reports from.
            final_state = benchmark.scheduler_state
            totals = benchmark.metrics.request_totals
            self._last_requests = {
                "processed": final_state.processed_requests,
                "successful": final_state.successful_requests,
                "errored": final_state.errored_requests,
                "cancelled": final_state.cancelled_requests,
                "total_budget": final_state.progress.total_requests,
                "measured_successful": totals.successful,
                "measured_failed": totals.incomplete + totals.errored,
                "measured_attempted": totals.total,
            }
            self.writer.record(self._terminal_snapshot(stage="saving"), force=True)

        self._safe(_work)

    async def on_finalize(self) -> None:
        self._safe(lambda: self.writer.record(self._terminal_snapshot(stage="saving"), force=True))

    def _current_snapshot_locked(self, *, stage: str) -> dict[str, Any]:
        return {
            "schema": TELEMETRY_SCHEMA_VERSION,
            "run_id": self.run_label,
            "updated_at": time.time(),
            "stage": stage,
            "elapsed_seconds": 0.0,
            "requests": {"processed": 0, "successful": 0, "errored": 0, "cancelled": 0, "total_budget": None},
            "measured_requests_target": self.total_measured_requests,
            "remaining_fraction": None,
            "remaining_seconds_duration_limit": None,
            "eta_seconds": None,
            "observer_error": None,
        }

    def _terminal_snapshot(self, *, stage: str) -> dict[str, Any]:
        elapsed = (time.monotonic() - self._started_monotonic) if self._started_monotonic is not None else None
        return {
            "schema": TELEMETRY_SCHEMA_VERSION,
            "run_id": self.run_label,
            "updated_at": time.time(),
            "stage": stage,
            "elapsed_seconds": elapsed,
            "requests": self._last_requests,
            "measured_requests_target": self.total_measured_requests,
            "remaining_fraction": 0.0,
            "remaining_seconds_duration_limit": None,
            "eta_seconds": None,
            "observer_error": None,
        }


def _progress_base_class() -> type:
    """Import GuideLLM's abstract BenchmarkerProgress lazily, so importing
    this module never requires GuideLLM to be installed."""
    from guidellm.benchmark.progress import BenchmarkerProgress

    return BenchmarkerProgress


def build_progress_observer(writer: TelemetryWriter, *, run_label: str, total_measured_requests: int | None):
    """Construct a real `BenchmarkerProgress` subclass instance. Deferred
    until GuideLLM is confirmed available, per `_progress_base_class`."""
    base = _progress_base_class()
    # Multiple inheritance at call time: OmoBenchTelemetryProgress already
    # implements every abstract method BenchmarkerProgress declares: it just
    # is not declared a subclass until here, keeping this module importable
    # without GuideLLM installed.
    combined = type("OmoBenchTelemetryProgressImpl", (OmoBenchTelemetryProgress, base), {})
    instance = combined(writer, run_label=run_label, total_measured_requests=total_measured_requests)
    base.__init__(instance)
    return instance


# --------------------------------------------------------------------------
# Real execution: resolve, attach the observer, run in-process.
# --------------------------------------------------------------------------


def _run_coroutine_cancellable_by_sigterm(coroutine: Any) -> str:
    """Run `coroutine` to completion on a fresh event loop, registering a
    real asyncio-level SIGTERM handler (`loop.add_signal_handler`) that
    cancels the running task -- the textbook-correct way to interrupt an
    asyncio application with a POSIX signal, and one that works whether the
    active loop is the default asyncio loop or GuideLLM's own uvloop policy
    (a plain `signal.signal()` handler is not reliably honored once uvloop's
    C-level loop owns the process's signal handling).

    :return: "completed" or "cancelled".
    """
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(coroutine)
    signal_supported = sys.platform != "win32"
    if signal_supported:
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        loop.run_until_complete(task)
        return "completed"
    except asyncio.CancelledError:
        return "cancelled"
    finally:
        if signal_supported:
            loop.remove_signal_handler(signal.SIGTERM)
        asyncio.set_event_loop(None)
        loop.close()


def run_generative_benchmark_with_telemetry(
    argv: list[str],
    *,
    snapshot_path: Path,
    history_path: Path,
    stdout_log_path: Path,
    run_label: str,
    total_measured_requests: int | None,
    min_emit_interval: float = DEFAULT_MIN_EMIT_INTERVAL_SECONDS,
    history_maxlen: int = DEFAULT_HISTORY_MAXLEN,
) -> int:
    """Run the exact benchmark `argv` describes, in-process, with a live
    telemetry observer attached. Returns a subprocess-style exit code (0 on
    success, 130 on cancellation, nonzero otherwise) so callers can treat it
    exactly like `subprocess.run(...).returncode`.

    Raises TelemetryUnavailable only for a setup-time problem (before any
    request is sent) -- the caller is expected to fall back to the plain
    subprocess path in that case, so a telemetry-adapter problem never
    prevents the benchmark from running.
    """
    import guidellm.cli  # noqa: F401
    from guidellm.utils.console import Console

    scenario = resolve_benchmark_scenario(argv)
    real_entrypoint = sys.modules["guidellm.benchmark.entrypoints"].benchmark_generative_text

    writer = TelemetryWriter(
        snapshot_path=snapshot_path,
        history_path=history_path,
        min_interval=min_emit_interval,
        history_maxlen=history_maxlen,
    )
    observer = build_progress_observer(writer, run_label=run_label, total_measured_requests=total_measured_requests)

    with stdout_log_path.open("w", encoding="utf-8") as log_file:
        console = Console(file=log_file)
        try:
            outcome = _run_coroutine_cancellable_by_sigterm(
                real_entrypoint(args=scenario, progress=observer, console=console)
            )
        except Exception as error:  # noqa: BLE001
            log_file.write(f"\nOmoBench: benchmark execution failed: {error}\n")
            return 1
        if outcome == "cancelled":
            return 130
    return 0
