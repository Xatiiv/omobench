from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from omobench.ui import UIServer
from omobench import managed_setup as ms
from test_cli import GUIDELLM_REPORT_V2


ROOT = Path(__file__).resolve().parents[2]


def make_root(root: Path) -> None:
    for relative in ("benchmarks", "catalog", "schema", "tools"):
        shutil.copytree(ROOT / relative, root / relative, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "results").mkdir()


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("Timed out waiting for the local test condition")


class GuidedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        make_root(self.root)
        self.server = UIServer(("127.0.0.1", 0), self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.managed.close()
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

    def auth(self):
        return {"X-OmoBench-Token": self.server.token}

    def post(self, path, data):
        headers = {**self.auth(), "Content-Type": "application/json"}
        return self.request(path, headers, "POST", json.dumps(data))

    # -- static assets --------------------------------------------------------

    def test_guided_assets_are_served_without_exposing_the_token(self):
        for path, content_type in [("/guided", "text/html"), ("/guided.js", "text/javascript"), ("/guided.css", "text/css")]:
            status, headers, content = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertIn(content_type, headers["Content-Type"])
            self.assertNotIn(self.server.token.encode(), content)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_index_links_to_guided_page(self):
        _, _, content = self.request("/")
        self.assertIn(b'href="/guided"', content)

    # -- auth ------------------------------------------------------------------

    def test_managed_endpoints_require_the_panel_token(self):
        self.assertEqual(self.request("/api/managed/recipes")[0], 401)
        status, _, body = self.request("/api/managed/recipes", self.auth())
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual([r["id"] for r in payload["recipes"]], ["qwen3-4b-cpu-v1"])

    def test_managed_run_requires_token_too(self):
        headers = {"Content-Type": "application/json"}
        status, _, _ = self.request(
            "/api/managed/runs", headers, "POST", json.dumps({"recipe": "x", "storage_dir": "x", "mode": "smoke"})
        )
        self.assertEqual(status, 401)

    # -- review ------------------------------------------------------------------

    def test_review_endpoint_reports_files_and_platform_without_writing(self):
        with tempfile.TemporaryDirectory() as storage:
            storage_dir = str(Path(storage) / "storage")
            status, _, body = self.post(
                "/api/managed/review", {"recipe": "qwen3-4b-cpu-v1", "storage_dir": storage_dir}
            )
            self.assertEqual(status, 200, body)
            payload = json.loads(body)
            self.assertEqual(len(payload["files"]), 2)
            self.assertFalse(Path(storage_dir).exists())

    def test_review_rejects_unknown_recipe(self):
        with tempfile.TemporaryDirectory() as storage:
            status, _, body = self.post("/api/managed/review", {"recipe": "nope", "storage_dir": str(Path(storage) / "x")})
            self.assertEqual(status, 400)
            self.assertIn("Unknown managed recipe", json.loads(body)["error"])

    # -- prepare (setup jobs) ------------------------------------------------------

    def test_prepare_requires_explicit_confirmation(self):
        status, _, body = self.post(
            "/api/managed/setup-jobs", {"recipe": "qwen3-4b-cpu-v1", "storage_dir": "/tmp/x"}
        )
        self.assertEqual(status, 400)
        self.assertIn("confirm", json.loads(body)["error"].lower())

    def test_setup_job_progresses_and_can_be_cancelled(self):
        release = threading.Event()

        def fake_download_and_install(root, recipe_id, storage_dir, *, progress=None, cancel_event=None):
            if progress:
                progress("model", 1, 2)
            release.wait(5)
            if cancel_event is not None and cancel_event.is_set():
                raise ms.ManagedSetupError("cancelled for test")
            return {"model": {"path": "x"}, "runtime": {"executable_path": "y"}}

        with patch("omobench.managed_setup.download_and_install", side_effect=fake_download_and_install):
            with tempfile.TemporaryDirectory() as storage:
                status, _, body = self.post(
                    "/api/managed/setup-jobs",
                    {"recipe": "qwen3-4b-cpu-v1", "storage_dir": str(Path(storage) / "s"), "confirmed": True},
                )
                self.assertEqual(status, 202)
                job_id = json.loads(body)["id"]

                def has_progress():
                    detail = json.loads(self.request(f"/api/managed/setup-jobs/{job_id}", self.auth())[2])
                    return detail["progress"].get("model")

                wait_for(has_progress)

                status, _, body = self.post(f"/api/managed/setup-jobs/{job_id}/cancel", {})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["status"], "cancelling")
                release.set()

                def finished():
                    detail = json.loads(self.request(f"/api/managed/setup-jobs/{job_id}", self.auth())[2])
                    return detail["status"] in {"cancelled", "failed", "completed"}

                wait_for(finished)
                detail = json.loads(self.request(f"/api/managed/setup-jobs/{job_id}", self.auth())[2])
                self.assertEqual(detail["status"], "cancelled")

    def test_setup_job_listing_and_unknown_job_404(self):
        status, _, _ = self.request("/api/managed/setup-jobs/mjob-0000000000000000", self.auth())
        self.assertEqual(status, 404)
        status, _, body = self.request("/api/managed/setup-jobs", self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), [])

    # -- run mode validation ------------------------------------------------------

    def test_run_requires_a_known_mode(self):
        status, _, body = self.post(
            "/api/managed/runs", {"recipe": "qwen3-4b-cpu-v1", "storage_dir": "/tmp/x", "mode": "bogus"}
        )
        self.assertEqual(status, 400)
        self.assertIn("smoke", json.loads(body)["error"])


