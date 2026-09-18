from __future__ import annotations

import contextlib
import gzip
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from omobench import cli, telemetry


ROOT = Path(__file__).resolve().parents[2]
# Already cached from earlier real CPU-run work in this checkout's environment
# (see docs/local-demo.md); real GuideLLM synthetic-text workloads need a real
# tokenizer, so integration tests here need this cached locally or reachable
# over the network. Guarded by _require_offline_tokenizer below -- skipped,
# not failed, when neither is available.
FIXTURE_MODEL_ID = "Qwen/Qwen3-0.6B"


def _require_offline_tokenizer(test_case: unittest.TestCase) -> None:
    """Real pinned-GuideLLM integration needs a real tokenizer for the
    synthetic-text workload; skip (never fail) when this environment has
    neither a cached copy nor network access, consistent with this repo's
    existing skip-on-missing-prerequisite convention."""
    try:
        import transformers
    except ImportError as error:
        raise unittest.SkipTest(f"transformers is not installed: {error}") from None
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        transformers.AutoTokenizer.from_pretrained(FIXTURE_MODEL_ID, local_files_only=True)
    except Exception as error:  # noqa: BLE001 - any resolution failure means "skip", not "fail"
        raise unittest.SkipTest(
            f"{FIXTURE_MODEL_ID} tokenizer is not cached locally and this test forces "
            f"HF_HUB_OFFLINE=1 rather than depend on network access: {error}"
        ) from None


class _StreamingChatHandler(BaseHTTPRequestHandler):
    """Minimal real OpenAI-compatible streaming chat-completions fixture:
    enough for GuideLLM's real backend/scheduler/accumulator code to run a
    genuine request lifecycle against, without any model or GPU/CPU-heavy
    inference. No secrets, no persistent state."""

    model_id = FIXTURE_MODEL_ID
    per_request_delay_seconds = 0.0

    def do_GET(self):
        if self.path == "/health":
            # GuideLLM's own OpenAIHTTPBackend.validate() (backend/openai/http.py)
            # GETs this before running anything; the real production command
            # (build_guidellm_command(), unchanged) does not disable that check.
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/v1/models":
            body = json.dumps({"data": [{"id": self.model_id}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.per_request_delay_seconds:
            time.sleep(self.per_request_delay_seconds)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {
            "id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": self.model_id,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "hello there"}, "finish_reason": None}],
        }
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        final = {
            "id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": self.model_id,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *_args):
        pass


def _start_fixture_server(*, per_request_delay_seconds: float = 0.0) -> ThreadingHTTPServer:
    handler = type("Handler", (_StreamingChatHandler,), {"per_request_delay_seconds": per_request_delay_seconds})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _fixture_argv(*, endpoint: str, output_path: Path, max_requests: int = 3, max_duration_seconds: int = 60,
                   warmup: str = "0.0", cooldown: str = "0.0") -> list[str]:
    return [
        "--backend",
        f"kind=openai_http,target={endpoint},model={FIXTURE_MODEL_ID},request_format=/v1/chat/completions,validate_backend=false",
        "--data",
        "kind=synthetic_text,prompt_tokens=8,output_tokens=4",
        "--profile",
        f"kind=synchronous,warmup={warmup},cooldown={cooldown}",
        "--constraint",
        f"kind=max_requests,count={max_requests}",
        "--constraint",
        f"kind=max_duration,seconds={max_duration_seconds}",
        "--constraint",
        "kind=max_error_rate,rate=0.5",
        "--seed",
        "kind=static,value=20260828",
        "--metrics",
        "kind=generative,sample_size=0",
        "--output",
        f"kind=json,path={output_path}",
        "--disable-console-interactive",
    ]


def _skip_unless_guidellm_pinned() -> None:
    try:
        import guidellm.cli  # noqa: F401
    except ImportError as error:
        raise unittest.SkipTest(f"GuideLLM is not installed in this environment: {error}") from None


