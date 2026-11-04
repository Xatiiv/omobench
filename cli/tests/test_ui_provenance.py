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

from omobench import bridge  # noqa: F401  (patched by name below)
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
               "run_path": str(run),
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

    def test_describing_the_serving_machine_twice_is_refused(self) -> None:
        """Two answers to one question is worse than being asked once."""
        job_id = self.completed_job(bridged=True)
        for extra in ({"cpu": "Some Other CPU"}, {"gpu_memory_gb": 24}):
            with self.subTest(extra=extra):
                with self.assertRaises(UIError) as raised:
                    self.package(job_id, serving=SERVING, **extra)
                self.assertIn("describing the serving machine above", str(raised.exception))

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


class WorkflowPreviewTests(PanelFixture):
    """What a public submission will need, said before the benchmark runs.

    The requirements used to appear only after a run finished, which is how
    somebody spends an hour measuring a model and then learns there is no route
    for it. They are previewed from the same checks that later judge the real
    run, so the preview and the verdict cannot disagree.
    """

    def workflow(self, **data) -> dict:
        return self.controller.workflow_state(data)

    def states(self, report: dict) -> dict:
        return {item["id"]: item["state"] for item in report["checks"]}

    def gate(self, report: dict, identifier: str) -> str | None:
        return next(item["blocks"] for item in report["checks"] if item["id"] == identifier)

    def test_a_local_report_needs_none_of_it(self) -> None:
        report = self.workflow(model="Qwen/Qwen3-4B")
        self.assertIn("Saving a local report needs none of this", report["local_report"])
        self.assertIn("any model, on any hardware", report["local_report"])

    def test_the_three_gates_are_described_separately(self) -> None:
        gates = self.workflow(model="Qwen/Qwen3-4B")["gates"]
        self.assertEqual(set(gates), {"run", "package", "submit"})
        self.assertIn("can stop a benchmark or invalidate a bundle", gates["submit"])

    def test_requirements_are_previewed_before_any_run_exists(self) -> None:
        report = self.workflow(model="acme/private-v9")
        self.assertFalse(report["has_run"])
        self.assertIn("weights-revision", self.states(report))
        self.assertIn("catalog", self.states(report))

    def test_a_catalogued_model_is_already_satisfied(self) -> None:
        report = self.workflow(model="Qwen/Qwen3-4B")
        self.assertEqual(self.states(report)["catalog"], "ok")
        self.assertTrue(report["proposal"]["catalogued"])

    def test_an_uncatalogued_model_blocks_submission_and_nothing_else(self) -> None:
        report = self.workflow(model="acme/private-v9")
        self.assertEqual(self.gate(report, "catalog"), "submit")
        self.assertNotIn("catalog", report["blocking"]["run"])
        self.assertNotIn("catalog", report["blocking"]["package"])

    def test_an_unloaded_tokenizer_blocks_running(self) -> None:
        report = self.workflow(model="unsloth/Model-GGUF")
        self.assertEqual(self.states(report)["tokenizer"], "unknown")
        self.assertIn("tokenizer", report["blocking"]["run"])
        recovery = next(item["recovery"] for item in report["checks"]
                        if item["id"] == "tokenizer")
        self.assertIn("contains no Transformers tokenizer", recovery)

    def test_an_unchecked_endpoint_is_reported_as_unchecked(self) -> None:
        report = self.workflow(model="Qwen/Qwen3-4B", endpoint="http://127.0.0.1:9999")
        self.assertEqual(self.states(report)["endpoint"], "unknown")
        self.assertIn("endpoint", report["blocking"]["run"])

    def test_the_stepper_starts_at_connect_with_nothing_done(self) -> None:
        # Pinned so the stage does not depend on whether this particular
        # machine can run a benchmark at all.
        with patch.object(Controller, "runner_readiness", return_value=(True, "ready")):
            stages = {stage["id"]: stage["state"]
                      for stage in self.workflow(model="Qwen/Qwen3-4B")["stages"]}
        self.assertEqual(stages["connect"], "current")
        self.assertEqual(stages["run"], "todo")

    def test_an_unusable_runner_blocks_the_first_stage_rather_than_waiting(self) -> None:
        with patch.object(Controller, "runner_readiness",
                          return_value=(False, "Install the runner first.")):
            report = self.workflow(model="Qwen/Qwen3-4B")
        stages = {stage["id"]: stage["state"] for stage in report["stages"]}
        self.assertEqual(stages["connect"], "blocked")
        self.assertIn("runner", report["blocking"]["run"])

    def test_choosing_the_bridge_previews_its_serving_requirement(self) -> None:
        """Found out before the run, not after an hour of measuring."""
        report = self.workflow(model="Qwen/Qwen3-4B", windows_bridge=True)
        self.assertIn("serving-environment", report["blocking"]["package"])
        direct = self.workflow(model="Qwen/Qwen3-4B")
        self.assertNotIn("serving-environment", direct["blocking"]["package"])

    def test_an_invalid_bridge_selection_is_refused(self) -> None:
        with self.assertRaises(UIError):
            self.workflow(model="Qwen/Qwen3-4B", windows_bridge="yes")


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


