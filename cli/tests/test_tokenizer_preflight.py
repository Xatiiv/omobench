"""Loading a tokenizer before the benchmark tries to, and saying what that proved.

The failure these exist for: a contributor enters a GGUF repository, every
check passes, the benchmark starts, and GuideLLM dies resolving the tokenizer
before sending a single request. The old check only looked at the shape of the
string and whether files happened to be cached, which is exactly the part that
was never the problem.

No test here reaches the network or downloads anything. The probe process is
driven with stand-in interpreters that print the documents a real probe would.
"""
from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from omobench import tokenizer_preflight
from omobench.tokenizer_preflight import (
    NETWORK_DOWNLOAD,
    NETWORK_METADATA,
    NETWORK_NONE,
    download_plan,
    preflight,
)


MODEL_CARD_DATA = """
# A stand-in shaped like huggingface_hub's ModelCardData: an object with
# attributes and a to_dict(), not a mapping.
class _ModelCardData:
    def __init__(self):
        self.base_model = "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"

    def to_dict(self):
        return {"base_model": self.base_model}

"""


def fake_interpreter(directory: Path, body: str) -> list[str]:
    """A stand-in for the runner's interpreter.

    The caller appends `-I -c <probe source>`, which this ignores: it reads the
    same request on stdin the real probe reads and prints whatever `body`
    decides, so every outcome can be exercised without transformers, without a
    repository and without a network.
    """
    script = directory / f"stand_in_{abs(hash(body)) % 10**8}.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return [sys.executable, str(script)]


class PreflightFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def interpreter(self, body: str) -> list[str]:
        return fake_interpreter(self.directory, body)

    def probe(self, document: dict) -> list[str]:
        return self.interpreter(f'''
            import json, sys
            sys.stdin.read()
            print("OMOBENCH_TOKENIZER " + json.dumps({document!r}))
        ''')


class SyntaxTests(PreflightFixture):
    def test_a_value_that_is_not_a_repository_or_folder_fails_before_any_process(self) -> None:
        report = preflight("not a repo id", interpreter=self.interpreter("raise SystemExit(9)"))
        self.assertFalse(report.ok)
        self.assertEqual(report.kind, "invalid")
        self.assertEqual(self.state(report, "syntax"), "failed")
        self.assertEqual(self.state(report, "loaded"), "skipped")

    def test_an_empty_source_is_refused(self) -> None:
        report = preflight("   ", interpreter=sys.executable)
        self.assertEqual(report.kind, "invalid")

    def test_a_source_that_could_inject_a_second_runner_setting_is_refused(self) -> None:
        for source in ("Qwen/Qwen3-4B,kind=other", "Qwen/Qwen3-4B=x"):
            with self.subTest(source=source):
                self.assertEqual(preflight(source, interpreter=sys.executable).kind, "invalid")

    @staticmethod
    def state(report, check_id: str) -> str:
        return next(check["state"] for check in report.checks if check["id"] == check_id)


class LoadOutcomeTests(PreflightFixture):
    @staticmethod
    def state(report, check_id: str) -> str:
        return next(check["state"] for check in report.checks if check["id"] == check_id)

    def test_a_tokenizer_that_loads_reports_what_it_is(self) -> None:
        report = preflight("Qwen/Qwen3-4B", interpreter=self.probe({
            "outcome": "loaded",
            "identity": {"class": "Qwen2TokenizerFast", "vocabulary_size": 151936},
            "resolved_files": {}}))
        self.assertTrue(report.ok)
        self.assertEqual(report.kind, "loaded")
        self.assertEqual(report.identity["class"], "Qwen2TokenizerFast")
        self.assertEqual(report.identity["vocabulary_size"], 151936)
        self.assertEqual(self.state(report, "loaded"), "ok")

    def test_a_successful_load_still_reports_compatibility_as_unverified(self) -> None:
        """The whole point of keeping the four questions apart."""
        report = preflight("Qwen/Qwen3-4B", interpreter=self.probe({
            "outcome": "loaded", "identity": {"class": "X", "vocabulary_size": 10},
            "resolved_files": {}}))
        self.assertEqual(self.state(report, "compatibility"), "unverified")
        self.assertIn("not verifiable from here", report.message
                      + next(c["detail"] for c in report.checks if c["id"] == "compatibility"))

    def test_an_identifier_that_is_well_formed_but_will_not_load_is_a_failure(self) -> None:
        """Accepted syntax, real failure: the case the old check could not see."""
        report = preflight("Qwen/Does-Not-Exist", network=NETWORK_METADATA,
                           interpreter=self.probe({
                               "outcome": "not-loaded",
                               "load_error": "OSError: Qwen/Does-Not-Exist is not a local folder",
                               "files": [], "base_model": None}))
        self.assertFalse(report.ok)
        self.assertEqual(report.kind, "load-failed")
        self.assertEqual(self.state(report, "syntax"), "ok")
        self.assertEqual(self.state(report, "loaded"), "failed")
        self.assertIn("before any request is sent", report.message)

    def test_an_uncached_repository_is_reported_as_uncached_not_as_broken(self) -> None:
        report = preflight("Qwen/Qwen3-4B", network=NETWORK_NONE, interpreter=self.probe({
            "outcome": "not-loaded", "load_error": "OSError: offline mode"}))
        self.assertEqual(report.kind, "not-cached")
        self.assertIn("not in this machine's cache", report.message)
        self.assertEqual(report.network, "none")

    def test_a_repository_that_wants_to_run_its_own_code_is_refused(self) -> None:
        report = preflight("acme/custom", network=NETWORK_METADATA, interpreter=self.probe({
            "outcome": "not-loaded",
            "load_error": "ValueError: Loading acme/custom requires you to execute custom code"
                          " ... trust_remote_code=True",
            "files": ["tokenization_acme.py"], "base_model": None}))
        self.assertEqual(report.kind, "needs-remote-code")
        self.assertIn("refuses that", report.message)


