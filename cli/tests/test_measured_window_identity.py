"""Which requests end up measured, named one by one.

The earlier phase tests asserted totals. Totals hid three things:

  * a run that expired during warmup reported four *warmup* requests as
    measured, because GuideLLM's measured window falls back to the whole run
    when the warmup boundary is never crossed;
  * "requests served" counts quoted from the fixture included the readiness
    probe this harness sends before timing anything, which is not a benchmark
    request;
  * "59 served" against "50 measured + 5 warmup + 3 cooldown" never said which
    requests were in flight and which were never dispatched at all.

So these tests identify every request. The fixture stamps its arrival index
into the response id, which GuideLLM carries through to
`benchmark.requests.*[].response_id`, so a record in the report can be matched
to the exact arrival at the server. Nothing is inferred from a count.

No model and no inference: canned streamed tokens and a tokenizer generated in
the test.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from omobench import cli

from test_chat_small_v2 import MODEL, build_tokenizer, guidellm_available


ROOT = Path(__file__).resolve().parents[2]
CRLF = b"\r\n"

# What the profile promises, and what the runner is configured to enforce.
DECLARED_WARMUP = 5
DECLARED_MEASURED = 50
DECLARED_COOLDOWN = 5
SCHEDULED = DECLARED_WARMUP + DECLARED_MEASURED + DECLARED_COOLDOWN


class IdentifyingFixture:
    """A streaming server that names every request it answers.

    Arrival n is answered with `id: "chatcmpl-n"`, which reaches the report as
    `response_id`. `arrivals` is the ordered list of indexes it was asked for,
    so "which requests reached the server" is recorded by the server itself
    rather than reconstructed from the client's view.
    """

    def __init__(self, seconds_per_request: float = 0.02, chunks: int = 4):
        self.seconds = seconds_per_request
        self.chunks = chunks
        self.arrivals: list[int] = []
        # The backend's own readiness traffic: GET /health and GET /v1/models,
        # issued by OpenAIHTTPBackend.validate() before any benchmark request.
        # Recorded separately so it can never be counted as workload.
        self.validations: list[str] = []
        self._lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _json(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                with fixture._lock:
                    fixture.validations.append(self.path)
                if self.path.rstrip("/").endswith("health"):
                    self._json(200, {"status": "ok"})
                elif self.path.startswith("/v1/models"):
                    self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
                else:
                    self._json(404, {})

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with fixture._lock:
                    fixture.arrivals.append(len(fixture.arrivals) + 1)
                    index = fixture.arrivals[-1]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()

                def emit(text: str) -> None:
                    payload = text.encode()
                    self.wfile.write(("%X" % len(payload)).encode() + CRLF + payload + CRLF)
                    self.wfile.flush()

                try:
                    for position in range(fixture.chunks):
                        threading.Event().wait(fixture.seconds / max(1, fixture.chunks))
                        emit("data: " + json.dumps({
                            "id": f"chatcmpl-{index}", "object": "chat.completion.chunk", "model": MODEL,
                            "choices": [{"index": 0, "delta": {"content": f"w{100 + position} "},
                                         "finish_reason": None}]}) + "\n\n")
                    emit("data: " + json.dumps({
                        "id": f"chatcmpl-{index}", "object": "chat.completion.chunk", "model": MODEL,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1024, "completion_tokens": fixture.chunks,
                                  "total_tokens": 1024 + fixture.chunks}}) + "\n\n")
                    emit("data: [DONE]\n\n")
                    self.wfile.write(b"0" + CRLF + CRLF)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):  # pragma: no cover - client gone
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class Ledger:
    """Every request of one run, placed by identity rather than by count."""

    def __init__(self, report: dict, arrivals: list[int], readiness: int):
        self.report = report
        benchmark = report["benchmarks"][0]
        self.benchmark = benchmark
        metrics = benchmark["scheduler_metrics"]
        self.window = (metrics["measure_start_time"], metrics["measure_end_time"])
        self.run_start = metrics["start_time"]
        self.scheduled = benchmark["scheduler_metrics"]["requests_made"]
        self.totals = benchmark["metrics"]["request_totals"]
        self.warmup_duration = benchmark["warmup_duration"]
        self.cooldown_duration = benchmark["cooldown_duration"]

        # The readiness probe is arrival 1..readiness; benchmark request k is
        # arrival k + readiness. Everything below is stated as benchmark
        # request numbers, so "request 1" is the first request of the run.
        self.readiness = readiness
        self.served = [n - readiness for n in arrivals[readiness:]]

        self.before: list[int] = []
        self.inside: list[int] = []
        self.after: list[int] = []
        self.undispatched = 0
        for bucket in ("successful", "errored", "incomplete"):
            for record in benchmark["requests"].get(bucket) or []:
                response_id = record.get("response_id")
                started = record.get("request_start_time")
                if response_id is None or started is None:
                    # Reached the server but never produced a response id, or
                    # never got far enough to have a start time: in flight when
                    # the run stopped.
                    self.undispatched += 1
                    continue
                number = int(response_id.split("-")[1]) - readiness
                start, end = self.window
                (self.inside if start <= started <= end
                 else self.before if started < start else self.after).append(number)
        for group in (self.before, self.inside, self.after):
            group.sort()

    @property
    def identified(self) -> int:
        """Requests the server answered far enough to stamp with an identity."""
        return len(self.before) + len(self.inside) + len(self.after)

    @property
    def in_flight_or_undispatched(self) -> int:
        """Scheduled but never completed with an identity of their own."""
        return self.scheduled["total"] - self.identified

    @property
    def in_flight_at_the_server(self) -> int:
        """Reached the fixture but was still open when the run stopped.

        The fixture logs an arrival the moment a POST is accepted, before it
        writes a single chunk, so the gap between what it served and what
        carries an identity is exactly the set of requests cut off mid-answer.
        """
        return len(self.served) - self.identified

    @property
    def never_dispatched(self) -> int:
        """Scheduled, then abandoned before any POST was sent."""
        return self.in_flight_or_undispatched - self.in_flight_at_the_server

    def describe(self) -> str:
        return json.dumps({
            "scheduled": self.scheduled,
            "measured_totals": self.totals,
            "served_by_the_fixture": len(self.served),
            "readiness_probes_excluded": self.readiness,
            "warmup_duration": self.warmup_duration,
            "cooldown_duration": self.cooldown_duration,
            "before_window": self.before,
            "inside_window_first_last": [self.inside[:3], self.inside[-3:]] if self.inside else [],
            "inside_window_count": len(self.inside),
            "after_window": self.after,
            "in_flight_at_the_server": self.in_flight_at_the_server,
            "never_dispatched": self.never_dispatched,
        }, indent=2)


class RunMixin:
    """Run the production command, and say exactly what was overridden."""

    @classmethod
    def setUpClass(cls):
        guidellm_available()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.tokenizer = build_tokenizer(Path(cls.temporary.name) / "tokenizer")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def build(self, profile_id, endpoint, output, *, max_duration=None):
        """The production command, with at most one documented substitution.

        The only thing these tests ever change is the duration ceiling, and
        only to make a long profile finish inside a test run. Nothing else --
        not the phase mapping, not the request cap, not the error rate, not the
        seed -- is touched, so a count that comes back is the count the shipped
        configuration produces.
        """
        command = cli.build_guidellm_command(
            root=ROOT, endpoint=endpoint, model=MODEL, profile_id=profile_id,
            raw_output_path=output, tokenizer=str(self.tokenizer))
        if max_duration is None:
            return command, []
        replaced = [f"kind=max_duration,seconds={max_duration}"
                    if part.startswith("kind=max_duration") else part for part in command]
        overrides = [(was, now) for was, now in zip(command, replaced, strict=True) if was != now]
        return replaced, overrides

    def run_profile(self, profile_id, *, seconds_per_request, max_duration=None, timeout=420):
        with IdentifyingFixture(seconds_per_request=seconds_per_request) as fixture:
            # Prove a streamed POST is served before anything is timed. This is
            # a harness request, not a benchmark request, and every number
            # below subtracts it; earlier reports quoted "served" totals that
            # had not.
            probe = subprocess.run(
                ["curl", "-s", "-o", os.devnull, "-X", "POST",
                 f"{fixture.endpoint}/v1/chat/completions", "-H", "Content-Type: application/json",
                 "-d", json.dumps({"model": MODEL, "messages": [], "stream": True})],
                capture_output=True)
            self.assertEqual(probe.returncode, 0, "the fixture did not answer a streaming POST")
            readiness = len(fixture.arrivals)
            self.assertEqual(readiness, 1, "the readiness probe should be exactly one request")

            output = Path(self.temporary.name) / f"{profile_id}-{seconds_per_request}-{max_duration}.json"
            command, overrides = self.build(profile_id, fixture.endpoint, output,
                                            max_duration=max_duration)
            environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                           "TOKENIZERS_PARALLELISM": "false"}
            completed = subprocess.run(cli.guidellm_runner_invocation(command),
                                       capture_output=True, text=True, env=environment,
                                       timeout=timeout)
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            ledger = Ledger(json.loads(output.read_text(encoding="utf-8")),
                            fixture.arrivals, readiness)
            ledger.overrides = overrides
            ledger.validations = list(fixture.validations)
            return ledger


class DisclosedOverridesTests(RunMixin, unittest.TestCase):
    """What these tests change about the shipped command, stated as a test."""

    def test_only_the_duration_ceiling_is_ever_substituted(self):
        _, overrides = self.build("chat-small-v2", "http://127.0.0.1:1", Path("x.json"),
                                  max_duration=8)
        self.assertEqual(overrides, [("kind=max_duration,seconds=900",
                                      "kind=max_duration,seconds=8")])

    def test_the_phase_mapping_and_request_cap_are_never_overridden(self):
        command, _ = self.build("chat-small-v2", "http://127.0.0.1:1", Path("x.json"),
                                max_duration=8)
        self.assertIn("kind=synchronous,warmup.value=4,warmup.mode=requests,"
                      "cooldown.value=4,cooldown.mode=requests", command)
        self.assertIn("kind=max_requests,count=60", command)
        self.assertIn("kind=max_error_rate,rate=0.01", command)

    def test_the_v1_command_keeps_its_own_fifty_request_cap(self):
        """v1 is never run with v2's 60-request budget.

        An earlier report quoted the v1 contrast as having served 60 requests.
        It does not: v1's shipped cap is 50, and nothing here raises it.
        """
        command, overrides = self.build("chat-small-v1", "http://127.0.0.1:1", Path("x.json"),
                                        max_duration=20)
        self.assertIn("kind=max_requests,count=50", command)
        self.assertNotIn("kind=max_requests,count=60", command)
        self.assertIn("kind=synchronous,warmup=0.1,cooldown=0.1", command)
        self.assertEqual(overrides, [("kind=max_duration,seconds=900",
                                      "kind=max_duration,seconds=20")])


class IntegrityRuleTests(unittest.TestCase):
    """The rule itself, without running anything.

    It must fire on one positive observation and nothing else: a report that
    omits the field, or that configures no warmup, is unverifiable rather than
    broken, and rejecting it would turn a missing field into a failed run.
    """

    def integrity(self, **benchmark):
        return cli.measured_window_integrity(benchmark)

    def test_a_configured_warmup_recorded_as_zero_is_the_fallback(self):
        result = self.integrity(
            config={"warmup": {"percent": None, "value": 4, "mode": "requests"}},
            warmup_duration=0.0)
        self.assertFalse(result["warmup_boundary_crossed"])
        self.assertTrue(result["measured_window_is_the_whole_run"])

    def test_a_configured_warmup_with_a_real_duration_is_fine(self):
        result = self.integrity(
            config={"warmup": {"percent": None, "value": 4, "mode": "requests"}},
            warmup_duration=0.16)
        self.assertTrue(result["warmup_boundary_crossed"])

    def test_a_percentage_warmup_is_configured_too(self):
        result = self.integrity(
            config={"warmup": {"percent": 0.1, "value": None, "mode": "prefer_duration"}},
            warmup_duration=0.0)
        self.assertFalse(result["warmup_boundary_crossed"])

    def test_no_warmup_configured_is_not_a_fallback(self):
        result = self.integrity(
            config={"warmup": {"percent": None, "value": None, "mode": "requests"}},
            warmup_duration=0.0)
        self.assertTrue(result["warmup_boundary_crossed"])
        self.assertFalse(result["warmup_configured"])

    def test_a_report_without_the_field_is_not_rejected(self):
        result = self.integrity(config={"warmup": {"percent": None, "value": 4, "mode": "requests"}})
        self.assertTrue(result["warmup_boundary_crossed"])
        self.assertIsNone(result["warmup_duration_seconds"])

    def test_a_report_without_a_config_block_is_not_rejected(self):
        self.assertTrue(self.integrity(warmup_duration=0.0)["warmup_boundary_crossed"])
        self.assertTrue(self.integrity()["warmup_boundary_crossed"])


class CompleteRunIdentityTests(RunMixin, unittest.TestCase):
    """A run that finishes: which requests were measured, by name."""

    def test_the_measured_set_is_exactly_requests_six_to_fifty_five(self):
        ledger = self.run_profile("chat-small-v2", seconds_per_request=0.02)
        self.assertEqual(ledger.scheduled["total"], SCHEDULED, ledger.describe())
        self.assertEqual(len(ledger.served), SCHEDULED, ledger.describe())
        self.assertEqual(ledger.before, list(range(1, DECLARED_WARMUP + 1)), ledger.describe())
        self.assertEqual(ledger.inside, list(range(DECLARED_WARMUP + 1, DECLARED_WARMUP + 1 + DECLARED_MEASURED)),
                         ledger.describe())
        self.assertEqual(ledger.after, list(range(SCHEDULED - DECLARED_COOLDOWN + 1, SCHEDULED + 1)),
                         ledger.describe())
        self.assertEqual(ledger.totals["total"], DECLARED_MEASURED, ledger.describe())

    def test_every_scheduled_request_is_accounted_for(self):
        ledger = self.run_profile("chat-small-v2", seconds_per_request=0.02)
        self.assertEqual(
            len(ledger.before) + len(ledger.inside) + len(ledger.after)
            + ledger.in_flight_or_undispatched,
            ledger.scheduled["total"], ledger.describe())
        self.assertEqual(ledger.in_flight_or_undispatched, 0, ledger.describe())


class RequestKindTests(RunMixin, unittest.TestCase):
    """Four different things have been called "requests". They are not the same."""

    def test_validation_retries_and_workload_are_kept_apart(self):
        ledger = self.run_profile("chat-small-v2", seconds_per_request=0.02)

        # 1. Validation. OpenAIHTTPBackend.validate() GETs /health and
        #    /v1/models before the run. They are not workload and never reach
        #    the arrival log, which only records completion POSTs.
        self.assertTrue(ledger.validations,
                        "the backend did not validate; this test is not testing anything")
        self.assertTrue(all(path.startswith("/health") or path.startswith("/v1/models")
                            for path in ledger.validations), ledger.validations)

        # 2. The harness readiness probe: one streamed POST, subtracted
        #    everywhere. It is the reason earlier "served" figures were one
        #    too high.
        self.assertEqual(ledger.readiness, 1)

        # 3. Scheduled benchmark requests: what the runner planned.
        self.assertEqual(ledger.scheduled["total"], SCHEDULED)

        # 4. Server-observed benchmark requests: what the fixture was asked
        #    for, excluding the probe. One arrival per scheduled request, each
        #    index once and in order.
        self.assertEqual(ledger.served, list(range(1, SCHEDULED + 1)), ledger.describe())

    def test_no_request_is_ever_retried(self):
        """GuideLLM 0.7.3 has no request-level retry, and this shows it.

        OpenAIHTTPBackendArgs carries no retry setting, and httpx's transport
        defaults to none. A retry would make the fixture see more arrivals
        than the scheduler scheduled; it sees exactly as many, each identity
        appearing once.
        """
        ledger = self.run_profile("chat-small-v2", seconds_per_request=0.02)
        self.assertEqual(len(ledger.served), len(set(ledger.served)), "an identity was served twice")
        self.assertEqual(len(ledger.served), ledger.scheduled["total"] - ledger.never_dispatched,
                         ledger.describe())
        identified = ledger.before + ledger.inside + ledger.after
        self.assertEqual(len(identified), len(set(identified)), "an identity was reported twice")


class WarmupExpiryTests(RunMixin, unittest.TestCase):
    """The case that was reported as "4 measured". None of them were measured."""

    def run_expiring_in_warmup(self):
        # 0.5 s per request against a 2 s ceiling: the run stops while still
        # inside the five warmup requests, so the boundary is never crossed.
        return self.run_profile("chat-small-v2", seconds_per_request=0.5, max_duration=2,
                                timeout=120)

    def test_the_warmup_boundary_is_never_crossed(self):
        ledger = self.run_expiring_in_warmup()
        self.assertLessEqual(len(ledger.served), DECLARED_WARMUP, ledger.describe())
        # Exactly zero, by construction: measure_start fell back to
        # request_start, so warmup_duration is one value minus itself.
        self.assertEqual(ledger.warmup_duration, 0.0, ledger.describe())

    def test_warmup_requests_are_what_the_raw_report_calls_measured(self):
        """The defect itself, pinned so it cannot be mistaken for a fix.

        Nothing lands *before* the window, every served request lands inside
        it, and those requests are warmup requests one through four or five.
        """
        ledger = self.run_expiring_in_warmup()
        self.assertEqual(ledger.before, [], ledger.describe())
        self.assertTrue(ledger.inside, ledger.describe())
        self.assertTrue(all(number <= DECLARED_WARMUP for number in ledger.inside),
                        ledger.describe())
        self.assertEqual(ledger.totals["total"], len(ledger.inside), ledger.describe())

    def test_omobench_refuses_to_normalize_it_as_a_measurement(self):
        ledger = self.run_expiring_in_warmup()
        integrity = cli.measured_window_integrity(ledger.benchmark)
        self.assertTrue(integrity["warmup_configured"])
        self.assertFalse(integrity["warmup_boundary_crossed"])
        self.assertTrue(integrity["measured_window_is_the_whole_run"])
        with self.assertRaises(ValueError) as caught:
            cli.normalize_guidellm_report(ledger.report)
        self.assertIn("never left its warmup phase", str(caught.exception))
        self.assertIn("no measured window", str(caught.exception))

    def test_a_complete_run_is_not_refused_by_the_same_check(self):
        ledger = self.run_profile("chat-small-v2", seconds_per_request=0.02)
        integrity = cli.measured_window_integrity(ledger.benchmark)
        self.assertTrue(integrity["warmup_boundary_crossed"])
        self.assertFalse(integrity["measured_window_is_the_whole_run"])
        normalized = cli.normalize_guidellm_report(ledger.report)
        self.assertEqual(normalized["results"]["requests"]["attempted"], DECLARED_MEASURED)


class LateExpiryLedgerTests(RunMixin, unittest.TestCase):
    """Expiry after measurement began: every scheduled request placed."""

    def test_the_ledger_balances_and_warmup_is_still_excluded(self):
        # Long enough to cross the warmup boundary and measure, short enough
        # that the run cannot reach its 60th request.
        ledger = self.run_profile("chat-small-v2", seconds_per_request=0.1, max_duration=4,
                                  timeout=180)
        self.assertEqual(ledger.warmup_duration > 0.0, True, ledger.describe())

        # The five warmup requests are excluded, by identity, even though the
        # run ended early.
        self.assertEqual(ledger.before, list(range(1, DECLARED_WARMUP + 1)), ledger.describe())

        # The measured set starts at request six and is contiguous: a truncated
        # run measures a prefix of the measured window, never a gap.
        self.assertTrue(ledger.inside, ledger.describe())
        self.assertEqual(ledger.inside[0], DECLARED_WARMUP + 1, ledger.describe())
        self.assertEqual(ledger.inside, list(range(ledger.inside[0], ledger.inside[-1] + 1)),
                         ledger.describe())
        self.assertLessEqual(len(ledger.inside), DECLARED_MEASURED, ledger.describe())

        # And nothing is lost: scheduled == excluded + measured + cooldown
        # + the ones with no identity, which are those in flight at the server
        # when it stopped plus those never dispatched at all.
        self.assertEqual(
            len(ledger.before) + len(ledger.inside) + len(ledger.after)
            + ledger.in_flight_or_undispatched,
            ledger.scheduled["total"], ledger.describe())
        self.assertEqual(ledger.totals["total"], len(ledger.inside), ledger.describe())

        # The fixture's own view agrees, and splits the leftovers: what it
        # served is what carries an identity plus what was still open, and the
        # rest were never dispatched at all. The readiness probe is in neither.
        self.assertEqual(len(ledger.served),
                         ledger.identified + ledger.in_flight_at_the_server,
                         ledger.describe())
        self.assertGreaterEqual(ledger.in_flight_at_the_server, 0, ledger.describe())
        self.assertGreaterEqual(ledger.never_dispatched, 0, ledger.describe())


class ProductionV1ControlTests(RunMixin, unittest.TestCase):
    """The v1 contrast, run with its own shipped configuration."""

    def test_v1_stops_at_fifty_requests_and_measures_none_of_them(self):
        """50 scheduled, not 60, against the unchanged production configuration.

        A shortened ceiling also shortens the clock warmup. A two-second
        warmup was consumed by worker startup on Python 3.14, invalidating the
        old control's assumption of zero measurements. Keep the real 90-second
        warmup; the 50-request cap still ends this fixture promptly.
        """
        ledger = self.run_profile("chat-small-v1", seconds_per_request=0.02)
        self.assertEqual(ledger.overrides, [])
        self.assertEqual(ledger.scheduled["total"], 50, ledger.describe())
        self.assertEqual(len(ledger.served), 50, ledger.describe())
        self.assertEqual(ledger.before, list(range(1, 51)), ledger.describe())
        self.assertEqual(ledger.inside, [], ledger.describe())
        self.assertEqual(ledger.after, [], ledger.describe())
        self.assertEqual(ledger.in_flight_or_undispatched, 0, ledger.describe())
        self.assertEqual(ledger.totals["total"], 0, ledger.describe())
        with self.assertRaises(ValueError):
            cli.normalize_guidellm_report(ledger.report)


if __name__ == "__main__":
    unittest.main()
