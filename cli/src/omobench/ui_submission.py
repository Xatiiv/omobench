"""Validate and submit complete bundles; never mutate the user's Git checkout."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import quote, urlencode

from .ui import UIError


BUNDLE_FILES = ("result.json", "guidellm-benchmarks.json.gz", "guidellm.stdout.log", "environment.json", "reproduce.md")
ARTIFACT_FILES = {
    "raw_runner_output": "guidellm-benchmarks.json.gz",
    "runner_stdout_log": "guidellm.stdout.log",
    "environment_snapshot": "environment.json",
    "reproduce_markdown": "reproduce.md",
}


def validate_bundle(root: Path, run: Path) -> list[str]:
    """Call the repository's actual validator, including registry duplicate checks."""
    root, run = root.resolve(), run.resolve()
    if not run.is_relative_to(root / ".omobench-runs"):
        raise UIError("Only local staging evidence can be submitted from this panel.")
    for name in BUNDLE_FILES:
        path = run / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(run):
            return [f"Missing or unsafe bundle file: {name}"]
        if path.stat().st_size > 50 * 1024 * 1024:
            return [f"{name} exceeds the panel's 50 MiB per-file limit. Use the documented manual submission workflow."]
    try:
        spec = importlib.util.spec_from_file_location("omobench_registry_validator", root / "tools" / "validate_results.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        catalog = module.load_model_catalog(module.CATALOG_PATH)
        schema = json.loads(module.SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = module.Draft202012Validator(schema, format_checker=module.FormatChecker())
        run_ids, duplicate_keys = set(), set()
        errors = []
        for result_path in sorted(module.RESULTS_PATH.rglob("result.json")):
            errors.extend(module.validate_result(result_path, validator, catalog, run_ids, duplicate_keys))
        errors.extend(module.validate_result(run / "result.json", validator, catalog, run_ids, duplicate_keys))
        result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        if not errors and any(result["artifacts"][key]["path"] != name for key, name in ARTIFACT_FILES.items()):
            errors.append("The panel submits only the standard CLI-generated artifact filenames.")
        return errors
    except ImportError as error:
        raise UIError("Install the UI validator: python -m pip install -e './cli[ui]'") from error
    except (OSError, ValueError, AttributeError, KeyError) as error:
        raise UIError("Could not read the bundle, schema, or repository validator.") from error
    except Exception as error:
        # The validator's CatalogError is defined in the dynamically loaded module.
        if type(error).__name__ == "CatalogError":
            raise UIError(f"Model catalog error: {error}") from error
        raise


def bundle_snapshot(root: Path, run: Path) -> tuple[dict, dict[str, bytes], str]:
    errors = validate_bundle(root, run)
    if errors:
        raise UIError("Bundle validation failed:\n" + "\n".join(errors))
    contents = {name: (run / name).read_bytes() for name in BUNDLE_FILES}
    result = json.loads(contents["result.json"])
    # Verify the bytes that will actually be uploaded, not just the earlier file reads.
    for key, name in ARTIFACT_FILES.items():
        if result["artifacts"][key]["sha256"] != "sha256:" + hashlib.sha256(contents[name]).hexdigest():
            raise UIError("Evidence changed during preparation. Validate and review the bundle again.")
    digest = hashlib.sha256(json.dumps({name: hashlib.sha256(content).hexdigest()
                                      for name, content in contents.items()}, sort_keys=True).encode()).hexdigest()
    return result, contents, digest


def destination(result: dict) -> str:
    slug = re.sub(r"[^a-z0-9.-]+", "-", result["model"]["id"].lower()).strip(".-")[:90] or "model"
    return f"results/{slug}/{result['started_at'][:10]}-{result['run_id']}"


def description(result: dict, prefix: str) -> tuple[str, str]:
    title = f"Add {result['model']['id']} benchmark ({result['benchmark']['profile_id']})"
    metrics = result["results"]
    body = (
        f"Adds the complete evidence bundle at `{prefix}`.\n\n"
        f"- Model: {result['model']['id']} at `{result['model']['revision']}`\n"
        f"- Runtime: {result['runtime']['framework']} {result['runtime']['version']}\n"
        f"- Profile: `{result['benchmark']['profile_id']}`\n"
        f"- Successful requests: {metrics['requests']['succeeded']} / {metrics['requests']['attempted']}\n"
        f"- Output tokens/s: {metrics['output_tokens_per_second']:.2f}\n\n"
        "The contributor confirms GPU execution and has reviewed the bundle for secrets. "
        "The local schema, catalog, artifact-hash, and duplicate checks passed. "
        "CI and maintainer review are still required before this becomes a registry result.\n"
    )
    return title, body


class GitHub:
    """Use the user's existing gh authentication; no credentials reach the browser."""
    def __init__(self, root: Path):
        self.root = root
        self.environment = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"}

    def _run(self, arguments: list[str], payload: dict | None = None):
        try:
            result = subprocess.run(["gh", *arguments], cwd=self.root, env=self.environment,
                                    input=json.dumps(payload) if payload is not None else None,
                                    capture_output=True, text=True, timeout=60)
        except FileNotFoundError as error:
            raise UIError("Install GitHub CLI and run gh auth login in this environment, or download the bundle for a manual PR.") from error
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UIError("GitHub did not respond. Local evidence is preserved; retry preparation or submission.") from error
        if result.returncode:
            # gh stderr can contain URLs or authentication details; do not relay it.
            if "HTTP 404" in result.stderr:
                raise UIError("GitHub resource not found or not accessible.", 404)
            raise UIError("GitHub rejected the operation. Check gh auth status, repository write access, and branch rules. "
                          "Local evidence is preserved; you can retry or download the bundle.")
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise UIError("GitHub returned an unexpected response.") from error

    def repository(self) -> dict:
        # Make the destination explicit: always origin, not gh's implicit upstream/fork preference.
        try:
            origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=self.root,
                                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UIError("Could not read this checkout's origin remote.") from error
        match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?", origin.stdout.strip())
        if origin.returncode or not match:
            raise UIError("Direct submission needs a github.com origin remote. You can download the bundle for a manual PR.")
        return self.api(f"repos/{match[1]}")

    def api(self, path: str, payload: dict | None = None):
        arguments = ["api", "--hostname", "github.com", "--method", "POST" if payload is not None else "GET", path]
        if payload is not None:
            arguments.extend(["--input", "-"])
        return self._run(arguments, payload)

    def optional(self, path: str):
        try:
            return self.api(path)
        except UIError as error:
            if error.status == 404:
                return None
            raise


def prepare_submission(root: Path, run: Path) -> dict:
    result, contents, digest = bundle_snapshot(root, run)
    prefix = destination(result)
    title, body = description(result, prefix)
    preview = {"files": [{"path": f"{prefix}/{name}", "bytes": len(content),
                          "sha256": hashlib.sha256(content).hexdigest()} for name, content in contents.items()],
               "result": result, "title": title, "body": body, "bundle_digest": digest,
               "repository": None, "can_submit": False, "message": ""}
    try:
        repo = GitHub(root).repository()
        preview["repository"] = repo["full_name"]
        preview["base_branch"] = repo["default_branch"]
        preview["can_submit"] = repo.get("permissions", {}).get("push") is True
        if not preview["can_submit"]:
            preview["message"] = "Your GitHub account needs write access to origin for direct submission. Download the bundle and use the manual fork/PR workflow."
    except UIError as error:
        preview["message"] = str(error)
    return preview


def bundle_archive(root: Path, run: Path) -> bytes:
    result, contents, _ = bundle_snapshot(root, run)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            archive.writestr(f"{destination(result)}/{name}", content)
    return output.getvalue()


def submit_pull_request(root: Path, run: Path, expected_repository: str, expected_digest: str,
                        state: dict, save) -> None:
    result, contents, digest = bundle_snapshot(root, run)
    if digest != expected_digest or (state.get("bundle_digest") and state["bundle_digest"] != digest):
        raise UIError("The bundle changed since review. Prepare and review it again before submission.")
    github = GitHub(root)
    repo = github.repository()
    if repo["full_name"] != expected_repository or (state.get("repository") and state["repository"] != expected_repository):
        raise UIError("The destination repository changed. Review the submission destination again.")
    if not repo.get("permissions", {}).get("push"):
        raise UIError("Direct submission needs write access to origin. Download the bundle for a manual fork/PR instead.")
    repository = repo["full_name"]
    branch = "submit/" + result["run_id"]
    base = f"repos/{repository}"
    state.update(repository=repository, branch=branch, bundle_digest=digest)
    save()
    ref = github.optional(f"{base}/git/ref/heads/{quote(branch, safe='')}")
    if ref:
        if not state.get("commit_sha") or ref["object"]["sha"] != state["commit_sha"]:
            raise UIError("That submission branch already exists with different work. It will not be overwritten; use a new run or submit manually.")
        state["branch_created"] = True
        save()
    elif state.get("branch_created"):
        raise UIError("The previously created branch was removed. Inspect the existing PR before submitting manually.")
    else:
        if not state.get("commit_sha"):
            tip = github.api(f"{base}/commits/{quote(repo['default_branch'], safe='')}")
            prefix = destination(result)
            # Never replace an existing public bundle, even when local main is behind.
            if github.optional(f"{base}/contents/{quote(prefix, safe='/')}?" + urlencode({"ref": tip["sha"]})):
                raise UIError("The destination bundle already exists on GitHub. It will not be overwritten.")
            elements = []
            for name, content in contents.items():
                blob = github.api(f"{base}/git/blobs", {"content": base64.b64encode(content).decode(), "encoding": "base64"})
                elements.append({"path": f"{prefix}/{name}", "mode": "100644", "type": "blob", "sha": blob["sha"]})
            tree = github.api(f"{base}/git/trees", {"base_tree": tip["commit"]["tree"]["sha"], "tree": elements})
            title, _ = description(result, prefix)
            commit = github.api(f"{base}/git/commits", {"message": title, "tree": tree["sha"], "parents": [tip["sha"]]})
            state["commit_sha"] = commit["sha"]
            save()  # Save before creating the ref so a lost HTTP response is safe to retry.
        github.api(f"{base}/git/refs", {"ref": f"refs/heads/{branch}", "sha": state["commit_sha"]})
        state["branch_created"] = True
        save()
    query = urlencode({"head": f"{repo['owner']['login']}:{branch}", "state": "all"})
    existing = github.api(f"{base}/pulls?{query}")
    if existing:
        pull = existing[0]
    else:
        title, body = description(result, destination(result))
        pull = github.api(f"{base}/pulls", {"title": title, "body": body, "head": branch, "base": repo["default_branch"]})
    state["pull_request_url"] = pull["html_url"]
    save()
