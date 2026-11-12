"""The panel and the website explain a result in exactly the same words.

There are two implementations because one runs in a Python panel and the other
in a React page, and nothing can be shared between them at runtime. What can be
shared is this: the same result goes through both, and every section, row,
value, source and note has to come out identical. Without it the two drift, and
a contributor reads one account of their evidence while a reader of the
published result gets another.

Skipped where Node is unavailable.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from omobench import evidence
from test_evidence import BRIDGED, LEGACY

ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).resolve().parent / "evidence_parity_harness.mjs"
RECORD = ROOT / "app" / "lib" / "result-record.ts"
EVIDENCE = ROOT / "app" / "lib" / "evidence.ts"


def website_summary(result: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node is not available in this environment")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(result, handle)
        path = handle.name
    try:
        # UTF-8 explicitly: the summary contains "×" and "·", and decoding
        # Node's output with the platform's locale encoding would make the two
        # sides differ over nothing.
        completed = subprocess.run(
            [node, str(HARNESS), str(RECORD), str(EVIDENCE), path],
            capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT))
    finally:
        Path(path).unlink(missing_ok=True)
    if completed.returncode != 0:
        # The harness transpiles the website's TypeScript, so it needs the
        # repository's node_modules. A contributor who has only set up the
        # Python side is told that rather than shown a failure they did not
        # cause -- and CI installs them, so there the check really runs.
        if "ERR_MODULE_NOT_FOUND" in completed.stderr and "typescript" in completed.stderr:
            raise unittest.SkipTest(
                "The website's TypeScript toolchain is not installed here; run `npm ci` to "
                "check panel/website parity locally.")
        raise AssertionError(f"website evidence harness failed:\n{completed.stderr}")
    return json.loads(completed.stdout)


class EvidenceParityTests(unittest.TestCase):
    def assert_same(self, result: dict) -> None:
        theirs = website_summary(result)
        ours = evidence.summarize(result)
        self.assertEqual([section["id"] for section in ours["sections"]],
                         [section["id"] for section in theirs["sections"]])
        for mine, yours in zip(ours["sections"], theirs["sections"], strict=True):
            with self.subTest(section=mine["id"]):
                self.assertEqual(mine["title"], yours["title"])
                self.assertEqual(len(mine["rows"]), len(yours["rows"]), mine["id"])
                for left, right in zip(mine["rows"], yours["rows"], strict=True):
                    self.assertEqual(left, right, f"{mine['id']} · {left['label']}")
        self.assertEqual(ours["qualifications"], theirs["qualifications"])

    def test_a_schema_1_1_result_reads_the_same_in_both(self) -> None:
        self.assert_same(BRIDGED)

    def test_a_schema_1_0_result_reads_the_same_in_both(self) -> None:
        self.assert_same(LEGACY)

    def test_a_proposed_model_is_qualified_the_same_way_in_both(self) -> None:
        document = {**BRIDGED, "model": {**BRIDGED["model"], "catalog_status": "proposed"}}
        self.assert_same(document)

    def test_a_derived_bundle_is_qualified_the_same_way_in_both(self) -> None:
        document = {**BRIDGED, "submission": {
            **BRIDGED["submission"],
            "packaging": {"metadata_declared_after_execution": True}}}
        self.assert_same(document)

    def test_content_hashed_weights_read_the_same_in_both(self) -> None:
        document = {**BRIDGED, "model": {**BRIDGED["model"], "weights": {
            "kind": "local-files", "repository": None, "repository_revision": None,
            "identity": "content-hash",
            "content": {"manifest_sha256": "sha256:" + "d" * 64,
                        "files": [{"name": "a.gguf", "bytes": 1,
                                   "sha256": "sha256:" + "e" * 64}]}}}}
        document["model"]["revision"] = "sha256-manifest:" + "d" * 64
        self.assert_same(document)

    def test_cores_installed_memory_and_tokenizer_revision_read_the_same_in_both(self) -> None:
        """The 1.2 facts a run-start probe and a verified tokenizer add."""
        host = {**BRIDGED["environment"]["serving"]["host"],
                "cpu_cores": {"value": 8, "provenance": "detected"},
                "cpu_threads": {"value": 16, "provenance": "detected"},
                "ram_installed_gb": {"value": 32.0, "provenance": "detected"}}
        environment = {**BRIDGED["environment"],
                       "serving": {**BRIDGED["environment"]["serving"], "host": host}}
        tokenizer = {**(BRIDGED["model"].get("tokenizer") or {
            "source": "Qwen/Qwen3-4B", "kind": "huggingface", "loaded": "loaded-before-run",
            "matches_served_weights": "not-verifiable-here", "identity": None}),
            "revision": "0123456789abcdef0123456789abcdef01234567"}
        document = {**BRIDGED, "schema_version": "1.2", "environment": environment,
                    "model": {**BRIDGED["model"], "tokenizer": tokenizer}}
        self.assert_same(document)
        rows = {row["label"]: row["value"] for section in evidence.summarize(document)["sections"]
                for row in section["rows"]}
        self.assertEqual(rows["Serving CPU cores"], "8 physical cores · 16 threads")
        self.assertEqual(rows["Installed memory"], "32 GB")

    def test_the_vocabulary_itself_is_the_same_in_both(self) -> None:
        """The labels, not just the rows built from them."""
        source = EVIDENCE.read_text(encoding="utf-8")
        for value, label in evidence.PROVENANCE_LABELS.items():
            with self.subTest(provenance=value):
                self.assertIn(f'"{label}"', source)
        self.assertIn(evidence.NOT_RECORDED, source)
        self.assertIn(evidence.STANDING_NOTE[:60], source)
        self.assertIn(evidence.LEGACY_NOTE[:60], source)


if __name__ == "__main__":
    unittest.main()