class ArgvHelperTests(unittest.TestCase):
    def test_strips_the_leading_guidellm_run(self):
        self.assertEqual(
            telemetry.argv_for_run_command(["guidellm", "run", "--backend", "x"]),
            ["--backend", "x"],
        )

    def test_rejects_a_command_that_is_not_guidellm_run(self):
        with self.assertRaises(ValueError):
            telemetry.argv_for_run_command(["guidellm", "package", "--backend", "x"])


class ConfigurationEquivalenceTests(unittest.TestCase):
    """resolve_benchmark_scenario() must resolve *exactly* the configuration
    build_guidellm_command() already describes -- same backend, workload,
    profile (including warmup/cooldown), constraints, seed, metrics, and
    output behavior -- through GuideLLM's own CLI resolution code, whether
    or not telemetry is the thing calling it."""

    def setUp(self):
        _skip_unless_guidellm_pinned()

    def _resolve(self, profile_id: str, raw_output_path: Path):
        command = cli.build_guidellm_command(
            endpoint="http://127.0.0.1:9",
            model="Qwen/Qwen3-4B",
            profile_id=profile_id,
            raw_output_path=raw_output_path,
        )
        argv = telemetry.argv_for_run_command(command)
        scenario = telemetry.resolve_benchmark_scenario(argv)
        return telemetry.scenario_equivalence_summary(scenario)

    def test_chat_small_v1_resolves_to_the_declared_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_output_path = Path(tmp) / "out.json"
            summary = self._resolve("chat-small-v1", raw_output_path)
        spec = cli.PROFILE_SPECS["chat-small-v1"]
        self.assertEqual(summary["backend_target"], "http://127.0.0.1:9")
        self.assertEqual(summary["backend_model"], "Qwen/Qwen3-4B")
        self.assertEqual(summary["backend_request_format"], "/v1/chat/completions")
        self.assertEqual(summary["prompt_tokens"], spec["input_tokens"])
        self.assertEqual(summary["output_tokens"], spec["output_tokens"])
        self.assertEqual(summary["profile_kind"], "synchronous")
        constraint_json = dict(summary["constraints"])
        self.assertIn(f'"count":{spec["max_requests"]}', constraint_json["max_requests"])
        self.assertIn(f'"seconds":{spec["max_duration_seconds"]}', constraint_json["max_duration"])
        self.assertEqual(summary["seed"], {"kind": "static", "value": 20260828})
        self.assertEqual(summary["metrics_kind"], "generative")
        self.assertEqual(summary["outputs"], [("json", str(raw_output_path))])

    def test_chat_small_extended_v1_resolves_its_own_warmup_cooldown_contract(self):
        """Regression guard: the extended profile's request-count warmup/
        cooldown compensation (cooldown.value=4 to actually exclude 5) must
        resolve identically through this path -- telemetry must never be
        able to see, let alone change, a different warmup/cooldown."""
        with tempfile.TemporaryDirectory() as tmp:
            summary = self._resolve("chat-small-extended-v1", Path(tmp) / "out.json")
        spec = cli.PROFILE_SPECS["chat-small-extended-v1"]
        constraint_json = dict(summary["constraints"])
        self.assertIn(f'"count":{spec["max_requests"]}', constraint_json["max_requests"])
        self.assertEqual(summary["profile_kind"], "synchronous")

    def test_resolution_is_identical_whether_or_not_telemetry_is_the_caller(self):
        """Calling resolve_benchmark_scenario twice against the same argv --
        once as the "plain" resolution, once as the "for telemetry"
        resolution -- must produce the same configuration. Telemetry
        observes; it does not get a different, second configuration."""
        command = cli.build_guidellm_command(
            endpoint="http://127.0.0.1:9", model="Qwen/Qwen3-4B",
            profile_id="chat-small-v1", raw_output_path=Path("/tmp/out.json"),
        )
        argv = telemetry.argv_for_run_command(command)
        first = telemetry.scenario_equivalence_summary(telemetry.resolve_benchmark_scenario(argv))
        second = telemetry.scenario_equivalence_summary(telemetry.resolve_benchmark_scenario(argv))
        self.assertEqual(first, second)


