"""GitHub device sign-in for an installed panel, with session-only credentials.

Only the public OAuth client ID ships with the application. Access/device tokens
never enter job JSON, browser responses, logs, argv, or the evidence directory.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .ui import UIError


# Set to the project's registered OAuth app client ID before a signed-in release.
# This is public configuration, not a secret. The environment override is useful
# for downstream projects and integration tests; endpoints are never configurable.
OAUTH_CLIENT_ID = ""
UPSTREAM = "Xatiiv/omobench"
VERIFY_URL = "https://github.com/login/device"
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")


class GitHubError(UIError):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message, 401 if code == 401 else 409)
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubHTTP:
    """A fixed-host transport. Remote errors are deliberately not reflected."""
    def __init__(self):
        self.opener = build_opener(NoRedirect())

    def request(self, url: str, payload=None, token: str | None = None, *, form=False):
        allowed = url.startswith("https://api.github.com/") or url in {
            "https://github.com/login/device/code", "https://github.com/login/oauth/access_token"}
        if not allowed or (token and not url.startswith("https://api.github.com/")):
            raise UIError("Refusing a GitHub request outside the allowed hosts.")
        headers = {"Accept": "application/json", "User-Agent": "OmoBench",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
            data = (urlencode(payload) if form else json.dumps(payload)).encode()
        try:
            with self.opener.open(Request(url, data=data, headers=headers), timeout=15) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise UIError("GitHub returned an oversized response.")
                return json.loads(raw)
        except HTTPError as error:
            code = error.code
            error.close()
            messages = {
                401: "GitHub sign-in expired or was revoked. Sign in again and review the submission.",
                403: "GitHub refused this operation. Check app access or rate limits; local evidence is preserved.",
                404: "GitHub could not find the requested repository or object.",
                422: "GitHub could not create this object. Review the destination and retry; nothing is overwritten.",
                429: "GitHub is rate limiting requests. Wait before retrying; local evidence is preserved.",
            }
            raise GitHubError(messages.get(code, "GitHub could not complete this request. Retry later; local evidence is preserved."), code) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise GitHubError("Could not reach GitHub or read its response. Retry later; local evidence is preserved.") from None


class GitHubAPI:
    def __init__(self, token: str, http: GitHubHTTP):
        self._token, self.http = token, http

    def api(self, path: str, payload=None):
        if not path or path.startswith(("/", ".")) or ":" in path or "\\" in path or ".." in path:
            raise UIError("Invalid GitHub API path.")
        return self.http.request("https://api.github.com/" + path, payload, self._token)

    def optional(self, path: str):
        try:
            return self.api(path)
        except GitHubError as error:
            if error.code == 404:
                return None
            raise


def identity(value) -> dict:
    if (not isinstance(value, dict) or not isinstance(value.get("login"), str)
            or not LOGIN.fullmatch(value["login"]) or type(value.get("id")) is not int or value["id"] <= 0):
        raise UIError("GitHub did not return a valid account identity.")
    return {"login": value["login"], "id": value["id"]}


class DeviceSession:
    def __init__(self, client_id=None, *, http=None, clock=time.monotonic):
        self.client_id = client_id if client_id is not None else os.environ.get("OMOBENCH_GITHUB_CLIENT_ID", OAUTH_CLIENT_ID)
        self.http = http or GitHubHTTP()
        self.clock = clock
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.generation = secrets.token_hex(16)
        self.worker = None
        self._token = None
        self._token_deadline = 0.0
        self.account = None
        self.state = {"status": "signed_out"}

    @property
    def enabled(self):
        return isinstance(self.client_id, str) and re.fullmatch(r"[A-Za-z0-9_]{10,100}", self.client_id) is not None

    def _expire(self):
        if self._token and self.clock() >= self._token_deadline:
            self._token, self.account = None, None
            self.state = {"status": "expired", "message": "GitHub sign-in expired. Sign in again to submit."}
            self.generation = secrets.token_hex(16)

    def snapshot(self):
        with self.lock:
            self._expire()
            return {**self.state, "enabled": self.enabled, "generation": self.generation,
                    "account": dict(self.account) if self.account else None, "repository": UPSTREAM,
                    "authorization_url": ("https://github.com/settings/connections/applications/" + self.client_id) if self.enabled else None}

    def sign_out(self):
        with self.lock:
            self.cancelled.set()
            self.generation = secrets.token_hex(16)
            self._token, self.account = None, None
            self.state = {"status": "signed_out"}
        return self.snapshot()

    def start(self):
        if not self.enabled:
            raise UIError("GitHub sign-in is not configured in this build. You can still download the complete bundle.")
        with self.lock:
            if self.state["status"] in {"requesting", "pending"}:
                return self.snapshot()
            self.sign_out()
            self.cancelled = threading.Event()
            self.state = {"status": "requesting"}
            generation, cancelled = self.generation, self.cancelled
            self.worker = threading.Thread(target=self._authorize, args=(generation, cancelled), daemon=True)
            self.worker.start()
        return self.snapshot()

    def _set(self, generation, **state):
        with self.lock:
            if generation != self.generation or self.cancelled.is_set():
                return False
            self.state = state
            return True

    def _authorize(self, generation, cancelled):
        try:
            challenge = self.http.request("https://github.com/login/device/code",
                                          {"client_id": self.client_id, "scope": "public_repo"}, form=True)
            if (not isinstance(challenge, dict) or challenge.get("verification_uri") != VERIFY_URL
                    or not isinstance(challenge.get("device_code"), str)
                    or not re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", challenge.get("user_code", ""))
                    or type(challenge.get("expires_in")) is not int or not 1 <= challenge["expires_in"] <= 1800
                    or type(challenge.get("interval")) is not int or not 1 <= challenge["interval"] <= 300):
                raise UIError("GitHub could not start sign-in. The project's OAuth app must have device flow enabled.")
            interval = challenge["interval"]
            deadline = self.clock() + challenge["expires_in"]
            if not self._set(generation, status="pending", user_code=challenge["user_code"],
                             verification_uri=VERIFY_URL, expires_in=challenge["expires_in"]):
                return
            while not cancelled.wait(interval):
                if self.clock() >= deadline:
                    self._set(generation, status="expired", message="The sign-in code expired. Request a new code.")
                    return
                answer = self.http.request("https://github.com/login/oauth/access_token", {
                    "client_id": self.client_id, "device_code": challenge["device_code"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}, form=True)
                if not isinstance(answer, dict):
                    raise UIError("GitHub returned an invalid sign-in response.")
                error = answer.get("error")
                if error == "authorization_pending":
                    continue
                if error == "slow_down":
                    supplied = answer.get("interval", 0)
                    interval = max(interval + 5, supplied if type(supplied) is int else 0)
                    continue
                if error in {"expired_token", "token_expired", "access_denied"}:
                    self._set(generation, status="expired" if error != "access_denied" else "denied",
                              message="Sign-in expired or was declined. You can request a new code or download the bundle.")
                    return
                token = answer.get("access_token")
                scopes = set(str(answer.get("scope", "")).replace(",", " ").split())
                if error or not isinstance(token, str) or not token or scopes != {"public_repo"}:
                    raise UIError("Sign-in did not grant the requested public-repository permission. Review the app authorization on GitHub and try again.")
                lifetime = answer.get("expires_in", 8 * 3600)
                if type(lifetime) is not int or lifetime <= 0:
                    raise UIError("GitHub returned an invalid token expiry.")
                token_deadline = self.clock() + min(lifetime, 8 * 3600)
                if cancelled.is_set():
                    return
                account = identity(GitHubAPI(token, self.http).api("user"))
                with self.lock:
                    if cancelled.is_set() or generation != self.generation:
                        return
                    self._token, self.account = token, account
                    self._token_deadline = token_deadline
                    self.state = {"status": "signed_in"}
                return
        except UIError as error:
            self._set(generation, status="error", message=str(error))
        except Exception:
            self._set(generation, status="error", message="GitHub sign-in could not finish. Try again or download the bundle.")

    def client(self):
        with self.lock:
            self._expire()
            if not self._token or not self.account:
                raise UIError("Sign in to GitHub, then review the submission again.", 401)
            return GitHubAPI(self._token, self.http), dict(self.account), self.generation

    def close(self):
        self.sign_out()
        if self.worker:
            self.worker.join(timeout=1)
