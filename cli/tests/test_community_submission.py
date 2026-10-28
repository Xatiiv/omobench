from __future__ import annotations

import base64
import copy
import json
import shutil
import unittest
from unittest.mock import patch

import test_ui_submission as fixtures
from omobench.community_submission import prepare_community, submit_community
from omobench.github_auth import GitHubError, UPSTREAM
from omobench.ui import Controller, UIError
from omobench.ui_submission import bundle_archive, bundle_snapshot

ACTOR = {"login": "contributor", "id": 42}
TIP, TREE, COMMIT = "a" * 40, "b" * 40, "c" * 40


class FakeAPI:
    def __init__(self):
        self.actor = dict(ACTOR)
        self.upstream = {"id": 1, "name": "omobench", "full_name": UPSTREAM, "owner": {"login": "Xatiiv", "id": 1},
                         "private": False, "default_branch": "main", "permissions": {"push": True}}
        self.fork = None
        self.ready = True
        self.existing_bundle = False
        self.ref = None
        self.pulls = []
        self.calls = []
        self.lose = None
        self.cancel_write = None

    def new_fork(self, name="omobench"):
        return {"id": 2, "name": name, "full_name": self.actor["login"] + "/" + name,
                "owner": dict(self.actor), "private": False, "fork": True, "source": {"id": 1},
                "default_branch": "old-branch", "permissions": {"push": True}}

    def optional(self, path):
        try: return self.api(path)
        except GitHubError as e:
            if e.code == 404: return None
            raise

    def api(self, path, payload=None):
        self.calls.append((path, copy.deepcopy(payload)))
        value = self.respond(path, payload)
        if payload and self.cancel_write: self.cancel_write()
        if self.lose and path.endswith(self.lose) and payload is not None:
            self.lose = None
            raise GitHubError("Response lost", 0)
        return copy.deepcopy(value)

    def respond(self, path, payload):
        if path == "user": return self.actor
        if path == f"repos/{UPSTREAM}": return self.upstream
        if path == f"repos/{UPSTREAM}/forks" and payload:
            self.fork = self.new_fork(); return self.fork
        if "/forks?" in path: return [self.fork] if self.fork else []
        if path == f"repos/{self.actor['login']}/omobench" or self.fork and path == "repos/" + self.fork["full_name"]:
            if self.fork and path == "repos/" + self.fork["full_name"]: return self.fork
            raise GitHubError("missing", 404)
        if path == f"repos/{UPSTREAM}/commits/main": return {"sha": TIP, "commit": {"tree": {"sha": TREE}}}
        if "/contents/" in path: return [{"name": "existing"}] if self.existing_bundle else self.missing()
        if "/git/commits/" in path: return {"sha": TIP} if self.ready else self.missing()
        if "/git/ref/" in path: return self.ref or self.missing()
        if path.endswith("/git/blobs"): return {"sha": "d" * 40}
        if path.endswith("/git/trees"): return {"sha": "e" * 40}
        if path.endswith("/git/commits"): return {"sha": COMMIT}
        if path.endswith("/git/refs"):
            self.ref = {"object": {"sha": payload["sha"]}}; return self.ref
        if "/pulls?" in path: return self.pulls
        if path.endswith("/pulls"):
            repo = self.fork or self.upstream
            pull = {"number": 10, "state": "open", "head": {"sha": COMMIT, "repo": {"id": repo["id"]}},
                    "base": {"ref": "main", "repo": {"id": 1}}}
            self.pulls.append(pull); return pull
        raise AssertionError(path)

    def missing(self): raise GitHubError("missing", 404)
    @property
    def writes(self): return [(p, d) for p, d in self.calls if d is not None]


class Session:
    def __init__(self, api): self.api, self.account, self.generation = api, dict(api.actor), "generation-one"
    def client(self): return self.api, dict(self.account), self.generation
    def snapshot(self): return {"generation": self.generation}
    def close(self): self.generation = "signed-out"


