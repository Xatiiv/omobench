"""Both launch routes run the same benchmark.

A guided recipe and a server the contributor runs themselves must not become
two benchmarks that happen to share a name. The guided route starts its
verified server and then delegates to the very same benchmark Controller the
advanced panel uses, so the runner, profile, request format, constraints,
accounting and evidence handling are one implementation, not two.
"""
from __future__ import annotations

import inspect
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omobench import cli
from omobench.ui import Controller
from omobench.ui_managed import ManagedController


ROOT = Path(__file__).resolve().parents[2]
EXTERNAL = {"endpoint": "http://127.0.0.1:1234/v1", "model": "qwen/qwen3-vl-4b:3",
            "profile": "chat-small-v1", "execution_device": "cpu", "device_confirmed": True}
# The configuration a managed run delegates with: a CPU recipe's own model
# against the server this panel just started and verified.
MANAGED = {"endpoint": "http://127.0.0.1:8099", "model": "Qwen/Qwen3-4B",
           "profile": "chat-small-v1", "execution_device": "cpu", "device_confirmed": True}


class ManagedRouteDelegationTests(unittest.TestCase):
    def test_a_managed_run_owns_no_runner_of_its_own(self):
        source = inspect.getsource(ManagedController._run_managed)
        self.assertIn("self.benchmark_controller.start(", source)
        for forbidden in ("build_guidellm_command", "subprocess.", "guidellm"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_managed_route_hands_over_the_same_declared_fields(self):
        source = inspect.getsource(ManagedController._run_managed)
        for field in ('"endpoint"', '"model"', '"profile"', '"execution_device"', '"device_confirmed"'):
            with self.subTest(field=field):
                self.assertIn(field, source)
        # The device a managed CPU recipe declares is the recipe's own, and it
        # is passed through rather than re-derived later.
        self.assertIn('"execution_device": "cpu"', source)


class RunnerInvocationTests(unittest.TestCase):
    """Whatever starts a run, the runner command is built in one place."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in ("benchmarks", "catalog", "schema", "tools"):
            shutil.copytree(ROOT / relative, self.root / relative, ignore=shutil.ignore_patterns("__pycache__"))

    def captured_arguments(self, config):
        controller = Controller(self.root)
        self.addCleanup(controller.close)
        seen = {}

        class StubProcess:
            def __init__(self, arguments, **kwargs):
                seen["arguments"] = arguments
                seen["cwd"] = kwargs.get("cwd")
                seen["env"] = kwargs.get("env")
                self.pid = 4242

            def wait(self):
                return 0

        with patch("omobench.ui.subprocess.Popen", StubProcess), \
             patch("omobench.cli.guidellm_runner_preflight",
                   return_value=cli.RunnerPreflight(ok=True, kind="ok", version=cli.GUIDELLM_VERSION, message="ready")), \
             patch.object(sys, "platform", "linux"):
            job = controller.start(config)
            if controller.worker:
                controller.worker.join(timeout=10)
        return seen, job

    def test_both_routes_build_the_same_runner_command(self):
        external, _ = self.captured_arguments(EXTERNAL)
        managed, _ = self.captured_arguments(MANAGED)

        def flags(arguments):
            return [part.split("=", 1)[0] for part in arguments]

        self.assertEqual(flags(external["arguments"]), flags(managed["arguments"]))
        # The same verified interpreter, never a PATH lookup.
        self.assertEqual(external["arguments"][:4], [sys.executable, "-u", "-m", "omobench.cli"])
        self.assertEqual(managed["arguments"][:4], external["arguments"][:4])
        self.assertIn("run", external["arguments"])
        self.assertIn("--execute", external["arguments"])
        # Only the connection, the identity and this job's own directories differ.
        differing = {a.split("=", 1)[0] for a, b in zip(external["arguments"], managed["arguments"]) if a != b}
        self.assertEqual(differing, {"--endpoint", "--model", "--output-dir", "--telemetry-dir"})

    def test_the_workload_itself_is_fixed_by_the_profile_alone(self):
        external = cli.build_guidellm_command(
            root=ROOT, endpoint="http://127.0.0.1:1234/v1", model="qwen/qwen3-vl-4b:3",
            profile_id="chat-small-v1", raw_output_path=Path("/fixture/run/raw.json"))
        managed = cli.build_guidellm_command(
            root=ROOT, endpoint="http://127.0.0.1:8099", model="Qwen/Qwen3-4B",
            profile_id="chat-small-v1", raw_output_path=Path("/fixture/run/raw.json"))

        def without_backend(command):
            index = command.index("--backend")
            return command[:index] + command[index + 2:]

        # Same data, profile, constraints, seed, metrics and output format: the
        # only difference is the backend's own target and model.
        self.assertEqual(without_backend(external), without_backend(managed))
        for flag in ("--data", "--profile", "--constraint", "--seed", "--metrics", "--disable-console-interactive"):
            with self.subTest(flag=flag):
                self.assertIn(flag, external)
        self.assertEqual(external.count("--constraint"), 3)

    def test_streaming_timeout_and_request_format_come_from_the_profile(self):
        command = cli.build_guidellm_command(
            root=ROOT, endpoint="http://127.0.0.1:1234", model="fixture/model",
            profile_id="chat-small-v1", raw_output_path=Path("/fixture/run/raw.json"))
        backend = command[command.index("--backend") + 1]
        self.assertIn("kind=openai_http", backend)
        self.assertIn("request_format=/v1/chat/completions", backend)
        timeout = cli.profile_request_timeout_seconds(ROOT, "chat-small-v1")
        if timeout is not None:
            self.assertIn(f"timeout={timeout}", backend)

    def test_the_token_targets_and_budgets_are_the_profiles_own(self):
        spec = cli.PROFILE_SPECS["chat-small-v1"]
        command = cli.build_guidellm_command(
            root=ROOT, endpoint="http://127.0.0.1:1234", model="fixture/model",
            profile_id="chat-small-v1", raw_output_path=Path("/fixture/run/raw.json"))
        self.assertIn(f"kind=synthetic_text,prompt_tokens={spec['input_tokens']},output_tokens={spec['output_tokens']}", command)
        self.assertIn(f"kind=max_requests,count={spec['max_requests']}", command)
        self.assertIn(f"kind=max_duration,seconds={spec['max_duration_seconds']}", command)
        # The runner's request budget includes warmup and cooldown; the
        # profile's measured-request target is a separate, smaller number.
        measured = cli.profile_measured_requests(ROOT, "chat-small-v1")
        if measured is not None:
            self.assertLessEqual(measured, spec["max_requests"])

    def test_the_runner_is_the_verified_interpreters_own_installation(self):
        invocation = cli.guidellm_runner_invocation(["guidellm", "run", "--backend", "x"])
        self.assertEqual(invocation[:3], [sys.executable, "-m", "guidellm"])


class DeclaredDeviceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in ("benchmarks", "catalog", "schema", "tools"):
            shutil.copytree(ROOT / relative, self.root / relative, ignore=shutil.ignore_patterns("__pycache__"))

    def test_a_cpu_declaration_is_recorded_as_cpu_from_launch_to_report(self):
        controller = Controller(self.root)
        self.addCleanup(controller.close)

        class StubProcess:
            def __init__(self, arguments, **kwargs):
                self.pid = 4242

            def wait(self):
                return 1  # the run fails; the declaration must survive anyway

        with patch("omobench.ui.subprocess.Popen", StubProcess), \
             patch("omobench.cli.guidellm_runner_preflight",
                   return_value=cli.RunnerPreflight(ok=True, kind="ok", version=cli.GUIDELLM_VERSION, message="ready")), \
             patch.object(sys, "platform", "linux"):
            job = controller.start({**EXTERNAL, "tokenizer": "Qwen/Qwen3-VL-4B-Instruct"})
            if controller.worker:
                controller.worker.join(timeout=10)
        detail = controller.detail(job["id"])
        self.assertEqual(detail["execution_device"], "cpu")
        self.assertEqual(detail["model"], "qwen/qwen3-vl-4b:3")
        self.assertEqual(detail["tokenizer"], "Qwen/Qwen3-VL-4B-Instruct")
        requirements = controller.publication_requirements(job["id"])
        self.assertFalse(requirements["catalogued"])


if __name__ == "__main__":
    unittest.main()
