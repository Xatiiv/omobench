"""Real sockets and helper pipes. Native Windows CI selects the compiled EXE."""
import asyncio
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import Request, ProxyHandler, build_opener

from omobench.bridge import BridgeSession, provenance
from omobench.bridge_transport import Multiplexer, HEADER, CHUNK, DATA, READY, CREDIT
from omobench import cli, ui

HERE = Path(__file__).parent


def helper_command(port):
    executable = os.environ.get("OMOBENCH_TEST_BRIDGE_EXE")
    if executable:
        return [executable, "--bridge-stdio-v1", str(port)]
    if sys.platform == "win32":
        raise unittest.SkipTest("Native Windows helper path not supplied")
    return [sys.executable, str(HERE / "bridge_helper_fixture.py"), str(port)]


class Server:
    def __init__(self):
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def do_GET(self):
                owner.requests.append({k.lower(): v for k, v in self.headers.items()})
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self, *exc):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)


class TransportIntegrationTests(unittest.TestCase):
    def test_real_helper_pipes_sixteen_streams_secret_stripping_and_cleanup(self):
        with Server() as server:
            session = BridgeSession(server.port, _helper_command=helper_command(server.port))
            with session:
                def request(_):
                    req = Request(session.endpoint + "/v1/models", headers={
                        "X-OmoBench-Bridge-Token": session.credentials["token"],
                        "Authorization": "Bearer synthetic-server-credential"})
                    with build_opener(ProxyHandler({})).open(req, timeout=15) as reply:
                        return reply.read()
                with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                    self.assertEqual(list(pool.map(request, range(16))), [b"ok"] * 16)
                secret = session.credentials["token"]
                self.assertEqual(len(server.requests), 16)
                self.assertNotIn(secret, json.dumps(server.requests))
                self.assertTrue(all(r.get("authorization") == "Bearer synthetic-server-credential" for r in server.requests))
                process = session.process
                port = session.frontend.port
            self.assertIsNotNone(process.returncode)
            self.assertFalse(session.thread.is_alive())
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=1)
            with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{server.port}/", timeout=2) as reply:
                self.assertEqual(reply.read(), b"ok")

    def test_helper_death_does_not_fallback_or_restart(self):
        with Server() as server, BridgeSession(server.port, _helper_command=helper_command(server.port)) as session:
            session.process.kill()
            future = asyncio.run_coroutine_threadsafe(session.process.wait(), session.loop)
            future.result(5)
            req = Request(session.endpoint + "/", headers={"X-OmoBench-Bridge-Token": session.credentials["token"]})
            from urllib.error import HTTPError
            with self.assertRaises(HTTPError):
                build_opener(ProxyHandler({})).open(req, timeout=10)
            self.assertEqual(server.requests, [])

    def test_helper_port_refusal_is_not_a_server_response(self):
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        with BridgeSession(port, _helper_command=helper_command(port)) as session:
            req = Request(session.endpoint + "/", headers={"X-OmoBench-Bridge-Token": session.credentials["token"]})
            from urllib.error import HTTPError
            with self.assertRaises(HTTPError) as caught:
                build_opener(ProxyHandler({})).open(req, timeout=10)
            self.assertEqual(caught.exception.code, 502)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversize_frame_fails_closed_before_payload_read(self):
        reader = asyncio.StreamReader()
        class Writer:
            closed = False
            def close(self): self.closed = True
        writer = Writer()
        mux = Multiplexer(reader, writer)
        mux.next_id = 1
        reader.feed_data(HEADER.pack(DATA, 1, CHUNK + 1))
        await asyncio.wait_for(mux.receive(), 1)
        self.assertTrue(mux.closed)
        self.assertTrue(writer.closed)

    async def test_bad_credit_terminates_owned_streams(self):
        from omobench.bridge_transport import Stream
        reader = asyncio.StreamReader()
        class Writer:
            def close(self): pass
        mux = Multiplexer(reader, Writer()); mux.next_id = 1
        stream = Stream(mux, 1); mux.streams[1] = stream
        import struct
        reader.feed_data(HEADER.pack(CREDIT, 1, 4) + struct.pack("!I", 1))
        await asyncio.wait_for(mux.receive(), 1)
        self.assertTrue(stream.closed)
        self.assertFalse(mux.streams)


class InvocationTests(unittest.TestCase):
    def test_cli_packaging_refuses_bridged_environment_without_writing_result(self):
        import tempfile
        from test_cli import build_staged_run_directory, base_package_args
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = build_staged_run_directory(root)
            environment = run / "environment.json"
            document = json.loads(environment.read_text())
            document["transport"] = provenance(1234)
            environment.write_text(json.dumps(document))
            original = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
            with patch.object(cli, "find_repository_root", return_value=root):
                self.assertEqual(cli.package_local_run(base_package_args(run)), 2)
            self.assertFalse((run / "result.json").exists())
            self.assertEqual(original, {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()})

    def test_panel_refuses_packaging_preparation_and_submission_but_exports(self):
        import tempfile
        import shutil
        from test_local_export import SUMMARY
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = HERE.parent.parent
            for name in ("catalog", "benchmarks", "schema", "tools"):
                shutil.copytree(source / name, root / name)
            controller = ui.Controller(root)
            try:
                job = {"id": "job-" + "ab" * 8, "status": "completed", "model": "Fixture/Model",
                       "profile": "chat-small-v2", "execution_device": "cpu", "created_at": "2026-11-03T00:00:00Z",
                       "summary": SUMMARY, "transport": provenance(1234)}
                controller._directory(job["id"]).mkdir()
                controller.jobs[job["id"]] = job
                for action in ("packaging", "preparing", "submitting"):
                    with self.assertRaisesRegex(ui.UIError, "local-only"):
                        controller._begin_action(job["id"], action)
                report = controller.local_report(job["id"])
                self.assertEqual(report["transport"], provenance(1234))
                self.assertFalse(report["publication"]["ready"])
            finally:
                controller.close()

    def test_plain_runner_secret_uses_stdin_not_argv(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                cli._run_guidellm_command(["guidellm", "run", "--backend", "kind=openai_http"],
                    log_path=Path(directory) / "log", root=Path(directory), profile_id="chat-small-v2",
                    telemetry_dir=None, bridge_credentials={"endpoint": "http://127.0.0.1:3456", "token": "secret-canary"})
                args, kwargs = run.call_args
                self.assertNotIn("secret-canary", str(args))
                self.assertIn("secret-canary", kwargs["input"])
                self.assertEqual(args[0][1:3], ["-m", "omobench.bridge_runner"])

    def test_ui_rejects_arbitrary_transport_selection(self):
        with self.assertRaises(ui.UIError):
            ui.run_config({"windows_bridge": "yes"})

    def test_provenance_contains_no_machine_path_or_token(self):
        data = provenance(1234)
        self.assertEqual(data["publication"], "local-only")
        self.assertEqual(data["runner_host"], "WSL2")
        self.assertNotIn("token", json.dumps(data))


if __name__ == "__main__":
    unittest.main()
