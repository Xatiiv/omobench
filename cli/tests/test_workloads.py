"""Curated workloads, read against hardware.

OmoBench measures hardware, so the question is what a machine can be asked to
run. These check the two distinctions the module exists to keep: expected to
fit is not benchmarked, and serving hardware is not runner hardware.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from omobench import workloads

ROOT = Path(__file__).resolve().parents[2]


class CatalogTests(unittest.TestCase):
    def test_the_shipped_catalog_loads_and_names_its_families(self) -> None:
        document = workloads.load(ROOT)
        names = [family["family"] for family in document["families"]]
        self.assertIn("Qwen3", names)
        self.assertGreaterEqual(len(names), 5, "five families were researched")

    def test_a_missing_catalog_is_not_an_error(self) -> None:
        """A contributor connecting their own server needs none of it."""
        document = workloads.load(Path("/nonexistent-checkout"))
        self.assertEqual(document["families"], [])

    def test_every_weight_figure_says_whether_it_was_measured(self) -> None:
        for family in workloads.load(ROOT)["families"]:
            for variant in family["variants"]:
                for option in variant["quantisations"]:
                    self.assertIn("estimated", option, f"{variant['id']} {option['label']}")
                    self.assertIsInstance(option["estimated"], bool)

    def test_a_family_nobody_re_read_says_so(self) -> None:
        """Carried-over records and fresh readings are different things."""
        families = {f["family"]: f for f in workloads.load(ROOT)["families"]}
        self.assertIsNone(families["DeepSeek"]["source_verified"])
        self.assertIn("not re-verified", families["DeepSeek"]["source_note"])
        self.assertEqual(str(families["Qwen3"]["source_verified"]), "2026-11-06")

    def test_a_gated_family_is_marked_and_explained(self) -> None:
        families = {f["family"]: f for f in workloads.load(ROOT)["families"]}
        self.assertTrue(families["Llama 3.1"]["gated"])
        self.assertIn("accept the Llama 3.1 Community License",
                      families["Llama 3.1"]["gate_note"])
        self.assertFalse(families["Qwen3"]["gated"])

    def test_an_excluded_model_keeps_its_reason_on_the_record(self) -> None:
        excluded = {entry["id"]: entry for entry in workloads.load(ROOT)["excluded"]}
        self.assertIn("google/gemma-3-4b-it", excluded)
        self.assertIn("multimodal", excluded["google/gemma-3-4b-it"]["reason"])


class TierTests(unittest.TestCase):
    def test_a_machine_belongs_to_the_largest_tier_it_meets(self) -> None:
        for gigabytes, expected in ((7.5, None), (8, 8), (12, 8), (16, 16), (31, 16), (64, 64),
                                    (128, 64)):
            self.assertEqual(workloads.tier_for(gigabytes, workloads.CPU_RAM_TIERS_GB), expected,
                             gigabytes)

    def test_rounding_up_would_be_flattery(self) -> None:
        self.assertEqual(workloads.tier_for(15.9, workloads.CPU_RAM_TIERS_GB), 8)

    def test_unknown_memory_produces_no_tier(self) -> None:
        self.assertIsNone(workloads.tier_for(None, workloads.CPU_RAM_TIERS_GB))

    def test_unknown_is_a_third_answer_and_not_a_failure(self) -> None:
        self.assertEqual(workloads.fits(8, None), "unknown")
        self.assertEqual(workloads.fits(None, 64), "unknown")
        self.assertEqual(workloads.fits(8, 8), "fits")
        self.assertEqual(workloads.fits(16, 8), "too-large")


class DescribeTests(unittest.TestCase):
    def options(self, document, model_id, label):
        for family in document["families"]:
            for variant in family["variants"]:
                if variant["id"] != model_id:
                    continue
                for option in variant["quantisations"]:
                    if option["quantisation"] == label:
                        return option
        raise AssertionError(f"{model_id} {label} not in the catalog")

    def test_a_small_machine_is_told_what_fits_and_what_does_not(self) -> None:
        document = workloads.describe(ROOT, serving_ram_gb=8.0)
        self.assertEqual(document["hardware"]["tier_gb"], 8)
        self.assertEqual(document["hardware"]["available_gb"], 8.0)
        self.assertEqual(self.options(document, "Qwen/Qwen3-4B", "Q4_K_M")["fit"], "fits")
        self.assertEqual(self.options(document, "microsoft/phi-4", "Q4_K_M")["fit"], "too-large")

    def test_a_larger_machine_may_still_run_a_smaller_workload(self) -> None:
        """Tiers are recommendations, not restrictions."""
        document = workloads.describe(ROOT, serving_ram_gb=64.0)
        for label in ("Q4_K_M", "Q8_0", "F16"):
            self.assertEqual(self.options(document, "Qwen/Qwen3-4B", label)["fit"], "fits", label)

    def test_hardware_nobody_established_produces_unknown_not_no(self) -> None:
        document = workloads.describe(ROOT)
        self.assertFalse(document["hardware"]["known"])
        self.assertEqual(self.options(document, "Qwen/Qwen3-4B", "Q4_K_M")["fit"], "unknown")

    def test_a_machine_below_a_tier_still_runs_what_it_can(self) -> None:
        """The bug this found: comparing a machine against the tier it belongs
        to said a 7.49 GB laptop could not run the 4 GB workload it had been
        running all day. A requirement decides; a tier only labels."""
        document = workloads.describe(ROOT, serving_ram_gb=7.49)
        self.assertIsNone(document["hardware"]["tier_gb"], "short of the 8 GB tier")
        option = self.options(document, "Qwen/Qwen3-4B", "Q4_K_M")
        self.assertEqual(option["fit"], "fits")
        self.assertEqual(option["requires_gb"], 4.0)
        self.assertEqual(option["tier_gb"], 8, "it is still an 8 GB-class workload")
        self.assertEqual(self.options(document, "Qwen/Qwen3-4B", "F16")["fit"], "too-large")

    def test_gpu_placement_reads_vram_rather_than_system_memory(self) -> None:
        """Serving hardware is not runner hardware, and VRAM is not RAM."""
        document = workloads.describe(ROOT, serving_ram_gb=64.0, serving_vram_gb=4.0,
                                      placement="gpu-only")
        self.assertEqual(document["hardware"]["device"], "gpu")
        self.assertEqual(document["hardware"]["tier_gb"], 4)
        self.assertEqual(self.options(document, "Qwen/Qwen3-4B", "Q4_K_M")["fit"], "fits")
        self.assertEqual(self.options(document, "Qwen/Qwen3-4B", "F16")["fit"], "too-large")
        self.assertTrue(self.options(document, "Qwen/Qwen3-4B", "F16")["offload_required"])

    def test_a_cpu_only_run_never_reports_offload(self) -> None:
        document = workloads.describe(ROOT, serving_ram_gb=8.0)
        self.assertFalse(self.options(document, "microsoft/phi-4", "Q4_K_M")["offload_required"])

    def test_a_prepared_workload_is_distinguished_from_one_you_must_serve(self) -> None:
        document = workloads.describe(ROOT, serving_ram_gb=16.0)
        prepared = self.options(document, "Qwen/Qwen3-4B", "Q4_K_M")
        self.assertTrue(prepared["prepared"])
        self.assertEqual(prepared["recipe"], "qwen3-4b-cpu-v2")
        self.assertFalse(self.options(document, "microsoft/phi-4", "Q4_K_M")["prepared"])

    def test_the_answer_can_actually_be_sent_to_a_browser(self) -> None:
        """YAML turns an unquoted date into a date object, which no JSON
        encoder will take. The endpoint returned 400 until this was pinned."""
        import json

        document = workloads.describe(ROOT, serving_ram_gb=16.0)
        json.dumps(document)
        verified = {f["family"]: f["source_verified"] for f in document["families"]}
        self.assertEqual(verified["Qwen3"], "2026-11-06")
        self.assertIsNone(verified["DeepSeek"])

    def test_the_answer_carries_its_own_caveats(self) -> None:
        """Every surface repeats these rather than inventing its own wording."""
        qualifications = " ".join(workloads.describe(ROOT)["qualifications"])
        self.assertIn("not a benchmark result", qualifications)
        self.assertIn("can still fail to finish one", qualifications)
        self.assertIn("different computer", qualifications)
        self.assertIn("recommendation", qualifications)

    def test_nothing_here_ranks_anything(self) -> None:
        """A bigger or faster model is not a better one, and this says nothing
        about the quality of what any of them produces."""
        document = workloads.describe(ROOT, serving_ram_gb=64.0)
        text = str(document).lower()
        for word in ("best", "better", "recommended_model", "score", "rank"):
            self.assertNotIn(word, text, word)


if __name__ == "__main__":
    unittest.main()
