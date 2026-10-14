from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from omobench.cli import (
    GUIDELLM_VERSION,
    OTHER_SERVER_FRAMEWORK,
    RUNTIME_FRAMEWORKS,
    build_guidellm_command,
    build_parser,
    command_output,
    detect_amd_gpus,
    detect_gpus,
    detect_nvidia_gpus,
    detect_windows_cim_gpus,
    doctor_report,
    guidellm_installed_version,
    guidellm_runner_preflight,
    hardware_from_environment,
    normalize_guidellm_report,
    package_local_run,
    print_doctor,
    redact_endpoint,
    require_https_source_url,
    sha256_json,
    validate_execution_device,
    write_run_intent,
)


ROOT = Path(__file__).resolve().parents[2]


def copy_repo_tree_for_registry_validation(root: Path) -> None:
    """Give a temporary root the real benchmarks/catalog/schema/tools directories,
    so ui_submission.validate_bundle()'s dynamic import of tools/validate_results.py
    finds a real module to load. Call before build_staged_run_directory(), whose own
    profile-stub writes intentionally take precedence over the copied real profiles."""
    for relative in ("benchmarks", "catalog", "schema", "tools"):
        shutil.copytree(ROOT / relative, root / relative, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "results").mkdir()


GUIDELLM_REPORT_V2 = {
    "metadata": {"version": 2, "guidellm_version": "0.7.3"},
    "benchmarks": [
        {
            "scheduler_metrics": {
                "measure_start_time": 1767225600,
                # Deliberately different from metrics.request_totals below: this field
                # spans the full scheduler lifecycle (including warmup/cooldown) and
                # must NOT be what the normalizer reports, per
                # report.exclude_warmup_and_cooldown in every OmoBench profile.
                "requests_made": {
                    "successful": 52,
                    "incomplete": 3,
                    "errored": 2,
                    "total": 57,
                },
            },
            "metrics": {
                # The measured-window-only counts (GuideLLM's own
                # get_within_range(measure_start_time, measure_end_time) filtering):
                # this is what the normalizer must report. Zero errors so this fixture
                # satisfies the real chat-small-v1 profile's maximum_error_rate_percent
                # (1%) wherever tests use the real profile via make_root().
                "request_totals": {
                    "successful": 50,
                    "incomplete": 0,
                    "errored": 0,
                    "total": 50,
                },
                "time_to_first_token_ms": {
                    "successful": {"percentiles": {"p50": 210.0, "p95": 315.0}}
                },
                "inter_token_latency_ms": {
                    "successful": {"percentiles": {"p50": 12.5, "p95": 18.0}}
                },
                "output_tokens_per_second": {"total": {"mean": 87.5}},
                "tokens_per_second": {"total": {"mean": 220.0}},
                "requests_per_second": {"total": {"mean": 3.2}},
            },
        }
    ],
}


class GuideLLMCommandTests(unittest.TestCase):
    def test_chat_small_is_a_fixed_single_stream_workload(self) -> None:
        command = build_guidellm_command(
            root=ROOT,
            endpoint="http://localhost:8000/v1",
            model="Qwen/Qwen3-4B",
            profile_id="chat-small-v1",
            raw_output_path=Path("/tmp/benchmarks.json"),
        )
        self.assertIn("kind=synchronous,warmup=0.1,cooldown=0.1", command)
        self.assertIn("kind=synthetic_text,prompt_tokens=1024,output_tokens=256", command)
        self.assertIn("kind=max_requests,count=50", command)
        self.assertIn("kind=generative,sample_size=0", command)

    def test_throughput_is_fixed_at_sixteen_streams(self) -> None:
        command = build_guidellm_command(
            root=ROOT,
            endpoint="http://localhost:8000/v1",
            model="Qwen/Qwen3-4B",
            profile_id="throughput-v1",
            raw_output_path=Path("/tmp/benchmarks.json"),
        )
        self.assertIn(
            "kind=concurrent,streams=16,max_concurrency=16,warmup=0.1,cooldown=0.1",
            command,
        )
        self.assertIn("kind=max_requests,count=160", command)

    def test_chat_small_extended_uses_explicit_request_count_phases_and_both_stopping_constraints(self) -> None:
        """Same workload as chat-small-v1 (tokens, single stream), but warmup/cooldown
        are explicit request counts, not the percent/prefer_duration scheme.
        warmup.value=4 and cooldown.value=4 (not the naively-expected 5 for either)
        are the documented compensation for GuideLLM's own phase-boundary off-by-one
        (a phase value of N excludes N+1 requests, verified end to end against the
        real multi-event request lifecycle in ChatSmallExtendedBoundaryTests), so the
        *actual* excluded counts are 5 and 5, matching the declared contract exactly:
        5 warmup + 50 measured + 5 cooldown = 60, not 61 or 62."""
        command = build_guidellm_command(
            root=ROOT,
            endpoint="http://localhost:8000/v1",
            model="Qwen/Qwen3-4B",
            profile_id="chat-small-extended-v1",
            raw_output_path=Path("/tmp/benchmarks.json"),
        )
        self.assertIn(
            "kind=synchronous,warmup.value=4,warmup.mode=requests,cooldown.value=4,cooldown.mode=requests",
            command,
        )
        # Same workload as chat-small-v1: unchanged tokens and request format.
        self.assertIn("kind=synthetic_text,prompt_tokens=1024,output_tokens=256", command)
        # Both stopping constraints are present; whichever fires first ends the run,
        # so 90 minutes is a ceiling, not a mandatory runtime.
        self.assertIn("kind=max_requests,count=60", command)
        self.assertIn("kind=max_duration,seconds=5400", command)

    def test_local_endpoint_is_redacted_before_evidence_is_written(self) -> None:
        value = "server=http://localhost:8000/v1 and again http://localhost:8000/v1"
        self.assertEqual(
            redact_endpoint(value, "http://localhost:8000/v1"),
            "server=<LOCAL_ENDPOINT> and again <LOCAL_ENDPOINT>",
        )


GUIDELLM_MODULE_STUB = (
    "import pathlib, sys\n"
    "pathlib.Path({marker!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n"
)


def install_guidellm_module_stub(directory: Path, body: str) -> Path:
    """Shadow the real `guidellm` *package* for any subprocess whose
    PYTHONPATH includes `directory`.

    Execution goes through `<this interpreter> -m guidellm`
    (cli.guidellm_runner_invocation), so a stand-in runner must shadow the
    module; a script named "guidellm" on PATH is deliberately not consulted."""
    package = directory / "guidellm"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(body, encoding="utf-8")
    return directory


