"""Three gates, kept apart, and six states nobody is allowed to skip.

The decision this file exists to pin: a model awaiting catalog review stops a
submission and nothing else. The benchmark runs, the measurements are real, the
evidence is complete and the bundle validates, because none of that depends on
somebody else's pending decision. Conversely a bundle that validates is not
thereby a result the registry will take, and an open pull request is not an
accepted one.

All synthetic. Nothing here runs a benchmark or contacts a server.
"""
from __future__ import annotations

import unittest

from omobench import workflow

CATALOG = ["Qwen/Qwen3-4B", "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"]

COMPLETE_PROPOSAL = {"missing": [], "document": {"proposed_id": "acme/private-v9"}}
PARTIAL_PROPOSAL = {"missing": ["licence", "rationale"], "document": None}

SERVER_OK = {
    "stages": [{"title": "Address", "state": "ok", "detail": ""},
               {"title": "Streaming request", "state": "ok", "detail": ""}],
    "served_models": ["qwen3-4b"],
    "summary": "The server answered and streamed one request.",
}
SERVER_FAILED = {
    "stages": [{"title": "Address", "state": "ok", "detail": ""},
               {"title": "Streaming request", "state": "failed",
                "detail": "The server refused the streaming request."}],
    "served_models": [],
}
TOKENIZER_OK = {"ok": True, "source": "Qwen/Qwen3-4B", "message": "Loaded.",
                "identity": {"class": "Qwen2TokenizerFast", "vocabulary_size": 151936}}
TOKENIZER_GGUF = {"ok": False, "kind": "gguf-only", "source": "unsloth/Qwen3-4B-GGUF",
                  "message": "contains GGUF weights and no Transformers tokenizer",
                  "suggestion": {"source": "Qwen/Qwen3-4B", "reason": "declared base model"}}

COMPLETED_JOB = {
    "model": "Qwen/Qwen3-4B", "status": "completed", "run_path": "/runs/run-1",
    "summary": {"requests": {"succeeded": 50, "attempted": 50, "failed": 0}},
}


def ids(checks, gate):
    return [item["id"] for item in workflow.blocking(checks, gate)]


def find(checks, identifier):
    return next(item for item in checks if item["id"] == identifier)


class CheckShapeTests(unittest.TestCase):
    def test_every_check_says_what_it_blocks_and_how_to_recover(self) -> None:
        checks = workflow.run_checks(
            setup_ok=True, setup_message="ready", endpoint="http://127.0.0.1:8080",
            server_check=SERVER_FAILED, tokenizer_report=TOKENIZER_GGUF,
            tokenizer_source="unsloth/Qwen3-4B-GGUF")
        for item in checks:
            with self.subTest(check=item["id"]):
                self.assertIn(item["state"], {"ok", "blocked", "attention", "unknown"})
                self.assertIn(item["blocks"], {None, "run", "package", "submit"})
                self.assertTrue(item["label"])
                if item["state"] == "blocked":
                    self.assertTrue(item["recovery"], "a blocked check must say what to do")

    def test_a_check_cannot_claim_to_block_something_that_is_not_a_gate(self) -> None:
        with self.assertRaises(ValueError):
            workflow.check("x", "X", state="ok", blocks="everything")

    def test_an_unestablished_value_is_never_given_a_source(self) -> None:
        item = workflow.check("x", "X", state="unknown", observed=None, source="detected")
        self.assertEqual(item["source"], workflow.SOURCE_UNCHECKED)


