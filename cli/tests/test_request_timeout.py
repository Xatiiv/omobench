"""execution.request_timeout_seconds -> GuideLLM backend timeout wiring.

Two layers:

1. Wiring: the profile YAML's declared execution.request_timeout_seconds
   reaches the real resolved GuideLLM backend object, identically on the plain
   subprocess path and the in-process telemetry path (test_telemetry.py's
   equivalence summary covers the second half of that).

2. Behavior: what that timeout actually does, driven against real local
   fixture HTTP servers. GuideLLM 0.7.3 passes it to httpx as the READ timeout
   (httpx.Timeout(read=args.timeout, ...), see guidellm/backends/openai/http.py),
   which bounds the wait for the NEXT response chunk -- not the total duration
   of a streaming request. These tests pin that distinction down rather than
   asserting a convenient-sounding claim about it: a response that keeps
   streaming steadily past the read timeout is NOT cut off by it.
"""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from omobench import cli


ROOT = Path(__file__).resolve().parents[2]
READ_TIMEOUT_SECONDS = 1.0


def _skip_unless_guidellm() -> dict:
    try:
        from guidellm.backends.openai.http import OpenAIHTTPBackend, OpenAIHTTPBackendArgs
        from guidellm.schemas import GenerationRequest, GenerationRequestArguments, RequestInfo
    except ImportError as error:  # pragma: no cover - environment dependent
        raise unittest.SkipTest(f"GuideLLM is not installed in this environment: {error}") from None
    return {
        "OpenAIHTTPBackend": OpenAIHTTPBackend,
        "OpenAIHTTPBackendArgs": OpenAIHTTPBackendArgs,
        "GenerationRequest": GenerationRequest,
        "GenerationRequestArguments": GenerationRequestArguments,
        "RequestInfo": RequestInfo,
    }


class ProfileTimeoutWiringTests(unittest.TestCase):
    """The declared profile value reaches the command GuideLLM actually runs."""

    def test_every_shipped_profile_declares_a_timeout_that_reaches_the_backend(self) -> None:
        for profile_id in sorted(cli.PROFILE_SPECS):
            with self.subTest(profile_id=profile_id):
                declared = cli.profile_request_timeout_seconds(ROOT, profile_id)
                self.assertIsNotNone(declared, f"{profile_id}.yaml declares no execution.request_timeout_seconds")
                command = cli.build_guidellm_command(
                    root=ROOT,
                    endpoint="http://127.0.0.1:9",
                    model="Qwen/Qwen3-4B",
                    profile_id=profile_id,
                    raw_output_path=Path("/tmp/out.json"),
                )
                backend = command[command.index("--backend") + 1]
                self.assertIn(f"timeout={declared}", backend)

    def test_chat_small_extended_carries_its_own_declared_three_hundred_seconds(self) -> None:
        self.assertEqual(cli.profile_request_timeout_seconds(ROOT, "chat-small-extended-v1"), 300.0)

    def test_a_profile_without_a_declared_timeout_leaves_the_backend_default_alone(self) -> None:
        """No declared value means GuideLLM's own default stands; OmoBench never
        invents a timeout the profile did not declare."""
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "benchmarks" / "profiles").mkdir(parents=True)
            (root / "benchmarks" / "profiles" / "chat-small-v1.yaml").write_text(
                "id: chat-small-v1\nexecution:\n  concurrency: 1\n", encoding="utf-8"
            )
            self.assertIsNone(cli.profile_request_timeout_seconds(root, "chat-small-v1"))
            command = cli.build_guidellm_command(
                root=root,
                endpoint="http://127.0.0.1:9",
                model="Qwen/Qwen3-4B",
                profile_id="chat-small-v1",
                raw_output_path=Path("/tmp/out.json"),
            )
            self.assertNotIn("timeout=", command[command.index("--backend") + 1])

    def test_a_missing_or_malformed_profile_file_does_not_break_command_construction(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(cli.profile_request_timeout_seconds(root, "chat-small-v1"))
            (root / "benchmarks" / "profiles").mkdir(parents=True)
            (root / "benchmarks" / "profiles" / "chat-small-v1.yaml").write_text(
                "execution:\n  request_timeout_seconds: not-a-number\n", encoding="utf-8"
            )
            self.assertIsNone(cli.profile_request_timeout_seconds(root, "chat-small-v1"))
            command = cli.build_guidellm_command(
                root=root, endpoint="http://127.0.0.1:9", model="m",
                profile_id="chat-small-v1", raw_output_path=Path("/tmp/out.json"),
            )
            self.assertTrue(any(part.startswith("kind=openai_http") for part in command))


class _StreamingFixtureHandler(BaseHTTPRequestHandler):
    """Fixture server with three behaviours, selected per request path."""

    silence_seconds = READ_TIMEOUT_SECONDS * 3
    chunk_interval_seconds = READ_TIMEOUT_SECONDS / 5
    total_chunks = 15

    def _send_sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _chunk(self, content: str) -> bytes:
        return (
            'data: {"id":"x","object":"chat.completion.chunk","choices":'
            f'[{{"index":0,"delta":{{"content":"{content}"}},"finish_reason":null}}]}}\n\n'
        ).encode()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        try:
            if self.path.endswith("/silent"):
                # Accepts the connection, then sends nothing at all.
                time.sleep(self.silence_seconds)
                self._send_sse_headers()
                self.wfile.write(self._chunk("late"))
            elif self.path.endswith("/stalling"):
                # Streams briefly, then stalls between chunks.
                self._send_sse_headers()
                self.wfile.write(self._chunk("a"))
                self.wfile.flush()
                time.sleep(self.silence_seconds)
                self.wfile.write(self._chunk("b"))
                self.wfile.flush()
            else:
                # Streams steadily for far longer than the read timeout, never
                # pausing longer than chunk_interval_seconds between chunks.
                self._send_sse_headers()
                for index in range(self.total_chunks):
                    self.wfile.write(self._chunk(f"t{index}"))
                    self.wfile.flush()
                    time.sleep(self.chunk_interval_seconds)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args) -> None:
        pass