class CommunityTests(unittest.TestCase):
    setUp = fixtures.SubmissionTests.setUp
    tearDown = fixtures.SubmissionTests.tearDown
    change_result = fixtures.SubmissionTests.change_result

    def prepare(self, api=None, state=None):
        api = api or FakeAPI()
        session = Session(api)
        preview, review = prepare_community(self.root, self.run, session, state or {})
        self.assertTrue(preview["can_submit"], preview)
        self.assertFalse(api.writes)
        return api, session, preview, review

    def test_installed_source_without_git_or_gh_uploads_exactly_five_files(self):
        self.assertFalse((self.root / ".git").exists())
        _, contents, _ = bundle_snapshot(self.root, self.run)
        with patch("subprocess.run", side_effect=AssertionError("no git or gh")):
            api, session, preview, review = self.prepare()
            self.assertTrue(preview["needs_fork"])
            state = {}
            submit_community(self.root, self.run, session, review, state, lambda: None)
        blobs = [base64.b64decode(d["content"]) for p, d in api.writes if p.endswith("/git/blobs")]
        self.assertEqual(blobs, list(contents.values()))
        self.assertEqual(state["pull_request_url"], f"https://github.com/{UPSTREAM}/pull/10")
        self.assertEqual(state["account"], ACTOR)
        for path, payload in api.writes:
            if "/git/" in path: self.assertTrue(path.startswith("repos/contributor/omobench/"))
            if path.endswith("/git/trees"):
                self.assertEqual(payload["base_tree"], TREE)
                self.assertEqual(len(payload["tree"]), 5)
            if path.endswith("/git/commits"): self.assertEqual(payload["parents"], [TIP])
            if path.endswith("/git/refs"): self.assertEqual(payload["ref"], "refs/heads/submit/run-fixture-1234")
            if path.endswith("/pulls"):
                self.assertEqual(path, f"repos/{UPSTREAM}/pulls")
                self.assertEqual(payload["base"], "main")
                self.assertEqual(payload["head"], "contributor:submit/run-fixture-1234")
        self.assertEqual(bundle_snapshot(self.root, self.run)[1], contents)
        self.assertEqual(list((self.root / "results").iterdir()), [])

    def test_existing_renamed_and_behind_fork_are_reused_without_updating_default_branch(self):
        for name in ("omobench", "my-benchmarks"):
            with self.subTest(name=name):
                api = FakeAPI(); api.fork = api.new_fork(name)
                api, session, preview, review = self.prepare(api)
                self.assertFalse(preview["needs_fork"])
                self.assertEqual(preview["upload_repository"], f"contributor/{name}")
                submit_community(self.root, self.run, session, review, {}, lambda: None)
                self.assertFalse(any(p.endswith("/forks") for p, _ in api.writes))
                self.assertEqual([d["parents"] for p, d in api.writes if p.endswith("/git/commits")], [[TIP]])

    def test_project_owner_can_submit_from_a_new_branch_without_forking_itself(self):
        api = FakeAPI(); api.actor = dict(api.upstream["owner"])
        api, session, preview, review = self.prepare(api)
        self.assertEqual(preview["upload_repository"], UPSTREAM)
        submit_community(self.root, self.run, session, review, {}, lambda: None)
        self.assertFalse(any(p.endswith("/forks") for p, _ in api.writes))

    def test_unsigned_preview_and_download_remain_available(self):
        session = Session(FakeAPI())
        with patch.object(session, "client", side_effect=UIError("Sign in first")):
            preview, review = prepare_community(self.root, self.run, session, {})
        self.assertFalse(preview["can_submit"])
        self.assertIsNone(review)
        self.assertEqual(len(preview["files"]), 5)
        self.assertTrue(bundle_archive(self.root, self.run).startswith(b"PK"))

    def test_private_unrelated_or_wrong_owner_fork_refused_before_writes(self):
        for change in [{"private": True}, {"fork": False}, {"source": {"id": 99}}, {"owner": {"login": "stranger", "id": 44}}, {"permissions": {"push": False}}]:
            api = FakeAPI(); api.fork = api.new_fork(); api.fork.update(change)
            preview, review = prepare_community(self.root, self.run, Session(api), {})
            self.assertFalse(preview["can_submit"], change)
            self.assertIsNone(review); self.assertFalse(api.writes)

    def test_changed_account_session_evidence_and_repository_invalidate_review(self):
        for change in ("account", "session", "evidence", "repository", "revoked"):
            with self.subTest(change=change):
                api, session, _, review = self.prepare()
                original = (self.run / "result.json").read_bytes()
                if change == "account": api.actor["id"] = 43
                if change == "session": session.generation = "new-session"
                if change == "evidence": self.change_result(lambda r: r["model"].update(quantization="Q8_0"))
                if change == "repository": api.upstream["id"] = 123
                try:
                    if change == "revoked":
                        with patch.object(api, "api", side_effect=GitHubError("revoked", 401)), self.assertRaises(UIError):
                            submit_community(self.root, self.run, session, review, {}, lambda: None)
                    else:
                        with self.assertRaises(UIError): submit_community(self.root, self.run, session, review, {}, lambda: None)
                    self.assertFalse(api.writes)
                finally: (self.run / "result.json").write_bytes(original)

    def test_fork_not_ready_preserves_binding_and_can_retry(self):
        api, session, _, review = self.prepare(); state = {}; api.ready = False
        with self.assertRaisesRegex(UIError, "still preparing"):
            submit_community(self.root, self.run, session, review, state, lambda: None)
        self.assertTrue(state["started"])
        self.assertEqual(len(api.writes), 1)
        api.ready = True
        _, new_review = prepare_community(self.root, self.run, session, state)
        submit_community(self.root, self.run, session, new_review, state, lambda: None)
        self.assertEqual(len([p for p, _ in api.writes if p.endswith("/forks")]), 1)

    def test_lost_fork_ref_or_pr_response_never_duplicates_or_overwrites(self):
        for stage in ("/forks", "/git/refs", "/pulls"):
            with self.subTest(stage=stage):
                api, session, _, review = self.prepare(); state = {}; api.lose = stage
                with self.assertRaisesRegex(UIError, "Response lost"):
                    submit_community(self.root, self.run, session, review, state, lambda: None)
                _, review = prepare_community(self.root, self.run, session, state)
                submit_community(self.root, self.run, session, review, state, lambda: None)
                submit_community(self.root, self.run, session, review, state, lambda: None)
                for path in ("/forks", "/git/refs", "/pulls"):
                    self.assertEqual(len([p for p, _ in api.writes if p.endswith(path)]), 1)
                self.assertEqual(len(api.pulls), 1)

    def test_collisions_and_existing_submission_under_other_account_refuse(self):
        for collision in ("bundle", "branch", "other-account"):
            api = FakeAPI(); api.fork = api.new_fork()
            api, session, _, review = self.prepare(api); state = {}
            if collision == "bundle": api.existing_bundle = True
            if collision == "branch": api.ref = {"object": {"sha": "f" * 40}}
            if collision == "other-account": state = {"mode": "community", "account": {"id": 99}}
            with self.assertRaises(UIError): submit_community(self.root, self.run, session, review, state, lambda: None)
            self.assertFalse(api.writes)

    def test_closed_and_merged_pr_are_reported_without_creating_another(self):
        api, session, _, review = self.prepare(); state = {}
        submit_community(self.root, self.run, session, review, state, lambda: None)
        api.pulls[0]["state"] = "closed"
        for merged, expected in [(None, "closed"), ("2026-01-01", "merged")]:
            api.pulls[0]["merged_at"] = merged
            submit_community(self.root, self.run, session, review, state, lambda: None)
            self.assertEqual(state["pull_request_state"], expected)
        self.assertEqual(len(api.pulls), 1)

    def test_closing_panel_during_upload_prevents_further_writes(self):
        api, session, _, review = self.prepare(); state = {}
        api.cancel_write = session.close
        with self.assertRaisesRegex(UIError, "sign-in ended"):
            submit_community(self.root, self.run, session, review, state, lambda: None)
        self.assertEqual(len(api.writes), 1)

    def controller(self):
        controller = Controller(self.root)
        job_id = "job-" + "a" * 16
        directory = controller.storage / job_id; directory.mkdir()
        shutil.move(self.run, directory / self.run.name); self.run = directory / self.run.name
        controller.jobs[job_id] = {"id": job_id, "created_at": "2026-09-10T12:00:00Z", "status": "completed",
            "profile": "chat-small-v1", "execution_device": "gpu", "submission": None}
        controller.github_auth = Session(FakeAPI())
        self.addCleanup(controller.close)
        return controller, job_id

    def test_controller_requires_explicit_current_review_and_freezes_started_bundle(self):
        controller, job = self.controller()
        preview = controller.prepare(job, {"mode": "community"})
        data = {"mode": "community", "confirmed": True, "repository": UPSTREAM,
                "bundle_digest": preview["bundle_digest"], "review_id": preview["review_id"]}
        for changes in [{"confirmed": False}, {"review_id": "other"}, {"repository": "wrong/repo"}, {"bundle_digest": "different"}]:
            with self.assertRaises(UIError): controller.submit(job, {**data, **changes})
            self.assertFalse(controller.github_auth.api.writes)
        controller.community_reviews[job]["expires"] = 0
        with self.assertRaisesRegex(UIError, "expired"): controller.submit(job, data)
        preview = controller.prepare(job, {"mode": "community"}); data["review_id"] = preview["review_id"]
        detail = controller.submit(job, data)
        self.assertEqual(detail["submission"]["pull_request_state"], "open")
        stored = (controller.storage / job / "job.json").read_text()
        self.assertNotIn("auth_generation", stored); self.assertNotIn("review_id", stored)
        with self.assertRaises(UIError): controller._begin_action(job, "packaging")
        with self.assertRaises(UIError): controller.prepare(job, {"mode": "origin"})
        controller.busy = True
        with self.assertRaises(UIError): controller.github_action("sign-out")
        controller.busy = False


if __name__ == "__main__": unittest.main()
