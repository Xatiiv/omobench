"""Reading a result without the schema open beside it.

Three rules this pins. A fact nobody recorded reads as "not recorded" rather
than being left out, because a missing row is a gap a reader fills with an
assumption. Nothing is scored, because a detected processor and a declared GPU
memory are not two points on one scale. And detection is not verification:
reading a machine establishes what it reports about itself and nothing more.

All synthetic.
"""
from __future__ import annotations

import unittest

from omobench import evidence

# Enough of a complete result that both readers accept it: the evidence summary
# ignores the measurements, and the website's reader checks their arithmetic
# before it will describe anything.
MEASUREMENTS = {
    "requests": {"attempted": 50, "succeeded": 50, "failed": 0},
    "latency_ms": {"ttft": {"p50": 180.0, "p95": 260.0},
                   "inter_token": {"p50": 20.0, "p95": 30.0}},
    "output_tokens_per_second": 30.0, "total_tokens_per_second": 48.0,
    "requests_per_second": 0.5, "error_rate_percent": 0.0,
}
ARTIFACTS = {name: {"path": path, "sha256": "sha256:" + "f" * 64} for name, path in (
    ("raw_runner_output", "guidellm-benchmarks.json.gz"),
    ("runner_stdout_log", "guidellm.stdout.log"),
    ("environment_snapshot", "environment.json"),
    ("reproduce_markdown", "reproduce.md"))}

LEGACY = {
    "run_id": "run-legacy-000001",
    "started_at": "2026-01-01T00:00:00Z",
    "configuration_fingerprint": "sha256:" + "1" * 64,
    "results": MEASUREMENTS,
    "artifacts": ARTIFACTS,
    "schema_version": "1.0",
    "execution_device": "gpu",
    "model": {"id": "Qwen/Qwen3-4B", "revision": "a" * 40, "quantization": "Q4_K_M"},
    "runtime": {"framework": "llama.cpp", "version": "b10621",
                "arguments": ["--ctx-size=4096"], "endpoint_type": "openai-compatible"},
    "hardware": {"os": "Linux-6.8", "cpu": "AMD Ryzen 9 7950X", "ram_gb": 64.0,
                 "gpus": [{"vendor": "nvidia", "model": "NVIDIA RTX 4090",
                           "memory_gb": 24.0, "count": 1}],
                 "driver_version": "560.35",
                 "detection": {"hardware": "auto", "model": "manual", "runtime": "manual"}},
    "benchmark": {"profile_id": "chat-small-v1", "profile_sha256": "sha256:" + "a" * 64,
                  "runner": "guidellm", "runner_version": "0.7.3"},
    "submission": {"submitter": "fixture", "run_kind": "initial"},
}


def fact(value, provenance="contributor-declared"):
    return {"value": value, "provenance": "unknown" if value is None else provenance}


BRIDGED = {
    "run_id": "run-bridged-00001",
    "started_at": "2026-11-01T10:00:00Z",
    "configuration_fingerprint": "sha256:" + "2" * 64,
    "results": MEASUREMENTS,
    "artifacts": ARTIFACTS,
    "schema_version": "1.1",
    "execution_device": "gpu",
    "model": {
        "id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", "revision": "b" * 40,
        "quantization": "Q4_K_M", "served_model_id": "deepseek-r1-distill-qwen-14b",
        "catalog_status": "catalogued",
        "weights": {"kind": "repository", "repository": "https://example.invalid/w",
                    "repository_revision": "b" * 40, "identity": "repository-revision",
                    "content": None},
        "tokenizer": {"source": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
                      "kind": "huggingface", "loaded": "loaded-before-run",
                      "matches_served_weights": "not-verifiable-here",
                      "identity": {"class": "LlamaTokenizerFast", "vocabulary_size": 152064}},
    },
    "runtime": {"framework": "lmstudio", "version": "b10621", "arguments": [],
                "endpoint_type": "openai-compatible"},
    "hardware": {"os": "Windows 11 Pro", "cpu": "AMD Ryzen 7 9800X3D", "ram_gb": 64.0,
                 "gpus": [{"vendor": "amd", "model": "AMD Radeon RX 9070",
                           "memory_gb": 16.0, "count": 1}],
                 "driver_version": None,
                 "detection": {"hardware": "user-confirmed", "model": "manual",
                               "runtime": "manual"}},
    "environment": {
        "metadata_version": "serving-environment-v1",
        "relationship": "windows-host-wsl-runner",
        "runner": {"os": "Linux-6.6-WSL2", "kind": "wsl2", "python_version": "3.12.3",
                   "cpu": "x86_64", "ram_gb": 31.0},
        "serving": {
            "host": {"os": fact("Windows 11 Pro", "detected"),
                     "cpu": fact("AMD Ryzen 7 9800X3D", "detected"),
                     "ram_gb": fact(64.0, "detected")},
            "accelerators": [
                {"vendor": "amd", "model": "AMD Radeon RX 9070", "memory_gb": 16.0,
                 "count": 1, "role": "inference", "provenance": "contributor-declared"},
                {"vendor": "intel", "model": "Intel Arc A770", "memory_gb": 16.0,
                 "count": 1, "role": "present-not-used", "provenance": "detected"},
            ],
            "placement": {"mode": "cpu-gpu-offload", "detail": "32 of 49 layers on GPU",
                          "provenance": "contributor-declared"},
            "runtime": {
                "application": {"name": "LM Studio", "version": "0.3.31",
                                "provenance": "contributor-declared"},
                "engine": {"name": "llama.cpp", "version": "b10621",
                           "provenance": "contributor-declared"},
                "settings": [{"name": "context_length", "value": "8192",
                              "provenance": "contributor-declared"}],
            },
        },
        "transport": {"kind": "windows-wsl-stdio-bridge", "protocol": "HTTP/1.1",
                      "bridge_version": "windows-stdio-http1-v1",
                      "overhead": "included-not-measured"},
    },
    "benchmark": {"profile_id": "chat-small-v2", "profile_sha256": "sha256:" + "c" * 64,
                  "runner": "guidellm", "runner_version": "0.7.3"},
    "submission": {"submitter": "fixture", "run_kind": "initial"},
}


