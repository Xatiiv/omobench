"""Which tokenizer and which files a served identifier refers to, and the
weights a run used, identified at packaging time.

Fixtures only: tiny GGUF headers in a folder laid out like LM Studio's, and a
search function standing in for Hugging Face.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from omobench import model_resolution as mr
from omobench.ui import Controller, UIError

from test_local_models import write_gguf

ROOT = Path(__file__).resolve().parents[2]
QWEN_ALIAS = "qwen2.5-coder-14b-instruct"
QWEN_META = {"quantization": "Q4_K_M", "arch": "qwen2", "compatibility_type": "gguf"}


def lmstudio_folder(root: Path) -> Path:
    qwen = root / "lmstudio-community" / "Qwen2.5-Coder-14B-Instruct-GGUF"
    write_gguf(qwen / "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf", {
        "general.name": "Qwen2.5 Coder 14B Instruct",
        "general.base_model.count": 1,
        "general.base_model.0.repo_url": "https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct",
    })
    write_gguf(qwen / "Qwen2.5-Coder-14B-Instruct-Q8_0.gguf", {
        "general.name": "Qwen2.5 Coder 14B Instruct",
        "general.base_model.count": 1,
        "general.base_model.0.repo_url": "https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct",
    })
    # A model whose two files disagree about where they came from.
    odd = root / "someone" / "Mystery-7B-GGUF"
    write_gguf(odd / "Mystery-7B-Q4_K_M.gguf", {"general.source.huggingface.repository": "org-a/Mystery-7B"})
    write_gguf(odd / "Mystery-7B-Q8_0.gguf", {"general.source.huggingface.repository": "org-b/Mystery-7B"})
    return root


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.models = lmstudio_folder(self.base / "models")
        self.choices = mr.ChoiceStore(self.base / "choices.json")
        self.searched: list[str] = []

    def search(self, name):
        self.searched.append(name)
        return ["Qwen/Qwen2.5-Coder-14B-Instruct", "unsloth/Qwen2.5-Coder-14B-Instruct"]

    def resolve(self, served=QWEN_ALIAS, metadata=QWEN_META, root=True, search=None):
        return mr.resolve(served, server_metadata=metadata, model_root=self.models if root else None,
                          choices=self.choices, catalog=[], search=search or self.search)

    def test_the_qwen_alias_resolves_from_the_file_itself(self) -> None:
        """The real failure: LM Studio served the bare alias and the runner
        tried to load it as a repository."""
        answer = self.resolve()
        tokenizer = answer["tokenizer"]
        self.assertEqual(tokenizer["state"], "metadata")
        self.assertEqual(tokenizer["source"], "Qwen/Qwen2.5-Coder-14B-Instruct")
        self.assertFalse(tokenizer["needs_confirmation"])
        self.assertEqual(answer["files"]["state"], "found")
        self.assertEqual(answer["files"]["groups"][0]["quantization"], "Q4_K_M")
        self.assertEqual(self.searched, [], "nothing was looked up by name when the file answered")

    def test_two_quantisations_that_agree_still_give_one_tokenizer(self) -> None:
        answer = self.resolve(metadata={"arch": "qwen2"})
        self.assertEqual(answer["files"]["state"], "ambiguous")
        self.assertEqual(answer["tokenizer"]["state"], "metadata")

    def test_files_that_disagree_are_a_question(self) -> None:
        answer = self.resolve(served="mystery-7b", metadata={})
        self.assertEqual(answer["tokenizer"]["state"], "ambiguous")
        self.assertTrue(answer["tokenizer"]["needs_confirmation"])
        self.assertEqual(sorted(c["source"] for c in answer["tokenizer"]["candidates"]),
                         ["org-a/Mystery-7B", "org-b/Mystery-7B"])

    def test_without_the_file_a_name_match_needs_confirmation(self) -> None:
        answer = self.resolve(root=False)
        tokenizer = answer["tokenizer"]
        self.assertEqual(tokenizer["state"], "name-only")
        self.assertTrue(tokenizer["needs_confirmation"])
        self.assertEqual(tokenizer["candidates"][0]["source"], "Qwen/Qwen2.5-Coder-14B-Instruct")

    def test_an_unreachable_hugging_face_is_said_not_assumed(self) -> None:
        answer = self.resolve(root=False, search=lambda name: None)
        self.assertEqual(answer["tokenizer"]["state"], "none")
        self.assertIn("could not be reached", answer["tokenizer"]["reason"])

    def test_a_verified_choice_is_reused_for_the_same_model(self) -> None:
        key = mr.identity_key(QWEN_ALIAS, QWEN_META)
        self.choices.put(key, "Qwen/Qwen2.5-Coder-14B-Instruct", revision="abc123", basis="verified")
        answer = self.resolve(root=False)
        self.assertEqual(answer["tokenizer"]["state"], "remembered")
        self.assertEqual(answer["tokenizer"]["revision"], "abc123")
        self.assertEqual(self.searched, [])

    def test_a_different_quantisation_is_not_the_same_identity(self) -> None:
        self.assertNotEqual(mr.identity_key(QWEN_ALIAS, QWEN_META),
                            mr.identity_key(QWEN_ALIAS, {**QWEN_META, "quantization": "Q8_0"}))

    def test_a_repository_id_served_as_is_is_a_candidate_to_check(self) -> None:
        answer = self.resolve(served="Qwen/Qwen3-4B", metadata={}, root=False)
        self.assertEqual(answer["tokenizer"]["state"], "served-id")
        self.assertEqual(answer["tokenizer"]["source"], "Qwen/Qwen3-4B")


class PanelWeightsTests(unittest.TestCase):
    """Identifying the weights a run used, at packaging time."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "checkout"
        for relative in ("benchmarks", "catalog", "schema", "tools"):
            shutil.copytree(ROOT / relative, root / relative, ignore=shutil.ignore_patterns("__pycache__"))
        (root / "results").mkdir()
        self.models = lmstudio_folder(Path(temporary.name) / "models")
        big = self.models / "someone" / "Big-70B-GGUF"
        for index in (1, 2):
            write_gguf(big / f"Big-70B-Q4_K_M-{index:05d}-of-00002.gguf", {"general.name": "b"})
        self.controller = Controller(root)
        self.addCleanup(self.controller.close)
        self.controller.update_settings({"model_root": str(self.models)})
        self.job = "job-" + "c" * 16
        with self.controller.lock:
            self.controller.jobs[self.job] = {
                "id": self.job, "status": "completed", "model": QWEN_ALIAS,
                "created_at": "2026-11-12T10:00:00+00:00", "server_metadata": {"quantization": "Q4_K_M"}}

    def record_at_start(self, files):
        with self.controller.lock:
            self.controller.jobs[self.job]["model_files_at_start"] = \
                self.controller._model_files_at_start(files)

    def test_the_file_recorded_at_run_start_is_fingerprinted_and_verified(self) -> None:
        self.record_at_start(["lmstudio-community/Qwen2.5-Coder-14B-Instruct-GGUF/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"])
        answer = self.controller.identify_weights(self.job, {})
        self.assertEqual(answer["state"], "identified")
        self.assertTrue(answer["verified"])
        self.assertTrue(answer["revision"].startswith("sha256-manifest:"))
        self.assertFalse(answer["reused_fingerprint"])
        again = self.controller.identify_weights(self.job, {})
        self.assertTrue(again["reused_fingerprint"], "an unchanged file is not hashed twice")
        self.assertEqual(again["revision"], answer["revision"])

    def test_a_file_changed_since_the_run_is_not_attached(self) -> None:
        name = "lmstudio-community/Qwen2.5-Coder-14B-Instruct-GGUF/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"
        self.record_at_start([name])
        time.sleep(0.01)
        (self.models / name).write_bytes(b"GGUF different bytes")
        answer = self.controller.identify_weights(self.job, {})
        self.assertEqual(answer["state"], "changed")
        self.assertNotIn("revision", answer)

    def test_a_split_model_is_fingerprinted_as_all_of_its_shards(self) -> None:
        self.record_at_start([f"someone/Big-70B-GGUF/Big-70B-Q4_K_M-{i:05d}-of-00002.gguf" for i in (1, 2)])
        answer = self.controller.identify_weights(self.job, {})
        self.assertEqual(len(answer["manifest"]["files"]), 2)

    def test_a_reopened_run_offers_its_candidates_and_asks(self) -> None:
        answer = self.controller.identify_weights(self.job, {})
        self.assertEqual(answer["state"], "choose")
        self.assertEqual(len(answer["groups"]), 1, "the server's quantisation narrows it to one")

    def test_a_chosen_file_needs_confirmation_and_is_not_called_verified(self) -> None:
        name = "lmstudio-community/Qwen2.5-Coder-14B-Instruct-GGUF/Qwen2.5-Coder-14B-Instruct-Q8_0.gguf"
        with self.assertRaises(UIError):
            self.controller.identify_weights(self.job, {"files": [name]})
        answer = self.controller.identify_weights(self.job, {"files": [name], "confirm": True})
        self.assertEqual(answer["state"], "identified")
        self.assertFalse(answer["verified"])
        self.assertEqual(answer["basis"], "chosen-by-you")

    def test_a_file_outside_the_model_folder_is_refused(self) -> None:
        with self.assertRaises(UIError):
            self.controller.identify_weights(self.job, {"files": ["../../etc/passwd"], "confirm": True})

    def test_the_model_folder_takes_a_windows_path(self) -> None:
        settings = self.controller.settings()
        self.assertTrue(settings["model_root_readable"])
        with self.assertRaises(UIError):
            self.controller.update_settings({"model_root": r"Z:\no\such\folder"})


class NewerFactsTests(unittest.TestCase):
    """A tokenizer revision and CPU cores are 1.2 facts."""

    def test_the_schema_admits_them_on_1_2_and_refuses_them_on_1_1(self) -> None:
        schema = json.loads((ROOT / "schema" / "benchmark-run.schema.json").read_text(encoding="utf-8"))
        tokenizer = schema["properties"]["model"]["properties"]["tokenizer"]["properties"]
        host = schema["properties"]["environment"]["properties"]["serving"]["properties"]["host"]["properties"]
        self.assertIn("revision", tokenizer)
        for name in ("cpu_cores", "cpu_threads", "ram_installed_gb"):
            self.assertIn(name, host)
        rule = next(r for r in schema["allOf"]
                    if r["if"]["properties"]["schema_version"].get("const") == "1.1")
        text = json.dumps(rule)
        for name in ("revision", "cpu_cores", "cpu_threads", "ram_installed_gb"):
            self.assertIn(name, text, f"1.1 cannot acquire {name}")


if __name__ == "__main__":
    unittest.main()
