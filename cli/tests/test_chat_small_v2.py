"""chat-small-v2 measures the requests it declares, on fast and slow hardware.

chat-small-v1's `warmup=0.1` is resolved by GuideLLM 0.7.3 against *duration*,
not requests, so its measured window is a fixed slice of the clock and the
number of requests inside it depends entirely on per-request latency. v2 counts
requests instead. These tests prove that end to end: OmoBench's own command
construction, the pinned runner's real scheduler and accumulator, its real
report, and OmoBench's own normalization -- against an isolated streaming
fixture server and a tokenizer built here.

Nothing is inferred and nothing is downloaded: the fixture answers with canned
tokens, and the tokenizer is a WordLevel vocabulary generated in the test.
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


ROOT = Path(__file__).resolve().parents[2]
MODEL = "fixture/stream-model"
CRLF = b"\r\n"


def build_tokenizer(target: Path) -> Path:
    """A tiny local tokenizer, generated offline."""
    try:
        from tokenizers import Tokenizer, decoders, models, pre_tokenizers
    except ImportError as error:  # pragma: no cover - environment without the runner extra
        raise unittest.SkipTest(f"tokenizers is not installed: {error}") from None
    target.mkdir(parents=True, exist_ok=True)
    vocabulary = {"<unk>": 0, "<pad>": 1, "<s>": 2, "</s>": 3}
    vocabulary.update({f"w{index}": index for index in range(4, 4096)})
    tokenizer = Tokenizer(models.WordLevel(vocab=vocabulary, unk_token="<unk>"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.decoder = decoders.WordPiece(prefix="")
    tokenizer.save(str(target / "tokenizer.json"))
    (target / "tokenizer_config.json").write_text(json.dumps({
        "tokenizer_class": "PreTrainedTokenizerFast", "unk_token": "<unk>", "pad_token": "<pad>",
        "bos_token": "<s>", "eos_token": "</s>", "model_max_length": 32768,
        "clean_up_tokenization_spaces": False,
    }) + "\n", encoding="utf-8")
    (target / "special_tokens_map.json").write_text(json.dumps({
        "unk_token": "<unk>", "pad_token": "<pad>", "bos_token": "<s>", "eos_token": "</s>",
    }) + "\n", encoding="utf-8")
    return target


class StreamingFixture:
    """An OpenAI-compatible server that streams canned tokens and logs arrivals."""

    def __init__(self, seconds_per_request: float = 0.02, chunks: int = 4):
        self.seconds = seconds_per_request
        self.chunks = chunks
        self.arrivals: list[int] = []
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
                    index = len(fixture.arrivals)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                # Explicit framing: a keep-alive connection cannot otherwise tell
                # where a streamed response ends.
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
                except (BrokenPipeError, ConnectionResetError):  # pragma: no cover - client went away
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


def guidellm_available() -> None:
    if cli.guidellm_installed_version() != cli.GUIDELLM_VERSION:
        raise unittest.SkipTest("the pinned GuideLLM is not installed in this environment")


class ProfileRegistrationTests(unittest.TestCase):
    """What v2 declares, and that v1 is untouched."""

    def test_v2_is_registered_with_request_counted_phases(self):
        spec = cli.PROFILE_SPECS["chat-small-v2"]
        self.assertIn("warmup.mode=requests", spec["profile"])
        self.assertIn("cooldown.mode=requests", spec["profile"])
        self.assertEqual(spec["max_requests"], 60)
        self.assertEqual(spec["max_duration_seconds"], 900)

    def test_v2_keeps_the_short_profiles_workload_and_ceiling(self):
        first, second = cli.PROFILE_SPECS["chat-small-v1"], cli.PROFILE_SPECS["chat-small-v2"]
        self.assertEqual(first["input_tokens"], second["input_tokens"])
        self.assertEqual(first["output_tokens"], second["output_tokens"])
        self.assertEqual(first["max_duration_seconds"], second["max_duration_seconds"])

    def test_v2_keeps_the_short_profiles_acceptance_thresholds(self):
        import yaml

        def acceptance(profile_id):
            document = yaml.safe_load((ROOT / "benchmarks" / "profiles" / f"{profile_id}.yaml")
                                      .read_text(encoding="utf-8"))
            return document["acceptance"]

        self.assertEqual(acceptance("chat-small-v2"), acceptance("chat-small-v1"))
        self.assertEqual(acceptance("chat-small-v2"),
                         {"minimum_successful_requests": 40, "maximum_error_rate_percent": 1})

    def test_v1_is_unchanged(self):
        spec = cli.PROFILE_SPECS["chat-small-v1"]
        self.assertEqual(spec["profile"], "kind=synchronous,warmup=0.1,cooldown=0.1")
        self.assertEqual(spec["max_requests"], 50)
        contract = cli.profile_phase_contract(ROOT, "chat-small-v1")
        self.assertEqual(contract["phase_basis"], "duration")
        self.assertFalse(contract["declaration_matches_execution"])

    def test_the_extended_profile_is_unchanged(self):
        self.assertEqual(
            cli.PROFILE_SPECS["chat-small-extended-v1"]["profile"],
            "kind=synchronous,warmup.value=4,warmup.mode=requests,"
            "cooldown.value=4,cooldown.mode=requests")
        self.assertEqual(cli.PROFILE_SPECS["chat-small-extended-v1"]["max_requests"], 60)

    def test_v2s_contract_matches_its_declaration(self):
        contract = cli.profile_phase_contract(ROOT, "chat-small-v2")
        self.assertEqual(contract["phase_basis"], "requests")
        self.assertEqual(contract["warmup"], 5)       # actually excluded
        self.assertEqual(contract["cooldown"], 5)
        self.assertEqual(contract["warmup_configured"], 4)  # asked for
        self.assertEqual(contract["measured_window_requests"], 50)
        self.assertTrue(contract["declaration_matches_execution"])

    def test_the_two_profiles_stay_separately_identified(self):
        first = cli.sha256_file(ROOT / "benchmarks" / "profiles" / "chat-small-v1.yaml")
        second = cli.sha256_file(ROOT / "benchmarks" / "profiles" / "chat-small-v2.yaml")
        self.assertNotEqual(first, second)


class EndToEndPhaseTests(unittest.TestCase):
    """The pinned runner, driven by OmoBench's own command, against a fixture."""

    @classmethod
    def setUpClass(cls):
        guidellm_available()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.tokenizer = build_tokenizer(Path(cls.temporary.name) / "tokenizer")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_profile(self, profile_id, *, seconds_per_request, max_duration=None, timeout=420):
        with StreamingFixture(seconds_per_request=seconds_per_request) as fixture:
            # Warm the connection and prove a streamed POST is served before
            # anything is timed; this arrival is excluded from the counts below.
            probe = subprocess.run(
                ["curl", "-s", "-o", os.devnull, "-X", "POST",
                 f"{fixture.endpoint}/v1/chat/completions", "-H", "Content-Type: application/json",
                 "-d", json.dumps({"model": MODEL, "messages": [], "stream": True})],
                capture_output=True)
            self.assertEqual(probe.returncode, 0, "the fixture server did not answer a streaming POST")
            readiness_arrivals = len(fixture.arrivals)

            output = Path(self.temporary.name) / f"{profile_id}-{seconds_per_request}.json"
            command = cli.build_guidellm_command(
                root=ROOT, endpoint=fixture.endpoint, model=MODEL,
                profile_id=profile_id, raw_output_path=output,
                tokenizer=str(self.tokenizer))
            if max_duration is not None:
                command = [f"kind=max_duration,seconds={max_duration}"
                           if part.startswith("kind=max_duration") else part for part in command]
            environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                           "TOKENIZERS_PARALLELISM": "false"}
            completed = subprocess.run(cli.guidellm_runner_invocation(command),
                                       capture_output=True, text=True, env=environment, timeout=timeout)
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            report = json.loads(output.read_text(encoding="utf-8"))
            benchmark = report["benchmarks"][0]
            return {
                "measured": benchmark["metrics"]["request_totals"],
                "all_phases": benchmark["scheduler_metrics"]["requests_made"],
                "benchmark_arrivals": len(fixture.arrivals) - readiness_arrivals,
                "report": report,
            }

    def test_a_complete_run_measures_exactly_fifty_of_sixty_requests(self):
        outcome = self.run_profile("chat-small-v2", seconds_per_request=0.02)
        self.assertEqual(outcome["benchmark_arrivals"], 60)
        self.assertEqual(outcome["all_phases"]["total"], 60)
        self.assertEqual(outcome["measured"],
                         {"successful": 50, "errored": 0, "incomplete": 0, "total": 50})

    def test_the_same_counts_hold_when_every_request_is_slow(self):
        outcome = self.run_profile("chat-small-v2", seconds_per_request=0.4)
        self.assertEqual(outcome["benchmark_arrivals"], 60)
        self.assertEqual(outcome["measured"]["total"], 50)
        self.assertEqual(outcome["measured"]["successful"], 50)

    def test_omobench_normalizes_the_runs_own_report(self):
        outcome = self.run_profile("chat-small-v2", seconds_per_request=0.02)
        normalized = cli.normalize_guidellm_report(outcome["report"])
        self.assertEqual(normalized["results"]["requests"]["attempted"], 50)
        self.assertEqual(normalized["results"]["requests"]["succeeded"], 50)
        self.assertEqual(normalized["results"]["error_rate_percent"], 0.0)
        self.assertEqual(normalized["runner_version"], cli.GUIDELLM_VERSION)

    def test_a_run_cut_short_reports_what_it_measured_and_not_the_target(self):
        # The ceiling expires part-way through the measured window.
        outcome = self.run_profile("chat-small-v2", seconds_per_request=0.4, max_duration=8)
        measured = outcome["measured"]
        self.assertLess(measured["total"], 50)
        self.assertGreater(measured["total"], 0)
        self.assertLess(outcome["benchmark_arrivals"], 60)

    def test_the_v1_profile_still_measures_by_the_clock(self):
        """The contrast, on the same fixture: a fast host measures far fewer."""
        outcome = self.run_profile("chat-small-v1", seconds_per_request=0.02, max_duration=20)
        # v1's window is [start+2 s, start+18 s] at a 20 s ceiling, and the run
        # ends at its 50-request limit in about a second, so nothing lands in it.
        self.assertEqual(outcome["measured"]["total"], 0)
        self.assertEqual(outcome["all_phases"]["total"], 50)


if __name__ == "__main__":
    unittest.main()