class GgufRepositoryTests(PreflightFixture):
    GGUF_FILES = ["README.md", "model-Q4_K_M.gguf", "model-Q8_0.gguf", ".gitattributes"]

    def test_a_gguf_only_repository_is_named_as_the_problem(self) -> None:
        report = preflight("unsloth/Qwen3-4B-GGUF", network=NETWORK_METADATA,
                           interpreter=self.probe({
                               "outcome": "not-loaded",
                               "load_error": "OSError: does not appear to have a file named"
                                             " tokenizer.json",
                               "files": self.GGUF_FILES, "base_model": None}))
        self.assertEqual(report.kind, "gguf-only")
        self.assertIn("contains GGUF weights and no Transformers tokenizer", report.message)
        self.assertIn("Your server loads GGUF happily", report.message)

    def test_the_publisher_declared_base_model_is_suggested_not_chosen(self) -> None:
        report = preflight("unsloth/Qwen3-4B-GGUF", network=NETWORK_METADATA,
                           interpreter=self.probe({
                               "outcome": "not-loaded", "load_error": "OSError: no tokenizer.json",
                               "files": self.GGUF_FILES, "base_model": "Qwen/Qwen3-4B"}))
        self.assertEqual(report.suggestion["source"], "Qwen/Qwen3-4B")
        self.assertEqual(report.suggestion["basis"], "publisher-declared-base-model")
        self.assertIn("the publisher's statement", report.suggestion["reason"])
        self.assertFalse(report.ok)

    def test_no_generic_tokenizer_is_offered_when_the_publisher_declared_none(self) -> None:
        report = preflight("acme/mystery-GGUF", network=NETWORK_METADATA,
                           interpreter=self.probe({
                               "outcome": "not-loaded", "load_error": "OSError: no tokenizer.json",
                               "files": ["model.gguf"], "base_model": None}))
        self.assertIsNone(report.suggestion)
        self.assertIn("usually the original, unquantized model", report.message)

    def test_a_repository_that_declares_itself_as_its_own_base_offers_nothing(self) -> None:
        report = preflight("acme/model", network=NETWORK_METADATA, interpreter=self.probe({
            "outcome": "not-loaded", "load_error": "OSError", "files": ["model.gguf"],
            "base_model": "acme/model"}))
        self.assertIsNone(report.suggestion)

    def test_a_repository_with_both_weights_and_a_tokenizer_is_not_gguf_only(self) -> None:
        report = preflight("acme/model", network=NETWORK_METADATA, interpreter=self.probe({
            "outcome": "not-loaded", "load_error": "OSError: something else",
            "files": ["model.gguf", "tokenizer.json"], "base_model": None}))
        self.assertEqual(report.kind, "load-failed")


