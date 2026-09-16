from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from omobench import launcher, managed_setup as ms


FIXTURE_RECIPE = {
    "id": "fixture-recipe",
    "model_id": "Fixture/Alias",
    "benchmark_profile": "chat-small-v1",
}


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.03)
    raise AssertionError("Timed out waiting for the fixture server condition")


def write_fixture_server(path: Path, *, delay_ready: bool = False) -> None:
    """A tiny fixture llama-server: serves /v1/models with the requested alias."""
    content = f"""#!{sys.executable}
import http.server, json, sys, time

args = sys.argv[1:]
alias = args[args.index('--alias') + 1]
port = int(args[args.index('--port') + 1])
delay_ready = {delay_ready!r}

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/v1/models' and not delay_ready:
            body = json.dumps({{'data': [{{'id': alias}}]}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(503)
            self.end_headers()

    def log_message(self, *a):
        pass

server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
with open({str(path.with_suffix(".pid"))!r}, 'w') as pid_file:
    import os
    pid_file.write(str(os.getpid()))
server.serve_forever()
"""
    path.write_text(content)
    path.chmod(0o755)


@unittest.skipIf(os.name == "nt", "The fixture server uses a POSIX shebang and process group")
class ManagedServerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.executable = self.root / "llama-server"
        write_fixture_server(self.executable)
        self.model_path = self.root / "model.gguf"
        self.model_path.write_bytes(b"fixture-model")

        self.get_recipe_patch = patch("omobench.managed_setup.get_recipe", return_value=FIXTURE_RECIPE)
        self.verify_patch = patch(
            "omobench.managed_setup.verify_installed",
            return_value={
                "model_path": self.model_path,
                "executable_path": self.executable,
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
        self.temporary.cleanup()

    def test_smoke_confirms_readiness_and_stops_server_without_inference(self):
        result = launcher.run_smoke(self.root, "fixture-recipe", self.root)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["model_id"], "Fixture/Alias")
        pid = int(self.executable.with_suffix(".pid").read_text())
        wait_for(lambda: not _pid_alive(pid))

    def test_execute_delegates_to_existing_run_and_stops_server_after(self):
        captured = {}

        def fake_write_local_run(args):
            captured["endpoint"] = args.endpoint
            captured["model"] = args.model
            captured["profile"] = args.profile
            captured["execute"] = args.execute
            return 0

        with patch("omobench.cli.write_local_run", side_effect=fake_write_local_run):
            returncode = launcher.run_execute(self.root, "fixture-recipe", self.root)
        self.assertEqual(returncode, 0)
        self.assertEqual(captured["model"], "Fixture/Alias")
        self.assertEqual(captured["profile"], "chat-small-v1")
        self.assertTrue(captured["execute"])
        self.assertTrue(captured["endpoint"].startswith("http://127.0.0.1:"))
        pid = int(self.executable.with_suffix(".pid").read_text())
        wait_for(lambda: not _pid_alive(pid))

    def test_never_stops_an_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            launcher.run_smoke(self.root, "fixture-recipe", self.root)
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(5)

    def test_platform_check_blocks_before_spawning(self):
        with patch(
            "omobench.managed_setup.check_platform",
            return_value=ms.PlatformStatus(False, "Native Windows cannot run the managed CPU recipe. Use WSL2."),
        ), patch("omobench.launcher.subprocess.Popen") as popen:
            with self.assertRaisesRegex(launcher.LauncherError, "WSL2"):
                launcher.run_smoke(self.root, "fixture-recipe", self.root)
            popen.assert_not_called()

    def test_cleanup_runs_even_when_server_never_becomes_ready(self):
        never_ready = self.root / "never-ready-server"
        write_fixture_server(never_ready, delay_ready=True)
        with patch(
            "omobench.managed_setup.verify_installed",
            return_value={
                "model_path": self.model_path,
                "executable_path": never_ready,
                "model_id": "Fixture/Alias",
                "benchmark_profile": "chat-small-v1",
            },
        ):
            with self.assertRaises(launcher.LauncherError):
                with launcher.managed_server(self.root, "fixture-recipe", self.root) as (process, endpoint, recipe):
                    launcher.wait_for_ready(endpoint, recipe["model_id"], process, timeout=2)
            pid = int(never_ready.with_suffix(".pid").read_text())
            wait_for(lambda: not _pid_alive(pid))

    def test_early_exit_is_reported_without_hanging(self):
        exits_immediately = self.root / "exits-immediately"
        exits_immediately.write_text(f"#!{sys.executable}\nimport sys; sys.exit(3)\n")
        exits_immediately.chmod(0o755)
        with patch(
            "omobench.managed_setup.verify_installed",
            return_value={
                "model_path": self.model_path,
                "executable_path": exits_immediately,
                "model_id": "Fixture/Alias",
                "benchmark_profile": "chat-small-v1",
            },
        ):
            with self.assertRaisesRegex(launcher.LauncherError, "exited early"):
                launcher.run_smoke(self.root, "fixture-recipe", self.root)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class BuildServerCommandTests(unittest.TestCase):
    def test_command_is_cpu_only_and_loopback(self):
        command = launcher.build_server_command(Path("/bin/llama-server"), Path("/models/m.gguf"), "Vendor/Model", 8093)
        self.assertIn("--device", command)
        self.assertIn("none", command)
        self.assertIn("--n-gpu-layers", command)
        self.assertIn("--ctx-size", command)
        self.assertIn("2048", command)
        self.assertIn("--parallel", command)
        self.assertIn("--reasoning", command)
        self.assertIn("off", command)
        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
        self.assertEqual(command[command.index("--alias") + 1], "Vendor/Model")
        self.assertEqual(command[command.index("--port") + 1], "8093")

    def test_free_loopback_port_is_usable(self):
        port = launcher.free_loopback_port()
        self.assertGreater(port, 0)


if __name__ == "__main__":
    unittest.main()
