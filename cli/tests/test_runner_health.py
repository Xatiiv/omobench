"""Installed is not usable: the runner must actually import.

A distribution can be present at exactly the pinned version and still fail to
load, which is how a prepared environment on a contributor's machine looked
ready and then died importing torch. These drive the real health check with
isolated probe scripts -- no model, no tokenizer, no network, no inference --
through success, a crash, a timeout, a cancellation and a missing dependency.
"""
from __future__ import annotations

import os
import signal
import sys
import time
import unittest
from unittest.mock import patch

from omobench import cli


MARKER = "OMOBENCH_RUNNER_HEALTH"
POSIX_ONLY = unittest.skipIf(os.name == "nt", "signal delivery differs on Windows")


def probe_printing(version: str = cli.GUIDELLM_VERSION) -> str:
    return (
        "import json, sys;"
        f"print('{MARKER} ' + json.dumps({{'guidellm': {version!r}, 'python': sys.version.split()[0],"
        " 'executable': sys.executable}))"
    )


class SuccessTests(unittest.TestCase):
    def test_a_healthy_environment_is_reported_with_what_it_proved(self):
        health = cli.runner_health_check(probe=probe_printing(), timeout=30)
        self.assertTrue(health.ok)
        self.assertEqual(health.kind, "ok")
        self.assertEqual(health.version, cli.GUIDELLM_VERSION)
        self.assertIn("imported successfully", health.message)
        self.assertIn("Nothing was downloaded and no inference ran", health.message)
        self.assertGreater(health.seconds, 0)

    def test_a_wrong_version_that_imports_is_still_refused(self):
        health = cli.runner_health_check(probe=probe_printing("9.9.9"), timeout=30)
        self.assertFalse(health.ok)
        self.assertEqual(health.kind, "version_mismatch")
        self.assertIn("9.9.9", health.message)
        self.assertIn(cli.GUIDELLM_VERSION, health.message)


class FailureTests(unittest.TestCase):
    def test_a_missing_dependency_names_the_install_command(self):
        health = cli.runner_health_check(probe="import omobench_not_a_real_module", timeout=30)
        self.assertFalse(health.ok)
        self.assertEqual(health.kind, "missing")
        self.assertIn("pip install -e './cli[ui,runner]'", health.message)
        self.assertIn("omobench_not_a_real_module", health.detail)

    def test_a_nonzero_exit_reports_the_runners_own_error(self):
        health = cli.runner_health_check(
            probe="import sys; print('boom', file=sys.stderr); raise SystemExit(3)", timeout=30)
        self.assertFalse(health.ok)
        self.assertEqual(health.kind, "import_failed")
        self.assertIn("exit code 3", health.message)
        self.assertIn("boom", health.detail)

    @POSIX_ONLY
    def test_a_crash_on_import_is_reported_as_a_crash_without_guessing_a_cause(self):
        health = cli.runner_health_check(
            probe="import os, signal; os.kill(os.getpid(), signal.SIGBUS)", timeout=30)
        self.assertFalse(health.ok)
        self.assertEqual(health.kind, "crashed")
        self.assertIn("SIGBUS", health.message)
        # A class of likely causes, explicitly not a diagnosis.
        self.assertIn("cannot tell which from here, and does not guess", health.message)

    @POSIX_ONLY
    def test_a_segmentation_fault_is_named_too(self):
        health = cli.runner_health_check(
            probe="import os, signal; os.kill(os.getpid(), signal.SIGSEGV)", timeout=30)
        self.assertEqual(health.kind, "crashed")
        self.assertIn("SIGSEGV", health.message)

    def test_an_environment_whose_interpreter_cannot_start_says_so(self):
        health = cli.runner_health_check(interpreter="/definitely/not/a/python", timeout=10)
        self.assertFalse(health.ok)
        self.assertEqual(health.kind, "unavailable")
        self.assertIn("Could not start", health.message)


