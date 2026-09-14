from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from omobench.cli import package_local_run
from omobench.ui import Controller, UIError
from omobench.ui_submission import (
    BUNDLE_FILES, GitHub, bundle_archive, bundle_snapshot, description, destination, prepare_submission,
    submit_pull_request, validate_bundle,
)
from test_cli import base_package_args, build_staged_run_directory
from test_ui import ROOT, make_root


class FakeGitHub:
    """In-memory GitHub protocol fixture; never contacts GitHub."""
    def __init__(self):
        self.calls = []
        self.ref = None
        self.pulls = []
        self.fail_once = False
        self.existing_directory = False
        self.repo = {"full_name": "example/omobench", "default_branch": "main",
                     "owner": {"login": "example"}, "permissions": {"push": True}}

    def repository(self):
        return self.repo

    def optional(self, path):
        self.calls.append((path, None))
        if "/git/ref/" in path:
            return self.ref
        return [{"name": "existing"}] if self.existing_directory else None

    def api(self, path, payload=None):
        self.calls.append((path, payload))
        if "/commits/main" in path:
            return {"sha": "base", "commit": {"tree": {"sha": "base-tree"}}}
        if path.endswith("/git/blobs"):
            return {"sha": "blob"}
        if path.endswith("/git/trees"):
            return {"sha": "tree"}
        if path.endswith("/git/commits"):
            return {"sha": "new-commit"}
        if path.endswith("/git/refs"):
            self.ref = {"object": {"sha": payload["sha"]}}
            return self.ref
        if "/pulls?" in path:
            return self.pulls
        if path.endswith("/pulls"):
            if self.fail_once:
                self.fail_once = False
                raise UIError("Temporary PR failure")
            self.pulls = [{"html_url": "https://github.com/example/omobench/pull/5"}]
            return self.pulls[0]
        raise AssertionError(path)


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        make_root(self.root)
        self.run = build_staged_run_directory(self.root)
        args = base_package_args(self.run)
        with patch("omobench.cli.find_repository_root", return_value=self.root), patch("omobench.cli.current_git_commit", return_value="a" * 40), redirect_stdout(io.StringIO()):
            self.assertEqual(package_local_run(args), 0)

    def tearDown(self):
        self.temporary.cleanup()

    def change_result(self, change):
        path = self.run / "result.json"
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    def test_real_validator_gates_catalog_revisions_and_artifact_hashes(self):
        self.assertEqual(validate_bundle(self.root, self.run), [])
        original = (self.run / "result.json").read_bytes()
        for change, expected in [
            (lambda r: r["model"].update(id="unlisted/model"), "catalog"),
            (lambda r: r["model"].update(revision="main"), "mutable"),
            (lambda r: r["runtime"].update(framework="openai-compatible-other", server_description="   "), "server_description"),
            (lambda r: r["artifacts"]["runner_stdout_log"].update(path="../outside.log"), "inside"),
        ]:
            with self.subTest(expected=expected):
                self.change_result(change)
                self.assertIn(expected, "\n".join(validate_bundle(self.root, self.run)))
                (self.run / "result.json").write_bytes(original)
        (self.run / "guidellm.stdout.log").write_text("changed")
        with self.assertRaisesRegex(UIError, "hash mismatch"):
            bundle_archive(self.root, self.run)

    def test_duplicate_registry_bundle_is_rejected(self):
        shutil.copytree(self.run, self.root / "results" / "existing")
        self.assertIn("duplicate run_id", "\n".join(validate_bundle(self.root, self.run)))

    def test_zip_contains_exact_evidence_bytes_and_no_ui_or_extra_files(self):
        (self.run / "secret.txt").write_text("not part of evidence")
        (self.run / "job.json").write_text("local UI metadata")
        result, contents, _ = bundle_snapshot(self.root, self.run)
        zipped = bundle_archive(self.root, self.run)
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            expected = {f"{destination(result)}/{name}" for name in BUNDLE_FILES}
            self.assertEqual(set(archive.namelist()), expected)
            for name, content in contents.items():
                self.assertEqual(archive.read(f"{destination(result)}/{name}"), content)

    def test_preview_and_download_work_without_gh(self):
        with patch("omobench.ui_submission.GitHub.repository", side_effect=UIError("Install GitHub CLI")):
            preview = prepare_submission(self.root, self.run)
        self.assertFalse(preview["can_submit"])
        self.assertIn("Install", preview["message"])
        self.assertEqual(len(preview["files"]), 5)
        self.assertIn("chat-small-v1", preview["title"])
        self.assertTrue(bundle_archive(self.root, self.run).startswith(b"PK"))

    def test_review_digest_and_destination_checked_before_remote_mutation(self):
        github = FakeGitHub()
        _, _, digest = bundle_snapshot(self.root, self.run)
        with patch("omobench.ui_submission.GitHub", return_value=github):
            with self.assertRaisesRegex(UIError, "destination"):
                submit_pull_request(self.root, self.run, "different/repo", digest, {}, lambda: None)
            self.change_result(lambda r: r["model"].update(quantization="Q8_0"))
            with self.assertRaisesRegex(UIError, "changed"):
                submit_pull_request(self.root, self.run, "example/omobench", digest, {}, lambda: None)
        self.assertFalse(github.calls)

    def test_pr_uploads_only_five_blobs_and_never_updates_main_or_local_files(self):
        github = FakeGitHub()
        state = {}
        _, contents, digest = bundle_snapshot(self.root, self.run)
        with patch("omobench.ui_submission.GitHub", return_value=github):
            submit_pull_request(self.root, self.run, "example/omobench", digest, state, lambda: None)
        self.assertEqual(state["pull_request_url"], "https://github.com/example/omobench/pull/5")
        self.assertEqual(len([p for p, _ in github.calls if p.endswith("/git/blobs")]), 5)
        ref = next(data for path, data in github.calls if path.endswith("/git/refs"))
        self.assertEqual(ref["ref"], "refs/heads/submit/run-fixture-1234")
        tree = next(data for path, data in github.calls if path.endswith("/git/trees"))
        self.assertEqual(tree["base_tree"], "base-tree")
        self.assertEqual(len(tree["tree"]), 5)
        self.assertTrue(all(item["path"].startswith("results/") for item in tree["tree"]))
        self.assertEqual(list((self.root / "results").iterdir()), [])
        for name, content in contents.items():
            self.assertEqual((self.run / name).read_bytes(), content)

    def test_retry_reuses_branch_and_existing_pr(self):
        github = FakeGitHub()
        github.fail_once = True
        state = {}
        _, _, digest = bundle_snapshot(self.root, self.run)
        with patch("omobench.ui_submission.GitHub", return_value=github):
            with self.assertRaisesRegex(UIError, "Temporary"):
                submit_pull_request(self.root, self.run, "example/omobench", digest, state, lambda: None)
            self.assertTrue(state["branch_created"])
            submit_pull_request(self.root, self.run, "example/omobench", digest, state, lambda: None)
            submit_pull_request(self.root, self.run, "example/omobench", digest, state, lambda: None)
        self.assertEqual(len([p for p, _ in github.calls if p.endswith("/git/refs")]), 1)
        self.assertEqual(len([p for p, _ in github.calls if p.endswith("/git/commits")]), 1)
        self.assertEqual(len(github.pulls), 1)

    def test_remote_collisions_are_never_overwritten(self):
        _, _, digest = bundle_snapshot(self.root, self.run)
        for branch_collision in [True, False]:
            github = FakeGitHub()
            if branch_collision:
                github.ref = {"object": {"sha": "someone-elses-work"}}
            else:
                github.existing_directory = True
            with patch("omobench.ui_submission.GitHub", return_value=github), self.assertRaisesRegex(UIError, "overwritten"):
                submit_pull_request(self.root, self.run, "example/omobench", digest, {}, lambda: None)
            self.assertTrue(all(data is None for _, data in github.calls))

    def test_gh_uses_structured_stdin_and_does_not_expose_errors(self):
        completed = type("Result", (), {"returncode": 0, "stdout": '{"ok": true}', "stderr": ""})()
        github = GitHub(self.root)
        payload = {"body": "actual\nnewlines and `literal` $(no shell)"}
        with patch("omobench.ui_submission.subprocess.run", return_value=completed) as run:
            github.api("repos/example/omobench/pulls", payload)
            args, kwargs = run.call_args
            self.assertIn("--input", args[0])
            self.assertEqual(json.loads(kwargs["input"]), payload)
            self.assertNotIn("shell", kwargs)
            completed.returncode, completed.stderr = 1, "token=secret-do-not-return"
            with self.assertRaises(UIError) as raised:
                github.api("repos/example/omobench")
            self.assertNotIn("secret", str(raised.exception))

    def test_controller_gpu_packaging_invokes_cli_and_validates_output(self):
        controller = Controller(self.root)
        controller.environment["PYTHONPATH"] = str(ROOT / "cli" / "src")
        job_id = "job-" + "a" * 16
        directory = controller.storage / job_id
        directory.mkdir()
        new_run = directory / self.run.name
        shutil.move(self.run, new_run)
        self.run = new_run
        controller.jobs[job_id] = {"id": job_id, "created_at": "2026-09-10T12:00:00Z", "status": "completed",
                                   "model": "test-alias", "profile": "chat-small-v1", "execution_device": "gpu",
                                   "summary": {"results": {}}, "validation": None, "submission": None}
        data = {"model": "Qwen/Qwen3-4B", "model_revision": "abc123", "framework": "llama.cpp",
                "framework_version": "test-build", "submitter": "tester", "quantization": "Q4_0",
                "runtime_args": ["--ctx-size=2048"]}
        packaged = controller.package(job_id, data)
        self.assertTrue(packaged["validation"]["valid"], packaged)
        result = json.loads((self.run / "result.json").read_text())
        self.assertEqual(result["runtime"]["arguments"], ["--ctx-size=2048"])
        bad = controller.package(job_id, {**data, "model_revision": "latest"})
        self.assertFalse(bad["validation"]["valid"])
        controller.jobs[job_id]["submission"] = {"commit_sha": "abc"}
        with self.assertRaisesRegex(UIError, "Submission has started"):
            controller.package(job_id, data)
        with self.assertRaisesRegex(UIError, "confirm submission"):
            controller.submit(job_id, {})
        controller.close()

    def test_controller_cpu_packaging_is_enabled_and_reviewable(self) -> None:
        """CPU results are first-class: the panel's package/review/submit workflow must
        accept them the same way it already accepts GPU results."""
        controller = Controller(self.root)
        controller.environment["PYTHONPATH"] = str(ROOT / "cli" / "src")
        job_id = "job-" + "b" * 16
        directory = controller.storage / job_id
        directory.mkdir()
        shutil.rmtree(self.run)  # setUp already staged an unrelated GPU-oriented run at this fixed path
        cpu_run_source = build_staged_run_directory(
            self.root,
            environment={"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64},
        )
        cpu_run = directory / cpu_run_source.name
        shutil.move(cpu_run_source, cpu_run)
        controller.jobs[job_id] = {
            "id": job_id,
            "created_at": "2026-09-10T12:00:00Z",
            "status": "completed",
            "model": "test-alias",
            "profile": "chat-small-v1",
            "execution_device": "cpu",
            "summary": {"results": {}},
            "validation": None,
            "submission": None,
        }
        data = {
            "model": "Qwen/Qwen3-4B",
            "model_revision": "abc123",
            "framework": "llama.cpp",
            "framework_version": "test-build",
            "submitter": "tester",
            "quantization": "Q4_0",
            "runtime_args": ["--ctx-size=2048"],
        }
        packaged = controller.package(job_id, data)
        self.assertTrue(packaged["validation"]["valid"], packaged)
        result = json.loads((cpu_run / "result.json").read_text())
        self.assertEqual(result["execution_device"], "cpu")
        self.assertEqual(result["hardware"]["gpus"], [])
        self.assertIsNone(result["hardware"]["driver_version"])

        preview = prepare_submission(self.root, cpu_run)
        self.assertEqual(len(preview["files"]), 5)
        self.assertIn("CPU", preview["body"])
        controller.close()

    def test_controller_rejects_gpu_memory_override_for_a_cpu_job(self) -> None:
        controller = Controller(self.root)
        controller.environment["PYTHONPATH"] = str(ROOT / "cli" / "src")
        job_id = "job-" + "c" * 16
        directory = controller.storage / job_id
        directory.mkdir()
        shutil.rmtree(self.run)  # setUp already staged an unrelated GPU-oriented run at this fixed path
        cpu_run_source = build_staged_run_directory(
            self.root,
            environment={"operating_system": "Test Linux", "cpu": "Test CPU", "ram_gb": 64},
        )
        cpu_run = directory / cpu_run_source.name
        shutil.move(cpu_run_source, cpu_run)
        controller.jobs[job_id] = {
            "id": job_id,
            "created_at": "2026-09-10T12:00:00Z",
            "status": "completed",
            "model": "test-alias",
            "profile": "chat-small-v1",
            "execution_device": "cpu",
            "summary": {"results": {}},
            "validation": None,
            "submission": None,
        }
        data = {
            "model": "Qwen/Qwen3-4B",
            "model_revision": "abc123",
            "framework": "llama.cpp",
            "framework_version": "test-build",
            "submitter": "tester",
            "gpu_memory_gb": 16.0,
        }
        with self.assertRaisesRegex(UIError, "does not apply to a CPU run"):
            controller.package(job_id, data)
        self.assertFalse((cpu_run / "result.json").exists())
        controller.close()

    def test_pull_request_description_labels_execution_device(self) -> None:
        gpu_result, _, _ = bundle_snapshot(self.root, self.run)
        self.assertEqual(gpu_result["execution_device"], "gpu")
        _, gpu_body = description(gpu_result, "results/example")
        self.assertIn("Execution device: GPU", gpu_body)
        self.assertIn("confirms GPU execution", gpu_body)

        cpu_result = dict(gpu_result, execution_device="cpu")
        _, cpu_body = description(cpu_result, "results/example")
        self.assertIn("Execution device: CPU", cpu_body)
        self.assertIn("confirms CPU execution", cpu_body)


if __name__ == "__main__":
    unittest.main()
