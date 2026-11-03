"""Packaging a run whose server and runner were not the same environment.

The case this exists for: a model served by LM Studio on Windows, measured by a
GuideLLM runner inside WSL2, over the explicit loopback bridge. That run used
to be unpublishable -- not because anything was wrong with it, but because
schema 1.0 has one hardware host and no way to say which of the two it is.

Everything here uses synthetic evidence. Nothing runs a model.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import provenance_fixtures as fixtures
from omobench import model_identity, serving_metadata
from omobench.cli import package_local_run, repackage_local_run, sha256_file
from test_cli import base_package_args, copy_repo_tree_for_registry_validation


class PackagingFixture(unittest.TestCase):
    """A temporary checkout with the real schema, catalog, profiles and validator."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_repo_tree_for_registry_validation(self.root)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)

    def declaration(self, document: dict) -> str:
        return fixtures.write_declaration(self.root / "provenance.json", document)

    def package(self, run: Path, **overrides) -> tuple[int, dict | None]:
        defaults = dict(framework="lmstudio", framework_version="b10621",
                        quantization="Q4_K_M", runtime_arg=[])
        defaults.update(overrides)
        code = package_local_run(base_package_args(run, **defaults))
        path = run / "result.json"
        return code, json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def validate(self, run: Path) -> list[str]:
        from omobench.ui_submission import validate_bundle
        return validate_bundle(self.root, run)


class BridgedPackagingTests(PackagingFixture):
    def test_a_bridged_run_packages_with_a_complete_declaration(self) -> None:
        run = fixtures.bridged_run(self.root)
        code, result = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()))
        self.assertEqual(code, 0)
        self.assertEqual(result["schema_version"], "1.1")
        self.assertEqual(result["environment"]["relationship"], "windows-host-wsl-runner")
        self.assertEqual(result["environment"]["transport"]["kind"], "windows-wsl-stdio-bridge")
        self.assertEqual(result["environment"]["transport"]["overhead"], "included-not-measured")
        self.assertEqual(self.validate(run), [])

    def test_the_serving_host_is_not_the_wsl_runner(self) -> None:
        """The bug this whole schema exists to prevent."""
        run = fixtures.bridged_run(self.root)
        _, result = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()))
        self.assertEqual(result["hardware"]["os"], "Windows 11 Pro 10.0.26200")
        self.assertEqual(result["hardware"]["cpu"], "AMD Ryzen 7 9800X3D")
        self.assertIn("WSL2", result["environment"]["runner"]["os"])
        self.assertEqual(result["environment"]["runner"]["kind"], "wsl2")
        self.assertNotEqual(result["hardware"]["cpu"], result["environment"]["runner"]["cpu"])

    def test_declared_serving_hardware_is_never_recorded_as_detected(self) -> None:
        run = fixtures.bridged_run(self.root)
        _, result = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()))
        host = result["environment"]["serving"]["host"]
        for field in ("os", "cpu", "ram_gb"):
            self.assertEqual(host[field]["provenance"], "contributor-declared")
        self.assertEqual(result["hardware"]["detection"]["hardware"], "user-confirmed")

    def test_a_bridged_run_without_a_declaration_is_refused_with_the_remedy(self) -> None:
        run = fixtures.bridged_run(self.root)
        code, result = self.package(run)
        self.assertEqual(code, 2)
        self.assertIsNone(result)

    def test_the_refusal_names_what_is_needed_rather_than_calling_it_local_only(self) -> None:
        run = fixtures.bridged_run(self.root)
        import io
        from contextlib import redirect_stderr
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.package(run)
        message = stderr.getvalue()
        self.assertIn("--provenance", message)
        self.assertIn("serving machine", message)
        self.assertNotIn("local-only", message)

    def test_a_direct_run_cannot_be_declared_as_a_bridged_one(self) -> None:
        run = fixtures.single_host_run(self.root)
        code, _ = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()),
            framework="vllm", framework_version="0.11.0")
        self.assertEqual(code, 2)

    def test_a_bridged_run_cannot_be_declared_as_a_single_host(self) -> None:
        run = fixtures.bridged_run(self.root)
        code, _ = self.package(
            run, provenance=self.declaration(fixtures.single_host_declaration()))
        self.assertEqual(code, 2)

    def test_machine_overrides_are_refused_when_the_serving_host_is_declared(self) -> None:
        """Two places to put the serving CPU is one place too many."""
        run = fixtures.bridged_run(self.root)
        code, _ = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()),
            cpu="Something Else")
        self.assertEqual(code, 2)


