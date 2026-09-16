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

    def test_root_serves_the_guided_page_by_default(self):
        """omobench ui now opens the guided experience by default (previously the
        advanced panel)."""
        status_root, _, content_root = self.request("/")
        status_guided, _, content_guided = self.request("/guided")
        self.assertEqual(status_root, 200)
        self.assertEqual(content_root, content_guided)
        self.assertIn(b"Choose your test", content_root)

    def test_advanced_panel_stays_available_and_cross_links_with_guided(self):
        status, _, content = self.request("/advanced")
        self.assertEqual(status, 200)
        self.assertIn(b'href="/"', content)  # back to the guided page
        _, _, guided_content = self.request("/")
        self.assertIn(b'href="/advanced"', guided_content)

    # -- auth ------------------------------------------------------------------

    def test_managed_endpoints_require_the_panel_token(self):
        self.assertEqual(self.request("/api/managed/recipes")[0], 401)
        status, _, body = self.request("/api/managed/recipes", self.auth())
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(
            [r["id"] for r in payload["recipes"]], ["qwen3-4b-cpu-v1", "qwen3-4b-cpu-extended-v1"]
        )

    def test_extended_recipe_is_selectable_alongside_the_classic_one(self):
        """The guided page's recipe picker (guided.js only auto-selects when exactly
        one recipe exists) now has a genuine choice: the classic chat-small-v1 recipe
        and the extended-duration one, both CPU, both reusing the same artifacts."""
        _, _, body = self.request("/api/managed/recipes", self.auth())
        recipes = {r["id"]: r for r in json.loads(body)["recipes"]}
        self.assertEqual(recipes["qwen3-4b-cpu-v1"]["benchmark_profile"], "chat-small-v1")
        self.assertEqual(recipes["qwen3-4b-cpu-extended-v1"]["benchmark_profile"], "chat-small-extended-v1")
        for recipe in recipes.values():
            self.assertEqual(recipe["model_id"], "Qwen/Qwen3-4B")
            self.assertEqual(recipe["execution_device"], "cpu")

    def test_recipes_carry_their_real_profile_duration_and_request_ceiling(self):
        """The guided page picks a "recommended for slower hardware" recipe and
        explains its duration ceiling from this real, profile-sourced data -- never
        a hard-coded recipe id or an invented number."""
        _, _, body = self.request("/api/managed/recipes", self.auth())
        recipes = {r["id"]: r for r in json.loads(body)["recipes"]}
        self.assertEqual(recipes["qwen3-4b-cpu-v1"]["max_duration_seconds"], 900)
        self.assertEqual(recipes["qwen3-4b-cpu-v1"]["max_requests"], 50)
        self.assertEqual(recipes["qwen3-4b-cpu-extended-v1"]["max_duration_seconds"], 5400)
        self.assertEqual(recipes["qwen3-4b-cpu-extended-v1"]["max_requests"], 60)

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

    def test_storage_folder_is_remembered_and_suggested_next_time(self):
        """"Reuse previously selected storage" -- never assumes either developer's
        own path, but a folder the operator already picked for this checkout comes
        back as the suggestion next time /api/managed/recipes is fetched."""

        def fake_download_and_install(root, recipe_id, storage_dir, *, progress=None, cancel_event=None):
            Path(storage_dir).mkdir(parents=True, exist_ok=True)  # the real installer creates it too
            return {"model": {"path": "x"}, "runtime": {"executable_path": "y"}}

        with patch("omobench.managed_setup.download_and_install", side_effect=fake_download_and_install):
            with tempfile.TemporaryDirectory() as storage:
                storage_dir = str(Path(storage) / "remember-me")
                status, _, body = self.post(
                    "/api/managed/setup-jobs",
                    {"recipe": "qwen3-4b-cpu-v1", "storage_dir": storage_dir, "confirmed": True},
                )
                self.assertEqual(status, 202, body)
                job_id = json.loads(body)["id"]

                def finished():
                    detail = json.loads(self.request(f"/api/managed/setup-jobs/{job_id}", self.auth())[2])
                    return detail["status"] == "completed"

                wait_for(finished)
                status, _, body = self.request("/api/managed/recipes", self.auth())
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["default_storage_dir"], storage_dir)

    # -- run mode validation ------------------------------------------------------

    def test_run_requires_a_known_mode(self):
        status, _, body = self.post(
            "/api/managed/runs", {"recipe": "qwen3-4b-cpu-v1", "storage_dir": "/tmp/x", "mode": "bogus"}
        )
        self.assertEqual(status, 400)
        self.assertIn("smoke", json.loads(body)["error"])

    def test_unknown_managed_run_is_404_not_silently_treated_as_resumed(self):
        """The guided page distinguishes "the panel restarted, this run's own
        bookkeeping is gone" from any other error using exactly this 404 -- it
        never claims a benchmark resumed when the panel has no record of it."""
        status, _, body = self.request("/api/managed/runs/mrun-0000000000000000", self.auth())
        self.assertEqual(status, 404)
        self.assertIn("Unknown managed run.", json.loads(body)["error"])
        status, _, body = self.request("/api/managed/runs", self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), [])


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
                "quantization": "Q4_K_M",
                "model": {"url": self.fixture_model_source, "revision": "fixture-revision-abc123"},
                "runtime": {"version": "b99999"},
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

        # Reconnect-after-refresh relies on the still-active run showing up in the
        # plain listing endpoint, exactly like the advanced panel's own jobs do --
        # confirm that happens for at least one of this run's active phases.
        def visible_while_active():
            listing = json.loads(self.request("/api/managed/runs", self.auth())[2])
            match = next((j for j in listing if j["id"] == job_id), None)
            return match if match and match["status"] in {"starting_server", "waiting_ready", "running_benchmark"} else None

        wait_for(visible_while_active)

        def completed():
            detail = json.loads(self.request(f"/api/managed/runs/{job_id}", self.auth())[2])
            return detail if detail["status"] not in {
                "starting_server", "waiting_ready", "running_benchmark"
            } else None

        detail = wait_for(completed, timeout=20)
        self.assertEqual(detail["status"], "completed", detail)
        self.assertIsNotNone(detail["benchmark_job_id"])
        self.assertEqual(detail["result"]["results"]["requests"]["succeeded"], 50)

        benchmark_status, _, benchmark_body = self.request(f"/api/runs/{detail['benchmark_job_id']}", self.auth())
        self.assertEqual(benchmark_status, 200)
        benchmark_detail = json.loads(benchmark_body)
        self.assertEqual(benchmark_detail["execution_device"], "cpu")
        self.assertEqual(benchmark_detail["status"], "completed")
        # Every field the reviewed recipe already establishes with certainty is
        # filled in automatically, so the guided page's "Share results" screen
        # (and the advanced panel's package form) never require typing a model
        # identity, weight source, revision, quantization, or runtime version by
        # hand -- only the submitter identity is genuinely unknown.
        self.assertEqual(benchmark_detail["model_source"], self.fixture_model_source)
        self.assertEqual(benchmark_detail["model_revision"], "fixture-revision-abc123")
        self.assertEqual(benchmark_detail["quantization"], "Q4_K_M")
        self.assertEqual(benchmark_detail["framework"], "llama.cpp")
        self.assertEqual(benchmark_detail["framework_version"], "b99999")
        self.assertIn("--device", benchmark_detail["runtime_args"])
        self.assertIn("none", benchmark_detail["runtime_args"])

        # Once no longer active, the run job no longer appears in the active
        # listing either -- reconnect logic sees a real terminal state, not a
        # stale "still active" row.
        listing = json.loads(self.request("/api/managed/runs", self.auth())[2])
        self.assertEqual(next(j for j in listing if j["id"] == job_id)["status"], "completed")

    def test_a_download_failure_is_reported_honestly_and_never_starts_a_server(self):
        """"Missing downloads": a corrupted/interrupted download must fail the
        setup job with its real reason, and must never let the launcher start a
        server against files that were never actually verified."""
        with patch(
            "omobench.managed_setup.download_and_install",
            side_effect=ms.ManagedSetupError("model failed SHA-256 verification and was quarantined, not installed."),
        ), patch("omobench.launcher.subprocess.Popen") as popen:
            with tempfile.TemporaryDirectory() as storage:
                status, _, body = self.post(
                    "/api/managed/setup-jobs",
                    {"recipe": "fixture-recipe", "storage_dir": storage, "confirmed": True},
                )
                self.assertEqual(status, 202, body)
                job_id = json.loads(body)["id"]

                def finished():
                    detail = json.loads(self.request(f"/api/managed/setup-jobs/{job_id}", self.auth())[2])
                    return detail if detail["status"] in {"failed", "completed", "cancelled"} else None

                detail = wait_for(finished)
                self.assertEqual(detail["status"], "failed")
                self.assertIn("quarantined", detail["error"])
                popen.assert_not_called()

    def test_completed_guided_run_can_be_packaged_reviewed_and_downloaded_without_gh(self):
        """Packaging and submission review are integrated directly into the guided
        flow, reusing the existing Controller endpoints. Without gh authentication,
        direct submission stays unavailable but the validated bundle can still be
        downloaded -- a completed process is not automatically a publishable
        result, and a local result stays usable either way."""
        status, _, body = self.post(
            "/api/managed/runs", {"recipe": "fixture-recipe", "storage_dir": str(self.root), "mode": "execute"}
        )
        job_id = json.loads(body)["id"]

        def completed():
            detail = json.loads(self.request(f"/api/managed/runs/{job_id}", self.auth())[2])
            return detail if detail["status"] not in {
                "starting_server", "waiting_ready", "running_benchmark"
            } else None

        detail = wait_for(completed, timeout=20)
        benchmark_job_id = detail["benchmark_job_id"]

        with patch("omobench.cli.current_git_commit", return_value="a" * 40):
            status, _, body = self.post(
                f"/api/runs/{benchmark_job_id}/package",
                {
                    # A real recipe's model_id is the canonical catalog identity;
                    # this fixture's alias ("Fixture/Alias") is only a stand-in for
                    # the API name used during inference, so the package call uses
                    # a real catalog-eligible model id here, as an actual managed
                    # recipe's own model_id would.
                    "model": "Qwen/Qwen3-4B",
                    "model_revision": "fixture-revision-abc123",
                    "framework": "llama.cpp",
                    "framework_version": "b99999",
                    "quantization": "Q4_K_M",
                    "model_source": self.fixture_model_source,
                    "runtime_args": ["--device", "none"],
                    "submitter": "octocat",
                },
            )
        self.assertEqual(status, 200, body)
        packaged = json.loads(body)
        self.assertTrue(packaged["validation"]["valid"], packaged["validation"])

        status, _, body = self.post(f"/api/runs/{benchmark_job_id}/prepare", {})
        self.assertEqual(status, 200, body)
        preview = json.loads(body)
        # This fixture's "gh" stub exits 0 but implements no real GitHub API, so
        # authentication/repository access is effectively unavailable here: direct
        # submission stays unavailable, but the reviewed bundle can still be
        # downloaded either way.
        self.assertFalse(preview["can_submit"])
        self.assertTrue(preview["message"])

        status, headers, body = self.request(f"/api/runs/{benchmark_job_id}/bundle", self.auth())
        self.assertEqual(status, 200)
        self.assertIn("application/zip", headers["Content-Type"])
        self.assertGreater(len(body), 0)


if __name__ == "__main__":
    unittest.main()
