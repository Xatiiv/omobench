"""Pinned runner, synthetic tokenizer/SSE only; no model download or inference."""
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import unittest

from omobench import cli
from omobench.bridge import BridgeSession
from test_bridge_integration import helper_command
from test_chat_small_v2 import StreamingFixture, MODEL, build_tokenizer, guidellm_available, ROOT


class RunnerBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guidellm_available()

    def test_private_backend_survives_worker_pickle_without_serialized_secret(self):
        from omobench.bridge_runner import authenticated_backend
        from guidellm.backends import Backend
        from guidellm.backends.openai.http import OpenAIHTTPBackendArgs
        secret = "A" * 43
        args = OpenAIHTTPBackendArgs(kind="openai_http", target="http://127.0.0.1:1234", model=MODEL,
                                    follow_redirects=False, http2=False)
        previous = Backend.get_registered_object("openai_http")
        with authenticated_backend({"endpoint": args.target, "token": secret}):
            backend = pickle.loads(pickle.dumps(Backend.create(args)))
            self.assertEqual(backend._bridge_token, secret)
            self.assertNotIn(secret, json.dumps(backend.info))
            self.assertNotIn(secret, repr(backend))
        self.assertIs(Backend.get_registered_object("openai_http"), previous)

    def run_route(self, telemetry):
        with tempfile.TemporaryDirectory() as directory, StreamingFixture(seconds_per_request=.02) as fixture:
            root = Path(directory)
            tokenizer = root / "tokenizer"
            build_tokenizer(tokenizer)
            with BridgeSession(fixture.port, _helper_command=helper_command(fixture.port)) as bridge:
                output = root / "raw.json"
                command = cli.build_guidellm_command(root=ROOT, endpoint=bridge.endpoint, model=MODEL,
                    profile_id="chat-small-v2", raw_output_path=output, tokenizer=str(tokenizer))
                command[command.index("--backend") + 1] += ",http2=false"
                # Keep the production profile and all request counts unchanged.
                script = '''import json,sys
from pathlib import Path
from omobench.cli import run_guidellm_command
d=json.load(sys.stdin)
result=run_guidellm_command(d['command'],log_path=Path(d['log']),root=Path(d['root']),profile_id='chat-small-v2',telemetry_dir=Path(d['telemetry']) if d['telemetry'] else None,bridge_credentials=d['credentials'])
raise SystemExit(result.returncode)
'''
                environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                               "TOKENIZERS_PARALLELISM": "false"}
                payload = {"command": command, "log": str(root / "runner.log"), "root": str(ROOT),
                           "telemetry": str(root / "telemetry") if telemetry else None,
                           "credentials": bridge.credentials}
                completed = subprocess.run([sys.executable, "-c", script], input=json.dumps(payload),
                    text=True, capture_output=True, env=environment, timeout=180)
                secret = bridge.credentials["token"]
                self.assertNotIn(secret, completed.stdout + completed.stderr)
                log = (root / "runner.log").read_text() if (root / "runner.log").exists() else ""
                self.assertEqual(completed.returncode, 0, (completed.stderr + log)[-4000:])
                report = json.loads(output.read_text())
                normalized = cli.normalize_guidellm_report(report)
                self.assertEqual(normalized["results"]["requests"]["succeeded"], 50)
                self.assertEqual(len(fixture.arrivals), 60)
                for path in root.rglob("*"):
                    if path.is_file():
                        self.assertNotIn(secret.encode(), path.read_bytes(), path.name)
                if telemetry:
                    self.assertTrue((root / "telemetry" / "telemetry.json").is_file())
                    self.assertNotIn("live telemetry unavailable", log)

    def test_plain_route_runs_exact_production_profile(self):
        self.run_route(False)

    def test_telemetry_route_runs_exact_production_profile(self):
        self.run_route(True)