class UnchangedBehaviourTests(PackagingFixture):
    def test_a_direct_run_without_a_declaration_still_packages_as_1_0(self) -> None:
        run = fixtures.single_host_run(self.root)
        code, result = self.package(run, framework="vllm", framework_version="0.11.0")
        self.assertEqual(code, 0)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertNotIn("environment", result)
        self.assertNotIn("served_model_id", result["model"])
        self.assertEqual(self.validate(run), [])

    def test_the_1_0_fingerprint_is_byte_for_byte_what_it_always_was(self) -> None:
        """Adding 1.1 must not move an existing configuration's fingerprint."""
        run = fixtures.single_host_run(self.root)
        _, result = self.package(run, framework="vllm", framework_version="0.11.0")
        from omobench.cli import sha256_json
        expected = sha256_json({
            "model": {"id": "Qwen/Qwen3-4B", "revision": "abc123", "quantization": "Q4_K_M"},
            "runtime": {"framework": "vllm", "version": "0.11.0", "arguments": [],
                        "endpoint_type": "openai-compatible"},
            "hardware": {k: result["hardware"][k]
                         for k in ("os", "cpu", "ram_gb", "gpus", "driver_version")},
            "benchmark": {"profile_id": result["benchmark"]["profile_id"],
                          "profile_sha256": result["benchmark"]["profile_sha256"]},
        })
        self.assertEqual(result["configuration_fingerprint"], expected)

    def test_a_declaration_changes_nothing_about_the_saved_evidence(self) -> None:
        run = fixtures.bridged_run(self.root)
        before = {name: sha256_file(run / name) for name in
                  ("guidellm-benchmarks.json.gz", "guidellm.stdout.log",
                   "environment.json", "reproduce.md")}
        self.package(run, provenance=self.declaration(fixtures.bridged_declaration()))
        after = {name: sha256_file(run / name) for name in before}
        self.assertEqual(before, after)

    def test_the_single_host_route_records_its_own_snapshot_as_detected(self) -> None:
        """One machine's run-time snapshot really is a reading of the server."""
        run = fixtures.single_host_run(self.root)
        _, result = self.package(
            run, provenance=self.declaration(fixtures.single_host_declaration()),
            framework="vllm", framework_version="0.11.0")
        host = result["environment"]["serving"]["host"]
        self.assertEqual(host["cpu"]["provenance"], "detected")
        self.assertEqual(result["environment"]["transport"]["kind"], "direct")
        self.assertEqual(result["environment"]["transport"]["overhead"], "none-direct")
        self.assertEqual(self.validate(run), [])


