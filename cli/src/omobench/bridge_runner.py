"""Pinned GuideLLM backend adapter; the secret never enters BackendArgs/info.

The plain runner receives credentials through stdin. Telemetry uses the same
adapter in its already isolated CLI process. Worker processes pickle the backend
instance, including its private token, rather than rebuilding it from argv.
"""
import contextlib
import json
import sys

_credentials = None


def _validate(credentials):
    if not isinstance(credentials, dict) or set(credentials) != {"endpoint", "token"}:
        raise ValueError("Invalid bridge credentials.")
    endpoint, token = credentials["endpoint"], credentials["token"]
    from .bridge_http import Destination
    destination = Destination.parse(endpoint)
    if destination.host != "127.0.0.1" or not isinstance(token, str) or len(token) != 43 or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in token):
        raise ValueError("Invalid bridge credentials.")
    return dict(credentials)


# Imported only when a bridge is selected, so ordinary commands and lightweight
# installs do not acquire a runner dependency just to import the CLI.
def _backend_type():
    from .bridge_backend import BridgeBackend
    return BridgeBackend


@contextlib.contextmanager
def authenticated_backend(credentials):
    global _credentials
    from guidellm.backends import Backend
    backend = _backend_type()
    if _credentials is not None:
        raise ValueError("A bridge runner context is already active.")
    previous = Backend.get_registered_object("openai_http")
    _credentials = _validate(credentials)
    Backend.registry["openai_http"] = backend
    try:
        yield
    finally:
        Backend.registry["openai_http"] = previous
        _credentials = None


def main():
    try:
        # Bounded private pipe, never an env var, argument, URL, or file.
        credentials = json.loads(sys.stdin.buffer.readline(4097))
        with authenticated_backend(credentials):
            from guidellm.__main__ import cli
            cli(args=sys.argv[1:])
        return 0
    except Exception:
        print("Windows bridge runner failed. Inspect the local runner status.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    from omobench.bridge_runner import main as entry
    raise SystemExit(entry())
