"""What the panel's serving-environment form actually sends.

Drives the real cli/src/omobench/web/app.js through Node, in a VM context with
the shared DOM stand-in, and asserts on the declaration it builds. The
packager's rules are tested in Python; this covers the other half, where a form
turns into a claim about somebody's hardware.

Skipped where Node is unavailable.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "cli" / "src" / "omobench" / "web" / "app.js"
HARNESS = Path(__file__).resolve().parent / "provenance_form_harness.mjs"


def _states() -> dict:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node is not available in this environment")
    completed = subprocess.run([node, str(HARNESS), str(APP_JS)],
                               capture_output=True, text=True,
                               encoding="utf-8", timeout=120)
    if completed.returncode != 0:
        raise AssertionError(f"provenance form harness failed:\n{completed.stderr}")
    return json.loads(completed.stdout)


class ProvenanceFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.states = _states()

    def test_the_serving_host_is_sent_as_its_own_object(self) -> None:
        host = self.states["bridged"]["serving"]["host"]
        self.assertEqual(host, {"os": "Windows 11 Pro 10.0.26200",
                                "cpu": "AMD Ryzen 7 9800X3D", "ram_gb": 64})

    def test_the_served_alias_travels_separately_from_the_canonical_model(self) -> None:
        state = self.states["bridged"]
        self.assertEqual(state["served_model_id"], "deepseek-r1-distill-qwen-14b")
        self.assertEqual(state["model"], "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B")

    def test_the_inference_engine_is_not_the_application_that_embeds_it(self) -> None:
        runtime = self.states["bridged"]["serving"]["runtime"]
        self.assertEqual(runtime["engine"], {"name": "llama.cpp", "version": "b10621"})
        self.assertEqual(runtime["application"], {"name": "LM Studio", "version": "0.3.31"})

    def test_an_engine_left_blank_falls_back_to_the_chosen_framework(self) -> None:
        self.assertEqual(self.states["empty"]["serving"]["runtime"]["engine"]["name"], "lmstudio")

    def test_serving_settings_are_sent_as_named_values(self) -> None:
        settings = self.states["bridged"]["serving"]["runtime"]["settings"]
        self.assertEqual(settings, [{"name": "context_length", "value": "8192"},
                                    {"name": "gpu_layers", "value": "49"}])

    def test_an_idle_card_keeps_its_own_role(self) -> None:
        roles = [device["role"] for device in self.states["mixed"]["serving"]["accelerators"]]
        self.assertEqual(roles, ["inference", "present-not-used"])

    def test_a_split_placement_carries_the_split(self) -> None:
        self.assertEqual(self.states["mixed"]["serving"]["placement"],
                         {"mode": "cpu-gpu-offload", "detail": "32 of 49 layers on GPU"})

    def test_an_unrecorded_placement_is_omitted_rather_than_guessed(self) -> None:
        self.assertNotIn("placement", self.states["empty"]["serving"])

    def test_nothing_declared_sends_nothing_about_the_serving_machine(self) -> None:
        serving = self.states["empty"]["serving"]
        self.assertEqual(serving["host"], {})
        self.assertEqual(serving["accelerators"], [])

    def test_a_proposal_is_sent_only_when_its_section_is_open(self) -> None:
        self.assertIsNone(self.states["bridged"].get("proposal"))
        self.assertNotIn("catalog_status", self.states["bridged"])
        proposal = self.states["proposed"]["proposal"]
        self.assertEqual(self.states["proposed"]["catalog_status"], "proposed")
        self.assertEqual(proposal["family"], "DeepSeek")
        self.assertIs(proposal["weights_publicly_downloadable"], True)

    def test_the_form_fields_do_not_leak_through_as_packaging_options(self) -> None:
        """The CLI would reject them, and they would be silently meaningless."""
        for state in ("bridged", "mixed", "empty", "proposed"):
            with self.subTest(state=state):
                leftovers = [key for key in self.states[state]
                             if key.startswith(("serving_", "proposal_", "weights_"))]
                self.assertEqual(leftovers, [])

    def test_malformed_accelerator_lines_are_refused_in_the_page(self) -> None:
        errors = self.states["errors"]
        self.assertIn("at least a vendor and a model", errors["tooFewFields"])
        self.assertIn("role is one of", errors["badRole"])
        self.assertIn("number of gigabytes", errors["badMemory"])
        self.assertIn("whole number", errors["badCount"])

    def test_a_malformed_setting_is_refused_rather_than_sent_as_a_name(self) -> None:
        self.assertIn('is "name = value"', self.states["errors"]["badSetting"])

    def test_a_weight_manifest_that_is_not_json_is_refused(self) -> None:
        self.assertIn("omobench weights", self.states["errors"]["badManifest"])

    # -- a model nobody curated ------------------------------------------------

    def test_a_custom_model_is_submitted_on_its_eligibility(self) -> None:
        """Not a proposal. Nobody is being asked to add it to the list."""
        state = self.states["uncatalogued"]
        self.assertEqual(state["catalog_status"], "uncatalogued")
        self.assertIsNone(state.get("proposal"))
        eligibility = state["eligibility"]
        self.assertEqual(eligibility["licence"], "apache-2.0")
        self.assertTrue(eligibility["licence_url"].startswith("https://"))
        self.assertIs(eligibility["weights_publicly_downloadable"], True)
        self.assertEqual(eligibility["modality"], "text-generation")
        self.assertEqual(eligibility["publisher"], "someone")

    def test_weights_nobody_else_can_obtain_are_declared_not_dropped(self) -> None:
        """The run is still worth doing locally. The page records the fact and
        lets the registry's own validator be the one that refuses it."""
        eligibility = self.states["privateWeights"]["eligibility"]
        self.assertIs(eligibility["weights_publicly_downloadable"], False)

    def test_an_untouched_proposal_section_declares_nothing(self) -> None:
        """The panel opens it by itself. Opening is not writing."""
        state = self.states["emptyProposalSection"]
        self.assertIsNone(state.get("proposal"))
        self.assertEqual(state["catalog_status"], "uncatalogued")
        self.assertIsNotNone(state["eligibility"])

    def test_an_open_proposal_keeps_precedence_over_the_eligibility_route(self) -> None:
        state = self.states["proposalWins"]
        self.assertEqual(state["catalog_status"], "proposed")
        self.assertIsNone(state.get("eligibility"))
        self.assertIsNotNone(state["proposal"])

    def test_an_empty_custom_panel_declares_nothing(self) -> None:
        """A blank field is not a claim that a model has no licence."""
        state = self.states["noCustomModel"]
        self.assertNotIn("catalog_status", state)
        self.assertIsNone(state.get("eligibility"))

    def test_a_hidden_custom_panel_describes_no_run(self) -> None:
        """Fields left over from an earlier choice are kept, not sent."""
        state = self.states["curatedAgain"]
        self.assertNotIn("catalog_status", state)
        self.assertIsNone(state.get("eligibility"))


if __name__ == "__main__":
    unittest.main()
