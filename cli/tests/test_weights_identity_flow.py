"""A model file's identity, from identification to the website's comparison.

Before this, the identity a panel found lived only in the browser: a reload or
a reopened run lost it, the manual folder control was not the folder
"Identify model file" read, and packaging accepted any string as the weights
revision. The published run-c758acaf0c4e is the result: a hand-typed revision
and no model.weights at all.

These tests follow one identity through the real panel controller, the real
packager, the real registry validator and the website's own reader and
comparison (through Node). Every model file is a LABELLED FIXTURE: a GGUF
header of a few kilobytes, never real weights, and nothing here is published.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from omobench.ui import Controller, UIError

from test_local_models import write_gguf
from test_model_resolution import lmstudio_folder
from test_ui_provenance import PACKAGE_FORM, SAME_HOST_SERVING, PanelFixture

ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).resolve().parent / "weights_identity_harness.mjs"
Q4 = "lmstudio-community/Qwen2.5-Coder-14B-Instruct-GGUF/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"
Q8 = "lmstudio-community/Qwen2.5-Coder-14B-Instruct-GGUF/Qwen2.5-Coder-14B-Instruct-Q8_0.gguf"
SHARDS = [f"someone/Big-70B-GGUF/Big-70B-Q4_K_M-{index:05d}-of-00002.gguf" for index in (1, 2)]
# What the form sends when the revision is left for the identified file to fill.
FORM = {**PACKAGE_FORM, "model_revision": "", "serving": SAME_HOST_SERVING}


class ModelsFolderFixture(PanelFixture):
    """A panel with a labelled LM Studio models folder saved as its Model folder."""

    def setUp(self) -> None:
        super().setUp()
        self.models = lmstudio_folder(self.root.parent / f"{self.root.name}-models")
        self.addCleanup(shutil.rmtree, self.models, ignore_errors=True)
        (self.models / "README-TEST-FIXTURE.txt").write_text(
            "LABELLED TEST FIXTURE: GGUF headers only, no weights.\n", encoding="utf-8")
        for name in SHARDS:
            write_gguf(self.models / name, {"general.name": "Big 70B fixture"})
        self.controller.update_settings({"model_root": str(self.models)})

    def qwen_run(self) -> str:
        """A finished run of the LM Studio Qwen alias, whose files are in the folder."""
        job_id = self.completed_job(bridged=False)
        with self.controller.lock:
            job = self.controller.jobs[job_id]
            job.update(model="qwen2.5-coder-14b-instruct", server_metadata={"quantization": "Q4_K_M"})
            self.controller._save(job)
        return job_id


class WeightsIdentityFlowTests(ModelsFolderFixture):
    # --- helpers -------------------------------------------------------------

    def stage(self, job_id: str) -> str:
        """A finished run at a job id of our choosing (completed_job uses one fixed id)."""
        staged = self.completed_job(bridged=False)
        if staged == job_id:
            return job_id
        with self.controller.lock:
            job = self.controller.jobs.pop(staged)
        source = self.controller._directory(staged)
        target = self.controller._directory(job_id)
        shutil.move(str(source), str(target))
        run = target / "run-fixture-1234"
        job.update(id=job_id, run_path=str(run))
        with self.controller.lock:
            self.controller.jobs[job_id] = job
            self.controller._save(job)
        return job_id

    def record_at_start(self, job_id: str, files: list[str]) -> None:
        with self.controller.lock:
            self.controller.jobs[job_id]["model_files_at_start"] = \
                self.controller._model_files_at_start(files)
            self.controller._save(self.controller.jobs[job_id])

    def result(self, job_id: str) -> dict:
        return json.loads((self.controller.run_directory(job_id) / "result.json").read_text(encoding="utf-8"))

    def site(self, *job_ids: str) -> dict:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is not available")
        paths = [str(self.controller.run_directory(job_id) / "result.json") for job_id in job_ids]
        completed = subprocess.run([node, str(HARNESS), str(ROOT), *paths], capture_output=True,
                                   text=True, encoding="utf-8", timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    # --- identification through to the website --------------------------------

    def test_a_file_recorded_at_run_start_is_verified_packaged_and_read_by_the_site(self) -> None:
        job_id = self.completed_job(bridged=False)
        self.record_at_start(job_id, [Q4])
        identified = self.controller.identify_weights(job_id, {})
        self.assertEqual(identified["provenance"], "verified-at-run-start")
        detail = self.controller.package(job_id, dict(FORM))  # revision left empty
        self.assertTrue(detail["validation"]["valid"], detail["validation"]["errors"])
        result = self.result(job_id)
        self.assertEqual(result["schema_version"], "1.2")
        self.assertEqual(result["model"]["revision"], identified["revision"])
        weights = result["model"]["weights"]
        self.assertEqual((weights["kind"], weights["identity"], weights["provenance"]),
                         ("local-files", "content-hash", "verified-at-run-start"))
        self.assertEqual([entry["name"] for entry in weights["content"]["files"]],
                         ["Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"])
        site = self.site(job_id)["records"][0]
        self.assertEqual(site["identity"]["kind"], "file-fingerprint")
        self.assertEqual(site["weights"]["provenance"], "verified-at-run-start")

    def test_a_sharded_model_is_one_identity_over_every_shard(self) -> None:
        job_id = self.completed_job(bridged=False)
        identified = self.controller.identify_weights(job_id, {"files": SHARDS, "confirm": True})
        self.assertEqual(identified["provenance"], "contributor-selected")
        self.controller.package(job_id, dict(FORM))
        files = self.result(job_id)["model"]["weights"]["content"]["files"]
        self.assertEqual(len(files), 2)
        self.assertEqual(self.result(job_id)["model"]["weights"]["provenance"], "contributor-selected")

    # --- one folder, two places to enter it ------------------------------------

    def test_identify_reads_the_folder_typed_in_review_when_none_is_saved(self) -> None:
        self.controller.update_settings({"model_root": ""})
        job_id = self.qwen_run()
        missing = self.controller.identify_weights(job_id, {})
        self.assertEqual(missing["state"], "no-root")
        self.assertIn("Folder containing the model file", missing["message"])
        self.assertIn("Model folder", missing["message"])
        found = self.controller.identify_weights(job_id, {"root": str(self.models)})
        self.assertEqual(found["state"], "choose")
        self.assertTrue(found["groups"])
        identified = self.controller.identify_weights(
            job_id, {"root": str(self.models), "files": [Q4], "confirm": True})
        self.assertEqual(identified["state"], "identified")
        # And it flows into packaging with nothing typed again.
        detail = self.controller.package(job_id, dict(FORM))
        self.assertTrue(detail["validation"]["valid"], detail["validation"]["errors"])

    def test_the_saved_model_folder_offers_the_files_to_choose(self) -> None:
        job_id = self.qwen_run()
        answer = self.controller.identify_weights(job_id, {})
        self.assertEqual(answer["state"], "choose")
        self.assertTrue(any(group["files"] == [Q4] for group in answer["groups"]))

    def test_a_chosen_file_needs_confirmation(self) -> None:
        job_id = self.completed_job(bridged=False)
        with self.assertRaisesRegex(UIError, "Confirm"):
            self.controller.identify_weights(job_id, {"files": [Q4]})

    # --- saved, reopened, changed ---------------------------------------------

    def test_a_reopened_run_keeps_its_identity_and_packages_it(self) -> None:
        job_id = self.completed_job(bridged=False)
        self.record_at_start(job_id, [Q4])
        identified = self.controller.identify_weights(job_id, {})
        # The page is gone: a new panel process reads the run back from disk.
        self.controller.close()
        self.controller = Controller(self.root)
        self.addCleanup(self.controller.close)
        stored = self.controller.detail(job_id)["weights_identity"]
        self.assertEqual(stored["revision"], identified["revision"])
        self.assertEqual(stored["provenance"], "verified-at-run-start")
        self.controller.package(job_id, dict(FORM))
        self.assertEqual(self.result(job_id)["model"]["weights"]["provenance"], "verified-at-run-start")

    def test_a_file_changed_after_it_was_identified_is_refused(self) -> None:
        job_id = self.completed_job(bridged=False)
        self.controller.identify_weights(job_id, {"files": [Q4], "confirm": True})
        path = self.models / Q4
        path.write_bytes(path.read_bytes() + b"\0")
        with self.assertRaisesRegex(UIError, "has changed since"):
            self.controller.package(job_id, dict(FORM))
        self.assertFalse((self.controller.run_directory(job_id) / "result.json").exists())

    def test_a_file_missing_after_it_was_identified_is_refused(self) -> None:
        job_id = self.completed_job(bridged=False)
        self.controller.identify_weights(job_id, {"files": [Q4], "confirm": True})
        (self.models / Q4).unlink()
        with self.assertRaisesRegex(UIError, "Identify the model file again"):
            self.controller.package(job_id, dict(FORM))

    def test_a_chosen_file_stays_chosen_even_when_it_was_also_recorded_at_start(self) -> None:
        job_id = self.completed_job(bridged=False)
        self.record_at_start(job_id, [Q4])
        identified = self.controller.identify_weights(job_id, {"files": [Q4], "confirm": True})
        self.assertEqual(identified["provenance"], "contributor-selected")
        self.assertFalse(identified["verified"])

    # --- what packaging refuses, early ------------------------------------------

    def test_a_different_typed_revision_is_refused_naming_both(self) -> None:
        job_id = self.completed_job(bridged=False)
        identified = self.controller.identify_weights(job_id, {"files": [Q4], "confirm": True})
        typed = "f0caced9823778600314115bf0b96834a0e82f6024e2af96625147626a1d8158"
        with self.assertRaises(UIError) as raised:
            self.controller.package(job_id, {**FORM, "model_revision": typed})
        self.assertIn(typed, str(raised.exception))
        self.assertIn(identified["revision"], str(raised.exception))

    def test_a_path_revision_is_refused_before_anything_is_written(self) -> None:
        job_id = self.completed_job(bridged=False)
        for revision in ("D:\\OmoBench\\lmstudio-models\\Qwen", "/home/me/models", "file:///C:/m.gguf", "HEAD"):
            with self.subTest(revision=revision), self.assertRaises(UIError):
                self.controller.package(job_id, {**FORM, "model_revision": revision})
        self.assertFalse((self.controller.run_directory(job_id) / "provenance.json").exists())

    def test_without_a_typed_identity_the_bundle_is_reviewable_but_not_submittable(self) -> None:
        job_id = self.completed_job(bridged=False)
        # A 64-character string typed as the revision, as in run-c758acaf0c4e.
        typed = "f0caced9823778600314115bf0b96834a0e82f6024e2af96625147626a1d8158"
        detail = self.controller.package(job_id, {**FORM, "model_revision": typed})
        self.assertFalse(detail["validation"]["valid"])
        self.assertTrue(detail["validation"]["reviewable"])
        self.assertIn("new submission: model.weights", " ".join(detail["validation"]["errors"]))
        requirements = {item["id"]: item for item in self.controller.requirement_checks(
            self.controller.detail(job_id), {**FORM, "model_revision": typed})}
        self.assertEqual(requirements["weights-identity"]["state"], "attention")
        self.assertEqual(requirements["weights-identity"]["blocks"], "submit")

    # --- the comparison --------------------------------------------------------

    def test_two_runs_of_the_same_file_compare_and_a_different_file_does_not(self) -> None:
        first = self.stage("job-" + "1" * 16)
        self.controller.identify_weights(first, {"files": [Q4], "confirm": True})
        self.controller.package(first, dict(FORM))
        second = self.stage("job-" + "2" * 16)
        self.controller.identify_weights(second, {"files": [Q4], "confirm": True})
        self.controller.package(second, dict(FORM))
        third = self.stage("job-" + "3" * 16)
        self.controller.identify_weights(third, {"files": [Q8], "confirm": True})
        self.controller.package(third, dict(FORM))
        # The first run against the second (same file), then against the third (Q8_0).
        same_file, other_file = self.site(first, second, third)["comparisons"]
        self.assertEqual(same_file["blockers"], [])
        self.assertNotEqual(same_file["suitability"], "unsuitable")
        self.assertTrue(any("chosen by its contributor" in text for text in same_file["differences"]))
        self.assertIn("Different weights", other_file["blockers"])


if __name__ == "__main__":
    unittest.main()


class FolderPathScopeTests(ModelsFolderFixture):
    """Exactly what the two folder fields accept: a Windows drive path (read
    through WSL's default /mnt mount) and an absolute Linux/WSL path. Anything
    else is refused with what to enter instead. Drive letters are mapped onto a
    labelled stand-in mount root here, never onto the machine's real drives."""

    def setUp(self) -> None:
        super().setUp()
        from omobench import local_models
        mount = self.root.parent / f"{self.root.name}-mount"
        mount.mkdir()
        self.addCleanup(shutil.rmtree, mount, ignore_errors=True)
        (mount / "d").symlink_to(self.models, target_is_directory=True)
        original = local_models.WINDOWS_DRIVE_MOUNT_ROOT
        local_models.WINDOWS_DRIVE_MOUNT_ROOT = str(mount)
        self.addCleanup(setattr, local_models, "WINDOWS_DRIVE_MOUNT_ROOT", original)
        self.job_id = self.qwen_run()  # one finished run; the fixture makes it once

    def fields(self, value):
        """The same folder through the saved Model folder, Identify, and the manual listing."""
        job_id = self.job_id
        self.controller.update_settings({"model_root": value})
        saved = self.controller.identify_weights(job_id, {})
        self.controller.update_settings({"model_root": ""})
        typed = self.controller.identify_weights(job_id, {"root": value})
        return saved, typed

    def test_windows_and_wsl_paths_work_in_both_fields(self) -> None:
        for value in ("D:\\", "d:/", str(self.models)):  # D:\ maps onto the stand-in mount
            with self.subTest(value=value):
                saved, typed = self.fields(value)
                self.assertEqual(saved["state"], "choose")
                self.assertEqual(typed["state"], "choose")
        listing = self.controller.weight_identity(
            {"directory": r"D:\lmstudio-community\Qwen2.5-Coder-14B-Instruct-GGUF"})
        self.assertIn("Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf", [c["name"] for c in listing["candidates"]])

    def refusal(self, value) -> str:
        messages = []
        with self.assertRaises(UIError) as saved:
            self.controller.update_settings({"model_root": value})
        messages.append(str(saved.exception))
        job_id = self.job_id
        with self.assertRaises(UIError) as typed:
            self.controller.identify_weights(job_id, {"root": value})
        messages.append(str(typed.exception))
        with self.assertRaises(UIError) as listed:
            self.controller.weight_identity({"directory": value})
        messages.append(str(listed.exception))
        self.assertEqual(len(set(messages)), 1, messages)
        return messages[0]

    def test_unsupported_inputs_say_what_to_enter_instead(self) -> None:
        cases = {
            str(self.models / Q4): "is a file. Enter the folder that contains it: "
                                   + str((self.models / Q4).parent),
            "D:\\" + Q4.replace("/", "\\"):
                r"is a file. Enter the folder that contains it: D:\lmstudio-community\Qwen2.5-Coder-14B-Instruct-GGUF",
            r"Q:\models": "Drive Q: is not at",
            r"\\wsl$\Ubuntu\home\me\models": "Enter the Linux path",
            r"\\wsl.localhost\Ubuntu\home\me": "Enter the Linux path",
            r"\\server\share\models": "network share",
            "//server/share/models": "network share",
            "models": "Enter the full path",
            "~/models": "Enter the full path",
            "file:///D:/models": "not a URL",
            str(self.models / "no-such-folder"): "does not exist or cannot be read here",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertIn(expected, self.refusal(value))
