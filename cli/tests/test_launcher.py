"""Launch lifecycle tests use isolated fixture processes, never real benchmark evidence."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from omobench import cli, launcher
from test_cli import GUIDELLM_REPORT_V2


SOURCE = Path(cli.__file__).resolve().parents[1]


@unittest.skipIf(sys.platform == "win32", "POSIX/WSL process lifecycle experiment")
class LaunchLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="omobench launch ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "benchmarks" / "profiles").mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.model = self.root / "models with spaces" / "test.gguf"
        self.model.parent.mkdir()
        self.model.write_bytes(b"GGUFfixture model, not real weights")
        self.server = self.bin / "fixture server"
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            self.port = available.getsockname()[1]
        self.plan = launcher.LaunchPlan(self.root, self.server, self.model, "fixture/model", self.port, 2)
        self.write_server()
        self.runner = self.bin / "guidellm"
        self.runner.write_text(f'''#!{sys.executable}
import json, sys
from pathlib import Path
if '--version' in sys.argv:
    print('GuideLLM 0.7.3'); raise SystemExit(0)
Path('runner-argv.json').write_text(json.dumps(sys.argv[1:]))
output = sys.argv[sys.argv.index('--output') + 1].split('path=', 1)[1]
Path(output).write_text({json.dumps(GUIDELLM_REPORT_V2)!r})
print('Fixture runner finished; no actual inference measurements.')
''', encoding="utf-8")
        self.runner.chmod(0o755)
        self.env = patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                                         "PYTHONPATH": str(SOURCE), "LLAMA_ARG_HOST": "0.0.0.0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write_server(self, *, behavior="healthy"):
        self.server.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
if '--version' in sys.argv:
    print('fixture llama-server 1'); raise SystemExit(0)
Path('server-argv.json').write_text(json.dumps({{'argv': sys.argv[1:], 'host_override': os.getenv('LLAMA_ARG_HOST')}}))
if {behavior!r} == 'crash':
    raise SystemExit(7)
alias = sys.argv[sys.argv.index('--alias') + 1]
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        with Path('requests.log').open('a') as log: log.write(self.path + '\\n')
        if {behavior!r} == 'unhealthy':
            self.send_response(503); self.end_headers(); return
        if {behavior!r} == 'redirect':
            self.send_response(302); self.send_header('Location', '/not-followed'); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        body = {{'status':'ok'}} if self.path == '/health' else {{'data':[{{'id':alias if {behavior!r} != 'wrong-model' else 'another-model'}}]}}
        self.wfile.write(json.dumps(body).encode())
HTTPServer(('127.0.0.1', int(sys.argv[sys.argv.index('--port') + 1])), Handler).serve_forever()
''', encoding="utf-8")
        self.server.chmod(0o755)

    def metadata(self):
        return json.loads(next((self.root / ".omobench-runs" / "launcher").glob("job-*/launcher.json")).read_text())

    def assert_server_stopped(self):
        with socket.socket() as connection:
            connection.settimeout(0.2)
            self.assertNotEqual(connection.connect_ex(("127.0.0.1", self.port)), 0)

    def run_plan(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return launcher.run_plan(self.plan, **kwargs)

    def test_smoke_starts_real_fixture_process_checks_alias_and_cleans_up(self):
        self.assertEqual(self.run_plan(smoke=True), 0)
        self.assertEqual(self.metadata()["status"], "smoke_completed")
        self.assertNotIn("summary", self.metadata())
        self.assertFalse((self.root / "runner-argv.json").exists())
        args = json.loads((self.root / "server-argv.json").read_text())
        self.assertIsNone(args["host_override"])
        self.assertEqual(args["argv"], self.plan.server_command[1:])
        self.assert_server_stopped()

    def test_execute_delegates_unchanged_workload_and_normalization_to_cli(self):
        self.assertEqual(self.run_plan(), 0)
        record = self.metadata()
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["summary"], cli.normalize_guidellm_report(GUIDELLM_REPORT_V2))
        run = self.root / record["run_directory"]
        expected = cli.build_guidellm_command(endpoint=self.plan.endpoint, model="fixture/model",
                                             profile_id="chat-small-v1", raw_output_path=run / "guidellm-benchmarks.json")
        self.assertEqual(json.loads((self.root / "runner-argv.json").read_text()), expected[1:])
        self.assertEqual({p.name for p in run.iterdir()}, {"environment.json", "reproduce.md", "guidellm.stdout.log", "guidellm-benchmarks.json.gz"})
        self.assertFalse((self.root / "results").exists())
        self.assertEqual(record["model_sha256"], cli.sha256_file(self.model))
        self.assertEqual((self.root / "requests.log").read_text().splitlines(), ["/health", "/v1/models"])
        self.assert_server_stopped()

    def test_server_crash_fails_without_running_benchmark(self):
        self.write_server(behavior="crash")
        self.assertEqual(self.run_plan(), 1)
        self.assertEqual(self.metadata()["status"], "failed")
        self.assertIn("code 7", self.metadata()["error"])
        self.assertFalse((self.root / "runner-argv.json").exists())
        self.assert_server_stopped()

    def test_wrong_model_times_out_and_stops_owned_server(self):
        self.write_server(behavior="wrong-model")
        self.assertEqual(self.run_plan(smoke=True), 1)
        self.assertIn("startup timeout", self.metadata()["error"])
        self.assert_server_stopped()

    def test_redirect_is_rejected_not_followed(self):
        self.write_server(behavior="redirect")
        self.assertEqual(self.run_plan(smoke=True), 1)
        self.assertIn("HTTP 302", self.metadata()["error"])
        self.assertEqual((self.root / "requests.log").read_text().splitlines(), ["/health"])
        self.assert_server_stopped()

    def test_loading_server_times_out_without_running_benchmark(self):
        self.write_server(behavior="unhealthy")
        self.assertEqual(self.run_plan(), 1)
        self.assertIn("startup timeout", self.metadata()["error"])
        self.assertFalse((self.root / "runner-argv.json").exists())
        self.assert_server_stopped()

    def test_failed_runner_preserves_logs_and_stops_server(self):
        text = self.runner.read_text().replace("Path('runner-argv.json')", "raise SystemExit(9)\nPath('runner-argv.json')")
        self.runner.write_text(text)
        self.assertEqual(self.run_plan(), 1)
        self.assertIn("code 9", self.metadata()["error"])
        self.assertNotIn("summary", self.metadata())
        self.assert_server_stopped()

    def test_cancel_during_loading_stops_server(self):
        with patch.object(launcher, "wait_for_server", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_plan(smoke=True), 130)
        self.assertEqual(self.metadata()["status"], "interrupted")
        self.assert_server_stopped()

    def test_sigterm_during_benchmark_stops_both_owned_process_groups(self):
        self.runner.write_text(self.runner.read_text().replace("import json, sys", "import json, sys, os, time")
                               .replace("output = sys.argv", "Path('runner.pid').write_text(str(os.getpid()))\ntime.sleep(60)\noutput = sys.argv"))
        arguments = [sys.executable, "-m", "omobench.cli", "launch", "--server", str(self.server),
                     "--model-file", str(self.model), "--model", "fixture/model", "--port", str(self.port), "--execute"]
        with subprocess.Popen(arguments, cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              start_new_session=True) as process:
            try:
                deadline = time.monotonic() + 8
                while not (self.root / "runner.pid").exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail("Launcher exited before the fixture runner started")
                    time.sleep(0.05)
                self.assertTrue((self.root / "runner.pid").exists())
                pid = int((self.root / "runner.pid").read_text())
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=8), 130)
                self.assertEqual(self.metadata()["status"], "interrupted")
                self.assert_server_stopped()
                if sys.platform.startswith("linux"):
                    stat = Path(f"/proc/{pid}/stat")
                    # The test environment's init may not immediately reap an
                    # orphaned zombie; it must not be an executing process.
                    self.assertTrue(not stat.exists() or stat.read_text().split(") ", 1)[1].startswith("Z"))
            finally:
                launcher.stop_owned_process(process)

    def test_occupied_port_is_rejected_without_stopping_existing_listener(self):
        with socket.socket() as existing:
            existing.bind(("127.0.0.1", self.port))
            existing.listen()
            with self.assertRaisesRegex(ValueError, "occupied"):
                self.run_plan(smoke=True)
            self.assertEqual(existing.getsockname()[1], self.port)
            self.assertFalse((self.root / ".omobench-runs").exists())

    def test_missing_runner_fails_before_any_model_or_evidence_creation(self):
        with patch.object(cli, "executable_version", return_value=None):
            with self.assertRaisesRegex(ValueError, "GuideLLM 0.7.3"):
                self.run_plan()
        self.assertFalse((self.root / "server-argv.json").exists())
        self.assertFalse((self.root / ".omobench-runs").exists())

    def test_dry_run_has_no_process_network_or_file_writes(self):
        args = cli.build_parser().parse_args(["launch", "--server", str(self.server), "--model-file", str(self.model), "--model", "fixture/model"])
        with patch.object(cli, "find_repository_root", return_value=self.root), patch.object(launcher.subprocess, "Popen") as popen:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(launcher.launch(args), 0)
            self.assertTrue(json.loads(output.getvalue())["dry_run"])
            popen.assert_not_called()
        self.assertFalse((self.root / ".omobench-runs").exists())

    def test_model_and_loading_settings_are_validated_before_launch(self):
        base = ["launch", "--server", str(self.server), "--model-file", str(self.model), "--model", "fixture/model"]
        for extra in (["--port", "80"], ["--startup-timeout", "nan"], ["--startup-timeout", "0"], ["--model", "bad,alias"]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                launcher.make_plan(cli.build_parser().parse_args(base + extra), self.root)
        self.model.write_text("An error page, not model weights")
        with self.assertRaisesRegex(ValueError, "GGUF"):
            launcher.make_plan(cli.build_parser().parse_args(base), self.root)


class ReadinessTests(unittest.TestCase):
    def test_native_windows_never_claims_runner_execution_ready(self):
        completed = subprocess.CompletedProcess([], 0, "Ubuntu\r\n".encode("utf-16-le"), b"")
        with patch.object(launcher.sys, "platform", "win32"), patch.object(cli, "executable_version", return_value="0.7.3"), patch.object(launcher.subprocess, "run", return_value=completed):
            report = launcher.readiness()
        self.assertFalse(report["can_execute_here"])
        self.assertEqual(report["wsl_distributions"], ["Ubuntu"])
        self.assertFalse(report["managed_gpu_support"])

    def test_wsl_probe_failure_is_a_diagnostic_not_a_crash(self):
        for error in (PermissionError("blocked"), subprocess.TimeoutExpired("wsl.exe", 15)):
            with self.subTest(error=error), patch.object(launcher.sys, "platform", "win32"), patch.object(cli, "executable_version", return_value=None), patch.object(launcher.subprocess, "run", side_effect=error):
                report = launcher.readiness()
            self.assertFalse(report["wsl_probe_ok"])
            self.assertFalse(report["can_execute_here"])

    def test_runner_version_must_match(self):
        with patch.object(launcher.sys, "platform", "linux"), patch.object(cli, "executable_version", return_value="0.8.0"):
            self.assertFalse(launcher.readiness()["can_execute_here"])


if __name__ == "__main__":
    unittest.main()