def rows(summary, section_id):
    return {row["label"]: row for row in
            next(s for s in summary["sections"] if s["id"] == section_id)["rows"]}


def every_row(summary):
    return [row for section in summary["sections"] for row in section["rows"]]


class ShapeTests(unittest.TestCase):
    def test_the_same_sections_appear_for_both_schema_versions(self) -> None:
        self.assertEqual([s["id"] for s in evidence.summarize(LEGACY)["sections"]],
                         [s["id"] for s in evidence.summarize(BRIDGED)["sections"]])

    def test_nothing_is_scored(self) -> None:
        """Facts that differ in kind cannot be averaged into one number."""
        import re

        serialized = repr(evidence.summarize(BRIDGED)).lower()
        for word in ("score", "scored", "confidence", "rating", "rated", "grade", "trust"):
            with self.subTest(word=word):
                # Word boundaries, so "operating system" is not a rating.
                self.assertIsNone(re.search(rf"{word}", serialized), word)
        for row in every_row(evidence.summarize(BRIDGED)):
            self.assertNotIsInstance(row["value"], (int, float))

    def test_every_row_says_whether_it_was_recorded(self) -> None:
        for row in every_row(evidence.summarize(LEGACY)) + every_row(evidence.summarize(BRIDGED)):
            with self.subTest(row=row["label"]):
                self.assertIsInstance(row["recorded"], bool)
                if not row["recorded"]:
                    self.assertEqual(row["value"], evidence.NOT_RECORDED)

    def test_detection_is_never_described_as_verification(self) -> None:
        summary = evidence.summarize(BRIDGED)
        self.assertIn("None of this independently confirms", " ".join(summary["qualifications"]))
        for row in every_row(summary):
            self.assertNotIn("verified", (row["source"] or "").lower())

    def test_a_result_that_is_not_an_object_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            evidence.summarize("not a result")


class LegacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.summary = evidence.summarize(LEGACY)

    def test_a_1_0_result_is_still_readable(self) -> None:
        self.assertEqual(self.summary["schema_version"], "1.0")
        self.assertEqual(rows(self.summary, "environments")["Serving processor"]["value"],
                         "AMD Ryzen 9 7950X")

    def test_it_is_told_that_this_result_predates_serving_provenance(self) -> None:
        self.assertIn("packaged under schema 1.0", " ".join(self.summary["qualifications"]))

    def test_nothing_absent_is_filled_in(self) -> None:
        environments = rows(self.summary, "environments")
        self.assertFalse(environments["Arrangement"]["recorded"])
        self.assertFalse(environments["Benchmark runner"]["recorded"])
        self.assertFalse(rows(self.summary, "accelerators")["Weight placement"]["recorded"])
        self.assertFalse(rows(self.summary, "tokenizer")["Tokenizer source"]["recorded"])

    def test_the_one_detection_state_1_0_recorded_is_used_rather_than_guessed(self) -> None:
        self.assertEqual(rows(self.summary, "environments")["Serving processor"]["source"],
                         "read on that machine")
        confirmed = evidence.summarize({**LEGACY, "hardware": {
            **LEGACY["hardware"],
            "detection": {"hardware": "user-confirmed", "model": "manual", "runtime": "manual"}}})
        self.assertEqual(rows(confirmed, "environments")["Serving processor"]["source"],
                         "stated by the contributor")

    def test_a_1_0_accelerator_gains_no_role_it_never_recorded(self) -> None:
        accelerator = rows(self.summary, "accelerators")["Accelerator"]
        self.assertIn("recorded no role", accelerator["note"])
        self.assertNotIn("performed inference", accelerator["note"])

    def test_the_single_recorded_version_is_not_split_into_two(self) -> None:
        runtime = rows(self.summary, "runtime")
        self.assertEqual(runtime["Serving runtime build"]["value"], "b10621")
        self.assertFalse(runtime["Hosting application"]["recorded"])

    def test_the_transport_says_which_gate_established_it(self) -> None:
        transport = rows(self.summary, "transport")["Transport"]
        self.assertEqual(transport["value"], "Direct to the server")
        self.assertIn("refused bridged evidence outright", transport["note"])

    def test_the_missing_tokenizer_says_what_that_costs_a_reader(self) -> None:
        note = rows(self.summary, "tokenizer")["Tokenizer source"]["note"]
        self.assertIn("Every token figure in this result is some tokenizer's count", note)


class ServingProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.summary = evidence.summarize(BRIDGED)

    def test_the_runner_and_the_serving_machine_are_separate_rows(self) -> None:
        environments = rows(self.summary, "environments")
        self.assertEqual(environments["Serving processor"]["value"], "AMD Ryzen 7 9800X3D")
        self.assertIn("WSL2", environments["Benchmark runner"]["value"])
        self.assertIn("nothing it can see of its host is evidence about what served the model",
                      environments["Benchmark runner"]["note"])

    def test_each_fact_reads_as_who_established_it(self) -> None:
        environments = rows(self.summary, "environments")
        self.assertEqual(environments["Serving processor"]["source"], "read on that machine")
        self.assertEqual(environments["Arrangement"]["source"], "stated by the contributor")

    def test_an_idle_device_is_shown_with_its_own_role(self) -> None:
        accelerators = [row for row in every_row(self.summary) if row["label"] == "Accelerator"]
        self.assertEqual(len(accelerators), 2)
        self.assertIn("performed inference", accelerators[0]["note"])
        self.assertIn("present, did not serve", accelerators[1]["note"])
        for row in accelerators:
            self.assertIn("not that inference touched it", row["note"])

    def test_a_split_placement_carries_its_split(self) -> None:
        placement = rows(self.summary, "accelerators")["Weight placement"]
        self.assertEqual(placement["value"], "Split between CPU and GPU")
        self.assertEqual(placement["note"], "32 of 49 layers on GPU")

    def test_the_declared_execution_device_is_marked_as_a_declaration(self) -> None:
        declared = rows(self.summary, "accelerators")["Declared execution device"]
        self.assertEqual(declared["value"], "GPU")
        self.assertIn("cannot confirm it", declared["note"])

    def test_the_engine_build_is_separate_from_the_application_release(self) -> None:
        runtime = rows(self.summary, "runtime")
        self.assertEqual(runtime["Inference engine"]["value"], "llama.cpp b10621")
        self.assertEqual(runtime["Hosting application"]["value"], "LM Studio 0.3.31")
        self.assertIn("different thing", runtime["Hosting application"]["note"])

    def test_serving_settings_appear_as_their_own_rows(self) -> None:
        self.assertEqual(rows(self.summary, "runtime")["Serving setting · context_length"]["value"],
                         "8192")

    def test_the_alias_and_the_canonical_model_are_separate_rows(self) -> None:
        model = rows(self.summary, "model")
        self.assertEqual(model["Canonical model"]["value"],
                         "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B")
        self.assertEqual(model["Identifier the server answered to"]["value"],
                         "deepseek-r1-distill-qwen-14b")
        self.assertIn("not a claim about which weights",
                      model["Identifier the server answered to"]["note"])

    def test_weights_are_shown_as_a_repository_commit(self) -> None:
        weights = rows(self.summary, "model")["Weights"]
        self.assertIn("https://example.invalid/w", weights["value"])
        self.assertIn("A file checksum is a different thing", weights["note"])

    def test_content_hashed_weights_say_the_bytes_are_the_identity(self) -> None:
        document = {**BRIDGED, "model": {**BRIDGED["model"], "weights": {
            "kind": "local-files", "repository": None, "repository_revision": None,
            "identity": "content-hash",
            "content": {"manifest_sha256": "sha256:" + "d" * 64,
                        "files": [{"name": "a.gguf", "bytes": 1, "sha256": "sha256:" + "e" * 64}]}}}}
        weights = rows(evidence.summarize(document), "model")["Weights"]
        self.assertIn("identified by their bytes", weights["value"])
        self.assertIn("no public immutable revision", weights["note"])

    def test_a_loaded_tokenizer_is_never_shown_as_a_matching_one(self) -> None:
        tokenizer = rows(self.summary, "tokenizer")
        self.assertIn("in the runner's own interpreter", tokenizer["Loaded before the run"]["value"])
        self.assertEqual(tokenizer["Matches the served weights"]["value"],
                         "Not verifiable from this evidence")

    def test_the_bridge_and_its_cost_are_both_stated(self) -> None:
        transport = rows(self.summary, "transport")
        self.assertIn("Windows–WSL bridge", transport["Transport"]["value"])
        self.assertIn("never subtracted", transport["Transport latency"]["value"])
        self.assertIn("Its latency is inside every number",
                      " ".join(self.summary["qualifications"]))

    def test_a_proposed_model_is_qualified_as_awaiting_review(self) -> None:
        document = {**BRIDGED, "model": {**BRIDGED["model"], "catalog_status": "proposed"}}
        summary = evidence.summarize(document)
        self.assertEqual(rows(summary, "model")["Catalog status"]["value"],
                         "Proposed — awaiting review")
        self.assertIn("separate decision", " ".join(summary["qualifications"]))

    def test_metadata_supplied_afterwards_is_qualified_as_such(self) -> None:
        document = {**BRIDGED, "submission": {
            **BRIDGED["submission"],
            "packaging": {"metadata_declared_after_execution": True}}}
        self.assertIn("packaged later from saved evidence",
                      " ".join(evidence.summarize(document)["qualifications"]))