class ReadTimeoutBehaviourTests(unittest.TestCase):
    """What the wired timeout actually bounds, against real fixture servers."""

    def setUp(self) -> None:
        self.g = _skip_unless_guidellm()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _StreamingFixtureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _resolve(self, path_suffix: str) -> tuple[bool, float, Exception | None]:
        """Drive the real backend against the fixture server once. Returns
        (completed_without_error, elapsed_seconds, error)."""
        backend = self.g["OpenAIHTTPBackend"](
            self.g["OpenAIHTTPBackendArgs"](
                target=f"http://127.0.0.1:{self.server.server_port}",
                model="fixture-model",
                request_format="/v1/chat/completions",
                timeout=READ_TIMEOUT_SECONDS,
                http2=False,
                validate_backend=False,
                api_routes={"/v1/chat/completions": f"v1/chat/completions{path_suffix}"},
            )
        )
        request = self.g["GenerationRequest"](
            request_type="text_completions",
            arguments=self.g["GenerationRequestArguments"](
                body={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 16},
                stream=True,
            ),
        )

        async def drive() -> tuple[bool, Exception | None]:
            await backend.process_startup()
            try:
                async for _response, _info in backend.resolve(request, self.g["RequestInfo"]()):
                    pass
                return True, None
            except Exception as error:  # noqa: BLE001 - the outcome under test
                return False, error
            finally:
                await backend.process_shutdown()

        started = time.monotonic()
        completed, error = asyncio.run(drive())
        return completed, time.monotonic() - started, error

    def test_no_response_data_before_the_timeout_fails_the_request(self) -> None:
        """A server that accepts the connection and then sends nothing is cut
        off at the read timeout, not left hanging until the duration limit."""
        completed, elapsed, error = self._resolve("/silent")
        self.assertFalse(completed, "a silent server should not produce a successful response")
        self.assertIsNotNone(error)
        self.assertLess(elapsed, _StreamingFixtureHandler.silence_seconds,
                        "the request should fail at the read timeout, well before the server speaks")
        self.assertGreaterEqual(elapsed, READ_TIMEOUT_SECONDS * 0.5)

    def test_a_stream_that_stalls_between_chunks_fails_at_the_read_timeout(self) -> None:
        """The timeout applies per chunk, so a stream that goes quiet mid-response
        fails too -- the gap between chunks is what it measures."""
        completed, elapsed, error = self._resolve("/stalling")
        self.assertFalse(completed, "a stalled stream should not be reported as a success")
        self.assertIsNotNone(error)
        self.assertLess(elapsed, _StreamingFixtureHandler.silence_seconds)

    def test_a_continuously_streaming_response_outlives_the_read_timeout(self) -> None:
        """The distinction this wiring must not overstate: a response whose TOTAL
        duration far exceeds the read timeout still succeeds, as long as no single
        gap between chunks does. This is why the timeout alone cannot prevent a
        single long request from overrunning a profile's duration budget."""
        completed, elapsed, error = self._resolve("")
        self.assertTrue(completed, f"a steadily streaming response must not time out (error: {error!r})")
        self.assertGreater(
            elapsed, READ_TIMEOUT_SECONDS,
            "fixture did not actually outlive the read timeout; the test would prove nothing",
        )


if __name__ == "__main__":
    unittest.main()