class HardwareShapeTests(PackagingFixture):
    def package_serving(self, serving: dict, **overrides):
        run = fixtures.bridged_run(self.root)
        declaration = fixtures.bridged_declaration()
        declaration["serving"].update(serving)
        return run, self.package(
            run, provenance=self.declaration(declaration), **overrides)

    def test_a_cpu_only_serving_host_records_no_inference_accelerator(self) -> None:
        run, (code, result) = self.package_serving(
            {"accelerators": [], "placement": {"mode": "cpu-only"}},
            execution_device="cpu")
        self.assertEqual(code, 0)
        self.assertEqual(result["hardware"]["gpus"], [])
        self.assertEqual(result["environment"]["serving"]["placement"]["mode"], "cpu-only")
        self.assertEqual(self.validate(run), [])

    def test_an_idle_gpu_on_a_cpu_run_is_recorded_as_present_not_used(self) -> None:
        run, (code, result) = self.package_serving(
            {"accelerators": [{"vendor": "amd", "model": "AMD Radeon RX 9070",
                               "memory_gb": 16.0, "count": 1, "role": "present-not-used"}],
             "placement": {"mode": "cpu-only"}},
            execution_device="cpu")
        self.assertEqual(code, 0)
        self.assertEqual(result["hardware"]["gpus"], [])
        self.assertEqual(result["environment"]["serving"]["accelerators"][0]["role"],
                         "present-not-used")
        self.assertEqual(self.validate(run), [])

    def test_a_cpu_run_cannot_claim_a_gpu_performed_inference(self) -> None:
        _, (code, _) = self.package_serving({"placement": {"mode": "cpu-only"}},
                                            execution_device="cpu")
        self.assertEqual(code, 2)

    def test_multiple_identical_gpus_collapse_into_one_counted_entry(self) -> None:
        run, (code, result) = self.package_serving(
            {"accelerators": [
                {"vendor": "nvidia", "model": "NVIDIA RTX 4090", "memory_gb": 24.0,
                 "count": 2, "role": "inference"}],
             "placement": {"mode": "gpu-only"}})
        self.assertEqual(code, 0)
        self.assertEqual(result["hardware"]["gpus"],
                         [{"vendor": "nvidia", "model": "NVIDIA RTX 4090",
                           "memory_gb": 24.0, "count": 2}])
        self.assertEqual(self.validate(run), [])

    def test_different_gpus_stay_separate_entries(self) -> None:
        run, (code, result) = self.package_serving(
            {"accelerators": [
                {"vendor": "nvidia", "model": "NVIDIA RTX 4090", "memory_gb": 24.0,
                 "count": 1, "role": "inference"},
                {"vendor": "nvidia", "model": "NVIDIA RTX 3090", "memory_gb": 24.0,
                 "count": 1, "role": "inference"}],
             "placement": {"mode": "gpu-only"}})
        self.assertEqual(code, 0)
        self.assertEqual([gpu["model"] for gpu in result["hardware"]["gpus"]],
                         ["NVIDIA RTX 3090", "NVIDIA RTX 4090"])
        self.assertEqual(self.validate(run), [])

    def test_mixed_cpu_gpu_offload_records_how_the_weights_were_split(self) -> None:
        run, (code, result) = self.package_serving(
            {"placement": {"mode": "cpu-gpu-offload", "detail": "32 of 49 layers on GPU"}})
        self.assertEqual(code, 0)
        placement = result["environment"]["serving"]["placement"]
        self.assertEqual(placement["mode"], "cpu-gpu-offload")
        self.assertEqual(placement["detail"], "32 of 49 layers on GPU")
        self.assertEqual(self.validate(run), [])

    def test_mixed_offload_without_its_split_is_refused(self) -> None:
        _, (code, _) = self.package_serving({"placement": {"mode": "cpu-gpu-offload"}})
        self.assertEqual(code, 2)

    def test_an_accelerator_with_unknown_memory_is_refused_rather_than_guessed(self) -> None:
        _, (code, _) = self.package_serving(
            {"accelerators": [{"vendor": "amd", "model": "AMD Radeon RX 9070",
                               "memory_gb": None, "count": 1, "role": "inference"}]})
        self.assertEqual(code, 2)

    def test_an_unknown_serving_cpu_is_refused_rather_than_borrowed_from_wsl(self) -> None:
        run = fixtures.bridged_run(self.root)
        declaration = fixtures.bridged_declaration()
        declaration["serving"]["host"].pop("cpu")
        code, _ = self.package(run, provenance=self.declaration(declaration))
        self.assertEqual(code, 2)


class ApplicationAndEngineTests(PackagingFixture):
    def test_the_lm_studio_release_and_the_llama_cpp_build_are_both_kept(self) -> None:
        run = fixtures.bridged_run(self.root)
        _, result = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()))
        runtime = result["environment"]["serving"]["runtime"]
        self.assertEqual(runtime["application"], {"name": "LM Studio", "version": "0.3.31",
                                                  "provenance": "contributor-declared"})
        self.assertEqual(runtime["engine"]["version"], "b10621")
        self.assertEqual(result["runtime"]["version"], "b10621")
        self.assertNotEqual(runtime["application"]["version"], runtime["engine"]["version"])

    def test_an_application_version_recorded_as_the_runtime_version_is_rejected(self) -> None:
        run = fixtures.bridged_run(self.root)
        code, _ = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()),
            framework_version="0.3.31")
        self.assertEqual(code, 0)  # packaging builds it; the registry refuses it
        self.assertTrue(any("inference engine's build" in message
                            for message in self.validate(run)))

    def test_serving_settings_that_change_the_measurement_are_recorded(self) -> None:
        run = fixtures.bridged_run(self.root)
        _, result = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()))
        settings = {entry["name"]: entry["value"]
                    for entry in result["environment"]["serving"]["runtime"]["settings"]}
        self.assertEqual(settings, {"context_length": "8192", "gpu_layers": "49"})