class RunGateTests(unittest.TestCase):
    def ready(self, **overrides):
        arguments = dict(setup_ok=True, setup_message="ready",
                         endpoint="http://127.0.0.1:8080", server_check=SERVER_OK,
                         tokenizer_report=TOKENIZER_OK, tokenizer_source="Qwen/Qwen3-4B")
        arguments.update(overrides)
        return workflow.run_checks(**arguments)

    def test_a_ready_configuration_blocks_nothing(self) -> None:
        self.assertEqual(ids(self.ready(), "run"), [])

    def test_an_unchecked_endpoint_blocks_running_rather_than_passing(self) -> None:
        checks = self.ready(server_check=None)
        self.assertEqual(find(checks, "endpoint")["state"], "unknown")
        self.assertIn("Check this server", find(checks, "endpoint")["recovery"])

    def test_a_server_that_refused_the_streaming_request_blocks_running(self) -> None:
        checks = self.ready(server_check=SERVER_FAILED)
        self.assertEqual(ids(checks, "run"), ["endpoint"])
        self.assertIn("refused the streaming request", find(checks, "endpoint")["recovery"])

    def test_a_tokenizer_that_will_not_load_blocks_running(self) -> None:
        """The run would die resolving it, before sending one request."""
        checks = self.ready(tokenizer_report=TOKENIZER_GGUF)
        self.assertEqual(ids(checks, "run"), ["tokenizer"])
        self.assertIn("Qwen/Qwen3-4B", find(checks, "tokenizer")["recovery"])

    def test_an_unloaded_tokenizer_is_unknown_and_still_blocks(self) -> None:
        checks = self.ready(tokenizer_report=None)
        self.assertEqual(find(checks, "tokenizer")["state"], "unknown")
        self.assertEqual(find(checks, "tokenizer")["blocks"], "run")

    def test_whether_the_tokenizer_matches_the_weights_blocks_nothing(self) -> None:
        """It cannot be established, so it cannot be a gate."""
        item = find(self.ready(), "tokenizer-match")
        self.assertEqual(item["state"], "unknown")
        self.assertIsNone(item["blocks"])
        self.assertIn("Not verifiable from here", item["detail"])

    def test_an_unusable_runner_blocks_running_and_says_why(self) -> None:
        checks = self.ready(setup_ok=False, setup_message="Install the runner first.")
        self.assertEqual(ids(checks, "run"), ["runner"])
        self.assertEqual(find(checks, "runner")["recovery"], "Install the runner first.")

    def test_the_servers_own_model_list_is_recorded_as_the_servers(self) -> None:
        item = find(self.ready(), "served-models")
        self.assertEqual(item["source"], workflow.SOURCE_SERVER)
        self.assertIn("not necessarily catalog model identities", item["detail"])


class GateSeparationTests(unittest.TestCase):
    """The milestone's central rule, pinned from both directions."""

    def submit(self, **overrides):
        arguments = dict(job=COMPLETED_JOB, model="acme/private-v9", catalog=CATALOG,
                         proposal=None, submitter="someone")
        arguments.update(overrides)
        return workflow.submit_checks(**arguments)

    def package(self, **overrides):
        arguments = dict(job=COMPLETED_JOB, model="acme/private-v9", revision="a" * 40,
                         framework="llama.cpp", framework_version="b10621", serving=None)
        arguments.update(overrides)
        return workflow.package_checks(**arguments)

    def test_an_uncatalogued_model_blocks_submission_only(self) -> None:
        checks = self.package() + self.submit()
        self.assertEqual(ids(checks, "run"), [])
        self.assertEqual(ids(checks, "package"), [])
        self.assertIn("proposal", ids(checks, "submit"))

    def test_a_model_awaiting_review_never_blocks_packaging(self) -> None:
        checks = self.package() + self.submit(proposal=COMPLETE_PROPOSAL)
        self.assertEqual(ids(checks, "package"), [])
        self.assertEqual(find(checks, "catalog")["state"], "ok")
        self.assertIn("judged separately", find(checks, "catalog")["detail"])

    def test_an_incomplete_proposal_names_what_is_missing(self) -> None:
        item = find(self.submit(proposal=PARTIAL_PROPOSAL), "proposal")
        self.assertEqual(item["state"], "blocked")
        self.assertEqual(item["observed"], "licence, rationale")
        self.assertEqual(item["blocks"], "submit")

    def test_a_proposal_is_described_as_a_request_not_an_entry(self) -> None:
        item = find(self.submit(proposal=PARTIAL_PROPOSAL), "proposal")
        self.assertIn("merging this result would not add the model", item["detail"])

    def test_a_catalogued_model_needs_no_proposal_at_all(self) -> None:
        checks = self.submit(model="Qwen/Qwen3-4B")
        self.assertNotIn("proposal", [item["id"] for item in checks])
        self.assertEqual(find(checks, "catalog")["state"], "ok")

    def test_missing_packaging_metadata_blocks_packaging_not_running(self) -> None:
        checks = self.package(revision=None, framework_version=None)
        self.assertEqual(sorted(ids(checks, "package")), ["runtime-build", "weights-revision"])
        self.assertEqual(ids(checks, "run"), [])

    def test_a_moving_revision_is_not_an_immutable_one(self) -> None:
        for revision in ("main", "master", "latest", "HEAD"):
            with self.subTest(revision=revision):
                self.assertIn("weights-revision", ids(self.package(revision=revision), "package"))

    def test_acceptance_is_unknown_until_the_bundle_is_validated(self) -> None:
        item = find(self.submit(), "acceptance")
        self.assertEqual(item["state"], "unknown")
        self.assertIn("Checked when you package", item["recovery"])

    def test_a_failed_acceptance_blocks_submission_with_the_validators_words(self) -> None:
        job = {**COMPLETED_JOB, "validation": {
            "valid": False, "errors": ["results/x: acceptance: 12 successful requests is below"]}}
        item = find(self.submit(job=job), "acceptance")
        self.assertEqual(item["state"], "blocked")
        self.assertIn("12 successful requests", item["recovery"])

    def test_a_submitter_nobody_entered_is_unchecked_not_satisfied(self) -> None:
        """Absent, empty and present are three different answers."""
        self.assertEqual(find(self.submit(submitter=None), "submitter")["state"], "unknown")
        self.assertEqual(find(self.submit(submitter="  "), "submitter")["state"], "attention")
        self.assertEqual(find(self.submit(submitter="xatiiv"), "submitter")["state"], "ok")
        for value in (None, "  "):
            self.assertIn("submitter", ids(self.submit(submitter=value), "submit"))

    def test_what_is_absent_is_not_reported_as_what_is_wrong(self) -> None:
        """Before a run, "you will need this" is not a failure."""
        absent = find(self.package(revision=None), "weights-revision")
        wrong = find(self.package(revision="main"), "weights-revision")
        self.assertEqual(absent["state"], "attention")
        self.assertEqual(wrong["state"], "blocked")
        self.assertEqual(find(self.package(job={}), "measurements")["state"], "attention")
        self.assertIn("Run the benchmark first",
                      find(self.package(job={}), "measurements")["recovery"])

    def test_a_bridged_run_needs_its_serving_machine_before_packaging(self) -> None:
        job = {**COMPLETED_JOB, "transport": {"kind": "windows-stdio-http1-v1"}}
        self.assertIn("serving-environment", ids(self.package(job=job, serving={}), "package"))
        declared = self.package(job=job, serving={"host": {"cpu": "AMD Ryzen 7 9800X3D"}})
        self.assertEqual(ids(declared, "package"), [])

    def test_bridge_overhead_is_stated_and_blocks_nothing(self) -> None:
        job = {**COMPLETED_JOB, "transport": {"kind": "windows-stdio-http1-v1"}}
        item = find(self.package(job=job, serving={"host": {"cpu": "x"}}), "transport-overhead")
        self.assertEqual(item["state"], "ok")
        self.assertIsNone(item["blocks"])
        self.assertIn("never subtracted", item["detail"])