COMPANION = r"C:\Program Files\OmoBench\OmoBenchCompanion.exe"

PROBE_DOCUMENT = {
    "kind": "omobench-serving-hardware-probe-v1",
    "os": "Microsoft Windows 11 Pro 10.0.26200",
    "cpu": "AMD Ryzen 7 9800X3D",
    "ram_gb": 63.72,
    # What the real companion reports: a driver version it also knows, and no
    # video memory, because Windows reports that capped and wrong.
    "accelerators": [
        {"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": None, "count": 1,
         "driver_version": "32.0.15.9671"},
    ],
}


class ServingProbeTests(PanelFixture):
    """Reading the Windows machine's hardware, on the Windows machine."""

    def probe(self, *, returncode=0, stdout=None, raises=None):
        """Stand in for the companion, and only for the companion.

        Every other subprocess the panel starts -- packaging runs the real CLI
        -- goes through untouched, so these tests exercise the real packaging
        path with a stand-in reading of the serving machine.
        """
        import subprocess as subprocess_module

        calls = []
        real_run = subprocess_module.run

        def fake_run(command, **kwargs):
            if not (isinstance(command, list) and "--hardware-probe-v1" in command):
                return real_run(command, **kwargs)
            calls.append(command)
            if raises is not None:
                raise raises
            return subprocess_module.CompletedProcess(
                command, returncode,
                stdout=json.dumps(PROBE_DOCUMENT) if stdout is None else stdout, stderr="")

        helper = patch("omobench.bridge.helper_path", return_value=COMPANION)
        runner = patch("omobench.ui.subprocess.run", side_effect=fake_run)
        helper.start(); runner.start()
        self.addCleanup(helper.stop); self.addCleanup(runner.stop)
        return calls

    def test_the_probe_runs_the_companion_and_nothing_else(self) -> None:
        calls = self.probe()
        document = self.controller.serving_probe({})
        self.assertEqual(calls, [[COMPANION, "--hardware-probe-v1"]])
        self.assertEqual(document["cpu"], "AMD Ryzen 7 9800X3D")

    def test_the_answer_says_what_it_cannot_establish(self) -> None:
        self.probe()
        note = self.controller.serving_probe({})["note"]
        self.assertIn("Video memory is not among them", note)
        self.assertIn("not evidence that inference used it", note)

    def test_facts_read_on_the_serving_machine_are_recorded_as_detected(self) -> None:
        self.probe()
        self.controller.serving_probe({})
        job_id = self.completed_job(bridged=True)
        self.controller.package(job_id, {**PACKAGE_FORM, "serving": SERVING, "probe": True})
        result = json.loads(
            (self.controller.run_directory(job_id) / "result.json").read_text(encoding="utf-8"))
        host = result["environment"]["serving"]["host"]
        self.assertEqual(host["cpu"], {"value": "AMD Ryzen 7 9800X3D", "provenance": "detected"})
        self.assertEqual(host["os"]["provenance"], "detected")

    def test_a_device_whose_memory_a_person_supplied_is_not_a_pure_detection(self) -> None:
        """The probe found the card; Windows would not say how much VRAM it has."""
        self.probe()
        self.controller.serving_probe({})
        job_id = self.completed_job(bridged=True)
        self.controller.package(job_id, {**PACKAGE_FORM, "serving": SERVING, "probe": True})
        result = json.loads(
            (self.controller.run_directory(job_id) / "result.json").read_text(encoding="utf-8"))
        device = result["environment"]["serving"]["accelerators"][0]
        self.assertEqual(device["memory_gb"], 16.0)
        self.assertEqual(device["provenance"], "contributor-declared")
        self.assertEqual(device["role"], "inference")

    def test_the_panel_uses_the_probe_it_read_rather_than_one_it_was_handed(self) -> None:
        self.probe()
        self.controller.serving_probe({})
        job_id = self.completed_job(bridged=True)
        invented = {"kind": "omobench-serving-hardware-probe-v1", "os": "Invented OS",
                    "cpu": "Invented CPU", "ram_gb": 999.0, "accelerators": []}
        self.controller.package(job_id, {**PACKAGE_FORM, "serving": SERVING, "probe": invented})
        result = json.loads(
            (self.controller.run_directory(job_id) / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["environment"]["serving"]["host"]["cpu"]["value"],
                         "AMD Ryzen 7 9800X3D")
        self.assertNotEqual(result["hardware"]["ram_gb"], 999.0)

    def test_asking_for_a_probe_before_reading_one_is_refused(self) -> None:
        job_id = self.completed_job(bridged=True)
        with self.assertRaises(UIError) as raised:
            self.controller.package(job_id, {**PACKAGE_FORM, "serving": SERVING, "probe": True})
        self.assertIn("No serving hardware has been read", str(raised.exception))

    def test_a_companion_that_is_not_there_is_reported_not_guessed_around(self) -> None:
        helper = patch("omobench.bridge.helper_path",
                       side_effect=ValueError("Open this panel through the updated companion."))
        helper.start(); self.addCleanup(helper.stop)
        with self.assertRaises(UIError):
            self.controller.serving_probe({})

    def test_a_failing_probe_sends_the_contributor_to_the_form(self) -> None:
        self.probe(returncode=1)
        with self.assertRaises(UIError) as raised:
            self.controller.serving_probe({})
        self.assertIn("Enter the serving machine's details yourself", str(raised.exception))

    def test_a_document_this_panel_does_not_recognise_is_refused(self) -> None:
        self.probe(stdout=json.dumps({"kind": "something-else"}))
        with self.assertRaises(UIError):
            self.controller.serving_probe({})

    def test_output_that_is_not_json_is_refused(self) -> None:
        self.probe(stdout="not json at all")
        with self.assertRaises(UIError):
            self.controller.serving_probe({})

    def test_a_companion_that_does_not_answer_is_reported(self) -> None:
        import subprocess as subprocess_module
        self.probe(raises=subprocess_module.TimeoutExpired("companion", 60))
        with self.assertRaises(UIError) as raised:
            self.controller.serving_probe({})
        self.assertIn("did not answer", str(raised.exception))

    def test_the_probe_does_not_start_while_another_action_holds_the_panel(self) -> None:
        self.probe()
        with self.controller.lock:
            self.controller.busy = True
        try:
            with self.assertRaises(UIError):
                self.controller.serving_probe({})
        finally:
            with self.controller.lock:
                self.controller.busy = False

class CompletionAndRecoveryTests(PanelFixture):
    """Six states, and getting back on the path after something fails.

    A benchmark is expensive and its evidence is the only part that cannot be
    produced again cheaply. Everything after it -- packaging, validating,
    submitting -- has to be retryable without touching that evidence, and none
    of it may report success before something confirms it.
    """

    def completion(self, job_id: str, **data) -> dict:
        report = self.controller.publication_requirements(job_id, {**PACKAGE_FORM, **data})
        return {step["id"]: step for step in report["completion"]}

    def evidence_hashes(self, job_id: str) -> dict:
        from omobench.cli import sha256_file
        run = self.controller.run_directory(job_id)
        return {path.name: sha256_file(path) for path in sorted(run.iterdir())
                if path.name != "result.json" and path.is_file()}

    def test_a_finished_benchmark_is_not_a_validated_bundle(self) -> None:
        job_id = self.completed_job(bridged=False)
        states = self.completion(job_id)
        self.assertEqual(states["benchmark"]["state"], "done")
        self.assertEqual(states["evidence"]["state"], "done")
        self.assertEqual(states["package"]["state"], "pending")
        self.assertEqual(states["submitted"]["state"], "pending")

    def test_publication_is_never_reported_as_done(self) -> None:
        job_id = self.completed_job(bridged=False)
        self.controller.package(job_id, dict(PACKAGE_FORM, framework="vllm",
                                             framework_version="0.11.0"))
        with self.controller.lock:
            self.controller.jobs[job_id]["submission"] = {
                "pull_request_url": "https://github.com/Xatiiv/omobench/pull/99",
                "pull_request_state": "merged"}
        states = self.completion(job_id)
        self.assertEqual(states["submitted"]["state"], "done")
        self.assertEqual(states["published"]["state"], "pending")
        self.assertIn("does not observe it", states["published"]["detail"])

    def test_a_refused_bundle_keeps_every_byte_of_the_evidence(self) -> None:
        """Packaging is a separate step and failing it costs no measurements."""
        job_id = self.completed_job(bridged=False)
        before = self.evidence_hashes(job_id)
        # A mutable revision: the registry refuses it, the benchmark is untouched.
        self.controller.package(job_id, dict(PACKAGE_FORM, framework="vllm",
                                             framework_version="0.11.0",
                                             model_revision="main"))
        states = self.completion(job_id, model_revision="main")
        self.assertEqual(states["package"]["state"], "failed")
        self.assertEqual(states["benchmark"]["state"], "done")
        self.assertEqual(states["evidence"]["state"], "done")
        self.assertEqual(self.evidence_hashes(job_id), before)

    def test_a_refused_bundle_can_be_packaged_again_without_rerunning(self) -> None:
        job_id = self.completed_job(bridged=False)
        before = self.evidence_hashes(job_id)
        self.controller.package(job_id, dict(PACKAGE_FORM, framework="vllm",
                                             framework_version="0.11.0",
                                             model_revision="main"))
        detail = self.controller.package(job_id, dict(PACKAGE_FORM, framework="vllm",
                                                      framework_version="0.11.0"))
        self.assertEqual(detail["validation"]["errors"], [])
        self.assertEqual(self.completion(job_id)["package"]["state"], "done")
        self.assertEqual(self.evidence_hashes(job_id), before)

    def test_a_submission_that_started_and_produced_no_pull_request_offers_a_retry(self) -> None:
        job_id = self.completed_job(bridged=False)
        self.controller.package(job_id, dict(PACKAGE_FORM, framework="vllm",
                                             framework_version="0.11.0"))
        with self.controller.lock:
            self.controller.jobs[job_id]["submission"] = {"started": True}
        step = self.completion(job_id)["submitted"]
        self.assertEqual(step["state"], "failed")
        self.assertIn("without re-running anything", step["detail"])
        # And the bundle is still there to submit.
        self.assertEqual(self.completion(job_id)["package"]["state"], "done")

    def test_a_bundle_the_registry_will_refuse_is_not_called_ready(self) -> None:
        job_id = self.completed_job(bridged=False)
        self.controller.package(job_id, dict(PACKAGE_FORM, framework="vllm",
                                             framework_version="0.11.0"))
        # Valid bundle, no submitter entered: ready is not yet true.
        states = self.completion(job_id, submitter="")
        self.assertEqual(states["package"]["state"], "done")
        self.assertEqual(states["ready"]["state"], "blocked")
        self.assertIn("registry still needs something", states["ready"]["detail"])

    def test_the_preview_lists_exactly_the_files_that_would_be_shared(self) -> None:
        from omobench.ui_submission import bundle_snapshot
        job_id = self.completed_job(bridged=False)
        self.controller.package(job_id, dict(PACKAGE_FORM, framework="vllm",
                                             framework_version="0.11.0"))
        _, contents, _ = bundle_snapshot(self.root, self.controller.run_directory(job_id))
        self.assertEqual(sorted(contents), [
            "environment.json", "guidellm-benchmarks.json.gz", "guidellm.stdout.log",
            "reproduce.md", "result.json"])

    def test_an_evidence_summary_is_available_once_the_run_is_packaged(self) -> None:
        job_id = self.completed_job(bridged=False)
        with self.assertRaises(UIError):
            self.controller.evidence_summary(job_id)
        self.controller.package(job_id, dict(PACKAGE_FORM, framework="vllm",
                                             framework_version="0.11.0"))
        summary = self.controller.evidence_summary(job_id)
        self.assertEqual([section["id"] for section in summary["sections"]],
                         ["environments", "accelerators", "model", "runtime", "tokenizer",
                          "transport"])
        self.assertTrue(summary["qualifications"])

    def test_an_uncatalogued_model_never_stops_the_evidence_being_packaged(self) -> None:
        """The decision is somebody else's; the bundle does not wait for it."""
        job_id = self.completed_job(bridged=False)
        form = dict(PACKAGE_FORM, framework="vllm", framework_version="0.11.0",
                    model="acme/private-model-v9")
        report = self.controller.publication_requirements(job_id, form)
        self.assertEqual(report["blocks_packaging"], [])
        self.assertIn("Canonical model ID is in the OmoBench catalog",
                      report["blocks_submission"])
        detail = self.controller.package(job_id, form)
        # The bundle exists; the registry refuses it, which is a later state.
        self.assertFalse(detail["validation"]["valid"])
        states = self.completion(job_id, **form)
        self.assertEqual(states["package"]["state"], "done")
        self.assertEqual(states["ready"]["state"], "blocked")
        self.assertIn("does not affect the measurements",
                      states["ready"]["detail"].replace("Nothing about that affects",
                                                        "does not affect"))

    def test_a_cancelled_run_is_not_reported_as_a_completed_benchmark(self) -> None:
        job_id = self.completed_job(bridged=False)
        with self.controller.lock:
            job = self.controller.jobs[job_id]
            job["status"] = "cancelled"
            job["summary"] = None
        states = self.completion(job_id)
        self.assertEqual(states["benchmark"]["state"], "failed")
        self.assertIn("No measurements were produced", states["benchmark"]["detail"])

if __name__ == "__main__":
    unittest.main()
