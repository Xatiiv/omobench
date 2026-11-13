"""Finding the served model's files, and reading what a GGUF says about itself.

The fixtures here are labelled stand-ins: tiny GGUF files with real headers and
no weights, laid out the way LM Studio lays out a models folder.
"""

from __future__ import annotations

import os
import struct
import tempfile
import time
import unittest
from pathlib import Path

from omobench import local_models as lm


def _string(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def write_gguf(path: Path, values: dict, *, vocabulary: int = 2000) -> Path:
    """A GGUF v3 header with the given general.* strings and a token array."""
    entries = []
    for key, value in values.items():
        if isinstance(value, int):
            entries.append(_string(key) + struct.pack("<I", 4) + struct.pack("<I", value))
        else:
            entries.append(_string(key) + struct.pack("<I", 8) + _string(value))
    # The tokenizer vocabulary a real header carries: long, and skipped.
    tokens = b"".join(_string(f"tok{i}") for i in range(vocabulary))
    entries.append(_string("tokenizer.ggml.tokens") + struct.pack("<I", 9)
                   + struct.pack("<I", 8) + struct.pack("<Q", vocabulary) + tokens)
    entries.append(_string("general.after_tokens") + struct.pack("<I", 8) + _string("still read"))
    header = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(entries))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b"".join(entries) + b"\0" * 64)
    return path


class PathTests(unittest.TestCase):
    def test_a_windows_drive_path_is_read_through_wsl(self) -> None:
        self.assertEqual(lm.to_local_path(r"D:\OmoBench\lmstudio-models"),
                         Path("/mnt/d/OmoBench/lmstudio-models"))
        self.assertEqual(lm.to_local_path("d:/OmoBench/models"), Path("/mnt/d/OmoBench/models"))

    def test_a_posix_path_is_taken_as_given(self) -> None:
        self.assertEqual(lm.to_local_path("/home/me/models"), Path("/home/me/models"))

    def test_nothing_is_guessed_for_a_relative_or_network_path(self) -> None:
        for value in ("models", "", r"\\server\share\models"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lm.to_local_path(value)


class GGUFHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_the_header_names_the_model_it_came_from(self) -> None:
        path = write_gguf(self.root / "model.gguf", {
            "general.name": "Qwen2.5 Coder 14B Instruct",
            "general.base_model.count": 1,
            "general.base_model.0.repo_url": "https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct",
        })
        meta = lm.read_gguf_metadata(path)
        self.assertEqual(meta["general.name"], "Qwen2.5 Coder 14B Instruct")
        self.assertEqual(meta["general.after_tokens"], "still read",
                         "the vocabulary array is skipped, and reading carries on past it")
        self.assertNotIn("tokenizer.ggml.tokens", meta)
        self.assertEqual(lm.tokenizer_sources_from_gguf(meta)[0]["source"],
                         "Qwen/Qwen2.5-Coder-14B-Instruct")

    def test_a_gguf_repository_is_never_offered_as_a_tokenizer(self) -> None:
        meta = {"general.source.huggingface.repository": "lmstudio-community/Foo-GGUF"}
        self.assertEqual(lm.tokenizer_sources_from_gguf(meta), [])

    def test_a_file_that_names_no_origin_offers_nothing(self) -> None:
        self.assertEqual(lm.tokenizer_sources_from_gguf({"general.name": "Something"}), [])

    def test_not_a_gguf_is_refused(self) -> None:
        path = self.root / "not.gguf"
        path.write_bytes(b"NOPE" + b"\0" * 32)
        with self.assertRaises(lm.GGUFError):
            lm.read_gguf_metadata(path)


class FindModelFilesTests(unittest.TestCase):
    """A models folder laid out the way LM Studio lays it out."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        qwen = self.root / "lmstudio-community" / "Qwen2.5-Coder-14B-Instruct-GGUF"
        write_gguf(qwen / "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf", {"general.name": "q"})
        other = self.root / "lmstudio-community" / "Mistral-7B-Instruct-v0.3-GGUF"
        write_gguf(other / "Mistral-7B-Instruct-v0.3-Q4_K_M.gguf", {"general.name": "m"})
        write_gguf(other / "Mistral-7B-Instruct-v0.3-Q8_0.gguf", {"general.name": "m"})
        big = self.root / "someone" / "Big-70B-GGUF"
        for index in (1, 2, 3):
            write_gguf(big / f"Big-70B-Q4_K_M-{index:05d}-of-00003.gguf", {"general.name": "b"})

    def test_the_qwen_alias_finds_its_one_file(self) -> None:
        found = lm.find_model_files(self.root, "qwen2.5-coder-14b-instruct", quantization="Q4_K_M")
        self.assertEqual(found["state"], "found")
        self.assertEqual(found["groups"][0]["files"],
                         ["lmstudio-community/Qwen2.5-Coder-14B-Instruct-GGUF/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"])
        self.assertEqual(found["groups"][0]["quantization"], "Q4_K_M")

    def test_two_quantisations_are_a_question_until_the_server_says_which(self) -> None:
        both = lm.find_model_files(self.root, "mistral-7b-instruct-v0.3")
        self.assertEqual(both["state"], "ambiguous")
        self.assertEqual(sorted(g["quantization"] for g in both["groups"]), ["Q4_K_M", "Q8_0"])
        one = lm.find_model_files(self.root, "mistral-7b-instruct-v0.3", quantization="Q8_0")
        self.assertEqual(one["state"], "found")

    def test_a_split_model_is_every_shard(self) -> None:
        found = lm.find_model_files(self.root, "big-70b")
        self.assertEqual(found["state"], "found")
        self.assertEqual(len(found["groups"][0]["files"]), 3)
        self.assertTrue(found["groups"][0]["complete"])

    def test_a_missing_shard_is_reported_not_ignored(self) -> None:
        os.remove(self.root / "someone" / "Big-70B-GGUF" / "Big-70B-Q4_K_M-00002-of-00003.gguf")
        group = lm.find_model_files(self.root, "big-70b")["groups"][0]
        self.assertFalse(group["complete"])
        self.assertEqual(group["missing"], ["Big-70B-Q4_K_M-00002-of-00003.gguf"])

    def test_an_lm_studio_suffix_and_publisher_prefix_still_match(self) -> None:
        self.assertEqual(lm.served_core("qwen/qwen2.5-coder-14b-instruct:2"),
                         lm.served_core("qwen2.5-coder-14b-instruct"))

    def test_a_model_not_in_the_folder_is_none(self) -> None:
        self.assertEqual(lm.find_model_files(self.root, "phi-4")["state"], "none")


class FingerprintCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.file = self.root / "m.gguf"
        self.file.write_bytes(b"x" * 1024)
        self.cache = lm.FingerprintCache(self.root / "cache.json")

    def test_a_fingerprint_is_reused_while_the_file_is_unchanged(self) -> None:
        self.cache.put([self.file], {"files": []}, "sha256-manifest:abc")
        self.assertEqual(self.cache.get([self.file])["revision"], "sha256-manifest:abc")

    def test_a_changed_file_invalidates_it(self) -> None:
        self.cache.put([self.file], {"files": []}, "sha256-manifest:abc")
        time.sleep(0.01)
        self.file.write_bytes(b"y" * 2048)
        self.assertIsNone(self.cache.get([self.file]))


if __name__ == "__main__":
    unittest.main()
