"""Saving a local result and preparing a public submission are separate things.

A benchmark that finished is a real measurement on this machine, whatever the
registry thinks of its model. Exporting it must not demand publication metadata
the contributor does not have -- an immutable weights revision, an exact runtime
build, a catalogued model id -- and must not invent any of it either.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from omobench.ui import Controller, UIError


ROOT = Path(__file__).resolve().parents[2]
UNCATALOGUED = "Qwen/Qwen3-VL-4B-Instruct"
ALIAS = "qwen/qwen3-vl-4b:3"
SUMMARY = {
    "runner_version": "0.7.3",
    "started_at": "2026-10-29T10:00:00+00:00",
    "results": {
        "latency_ms": {"ttft": {"p50": 1200.0, "p95": 2400.0}, "inter_token": {"p50": 90.0}},
        "output_tokens_per_second": 11.5, "total_tokens_per_second": 40.0,
        "requests_per_second": 0.2, "requests": {"succeeded": 48, "attempted": 48},
        "error_rate_percent": 0.0,
    },
}
SECRET = "hf_FIXTURETOKENvalue1234567890"


class LocalExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for relative in ("benchmarks", "catalog", "schema", "tools"):
            shutil.copytree(ROOT / relative, self.root / relative, ignore=shutil.ignore_patterns("__pycache__"))
        self.controller = Controller(self.root)
        self.addCleanup(self.controller.close)

    def job(self, controller=None, **changes):
        """A clearly synthetic completed run against a server the contributor
        already had running, with the identities OmoBench itself observed."""
        controller = controller or self.controller
        job = {
            "id": "job-" + "ab" * 8, "created_at": "2026-10-29T10:00:00+00:00",
            "finished_at": "2026-10-29T10:20:00+00:00", "status": "completed",
            "model": ALIAS, "profile": "chat-small-v1", "execution_device": "cpu",
            "tokenizer": UNCATALOGUED, "summary": SUMMARY, "error": None,
            "validation": None, "submission": None,
        }
        job.update(changes)
        directory = controller._directory(job["id"])
        directory.mkdir(exist_ok=True)
        # Evidence that must never be swept into an export.
        evidence = directory / "run-fixturerun01"
        evidence.mkdir(exist_ok=True)
        (directory / "cli.log").write_text(f"Authorization: Bearer {SECRET}\n", encoding="utf-8")
        (evidence / "environment.json").write_text(json.dumps({"api_key": SECRET}), encoding="utf-8")
        controller.jobs[job["id"]] = job
        controller._save(job)
        return job

    # -- A: local export -------------------------------------------------------

    def test_a_completed_uncatalogued_run_with_no_revision_still_exports(self):
        job = self.job()
        report = self.controller.local_report(job["id"])
        self.assertEqual(report["kind"], "omobench-local-report")
        self.assertEqual(report["measurements"], SUMMARY["results"])
        self.assertEqual(report["server"]["model_identifier"], ALIAS)
        self.assertEqual(report["run"]["profile_id"], "chat-small-v1")
        self.assertEqual(report["run"]["runner_version"], "0.7.3")

    def test_unknown_provenance_is_preserved_as_unknown_not_invented(self):
        report = self.controller.local_report(self.job()["id"])
        declared = report["recorded_provenance"]["declared_or_prefilled"]
        self.assertEqual(declared["model_revision"], "unknown")
        self.assertEqual(declared["runtime_version"], "unknown")
        self.assertEqual(declared["quantization"], "unknown")
        self.assertEqual(declared["runtime_arguments"], [])

    def test_known_provenance_is_carried_through_unchanged(self):
        job = self.job(model_revision="a" * 40, framework="llama.cpp",
                       framework_version="b10621", quantization="Q4_K_M",
                       runtime_args=["--ctx-size 2048"], model_source="https://example.invalid/weights")
        declared = self.controller.local_report(job["id"])["recorded_provenance"]["declared_or_prefilled"]
        self.assertEqual(declared["model_revision"], "a" * 40)
        self.assertEqual(declared["runtime_framework"], "llama.cpp")
        self.assertEqual(declared["runtime_version"], "b10621")
        self.assertEqual(declared["runtime_arguments"], ["--ctx-size 2048"])

    def test_the_two_identities_stay_distinct_in_the_export(self):
        report = self.controller.local_report(self.job()["id"])
        self.assertEqual(report["server"]["model_identifier"], ALIAS)
        self.assertEqual(report["workload"]["tokenizer_source"], UNCATALOGUED)
        self.assertTrue(report["workload"]["tokenizer_source_explicit"])

    def test_without_an_override_the_alias_is_named_as_the_tokenizer_source_too(self):
        report = self.controller.local_report(self.job(tokenizer=None)["id"])
        self.assertEqual(report["workload"]["tokenizer_source"], ALIAS)
        self.assertFalse(report["workload"]["tokenizer_source_explicit"])

    def test_the_cpu_declaration_is_still_cpu_in_the_export(self):
        report = self.controller.local_report(self.job()["id"])
        self.assertEqual(report["server"]["execution_device"], "cpu")
        self.assertIn("declared", report["server"]["execution_device_source"])

    def test_the_export_carries_no_log_environment_or_credential(self):
        serialized = json.dumps(self.controller.local_report(self.job()["id"]))
        self.assertNotIn(SECRET, serialized)
        self.assertNotIn("Authorization", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("127.0.0.1", serialized)

    def test_the_export_says_it_is_not_a_public_result(self):
        report = self.controller.local_report(self.job()["id"])
        self.assertIn("not an OmoBench", report["note"])
        self.assertIn("result.json", report["note"])
        self.assertFalse(report["publication"]["catalogued"])

    def test_an_unfinished_run_has_nothing_to_export(self):
        job = self.job(status="failed", summary=None)
        with self.assertRaises(UIError):
            self.controller.local_report(job["id"])

    def test_an_old_run_exports_after_a_restart_without_rerunning_or_rewriting(self):
        job = self.job()
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in self.root.rglob("*") if path.is_file()}
        restarted = Controller(self.root)  # the panel, started again over the same checkout
        self.addCleanup(restarted.close)
        report = restarted.local_report(job["id"])
        self.assertEqual(report["measurements"], SUMMARY["results"])
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns)
                                  for path in self.root.rglob("*") if path.is_file()})

    def test_the_export_separates_workload_model_and_execution_context(self):
        report = self.controller.local_report(self.job(quantization="Q4_K_M")["id"])
        context = report["comparability"]
        self.assertEqual(context["workload_definition"]["profile_id"], "chat-small-v1")
        self.assertRegex(context["workload_definition"]["profile_sha256"], r"^sha256:[a-f0-9]{64}$")
        self.assertIn("/v1/chat/completions", context["workload_definition"]["request_format"])
        self.assertEqual(context["model_configuration"]["server_model_identifier"], ALIAS)
        self.assertEqual(context["model_configuration"]["quantization"], "Q4_K_M")
        self.assertEqual(context["model_configuration"]["tokenizer_source"], UNCATALOGUED)
        self.assertEqual(context["execution_configuration"]["execution_device_declared"], "cpu")

    def test_hardware_and_device_are_a_comparison_dimension_not_a_disqualifier(self):
        context = self.controller.local_report(self.job()["id"])["comparability"]
        dimensions = " ".join(context["intended_comparison_dimensions"])
        self.assertIn("CPU against GPU", dimensions)
        self.assertIn("controlled comparison, not a", dimensions)
        # The workload definition is what must match; execution is the subject.
        requires = " ".join(context["controlled_comparison_requires"])
        self.assertIn("workload_definition", requires)
        self.assertNotIn("hardware", requires)

    def test_equal_token_counts_under_different_tokenizers_are_not_claimed_equal(self):
        notes = " ".join(self.controller.local_report(self.job()["id"])["comparability"]["notes"])
        self.assertIn("not necessarily processed", notes)
        self.assertIn("same amount of work", notes)
        self.assertIn("Python dependencies", notes)

    def test_unknown_provenance_leaves_comparability_unestablished_not_forbidden(self):
        context = self.controller.local_report(self.job()["id"])["comparability"]
        self.assertFalse(context["comparability_established"])
        self.assertIn("model_revision", context["unknown_fields"])
        self.assertIn("serving_runtime", context["unknown_fields"])
        notes = " ".join(context["notes"])
        self.assertIn("neither certify a comparison nor forbid one", notes)

    def test_full_provenance_establishes_comparability(self):
        job = self.job(model_revision="a" * 40, framework="llama.cpp",
                       framework_version="b10621", quantization="Q4_K_M",
                       canonical_model="Qwen/Qwen3-4B")
        context = self.controller.local_report(job["id"])["comparability"]
        self.assertEqual(context["unknown_fields"], [])
        self.assertTrue(context["comparability_established"])

    def test_comparison_context_is_not_publication_eligibility(self):
        job = self.job(model_revision="a" * 40, framework="llama.cpp",
                       framework_version="b10621", quantization="Q4_K_M",
                       canonical_model="Qwen/Qwen3-4B")
        report = self.controller.local_report(job["id"])
        self.assertTrue(report["comparability"]["comparability_established"])
        # Still uncatalogued, so still not publishable.
        self.assertFalse(report["publication"]["catalogued"])
        self.assertFalse(report["publication"]["ready"])
        notes = " ".join(report["comparability"]["notes"])
        self.assertIn("not publication eligibility", notes)

    def test_the_workload_definition_carries_the_profiles_real_phase_contract(self):
        context = self.controller.local_report(self.job()["id"])["comparability"]
        contract = context["workload_definition"]["phase_contract"]
        self.assertEqual(contract["phase_basis"], "duration")
        self.assertFalse(contract["declaration_matches_execution"])

    # -- B: public submission --------------------------------------------------

    def test_the_same_run_is_blocked_from_public_submission(self):
        report = self.controller.publication_requirements(self.job()["id"])
        self.assertFalse(report["ready"])
        self.assertTrue(report["local_export_available"])

    def test_every_missing_requirement_is_listed_together(self):
        items = self.controller.publication_requirements(self.job()["id"])["items"]
        failing = {item["label"] for item in items if item["ok"] is False}
        self.assertIn("Canonical model ID is in the OmoBench catalog", failing)
        self.assertIn("Immutable revision of the actual weights source", failing)
        self.assertIn("Exact serving runtime version", failing)
        self.assertGreaterEqual(len(failing), 3)  # all at once, not one popup at a time

    def test_an_uncatalogued_model_is_explained_and_never_relabelled(self):
        report = self.controller.publication_requirements(self.job()["id"], {"model": UNCATALOGUED})
        catalog_item = next(item for item in report["items"] if item["field"] == "model")
        self.assertFalse(catalog_item["ok"])
        self.assertIn("not currently eligible for the community registry", catalog_item["detail"])
        self.assertIn("Qwen/Qwen3-4B", catalog_item["detail"])  # shown as the catalog, not as a substitute
        self.assertEqual(report["model"], UNCATALOGUED)
        self.assertNotIn(UNCATALOGUED, report["catalog"])

    def test_a_moving_revision_is_not_an_immutable_one(self):
        for revision in ("main", "master", "latest", "HEAD"):
            with self.subTest(revision=revision):
                report = self.controller.publication_requirements(
                    self.job()["id"], {"model_revision": revision})
                item = next(entry for entry in report["items"] if entry["field"] == "model_revision")
                self.assertFalse(item["ok"])
                self.assertIn("immutable", item["detail"])

    def test_the_revision_requirement_names_what_is_not_a_substitute(self):
        item = next(entry for entry in self.controller.publication_requirements(self.job()["id"])["items"]
                    if entry["field"] == "model_revision")
        self.assertIn("SHA-256 checksum", item["detail"])
        self.assertIn("OmoBench source commit", item["detail"])
        self.assertIn("quantization repository", item["detail"])

    def test_the_runtime_requirement_separates_application_from_runtime_version(self):
        item = next(entry for entry in self.controller.publication_requirements(self.job()["id"])["items"]
                    if entry["field"] == "framework_version")
        self.assertIn("LM Studio", item["detail"])
        self.assertIn("do not guess", item["detail"])

    def test_a_complete_catalogued_run_reports_ready(self):
        job = self.job(model="Qwen/Qwen3-4B", model_revision="c" * 40,
                       framework="llama.cpp", framework_version="b10621")
        report = self.controller.publication_requirements(job["id"], {"submitter": "fixture-user"})
        self.assertTrue(report["ready"], report["items"])

    def test_acceptance_failure_still_blocks_even_with_every_field_filled(self):
        job = self.job(
            model="Qwen/Qwen3-4B", model_revision="c" * 40, framework="llama.cpp",
            framework_version="b10621",
            validation={"valid": False, "errors": [
                "results/x/result.json: acceptance: 29 successful requests is below the minimum"]},
        )
        report = self.controller.publication_requirements(job["id"], {"submitter": "fixture-user"})
        acceptance = next(item for item in report["items"] if item["label"] == "Profile acceptance")
        self.assertFalse(acceptance["ok"])
        self.assertIn("29 successful requests", acceptance["detail"])
        self.assertFalse(report["ready"])

    def test_a_submitter_that_was_never_entered_is_unchecked_not_satisfied(self):
        item = next(entry for entry in self.controller.publication_requirements(self.job()["id"])["items"]
                    if entry["field"] == "submitter")
        self.assertIsNone(item["ok"])
        blank = next(entry for entry in self.controller.publication_requirements(
            self.job()["id"], {"submitter": "  "})["items"] if entry["field"] == "submitter")
        self.assertFalse(blank["ok"])
        self.assertIn("Enter the GitHub username", blank["detail"])

    def test_acceptance_is_reported_as_unchecked_before_packaging(self):
        acceptance = next(item for item in self.controller.publication_requirements(self.job()["id"])["items"]
                          if item["label"] == "Profile acceptance")
        self.assertIsNone(acceptance["ok"])
        self.assertIn("Checked when you package", acceptance["detail"])

    def test_checking_requirements_never_changes_the_run(self):
        job = self.job()
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in self.root.rglob("*") if path.is_file()}
        self.controller.publication_requirements(job["id"], {"model_revision": "d" * 40})
        self.controller.local_report(job["id"])
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns)
                                  for path in self.root.rglob("*") if path.is_file()})

    def test_the_catalog_is_read_from_the_repository_not_hard_coded(self):
        self.assertEqual(self.controller.catalog_models(), self.controller.setup()["catalog"])
        self.assertIn("Qwen/Qwen3-4B", self.controller.catalog_models())


if __name__ == "__main__":
    unittest.main()