class NetworkBoundaryTests(PreflightFixture):
    def environment_probe(self) -> str:
        """Report the offline switches the probe process was actually given."""
        return self.interpreter('''
            import json, os, sys
            sys.stdin.read()
            print("OMOBENCH_TOKENIZER " + json.dumps({
                "outcome": "not-loaded",
                "load_error": "env HF_HUB_OFFLINE=%s TRANSFORMERS_OFFLINE=%s" % (
                    os.environ.get("HF_HUB_OFFLINE"), os.environ.get("TRANSFORMERS_OFFLINE")),
            }))
        ''')

    def test_the_offline_level_cannot_reach_the_network(self) -> None:
        report = preflight("Qwen/Qwen3-4B", network=NETWORK_NONE,
                           interpreter=self.environment_probe())
        self.assertIn("HF_HUB_OFFLINE=1", report.detail)
        self.assertIn("TRANSFORMERS_OFFLINE=1", report.detail)

    def test_the_metadata_level_lifts_only_the_offline_switches(self) -> None:
        report = preflight("Qwen/Qwen3-4B", network=NETWORK_METADATA,
                           interpreter=self.environment_probe())
        self.assertIn("HF_HUB_OFFLINE=None", report.detail)

    def test_an_unsupported_network_level_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            preflight("Qwen/Qwen3-4B", network="everything")

    def test_the_level_that_ran_is_always_reported_back(self) -> None:
        for level in (NETWORK_NONE, NETWORK_METADATA, NETWORK_DOWNLOAD):
            with self.subTest(level=level):
                report = preflight("Qwen/Qwen3-4B", network=level, interpreter=self.probe(
                    {"outcome": "loaded", "identity": {}, "resolved_files": {}}))
                self.assertEqual(report.network, level)

    def test_a_download_names_its_source_and_destination_before_running(self) -> None:
        plan = download_plan("Qwen/Qwen3-4B")
        self.assertEqual(plan["source"], "https://huggingface.co/Qwen/Qwen3-4B")
        self.assertTrue(plan["destination"])
        self.assertIn("Tokenizer files only", plan["downloads"])
        self.assertIn("never requested", plan["never_downloads"])

    def test_there_is_nothing_to_download_for_a_local_folder(self) -> None:
        folder = self.directory / "tokenizer"
        folder.mkdir()
        (folder / "tokenizer.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            download_plan(str(folder))

    def test_a_download_reports_where_the_files_landed(self) -> None:
        report = preflight("Qwen/Qwen3-4B", network=NETWORK_DOWNLOAD, interpreter=self.probe({
            "outcome": "loaded", "identity": {"class": "X", "vocabulary_size": 1},
            "resolved_files": {"vocab_file": "vocab.json", "merges_file": "merges.txt"}}))
        self.assertTrue(report.ok)
        self.assertEqual(report.downloaded, ["merges.txt", "vocab.json"])
        self.assertTrue(report.destination)
        self.assertIn("No model weights were requested",
                      next(c["detail"] for c in report.checks if c["id"] == "files"))

    def test_the_probe_never_asks_for_a_weight_file(self) -> None:
        """Read the probe source itself: no weight suffix is ever requested."""
        source = tokenizer_preflight.PROBE_SOURCE
        for suffix in tokenizer_preflight.WEIGHT_SUFFIXES:
            self.assertNotIn(suffix, source, suffix)
        self.assertIn("trust_remote_code=False", source)

    def test_the_probe_refuses_repository_code_explicitly(self) -> None:
        self.assertIn("trust_remote_code=False", tokenizer_preflight.PROBE_SOURCE)
        self.assertNotIn("trust_remote_code=True", tokenizer_preflight.PROBE_SOURCE)


class CancellationAndTimeoutTests(PreflightFixture):
    def slow_interpreter(self) -> str:
        return self.interpreter('''
            import sys, time
            sys.stdin.read()
            time.sleep(60)
        ''')

    def test_a_check_that_runs_too_long_is_stopped_and_said_to_be_stopped(self) -> None:
        started = time.monotonic()
        report = preflight("Qwen/Qwen3-4B", interpreter=self.slow_interpreter(), timeout=5.0)
        self.assertLess(time.monotonic() - started, 40)
        self.assertEqual(report.kind, "timeout")
        self.assertFalse(report.ok)
        self.assertIn("was stopped", report.message)

    def test_cancelling_stops_the_probe_and_claims_nothing(self) -> None:
        cancelled = {"value": False}

        def cancel() -> bool:
            cancelled["value"] = True
            return True

        report = preflight("Qwen/Qwen3-4B", interpreter=self.slow_interpreter(),
                           timeout=60.0, cancel=cancel)
        self.assertEqual(report.kind, "cancelled")
        self.assertTrue(cancelled["value"])
        self.assertIn("Nothing further was loaded or downloaded", report.message)
        self.assertEqual(
            next(c["state"] for c in report.checks if c["id"] == "loaded"), "skipped")

    def test_an_interpreter_that_cannot_be_started_is_reported_not_raised(self) -> None:
        report = preflight("Qwen/Qwen3-4B",
                           interpreter=str(self.directory / "absent-interpreter"))
        self.assertEqual(report.kind, "runner-unusable")
        self.assertFalse(report.ok)

    def test_a_runner_without_transformers_names_the_runner_as_the_problem(self) -> None:
        report = preflight("Qwen/Qwen3-4B", interpreter=self.probe({
            "outcome": "runner-unusable", "error": "ModuleNotFoundError: transformers"}))
        self.assertEqual(report.kind, "runner-unusable")
        self.assertIn("runner installation", report.message)

    def test_a_probe_that_prints_nothing_usable_is_a_failure_not_a_pass(self) -> None:
        report = preflight("Qwen/Qwen3-4B", interpreter=self.interpreter('''
            import sys
            sys.stdin.read()
            print("surprise")
        '''))
        self.assertFalse(report.ok)
        self.assertEqual(report.kind, "runner-unusable")


class CommandLineTests(unittest.TestCase):
    """The `omobench tokenizer` entry point, which drives the same preflight."""

    def run_command(self, arguments: list[str]) -> tuple[int, str, str]:
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from omobench.cli import build_parser, run_tokenizer_command

        args = build_parser().parse_args(["tokenizer", *arguments])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = run_tokenizer_command(args)
        return code, out.getvalue(), err.getvalue()

    def test_neither_a_source_nor_a_model_is_refused(self) -> None:
        code, _, stderr = self.run_command([])
        self.assertEqual(code, 2)
        self.assertIn("--source", stderr)

    def checked_source(self, arguments: list[str]) -> str:
        """Which source the command actually hands to the preflight."""
        from unittest.mock import patch

        from omobench.tokenizer_preflight import TokenizerPreflight

        seen = {}

        def record(source, **kwargs):
            seen["source"] = source
            return TokenizerPreflight(ok=True, kind="loaded", message="", source=source,
                                      network=kwargs.get("network", "none"))

        with patch("omobench.tokenizer_preflight.preflight", side_effect=record):
            self.run_command(arguments)
        return seen["source"]

    def test_the_served_identifier_is_what_gets_checked_without_a_source(self) -> None:
        """The runner falls back to it, so this is the case worth checking."""
        self.assertEqual(self.checked_source(["--model", "deepseek-r1-distill-qwen-14b"]),
                         "deepseek-r1-distill-qwen-14b")

    def test_an_explicit_source_wins_over_the_served_identifier(self) -> None:
        self.assertEqual(
            self.checked_source(["--source", "Qwen/Qwen3-4B", "--model", "qwen3-4b"]),
            "Qwen/Qwen3-4B")

    def test_an_unloadable_source_exits_non_zero(self) -> None:
        code, _, _ = self.run_command(["--source", "not a repo id"])
        self.assertEqual(code, 1)

    def test_the_offline_level_is_the_default(self) -> None:
        from omobench.cli import build_parser
        self.assertEqual(build_parser().parse_args(
            ["tokenizer", "--source", "Qwen/Qwen3-4B"]).network, "none")


class RealRunnerTests(unittest.TestCase):
    """One check against the interpreter and library the benchmark really uses."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import transformers  # noqa: F401
        except Exception as error:  # pragma: no cover - depends on the environment
            raise unittest.SkipTest(f"transformers is not importable here: {error}")

    def test_a_nonexistent_local_folder_fails_the_real_loader_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "empty-tokenizer"
            folder.mkdir()
            report = preflight(str(folder), network=NETWORK_NONE, timeout=120.0)
        self.assertFalse(report.ok)
        # A folder has nothing to download; the person needs a different source.
        self.assertEqual(report.kind, "local-unloadable")
        self.assertEqual(report.recovery, "choose-source")
        self.assertEqual(report.network, "none")

    def test_a_real_local_tokenizer_loads_through_the_runners_own_path(self) -> None:
        from tokenizers import Tokenizer, models, pre_tokenizers

        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "wordlevel"
            folder.mkdir()
            vocabulary = {"[UNK]": 0, **{word: index + 1 for index, word in
                                         enumerate(["alpha", "beta", "gamma"])}}
            tokenizer = Tokenizer(models.WordLevel(vocabulary, unk_token="[UNK]"))
            tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
            tokenizer.save(str(folder / "tokenizer.json"))
            (folder / "tokenizer_config.json").write_text(
                json.dumps({"tokenizer_class": "PreTrainedTokenizerFast",
                            "unk_token": "[UNK]"}), encoding="utf-8")
            report = preflight(str(folder), network=NETWORK_NONE, timeout=180.0)
        self.assertTrue(report.ok, report.message + "\n" + report.detail)
        self.assertEqual(report.identity["vocabulary_size"], len(vocabulary))


class ProbeSourceTests(PreflightFixture):
    """Run the real probe source against stand-in libraries.

    The tests above drive the probe's *answers*. These drive the probe itself,
    which is where a bug actually lived: `cardData` is a ModelCardData object
    rather than a plain dict, so reading the publisher's declared base model
    through `isinstance(card, dict)` silently found nothing and offered no
    suggestion for exactly the repositories that most need one.
    """

    def interpreter_with(self, modules: dict[str, str]) -> list[str]:
        """An interpreter that can see `modules`, then runs the real probe.

        The probe is launched with -I, which deliberately ignores PYTHONPATH,
        so the stand-ins are put on the path by a launcher that then executes
        the source it was handed -- the shipped source, not a copy.
        """
        library = self.directory / f"library_{len(modules)}_{abs(hash(tuple(sorted(modules))))%10**8}"
        library.mkdir(parents=True, exist_ok=True)
        for name, body in modules.items():
            (library / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
        launcher = library / "launcher.py"
        launcher.write_text(textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(library)!r})
            # argv is [this launcher, "-I", "-c", <the shipped probe source>]
            source = sys.argv[sys.argv.index("-c") + 1]
            exec(compile(source, "probe", "exec"), {{"__name__": "__main__"}})
        """), encoding="utf-8")
        return [sys.executable, str(launcher)]

    FAILING_TOKENIZER = """
        class AutoTokenizer:
            @staticmethod
            def from_pretrained(source, **kwargs):
                raise OSError(
                    "%s does not appear to have a file named tokenizer.json" % source)
    """

    @staticmethod
    def hub_module(card_expression: str, files: str) -> str:
        # Dedented here, because the stand-in is concatenated with a block that
        # already starts at column zero and a later dedent would then find no
        # common prefix to remove.
        return MODEL_CARD_DATA + textwrap.dedent(f"""
            class _Sibling:
                def __init__(self, name):
                    self.rfilename = name

            class _Info:
                def __init__(self):
                    self.siblings = [_Sibling(name) for name in {files}]
                    self.cardData = {card_expression}

            class HfApi:
                def model_info(self, source, **kwargs):
                    return _Info()
        """)

    GGUF_FILES = '["README.md", "model-Q4_K_M.gguf"]'

    def run_probe(self, card_expression: str, files: str = GGUF_FILES):
        return preflight("unsloth/Some-Model-GGUF", network=NETWORK_METADATA, timeout=120,
                         interpreter=self.interpreter_with({
                             "transformers": self.FAILING_TOKENIZER,
                             "huggingface_hub": self.hub_module(card_expression, files)}))

    def test_a_card_object_rather_than_a_dict_still_yields_the_base_model(self) -> None:
        """What the real library returns, and what the first version missed."""
        report = self.run_probe("""_ModelCardData()""")
        self.assertEqual(report.kind, "gguf-only")
        self.assertIsNotNone(report.suggestion)
        self.assertEqual(report.suggestion["source"], "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B")

    def test_a_plain_dict_card_still_works(self) -> None:
        report = self.run_probe(
            '{"base_model": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"}')
        self.assertEqual(report.suggestion["source"], "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B")

    def test_a_single_item_list_is_the_publisher_naming_one_model(self) -> None:
        report = self.run_probe('{"base_model": ["Qwen/Qwen3-4B"]}')
        self.assertEqual(report.suggestion["source"], "Qwen/Qwen3-4B")

    def test_several_base_models_are_ambiguous_and_suggest_nothing(self) -> None:
        report = self.run_probe('{"base_model": ["Qwen/Qwen3-4B", "Qwen/Qwen3-8B"]}')
        self.assertIsNone(report.suggestion)

    def test_a_card_with_no_declaration_suggests_nothing(self) -> None:
        self.assertIsNone(self.run_probe("{}").suggestion)
        self.assertIsNone(self.run_probe("None").suggestion)

    def test_a_repository_listing_reaches_the_gguf_rule(self) -> None:
        report = self.run_probe("{}", files='["tokenizer.json", "model.gguf"]')
        self.assertEqual(report.kind, "load-failed")
        self.assertIn("tokenizer.json", report.repository_files)

    def test_a_tokenizer_that_loads_reports_its_real_class_and_vocabulary(self) -> None:
        report = preflight("Qwen/Qwen3-4B", network=NETWORK_NONE, timeout=120,
                           interpreter=self.interpreter_with({"transformers": """
            class Qwen2TokenizerFast:
                vocab_size = 151936
                vocab_file = "/fixture/vocab.json"

                def encode(self, text):
                    return list(range(len(text.split())))

            class AutoTokenizer:
                @staticmethod
                def from_pretrained(source, **kwargs):
                    assert kwargs["trust_remote_code"] is False, kwargs
                    return Qwen2TokenizerFast()
        """}))
        self.assertTrue(report.ok, report.message + report.detail)
        self.assertEqual(report.identity, {"class": "Qwen2TokenizerFast",
                                           "vocabulary_size": 151936})
        # Loaded is not used: the sample sentence was encoded too.
        self.assertGreater(report.encoded_tokens, 0)

    def test_a_tokenizer_that_loads_but_cannot_encode_is_not_ready(self) -> None:
        """The workload is built by encoding text, so that is what is tried."""
        report = preflight("Qwen/Qwen3-4B", network=NETWORK_NONE, timeout=120,
                           interpreter=self.interpreter_with({"transformers": """
            class Broken:
                vocab_size = 10

                def encode(self, text):
                    raise ValueError("no vocabulary")

            class AutoTokenizer:
                @staticmethod
                def from_pretrained(source, **kwargs):
                    return Broken()
        """}))
        self.assertFalse(report.ok)
        self.assertEqual(report.kind, "encode-failed")
        self.assertIn("could not encode a sample sentence", report.message)

    def test_the_probe_refuses_repository_code_in_the_call_it_really_makes(self) -> None:
        """Asserted inside the stand-in, on the arguments it is handed."""
        report = preflight("acme/custom", network=NETWORK_NONE, timeout=120,
                           interpreter=self.interpreter_with({"transformers": """
            class AutoTokenizer:
                @staticmethod
                def from_pretrained(source, **kwargs):
                    if kwargs.get("trust_remote_code") is not False:
                        raise AssertionError("repository code was not refused")
                    raise ValueError("Loading acme/custom requires you to execute custom code")
        """}))
        # Refused, and named as refused: offering a download here would be a
        # promise nothing can keep.
        self.assertEqual(report.kind, "needs-remote-code")
        self.assertEqual(report.recovery, "choose-source")

    def test_a_runner_without_transformers_is_named_as_the_problem(self) -> None:
        """Shadow the library rather than relying on it being absent here."""
        report = preflight("Qwen/Qwen3-4B", network=NETWORK_NONE, timeout=120,
                           interpreter=self.interpreter_with({
                               "transformers": "raise ImportError('no transformers here')"}))
        self.assertEqual(report.kind, "runner-unusable")
        self.assertIn("runner installation", report.message)

