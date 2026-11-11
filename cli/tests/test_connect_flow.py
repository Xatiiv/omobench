"""Connect, choose a model, confirm the hardware, run.

The listing behind "Connect" is quick and loopback-only, and what a person
confirms about the serving machine before a run is recorded with that run --
as their declaration, bounded, and refused when it says nothing usable.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from omobench.ui import Controller, UIError, _serving_at_start


ROOT = Path(__file__).resolve().parents[2]


class FakeServer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        models = self.server.models
        if self.path == "/v1/models":
            body = json.dumps({"object": "list", "data": [{"id": m} for m in models]}).encode()
            self.send_response(200)
        elif self.path.startswith("/api/v0/models/") and self.server.lmstudio:
            model = self.path.rsplit("/", 1)[1]
            body = json.dumps({"id": model, "compatibility_type": "gguf",
                               "quantization": "Q4_K_M"}).encode()
            self.send_response(200)
        else:
            body = b"{}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ServedModelListTests(unittest.TestCase):
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
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
        self.server.models, self.server.lmstudio = ["only-model"], True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def listing(self) -> dict:
        return self.controller.served_model_list({"endpoint": self.endpoint})

    def test_a_sole_model_is_listed_with_what_lm_studio_says_about_it(self) -> None:
        answer = self.listing()
        self.assertEqual(answer["models"], ["only-model"])
        self.assertEqual(answer["server_metadata"]["only-model"]["quantization"], "Q4_K_M")

    def test_several_models_are_all_listed_for_the_person_to_choose(self) -> None:
        self.server.models = ["model-a", "model-b", "model-c"]
        self.assertEqual(self.listing()["models"], ["model-a", "model-b", "model-c"])

    def test_a_server_that_is_not_lm_studio_still_lists_its_models(self) -> None:
        self.server.lmstudio = False
        answer = self.listing()
        self.assertEqual(answer["models"], ["only-model"])
        self.assertEqual(answer["server_metadata"], {})

    def test_an_address_off_this_machine_is_refused(self) -> None:
        with self.assertRaises(UIError):
            self.controller.served_model_list({"endpoint": "http://192.0.2.1:1234"})


class ServingAtStartTests(unittest.TestCase):
    GPU = {"placement": {"mode": "gpu-only"},
           "accelerators": [{"vendor": "amd", "model": "AMD Radeon RX 9070", "count": 1, "role": "inference"},
                            {"vendor": "amd", "model": "AMD Radeon(TM) Graphics", "count": 1,
                             "role": "present-not-used"}],
           "host": {"os": "Windows 11 Pro", "cpu": "AMD Ryzen 7 9800X3D", "ram_gb": 64},
           "informed_by": ["read on the Windows machine by the companion"]}

    def test_a_confirmed_gpu_run_is_recorded_as_confirmed(self) -> None:
        recorded = _serving_at_start(self.GPU)
        self.assertEqual(recorded["placement"]["mode"], "gpu-only")
        self.assertEqual([d["role"] for d in recorded["accelerators"]], ["inference", "present-not-used"])
        self.assertIn("confirmed_at", recorded)

    def test_nothing_sent_records_nothing(self) -> None:
        self.assertIsNone(_serving_at_start(None))

    def test_a_placement_nobody_chose_is_refused(self) -> None:
        with self.assertRaises(UIError):
            _serving_at_start({**self.GPU, "placement": {"mode": "unknown"}})

    def test_a_gpu_run_that_names_no_card_is_refused(self) -> None:
        unnamed = {**self.GPU, "accelerators": [{**d, "role": "present-not-used"} for d in self.GPU["accelerators"]]}
        with self.assertRaises(UIError) as refused:
            _serving_at_start(unnamed)
        self.assertIn("which card", str(refused.exception))

    def test_a_cpu_run_needs_no_card(self) -> None:
        self.assertEqual(_serving_at_start({"placement": {"mode": "cpu-only"}})["placement"]["mode"], "cpu-only")


if __name__ == "__main__":
    unittest.main()