class WeightIdentityPackagingTests(PackagingFixture):
    def test_a_quantization_repository_is_recorded_beside_the_canonical_model(self) -> None:
        run = fixtures.bridged_run(self.root)
        declaration = fixtures.bridged_declaration(weights={
            "kind": "repository",
            "repository": "https://huggingface.co/unsloth/Qwen3-4B-GGUF",
            "repository_revision": "a" * 40})
        code, result = self.package(run, provenance=self.declaration(declaration),
                                    model_revision="a" * 40)
        self.assertEqual(code, 0)
        self.assertEqual(result["model"]["id"], "Qwen/Qwen3-4B")
        self.assertEqual(result["model"]["weights"]["repository"],
                         "https://huggingface.co/unsloth/Qwen3-4B-GGUF")
        self.assertEqual(result["model"]["weights"]["identity"], "repository-revision")
        self.assertEqual(self.validate(run), [])

    def test_local_weights_are_identified_by_hashing_their_actual_bytes(self) -> None:
        folder = self.root / "weights"
        folder.mkdir()
        (folder / "model.gguf").write_bytes(b"synthetic weight bytes")
        manifest = model_identity.weight_manifest([folder / "model.gguf"], root=folder)
        run = fixtures.bridged_run(self.root)
        declaration = fixtures.bridged_declaration(weights={
            "kind": "local-files", "manifest": manifest})
        code, result = self.package(
            run, provenance=self.declaration(declaration),
            model_revision=model_identity.manifest_revision(manifest))
        self.assertEqual(code, 0)
        self.assertEqual(result["model"]["weights"]["identity"], "content-hash")
        self.assertEqual(result["model"]["weights"]["content"]["files"][0]["name"], "model.gguf")
        self.assertEqual(self.validate(run), [])

    def test_a_sharded_weight_set_is_one_identity_over_every_shard(self) -> None:
        folder = self.root / "weights"
        folder.mkdir()
        shards = []
        for index in range(1, 4):
            shard = folder / f"model-0000{index}-of-00003.safetensors"
            shard.write_bytes(f"shard {index}".encode())
            shards.append(shard)
        manifest = model_identity.weight_manifest(shards, root=folder)
        run = fixtures.bridged_run(self.root)
        code, result = self.package(
            run,
            provenance=self.declaration(fixtures.bridged_declaration(
                weights={"kind": "local-files", "manifest": manifest})),
            model_revision=model_identity.manifest_revision(manifest))
        self.assertEqual(code, 0)
        self.assertEqual(len(result["model"]["weights"]["content"]["files"]), 3)
        self.assertEqual(self.validate(run), [])

    def test_a_revision_that_does_not_match_the_manifest_is_refused(self) -> None:
        folder = self.root / "weights"
        folder.mkdir()
        (folder / "model.gguf").write_bytes(b"synthetic weight bytes")
        manifest = model_identity.weight_manifest([folder / "model.gguf"], root=folder)
        run = fixtures.bridged_run(self.root)
        code, _ = self.package(
            run,
            provenance=self.declaration(fixtures.bridged_declaration(
                weights={"kind": "local-files", "manifest": manifest})),
            model_revision="sha256-manifest:" + "0" * 64)
        self.assertEqual(code, 2)

    def test_a_file_checksum_offered_as_a_repository_commit_is_refused(self) -> None:
        run = fixtures.bridged_run(self.root)
        code, _ = self.package(run, provenance=self.declaration(
            fixtures.bridged_declaration(weights={
                "kind": "repository",
                "repository": "https://huggingface.co/unsloth/Qwen3-4B-GGUF",
                "repository_revision": "sha256-manifest:" + "0" * 64})))
        self.assertEqual(code, 2)

    def test_a_weights_revision_disagreeing_with_the_model_revision_is_refused(self) -> None:
        run = fixtures.bridged_run(self.root)
        code, _ = self.package(run, provenance=self.declaration(
            fixtures.bridged_declaration(weights={
                "kind": "repository",
                "repository": "https://huggingface.co/unsloth/Qwen3-4B-GGUF",
                "repository_revision": "b" * 40})), model_revision="a" * 40)
        self.assertEqual(code, 2)


