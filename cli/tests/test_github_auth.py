from __future__ import annotations

import io
import json
import threading
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock

from omobench.github_auth import DeviceSession, GitHubAPI, GitHubError, GitHubHTTP, NoRedirect, VERIFY_URL
from omobench.ui import UIError

CHALLENGE = {"device_code": "private-device", "user_code": "ABCD-EFGH", "verification_uri": VERIFY_URL,
             "interval": 5, "expires_in": 900}
TOKEN = {"access_token": "private-access", "scope": "public_repo", "expires_in": 1200}
ACTOR = {"login": "contributor", "id": 42}


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now


class Event:
    def __init__(self, clock): self.clock, self.cancelled, self.waits = clock, False, []
    def set(self): self.cancelled = True
    def is_set(self): return self.cancelled
    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock.now += seconds
        return self.cancelled


class Responses:
    def __init__(self, answers): self.answers, self.calls = list(answers), []
    def request(self, url, payload=None, token=None, **kw):
        self.calls.append((url, payload, token, kw))
        value = self.answers.pop(0)
        return value() if callable(value) else value


class DeviceTests(unittest.TestCase):
    def run_flow(self, answers):
        clock = Clock()
        session = DeviceSession("public_client_id", http=Responses(answers), clock=clock)
        session.cancelled = Event(clock)
        session._authorize(session.generation, session.cancelled)
        return session

    def test_pending_and_slow_down_follow_server_cadence_without_exposing_tokens(self):
        session = self.run_flow([CHALLENGE, {"error": "authorization_pending"},
                                 {"error": "slow_down", "interval": 12}, TOKEN, ACTOR])
        self.assertEqual(session.cancelled.waits, [5, 5, 12])
        snapshot = session.snapshot()
        self.assertEqual(snapshot["account"], ACTOR)
        self.assertEqual(snapshot["status"], "signed_in")
        self.assertNotIn("private", json.dumps(snapshot))
        self.assertEqual(session.http.calls[0][1]["scope"], "public_repo")
        session.clock.now += 1201
        self.assertEqual(session.snapshot()["status"], "expired")
        with self.assertRaises(UIError): session.client()

    def test_sign_out_forgets_credential_and_invalidates_review_generation(self):
        session = self.run_flow([CHALLENGE, TOKEN, ACTOR])
        generation = session.generation
        session.close()
        self.assertIsNone(session._token)
        self.assertIsNone(session.snapshot()["account"])
        self.assertNotEqual(generation, session.generation)
        with self.assertRaises(UIError): session.client()

    def test_cancelled_or_superseded_result_cannot_sign_in(self):
        for cancel in ("sign_out", "replace"):
            with self.subTest(cancel=cancel):
                clock = Clock()
                session = DeviceSession("public_client_id", clock=clock)
                session.cancelled = Event(clock)
                def late():
                    session.sign_out()
                    if cancel == "replace":
                        session.state = {"status": "requesting"}
                        session.cancelled = Event(clock)
                    return ACTOR
                session.http = Responses([CHALLENGE, TOKEN, late])
                session._authorize(session.generation, session.cancelled)
                self.assertIsNone(session._token)
                self.assertEqual(session.state["status"], "requesting" if cancel == "replace" else "signed_out")

    def test_expired_declined_malformed_and_broader_permissions_never_store_token(self):
        for answer, expected in [({"error": "access_denied"}, "denied"),
                                 ({"error": "expired_token"}, "expired"),
                                 ({**TOKEN, "scope": "repo"}, "error"),
                                 ({**TOKEN, "scope": "public_repo,user"}, "error"),
                                 ({**TOKEN, "expires_in": True}, "error"), ([], "error")]:
            with self.subTest(answer=answer):
                session = self.run_flow([CHALLENGE, answer])
                self.assertEqual(session.snapshot()["status"], expected)
                self.assertIsNone(session._token)
                self.assertNotIn("private-access", json.dumps(session.snapshot()))
        session = self.run_flow([{**CHALLENGE, "expires_in": 1}])
        self.assertEqual(session.snapshot()["status"], "expired")

    def test_unconfigured_and_wrong_verification_url_do_not_start_sign_in(self):
        session = DeviceSession("")
        with self.assertRaisesRegex(UIError, "not configured"): session.start()
        self.assertFalse(session.snapshot()["enabled"])
        session = self.run_flow([{**CHALLENGE, "verification_uri": "https://evil.invalid"}])
        self.assertEqual(session.snapshot()["status"], "error")

    def test_background_start_returns_while_network_is_waiting_and_cancel_is_responsive(self):
        entered, release = threading.Event(), threading.Event()
        def wait_response():
            entered.set(); release.wait(2); return CHALLENGE
        session = DeviceSession("public_client_id", http=Responses([wait_response]))
        self.addCleanup(release.set)
        self.assertEqual(session.start()["status"], "requesting")
        self.assertTrue(entered.wait(1))
        self.assertEqual(session.sign_out()["status"], "signed_out")
        release.set(); session.worker.join(1)
        self.assertFalse(session.worker.is_alive())
        self.assertEqual(session.snapshot()["status"], "signed_out")


class TransportTests(unittest.TestCase):
    def test_fixed_host_and_no_redirect_credentials(self):
        http = GitHubHTTP()
        http.opener = Mock()
        for url in ["http://api.github.com/user", "https://api.github.com.evil/user", "https://evil.invalid"]:
            with self.assertRaises(UIError): http.request(url, token="secret")
        with self.assertRaises(UIError): http.request("https://github.com/login/device/code", token="secret")
        http.opener.open.assert_not_called()
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))

    def test_request_encoding_and_errors_do_not_echo_remote_secrets(self):
        http = GitHubHTTP(); http.opener = Mock()
        http.opener.open.return_value = io.BytesIO(b'{"id":42}')
        self.assertEqual(GitHubAPI("secret", http).api("user"), {"id": 42})
        request = http.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(request.full_url, "https://api.github.com/user")
        for code in [301, 401, 403, 404, 422, 429, 500]:
            http.opener.open.side_effect = HTTPError(request.full_url, code, "secret", {}, io.BytesIO(b'secret'))
            with self.assertRaises(GitHubError) as caught: http.request(request.full_url)
            self.assertNotIn("secret", str(caught.exception))
            self.assertEqual(caught.exception.code, code)
        http.opener.open.side_effect = None
        http.opener.open.return_value = io.BytesIO(b'secret invalid JSON')
        with self.assertRaises(GitHubError) as caught: http.request(request.full_url)
        self.assertNotIn("secret", str(caught.exception))

    def test_only_not_found_is_optional(self):
        http = Mock(); api = GitHubAPI("secret", http)
        http.request.side_effect = GitHubError("missing", 404)
        self.assertIsNone(api.optional("repos/example/repo"))
        http.request.side_effect = GitHubError("revoked", 401)
        with self.assertRaises(GitHubError): api.optional("repos/example/repo")
        for path in ["/user", "https://evil.invalid", "../user", "repos\\user"]:
            with self.assertRaises(UIError): api.api(path)


if __name__ == "__main__": unittest.main()