class EtaEstimationTests(unittest.TestCase):
    """Pure-function tests; no GuideLLM needed."""

    def test_estimating_before_enough_observations(self):
        self.assertIsNone(telemetry.compute_eta_seconds(
            processed_requests=1, elapsed_since_first_request=5.0,
            remaining_requests=49, remaining_duration=800.0,
        ))

    def test_estimating_before_enough_elapsed_time(self):
        self.assertIsNone(telemetry.compute_eta_seconds(
            processed_requests=5, elapsed_since_first_request=0.5,
            remaining_requests=45, remaining_duration=800.0,
        ))

    def test_missing_remaining_requests_is_unknown(self):
        self.assertIsNone(telemetry.compute_eta_seconds(
            processed_requests=5, elapsed_since_first_request=10.0,
            remaining_requests=None, remaining_duration=800.0,
        ))

    def test_missing_everything_is_unknown(self):
        self.assertIsNone(telemetry.compute_eta_seconds(
            processed_requests=None, elapsed_since_first_request=None,
            remaining_requests=None, remaining_duration=None,
        ))

    def test_duration_ceiling_alone_is_never_reported_as_the_estimate(self):
        """Regression: at the very start of a run (no completions yet), the
        remaining duration ceiling alone (e.g. ~5400s for the extended
        profile) must not be surfaced as "the" ETA."""
        self.assertIsNone(telemetry.compute_eta_seconds(
            processed_requests=0, elapsed_since_first_request=0.1,
            remaining_requests=60, remaining_duration=5399.9,
        ))

    def test_typical_rate_based_estimate(self):
        eta = telemetry.compute_eta_seconds(
            processed_requests=10, elapsed_since_first_request=20.0,
            remaining_requests=40, remaining_duration=800.0,
        )
        # observed rate = 0.5 req/s -> 40 remaining / 0.5 = 80s, well under
        # the 800s duration ceiling, so the rate-based estimate wins.
        self.assertAlmostEqual(eta, 80.0)

    def test_a_slowdown_lengthens_the_estimate(self):
        fast = telemetry.compute_eta_seconds(
            processed_requests=10, elapsed_since_first_request=10.0,
            remaining_requests=40, remaining_duration=None,
        )
        slow = telemetry.compute_eta_seconds(
            processed_requests=10, elapsed_since_first_request=100.0,
            remaining_requests=40, remaining_duration=None,
        )
        self.assertLess(fast, slow)

    def test_duration_ceiling_caps_a_rate_based_estimate_that_would_exceed_it(self):
        eta = telemetry.compute_eta_seconds(
            processed_requests=3, elapsed_since_first_request=30.0,
            remaining_requests=1000, remaining_duration=50.0,
        )
        self.assertEqual(eta, 50.0)

    def test_zero_remaining_requests_is_exactly_zero_not_negative(self):
        eta = telemetry.compute_eta_seconds(
            processed_requests=10, elapsed_since_first_request=10.0,
            remaining_requests=0, remaining_duration=None,
        )
        self.assertEqual(eta, 0.0)

    def test_never_negative_or_infinite(self):
        eta = telemetry.compute_eta_seconds(
            processed_requests=10, elapsed_since_first_request=10.0,
            remaining_requests=0, remaining_duration=0.0,
        )
        self.assertIsNotNone(eta)
        self.assertGreaterEqual(eta, 0.0)
        self.assertTrue(eta < float("inf"))


