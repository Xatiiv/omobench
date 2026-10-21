from __future__ import annotations

import copy
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from omobench.cli import build_parser
from omobench.ui import Controller, UIError, UIServer, endpoint_origin, panel_lock, probe_connection, run_config, safe_log
from test_cli import GUIDELLM_REPORT_V2, install_guidellm_module_stub, patch_parent_process_preflight


ROOT = Path(__file__).resolve().parents[2]
RUN_CONFIG = {"endpoint": "http://127.0.0.1:8092/v1", "model": "test/model", "profile": "chat-small-v1",
              "execution_device": "cpu", "device_confirmed": True}


def make_root(root: Path) -> None:
    for relative in ("benchmarks", "catalog", "schema", "tools"):
        shutil.copytree(ROOT / relative, root / relative, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "results").mkdir()


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("Timed out waiting for the local test operation")


class LocalBoundaryTests(unittest.TestCase):
    def test_only_one_panel_can_hold_a_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with panel_lock(root), self.assertRaisesRegex(UIError, "already running"):
                with panel_lock(root):
                    self.fail("Second panel obtained the lock")
            with panel_lock(root):
                pass  # Reopening after normal shutdown works without deleting a stale file.

    def test_parser_and_loopback_normalization(self):
        parsed = build_parser().parse_args(["ui", "--no-browser", "--port", "8766"])
        self.assertEqual(parsed.port, 8766)
        self.assertTrue(parsed.no_browser)
        self.assertEqual(endpoint_origin("http://127.0.0.1:8092/v1/"), "http://127.0.0.1:8092")
        self.assertEqual(endpoint_origin("http://[::1]:8092"), "http://[::1]:8092")

    def test_connection_rejects_external_or_credential_urls(self):
        for url in ["https://example.com", "file:///etc/passwd", "http://localhost.evil.test",
                    "http://user:secret@127.0.0.1:8", "http://127.0.0.1:8092/api", "http://localhost:0",
                    "http://localhost:65536", "http://localhost?token=secret", "http://localhost#x"]:
            with self.subTest(url=url), self.assertRaises(UIError):
                endpoint_origin(url)

    def test_run_requires_a_fixed_profile_and_device_declaration(self):
        for change in [{"model": "x,api_key=secret"}, {"model": "x\n--help"}, {"profile": "custom"},
                       {"execution_device": "auto"}, {"device_confirmed": False}]:
            with self.subTest(change=change), self.assertRaises(UIError):
                run_config({**RUN_CONFIG, **change})

    def test_live_log_redacts_urls_and_keys(self):
        log = safe_log("\x1b[32mhttp://127.0.0.1:123/v1 token=secret api_key=abc\x1b[0m")
        self.assertNotIn("127.0.0.1", log)
        self.assertNotIn("secret", log)
        self.assertNotIn("abc", log)
        self.assertNotIn("\x1b", log)
        self.assertNotIn("private-key", safe_log("Authorization: Bearer private-key"))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.server = UIServer(("127.0.0.1", 0), self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.controller.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temporary.cleanup()

    def request(self, path, headers=None, method="GET", body=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            client.close()

    def test_assets_are_served_without_exposing_api_token(self):
        for path in ["/", "/app.js", "/style.css"]:
            status, headers, content = self.request(path)
            self.assertEqual(status, 200)
            self.assertNotIn(self.server.token.encode(), content)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/../../README.md")[0], 404)

    def test_setup_lists_the_extended_profile_with_its_ceiling(self):
        """The panel's profile picker (profileDetail() in app.js) renders
        `up to ${max_duration_seconds / 60} min` directly from this data -- no
        separate UI code is needed for chat-small-extended-v1 to show "up to 90 min".
        Calls Controller.setup() directly (not over HTTP): it's the same data
        /api/setup returns, without depending on this environment's GPU-detection
        subprocess timing, which other tests in this class stub out and this one
        does not need at all."""
        setup = self.server.controller.setup()
        profiles = setup["profiles"]
        self.assertIn("chat-small-extended-v1", profiles)
        spec = profiles["chat-small-extended-v1"]
        self.assertEqual(spec["max_duration_seconds"], 5400)
        self.assertEqual(spec["max_duration_seconds"] / 60, 90)
        self.assertEqual(spec["input_tokens"], 1024)
        self.assertEqual(spec["output_tokens"], 256)
        # Unchanged existing profile, still present with its own ceiling.
        self.assertEqual(profiles["chat-small-v1"]["max_duration_seconds"], 900)

    def test_token_host_origin_and_json_enforcement(self):
        auth = {"X-OmoBench-Token": self.server.token}
        self.assertEqual(self.request("/api/runs")[0], 401)
        self.assertEqual(self.request("/api/runs", auth)[0], 200)
        for headers in [{**auth, "Origin": "https://evil.test"}, {**auth, "Host": "evil.test"}]:
            self.assertEqual(self.request("/api/runs", headers)[0], 403)
        self.assertEqual(self.request("/api/runs", auth, "POST", "{}")[0], 415)
        auth["Content-Type"] = "application/json"
        self.assertEqual(self.request("/api/runs", auth, "POST", "[]")[0], 400)
        self.assertEqual(self.request("/api/runs", auth, "POST", "x" * 32769)[0], 413)

    def test_symlinked_evidence_cannot_escape_staging(self):
        controller = self.server.controller
        outside = self.root / "private.txt"
        outside.write_text("private")
        alias = controller.storage / "alias"
        try:
            alias.symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks unavailable on this platform")
        with self.assertRaises(UIError):
            controller._inside(alias)

    def test_native_windows_is_blocked_before_spawning(self):
        with patch("omobench.ui.sys.platform", "win32"), patch("omobench.ui.subprocess.Popen") as process:
            with self.assertRaisesRegex(UIError, "WSL2"):
                self.server.controller.start(RUN_CONFIG)
            process.assert_not_called()

    def test_connection_probe_cannot_add_traffic_during_a_benchmark(self):
        controller = self.server.controller
        controller.busy = True
        with patch("omobench.ui.probe_connection") as probe:
            with self.assertRaisesRegex(UIError, "active operation"):
                controller.connection(RUN_CONFIG)
            probe.assert_not_called()
        controller.busy = False
        with patch("omobench.ui.probe_connection", side_effect=UIError("Connection failed")):
            with self.assertRaises(UIError):
                controller.connection(RUN_CONFIG)
        self.assertFalse(controller.busy)


class ConnectionTests(unittest.TestCase):
    def test_real_streaming_probe_and_redirect_rejection(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append(payload)
                if payload["model"] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "https://example.com/")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = {"endpoint": f"http://127.0.0.1:{server.server_port}/v1", "model": "fixture"}
            self.assertIn("works", probe_connection(config)["message"])
            self.assertEqual(received[0]["max_tokens"], 1)
            self.assertTrue(received[0]["stream"])
            with self.assertRaisesRegex(UIError, "redirected"):
                probe_connection({**config, "model": "redirect"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


@unittest.skipIf(os.name == "nt", "The supported runner uses a POSIX process group")
class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        make_root(self.root)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.stub = self.root / "stub"
        self.stub.mkdir()
        self.path_patch = patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"]})
        self.path_patch.start()
        # Controller.start() runs the real preflight in *this* process, before
        # spawning anything; the stub runner's metadata reaches only the child.
        patch_parent_process_preflight(self)
        self.controller = Controller(self.root)
        # The fixture checkout uses the real CLI under test, not a separately
        # installed worktree. The runner stand-in shadows the guidellm *module*
        # (self.stub), because execution goes through "<interpreter> -m
        # guidellm" and deliberately ignores anything named "guidellm" on PATH.
        self.controller.environment["PYTHONPATH"] = str(self.stub) + os.pathsep + str(ROOT / "cli" / "src")
        for tool in ["nvidia-smi", "rocm-smi", "lspci", "gh"]:
            script = self.bin / tool
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o755)
        # Safety net: without an explicit runner() call, no test may reach the
        # real GuideLLM and start a genuine benchmark against a dead endpoint.
        self.runner("missing")

    def tearDown(self):
        self.controller.close()
        self.path_patch.stop()
        self.temporary.cleanup()

    def runner(self, mode="success", report=None):
        """Install the stand-in runner as a `guidellm` module shadowing the
        real package on the subprocess's PYTHONPATH (see setUp): execution
        runs `<interpreter> -m guidellm`, so a PATH script is never consulted."""
        content = (
            "import json, pathlib, subprocess, sys, time\n"
            "if '--version' in sys.argv:\n print('GuideLLM 0.7.3'); sys.exit(0)\n"
            "output = pathlib.Path(sys.argv[sys.argv.index('--output') + 1].split('path=', 1)[1])\n"
            "print('runner connected to http://127.0.0.1:8092', flush=True)\n"
        )
        if mode == "wait":
            content += (
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                f"pathlib.Path({str(self.root / 'child.pid')!r}).write_text(str(child.pid))\n"
                "time.sleep(120)\n"
            )
        elif mode == "fail":
            content += "sys.exit(7)\n"
        elif mode != "missing":
            content += f"output.write_text(json.dumps({report if report is not None else GUIDELLM_REPORT_V2!r}))\n"
        install_guidellm_module_stub(self.stub, content)

    def finished(self, job):
        wait_for(lambda: not self.controller.busy)
        return self.controller.detail(job["id"])

    def test_cpu_uses_real_cli_evidence_and_can_be_packaged_and_reviewed(self):
        """CPU benchmarks are first-class: real evidence, then real packaging and review,
        through the same panel workflow GPU runs already use."""
        self.runner()
        job = self.finished(self.controller.start(RUN_CONFIG))
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(job["summary"]["results"]["requests"]["succeeded"], 50)
        self.assertNotIn("http://127.0.0.1", job["log"])
        run = self.controller.run_directory(job["id"])
        self.assertTrue((run / "guidellm-benchmarks.json.gz").is_file())
        self.assertFalse((run / "result.json").exists())
        for action in ["prepare", "bundle"]:
            with self.subTest(action=action), self.assertRaisesRegex(UIError, "result.json"):
                getattr(self.controller, action)(job["id"])

        packaged = self.controller.package(job["id"], {
            "model": "Qwen/Qwen3-4B", "model_revision": "abc123", "framework": "llama.cpp",
            "framework_version": "test-build", "submitter": "tester",
        })
        self.assertTrue(packaged["validation"]["valid"], packaged)
        result = json.loads((run / "result.json").read_text())
        self.assertEqual(result["execution_device"], "cpu")
        self.assertEqual(result["hardware"]["gpus"], [])

        preview = self.controller.prepare(job["id"])
        self.assertEqual(len(preview["files"]), 5)
        self.assertIn("CPU", preview["body"])
        self.assertTrue(self.controller.bundle(job["id"]).startswith(b"PK"))

        recovered = Controller(self.root)
        self.assertEqual(recovered.detail(job["id"])["summary"], job["summary"])

    def test_set_prefill_surfaces_a_model_source_the_package_form_can_use(self):
        """A managed run's recipe can attach a suggested model_source to the
        underlying job; the package form can read it back and still submit it like
        any other operator-supplied field."""
        self.runner()
        job = self.finished(self.controller.start(RUN_CONFIG))
        source_url = "https://huggingface.co/ggml-org/Qwen3-4B-GGUF/resolve/abc123/Qwen3-4B-Q4_K_M.gguf"
        self.controller.set_prefill(job["id"], model_source=source_url)
        self.assertEqual(self.controller.detail(job["id"])["model_source"], source_url)

        packaged = self.controller.package(job["id"], {
            "model": "Qwen/Qwen3-4B", "model_revision": "abc123", "framework": "llama.cpp",
            "framework_version": "test-build", "submitter": "tester", "model_source": source_url,
        })
        self.assertTrue(packaged["validation"]["valid"], packaged)
        run = self.controller.run_directory(job["id"])
        result = json.loads((run / "result.json").read_text())
        self.assertEqual(result["model"]["source"], source_url)

        recovered = Controller(self.root)
        self.assertEqual(recovered.detail(job["id"])["summary"], job["summary"])

    def test_nonzero_missing_and_zero_success_reports_are_failed(self):
        bad = copy.deepcopy(GUIDELLM_REPORT_V2)
        bad["benchmarks"][0]["metrics"]["request_totals"] = {"successful": 0, "incomplete": 0, "errored": 50, "total": 50}
        for mode, report in [("fail", None), ("missing", None), ("success", bad)]:
            with self.subTest(mode=mode):
                self.runner(mode, report)
                job = self.finished(self.controller.start(RUN_CONFIG))
                self.assertEqual(job["status"], "failed", job)
                self.assertIsNone(job["summary"])

    def test_cancel_stops_cli_descendants_and_keeps_unrelated_server(self):
        self.runner("wait")
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            job = self.controller.start(RUN_CONFIG)
            pid_file = self.root / "child.pid"
            wait_for(pid_file.exists)
            child = int(pid_file.read_text())
            with self.assertRaisesRegex(UIError, "Another operation"):
                self.controller.start(RUN_CONFIG)
            self.controller.cancel(job["id"])
            job = self.finished(job)
            self.assertEqual(job["status"], "cancelled")
            self.assertIsNone(job["summary"])
            self.assertIsNone(unrelated.poll())
            # ps distinguishes a terminated zombie from a running descendant on Linux/macOS.
            def stopped():
                output = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True).stdout.strip()
                return not output or output.startswith("Z")
            wait_for(stopped)
            self.runner()
            self.assertEqual(self.finished(self.controller.start(RUN_CONFIG))["status"], "completed")
        finally:
            unrelated.terminate()
            unrelated.wait(3)

    def test_shutdown_cancels_and_recovery_marks_interruption(self):
        self.runner("wait")
        job = self.controller.start(RUN_CONFIG)
        wait_for((self.root / "child.pid").exists)
        self.controller.close()
        self.assertEqual(self.controller.detail(job["id"])["status"], "cancelled")
        job["status"] = "running"
        self.controller._save(job)
        restored = Controller(self.root)
        self.assertEqual(restored.detail(job["id"])["status"], "interrupted")
        self.assertFalse(restored.busy)


if __name__ == "__main__":
    unittest.main()