class IncompleteProvenanceTests(unittest.TestCase):
    """A 1.1 result whose serving facts were never established."""

    def setUp(self) -> None:
        document = {**BRIDGED}
        environment = {**BRIDGED["environment"]}
        environment["serving"] = {
            **BRIDGED["environment"]["serving"],
            "host": {"os": fact(None), "cpu": fact(None), "ram_gb": fact(None)},
            "accelerators": [],
            "placement": {"mode": "unknown", "detail": None, "provenance": "unknown"},
            "runtime": {"application": None,
                        "engine": {"name": "llama.cpp", "version": None,
                                   "provenance": "unknown"},
                        "settings": []},
        }
        document["environment"] = environment
        document["model"] = {**BRIDGED["model"], "served_model_id": None,
                             "weights": None, "tokenizer": None}
        self.summary = evidence.summarize(document)

    def test_unestablished_facts_read_as_not_recorded(self) -> None:
        environments = rows(self.summary, "environments")
        for label in ("Serving operating system", "Serving processor", "Serving memory"):
            with self.subTest(label=label):
                self.assertEqual(environments[label]["value"], evidence.NOT_RECORDED)
                self.assertEqual(environments[label]["source"], "not recorded")

    def test_an_absent_accelerator_list_says_so_rather_than_vanishing(self) -> None:
        accelerator = rows(self.summary, "accelerators")["Accelerator"]
        self.assertFalse(accelerator["recorded"])
        self.assertIn("No accelerator is recorded", accelerator["note"])

    def test_an_unknown_placement_is_not_guessed_from_the_device(self) -> None:
        self.assertFalse(rows(self.summary, "accelerators")["Weight placement"]["recorded"])

    def test_an_absent_alias_and_weights_record_are_shown_as_absent(self) -> None:
        model = rows(self.summary, "model")
        self.assertFalse(model["Identifier the server answered to"]["recorded"])
        self.assertEqual(model["Weights"]["value"], "b" * 40)

    def test_an_absent_hosting_application_is_distinguished_from_an_unknown_one(self) -> None:
        note = rows(self.summary, "runtime")["Hosting application"]["note"]
        self.assertIn("engine was run directly", note)

    def test_the_standing_qualification_is_always_present(self) -> None:
        self.assertIn(evidence.STANDING_NOTE, self.summary["qualifications"])


if __name__ == "__main__":
    unittest.main()
