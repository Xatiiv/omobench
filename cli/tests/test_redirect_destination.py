"""The benchmark measures the destination that was selected, and only that one.

`OpenAIHTTPBackendArgs.follow_redirects` defaults to **True** in GuideLLM
0.7.3, so a server on the endpoint a contributor entered can answer a benchmark
request with a redirect and have the pinned runner send that request -- prompt
included -- somewhere else entirely, while the result still records the
endpoint that was entered as the thing that was measured.

OmoBench's own connection checks already refuse a redirect
(`ui.NoRedirect`). These tests cover the requests that actually produce the
measurements, against a fixture that redirects, and include the control case:
with the setting removed the escape happens, so a regression cannot pass
unnoticed.

Nothing is downloaded and no model runs: the decoy and the destination it names
are both local fixture servers, and the tokenizer is generated here.
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

from test_chat_small_v2 import MODEL, StreamingFixture, build_tokenizer, guidellm_available


ROOT = Path(__file__).resolve().parents[2]


class RedirectingFixture:
    """Answers health and /v1/models normally, and redirects every completion.

    It records the completions it was asked for, so a test can tell "the runner
    never came here" from "the runner came here and was sent away".
    """

    def __init__(self, destination: str):
        self.destination = destination
        self.completions: list[str] = []
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
                fixture.completions.append(self.path)
                self.send_response(307)
                self.send_header("Location", fixture.destination + self.path)
                self.send_header("Content-Length", "0")
                self.end_headers()

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


class CommandTests(unittest.TestCase):
    """What OmoBench asks the runner for, before anything runs."""

    def test_every_profile_pins_the_destination(self):
        for profile_id in cli.PROFILE_SPECS:
            with self.subTest(profile_id=profile_id):
                command = cli.build_guidellm_command(
                    root=ROOT, endpoint="http://127.0.0.1:8000", model=MODEL,
                    profile_id=profile_id, raw_output_path=Path("unused.json"))
                backend = command[command.index("--backend") + 1]
                self.assertIn("follow_redirects=false", backend)

    def test_the_setting_sits_on_the_backend_the_target_is_on(self):
        """One backend string, so the destination and the rule cannot separate."""
        command = cli.build_guidellm_command(
            root=ROOT, endpoint="http://127.0.0.1:8000", model=MODEL,
            profile_id="chat-small-v2", raw_output_path=Path("unused.json"))
        backend = command[command.index("--backend") + 1]
        self.assertIn("target=http://127.0.0.1:8000", backend)
        self.assertIn("follow_redirects=false", backend)


class RedirectEscapeTests(unittest.TestCase):
    """What the pinned runner really does when the server redirects."""

    @classmethod
    def setUpClass(cls):
        guidellm_available()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.tokenizer = build_tokenizer(Path(cls.temporary.name) / "tokenizer")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_against_redirect(self, *, allow_redirects: bool) -> dict:
        """Point the runner at a decoy that redirects to a real streaming server.

        `allow_redirects` strips OmoBench's setting back out, which is the
        control: it reproduces the pinned runner's own default so the test can
        show the escape it prevents.
        """
        with StreamingFixture(seconds_per_request=0.01, chunks=2) as elsewhere:
            with RedirectingFixture(elsewhere.endpoint) as decoy:
                output = Path(self.temporary.name) / f"redirect-{allow_redirects}.json"
                command = cli.build_guidellm_command(
                    root=ROOT, endpoint=decoy.endpoint, model=MODEL,
                    profile_id="chat-small-v2", raw_output_path=output,
                    tokenizer=str(self.tokenizer))
                command = [part.replace("kind=max_requests,count=60", "kind=max_requests,count=3")
                           for part in command]
                command = [f"kind=max_duration,seconds=90"
                           if part.startswith("kind=max_duration") else part for part in command]
                if allow_redirects:
                    command = [part.replace(",follow_redirects=false", "") for part in command]
                environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                               "TOKENIZERS_PARALLELISM": "false"}
                completed = subprocess.run(cli.guidellm_runner_invocation(command),
                                           capture_output=True, text=True, env=environment,
                                           timeout=300)
                return {
                    "exit_code": completed.returncode,
                    "stderr": completed.stderr[-2000:],
                    "asked_the_selected_destination": len(decoy.completions),
                    "reached_the_other_destination": len(elsewhere.arrivals),
                }

    def test_a_redirect_cannot_move_the_benchmark_off_the_selected_destination(self):
        outcome = self.run_against_redirect(allow_redirects=False)
        self.assertGreater(outcome["asked_the_selected_destination"], 0,
                           "the decoy was never contacted, so nothing was tested")
        self.assertEqual(outcome["reached_the_other_destination"], 0,
                         "a benchmark request reached a destination nobody selected")

    def test_the_control_shows_the_escape_this_prevents(self):
        """Without the setting the pinned runner really does follow the redirect.

        If this ever stops being true the protection above has become
        untestable, and the comment in build_guidellm_command() would be
        describing a hazard that no longer exists.
        """
        outcome = self.run_against_redirect(allow_redirects=True)
        self.assertGreater(outcome["reached_the_other_destination"], 0,
                           "GuideLLM no longer follows redirects by default; revisit this protection")


if __name__ == "__main__":
    unittest.main()