@unittest.skipIf(os.name == "nt", "The fixture server and SIGTERM cancellation test use POSIX signal semantics")
class RealGuideLLMTelemetryIntegrationTests(unittest.TestCase):
    """Runs the real, pinned GuideLLM 0.7.3 entrypoint in-process, against a
    real (fixture) OpenAI-compatible streaming server, through this
    module's adapter -- not a fake guidellm executable."""

    def setUp(self):
        _skip_unless_guidellm_pinned()
        _require_offline_tokenizer(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_normal_completion_produces_telemetry_and_the_normal_report(self):
        server = _start_fixture_server()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}"
            raw_output = self.root / "guidellm-benchmarks.json"
            argv = _fixture_argv(endpoint=endpoint, output_path=raw_output, max_requests=4)
            returncode = telemetry.run_generative_benchmark_with_telemetry(
                argv,
                snapshot_path=self.root / "telemetry.json",
                history_path=self.root / "telemetry-history.json",
                stdout_log_path=self.root / "guidellm.stdout.log",
                run_label="test-run",
                total_measured_requests=4,
                min_emit_interval=0.0,
            )
        finally:
            server.shutdown()

        self.assertEqual(returncode, 0)
        snapshot = json.loads((self.root / "telemetry.json").read_text())
        self.assertEqual(snapshot["stage"], "saving")
        self.assertEqual(snapshot["remaining_fraction"], 0.0)
        self.assertGreater(len(snapshot["chart_history"]), 0)
        for sample in snapshot["chart_history"]:
            self.assertGreaterEqual(sample["output_tokens_per_second"], 0)

        # Final displayed results still come from the existing, unchanged
        # normalizer -- prove it can read this run's real raw output.
        compressed = cli.compress_file(raw_output)
        report = cli.load_gzip_json(compressed)
        normalized = cli.normalize_guidellm_report(report)
        self.assertEqual(normalized["results"]["requests"]["attempted"], 4)
        self.assertEqual(normalized["results"]["requests"]["succeeded"], 4)

    def test_rate_limiting_keeps_history_small_for_the_default_interval(self):
        server = _start_fixture_server()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}"
            raw_output = self.root / "guidellm-benchmarks.json"
            argv = _fixture_argv(endpoint=endpoint, output_path=raw_output, max_requests=3)
            telemetry.run_generative_benchmark_with_telemetry(
                argv,
                snapshot_path=self.root / "telemetry.json",
                history_path=self.root / "telemetry-history.json",
                stdout_log_path=self.root / "guidellm.stdout.log",
                run_label="test-run",
                total_measured_requests=3,
                # default (~once/second) min_emit_interval: this whole tiny
                # run finishes in well under a second, so almost nothing
                # should have been accepted into the bounded history.
            )
        finally:
            server.shutdown()
        snapshot = json.loads((self.root / "telemetry.json").read_text())
        self.assertLessEqual(len(snapshot["chart_history"]), 1)

    def test_observer_failure_disables_telemetry_without_failing_the_benchmark(self):
        server = _start_fixture_server()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}"
            raw_output = self.root / "guidellm-benchmarks.json"
            argv = _fixture_argv(endpoint=endpoint, output_path=raw_output, max_requests=3)
            unwritable_snapshot = self.root / "does" / "not" / "exist" / "telemetry.json"
            returncode = telemetry.run_generative_benchmark_with_telemetry(
                argv,
                snapshot_path=unwritable_snapshot,
                history_path=unwritable_snapshot.with_name("history.json"),
                stdout_log_path=self.root / "guidellm.stdout.log",
                run_label="test-run",
                total_measured_requests=3,
                min_emit_interval=0.0,
            )
        finally:
            server.shutdown()
        self.assertEqual(returncode, 0, "a telemetry write failure must never fail the benchmark itself")
        self.assertFalse(unwritable_snapshot.exists())
        self.assertTrue(raw_output.exists() or raw_output.with_suffix(".json.gz").exists())

    def test_backend_failure_is_a_real_failure_not_masked_by_telemetry(self):
        """A target nobody is listening on: GuideLLM itself still exits 0
        (the *tool* ran; every request simply errored -- the same
        "execution completed, but look at the error rate" distinction
        OmoBench's own acceptance rules already draw). What must never
        happen is telemetry quietly reporting these as successes."""
        probe = ThreadingHTTPServer(("127.0.0.1", 0), _StreamingChatHandler)
        closed_port = probe.server_port
        probe.server_close()  # closed immediately: nothing is listening there

        raw_output = self.root / "guidellm-benchmarks.json"
        argv = _fixture_argv(endpoint=f"http://127.0.0.1:{closed_port}", output_path=raw_output, max_requests=3)
        telemetry.run_generative_benchmark_with_telemetry(
            argv,
            snapshot_path=self.root / "telemetry.json",
            history_path=self.root / "telemetry-history.json",
            stdout_log_path=self.root / "guidellm.stdout.log",
            run_label="test-run",
            total_measured_requests=3,
            min_emit_interval=0.0,
        )
        self.assertTrue(raw_output.exists() or raw_output.with_suffix(".json.gz").exists())
        report_path = raw_output if raw_output.exists() else raw_output.with_suffix(".json.gz")
        if report_path.suffix == ".gz":
            report = json.loads(gzip.decompress(report_path.read_bytes()))
        else:
            report = json.loads(report_path.read_text())
        benchmark = report["benchmarks"][0]
        self.assertEqual(benchmark["metrics"]["request_totals"]["successful"], 0)
        self.assertGreater(
            benchmark["metrics"]["request_totals"]["errored"] + benchmark["metrics"]["request_totals"]["incomplete"], 0
        )
        snapshot = json.loads((self.root / "telemetry.json").read_text())
        self.assertEqual(snapshot["requests"]["successful"], 0)

    def test_sigterm_cancels_an_in_process_run_like_the_subprocess_path_does(self):
        """Runs the adapter in a real subprocess (SIGTERM to the current
        process would kill the test itself) against a deliberately slow
        fixture server, then cancels it exactly the way
        Controller.cancel() already does -- SIGTERM, a bounded grace
        period, then an unconditional SIGKILL fallback -- confirming this
        in-process path is stopped within that same existing guarantee,
        not a new, purely-graceful-cancellation promise this module does
        not need to make on its own."""
        server = _start_fixture_server(per_request_delay_seconds=0.5)
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}"
            raw_output = self.root / "guidellm-benchmarks.json"
            script = f"""
import sys
sys.path.insert(0, {str(ROOT / "cli" / "src")!r})
from pathlib import Path
from omobench import telemetry
rc = telemetry.run_generative_benchmark_with_telemetry(
    {_fixture_argv(endpoint=endpoint, output_path=raw_output, max_requests=50, max_duration_seconds=120)!r},
    snapshot_path=Path({str(self.root / "telemetry.json")!r}),
    history_path=Path({str(self.root / "telemetry-history.json")!r}),
    stdout_log_path=Path({str(self.root / "guidellm.stdout.log")!r}),
    run_label="test-run",
    total_measured_requests=50,
    min_emit_interval=0.0,
)
sys.exit(rc)
"""
            process = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
            snapshot_path = self.root / "telemetry.json"
            deadline = time.monotonic() + 90
            # Wait for real proof the run is inside its signal-protected
            # execution phase (setup -- tokenizer/backend resolution -- can
            # itself take a while under load) rather than guessing a fixed
            # delay that could land before the SIGTERM handler is even
            # installed.
            while not snapshot_path.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(snapshot_path.exists(), "Setup never reached the running phase within 90s.")
            time.sleep(0.2)  # into the middle of the (slow, 0.5s/request) run

            # Mirrors ui.py Controller.cancel() exactly: SIGTERM, a short
            # grace period, then an unconditional SIGKILL -- the real
            # guarantee a cancelled job actually gets in production, not
            # graceful asyncio cancellation alone (which this module
            # attempts, via loop.add_signal_handler, but does not need to
            # guarantee completes within any particular bound by itself).
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.fail("The process was not stopped within the Controller's own SIGTERM+SIGKILL guarantee.")
        finally:
            server.shutdown()
        self.assertNotEqual(returncode, 0, "A cancelled run must not report success.")


if __name__ == "__main__":
    unittest.main()