class TokenizerRecordTests(PackagingFixture):
    def test_the_tokenizer_that_counted_the_tokens_is_recorded(self) -> None:
        run = fixtures.bridged_run(self.root)
        _, result = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()))
        tokenizer = result["model"]["tokenizer"]
        self.assertEqual(tokenizer["source"], "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B")
        self.assertEqual(tokenizer["loaded"], "loaded-before-run")
        self.assertEqual(tokenizer["identity"]["vocabulary_size"], 152064)

    def test_a_successful_load_is_never_recorded_as_a_match(self) -> None:
        run = fixtures.bridged_run(self.root)
        _, result = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()))
        self.assertEqual(result["model"]["tokenizer"]["matches_served_weights"],
                         "not-verifiable-here")

    def test_no_override_records_the_served_identifier_as_a_default(self) -> None:
        run = fixtures.bridged_run(self.root)
        declaration = fixtures.bridged_declaration()
        declaration.pop("tokenizer")
        _, result = self.package(run, provenance=self.declaration(declaration))
        tokenizer = result["model"]["tokenizer"]
        self.assertEqual(tokenizer["kind"], "served-model-default")
        self.assertEqual(tokenizer["source"], "deepseek-r1-distill-qwen-14b")
        self.assertEqual(tokenizer["loaded"], "not-checked")


class ModelProposalPackagingTests(PackagingFixture):
    PROPOSAL = {
        "family": "DeepSeek",
        "parameter_class": "14B",
        "source": "https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-99B",
        "licence": "MIT",
        "weights_publicly_downloadable": True,
        "rationale": "Measured on an AMD desktop; no catalog entry exists yet.",
        "initial_runtimes": ["lmstudio"],
    }

    def test_an_uncatalogued_model_can_be_proposed_with_its_result(self) -> None:
        run = fixtures.bridged_run(self.root)
        declaration = fixtures.bridged_declaration(
            catalog_status="proposed", proposal=self.PROPOSAL)
        code, result = self.package(run, provenance=self.declaration(declaration),
                                    model="deepseek-ai/DeepSeek-R1-Distill-Qwen-99B")
        self.assertEqual(code, 0)
        self.assertEqual(result["model"]["catalog_status"], "proposed")
        proposal = json.loads((run / "model-proposal.json").read_text(encoding="utf-8"))
        self.assertEqual(proposal["status"], "proposed")
        self.assertEqual(proposal["proposed_id"], "deepseek-ai/DeepSeek-R1-Distill-Qwen-99B")
        self.assertEqual(proposal["for_run_id"], result["run_id"])
        self.assertEqual(proposal["proposed_by"], result["submission"]["submitter"])

    def test_an_uncatalogued_model_without_a_proposal_is_refused(self) -> None:
        run = fixtures.bridged_run(self.root)
        code, _ = self.package(
            run, provenance=self.declaration(fixtures.bridged_declaration()),
            model="deepseek-ai/DeepSeek-R1-Distill-Qwen-99B")
        self.assertEqual(code, 2)

    def test_a_proposal_for_an_already_catalogued_model_is_refused(self) -> None:
        run = fixtures.bridged_run(self.root)
        code, _ = self.package(run, provenance=self.declaration(
            fixtures.bridged_declaration(catalog_status="proposed", proposal=self.PROPOSAL)))
        self.assertEqual(code, 2)

    def test_a_proposal_cannot_be_made_for_weights_that_are_not_public(self) -> None:
        run = fixtures.bridged_run(self.root)
        proposal = {**self.PROPOSAL, "weights_publicly_downloadable": False}
        code, _ = self.package(run, provenance=self.declaration(
            fixtures.bridged_declaration(catalog_status="proposed", proposal=proposal)),
            model="deepseek-ai/DeepSeek-R1-Distill-Qwen-99B")
        self.assertEqual(code, 2)

    def test_a_proposal_is_removed_when_a_repackage_no_longer_makes_one(self) -> None:
        run = fixtures.bridged_run(self.root)
        self.package(run, provenance=self.declaration(fixtures.bridged_declaration(
            catalog_status="proposed", proposal=self.PROPOSAL)),
            model="deepseek-ai/DeepSeek-R1-Distill-Qwen-99B")
        self.assertTrue((run / "model-proposal.json").is_file())
        self.package(run, provenance=self.declaration(fixtures.bridged_declaration()),
                     force=True)
        self.assertFalse((run / "model-proposal.json").exists())


