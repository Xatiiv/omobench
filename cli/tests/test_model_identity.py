"""Telling a serving alias from a model, and a name from a set of bytes.

The case these exist for: a completed benchmark of
`deepseek-ai/DeepSeek-R1-Distill-Qwen-14B` that could not be submitted, because
LM Studio serves it under the alias `deepseek-r1-distill-qwen-14b` and that
string was treated as the canonical model identity. The catalog had the model
all along.

The fix is not to map aliases automatically. It is to offer the mapping and
make a person confirm it, because an alias is a label a serving application
made up and nothing about it is evidence of which weights were loaded.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from omobench.model_identity import (
    ModelIdentityError,
    alias_core,
    is_manifest_revision,
    manifest_revision,
    suggest_catalog_models,
    weight_files_in,
    weight_manifest,
)

CATALOG = [
    "Qwen/Qwen3-4B",
    "meta-llama/Llama-3.1-8B-Instruct",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
    "mistralai/Mistral-Small-3.1-24B-Instruct-2503",
    "Qwen/Qwen3-32B",
]


class DeepSeekAliasTests(unittest.TestCase):
    """The exact case that blocked a real submission."""

    def test_the_lm_studio_alias_suggests_the_catalogued_model(self) -> None:
        suggestion = suggest_catalog_models("deepseek-r1-distill-qwen-14b", CATALOG)
        self.assertEqual([candidate["id"] for candidate in suggestion["candidates"]],
                         ["deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"])
        self.assertFalse(suggestion["ambiguous"])

    def test_the_alias_is_never_applied_without_a_person_choosing_it(self) -> None:
        suggestion = suggest_catalog_models("deepseek-r1-distill-qwen-14b", CATALOG)
        self.assertTrue(suggestion["requires_selection"])
        self.assertIn("Confirm it", suggestion["message"])
        self.assertIn("not proof of which weights were loaded", suggestion["message"])

    def test_the_alias_and_the_canonical_id_stay_different_strings(self) -> None:
        suggestion = suggest_catalog_models("deepseek-r1-distill-qwen-14b", CATALOG)
        self.assertEqual(suggestion["served_model_id"], "deepseek-r1-distill-qwen-14b")
        self.assertNotEqual(suggestion["served_model_id"], suggestion["candidates"][0]["id"])

    def test_the_canonical_id_itself_matches_exactly(self) -> None:
        suggestion = suggest_catalog_models(
            "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", CATALOG)
        self.assertEqual(suggestion["basis"], "exact-id")
        self.assertEqual(suggestion["candidates"][0]["id"],
                         "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B")


class AliasShapeTests(unittest.TestCase):
    def test_an_lm_studio_quantization_tag_is_not_part_of_the_name(self) -> None:
        self.assertEqual(alias_core("qwen/qwen3-4b:3"), ("qwen", "qwen3-4b", []))

    def test_a_gguf_republisher_is_recognised_as_the_same_model(self) -> None:
        suggestion = suggest_catalog_models("TheBloke/Qwen3-4B-GGUF", CATALOG)
        self.assertEqual(suggestion["basis"], "same-name-different-packaging")
        self.assertEqual(suggestion["candidates"][0]["id"], "Qwen/Qwen3-4B")
        self.assertIn("'gguf'", suggestion["candidates"][0]["reason"])

    def test_a_quantization_suffix_is_set_aside_but_reported(self) -> None:
        _, core, stripped = alias_core("Qwen3-4B-Q4_K_M-GGUF")
        self.assertEqual(core, "qwen3-4b")
        self.assertEqual(stripped, ["q4_k_m", "gguf"])

    def test_instruct_is_never_stripped_because_it_is_a_different_model(self) -> None:
        """Dropping it would propose weights nobody ran."""
        _, core, stripped = alias_core("Llama-3.1-8B-Instruct")
        # Dots normalize away on both sides, so this still matches the catalog.
        self.assertEqual(core, "llama-3-1-8b-instruct")
        self.assertEqual(stripped, [])
        self.assertEqual(
            [candidate["id"] for candidate in
             suggest_catalog_models("llama-3.1-8b-instruct", CATALOG)["candidates"]],
            ["meta-llama/Llama-3.1-8B-Instruct"])

    def test_a_base_model_does_not_match_its_instruct_sibling(self) -> None:
        self.assertEqual(suggest_catalog_models("Llama-3.1-8B", CATALOG)["candidates"], [])

    def test_an_unknown_alias_offers_nothing_and_says_so(self) -> None:
        suggestion = suggest_catalog_models("acme/private-model-v9", CATALOG)
        self.assertEqual(suggestion["candidates"], [])
        self.assertEqual(suggestion["basis"], "none")
        self.assertIn("propose it for the catalog", suggestion["message"])

    def test_an_ambiguous_alias_requires_the_contributor_to_choose(self) -> None:
        catalog = [*CATALOG, "unsloth/Qwen3-4B"]
        suggestion = suggest_catalog_models("qwen3-4b", catalog)
        self.assertTrue(suggestion["ambiguous"])
        self.assertEqual(len(suggestion["candidates"]), 2)
        self.assertIn("will not choose between them", suggestion["message"])

    def test_an_empty_catalog_offers_nothing_rather_than_failing(self) -> None:
        suggestion = suggest_catalog_models("deepseek-r1-distill-qwen-14b", [])
        self.assertEqual(suggestion["candidates"], [])

    def test_a_served_identifier_that_is_empty_is_handled(self) -> None:
        suggestion = suggest_catalog_models("", CATALOG)
        self.assertEqual(suggestion["candidates"], [])


class WeightManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def write(self, name: str, data: bytes) -> Path:
        path = self.folder / name
        path.write_bytes(data)
        return path

    def test_the_identity_comes_from_the_bytes_not_the_name(self) -> None:
        first = self.write("model.gguf", b"the actual weights")
        manifest = weight_manifest([first], root=self.folder)
        self.assertEqual(manifest["files"][0]["sha256"],
                         "sha256:" + hashlib.sha256(b"the actual weights").hexdigest())
        self.assertEqual(manifest["files"][0]["bytes"], len(b"the actual weights"))

    def test_renaming_a_file_changes_the_identity(self) -> None:
        """A filename is part of the record, so it cannot be swapped silently."""
        self.write("model.gguf", b"the actual weights")
        first = weight_manifest([self.folder / "model.gguf"], root=self.folder)
        (self.folder / "model.gguf").rename(self.folder / "renamed.gguf")
        second = weight_manifest([self.folder / "renamed.gguf"], root=self.folder)
        self.assertNotEqual(first["manifest_sha256"], second["manifest_sha256"])

    def test_identical_bytes_under_the_same_name_hash_identically(self) -> None:
        self.write("model.gguf", b"the actual weights")
        first = weight_manifest([self.folder / "model.gguf"], root=self.folder)
        second = weight_manifest([self.folder / "model.gguf"], root=self.folder)
        self.assertEqual(first, second)

    def test_a_sharded_set_is_one_identity_over_every_shard(self) -> None:
        shards = [self.write(f"model-0000{index}-of-00003.safetensors", f"shard {index}".encode())
                  for index in (1, 2, 3)]
        manifest = weight_manifest(shards, root=self.folder)
        self.assertEqual(len(manifest["files"]), 3)
        self.assertTrue(is_manifest_revision(manifest_revision(manifest)))

    def test_the_order_files_are_selected_in_does_not_change_the_identity(self) -> None:
        shards = [self.write(f"shard-{index}.safetensors", f"shard {index}".encode())
                  for index in (1, 2, 3)]
        self.assertEqual(weight_manifest(shards, root=self.folder),
                         weight_manifest(list(reversed(shards)), root=self.folder))

    def test_one_changed_shard_changes_the_whole_identity(self) -> None:
        shards = [self.write(f"shard-{index}.safetensors", f"shard {index}".encode())
                  for index in (1, 2, 3)]
        before = weight_manifest(shards, root=self.folder)["manifest_sha256"]
        shards[1].write_bytes(b"shard 2 corrected")
        after = weight_manifest(shards, root=self.folder)["manifest_sha256"]
        self.assertNotEqual(before, after)

    def test_a_missing_shard_changes_the_identity_rather_than_being_overlooked(self) -> None:
        shards = [self.write(f"shard-{index}.safetensors", f"shard {index}".encode())
                  for index in (1, 2, 3)]
        self.assertNotEqual(weight_manifest(shards, root=self.folder)["manifest_sha256"],
                            weight_manifest(shards[:2], root=self.folder)["manifest_sha256"])

    def test_a_manifest_revision_is_not_mistakable_for_a_commit(self) -> None:
        manifest = weight_manifest([self.write("model.gguf", b"x")], root=self.folder)
        revision = manifest_revision(manifest)
        self.assertTrue(revision.startswith("sha256-manifest:"))
        self.assertFalse(is_manifest_revision("a" * 40))
        self.assertFalse(is_manifest_revision("sha256:" + "a" * 64))

    def test_a_file_outside_the_selected_folder_is_refused(self) -> None:
        outside = Path(tempfile.mkdtemp()) / "elsewhere.gguf"
        outside.write_bytes(b"x")
        self.addCleanup(outside.unlink)
        with self.assertRaises(ModelIdentityError):
            weight_manifest([outside], root=self.folder)

    def test_the_same_file_cannot_be_counted_twice(self) -> None:
        path = self.write("model.gguf", b"x")
        with self.assertRaises(ModelIdentityError):
            weight_manifest([path, path], root=self.folder)

    def test_a_directory_is_not_a_weight_file(self) -> None:
        (self.folder / "subfolder").mkdir()
        with self.assertRaises(ModelIdentityError):
            weight_manifest([self.folder / "subfolder"], root=self.folder)

    def test_an_empty_selection_is_refused(self) -> None:
        with self.assertRaises(ModelIdentityError):
            weight_manifest([], root=self.folder)

    def test_listing_candidates_reads_no_file_contents(self) -> None:
        self.write("model.gguf", b"weights")
        self.write("README.md", b"not weights")
        self.write("tokenizer.json", b"not weights")
        self.assertEqual([path.name for path in weight_files_in(self.folder)], ["model.gguf"])

    def test_listing_a_folder_that_is_not_there_is_refused(self) -> None:
        with self.assertRaises(ModelIdentityError):
            weight_files_in(self.folder / "absent")


if __name__ == "__main__":
    unittest.main()