class StageTests(unittest.TestCase):
    def stages(self, checks, **overrides):
        arguments = dict(endpoint_checked=True, identity_settled=True, job=COMPLETED_JOB)
        arguments.update(overrides)
        return {stage["id"]: stage["state"] for stage in workflow.stages(checks, **arguments)}

    def ready_checks(self):
        return workflow.run_checks(
            setup_ok=True, setup_message="ready", endpoint="http://127.0.0.1:8080",
            server_check=SERVER_OK, tokenizer_report=TOKENIZER_OK,
            tokenizer_source="Qwen/Qwen3-4B")

    def test_progress_comes_from_state_rather_than_from_clicks(self) -> None:
        states = self.stages(self.ready_checks())
        self.assertEqual(states["connect"], "done")
        self.assertEqual(states["run"], "done")
        self.assertEqual(states["review"], "current")

    def test_an_unchecked_endpoint_leaves_the_first_stage_unfinished(self) -> None:
        states = self.stages(self.ready_checks(), endpoint_checked=False, job=None)
        self.assertEqual(states["connect"], "current")
        self.assertEqual(states["run"], "todo")

    def test_a_blocked_run_marks_the_check_stage_blocked(self) -> None:
        checks = workflow.run_checks(
            setup_ok=False, setup_message="no runner", endpoint="http://127.0.0.1:8080",
            server_check=SERVER_OK, tokenizer_report=TOKENIZER_OK, tokenizer_source="x")
        self.assertEqual(self.stages(checks)["check"], "blocked")

    def test_fixing_something_earlier_does_not_discard_what_already_happened(self) -> None:
        """Stages recompute from what is true, so going back loses nothing."""
        done = self.stages(self.ready_checks())
        revisited = self.stages(self.ready_checks(), identity_settled=False)
        self.assertEqual(revisited["run"], "done")
        self.assertEqual(revisited["identify"], "current")
        self.assertEqual(done["run"], revisited["run"])

    def test_a_validated_bundle_moves_the_contributor_to_submitting(self) -> None:
        job = {**COMPLETED_JOB, "validation": {"valid": True, "errors": []}}
        self.assertEqual(self.stages(self.ready_checks(), job=job)["review"], "done")

    def test_submission_is_done_only_once_a_pull_request_exists(self) -> None:
        job = {**COMPLETED_JOB, "validation": {"valid": True, "errors": []},
               "submission": {"started": True}}
        self.assertNotEqual(self.stages(self.ready_checks(), job=job)["submit"], "done")
        job["submission"]["pull_request_url"] = "https://github.com/x/y/pull/1"
        self.assertEqual(self.stages(self.ready_checks(), job=job)["submit"], "done")