class RepackagingTests(PackagingFixture):
    def repackage(self, run: Path, declaration: dict, **overrides) -> tuple[int, Path]:
        destination = self.root / ".omobench-runs" / "run-fixture-1234-repackaged"
        arguments = dict(run_dir=str(run), provenance=self.declaration(declaration),
                         output_dir=str(destination), submitter=None, force=False)
        arguments.update(overrides)
        return repackage_local_run(argparse.Namespace(**arguments)), destination

    @staticmethod
    def result_section(**overrides) -> dict:
        section = {"model": "Qwen/Qwen3-4B", "model_revision": "abc123",
                   "quantization": "Q4_K_M", "framework": "lmstudio",
                   "framework_version": "b10621", "execution_device": "gpu",
                   "submitter": "local-tester", "profile": "chat-small-v1",
                   "runtime_args": []}
        section.update(overrides)
        return section

    def test_a_finished_bridged_run_is_repackaged_without_rerunning_it(self) -> None:
        run = fixtures.bridged_run(self.root)
        declaration = fixtures.bridged_declaration(result=self.result_section())
        code, destination = self.repackage(run, declaration)
        self.assertEqual(code, 0)
        result = json.loads((destination / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["schema_version"], "1.1")
        self.assertEqual(result["hardware"]["cpu"], "AMD Ryzen 7 9800X3D")
        self.assertEqual(self.validate(destination), [])

    def test_the_original_evidence_is_left_exactly_as_it_was(self) -> None:
        run = fixtures.bridged_run(self.root)
        before = {path.name: sha256_file(path) for path in sorted(run.iterdir())}
        self.repackage(run, fixtures.bridged_declaration(result=self.result_section()))
        after = {path.name: sha256_file(path) for path in sorted(run.iterdir())}
        self.assertEqual(before, after)

    def test_the_derived_bundle_carries_the_same_measurements(self) -> None:
        run = fixtures.bridged_run(self.root)
        _, destination = self.repackage(
            run, fixtures.bridged_declaration(result=self.result_section()))
        for name in ("guidellm-benchmarks.json.gz", "guidellm.stdout.log",
                     "environment.json", "reproduce.md"):
            self.assertEqual(sha256_file(run / name), sha256_file(destination / name), name)

    def test_the_derived_result_links_back_to_the_evidence_it_came_from(self) -> None:
        run = fixtures.bridged_run(self.root)
        _, destination = self.repackage(
            run, fixtures.bridged_declaration(result=self.result_section()))
        packaging = json.loads(
            (destination / "result.json").read_text(encoding="utf-8"))["submission"]["packaging"]
        self.assertIs(packaging["metadata_declared_after_execution"], True)
        self.assertEqual(packaging["evidence_sha256"]["guidellm-benchmarks.json.gz"],
                         sha256_file(run / "guidellm-benchmarks.json.gz"))

    def test_metadata_supplied_afterwards_cannot_claim_to_have_been_detected(self) -> None:
        run = fixtures.bridged_run(self.root)
        declaration = fixtures.bridged_declaration(result=self.result_section())
        declaration["serving"]["probe"] = {
            "kind": serving_metadata.HARDWARE_PROBE_KIND,
            "os": "Windows 11 Pro 10.0.26200", "cpu": "AMD Ryzen 7 9800X3D", "ram_gb": 64.0,
            "accelerators": []}
        code, _ = self.repackage(run, declaration)
        self.assertEqual(code, 2)

    def test_a_run_missing_its_measurements_says_exactly_what_cannot_be_recovered(self) -> None:
        run = fixtures.bridged_run(self.root)
        (run / "guidellm-benchmarks.json.gz").unlink()
        import io
        from contextlib import redirect_stderr
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code, _ = self.repackage(
                run, fixtures.bridged_declaration(result=self.result_section()))
        self.assertEqual(code, 2)
        self.assertIn("guidellm-benchmarks.json.gz", stderr.getvalue())
        self.assertIn("Nothing can reconstruct them", stderr.getvalue())

    def test_repackaging_never_writes_over_the_original_directory(self) -> None:
        run = fixtures.bridged_run(self.root)
        code, _ = self.repackage(
            run, fixtures.bridged_declaration(result=self.result_section()),
            output_dir=str(run))
        self.assertEqual(code, 2)

    def test_an_existing_derived_bundle_is_not_replaced_without_force(self) -> None:
        run = fixtures.bridged_run(self.root)
        declaration = fixtures.bridged_declaration(result=self.result_section())
        self.repackage(run, declaration)
        code, _ = self.repackage(run, declaration)
        self.assertEqual(code, 2)

    def test_a_result_section_is_refused_when_packaging_directly(self) -> None:
        run = fixtures.bridged_run(self.root)
        code, _ = self.package(run, provenance=self.declaration(
            fixtures.bridged_declaration(result=self.result_section())))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