class FailureCategoryTests(PreflightFixture):
    """Each way a tokenizer can fail to load gets its own name and the right next step.

    The shipped probe runs against stand-ins for transformers and huggingface_hub
    that raise what the real libraries raise -- the hub's own error wrapped in an
    OSError -- so classification is tested on the evidence the backend really
    has, without a network or a download. A download is offered only where one
    can help.
    """

    interpreter_with = ProbeSourceTests.interpreter_with

    HUB = """
        class RepositoryNotFoundError(Exception): pass
        class GatedRepoError(RepositoryNotFoundError): pass
        class LocalEntryNotFoundError(Exception): pass
        class HTTPStatusError(Exception): pass
        class Sibling:
            def __init__(self, name): self.rfilename = name
        class Info:
            def __init__(self, files): self.siblings = [Sibling(f) for f in files]; self.cardData = None
        CACHED = {cached!r}
        LISTING = {listing!r}
        def try_to_load_from_cache(repo, name):
            return "/cache/models--x/snapshots/abc123/" + name if name in CACHED else None
        class HfApi:
            def model_info(self, repo, files_metadata=False):
                if LISTING == "not-found":
                    # The hub's real wording, which mentions gated repositories
                    # without the repository being gated.
                    raise RepositoryNotFoundError(
                        "401 Client Error. Repository Not Found for url. If you are trying to "
                        "access a private or gated repo, make sure you are authenticated.")
                if LISTING == "offline":
                    import socket
                    raise ConnectionError("Max retries exceeded") from socket.gaierror("Name or service not known")
                return Info(LISTING)
    """

    def run_with(self, source: str, network: str, transformers: str, *, cached=(), listing=()):
        hub = self.HUB.format(cached=tuple(cached), listing=listing if isinstance(listing, str) else list(listing))
        return preflight(source, network=network, timeout=120,
                         interpreter=self.interpreter_with({"transformers": transformers, "huggingface_hub": hub}))

    @staticmethod
    def raising(statement: str) -> str:
        return f"""
            import huggingface_hub as hub
            class AutoTokenizer:
                @staticmethod
                def from_pretrained(source, **kwargs):
                    assert kwargs.get("trust_remote_code") is False
                    {statement}
        """

    MISSING = ("raise OSError(\"We couldn't connect to 'https://huggingface.co' to load the files, "
               "and couldn't find them in the cached files.\") from hub.LocalEntryNotFoundError('offline')")

    @staticmethod
    def state(report, check_id: str) -> str:
        return next(check["state"] for check in report.checks if check["id"] == check_id)

    def assert_failure(self, report, kind: str, recovery: str) -> None:
        self.assertFalse(report.ok)
        self.assertEqual(report.kind, kind, report.message)
        self.assertEqual(report.recovery, recovery, report.message)

    def test_absent_from_the_cache_offers_the_download(self) -> None:
        report = self.run_with("Qwen/Qwen3-4B", NETWORK_NONE, self.raising(self.MISSING))
        self.assert_failure(report, "not-cached", "download")
        self.assertIn("cannot tell whether the repository exists", report.message)

    def test_a_partly_cached_tokenizer_still_offers_the_download(self) -> None:
        report = self.run_with("Qwen/Qwen3-4B", NETWORK_NONE, self.raising(self.MISSING),
                               cached=["tokenizer_config.json"])
        self.assert_failure(report, "not-cached", "download")
        self.assertIn("some of its tokenizer files are cached", report.message)

    def test_cached_files_that_will_not_load_are_not_called_missing(self) -> None:
        report = self.run_with("Qwen/Qwen3-4B", NETWORK_NONE,
                               self.raising("raise ValueError('Expecting value: line 1 column 1 (char 0)')"),
                               cached=["tokenizer.json", "tokenizer_config.json"])
        self.assert_failure(report, "cached-unloadable", "choose-source")
        self.assertIn("could not be determined", report.message)
        self.assertNotIn("not on this machine", report.message)
        self.assertIn("Expecting value", report.detail)

    def test_remote_code_offline_is_refused_not_offered_a_download(self) -> None:
        report = self.run_with("acme/custom", NETWORK_NONE, self.raising(
            "raise ValueError('acme/custom requires you to execute custom code; set trust_remote_code=True')"),
            cached=["tokenizer_config.json"])
        self.assert_failure(report, "needs-remote-code", "choose-source")
        self.assertIn("will not help", report.message)

    def test_a_nonexistent_repository_says_so_and_admits_it_may_be_private(self) -> None:
        report = self.run_with("Qwen/Qwen2.5-Coder-14B-Instruct-Typo", NETWORK_DOWNLOAD, self.raising(
            "raise OSError('is not a valid model identifier listed on huggingface.co. If this is a "
            "private repository, make sure to pass a token') from hub.RepositoryNotFoundError('401')"),
            listing="not-found")
        self.assert_failure(report, "not-found", "choose-source")
        self.assertIn("private", report.message)

    def test_a_gguf_only_repository_is_not_offered_a_download(self) -> None:
        report = self.run_with("acme/Model-GGUF", NETWORK_DOWNLOAD, self.raising(
            "raise OSError('acme/Model-GGUF does not appear to have a file named tokenizer_config.json')"),
            listing=["README.md", "model-Q4_K_M.gguf"])
        self.assert_failure(report, "gguf-only", "choose-source")
        self.assertIn("downloading will not", report.message)

    def test_a_network_failure_asks_for_the_connection_and_a_retry(self) -> None:
        report = self.run_with("Qwen/Qwen3-4B", NETWORK_DOWNLOAD, self.raising(
            "import socket\n                    raise OSError('Connection error') "
            "from ConnectionError('Max retries exceeded')"),
            listing="offline")
        self.assert_failure(report, "network", "fix-network")
        self.assertIn("could not be reached", report.message)

    def test_a_gated_repository_asks_for_access_not_a_different_tokenizer(self) -> None:
        report = self.run_with("meta-llama/Llama-3.1-8B-Instruct", NETWORK_DOWNLOAD, self.raising(
            "raise OSError('You are trying to access a gated repo. Access to model "
            "meta-llama/Llama-3.1-8B-Instruct is restricted.') from hub.GatedRepoError('401')"),
            listing=["tokenizer.json", "tokenizer_config.json", "model.safetensors"])
        self.assert_failure(report, "gated", "fix-access")
        self.assertIn("accepted its terms", report.message)

    def test_a_download_cut_off_while_the_lookup_succeeds_is_a_network_problem(self) -> None:
        """The files fail, model_info works: still a retry, not a different source."""
        report = self.run_with("Qwen/Qwen3-4B", NETWORK_DOWNLOAD, self.raising(
            "raise OSError(\"We couldn't connect to 'https://huggingface.co' to load the files, and "
            "couldn't find them in the cached files.\") from ConnectionError('Connection reset by peer')"),
            listing=["tokenizer.json", "tokenizer_config.json", "config.json"])
        self.assert_failure(report, "network", "fix-network")
        self.assertIn("exists, but its tokenizer files could not be downloaded", report.message)
        self.assertEqual(self.state(report, "files"), "ok")
        self.assertIn("couldn't connect", report.detail)

    def test_files_refused_while_the_lookup_succeeds_is_an_access_problem(self) -> None:
        """A 401/403 on the files themselves, with the repository listed."""
        report = self.run_with("acme/private-tokenizer", NETWORK_DOWNLOAD, self.raising(
            "raise OSError('403 Client Error: Forbidden for url: "
            "https://huggingface.co/acme/private-tokenizer/resolve/main/tokenizer.json') "
            "from hub.HTTPStatusError('403 Forbidden')"),
            listing=["tokenizer.json", "tokenizer_config.json"])
        self.assert_failure(report, "access-refused", "fix-access")
        self.assertIn("refused to serve its tokenizer files", report.message)
        self.assertEqual(self.state(report, "files"), "ok")

    def test_a_listed_repository_that_still_will_not_load_says_the_cause_is_unknown(self) -> None:
        report = self.run_with("acme/odd", NETWORK_DOWNLOAD,
                               self.raising("raise OSError('unexpected tokenizer format')"),
                               listing=["tokenizer.json", "config.json"])
        self.assert_failure(report, "load-failed", "choose-source")
        self.assertIn("unexpected tokenizer format", report.detail)

    def test_a_local_folder_without_a_tokenizer_has_nothing_to_download(self) -> None:
        folder = self.directory / "empty-tokenizer"
        folder.mkdir()
        report = self.run_with(str(folder), NETWORK_NONE,
                               self.raising("raise OSError('Can\\'t load tokenizer')"))
        self.assert_failure(report, "local-unloadable", "choose-source")
        self.assertIn("nothing to download", report.message)

    def test_the_download_that_was_offered_then_succeeds(self) -> None:
        """Recovery: offline it is missing; the offered download loads and encodes it."""
        transformers = """
            import huggingface_hub as hub
            class Loaded:
                vocab_size = 151936
                def encode(self, text): return [1, 2, 3, 4]
            class AutoTokenizer:
                @staticmethod
                def from_pretrained(source, **kwargs):
                    assert kwargs.get("trust_remote_code") is False
                    if kwargs.get("local_files_only"):
                        raise OSError("couldn't find them in the cached files") from hub.LocalEntryNotFoundError("x")
                    return Loaded()
        """
        offline = self.run_with("Qwen/Qwen3-4B", NETWORK_NONE, transformers)
        self.assert_failure(offline, "not-cached", "download")
        fetched = self.run_with("Qwen/Qwen3-4B", NETWORK_DOWNLOAD, transformers,
                                cached=["tokenizer_config.json"])
        self.assertTrue(fetched.ok, fetched.message)
        self.assertEqual(fetched.kind, "loaded")
        self.assertIsNone(fetched.recovery)
        self.assertEqual(fetched.encoded_tokens, 4)
        self.assertEqual(fetched.revision, "abc123")


if __name__ == "__main__":
    unittest.main()