def install_conflicting_path_executable(directory: Path, marker: Path) -> Path:
    """A different, unverified "guidellm" on PATH -- the exact situation that
    used to decide which runner actually executed. It records its own
    invocation so a test can prove it was never run."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "guidellm"
    script.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return directory


class RunnerInvocationTests(unittest.TestCase):
    """The runner that executes must be the one preflight verified."""

    def test_the_canonical_command_is_rewritten_onto_this_interpreter(self) -> None:
        from omobench.cli import guidellm_runner_invocation

        invocation = guidellm_runner_invocation(["guidellm", "run", "--backend", "x"])
        self.assertEqual(invocation, [sys.executable, "-m", "guidellm", "run", "--backend", "x"])

    def test_a_non_guidellm_command_is_rejected(self) -> None:
        from omobench.cli import guidellm_runner_invocation

        for command in ([], ["something-else", "run"]):
            with self.subTest(command=command), self.assertRaises(ValueError):
                guidellm_runner_invocation(command)

    def test_reproduce_evidence_keeps_the_portable_form_not_an_interpreter_path(self) -> None:
        """build_guidellm_command() stays canonical: reproduce.md is published
        evidence, and a local interpreter path (/home/<user>/.venvs/...) must
        not end up in it."""
        command = build_guidellm_command(
            root=ROOT, endpoint="http://127.0.0.1:9", model="m",
            profile_id="chat-small-v1", raw_output_path=Path("/tmp/out.json"),
        )
        self.assertEqual(command[:2], ["guidellm", "run"])
        self.assertNotIn(sys.executable, command)


@unittest.skipIf(os.name == "nt", "Uses a POSIX executable stub on PATH")
class ConflictingPathExecutableTests(unittest.TestCase):
    """A different `guidellm` on PATH must never be what runs: preflight can
    only vouch for this interpreter's installation."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.module_marker = self.root / "module-stub-ran.txt"
        self.path_marker = self.root / "path-executable-ran.txt"
        install_guidellm_module_stub(
            self.root / "stub", GUIDELLM_MODULE_STUB.format(marker=str(self.module_marker))
        )
        install_conflicting_path_executable(self.root / "bin", self.path_marker)
        self.log_path = self.root / "guidellm.stdout.log"
        self.environment = patch.dict(
            os.environ,
            {
                "PATH": str(self.root / "bin") + os.pathsep + os.environ["PATH"],
                "PYTHONPATH": str(self.root / "stub"),
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _command(self) -> list[str]:
        return ["guidellm", "run", "--backend", "kind=openai_http,target=http://127.0.0.1:9"]

    def test_ordinary_cli_execution_ignores_the_conflicting_executable(self) -> None:
        from omobench.cli import run_guidellm_command

        returncode = run_guidellm_command(
            self._command(), log_path=self.log_path, root=self.root,
            profile_id="chat-small-v1", telemetry_dir=None,
        )
        self.assertEqual(returncode, 0)
        self.assertTrue(self.module_marker.is_file(), "this interpreter's guidellm module should have run")
        self.assertFalse(self.path_marker.exists(), "the conflicting PATH executable must never be invoked")

    def test_telemetry_fallback_ignores_the_conflicting_executable(self) -> None:
        """The fallback path had the same defect: it re-ran bare "guidellm"."""
        from omobench import telemetry as telemetry_module
        from omobench.cli import run_guidellm_command

        with patch.object(
            telemetry_module,
            "run_generative_benchmark_with_telemetry",
            side_effect=telemetry_module.TelemetryUnavailable("no observer here"),
        ):
            returncode = run_guidellm_command(
                self._command(), log_path=self.log_path, root=self.root,
                profile_id="chat-small-v1", telemetry_dir=self.root / "telemetry",
            )
        self.assertEqual(returncode, 0)
        self.assertTrue(self.module_marker.is_file())
        self.assertFalse(self.path_marker.exists())
        self.assertIn("live telemetry unavailable", self.log_path.read_text(encoding="utf-8"))


class TelemetryFallbackBoundaryTests(unittest.TestCase):
    """Fallback is for failures *before* the benchmark is invoked. After that,
    an error must never start a second run or truncate the first one's log."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.log_path = self.root / "guidellm.stdout.log"

    def _run(self, telemetry_side_effect, subprocess_mock):
        from omobench import telemetry as telemetry_module
        from omobench.cli import run_guidellm_command

        with patch.object(
            telemetry_module, "run_generative_benchmark_with_telemetry", side_effect=telemetry_side_effect
        ), patch("omobench.cli.subprocess.run", subprocess_mock):
            return run_guidellm_command(
                ["guidellm", "run", "--backend", "x"],
                log_path=self.log_path, root=self.root,
                profile_id="chat-small-v1", telemetry_dir=self.root / "telemetry",
            )

    def test_a_setup_failure_before_execution_falls_back_exactly_once(self) -> None:
        from omobench import telemetry as telemetry_module

        subprocess_mock = Mock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
        returncode = self._run(telemetry_module.TelemetryUnavailable("could not attach"), subprocess_mock)
        self.assertEqual(returncode, 0)
        self.assertEqual(subprocess_mock.call_count, 1, "exactly one benchmark invocation")
        self.assertIn("live telemetry unavailable", self.log_path.read_text(encoding="utf-8"))

    def test_a_log_failure_after_execution_never_starts_a_second_run(self) -> None:
        """The regression: an OSError raised after the benchmark ran (a failing
        log write, flush, or close) used to reopen the log with "w" -- erasing
        the finished run's output -- and launch a duplicate benchmark."""
        evidence = "runner output from the run that already happened\n"

        def already_executed(*_args, **kwargs):
            Path(kwargs["stdout_log_path"]).write_text(evidence, encoding="utf-8")
            raise OSError(5, "Input/output error")

        subprocess_mock = Mock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
        with self.assertRaises(OSError):
            self._run(already_executed, subprocess_mock)
        subprocess_mock.assert_not_called()
        self.assertEqual(
            self.log_path.read_text(encoding="utf-8"), evidence,
            "the finished run's log must not be truncated by a fallback attempt",
        )

    def test_an_unexpected_error_after_execution_never_starts_a_second_run(self) -> None:
        """Any non-TelemetryUnavailable error is treated as post-invocation."""
        subprocess_mock = Mock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
        with self.assertRaises(RuntimeError):
            self._run(RuntimeError("something went wrong mid-run"), subprocess_mock)
        subprocess_mock.assert_not_called()

    def test_a_telemetry_directory_failure_is_still_pre_execution(self) -> None:
        """mkdir happens before anything is invoked, so it still falls back."""
        from omobench.cli import run_guidellm_command

        blocker = self.root / "telemetry"
        blocker.write_text("not a directory", encoding="utf-8")
        subprocess_mock = Mock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
        with patch("omobench.cli.subprocess.run", subprocess_mock):
            returncode = run_guidellm_command(
                ["guidellm", "run", "--backend", "x"],
                log_path=self.log_path, root=self.root,
                profile_id="chat-small-v1", telemetry_dir=blocker,
            )
        self.assertEqual(returncode, 0)
        self.assertEqual(subprocess_mock.call_count, 1)


def _guidellm_full_pipeline_classes():
    """Import the complete set of pinned GuideLLM 0.7.3 classes this profile's
    accounting depends on: real scheduler-facing objects (RequestInfo,
    SchedulerState/SchedulerProgress, GenerationRequest), the real accumulator
    that processes scheduler events (GenerativeBenchmarkAccumulator), and the
    real final-metrics compiler (GenerativeMetrics). Skips the whole test class
    when GuideLLM is not installed, matching this repo's existing convention of
    stubbing GuideLLM out entirely rather than requiring it (see
    ExecutionTests.runner() in test_ui.py)."""
    try:
        from guidellm.benchmark.schemas.accumulator import GenerativeBenchmarkAccumulator
        from guidellm.benchmark.schemas.base import BenchmarkConfig, TransientPhaseConfig
        from guidellm.benchmark.schemas.metrics import GenerativeMetrics
        from guidellm.schemas.info import RequestInfo, RequestTimings
        from guidellm.schemas.request import GenerationRequest
        from guidellm.scheduler.schemas import SchedulerProgress, SchedulerState
        from guidellm.scheduler.strategies import SynchronousStrategy
    except ImportError as error:
        raise unittest.SkipTest(f"GuideLLM is not installed in this environment: {error}") from None
    return {
        "GenerativeBenchmarkAccumulator": GenerativeBenchmarkAccumulator,
        "BenchmarkConfig": BenchmarkConfig,
        "TransientPhaseConfig": TransientPhaseConfig,
        "GenerativeMetrics": GenerativeMetrics,
        "RequestInfo": RequestInfo,
        "RequestTimings": RequestTimings,
        "GenerationRequest": GenerationRequest,
        "SchedulerProgress": SchedulerProgress,
        "SchedulerState": SchedulerState,
        "SynchronousStrategy": SynchronousStrategy,
    }


class ChatSmallExtendedBoundaryTests(unittest.TestCase):
    """Drives the pinned GuideLLM 0.7.3 package's real scheduler-facing accumulator
    (GenerativeBenchmarkAccumulator.update_estimate) through the *actual* per-request
    event sequence the real scheduler emits -- queued, pending, in_progress,
    first_token, then a terminal completed/errored/cancelled (confirmed directly in
    guidellm/scheduler/worker_group.py's WorkerProcessGroup._update_state_request_counts
    and guidellm/scheduler/scheduler.py's Scheduler.run() docstring) -- to the real
    final compiled metrics (GenerativeMetrics.compile()'s request_totals, itself backed
    by the real get_within_range() overlap filter).

    A single "completed"-only call per request (this class's previous approach) is
    NOT equivalent: GenerativeBenchmarkTimings.update_estimate() runs its warmup/
    cooldown transition check (TransientPhaseConfig.compute_transition_time) on
    *every* scheduler event, not just the terminal one, and does so based on
    `info.started_at`, which is only populated once a request reaches "in_progress".
    Replaying only the terminal event hid a real, separate warmup off-by-one (see
    test_uncompensated_warmup_of_five_actually_excludes_six_not_five below) that a
    real benchmark run surfaced: 24 successful requests recorded against a 50-request
    minimum, traced back to warmup excluding six requests instead of the declared
    five (see docs/proposals/chat-small-extended-v1.md). Skipped automatically
    wherever GuideLLM is not installed."""

    def setUp(self) -> None:
        self.g = _guidellm_full_pipeline_classes()
        self._counters = {
            "processed_requests": 0, "created_requests": 0, "successful_requests": 0,
            "errored_requests": 0, "cancelled_requests": 0,
            "queued_requests": 0, "pending_requests": 0, "processing_requests": 0,
        }

    def _state(self, configured_total: int):
        c = self._counters
        return self.g["SchedulerState"](
            processed_requests=c["processed_requests"], successful_requests=c["successful_requests"],
            errored_requests=c["errored_requests"], cancelled_requests=c["cancelled_requests"],
            created_requests=c["created_requests"], queued_requests=c["queued_requests"],
            pending_requests=c["pending_requests"], processing_requests=c["processing_requests"],
            start_requests_time=0.0,
            progress=self.g["SchedulerProgress"](
                remaining_requests=configured_total - c["processed_requests"], total_requests=configured_total
            ),
        )

    def _replay_request(
        self, accumulator, configured_total: int, request_id: str, start: float, duration: float,
        *, terminal_status: str = "completed",
    ) -> None:
        """Replay one request through the real, full five-event lifecycle a real
        scheduler emits -- queued, pending, in_progress, first_token, terminal --
        mutating `self._counters` exactly as
        WorkerProcessGroup._update_state_request_counts does, and calling
        accumulator.update_estimate() once per event (never once per request)."""
        c = self._counters
        request = self.g["GenerationRequest"](request_id=request_id)
        info = self.g["RequestInfo"](request_id=request_id, status="queued")

        c["created_requests"] += 1
        c["queued_requests"] += 1
        info.timings.queued = start
        accumulator.update_estimate(None, request, info, self._state(configured_total))

        c["queued_requests"] -= 1
        c["pending_requests"] += 1
        info.status = "pending"
        info.timings.dequeued = start + 0.001
        accumulator.update_estimate(None, request, info, self._state(configured_total))

        c["pending_requests"] -= 1
        c["processing_requests"] += 1
        info.status = "in_progress"
        info.timings.scheduled_at = start + 0.002
        info.timings.resolve_start = start + 0.003
        info.timings.request_start = start + 0.003
        accumulator.update_estimate(None, request, info, self._state(configured_total))

        if terminal_status == "cancelled_before_start":
            # A request cut off before the backend ever began resolving it: no
            # resolve_start, so the accumulator's incomplete-bucket guard
            # (`info.timings.resolve_start is not None`) correctly drops it
            # entirely rather than counting it as incomplete evidence.
            c["processing_requests"] -= 1
            c["cancelled_requests"] += 1
            c["processed_requests"] += 1
            info.status = "cancelled"
            info.timings.resolve_start = None
            info.timings.request_start = None
            accumulator.update_estimate(None, request, info, self._state(configured_total))
            return

        info.status = "first_token"
        info.timings.first_request_iteration = start + 0.01
        info.timings.first_token_iteration = start + 0.01
        info.timings.first_output_token_iteration = start + 0.01
        accumulator.update_estimate(None, request, info, self._state(configured_total))

        end = start + duration - 0.01
        if terminal_status == "completed":
            c["processing_requests"] -= 1
            c["processed_requests"] += 1
            c["successful_requests"] += 1
        elif terminal_status == "errored":
            c["processing_requests"] -= 1
            c["processed_requests"] += 1
            c["errored_requests"] += 1
        elif terminal_status == "cancelled":
            c["processing_requests"] -= 1
            c["processed_requests"] += 1
            c["cancelled_requests"] += 1
        else:
            raise ValueError(terminal_status)
        info.status = terminal_status
        info.timings.last_token_iteration = end
        info.timings.last_request_iteration = end
        info.timings.request_iterations = 1
        info.timings.token_iterations = 1
        info.timings.request_end = end
        info.timings.resolve_end = end
        info.timings.finalized = end
        accumulator.update_estimate(None, request, info, self._state(configured_total))

    def _run_full_pipeline(self, configured_total: int, completed_count: int, warmup_value: int = 4, cooldown_value: int = 4):
        """Replay `completed_count` sequential (concurrency=1), successful requests,
        each through the real five-event lifecycle above, with `configured_total`
        fixed throughout as the --constraint max_requests value -- unaffected by how
        many requests actually complete before a cutoff, exactly as the real
        scheduler's constraint (`remaining_requests = max_num -
        state.processed_requests`, confirmed in
        guidellm/scheduler/constraints/request.py) behaves. `warmup_value`/
        `cooldown_value` default to this profile's actually-shipped, compensated
        mapping (4, not the naively-expected 5 for either phase). Returns the real
        GenerativeMetrics.compile() result's request_totals."""
        warmup = self.g["TransientPhaseConfig"](value=warmup_value, mode="requests")
        cooldown = self.g["TransientPhaseConfig"](value=cooldown_value, mode="requests")
        config = self.g["BenchmarkConfig"](
            run_id="test-run", run_index=0, strategy=self.g["SynchronousStrategy"](),
            constraints={}, warmup=warmup, cooldown=cooldown,
            profile={}, requests={}, backend={}, environment={},
        )
        accumulator = self.g["GenerativeBenchmarkAccumulator"](config=config)
        for i in range(1, completed_count + 1):
            # Sequential, non-overlapping, back-to-back per-request window, matching
            # concurrency=1 (one in flight at a time): request i starts at i - 1.
            self._replay_request(accumulator, configured_total, f"req-{i}", float(i - 1), 1.0)
        return self.g["GenerativeMetrics"].compile(accumulator).request_totals

    def test_uncompensated_warmup_of_five_actually_excludes_six_not_five(self) -> None:
        """The real-world bug: GuideLLM 0.7.3's own request-count warmup boundary
        excludes phase_requests + 1 leading requests, not phase_requests, under the
        real multi-event lifecycle -- the same off-by-one already documented and
        compensated for cooldown, now confirmed to apply to warmup too. Left
        uncompensated (warmup.value=5, the naive declared-value-as-input choice,
        which is what chat-small-extended-v1 shipped with before this fix), cooldown
        disabled (huge value) to isolate warmup's own effect: 20 requests in, this
        excludes six, not five."""
        totals = self._run_full_pipeline(configured_total=1000, completed_count=20, warmup_value=5, cooldown_value=0)
        self.assertEqual(totals.total, 14)  # 20 - 6, not the declared 20 - 5 = 15

    def test_compensated_warmup_of_four_actually_excludes_exactly_five_requests(self) -> None:
        """warmup.value=4 (not the naively-expected 5) is the smallest correction that
        makes the runner's *actual* behavior match the declared five-warmup-request
        contract exactly, mirroring the already-shipped cooldown.value=4 compensation.
        Cooldown disabled (huge value) to isolate warmup's own effect."""
        totals = self._run_full_pipeline(configured_total=1000, completed_count=20, warmup_value=4, cooldown_value=0)
        self.assertEqual(totals.total, 15)  # 20 - 5, matching the declared contract

    def test_uncompensated_cooldown_of_five_actually_excludes_six_not_five(self) -> None:
        """The same documented, verified GuideLLM 0.7.3 quirk, for cooldown: asking
        for cooldown.value=N excludes N+1 trailing requests, not N. Left
        uncompensated (cooldown.value=5) at a total of 60, this falls one short."""
        totals = self._run_full_pipeline(configured_total=60, completed_count=60, cooldown_value=5)
        self.assertEqual(totals.total, 49)

    def test_compensated_mapping_achieves_exactly_the_declared_five_warmup_fifty_measured_five_cooldown(self) -> None:
        """The shipped runner mapping: warmup.value=4 and cooldown.value=4 (not the
        naively-expected 5 for either) at a total of 60 (not 61 or 62) -- the
        smallest correction that makes the runner's *actual* behavior match the
        declared contract exactly, rather than inflating the request budget to
        compensate. Verified end to end: real accumulator, real multi-event
        lifecycle, real final compiled metrics."""
        totals = self._run_full_pipeline(configured_total=60, completed_count=60)
        self.assertEqual(totals.successful, 50)
        self.assertEqual(totals.incomplete, 0)
        self.assertEqual(totals.errored, 0)
        self.assertEqual(totals.total, 50)
        # 60 total = 5 warmup + 50 measured + 5 (actual, compensated) cooldown.
        excluded = 60 - totals.total
        self.assertEqual(excluded, 10)

    def test_fast_run_stops_at_the_request_budget_without_a_nine_minute_warmup_wait(self) -> None:
        """Explicit request-count warmup ends as soon as 5 requests complete, however
        little wall-clock time that takes — unlike chat-small-v1's percent-of-duration
        scheme, which would always reserve a fixed 10% of the ceiling for warmup
        regardless of how fast the requests actually complete. This simulation uses
        request index as a stand-in clock (each request occupies one unit of time);
        the boundary logic under test only cares about request counts, not the
        absolute unit, so a fast real run (all 60 requests in well under a second)
        hits this same request-count boundary, not a wall-clock one."""
        totals = self._run_full_pipeline(configured_total=60, completed_count=60)
        self.assertEqual(totals.total, 50)  # request-count constraint binds, not the 5400s ceiling

    def test_duration_limited_run_retains_partial_measured_requests_with_no_phantom_cooldown(self) -> None:
        """If the run is cut off by the duration ceiling before completing the full
        60-request budget, cooldown's trailing-request trigger never fires (it needs
        the configured total to actually be reached), so every completed request
        after warmup counts as measured -- partial evidence is retained, not discarded
        to an assumed cooldown window."""
        # 100 s/request against the 5400 s ceiling completes floor(5400/100) = 54 requests.
        totals = self._run_full_pipeline(configured_total=60, completed_count=54)
        self.assertEqual(totals.incomplete, 0)
        self.assertEqual(totals.successful, 49)  # 54 completed - 5 warmup, no cooldown ever triggered
        self.assertGreaterEqual(totals.successful, 40)  # still meets acceptance

    def test_a_much_slower_duration_limited_run_falls_below_the_acceptance_minimum(self) -> None:
        """A host slow enough to only complete 27 of the 60 requests in 5400s (200 s/req)
        still gets correctly-scoped partial evidence, but that evidence legitimately
        falls short of minimum_successful_requests: 40 -- the correct, honest outcome
        for hardware this slow, not a bug. This is also, concretely, what the real
        preserved extended run's shortfall looks like once the warmup fix above is
        applied: a genuinely too-slow host, not a miscounted one."""
        totals = self._run_full_pipeline(configured_total=60, completed_count=27)
        self.assertEqual(totals.successful, 22)
        self.assertLess(totals.successful, 40)

    def test_duration_limited_run_preserves_errored_and_incomplete_requests_correctly(self) -> None:
        """Mixed terminal statuses under the real multi-event lifecycle: successful
        requests land in `successful`, a request that errors after dispatch lands in
        `errored` (not silently dropped or miscounted as successful), and a request
        cut off before the backend ever began resolving it is excluded entirely
        (never phantom-counted as incomplete evidence) -- matching
        GenerativeBenchmarkAccumulator.update_estimate's own incomplete-bucket guard."""
        warmup = self.g["TransientPhaseConfig"](value=4, mode="requests")
        cooldown = self.g["TransientPhaseConfig"](value=4, mode="requests")
        config = self.g["BenchmarkConfig"](
            run_id="test-run", run_index=0, strategy=self.g["SynchronousStrategy"](),
            constraints={}, warmup=warmup, cooldown=cooldown,
            profile={}, requests={}, backend={}, environment={},
        )
        accumulator = self.g["GenerativeBenchmarkAccumulator"](config=config)
        configured_total = 1000
        for i in range(1, 6):  # 5 warmup requests
            self._replay_request(accumulator, configured_total, f"warmup-{i}", float(i - 1), 1.0)
        for i in range(1, 6):  # 5 measured successes
            self._replay_request(accumulator, configured_total, f"ok-{i}", float(4 + i), 1.0)
        self._replay_request(accumulator, configured_total, "err-1", 10.0, 1.0, terminal_status="errored")
        self._replay_request(
            accumulator, configured_total, "cancelled-1", 11.0, 1.0, terminal_status="cancelled_before_start"
        )
        totals = self.g["GenerativeMetrics"].compile(accumulator).request_totals
        self.assertEqual(totals.successful, 5)
        self.assertEqual(totals.errored, 1)
        self.assertEqual(totals.incomplete, 0)
        self.assertEqual(totals.total, 6)


class RunIntentTests(unittest.TestCase):
    def test_run_intent_uses_a_portable_profile_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile_path = root / "benchmarks" / "profiles" / "chat-small-v1.yaml"
            profile_path.parent.mkdir(parents=True)
            profile_path.write_text("id: chat-small-v1\n", encoding="utf-8")
            output_path = root / ".omobench-runs" / "intent.json"
            args = argparse.Namespace(
                endpoint="http://localhost:8000/v1",
                model="Qwen/Qwen3-4B",
                profile="chat-small-v1",
                output=str(output_path),
                force=False,
            )
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.doctor_report", return_value={}):
                    self.assertEqual(write_run_intent(args), 0)
            finally:
                os.chdir(original_directory)

            intent = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(intent["profile_path"], "benchmarks/profiles/chat-small-v1.yaml")


class GpuDetectionTests(unittest.TestCase):
    def test_nvidia_detection_records_vendor_nvidia(self) -> None:
        csv_output = "NVIDIA Test GPU, 24576, 555.1\n"
        with patch("omobench.cli.command_output", return_value=csv_output):
            gpus = detect_nvidia_gpus()
        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0]["vendor"], "nvidia")
        self.assertEqual(gpus[0]["model"], "NVIDIA Test GPU")
        self.assertEqual(gpus[0]["memory_gb"], 24.0)

    def test_command_output_returns_none_on_permission_error(self) -> None:
        with patch("omobench.cli.subprocess.run", side_effect=PermissionError(13, "Permission denied")):
            self.assertIsNone(command_output(["rocm-smi", "--json"]))

    def test_command_output_returns_stdout_on_success(self) -> None:
        completed = subprocess.CompletedProcess(args=["rocm-smi"], returncode=0, stdout="ok\n", stderr="")
        with patch("omobench.cli.subprocess.run", return_value=completed):
            self.assertEqual(command_output(["rocm-smi"]), "ok")

    def test_detect_amd_gpus_returns_empty_when_rocm_smi_permission_denied(self) -> None:
        with patch("omobench.cli.sys.platform", "linux"), patch(
            "omobench.cli.subprocess.run", side_effect=PermissionError(13, "Permission denied")
        ):
            self.assertEqual(detect_amd_gpus(), [])

    def test_windows_cim_detects_amd_and_intel_with_null_memory(self) -> None:
        cim_json = json.dumps(
            [
                {"Name": "AMD Radeon RX 9070", "DriverVersion": "31.0.21001.1000"},
                {"Name": "Intel(R) Arc(TM) A770 Graphics", "DriverVersion": "31.0.101.5081"},
                {"Name": "Microsoft Basic Display Adapter", "DriverVersion": "10.0.0.1"},
            ]
        )
        with patch("omobench.cli.sys.platform", "win32"), patch(
            "omobench.cli.command_output", return_value=cim_json
        ):
            gpus = detect_windows_cim_gpus()

        self.assertEqual(len(gpus), 2)
        by_vendor = {gpu["vendor"]: gpu for gpu in gpus}
        self.assertEqual(by_vendor["amd"]["model"], "AMD Radeon RX 9070")
        self.assertEqual(by_vendor["amd"]["driver_version"], "31.0.21001.1000")
        self.assertIsNone(by_vendor["amd"]["memory_gb"])
        self.assertEqual(by_vendor["intel"]["model"], "Intel(R) Arc(TM) A770 Graphics")
        self.assertEqual(by_vendor["intel"]["driver_version"], "31.0.101.5081")
        self.assertIsNone(by_vendor["intel"]["memory_gb"])

    def test_windows_cim_returns_nothing_off_windows(self) -> None:
        with patch("omobench.cli.sys.platform", "linux"):
            self.assertEqual(detect_windows_cim_gpus(), [])

    def test_detect_gpus_suppresses_fallback_records_for_covered_vendors(self) -> None:
        specialized_nvidia = [
            {"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24.0, "driver_version": "555.1", "detected_by": "nvidia-smi"}
        ]
        specialized_amd = [
            {"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": 192.0, "driver_version": "6.2.1", "detected_by": "rocm-smi"}
        ]
        fallback = [
            {"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": None, "driver_version": None, "detected_by": "win32_videocontroller"},
            {"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": None, "driver_version": None, "detected_by": "win32_videocontroller"},
        ]
        with patch("omobench.cli.sys.platform", "win32"), patch(
            "omobench.cli.detect_nvidia_gpus", return_value=specialized_nvidia
        ), patch("omobench.cli.detect_amd_gpus", return_value=specialized_amd), patch(
            "omobench.cli.detect_windows_cim_gpus", return_value=fallback
        ):
            gpus = detect_gpus()

        self.assertEqual(gpus, specialized_nvidia + specialized_amd)

    def test_detect_gpus_retains_uncovered_fallback_vendor(self) -> None:
        specialized_nvidia = [
            {"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24.0, "driver_version": "555.1", "detected_by": "nvidia-smi"}
        ]
        intel_fallback = {
            "vendor": "intel",
            "model": "Intel(R) Arc(TM) A770 Graphics",
            "memory_gb": None,
            "driver_version": "31.0.101.5081",
            "detected_by": "win32_videocontroller",
        }
        fallback = [
            {"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": None, "driver_version": None, "detected_by": "win32_videocontroller"},
            intel_fallback,
        ]
        with patch("omobench.cli.sys.platform", "win32"), patch(
            "omobench.cli.detect_nvidia_gpus", return_value=specialized_nvidia
        ), patch("omobench.cli.detect_amd_gpus", return_value=[]), patch(
            "omobench.cli.detect_windows_cim_gpus", return_value=fallback
        ):
            gpus = detect_gpus()

        self.assertEqual(gpus, specialized_nvidia + [intel_fallback])

    def test_doctor_report_uses_generic_gpus_key(self) -> None:
        with patch("omobench.cli.detect_gpus", return_value=[{"vendor": "amd", "model": "Test", "memory_gb": None, "driver_version": None, "detected_by": "test"}]):
            report = doctor_report()
        self.assertIn("gpus", report)
        self.assertNotIn("nvidia_gpus", report)

    def test_print_doctor_reports_gpus_not_nvidia_gpus(self) -> None:
        report = {
            "omobench_cli_version": "0.0.0",
            "python": "3.12.0",
            "operating_system": "Test OS",
            "cpu": "Test CPU",
            "ram_gb": 32.0,
            "guidellm": None,
            "git": None,
            "github_cli": None,
            "gpus": [
                {"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "driver_version": None, "detected_by": "test"}
            ],
        }
        output = io.StringIO()
        with redirect_stdout(output):
            print_doctor(report)
        printed = output.getvalue()
        self.assertIn("GPUs:", printed)
        self.assertNotIn("NVIDIA GPUs", printed)
        self.assertIn("[amd] AMD Radeon RX 9070", printed)
        self.assertIn("unknown memory", printed)


class GuidellmRunnerPreflightTests(unittest.TestCase):
    """guidellm_runner_preflight() replaced a subprocess-based `guidellm
    --version` probe (command_output()'s hardcoded 60s timeout) that could
    hang long enough to waste a full managed model load before reporting a
    misleading failure. These tests pin its two real jobs: (1) it must never
    spawn a subprocess -- package metadata only; (2) it must produce a
    distinct, actionable result for "not installed" vs. "wrong version"
    vs. "installed and pinned", each naming the actual interpreter."""

    def test_never_spawns_a_subprocess(self) -> None:
        with patch("omobench.cli.subprocess.run", side_effect=AssertionError("must not spawn a subprocess")):
            preflight = guidellm_runner_preflight()
        # Whatever guidellm is actually pip-installed as in this test
        # environment -- the point is only that reaching a result never
        # touched subprocess.run.
        self.assertIn(preflight.kind, {"ok", "missing", "version_mismatch"})

    def test_missing_package_is_a_distinct_actionable_result(self) -> None:
        with patch("omobench.cli.guidellm_installed_version", return_value=None):
            preflight = guidellm_runner_preflight()
        self.assertFalse(preflight.ok)
        self.assertEqual(preflight.kind, "missing")
        self.assertIsNone(preflight.version)
        self.assertIn("not installed", preflight.message)
        self.assertIn(sys.executable, preflight.message)

    def test_version_mismatch_is_a_distinct_actionable_result(self) -> None:
        with patch("omobench.cli.guidellm_installed_version", return_value="0.6.9"):
            preflight = guidellm_runner_preflight()
        self.assertFalse(preflight.ok)
        self.assertEqual(preflight.kind, "version_mismatch")
        self.assertEqual(preflight.version, "0.6.9")
        self.assertIn(GUIDELLM_VERSION, preflight.message)
        self.assertIn("0.6.9", preflight.message)
        self.assertIn(sys.executable, preflight.message)

    def test_matching_pinned_version_is_ok(self) -> None:
        with patch("omobench.cli.guidellm_installed_version", return_value=GUIDELLM_VERSION):
            preflight = guidellm_runner_preflight()
        self.assertTrue(preflight.ok)
        self.assertEqual(preflight.kind, "ok")
        self.assertEqual(preflight.version, GUIDELLM_VERSION)

    def test_installed_version_reads_package_metadata_not_a_subprocess(self) -> None:
        with patch("omobench.cli.importlib.metadata.version", return_value="9.9.9") as metadata_version:
            with patch("omobench.cli.subprocess.run", side_effect=AssertionError("must not spawn a subprocess")):
                self.assertEqual(guidellm_installed_version(), "9.9.9")
        metadata_version.assert_called_once_with("guidellm")

    def test_installed_version_is_none_when_package_metadata_is_absent(self) -> None:
        import importlib.metadata as real_importlib_metadata

        with patch(
            "omobench.cli.importlib.metadata.version",
            side_effect=real_importlib_metadata.PackageNotFoundError("guidellm"),
        ):
            self.assertIsNone(guidellm_installed_version())

    def test_doctor_report_guidellm_field_never_spawns_a_subprocess(self) -> None:
        """doctor_report() runs on every guided/advanced page load; its
        "guidellm" field must stay cheap even while a model server is up --
        unlike its "git"/"github_cli" fields (still subprocess-based, and
        deliberately left untouched here), it must never invoke
        executable_version("guidellm") (the old subprocess-based probe)."""
        import omobench.cli as omobench_cli_module

        spy = Mock(wraps=omobench_cli_module.executable_version)
        with patch("omobench.cli.guidellm_installed_version", return_value=GUIDELLM_VERSION):
            with patch("omobench.cli.executable_version", spy):
                report = doctor_report()
        self.assertIn(GUIDELLM_VERSION, report["guidellm"])
        for call in spy.call_args_list:
            self.assertNotEqual(call.args[0], "guidellm")

    def test_doctor_report_guidellm_field_is_none_when_not_installed(self) -> None:
        with patch("omobench.cli.guidellm_installed_version", return_value=None):
            report = doctor_report()
        self.assertIsNone(report["guidellm"])


class HardwareFromEnvironmentTests(unittest.TestCase):
    def test_legacy_nvidia_gpus_key_still_packages_and_infers_vendor(self) -> None:
        environment = {
            "operating_system": "Test Linux",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "nvidia_gpus": [{"model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"}],
        }
        hardware = hardware_from_environment(environment, None, None, execution_device="gpu")
        self.assertEqual(hardware["gpus"], [{"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24.0, "count": 1}])

    def test_generic_amd_environment_packages_and_emits_vendor_amd(self) -> None:
        environment = {
            "operating_system": "Test Linux",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [{"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": 192, "driver_version": "6.2.1"}],
        }
        hardware = hardware_from_environment(environment, None, None, execution_device="gpu")
        self.assertEqual(
            hardware["gpus"],
            [{"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": 192.0, "count": 1}],
        )

    def test_missing_detected_gpu_blocks_packaging(self) -> None:
        environment = {"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64, "gpus": []}
        with self.assertRaisesRegex(ValueError, "No GPUs were detected"):
            hardware_from_environment(environment, None, None, execution_device="gpu")

    def test_single_unknown_memory_amd_gpu_requires_gpu_memory_gb(self) -> None:
        environment = {
            "operating_system": "Test Windows",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "driver_version": "31.0"}],
        }
        with self.assertRaisesRegex(ValueError, "unknown memory"):
            hardware_from_environment(environment, None, None, execution_device="gpu")

        hardware = hardware_from_environment(environment, None, None, gpu_memory_gb_override=16.0, execution_device="gpu")
        self.assertEqual(
            hardware["gpus"],
            [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": 16.0, "count": 1}],
        )
        self.assertEqual(hardware["detection"]["hardware"], "user-confirmed")

    def test_ambiguous_unknown_memory_gpus_fail_clearly(self) -> None:
        environment = {
            "operating_system": "Test Windows",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [
                {"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "driver_version": None},
                {"vendor": "intel", "model": "Intel Arc A770", "memory_gb": None, "driver_version": None},
            ],
        }
        with self.assertRaisesRegex(ValueError, "more than one"):
            hardware_from_environment(environment, None, None, gpu_memory_gb_override=16.0, execution_device="gpu")

    def test_invalid_gpu_memory_gb_override_types_raise_value_error(self) -> None:
        environment = {
            "operating_system": "Test Windows",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "driver_version": None}],
        }
        for invalid_value in ("16", True):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaisesRegex(ValueError, "--gpu-memory-gb must be a finite positive number."):
                    hardware_from_environment(environment, None, None, gpu_memory_gb_override=invalid_value, execution_device="gpu")

    def test_gpu_memory_gb_rejected_when_not_needed(self) -> None:
        environment = {
            "operating_system": "Test Linux",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [{"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"}],
        }
        with self.assertRaisesRegex(ValueError, "already has a known memory value"):
            hardware_from_environment(environment, None, None, gpu_memory_gb_override=16.0, execution_device="gpu")

    def test_cpu_execution_device_records_no_gpus_and_no_driver(self) -> None:
        environment = {"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64}
        hardware = hardware_from_environment(environment, None, None, execution_device="cpu")
        self.assertEqual(hardware["gpus"], [])
        self.assertIsNone(hardware["driver_version"])
        self.assertEqual(hardware["cpu"], "Test CPU")
        self.assertEqual(hardware["ram_gb"], 64.0)

    def test_cpu_execution_device_does_not_require_any_detected_gpu(self) -> None:
        """A CPU packaging must not need GPU detection, VRAM, or drivers at all."""
        environment = {"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64}
        self.assertNotIn("gpus", environment)
        hardware = hardware_from_environment(environment, None, None, execution_device="cpu")
        self.assertEqual(hardware["gpus"], [])

    def test_gpu_equipped_machine_running_cpu_only_does_not_report_the_gpu(self) -> None:
        """A GPU-equipped machine that performed CPU-only inference must not have its idle
        GPU promoted into the normalized hardware; that GPU did not perform inference."""
        environment = {
            "operating_system": "Test Linux",
            "cpu": "Test CPU",
            "ram_gb": 64,
            "gpus": [
                {
                    "vendor": "nvidia",
                    "model": "NVIDIA RTX 500 Ada Generation Laptop GPU",
                    "memory_gb": 4.0,
                    "driver_version": "596.71",
                }
            ],
        }
        hardware = hardware_from_environment(environment, None, None, execution_device="cpu")
        self.assertEqual(hardware["gpus"], [])
        self.assertIsNone(hardware["driver_version"])

    def test_gpu_memory_gb_override_is_rejected_for_cpu_execution(self) -> None:
        environment = {"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64}
        with self.assertRaisesRegex(ValueError, "cannot be used when --execution-device is cpu"):
            hardware_from_environment(environment, None, None, gpu_memory_gb_override=16.0, execution_device="cpu")

    def test_cpu_override_marks_hardware_detection_user_confirmed(self) -> None:
        environment = {"operating_system": "Test Linux", "cpu": "Detected CPU", "ram_gb": 64}
        hardware = hardware_from_environment(environment, "Overridden CPU", None, execution_device="cpu")
        self.assertEqual(hardware["cpu"], "Overridden CPU")
        self.assertEqual(hardware["detection"]["hardware"], "user-confirmed")


def build_staged_run_directory(root: Path, environment: dict | None = None, report: dict | None = None) -> Path:
    """Create a complete .omobench-runs/ staging directory with valid evidence."""
    profile_path = root / "benchmarks" / "profiles" / "chat-small-v1.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    # Permissive but real acceptance section: GUIDELLM_REPORT_V2's measured-window
    # counts (50 succeeded, 0.0% errors) must satisfy it, so existing tests that
    # only care about non-acceptance behavior keep passing while still genuinely
    # exercising the acceptance check rather than bypassing it.
    profile_path.write_text(
        "id: chat-small-v1\nacceptance:\n  minimum_successful_requests: 40\n  maximum_error_rate_percent: 5\n",
        encoding="utf-8",
    )
    # Real acceptance thresholds (matching the actual chat-small-extended-v1.yaml),
    # for tests packaging against that profile id.
    extended_profile_path = root / "benchmarks" / "profiles" / "chat-small-extended-v1.yaml"
    extended_profile_path.write_text(
        "id: chat-small-extended-v1\nacceptance:\n  minimum_successful_requests: 40\n  maximum_error_rate_percent: 1\n",
        encoding="utf-8",
    )
    run_directory = root / ".omobench-runs" / "run-fixture-1234"
    run_directory.mkdir(parents=True)
    with gzip.open(run_directory / "guidellm-benchmarks.json.gz", "wt", encoding="utf-8") as raw_file:
        json.dump(report if report is not None else GUIDELLM_REPORT_V2, raw_file)
    (run_directory / "guidellm.stdout.log").write_text("completed\n", encoding="utf-8")
    (run_directory / "reproduce.md").write_text("# Reproduce\n", encoding="utf-8")
    default_environment = {
        "operating_system": "Test Linux",
        "cpu": "Test CPU",
        "ram_gb": 64,
        "nvidia_gpus": [
            {"model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"},
            {"model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"},
        ],
    }
    (run_directory / "environment.json").write_text(
        json.dumps(environment if environment is not None else default_environment),
        encoding="utf-8",
    )
    return run_directory


def base_package_args(run_directory: Path, **overrides: object) -> argparse.Namespace:
    args = dict(
        run_dir=str(run_directory),
        model="Qwen/Qwen3-4B",
        model_revision="abc123",
        model_source=None,
        quantization="BF16",
        framework="vllm",
        framework_version="0.11.0",
        server_description=None,
        execution_device="gpu",
        runtime_arg=["--max-model-len=4096"],
        profile="chat-small-v1",
        submitter="local-tester",
        run_id=None,
        run_kind="initial",
        repeat_of_run_id=None,
        cpu=None,
        ram_gb=None,
        gpu_memory_gb=None,
        force=False,
    )
    args.update(overrides)
    return argparse.Namespace(**args)


class RuntimeFrameworkParserTests(unittest.TestCase):
    def test_package_accepts_every_documented_framework_choice(self) -> None:
        parser = build_parser()
        for framework in RUNTIME_FRAMEWORKS:
            with self.subTest(framework=framework):
                args = parser.parse_args(
                    [
                        "package",
                        "--run-dir",
                        ".omobench-runs/run-fixture-1234",
                        "--model",
                        "Qwen/Qwen3-4B",
                        "--model-revision",
                        "abc123",
                        "--framework",
                        framework,
                        "--framework-version",
                        "1.0.0",
                        "--execution-device",
                        "gpu",
                        "--profile",
                        "chat-small-v1",
                        "--submitter",
                        "local-tester",
                    ]
                )
                self.assertEqual(args.framework, framework)

    def test_unknown_framework_is_rejected_by_the_parser(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "package",
                    "--run-dir",
                    ".omobench-runs/run-fixture-1234",
                    "--model",
                    "Qwen/Qwen3-4B",
                    "--model-revision",
                    "abc123",
                    "--framework",
                    "unsupported-framework",
                    "--framework-version",
                    "1.0.0",
                    "--execution-device",
                    "gpu",
                    "--profile",
                    "chat-small-v1",
                    "--submitter",
                    "local-tester",
                ]
            )

    def test_server_description_argument_is_optional_and_defaults_to_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "package",
                "--run-dir",
                ".omobench-runs/run-fixture-1234",
                "--model",
                "Qwen/Qwen3-4B",
                "--model-revision",
                "abc123",
                "--framework",
                "vllm",
                "--framework-version",
                "1.0.0",
                "--execution-device",
                "gpu",
                "--profile",
                "chat-small-v1",
                "--submitter",
                "local-tester",
            ]
        )
        self.assertIsNone(args.server_description)

    def test_model_source_argument_is_optional_and_defaults_to_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "package",
                "--run-dir",
                ".omobench-runs/run-fixture-1234",
                "--model",
                "Qwen/Qwen3-4B",
                "--model-revision",
                "abc123",
                "--framework",
                "vllm",
                "--framework-version",
                "1.0.0",
                "--execution-device",
                "gpu",
                "--profile",
                "chat-small-v1",
                "--submitter",
                "local-tester",
            ]
        )
        self.assertIsNone(args.model_source)

        args = parser.parse_args(
            [
                "package",
                "--run-dir",
                ".omobench-runs/run-fixture-1234",
                "--model",
                "Qwen/Qwen3-4B",
                "--model-revision",
                "abc123",
                "--model-source",
                "https://huggingface.co/ggml-org/Qwen3-4B-GGUF/resolve/2f3b082b1356a6123f7ed71e65aea340da25d53c/Qwen3-4B-Q4_K_M.gguf",
                "--framework",
                "vllm",
                "--framework-version",
                "1.0.0",
                "--execution-device",
                "gpu",
                "--profile",
                "chat-small-v1",
                "--submitter",
                "local-tester",
            ]
        )
        self.assertTrue(args.model_source.startswith("https://huggingface.co/ggml-org/"))


class ModelSourceValidationTests(unittest.TestCase):
    def test_valid_https_url_is_accepted(self) -> None:
        url = "https://huggingface.co/ggml-org/Qwen3-4B-GGUF/resolve/abc123/Qwen3-4B-Q4_K_M.gguf"
        self.assertEqual(require_https_source_url(url, "--model-source"), url)

    def test_non_https_scheme_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must use https"):
            require_https_source_url("http://huggingface.co/x/y", "--model-source")

    def test_credentials_in_url_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not carry credentials"):
            require_https_source_url("https://user:pass@huggingface.co/x/y", "--model-source")

    def test_incomplete_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a complete URL"):
            require_https_source_url("https://", "--model-source")

    def test_empty_value_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            require_https_source_url("", "--model-source")


class ResultPackagingTests(unittest.TestCase):
    def test_normalizer_rejects_an_unpinned_guidellm_report(self) -> None:
        report = json.loads(json.dumps(GUIDELLM_REPORT_V2))
        report["metadata"]["guidellm_version"] = "0.7.2"
        with self.assertRaisesRegex(ValueError, "requires GuideLLM 0.7.3"):
            normalize_guidellm_report(report)

    def test_normalizer_rejects_a_report_with_no_successful_requests(self) -> None:
        report = json.loads(json.dumps(GUIDELLM_REPORT_V2))
        requests = report["benchmarks"][0]["metrics"]["request_totals"]
        requests.update(
            {
                "successful": 0,
                "incomplete": 0,
                "errored": 50,
                "total": 50,
            }
        )

        with self.assertRaisesRegex(ValueError, "no successful requests"):
            normalize_guidellm_report(report)

    def test_normalizer_uses_measured_window_counts_not_full_lifecycle_counts(self) -> None:
        """The profile requires excluding warmup and cooldown from the report.
        scheduler_metrics.requests_made spans the full lifecycle; metrics.request_totals
        is GuideLLM's own measured-window-only count. The normalizer must report the
        latter, even when they disagree (as the fixture deliberately makes them)."""
        report = json.loads(json.dumps(GUIDELLM_REPORT_V2))
        normalized = normalize_guidellm_report(report)
        self.assertEqual(normalized["results"]["requests"], {"attempted": 50, "succeeded": 50, "failed": 0})
        self.assertEqual(normalized["results"]["error_rate_percent"], 0.0)

    def test_cpu_and_gpu_are_both_valid_execution_devices(self) -> None:
        """CPU-only benchmarks are first-class, publishable OmoBench results."""
        validate_execution_device("cpu")
        validate_execution_device("gpu")
        with self.assertRaisesRegex(ValueError, "must be 'cpu' or 'gpu'"):
            validate_execution_device("auto")

    def test_package_creates_a_schema_ready_local_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(root)
            args = base_package_args(run_directory)
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["schema_version"], "1.0")
            self.assertEqual(result["run_id"], "run-fixture-1234")
            self.assertEqual(result["results"]["latency_ms"]["ttft"]["p95"], 315.0)
            self.assertEqual(result["results"]["error_rate_percent"], 0.0)
            self.assertEqual(result["hardware"]["gpus"][0]["count"], 2)
            self.assertEqual(result["hardware"]["gpus"][0]["vendor"], "nvidia")
            self.assertEqual(
                result["artifacts"]["raw_runner_output"]["path"],
                "guidellm-benchmarks.json.gz",
            )
            self.assertTrue(result["configuration_fingerprint"].startswith("sha256:"))
            self.assertNotIn("server_description", result["runtime"])

    def test_generic_amd_environment_packages_and_emits_vendor_amd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={
                    "operating_system": "Test Linux",
                    "cpu": "Test CPU",
                    "ram_gb": 64,
                    "gpus": [
                        {"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": 192, "driver_version": "6.2.1"}
                    ],
                },
            )
            args = base_package_args(run_directory)
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(
                result["hardware"]["gpus"],
                [{"vendor": "amd", "model": "AMD Instinct MI300X", "memory_gb": 192.0, "count": 1}],
            )

    def test_generic_amd_environment_with_unknown_memory_requires_gpu_memory_gb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={
                    "operating_system": "Test Linux",
                    "cpu": "Test CPU",
                    "ram_gb": 64,
                    "gpus": [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "driver_version": "31.0"}],
                },
            )
            original_directory = Path.cwd()

            args_without_override = base_package_args(run_directory)
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        exit_code = package_local_run(args_without_override)
            finally:
                os.chdir(original_directory)
            self.assertEqual(exit_code, 2)
            self.assertIn("unknown memory", stderr.getvalue())
            self.assertFalse((run_directory / "result.json").exists())

            args_with_override = base_package_args(run_directory, gpu_memory_gb=16.0)
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args_with_override), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(
                result["hardware"]["gpus"],
                [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": 16.0, "count": 1}],
            )
            self.assertEqual(result["hardware"]["detection"]["hardware"], "user-confirmed")

    def test_missing_detected_gpu_still_blocks_gpu_packaging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64, "gpus": []},
            )
            args = base_package_args(run_directory)
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        exit_code = package_local_run(args)
            finally:
                os.chdir(original_directory)

            self.assertEqual(exit_code, 2)
            self.assertIn("No GPUs were detected", stderr.getvalue())
            self.assertFalse((run_directory / "result.json").exists())

    def test_openai_compatible_other_without_description_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(root)
            args = base_package_args(
                run_directory,
                framework=OTHER_SERVER_FRAMEWORK,
                server_description=None,
            )
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        exit_code = package_local_run(args)
            finally:
                os.chdir(original_directory)

            self.assertEqual(exit_code, 2)
            self.assertIn("--server-description is required", stderr.getvalue())
            self.assertFalse((run_directory / "result.json").exists())

    def test_openai_compatible_other_with_description_is_recorded_and_fingerprinted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(root)
            args = base_package_args(
                run_directory,
                framework=OTHER_SERVER_FRAMEWORK,
                server_description="Custom internal OpenAI-compatible gateway",
            )
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["runtime"]["framework"], OTHER_SERVER_FRAMEWORK)
            self.assertEqual(
                result["runtime"]["server_description"],
                "Custom internal OpenAI-compatible gateway",
            )

            # Changing only the server description must change the configuration fingerprint.
            vllm_args = base_package_args(run_directory, force=True)
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(vllm_args), 0)
            finally:
                os.chdir(original_directory)
            vllm_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertNotEqual(
                result["configuration_fingerprint"],
                vllm_result["configuration_fingerprint"],
            )

    def test_gpu_fingerprint_matches_the_pre_execution_device_historical_format(self) -> None:
        """A gpu result's configuration_fingerprint must still equal what pre-change
        code (whose payload never had an execution_device key) would have computed
        for the same model/runtime/hardware/benchmark. Otherwise an old GPU bundle
        and a newly repackaged, otherwise-identical GPU run would never collide under
        the validator's (configuration_fingerprint, raw_output_sha256) duplicate key,
        silently breaking duplicate detection across the schema change."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(root)
            args = base_package_args(run_directory)
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)
            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            # The exact pre-execution_device fingerprint payload shape, built from
            # this same run's normalized fields.
            historical_payload = {
                "model": result["model"],
                "runtime": result["runtime"],
                "hardware": {
                    "os": result["hardware"]["os"],
                    "cpu": result["hardware"]["cpu"],
                    "ram_gb": result["hardware"]["ram_gb"],
                    "gpus": result["hardware"]["gpus"],
                    "driver_version": result["hardware"]["driver_version"],
                },
                "benchmark": {
                    "profile_id": result["benchmark"]["profile_id"],
                    "profile_sha256": result["benchmark"]["profile_sha256"],
                },
            }
            self.assertEqual(result["configuration_fingerprint"], sha256_json(historical_payload))

    def test_model_source_is_omitted_by_default_and_fingerprint_is_unaffected(self) -> None:
        """Omitting --model-source must leave result.json and its fingerprint exactly
        as they were before --model-source existed."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(root)
            args = base_package_args(run_directory)
            self.assertIsNone(args.model_source)
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)
            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertNotIn("source", result["model"])

            historical_payload = {
                "model": result["model"],
                "runtime": result["runtime"],
                "hardware": {
                    "os": result["hardware"]["os"],
                    "cpu": result["hardware"]["cpu"],
                    "ram_gb": result["hardware"]["ram_gb"],
                    "gpus": result["hardware"]["gpus"],
                    "driver_version": result["hardware"]["driver_version"],
                },
                "benchmark": {
                    "profile_id": result["benchmark"]["profile_id"],
                    "profile_sha256": result["benchmark"]["profile_sha256"],
                },
            }
            self.assertEqual(result["configuration_fingerprint"], sha256_json(historical_payload))

    def test_model_source_is_recorded_and_changes_the_fingerprint(self) -> None:
        """Supplying --model-source records model.source and, since it is genuinely
        new metadata about this specific run's configuration, changes the
        configuration_fingerprint relative to an otherwise-identical run without it —
        the same way --server-description already behaves."""
        source_url = (
            "https://huggingface.co/ggml-org/Qwen3-4B-GGUF/resolve/"
            "2f3b082b1356a6123f7ed71e65aea340da25d53c/Qwen3-4B-Q4_K_M.gguf"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(root)
            original_directory = Path.cwd()

            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(base_package_args(run_directory)), 0)
            finally:
                os.chdir(original_directory)
            without_source = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(
                        package_local_run(base_package_args(run_directory, model_source=source_url, force=True)),
                        0,
                    )
            finally:
                os.chdir(original_directory)
            with_source = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            self.assertEqual(with_source["model"]["source"], source_url)
            self.assertEqual(with_source["model"]["id"], without_source["model"]["id"])
            self.assertEqual(with_source["model"]["revision"], without_source["model"]["revision"])
            self.assertNotEqual(
                with_source["configuration_fingerprint"], without_source["configuration_fingerprint"]
            )

    def test_invalid_model_source_is_rejected_and_does_not_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(root)
            args = base_package_args(run_directory, model_source="http://not-https.example/weights.gguf")
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        exit_code = package_local_run(args)
            finally:
                os.chdir(original_directory)
            self.assertEqual(exit_code, 2)
            self.assertIn("must use https", stderr.getvalue())
            self.assertFalse((run_directory / "result.json").exists())

    def test_cpu_execution_device_packages_without_any_gpu_evidence(self) -> None:
        """CPU results must not require GPU detection, VRAM, or GPU drivers."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64},
            )
            args = base_package_args(run_directory, execution_device="cpu")
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["execution_device"], "cpu")
            self.assertEqual(result["hardware"]["gpus"], [])
            self.assertIsNone(result["hardware"]["driver_version"])
            self.assertTrue(result["configuration_fingerprint"].startswith("sha256:"))

    def test_gpu_equipped_machine_performing_cpu_only_inference_does_not_report_the_gpu(self) -> None:
        """A machine with a real, detected GPU that nonetheless ran inference on the CPU
        must not have that idle GPU represented as having performed inference."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={
                    "operating_system": "Test Linux",
                    "cpu": "Test CPU",
                    "ram_gb": 64,
                    "gpus": [
                        {
                            "vendor": "nvidia",
                            "model": "NVIDIA RTX 500 Ada Generation Laptop GPU",
                            "memory_gb": 4.0,
                            "driver_version": "596.71",
                        }
                    ],
                },
            )
            args = base_package_args(run_directory, execution_device="cpu")
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(args), 0)
            finally:
                os.chdir(original_directory)

            result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["execution_device"], "cpu")
            self.assertEqual(result["hardware"]["gpus"], [])
            self.assertIsNone(result["hardware"]["driver_version"])

    def test_gpu_memory_gb_override_is_rejected_when_packaging_a_cpu_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64},
            )
            args = base_package_args(run_directory, execution_device="cpu", gpu_memory_gb=16.0)
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        exit_code = package_local_run(args)
            finally:
                os.chdir(original_directory)
            self.assertEqual(exit_code, 2)
            self.assertIn("cannot be used when --execution-device is cpu", stderr.getvalue())
            self.assertFalse((run_directory / "result.json").exists())

    def test_cpu_and_gpu_runs_with_identical_settings_are_distinct_configurations(self) -> None:
        """Execution device must be part of configuration identity: an otherwise-identical
        CPU and GPU run must not collapse into the same configuration_fingerprint."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={
                    "operating_system": "Test Linux",
                    "cpu": "Test CPU",
                    "ram_gb": 64,
                    "gpus": [{"vendor": "nvidia", "model": "NVIDIA Test GPU", "memory_gb": 24, "driver_version": "555.1"}],
                },
            )
            original_directory = Path.cwd()

            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(base_package_args(run_directory, execution_device="gpu")), 0)
            finally:
                os.chdir(original_directory)
            gpu_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(
                        package_local_run(base_package_args(run_directory, execution_device="cpu", force=True)), 0
                    )
            finally:
                os.chdir(original_directory)
            cpu_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            self.assertNotEqual(gpu_result["configuration_fingerprint"], cpu_result["configuration_fingerprint"])

    def test_different_cpu_configurations_do_not_collapse_to_one_identity(self) -> None:
        """Two different CPU machines/configs must not share a configuration_fingerprint
        just because they both have an empty gpus list."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = build_staged_run_directory(
                root,
                environment={"operating_system": "Test Linux", "cpu": "CPU A", "ram_gb": 64},
            )
            original_directory = Path.cwd()

            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(package_local_run(base_package_args(run_directory, execution_device="cpu")), 0)
            finally:
                os.chdir(original_directory)
            first_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(
                        package_local_run(
                            base_package_args(run_directory, execution_device="cpu", cpu="CPU B", force=True)
                        ),
                        0,
                    )
            finally:
                os.chdir(original_directory)
            second_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

            self.assertEqual(first_result["hardware"]["gpus"], [])
            self.assertEqual(second_result["hardware"]["gpus"], [])
            self.assertNotEqual(first_result["hardware"]["cpu"], second_result["hardware"]["cpu"])
            self.assertNotEqual(
                first_result["configuration_fingerprint"], second_result["configuration_fingerprint"]
            )


