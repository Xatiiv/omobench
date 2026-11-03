"""A served model alias and a tokenizer source are two different identities.

LM Studio serves aliases such as "qwen/qwen3-vl-4b" or "qwen/qwen3-vl-4b:3".
GuideLLM 0.7.3 defaults its tokenizer to the backend model identifier, so those
aliases were sent to Hugging Face as repository ids and the run failed while
resolving the tokenizer, before any measurement. The override below keeps the
two identities separate without renaming anyone's served model.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omobench import cli
from omobench.ui import UIError, run_config


ROOT = Path(__file__).resolve().parents[2]
ALIAS = "qwen/qwen3-vl-4b:3"
TOKENIZER = "Qwen/Qwen3-VL-4B-Instruct"


class TokenizerSourceValidationTests(unittest.TestCase):
    def test_a_hugging_face_repository_id_is_accepted_unchanged(self):
        self.assertEqual(cli.validate_tokenizer_source(TOKENIZER), TOKENIZER)
        self.assertEqual(cli.validate_tokenizer_source("  " + TOKENIZER + " "), TOKENIZER)

    def test_an_existing_local_folder_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(cli.validate_tokenizer_source(temporary), str(Path(temporary)))

    def test_a_serving_alias_is_rejected_with_an_actionable_message(self):
        with self.assertRaises(ValueError) as caught:
            cli.validate_tokenizer_source(ALIAS)
        message = str(caught.exception)
        self.assertIn("Hugging Face repository id", message)
        self.assertIn("local folder", message)

    def test_a_value_cannot_inject_a_second_guidellm_setting(self):
        # The value travels inside a comma-separated kind=... argument, so a
        # comma or equals sign could otherwise add settings of its own,
        # including remote code execution.
        for hostile in ("Qwen/Qwen3-VL-4B-Instruct,load_kwargs={'trust_remote_code':True}",
                        "kind=huggingface_auto,model=other/repo",
                        "Qwen/Qwen3=VL"):
            with self.subTest(hostile=hostile), self.assertRaises(ValueError):
                cli.validate_tokenizer_source(hostile)

    def test_an_empty_or_oversized_source_is_rejected(self):
        for value in ("", "   ", "x/" + "y" * 600):
            with self.subTest(value=value[:20]), self.assertRaises(ValueError):
                cli.validate_tokenizer_source(value)


class TokenizerCommandTests(unittest.TestCase):
    def command(self, **extra):
        return cli.build_guidellm_command(
            root=ROOT,
            endpoint="http://127.0.0.1:1234/v1",
            model=ALIAS,
            profile_id="chat-small-v1",
            raw_output_path=Path("/fixture/run/guidellm-benchmarks.json"),
            **extra,
        )

    def test_the_default_command_is_byte_for_byte_unchanged(self):
        self.assertNotIn("--tokenizer", self.command())

    def test_an_override_adds_exactly_one_documented_tokenizer_argument(self):
        default, overridden = self.command(), self.command(tokenizer=TOKENIZER)
        self.assertEqual(overridden.count("--tokenizer"), 1)
        index = overridden.index("--tokenizer")
        self.assertEqual(overridden[index + 1], f"kind=huggingface_auto,model={TOKENIZER}")
        # Nothing else moves: same workload, same constraints, same seed.
        self.assertEqual([part for part in overridden if part not in
                          {"--tokenizer", f"kind=huggingface_auto,model={TOKENIZER}"}], default)

    def test_the_served_alias_still_reaches_the_backend_untouched(self):
        overridden = self.command(tokenizer=TOKENIZER)
        backend = overridden[overridden.index("--backend") + 1]
        self.assertIn(f"model={ALIAS}", backend)
        self.assertNotIn(TOKENIZER, backend)

    def test_an_invalid_override_never_reaches_the_runner(self):
        with self.assertRaises(ValueError):
            self.command(tokenizer="not a repo, really")


class TokenizerReportTests(unittest.TestCase):
    """The offline check behind the panel's "Check tokenizer" action."""

    def test_an_alias_used_as_its_own_tokenizer_is_reported_as_unusable(self):
        report = cli.tokenizer_source_report("", served_model=ALIAS)
        self.assertFalse(report["ok"])
        self.assertTrue(report["default"])
        self.assertIn(ALIAS, report["message"])
        self.assertIn("Enter a tokenizer source", report["message"])

    def test_an_uncached_repository_explains_the_download_before_it_happens(self):
        with tempfile.TemporaryDirectory() as cache:
            with patch.dict("os.environ", {"HUGGINGFACE_HUB_CACHE": cache}):
                report = cli.tokenizer_source_report(TOKENIZER, served_model=ALIAS)
        self.assertTrue(report["ok"])
        self.assertFalse(report["cached"])
        self.assertIn("download", report["message"])
        self.assertIn("huggingface.co", report["message"])
        self.assertIn("Model weights are not downloaded", report["message"])

    def test_a_cached_repository_says_no_download_is_needed(self):
        with tempfile.TemporaryDirectory() as cache:
            (Path(cache) / "models--Qwen--Qwen3-VL-4B-Instruct").mkdir()
            with patch.dict("os.environ", {"HUGGINGFACE_HUB_CACHE": cache}):
                report = cli.tokenizer_source_report(TOKENIZER)
        self.assertTrue(report["cached"])
        self.assertIn("no download is needed", report["message"])

    def test_a_local_folder_is_judged_by_the_files_actually_present(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = cli.tokenizer_source_report(folder)
            self.assertFalse(empty["ok"])
            self.assertIn("no recognizable tokenizer file", empty["message"])
            (Path(folder) / "tokenizer.json").write_text("{}", encoding="utf-8")
            present = cli.tokenizer_source_report(folder)
        self.assertTrue(present["ok"])
        self.assertEqual(present["kind"], "local")
        self.assertEqual(present["files"], ["tokenizer.json"])
        self.assertIn("Nothing is downloaded", present["message"])

    def test_the_report_never_contacts_the_network(self):
        import socket

        def forbidden(*args, **kwargs):
            raise AssertionError("the offline tokenizer check must not open a socket")

        with patch.object(socket.socket, "connect", forbidden):
            self.assertTrue(cli.tokenizer_source_report(TOKENIZER)["ok"])


class TokenizerCheckClaimTests(unittest.TestCase):
    """Four separate questions, four separate answers."""

    def checks(self, value, served_model=None):
        return {entry["id"]: entry for entry in
                cli.tokenizer_source_report(value, served_model=served_model)["checks"]}

    def test_loading_and_compatibility_are_never_claimed_by_an_offline_check(self):
        with tempfile.TemporaryDirectory() as cache:
            (Path(cache) / "models--Qwen--Qwen3-VL-4B-Instruct").mkdir()
            with patch.dict("os.environ", {"HUGGINGFACE_HUB_CACHE": cache}):
                found = self.checks(TOKENIZER)
        self.assertEqual(found["syntax"]["state"], "ok")
        self.assertEqual(found["files"]["state"], "ok")  # cached, so the files are there
        self.assertEqual(found["loaded"]["state"], "untested")
        self.assertEqual(found["compatibility"]["state"], "unverified")
        self.assertIn("does not import the tokenizer", found["loaded"]["detail"])
        self.assertIn("not thereby the tokenizer", found["compatibility"]["detail"])

    def test_an_uncached_repository_separates_syntax_from_files(self):
        with tempfile.TemporaryDirectory() as cache:
            with patch.dict("os.environ", {"HUGGINGFACE_HUB_CACHE": cache}):
                found = self.checks(TOKENIZER)
        self.assertEqual(found["syntax"]["state"], "ok")
        self.assertEqual(found["files"]["state"], "missing")
        self.assertIn("download them from huggingface.co", found["files"]["detail"])

    def test_an_incomplete_local_folder_fails_only_the_files_check(self):
        with tempfile.TemporaryDirectory() as folder:
            found = self.checks(folder)
        self.assertEqual(found["syntax"]["state"], "ok")
        self.assertEqual(found["files"]["state"], "missing")
        self.assertIn("no recognizable tokenizer file", found["files"]["detail"])

    def test_a_rejected_identifier_does_not_pretend_to_have_looked_for_files(self):
        found = self.checks("", served_model=ALIAS)
        self.assertEqual(found["syntax"]["state"], "failed")
        self.assertEqual(found["files"]["state"], "skipped")
        self.assertIn("no usable repository id or path", found["files"]["detail"])

    def test_malformed_options_are_refused_at_the_syntax_check(self):
        for hostile in ("Qwen/Qwen3,load_kwargs={}", "kind=hf_auto,model=x", "a/b=c"):
            with self.subTest(hostile=hostile):
                self.assertEqual(self.checks(hostile)["syntax"]["state"], "failed")

    def test_the_explanation_names_why_the_choice_changes_measurements(self):
        detail = self.checks(TOKENIZER)["compatibility"]["detail"]
        self.assertIn("how prompts", detail)
        self.assertIn("how tokens are counted", detail)
        self.assertIn("changes the measurements", detail)


class TokenizerRunConfigTests(unittest.TestCase):
    BASE = {"endpoint": "http://127.0.0.1:1234/v1", "model": ALIAS,
            "profile": "chat-small-v1", "execution_device": "cpu", "device_confirmed": True}

    def test_the_panel_keeps_both_identities_separately(self):
        config = run_config({**self.BASE, "tokenizer": TOKENIZER})
        self.assertEqual(config["model"], ALIAS)
        self.assertEqual(config["tokenizer"], TOKENIZER)

    def test_no_override_leaves_the_existing_default_in_place(self):
        self.assertIsNone(run_config(self.BASE)["tokenizer"])
        self.assertIsNone(run_config({**self.BASE, "tokenizer": "  "})["tokenizer"])

    def test_an_invalid_override_is_refused_before_a_run_starts(self):
        with self.assertRaises(UIError) as caught:
            run_config({**self.BASE, "tokenizer": "qwen/qwen3-vl-4b:3"})
        self.assertIn("Hugging Face repository id", str(caught.exception))

    def test_the_declared_cpu_device_survives_the_override(self):
        self.assertEqual(run_config({**self.BASE, "tokenizer": TOKENIZER})["execution_device"], "cpu")


class TokenizerArgumentParsingTests(unittest.TestCase):
    def test_the_run_parser_accepts_an_optional_tokenizer(self):
        parser = cli.build_parser()
        arguments = parser.parse_args(["run", "--endpoint=http://127.0.0.1:1234/v1",
                                       f"--model={ALIAS}", "--profile=chat-small-v1",
                                       f"--tokenizer={TOKENIZER}"])
        self.assertEqual(arguments.tokenizer, TOKENIZER)
        self.assertIsNone(parser.parse_args(["run", "--endpoint=http://127.0.0.1:1234/v1",
                                             f"--model={ALIAS}", "--profile=chat-small-v1"]).tokenizer)


if __name__ == "__main__":
    unittest.main()