@unittest.skipIf(os.name == "nt", "The fixture server/runner use a POSIX shebang and process group")
class GuidedRunLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        make_root(self.root)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.path_patch = patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"]})
        self.path_patch.start()
        for tool in ["nvidia-smi", "rocm-smi", "lspci", "gh"]:
            script = self.bin / tool
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o755)
        guidellm = self.bin / "guidellm"
        guidellm.write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            "if '--version' in sys.argv:\n print('GuideLLM 0.7.3'); sys.exit(0)\n"
            "output = pathlib.Path(sys.argv[sys.argv.index('--output') + 1].split('path=', 1)[1])\n"
            f"output.write_text(json.dumps({GUIDELLM_REPORT_V2!r}))\n"
        )
        guidellm.chmod(0o755)

        self.llama_server = self.root / "llama-server"
        self.llama_server.write_text(
            f"#!{sys.executable}\n"
            "import http.server, json, sys\n"
            "args = sys.argv[1:]\n"
            "alias = args[args.index('--alias') + 1]\n"
            "port = int(args[args.index('--port') + 1])\n"
            "class Handler(http.server.BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        body = json.dumps({'data': [{'id': alias}]}).encode()\n"
            "        self.send_response(200)\n"
            "        self.send_header('Content-Type', 'application/json')\n"
            "        self.send_header('Content-Length', str(len(body)))\n"
            "        self.end_headers()\n"
            "        self.wfile.write(body)\n"
            "    def log_message(self, *a):\n"
            "        pass\n"
            "http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()\n"
        )
        self.llama_server.chmod(0o755)
        self.model_path = self.root / "model.gguf"
        self.model_path.write_bytes(b"fixture-model")

        self.server = UIServer(("127.0.0.1", 0), self.root)
        self.server.controller.environment["PYTHONPATH"] = str(ROOT / "cli" / "src")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        self.fixture_model_source = "https://huggingface.co/fixture-org/Fixture-Model-GGUF/resolve/abc123/fixture.gguf"
        self.get_recipe_patch = patch(
            "omobench.managed_setup.get_recipe",
            return_value={
                "id": "fixture-recipe",
                "model_id": "Fixture/Alias",
                "benchmark_profile": "chat-small-v1",
                "model": {"url": self.fixture_model_source},
            },
        )
        self.verify_patch = patch(
            "omobench.managed_setup.verify_installed",
            return_value={
                "model_path": self.model_path,
                "executable_path": self.llama_server,
                "model_id": "Fixture/Alias",
                "benchmark_profile": "chat-small-v1",
            },
        )
        self.platform_patch = patch(
            "omobench.managed_setup.check_platform", return_value=ms.PlatformStatus(True, "ok")
        )
        self.get_recipe_patch.start()
        self.verify_patch.start()
        self.platform_patch.start()

    def tearDown(self):
        self.get_recipe_patch.stop()
        self.verify_patch.stop()
        self.platform_patch.stop()
        self.server.managed.close()
        self.server.controller.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.path_patch.stop()
        self.temporary.cleanup()

    def request(self, path, headers=None, method="GET", body=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            client.close()

    def auth(self):
        return {"X-OmoBench-Token": self.server.token}

    def post(self, path, data):
        headers = {**self.auth(), "Content-Type": "application/json"}
        return self.request(path, headers, "POST", json.dumps(data))

    def test_smoke_reports_ready_without_a_benchmark_job(self):
        status, _, body = self.post(
            "/api/managed/runs", {"recipe": "fixture-recipe", "storage_dir": str(self.root), "mode": "smoke"}
        )
        self.assertEqual(status, 202, body)
        job_id = json.loads(body)["id"]

        def finished():
            detail = json.loads(self.request(f"/api/managed/runs/{job_id}", self.auth())[2])
            return detail if detail["status"] not in {"starting_server", "waiting_ready"} else None

        detail = wait_for(finished)
        self.assertEqual(detail["status"], "completed")
        self.assertIsNone(detail["benchmark_job_id"])
        self.assertEqual(detail["result"]["model_id"], "Fixture/Alias")

    def test_execute_delegates_to_existing_benchmark_job_and_reaches_completion(self):
        status, _, body = self.post(
            "/api/managed/runs", {"recipe": "fixture-recipe", "storage_dir": str(self.root), "mode": "execute"}
        )
        self.assertEqual(status, 202, body)
        job_id = json.loads(body)["id"]

        def completed():
            detail = json.loads(self.request(f"/api/managed/runs/{job_id}", self.auth())[2])
            return detail if detail["status"] not in {
                "starting_server", "waiting_ready", "running_benchmark"
            } else None

        detail = wait_for(completed, timeout=20)
        self.assertEqual(detail["status"], "completed", detail)
        self.assertIsNotNone(detail["benchmark_job_id"])
        self.assertEqual(detail["result"]["results"]["requests"]["succeeded"], 49)

        benchmark_status, _, benchmark_body = self.request(f"/api/runs/{detail['benchmark_job_id']}", self.auth())
        self.assertEqual(benchmark_status, 200)
        benchmark_detail = json.loads(benchmark_body)
        self.assertEqual(benchmark_detail["execution_device"], "cpu")
        self.assertEqual(benchmark_detail["status"], "completed")
        # The recipe's own pinned download URL prefills model_source, so packaging
        # this run through the advanced panel never requires typing a source by hand.
        self.assertEqual(benchmark_detail["model_source"], self.fixture_model_source)


if __name__ == "__main__":
    unittest.main()