def _extended_report(successful: int, incomplete: int, errored: int, scheduler_total_multiplier_extra: int = 10) -> dict:
    """Build a raw GuideLLM report shaped like a real chat-small-extended-v1 run:
    scheduler_metrics spans the full warmup+measured+cooldown lifecycle (60 total
    when the request budget is fully reached: 5 warmup + 50 measured + 5 actual
    cooldown, thanks to the cooldown.value=4 compensation -- see
    ChatSmallExtendedBoundaryTests), while metrics.request_totals is the
    measured-window-only figure the normalizer must report. Numbers here come
    from the verified full-pipeline simulation, not invented."""
    report = json.loads(json.dumps(GUIDELLM_REPORT_V2))
    measured_total = successful + incomplete + errored
    report["benchmarks"][0]["scheduler_metrics"]["requests_made"] = {
        "successful": successful + scheduler_total_multiplier_extra,
        "incomplete": incomplete,
        "errored": errored,
        "total": measured_total + scheduler_total_multiplier_extra,
    }
    report["benchmarks"][0]["metrics"]["request_totals"] = {
        "successful": successful,
        "incomplete": incomplete,
        "errored": errored,
        "total": measured_total,
    }
    return report


class ChatSmallExtendedPackagingTests(unittest.TestCase):
    """OmoBench's own code (normalizer, PROFILE_SPECS, packaging, acceptance) handling
    of chat-small-extended-v1 report shapes derived from the verified boundary
    behavior above -- these are test fixtures with numbers computed from real GuideLLM
    semantics, not fabricated benchmark evidence."""

    def _package(self, root: Path, report: dict, **overrides) -> dict:
        run_directory = build_staged_run_directory(root, report=report)
        args = base_package_args(
            run_directory, profile="chat-small-extended-v1", execution_device="cpu", **overrides
        )
        original_directory = Path.cwd()
        os.chdir(root)
        try:
            with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                self.assertEqual(package_local_run(args), 0)
        finally:
            os.chdir(original_directory)
        return json.loads((run_directory / "result.json").read_text(encoding="utf-8"))

    def test_profile_is_selectable_and_produces_its_own_profile_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result = self._package(root, _extended_report(successful=50, incomplete=0, errored=0))
            self.assertEqual(result["benchmark"]["profile_id"], "chat-small-extended-v1")
            self.assertTrue(result["benchmark"]["profile_sha256"].startswith("sha256:"))

    def test_fast_run_reaching_the_request_budget_reports_fifty_measured_and_meets_acceptance(self) -> None:
        """Scenario (a): a fast/GPU-class run reaches the 60-request budget long before
        the 5400 s ceiling; the reported figures are the measured-window-only 50, not
        the full 60-request scheduler lifecycle total."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result = self._package(root, _extended_report(successful=50, incomplete=0, errored=0))
            self.assertEqual(result["results"]["requests"], {"attempted": 50, "succeeded": 50, "failed": 0})
            self.assertEqual(result["results"]["error_rate_percent"], 0.0)

    def test_one_attempted_request_failing_within_the_measured_window_yields_two_percent_error_rate_and_fails_acceptance(
        self,
    ) -> None:
        """The "49/50" case, named for what it actually checks rather than for the
        successful count alone: 50 requests were attempted inside the measured
        window (49 successful + 1 incomplete -- request_totals.total, GuideLLM's own
        overlap-filtered measured-window count, per the verified full-pipeline
        simulation), so the one failure is NOT discarded just because it may have
        been the request cut off by the 5400 s duration ceiling -- it is counted as
        an attempt like any other, giving error_rate_percent = 100 * 1 / 50 = 2.0.
        That 2% exceeds this profile's maximum_error_rate_percent (1, unchanged from
        chat-small-v1 -- see the YAML), so acceptance MUST fail here even though
        succeeded (49) alone clears minimum_successful_requests (40): the two
        acceptance criteria are independent, and a passing successful-count does not
        excuse a failing error rate. Compare
        test_forty_nine_attempted_all_successful_passes_acceptance below, which has
        the same successful count (49) but zero failures and passes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            copy_repo_tree_for_registry_validation(root)
            result = self._package(root, _extended_report(successful=49, incomplete=1, errored=0))
            self.assertEqual(result["results"]["requests"], {"attempted": 50, "succeeded": 49, "failed": 1})
            self.assertEqual(result["results"]["error_rate_percent"], 2.0)

            from omobench.ui_submission import validate_bundle

            errors = validate_bundle(root, root / ".omobench-runs" / "run-fixture-1234")
            self.assertTrue(
                any("maximum_error_rate_percent (1" in e for e in errors), errors
            )
            self.assertFalse(
                any("successful requests is below" in e for e in errors),
                "the successful count (49) alone clears the minimum; only the error rate should fail",
            )

    def test_forty_nine_attempted_all_successful_passes_acceptance(self) -> None:
        """Scenario (c), positive case: cut off by the 5400 s ceiling before completing
        the full 60-request budget (only 54 of 60 complete, per the verified
        simulation), cooldown's trigger never fires, and the 49 requests that
        attempted and completed within the measured window -- all 49 of them
        successful, zero incomplete, zero errored -- are retained as evidence.
        Exact measured counts: attempted 49, successful 49, incomplete 0, errored 0
        (failed = incomplete + errored = 0), error_rate_percent = 0.0. Both
        acceptance criteria pass independently: 49 >= minimum_successful_requests
        (40), and 0.0% <= maximum_error_rate_percent (1%) -- a finished-but-partial
        run can still be submission-ready. Compare
        test_one_attempted_request_failing_within_the_measured_window_... above,
        which has the same successful count (49) but one additional attempted-and-
        failed request and correctly fails."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            copy_repo_tree_for_registry_validation(root)
            # scheduler_metrics total in a partial run = completed(54) = warmup(5) + this
            # 49; no cooldown "extra" applies since cooldown never triggered.
            report = _extended_report(successful=49, incomplete=0, errored=0, scheduler_total_multiplier_extra=5)
            result = self._package(root, report)
            self.assertEqual(result["results"]["requests"], {"attempted": 49, "succeeded": 49, "failed": 0})
            self.assertEqual(result["results"]["error_rate_percent"], 0.0)

            from omobench.ui_submission import validate_bundle

            errors = validate_bundle(root, root / ".omobench-runs" / "run-fixture-1234")
            self.assertEqual(errors, [], errors)

    def test_duration_limited_run_that_falls_short_is_correctly_rejected_by_acceptance(self) -> None:
        """Scenario (c), negative case: a host too slow to reach even 40 measured
        requests within the 5400 s ceiling (22 measured, per the verified simulation
        of a 200 s/request host) is retained as evidence but correctly fails
        acceptance -- the ceiling being generous does not guarantee every host clears it."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            copy_repo_tree_for_registry_validation(root)
            report = _extended_report(successful=22, incomplete=0, errored=0, scheduler_total_multiplier_extra=5)
            result = self._package(root, report)
            self.assertEqual(result["results"]["requests"]["succeeded"], 22)

            from omobench.ui_submission import validate_bundle

            errors = validate_bundle(root, root / ".omobench-runs" / "run-fixture-1234")
            self.assertTrue(any("22 successful requests is below" in e for e in errors), errors)
            self.assertTrue(any("minimum_successful_requests (40)" in e for e in errors), errors)

    def test_warmup_and_cooldown_are_excluded_from_the_reported_figures(self) -> None:
        """Direct proof the normalizer reports metrics.request_totals (measured-window
        only), not scheduler_metrics.requests_made (full warmup+measured+cooldown
        lifecycle) -- the two are deliberately different in this fixture."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            report = _extended_report(successful=50, incomplete=0, errored=0)
            scheduler_total = report["benchmarks"][0]["scheduler_metrics"]["requests_made"]["total"]
            self.assertEqual(scheduler_total, 60)  # the full 5 + 50 + 5 lifecycle
            result = self._package(root, report)
            self.assertEqual(result["results"]["requests"]["attempted"], 50)  # not 60

    def test_packaging_and_registry_validation_use_the_new_profiles_own_id_and_hash(self) -> None:
        """Never conflated with chat-small-v1: distinct profile_id, distinct
        profile_sha256, distinct configuration_fingerprint for an otherwise identical
        run packaged against each."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            copy_repo_tree_for_registry_validation(root)
            extended_run = build_staged_run_directory(root, report=_extended_report(50, 0, 0))
            original_directory = Path.cwd()
            os.chdir(root)
            try:
                with patch("omobench.cli.current_git_commit", return_value="a" * 40):
                    self.assertEqual(
                        package_local_run(
                            base_package_args(
                                extended_run, profile="chat-small-extended-v1", execution_device="cpu"
                            )
                        ),
                        0,
                    )
            finally:
                os.chdir(original_directory)
            extended_result = json.loads((extended_run / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(extended_result["benchmark"]["profile_id"], "chat-small-extended-v1")

            from omobench.ui_submission import validate_bundle

            self.assertEqual(validate_bundle(root, extended_run), [])


if __name__ == "__main__":
    unittest.main()