class CompletionTests(unittest.TestCase):
    def states(self, job, *, submit_blocked=False, package_blocked=False):
        return {step["id"]: step["state"] for step in workflow.completion(
            job, submit_blocked=submit_blocked, package_blocked=package_blocked)}

    def test_the_six_states_are_reported_separately(self) -> None:
        self.assertEqual([step["id"] for step in workflow.completion({}, submit_blocked=True)],
                         ["benchmark", "evidence", "package", "ready", "submitted", "published"])

    def test_a_finished_benchmark_is_not_a_validated_bundle(self) -> None:
        states = self.states(COMPLETED_JOB)
        self.assertEqual(states["benchmark"], "done")
        self.assertEqual(states["evidence"], "done")
        self.assertEqual(states["package"], "pending")

    def test_a_validated_bundle_is_not_a_submission(self) -> None:
        job = {**COMPLETED_JOB, "validation": {"valid": True, "errors": []}}
        states = self.states(job)
        self.assertEqual(states["package"], "done")
        self.assertEqual(states["ready"], "done")
        self.assertEqual(states["submitted"], "pending")

    def test_a_valid_bundle_the_registry_will_refuse_is_not_ready(self) -> None:
        job = {**COMPLETED_JOB, "validation": {"valid": True, "errors": []}}
        self.assertEqual(self.states(job, submit_blocked=True)["ready"], "blocked")

    def test_an_open_pull_request_is_never_reported_as_accepted(self) -> None:
        """Acceptance happens elsewhere, after review, and is never assumed."""
        job = {**COMPLETED_JOB, "validation": {"valid": True, "errors": []},
               "submission": {"pull_request_url": "https://github.com/x/y/pull/1"}}
        states = self.states(job)
        self.assertEqual(states["submitted"], "done")
        self.assertEqual(states["published"], "pending")

    def test_published_is_never_done_whatever_the_job_says(self) -> None:
        job = {**COMPLETED_JOB, "validation": {"valid": True, "errors": []},
               "submission": {"pull_request_url": "https://github.com/x/y/pull/1",
                              "pull_request_state": "merged"}}
        self.assertEqual(self.states(job)["published"], "pending")

    def test_a_failed_submission_keeps_the_evidence_and_offers_a_retry(self) -> None:
        job = {**COMPLETED_JOB, "validation": {"valid": True, "errors": []},
               "submission": {"started": True}}
        step = next(entry for entry in workflow.completion(job, submit_blocked=False)
                    if entry["id"] == "submitted")
        self.assertEqual(step["state"], "failed")
        self.assertIn("without re-running anything", step["detail"])
        self.assertEqual(self.states(job)["evidence"], "done")
        self.assertEqual(self.states(job)["package"], "done")

    def test_a_bundle_the_registry_refuses_is_still_a_bundle(self) -> None:
        """Acceptance is the registry's gate, not the bundle's."""
        job = {**COMPLETED_JOB, "validation": {"valid": False, "errors": ["x: acceptance: no"]}}
        states = self.states(job, submit_blocked=True)
        self.assertEqual(states["package"], "done")
        self.assertEqual(states["ready"], "blocked")

    def test_evidence_that_cannot_be_packaged_is_reported_as_refused(self) -> None:
        job = {**COMPLETED_JOB, "validation": {"valid": False, "errors": ["x: model.revision"]}}
        step = next(entry for entry in
                    workflow.completion(job, submit_blocked=True, package_blocked=True)
                    if entry["id"] == "package")
        self.assertEqual(step["state"], "failed")
        self.assertIn("measurements are untouched", step["detail"])

    def test_a_cancelled_run_is_not_a_completed_benchmark(self) -> None:
        self.assertEqual(self.states({"status": "cancelled"})["benchmark"], "failed")


if __name__ == "__main__":
    unittest.main()