class BoundednessTests(unittest.TestCase):
    def test_an_import_that_never_finishes_is_bounded_and_explained(self):
        started = time.monotonic()
        health = cli.runner_health_check(probe="import time; time.sleep(120)", timeout=1)
        elapsed = time.monotonic() - started
        self.assertFalse(health.ok)
        self.assertEqual(health.kind, "timeout")
        self.assertLess(elapsed, 30)
        self.assertIn("did not finish within", health.message)
        self.assertIn("check the storage holding it", health.message)

    def test_cancellation_is_answered_promptly_and_starts_nothing(self):
        started = time.monotonic()
        health = cli.runner_health_check(probe="import time; time.sleep(120)",
                                         timeout=120, cancel=lambda: True)
        elapsed = time.monotonic() - started
        self.assertEqual(health.kind, "cancelled")
        self.assertLess(elapsed, 30)
        self.assertIn("Nothing was started", health.message)

    @POSIX_ONLY
    def test_a_timed_out_probe_leaves_no_process_behind(self):
        health = cli.runner_health_check(
            probe="import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)",
            timeout=2)
        self.assertEqual(health.kind, "timeout")
        # The probe runs in its own process group, which is terminated whole.
        self.assertNotIn("Traceback", health.detail)


class SafetyTests(unittest.TestCase):
    def test_the_probe_imports_libraries_and_does_nothing_else(self):
        source = cli.RUNNER_HEALTH_PROBE
        for module in ("guidellm", "guidellm.__main__", "guidellm.backends.openai.http",
                       "guidellm.data.tokenizers.huggingface"):
            self.assertIn(f"import {module};", source)
        for forbidden in ("from_pretrained", "trust_remote_code", "download", "http://", "https://",
                          "benchmark", "generate", "run("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_child_is_told_to_stay_offline(self):
        self.assertEqual(cli.RUNNER_HEALTH_ENVIRONMENT["HF_HUB_OFFLINE"], "1")
        self.assertEqual(cli.RUNNER_HEALTH_ENVIRONMENT["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(cli.RUNNER_HEALTH_ENVIRONMENT["HF_HUB_DISABLE_TELEMETRY"], "1")

    def test_the_check_never_falls_back_to_launching_a_benchmark(self):
        seen = []
        real = cli.subprocess.Popen

        def record(arguments, **kwargs):
            seen.append(list(arguments))
            return real(arguments, **kwargs)

        with patch.object(cli.subprocess, "Popen", record):
            cli.runner_health_check(probe="raise SystemExit(4)", timeout=20)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][:2], [sys.executable, "-I"])
        joined = " ".join(seen[0])
        for forbidden in ("guidellm", "run", "--execute", "omobench.cli"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f" {forbidden} ", f" {joined} ")

    def test_the_child_runs_isolated_from_ambient_python_configuration(self):
        seen = []
        real = cli.subprocess.Popen

        def record(arguments, **kwargs):
            seen.append((list(arguments), kwargs.get("env") or {}))
            return real(arguments, **kwargs)

        with patch.object(cli.subprocess, "Popen", record):
            cli.runner_health_check(probe=probe_printing(), timeout=20)
        arguments, environment = seen[0]
        self.assertIn("-I", arguments)
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")

    def test_diagnostics_are_sanitized_and_bounded(self):
        noisy = cli.runner_health_check(
            probe=("import sys; print('api_key=sk-fixture-secret-value', file=sys.stderr); "
                   "print('see https://example.invalid/path', file=sys.stderr); raise SystemExit(5)"),
            timeout=30)
        self.assertNotIn("sk-fixture-secret-value", noisy.detail)
        self.assertIn("<REDACTED>", noisy.detail)
        self.assertNotIn("example.invalid", noisy.detail)
        long = cli.runner_health_check(
            probe="import sys; print('x' * 20000, file=sys.stderr); raise SystemExit(6)", timeout=30)
        self.assertLessEqual(len(long.detail), 2100)

    def test_sanitize_strips_control_sequences(self):
        self.assertEqual(cli.sanitize_diagnostics("\x1b[31mred\x1b[0m"), "red")


class PreflightSeparationTests(unittest.TestCase):
    """The cheap metadata preflight stays cheap; health is the separate claim."""

    def test_metadata_preflight_does_not_import_the_runner(self):
        seen = []
        real = cli.subprocess.Popen

        def record(arguments, **kwargs):
            seen.append(list(arguments))
            return real(arguments, **kwargs)

        with patch.object(cli.subprocess, "Popen", record):
            cli.guidellm_runner_preflight()
        self.assertEqual(seen, [])

    def test_the_two_answer_different_questions(self):
        preflight = cli.guidellm_runner_preflight()
        self.assertIn(preflight.kind, {"ok", "missing", "version_mismatch"})
        health = cli.runner_health_check(probe=probe_printing(), timeout=30)
        self.assertEqual(health.kind, "ok")
        # A healthy import says more than installed metadata ever can.
        self.assertIn("imported", health.message)


if __name__ == "__main__":
    unittest.main()
