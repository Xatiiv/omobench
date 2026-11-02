"""Checking someone else's model server, honestly and in stages.

"The server answered" and "this benchmark can run" are different claims. These
drive the real staged check against isolated fixture servers that behave like
the OpenAI-compatible servers people actually run: some list their models, some
do not, some serve aliases that are not repository names, some answer without
streaming. No inference is performed and no server is installed.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from omobench import cli
from omobench.ui import UIError, endpoint_origin, served_models, server_check


ALIAS = "qwen/qwen3-vl-4b:3"
TOKENIZER = "Qwen/Qwen3-VL-4B-Instruct"


def make_handler(*, models, streaming=True, models_status=200, redirect=False):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, body: bytes, content_type: str):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.path.startswith("/v1/models"):
                self._send(404, b"{}", "application/json")
                return
            if models_status != 200:
                self._send(models_status, b'{"error":"not implemented"}', "application/json")
                return
            payload = {"object": "list", "data": [{"id": name, "object": "model"} for name in models]}
            self._send(200, json.dumps(payload).encode(), "application/json")

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if redirect:
                self.send_response(302)
                self.send_header("Location", "https://example.invalid/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not streaming:
                self._send(200, b'{"choices":[{"message":{"content":"OK"}}]}', "application/json")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n')
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *args):
            pass

    return Handler


@contextmanager
def fixture_server(**options):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(**options))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def stages(report):
    return {stage["id"]: stage for stage in report["stages"]}


class ServedModelListingTests(unittest.TestCase):
    def test_a_server_that_lists_models_is_read_not_guessed(self):
        with fixture_server(models=[ALIAS, "other/model"]) as endpoint:
            listing = served_models(endpoint)
        self.assertTrue(listing["reachable"])
        self.assertEqual(listing["models"], [ALIAS, "other/model"])

    def test_a_server_without_that_endpoint_is_still_reachable(self):
        with fixture_server(models=[], models_status=404) as endpoint:
            listing = served_models(endpoint)
        self.assertTrue(listing["reachable"])
        self.assertIsNone(listing["models"])
        self.assertIn("404", listing["detail"])

    def test_an_empty_listing_says_to_load_a_model(self):
        with fixture_server(models=[]) as endpoint:
            listing = served_models(endpoint)
        self.assertEqual(listing["models"], [])
        self.assertIn("Load a model", listing["detail"])

    def test_nothing_listening_is_reported_as_unreachable(self):
        listing = served_models("http://127.0.0.1:1", timeout=2)
        self.assertFalse(listing["reachable"])
        self.assertIsNone(listing["models"])


class StagedServerCheckTests(unittest.TestCase):
    def check(self, endpoint, model, tokenizer="", **kwargs):
        return server_check({"endpoint": endpoint, "model": model, "tokenizer": tokenizer},
                            **{"can_execute": True, "runner_message": "GuideLLM ready.", **kwargs})

    def test_a_compatible_server_with_an_ordinary_model_id_passes_every_stage(self):
        with tempfile.TemporaryDirectory() as cache:
            (Path(cache) / "models--Qwen--Qwen3-4B").mkdir()
            with fixture_server(models=["Qwen/Qwen3-4B"]) as endpoint, \
                 patch.dict("os.environ", {"HUGGINGFACE_HUB_CACHE": cache}):
                report = self.check(endpoint, "Qwen/Qwen3-4B")
        found = stages(report)
        self.assertEqual({entry["state"] for entry in report["stages"]}, {"ok"})
        self.assertTrue(report["ready"])
        self.assertIn("streams responses", found["streaming"]["detail"])
        self.assertEqual(report["models"], ["Qwen/Qwen3-4B"])

    def test_an_alias_the_server_serves_needs_a_separate_tokenizer(self):
        with fixture_server(models=[ALIAS]) as endpoint:
            without = self.check(endpoint, ALIAS)
            with_override = self.check(endpoint, ALIAS, TOKENIZER)
        # The alias itself is fine for the API and wrong as a tokenizer source.
        self.assertEqual(stages(without)["model"]["state"], "ok")
        self.assertEqual(stages(without)["streaming"]["state"], "ok")
        self.assertEqual(stages(without)["tokenizer"]["state"], "failed")
        self.assertFalse(without["ready"])
        self.assertEqual(stages(with_override)["tokenizer"]["state"], "ok")
        self.assertEqual(stages(with_override)["tokenizer"]["source"], TOKENIZER)
        self.assertTrue(with_override["ready"])

    def test_a_model_the_server_does_not_serve_is_named_against_what_it_does(self):
        with fixture_server(models=["served/one", "served/two"]) as endpoint:
            report = self.check(endpoint, "absent/model", TOKENIZER)
        model = stages(report)["model"]
        self.assertEqual(model["state"], "failed")
        self.assertIn("served/one", model["detail"])
        self.assertIn("served/two", model["detail"])
        self.assertEqual(model["options"], ["served/one", "served/two"])
        self.assertFalse(report["ready"])

    def test_a_server_without_a_model_listing_leaves_that_unknown_and_still_streams(self):
        with fixture_server(models=[], models_status=404) as endpoint:
            report = self.check(endpoint, "whatever/the-server-wants", TOKENIZER)
        found = stages(report)
        self.assertEqual(found["reachable"]["state"], "ok")
        self.assertEqual(found["model"]["state"], "unknown")
        self.assertEqual(found["streaming"]["state"], "ok")
        # An unconfirmable identifier must not block a run the server accepts.
        self.assertTrue(report["ready"])

    def test_a_server_that_cannot_stream_fails_the_check_that_matters(self):
        with fixture_server(models=["served/one"], streaming=False) as endpoint:
            report = self.check(endpoint, "served/one", TOKENIZER)
        streaming = stages(report)["streaming"]
        self.assertEqual(streaming["state"], "failed")
        self.assertIn("streaming /v1/chat/completions", streaming["detail"])
        self.assertEqual(stages(report)["reachable"]["state"], "ok")
        self.assertFalse(report["ready"])

    def test_a_redirecting_server_is_unsupported_api_behaviour_not_a_network_error(self):
        with fixture_server(models=["served/one"], redirect=True) as endpoint:
            report = self.check(endpoint, "served/one", TOKENIZER)
        self.assertEqual(stages(report)["reachable"]["state"], "ok")
        self.assertEqual(stages(report)["streaming"]["state"], "failed")
        self.assertIn("redirect", stages(report)["streaming"]["detail"].lower())

    def test_an_unreachable_server_skips_the_checks_it_makes_impossible(self):
        report = self.check("http://127.0.0.1:1", "served/one", TOKENIZER)
        found = stages(report)
        self.assertEqual(found["reachable"]["state"], "failed")
        self.assertEqual(found["model"]["state"], "skipped")
        self.assertEqual(found["streaming"]["state"], "skipped")
        self.assertIn("loopback on this machine", found["reachable"]["detail"])
        # The offline tokenizer check does not depend on the server at all.
        self.assertEqual(found["tokenizer"]["state"], "ok")

    def test_a_non_loopback_address_is_refused_with_the_reason_not_a_shrug(self):
        report = self.check("http://192.168.1.50:8080", "served/one")
        found = stages(report)
        self.assertEqual(found["endpoint"]["state"], "failed")
        self.assertIn("loopback", found["endpoint"]["detail"])
        self.assertIn("loopback forwarder", found["endpoint"]["detail"])
        self.assertEqual({found[name]["state"] for name in ("runner", "reachable", "model", "streaming", "tokenizer")},
                         {"skipped"})
        self.assertFalse(report["ready"])

    def test_the_report_says_which_environment_it_checked_from(self):
        with fixture_server(models=["served/one"]) as endpoint:
            runnable = self.check(endpoint, "served/one", TOKENIZER)
            unrunnable = self.check(endpoint, "served/one", TOKENIZER, can_execute=False,
                                    runner_message="Start this panel from WSL2, Linux or macOS.")
        self.assertIn("launches the benchmark runner", runnable["checked_from"])
        self.assertIn("cannot run the benchmark", unrunnable["checked_from"])
        self.assertEqual(stages(unrunnable)["runner"]["state"], "failed")
        self.assertIn("WSL2", stages(unrunnable)["runner"]["detail"])
        # The server itself was still checked, honestly labelled.
        self.assertEqual(stages(unrunnable)["streaming"]["state"], "ok")
        self.assertFalse(unrunnable["ready"])

    def test_no_model_entered_yet_offers_the_servers_own_identifiers(self):
        with fixture_server(models=["served/one", "served/two"]) as endpoint:
            report = self.check(endpoint, "", TOKENIZER)
        found = stages(report)
        self.assertEqual(found["model"]["state"], "unknown")
        self.assertEqual(found["model"]["options"], ["served/one", "served/two"])
        self.assertEqual(found["streaming"]["state"], "skipped")
        self.assertFalse(report["ready"])

    def test_the_report_always_names_what_it_did_not_establish(self):
        with fixture_server(models=["served/one"]) as endpoint:
            report = self.check(endpoint, "served/one", TOKENIZER)
        joined = " ".join(report["not_established"])
        self.assertIn("matches the weights", joined)
        self.assertIn("Which device performed inference", joined)
        self.assertIn("throughput", joined)
        self.assertIn("community registry", joined)

    def test_a_local_tokenizer_directory_is_accepted_as_a_source(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "tokenizer.json").write_text("{}", encoding="utf-8")
            with fixture_server(models=[ALIAS]) as endpoint:
                report = self.check(endpoint, ALIAS, folder)
        tokenizer = stages(report)["tokenizer"]
        self.assertEqual(tokenizer["state"], "ok")
        self.assertEqual(tokenizer["kind"], "local")
        self.assertIn("Nothing is downloaded", tokenizer["detail"])


class RunnerStageTests(unittest.TestCase):
    """The runner stage says which question it answered."""

    def setUp(self):
        self.server = fixture_server(models=["served/one"])
        self.endpoint = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)

    def check(self, **kwargs):
        return server_check({"endpoint": self.endpoint, "model": "served/one",
                             "tokenizer": TOKENIZER}, **kwargs)

    def test_without_a_health_result_the_stage_admits_it_read_metadata_only(self):
        report = self.check(can_execute=True, runner_message="GuideLLM 0.7.3 ready.")
        runner = stages(report)["runner"]
        self.assertEqual(runner["title"], "Benchmark runner available")
        self.assertIn("installed metadata only", runner["detail"])
        self.assertIn("not whether the runner imports", runner["detail"])

    def test_a_real_import_is_reported_as_one(self):
        health = cli.runner_health_check(
            probe=("import json, sys; print('OMOBENCH_RUNNER_HEALTH ' + json.dumps("
                   f"{{'guidellm': {cli.GUIDELLM_VERSION!r}, 'python': sys.version.split()[0],"
                   " 'executable': sys.executable}))"),
            timeout=30)
        report = self.check(can_execute=True, runner_health=health)
        runner = stages(report)["runner"]
        self.assertEqual(runner["title"], "Benchmark runner imports")
        self.assertEqual(runner["state"], "ok")
        self.assertIn("imported successfully", runner["detail"])
        self.assertIn("nothing was downloaded", runner["checked"])

    def test_an_environment_that_cannot_import_blocks_readiness(self):
        health = cli.runner_health_check(probe="import omobench_not_a_real_module", timeout=30)
        report = self.check(can_execute=True, runner_health=health)
        runner = stages(report)["runner"]
        self.assertEqual(runner["state"], "failed")
        self.assertIn("not fully installed", runner["detail"])
        self.assertFalse(report["ready"])


class LoopbackBoundaryTests(unittest.TestCase):
    """The loopback rule is a boundary, not an oversight."""

    def test_only_loopback_hosts_are_accepted(self):
        for accepted in ("http://127.0.0.1:8080", "http://localhost:1234/v1", "http://[::1]:9000"):
            with self.subTest(endpoint=accepted):
                self.assertTrue(endpoint_origin(accepted).startswith("http"))
        for refused in ("http://192.168.1.50:8080", "http://10.0.0.5:1234", "https://example.invalid/v1",
                        "http://host.docker.internal:1234", "http://user:pw@127.0.0.1:8080",
                        "http://127.0.0.1:8080/other"):
            with self.subTest(endpoint=refused), self.assertRaises(UIError):
                endpoint_origin(refused)

    def test_the_refusal_explains_the_windows_side_case(self):
        with self.assertRaises(UIError) as caught:
            endpoint_origin("http://172.20.0.1:1234")
        message = str(caught.exception)
        self.assertIn("Windows", message)
        self.assertIn("loopback forwarder", message)


if __name__ == "__main__":
    unittest.main()
