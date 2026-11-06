"""Curated workloads, read against hardware.

OmoBench measures hardware, so the question is what a machine can be asked to
run. These pin the distinctions the module exists to keep: identity lives in
one place, loading is not completing, a failure is not a requirement, one
observation is not a model, and an environment is not a computer.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from omobench import workloads

ROOT = Path(__file__).resolve().parents[2]


class IdentityTests(unittest.TestCase):
    """One authoritative record, referenced rather than copied."""

    def test_identity_comes_from_the_model_catalog(self) -> None:
        identities = workloads.models(ROOT)
        self.assertIn("Qwen/Qwen3-4B", identities)
        self.assertEqual(identities["Qwen/Qwen3-4B"]["licence"], "Apache-2.0")

    def test_no_recipe_carries_a_licence_of_its_own(self) -> None:
        """A licence copied into six recipes is a licence wrong in five."""
        for recipe in workloads.load(ROOT)["recipes"]:
            for field in ("licence", "licence_url", "source", "publisher", "parameter_class"):
                self.assertNotIn(field, recipe,
                                 f"{recipe['model']} {recipe['quantisation']} duplicates {field}")

    def test_every_recipe_names_a_model_the_catalog_knows(self) -> None:
        identities = workloads.models(ROOT)
        for recipe in workloads.load(ROOT)["recipes"]:
            self.assertIn(recipe["model"], identities)

    def test_legacy_catalog_lookup_still_works(self) -> None:
        """The registry validator reads these same files; the entries it
        needed before must still be there and still be shaped the same."""
        identities = workloads.models(ROOT)
        for legacy in ("Qwen/Qwen3-4B", "meta-llama/Llama-3.1-8B-Instruct",
                       "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
                       "mistralai/Mistral-Small-3.1-24B-Instruct-2503", "Qwen/Qwen3-32B"):
            entry = identities[legacy]
            for required in ("id", "modality", "source", "licence"):
                self.assertTrue(str(entry.get(required, "")).strip(), f"{legacy}.{required}")

    def test_an_entry_nobody_re_read_says_so(self) -> None:
        identities = workloads.models(ROOT)
        self.assertIsNone(identities["deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"].get("verified"))
        self.assertIsNotNone(identities["Qwen/Qwen3-4B"].get("verified"))

    def test_a_gated_model_is_marked_and_explained(self) -> None:
        identities = workloads.models(ROOT)
        meta = identities["meta-llama/Llama-3.1-8B-Instruct"]
        self.assertTrue(meta["gated"])
        self.assertIn("accept the Llama 3.1 Community License", meta["gate_note"])
        self.assertFalse(identities["Qwen/Qwen3-4B"]["gated"])


class HonestyTests(unittest.TestCase):
    """What the catalog refuses to claim."""

    def test_no_recipe_claims_a_completion_requirement(self) -> None:
        """Nothing has completed a profile on measured hardware, so nothing
        knows what completing costs."""
        for recipe in workloads.load(ROOT)["recipes"]:
            self.assertIsNone(recipe.get("completion_requirement_gb"),
                              f"{recipe['model']} {recipe['quantisation']}")

    def test_the_oom_figure_is_labelled_a_failure_not_a_requirement(self) -> None:
        recipe = next(r for r in workloads.load(ROOT)["recipes"]
                      if r["model"] == "Qwen/Qwen3-4B" and r["quantisation"] == "Q4_K_M")
        self.assertEqual(recipe["observed_before_failure_gb"], 6.99)
        self.assertIn("lower bound on a failure", recipe["observation_note"])
        self.assertIsNone(recipe["completion_requirement_gb"])

    def test_the_growth_hint_states_that_it_is_one_observation(self) -> None:
        hint = workloads.load(ROOT)["estimate_basis"]["growth_hint"]
        self.assertIn("ONE observation", hint["source"])
        self.assertIn("no second data point", hint["uncertainty"])
        self.assertEqual(hint["applies_to"], "llama.cpp")

    def test_the_hint_is_not_offered_for_an_engine_it_does_not_describe(self) -> None:
        """vLLM pre-allocates its KV cache; the llama.cpp ratio is the wrong
        shape and is not quietly reused."""
        document = workloads.describe(ROOT, serving_available_gb=64.0)
        for family in document["families"]:
            for model in family["models"]:
                for option in model["options"]:
                    if option["runtime"] == "vllm":
                        self.assertFalse(option["growth_hint_applies"], option["quantisation"])


class AssessTests(unittest.TestCase):
    """Two questions with separate answers: will it load, will it finish."""

    def test_a_known_load_on_a_big_machine_still_cannot_promise_completion(self) -> None:
        answer = workloads.assess(load_memory_gb=4.3, completion_requirement_gb=None,
                                  available_gb=64.0)
        self.assertEqual(answer["loads"], "fits")
        self.assertEqual(answer["completes"], "unknown")
        self.assertEqual(answer["verdict"], "marginal")

    def test_too_small_to_load_is_a_flat_no(self) -> None:
        answer = workloads.assess(load_memory_gb=11.0, completion_requirement_gb=None,
                                  available_gb=8.0)
        self.assertEqual(answer["verdict"], "too-large")

    def test_a_measured_completion_requirement_would_allow_a_yes(self) -> None:
        """None exists yet; the path is tested so it works when one does."""
        answer = workloads.assess(load_memory_gb=4.3, completion_requirement_gb=9.0,
                                  available_gb=16.0)
        self.assertEqual(answer["verdict"], "fits")
        self.assertEqual(answer["completes"], "fits")

    def test_unknown_memory_produces_unknown_and_not_a_refusal(self) -> None:
        answer = workloads.assess(load_memory_gb=4.3, completion_requirement_gb=None,
                                  available_gb=None)
        self.assertEqual(answer["verdict"], "unknown")
        self.assertEqual(answer["loads"], "unknown")

    def test_a_recipe_with_no_measured_load_falls_back_to_the_weight_file(self) -> None:
        answer = workloads.assess(load_memory_gb=None, completion_requirement_gb=None,
                                  available_gb=8.0, weights_gb=9.1)
        self.assertEqual(answer["verdict"], "too-large", "the weights alone do not fit")


class HardwareTests(unittest.TestCase):
    """An environment is not a computer, and an allocation is not free memory."""

    def test_allocation_and_available_are_reported_separately(self) -> None:
        document = workloads.describe(ROOT, serving_available_gb=6.4,
                                      serving_allocation_gb=7.49, physical_ram_gb=15.46)
        hardware = document["hardware"]
        self.assertEqual(hardware["serving_available_gb"], 6.4)
        self.assertEqual(hardware["serving_allocation_gb"], 7.49)
        self.assertEqual(hardware["physical_ram_gb"], 15.46)
        self.assertEqual(hardware["decides_on_gb"], 6.4, "what is free decides, not the ceiling")

    def test_a_bridged_run_says_both_environments_share_one_computer(self) -> None:
        """The runner's memory does not stop mattering because it is on the
        other side of a bridge: it is the same physical RAM."""
        document = workloads.describe(ROOT, serving_available_gb=20.0, bridged=True,
                                      physical_ram_gb=32.0)
        note = document["hardware"]["shared_host_note"]
        self.assertIn("same physical computer", note)
        self.assertIn("not available to the server", note)

    def test_a_same_host_run_says_so_too(self) -> None:
        document = workloads.describe(ROOT, serving_available_gb=7.0)
        self.assertIn("both serves the model and runs the benchmark",
                      document["hardware"]["shared_host_note"])

    def test_gpu_placement_decides_on_vram(self) -> None:
        document = workloads.describe(ROOT, serving_available_gb=64.0, serving_vram_gb=8.0,
                                      placement="gpu-only")
        self.assertEqual(document["hardware"]["device"], "gpu")
        self.assertEqual(document["hardware"]["decides_on_gb"], 8.0)

    def test_hardware_nobody_established_is_unknown_not_a_refusal(self) -> None:
        document = workloads.describe(ROOT)
        self.assertFalse(document["hardware"]["known"])
        for family in document["families"]:
            for model in family["models"]:
                for option in model["options"]:
                    self.assertEqual(option["verdict"], "unknown")


class ShapeTests(unittest.TestCase):
    def test_recipes_group_by_family_and_then_by_model(self) -> None:
        document = workloads.describe(ROOT, serving_available_gb=16.0)
        families = {family["family"]: family for family in document["families"]}
        self.assertIn("Qwen3", families)
        qwen = {model["id"]: model for model in families["Qwen3"]["models"]}
        self.assertEqual(sorted(qwen), ["Qwen/Qwen3-4B", "Qwen/Qwen3-8B"])
        labels = [option["quantisation"] for option in qwen["Qwen/Qwen3-4B"]["options"]]
        self.assertEqual(labels, ["Q4_K_M", "Q8_0", "F16"])

    def test_a_prepared_recipe_is_distinguished_from_one_you_must_serve(self) -> None:
        document = workloads.describe(ROOT, serving_available_gb=16.0)
        qwen = next(model for family in document["families"] for model in family["models"]
                    if model["id"] == "Qwen/Qwen3-4B")
        prepared = next(o for o in qwen["options"] if o["quantisation"] == "Q4_K_M")
        self.assertTrue(prepared["prepared"])
        self.assertEqual(prepared["managed_recipe"], "qwen3-4b-cpu-v2")

    def test_the_answer_can_be_sent_to_a_browser(self) -> None:
        json.dumps(workloads.describe(ROOT, serving_available_gb=16.0))

    def test_the_answer_carries_its_own_caveats(self) -> None:
        text = " ".join(workloads.describe(ROOT)["qualifications"])
        self.assertIn("Loading is not completing", text)
        self.assertIn("lower bound on a failure", text)
        self.assertIn("one observation", text)
        self.assertIn("allocation is not the memory currently free", text)

    def test_nothing_here_ranks_anything(self) -> None:
        document = workloads.describe(ROOT, serving_available_gb=64.0)
        text = json.dumps(document).lower()
        for word in ("best", "better", "score", "rank"):
            self.assertNotIn(word, text, word)

    def test_an_excluded_model_keeps_its_reason_on_the_record(self) -> None:
        excluded = {entry["id"]: entry for entry in workloads.describe(ROOT)["excluded"]}
        self.assertIn("multimodal", excluded["google/gemma-3-4b-it"]["reason"])


if __name__ == "__main__":
    unittest.main()
