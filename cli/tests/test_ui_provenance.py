"""What the panel does once a bridged run is no longer a dead end.

The panel used to answer "Windows bridge measurements are local-only. Download
the local report instead." to every packaging and submission attempt. It can
now package one, provided the contributor describes the machine that served the
model -- which the panel cannot discover, and does not pretend to.

All synthetic. No benchmark runs and no model is contacted.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omobench.ui import Controller, UIError
from test_cli import GUIDELLM_REPORT_V2
from test_ui import make_root

BRIDGE_TRANSPORT = {
    "kind": "windows-stdio-http1-v1",
    "windows_port": 1234,
    "runner_host": "WSL2",
    "serving_host": "Windows loopback, hardware not independently verified",
    "protocol": "HTTP/1.1, one request per connection",
    "overhead": "Included in measured latency and throughput, not subtracted",
    "publication": "local-only",
}

WSL_ENVIRONMENT = {
    "operating_system": "Linux-6.6-microsoft-standard-WSL2-x86_64",
    "python": "3.12.3", "cpu": "x86_64", "ram_gb": 31.2, "gpus": [],
}

SERVING = {
    "host": {"os": "Windows 11 Pro 10.0.26200", "cpu": "AMD Ryzen 7 9800X3D", "ram_gb": 64.0},
    "accelerators": [{"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": 16.0,
                      "count": 1, "role": "inference"}],
    "placement": {"mode": "gpu-only"},
    "runtime": {"application": {"name": "LM Studio", "version": "0.3.31"},
                "engine": {"name": "llama.cpp", "version": "b10621"},
                "settings": [{"name": "context_length", "value": "8192"}]},
}

PACKAGE_FORM = {
    "model": "Qwen/Qwen3-4B", "model_revision": "abc123", "framework": "lmstudio",
    "framework_version": "b10621", "submitter": "local-tester", "quantization": "Q4_K_M",
    "runtime_args": [],
}


class PanelFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        make_root(self.root)
        self.controller = Controller(self.root)
        self.addCleanup(self.controller.close)

    def completed_job(self, *, bridged: bool) -> str:
        """A finished job with real-shaped evidence on disk, written directly.

        The run itself is not re-executed here: these tests are about what the
        panel does with a run that already happened.
        """
        import gzip

        job_id = "job-" + "a" * 16
        run = self.root / ".omobench-runs" / "ui" / job_id / "run-fixture-1234"
        run.mkdir(parents=True)
        with gzip.open(run / "guidellm-benchmarks.json.gz", "wt", encoding="utf-8") as raw:
            json.dump(GUIDELLM_REPORT_V2, raw)
        (run / "guidellm.stdout.log").write_text("completed\n", encoding="utf-8")
        (run / "reproduce.md").write_text("# Reproduce\n", encoding="utf-8")
        environment = dict(WSL_ENVIRONMENT)
        if bridged:
            environment["transport"] = dict(BRIDGE_TRANSPORT)
        else:
            environment = {**environment, "operating_system": "Test Linux", "cpu": "Test CPU",
                           "ram_gb": 64.0,
                           "gpus": [{"vendor": "amd", "model": "AMD Radeon RX 9070",
                                     "memory_gb": 16.0}]}
        (run / "environment.json").write_text(json.dumps(environment), encoding="utf-8")
        job = {"id": job_id, "status": "completed", "profile": "chat-small-v1",
               "execution_device": "gpu", "model": "qwen3-4b", "endpoint": "http://127.0.0.1:1234",
               "created_at": "2026-11-03T09:00:00+00:00", "run_id": "run-fixture-1234",
               "summary": {"requests": {"succeeded": 50}}, "tokenizer": None,
               "transport": dict(BRIDGE_TRANSPORT) if bridged else None,
               "validation": None, "submission": None, "error": None, "log": ""}
        with self.controller.lock:
            self.controller.jobs[job_id] = job
            self.controller._save(job)
        return job_id


class PublicationRequirementTests(PanelFixture):
    def test_a_bridged_run_is_no_longer_told_it_is_local_only(self) -> None:
        job_id = self.completed_job(bridged=True)
        report = self.controller.publication_requirements(job_id, dict(PACKAGE_FORM))
        labels = [item["label"] for item in report["items"]]
        self.assertNotIn("Windows bridge: local reports only", labels)
        self.assertIn("Serving environment is described separately from the runner", labels)

    def test_the_serving_requirement_is_unchecked_until_something_is_declared(self) -> None:
        job_id = self.completed_job(bridged=True)
        report = self.controller.publication_requirements(job_id, dict(PACKAGE_FORM))
        serving = next(item for item in report["items"] if item["field"] == "serving")
        self.assertIsNone(serving["ok"])
        self.assertIn("not evidence about what served the model", serving["detail"])

    def test_declaring_the_serving_machine_satisfies_the_requirement(self) -> None:
        job_id = self.completed_job(bridged=True)
        report = self.controller.publication_requirements(
            job_id, {**PACKAGE_FORM, "serving": SERVING})
        serving = next(item for item in report["items"] if item["field"] == "serving")
        self.assertTrue(serving["ok"])
        self.assertIn("declared by you rather than detected", serving["detail"])

    def test_the_bridge_overhead_is_stated_rather_than_treated_as_a_defect(self) -> None:
        job_id = self.completed_job(bridged=True)
        report = self.controller.publication_requirements(job_id, dict(PACKAGE_FORM))
        overhead = next(item for item in report["items"]
                        if item["label"] == "Bridge latency is inside the measurements")
        self.assertTrue(overhead["ok"])
        self.assertIn("never subtracted", overhead["detail"])

    def test_a_direct_run_gains_no_serving_requirement_it_did_not_have(self) -> None:
        job_id = self.completed_job(bridged=False)
        report = self.controller.publication_requirements(job_id, dict(PACKAGE_FORM))
        self.assertNotIn("serving", [item["field"] for item in report["items"]])


class BridgedPackagingTests(PanelFixture):
    def package(self, job_id: str, **extra) -> dict:
        return self.controller.package(job_id, {**PACKAGE_FORM, **extra})

    def test_packaging_a_bridged_run_is_no_longer_refused_outright(self) -> None:
        job_id = self.completed_job(bridged=True)
        detail = self.package(job_id, serving=SERVING)
        self.assertEqual(detail["validation"]["errors"], [])
        self.assertTrue(detail["validation"]["valid"])

    def test_the_packaged_result_names_the_windows_host_not_the_wsl_runner(self) -> None:
        job_id = self.completed_job(bridged=True)
        self.package(job_id, serving=SERVING)
        result = json.loads(
            (self.controller.run_directory(job_id) / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["schema_version"], "1.1")
        self.assertEqual(result["hardware"]["cpu"], "AMD Ryzen 7 9800X3D")
        self.assertEqual(result["environment"]["runner"]["kind"], "wsl2")

    def test_the_declaration_is_kept_beside_the_evidence_for_review(self) -> None:
        job_id = self.completed_job(bridged=True)
        self.package(job_id, serving=SERVING)
        declaration = json.loads((self.controller.run_directory(job_id) / "provenance.json")
                                 .read_text(encoding="utf-8"))
        self.assertEqual(declaration["serving"]["relationship"], "windows-host-wsl-runner")

    def test_packaging_a_bridged_run_with_nothing_declared_fails_with_the_reason(self) -> None:
        job_id = self.completed_job(bridged=True)
        with self.assertRaises(UIError) as raised:
            self.package(job_id)
        self.assertIn("serving machine", str(raised.exception))

    def test_the_served_alias_is_recorded_beside_the_canonical_model(self) -> None:
        job_id = self.completed_job(bridged=True)
        self.package(job_id, serving=SERVING, served_model_id="deepseek-r1-distill-qwen-14b")
        result = json.loads(
            (self.controller.run_directory(job_id) / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["model"]["served_model_id"], "deepseek-r1-distill-qwen-14b")
        self.assertEqual(result["model"]["id"], "Qwen/Qwen3-4B")

    def test_a_direct_run_still_packages_as_schema_1_0(self) -> None:
        job_id = self.completed_job(bridged=False)
        detail = self.controller.package(job_id, dict(PACKAGE_FORM, framework="vllm",
                                                      framework_version="0.11.0"))
        self.assertEqual(detail["validation"]["errors"], [])
        result = json.loads(
            (self.controller.run_directory(job_id) / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["schema_version"], "1.0")
        self.assertFalse((self.controller.run_directory(job_id) / "provenance.json").exists())

    def test_submission_preparation_is_no_longer_blocked_for_a_bridged_run(self) -> None:
        job_id = self.completed_job(bridged=True)
        self.package(job_id, serving=SERVING)
        from omobench.ui_submission import bundle_snapshot
        result, contents, _ = bundle_snapshot(self.root, self.controller.run_directory(job_id))
        self.assertEqual(result["environment"]["transport"]["kind"], "windows-wsl-stdio-bridge")
        self.assertIn("result.json", contents)

    def test_the_pull_request_body_names_the_transport_and_its_overhead(self) -> None:
        job_id = self.completed_job(bridged=True)
        self.package(job_id, serving=SERVING)
        from omobench.ui_submission import bundle_snapshot, description, destination
        result, _, _ = bundle_snapshot(self.root, self.controller.run_directory(job_id))
        _, body = description(result, destination(result))
        self.assertIn("windows-wsl-stdio-bridge", body)
        self.assertIn("has not been subtracted", body)
        self.assertIn("LM Studio 0.3.31", body)


class ModelSuggestionEndpointTests(PanelFixture):
    def test_the_deepseek_alias_is_suggested_from_the_real_catalog(self) -> None:
        report = self.controller.model_suggestions(
            {"served_model_id": "deepseek-r1-distill-qwen-14b"})
        self.assertEqual([candidate["id"] for candidate in report["candidates"]],
                         ["deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"])
        self.assertTrue(report["requires_selection"])

    def test_an_unknown_alias_offers_the_proposal_route_instead(self) -> None:
        report = self.controller.model_suggestions({"served_model_id": "acme/private-v9"})
        self.assertEqual(report["candidates"], [])
        self.assertTrue(report["proposal_available"])

    def test_the_catalog_is_returned_so_a_person_can_choose_from_it(self) -> None:
        report = self.controller.model_suggestions({"served_model_id": ""})
        self.assertIn("Qwen/Qwen3-4B", report["catalog"])


class WeightIdentityEndpointTests(PanelFixture):
    def test_listing_reads_no_file_contents(self) -> None:
        folder = self.root / "weights"
        folder.mkdir()
        (folder / "model.gguf").write_bytes(b"weights")
        (folder / "README.md").write_bytes(b"not weights")
        report = self.controller.weight_identity({"directory": str(folder)})
        self.assertFalse(report["hashed"])
        self.assertEqual([entry["name"] for entry in report["candidates"]], ["model.gguf"])

    def test_hashing_happens_only_for_files_a_person_named(self) -> None:
        folder = self.root / "weights"
        folder.mkdir()
        (folder / "model.gguf").write_bytes(b"weights")
        report = self.controller.weight_identity(
            {"directory": str(folder), "files": ["model.gguf"]})
        self.assertTrue(report["hashed"])
        self.assertTrue(report["revision"].startswith("sha256-manifest:"))

    def test_a_path_outside_the_chosen_folder_is_refused(self) -> None:
        folder = self.root / "weights"
        folder.mkdir()
        with self.assertRaises(UIError):
            self.controller.weight_identity(
                {"directory": str(folder), "files": ["../escape.gguf"]})


class TokenizerEndpointTests(PanelFixture):
    def test_the_preflight_reports_the_level_it_was_allowed_to_reach(self) -> None:
        report = self.controller.tokenizer_preflight(
            {"tokenizer": "Qwen/Qwen3-4B", "network": "none"})
        self.assertEqual(report["network"], "none")
        self.assertIn(report["kind"], {"not-cached", "loaded", "runner-unusable"})

    def test_an_unsupported_level_is_refused(self) -> None:
        with self.assertRaises(UIError):
            self.controller.tokenizer_preflight(
                {"tokenizer": "Qwen/Qwen3-4B", "network": "everything"})

    def test_a_download_plan_names_its_source_and_destination_first(self) -> None:
        plan = self.controller.tokenizer_download_plan({"tokenizer": "Qwen/Qwen3-4B"})
        self.assertEqual(plan["source"], "https://huggingface.co/Qwen/Qwen3-4B")
        self.assertIn("never requested", plan["never_downloads"])

    def test_a_local_folder_has_nothing_to_download(self) -> None:
        folder = self.root / "tokenizer"
        folder.mkdir()
        with self.assertRaises(UIError):
            self.controller.tokenizer_download_plan({"tokenizer": str(folder)})

    def test_the_preflight_does_not_start_while_another_action_holds_the_panel(self) -> None:
        with self.controller.lock:
            self.controller.busy = True
        try:
            with self.assertRaises(UIError):
                self.controller.tokenizer_preflight({"tokenizer": "Qwen/Qwen3-4B"})
        finally:
            with self.controller.lock:
                self.controller.busy = False


if __name__ == "__main__":
    unittest.main()
