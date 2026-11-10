"""What Review & submit can fill in for somebody, and what it must ask.

Three sources, held to three different standards. What a run recorded when it
was packaged is its own record and is reused as it stands. What a server says
about the model it has loaded is a fact about now, reported by that server.
And nothing is invented: LM Studio's API names the weights format and the
quantisation, not the engine build or the context the server was started
with, so neither appears here.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from omobench import ui
from omobench.ui import Controller, captured_metadata, lmstudio_model_metadata


ROOT = Path(__file__).resolve().parents[2]
ALIAS = "deepseek-r1-distill-qwen-14b"

LMSTUDIO_MODEL = {
    "id": ALIAS, "object": "model", "type": "llm", "publisher": "lmstudio-community",
    "arch": "qwen2", "compatibility_type": "gguf", "quantization": "Q4_K_M",
    "state": "loaded", "max_context_length": 131072,
}


class FakeLMStudio(BaseHTTPRequestHandler):
    """Answers the one LM Studio endpoint this reads, and nothing else."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == f"/api/v0/models/{ALIAS}":
            body = json.dumps(self.server.document).encode()
            self.send_response(200)
        else:
            body = b'{"error":"not found"}'
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LMStudioMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLMStudio)
        self.server.document = dict(LMSTUDIO_MODEL)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def test_lm_studio_names_the_quantisation_and_the_engine_family(self) -> None:
        facts = lmstudio_model_metadata(self.endpoint, ALIAS)
        self.assertEqual(facts["quantization"], "Q4_K_M")
        self.assertEqual(facts["engine"], "llama.cpp", "a GGUF file runs on llama.cpp")
        self.assertEqual(facts["max_context_length"], 131072)
        self.assertIn("LM Studio", facts["source"])

    def test_nothing_is_invented_that_the_api_does_not_say(self) -> None:
        """No engine build, no loaded context, no GPU layers, no VRAM."""
        facts = lmstudio_model_metadata(self.endpoint, ALIAS)
        for absent in ("version", "engine_version", "context_length", "gpu_layers",
                       "memory_gb", "vram"):
            self.assertNotIn(absent, facts)

    def test_mlx_weights_name_the_mlx_engine(self) -> None:
        self.server.document = {**LMSTUDIO_MODEL, "compatibility_type": "mlx"}
        self.assertEqual(lmstudio_model_metadata(self.endpoint, ALIAS)["engine"], "MLX")

    def test_an_unknown_format_names_no_engine(self) -> None:
        self.server.document = {**LMSTUDIO_MODEL, "compatibility_type": "safetensors"}
        self.assertNotIn("engine", lmstudio_model_metadata(self.endpoint, ALIAS))

    def test_a_server_that_is_not_lm_studio_says_nothing(self) -> None:
        self.assertIsNone(lmstudio_model_metadata(self.endpoint, "some-other-model"))
        self.assertIsNone(lmstudio_model_metadata("http://127.0.0.1:9", ALIAS, timeout=0.5))

    def test_an_answer_about_a_different_model_is_not_believed(self) -> None:
        self.server.document = {**LMSTUDIO_MODEL, "id": "a-different-model"}
        self.assertIsNone(lmstudio_model_metadata(self.endpoint, ALIAS))


class CapturedMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name)

    def test_a_packaged_run_offers_what_it_recorded(self) -> None:
        (self.run_dir / "result.json").write_text(json.dumps({
            "model": {"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", "revision": "a" * 40,
                      "quantization": "Q4_K_M", "served_model_id": ALIAS},
            "runtime": {"framework": "lmstudio", "version": "b10621"},
            "submission": {"submitter": "someone"},
        }), encoding="utf-8")
        (self.run_dir / "provenance.json").write_text(json.dumps({
            "serving": {"host": {"cpu": "AMD Ryzen 7 9800X3D"},
                        "accelerators": [{"vendor": "amd", "model": "AMD Radeon RX 9070",
                                          "memory_gb": 16, "count": 1, "role": "inference"}],
                        "placement": {"mode": "gpu-only"}},
        }), encoding="utf-8")
        captured = captured_metadata(self.run_dir)
        self.assertEqual(captured["model"], "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B")
        self.assertEqual(captured["served_model_id"], ALIAS)
        self.assertEqual(captured["quantization"], "Q4_K_M")
        self.assertEqual(captured["framework_version"], "b10621")
        self.assertEqual(captured["submitter"], "someone")
        self.assertEqual(captured["serving"]["accelerators"][0]["role"], "inference")

    def test_a_run_never_packaged_has_nothing_to_offer(self) -> None:
        self.assertIsNone(captured_metadata(self.run_dir))

    def test_an_unreadable_record_is_ignored_rather_than_guessed_from(self) -> None:
        (self.run_dir / "result.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(captured_metadata(self.run_dir))


class HistoricalRunTests(unittest.TestCase):
    """A probe describes the machine now. A run that finished before the panel
    started may have been served differently."""

    def test_a_run_that_finished_before_the_panel_started_is_historical(self) -> None:
        started = datetime.now(UTC)
        earlier = (started - timedelta(hours=3)).isoformat()
        later = (started + timedelta(minutes=2)).isoformat()
        self.assertTrue(ui._finished_before({"finished_at": earlier}, started))
        self.assertFalse(ui._finished_before({"finished_at": later}, started))

    def test_a_run_with_no_readable_time_is_treated_as_historical(self) -> None:
        self.assertTrue(ui._finished_before({}, datetime.now(UTC)))
        self.assertTrue(ui._finished_before({"finished_at": "yesterday"}, datetime.now(UTC)))


class IdentityStageAgreesWithTheGateTests(unittest.TestCase):
    """The Identify stage and the submission gate read the same three routes,
    so one cannot say a model is settled while the other says it is not."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for relative in ("benchmarks", "catalog", "schema", "tools"):
            shutil.copytree(ROOT / relative, root / relative,
                            ignore=shutil.ignore_patterns("__pycache__"))
        (root / "results").mkdir()
        self.controller = Controller(root)
        self.addCleanup(self.controller.close)

    def identify(self, **values) -> dict:
        report = self.controller.workflow_state(values)
        return next(stage for stage in report["stages"] if stage["id"] == "identify")

    def test_a_declared_eligibility_settles_the_model(self) -> None:
        eligible = {"licence": "apache-2.0", "licence_url": "https://example.invalid/licence",
                    "weights_publicly_downloadable": True, "modality": "text-generation"}
        self.assertNotEqual(self.identify(model="someone/Model-7B")["state"], "done")
        self.assertEqual(self.identify(model="someone/Model-7B", eligibility=eligible)["state"], "done")

    def test_an_incomplete_declaration_does_not(self) -> None:
        partial = {"licence": "apache-2.0", "licence_url": "",
                   "weights_publicly_downloadable": True, "modality": "text-generation"}
        self.assertNotEqual(self.identify(model="someone/Model-7B", eligibility=partial)["state"],
                            "done")


if __name__ == "__main__":
    unittest.main()
