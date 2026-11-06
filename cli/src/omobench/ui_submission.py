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
# A sixth file, present only when the result proposes a model for the catalog.
# It is part of the same bundle and the same prefix: a submission still writes
# to exactly one directory, and this never becomes a second write target.
PROPOSAL_FILE = "model-proposal.json"
ARTIFACT_FILES = {
    "raw_runner_output": "guidellm-benchmarks.json.gz",
    "runner_stdout_log": "guidellm.stdout.log",
    "environment_snapshot": "environment.json",
    "reproduce_markdown": "reproduce.md",
}


def bundle_filenames(run: Path) -> tuple[str, ...]:
    """The files this bundle is made of, including a proposal when it has one."""
    proposal = run / PROPOSAL_FILE
    if proposal.is_file() and not proposal.is_symlink():
        return (*BUNDLE_FILES, PROPOSAL_FILE)
    return BUNDLE_FILES


def validate_bundle(root: Path, run: Path) -> list[str]:
    """Call the repository's actual validator, including registry duplicate checks."""
    root, run = root.resolve(), run.resolve()
    if not run.is_relative_to(root / ".omobench-runs"):
        raise UIError("Only local staging evidence can be submitted from this panel.")
    for name in bundle_filenames(run):
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
        proposals = module.Draft202012Validator(module.load_proposal_schema(),
                                                format_checker=module.FormatChecker())
        run_ids, duplicate_keys, proposed = set(), set(), {}
        errors = []
        for result_path in sorted(module.RESULTS_PATH.rglob("result.json")):
            errors.extend(module.validate_result(result_path, validator, catalog, run_ids,
                                                 duplicate_keys, proposals, proposed))
        errors.extend(module.validate_result(run / "result.json", validator, catalog, run_ids,
                                             duplicate_keys, proposals, proposed))
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


# The validator marks its own publication-acceptance findings. A bundle whose
# only findings are these is a complete, schema-valid, hash-correct bundle that
# the registry will not take -- which is a different fact from a bundle that
# could not be built, and the two must not be collapsed.
ACCEPTANCE_MARKER = ": acceptance: "
INTERRUPTION_MARKER = ": interruption: "
# The two reasons the registry refuses a bundle that is otherwise complete and
# schema-valid: it did not measure enough, or what it measured is not about
# the model because the machine was suspended partway through. Both stop
# publication. Neither is a reason to stop somebody looking at their own
# evidence -- which is the one view that lets them check it for secrets, and
# the only way to work out what went wrong.
DIAGNOSABLE_MARKERS = (ACCEPTANCE_MARKER, INTERRUPTION_MARKER)


def diagnosable(error: str) -> bool:
    return any(marker in error for marker in DIAGNOSABLE_MARKERS)


def acceptance_only(errors: list[str]) -> bool:
    return bool(errors) and all(diagnosable(error) for error in errors)


def bundle_snapshot(root: Path, run: Path, *,
                    allow_unacceptable: bool = False) -> tuple[dict, dict[str, bytes], str]:
    """The exact bytes that would be shared, and their digest.

    `allow_unacceptable` lets a contributor look at and download their own
    evidence when the registry's acceptance thresholds are the only thing
    standing in the way. Reading your own files is not publishing them, and
    refusing to show somebody the bundle they are being told not to submit
    takes away the one view that would let them check it for secrets. It never
    relaxes anything else, and submission never passes it.
    """
    errors = validate_bundle(root, run)
    if errors and not (allow_unacceptable and acceptance_only(errors)):
        raise UIError("Bundle validation failed:\n" + "\n".join(errors))
    contents = {name: (run / name).read_bytes() for name in bundle_filenames(run)}
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
    # Absent only on a result predating execution-device tracking; those are GPU runs.
    device_label = "CPU" if result.get("execution_device") == "cpu" else "GPU"
    environment = result.get("environment") or {}
    serving = environment.get("serving") or {}
    transport = environment.get("transport") or {}
    extra = ""
    if environment:
        engine = (serving.get("runtime") or {}).get("engine") or {}
        application = (serving.get("runtime") or {}).get("application")
        placement = (serving.get("placement") or {}).get("mode", "unknown")
        extra = (
            f"- Serving environment: {environment.get('relationship')}\n"
            f"- Serving engine: {engine.get('name')} {engine.get('version') or 'version unknown'}"
            + (f" inside {application['name']} {application['version']}" if application else "")
            + "\n"
            f"- Weight placement: {placement}\n"
            f"- Transport: {transport.get('kind')}"
            + (" — its latency is inside every measured number here and has not been subtracted"
               if transport.get("overhead") == "included-not-measured" else "")
            + "\n")
        if result["model"].get("served_model_id"):
            extra += (f"- Served identifier: `{result['model']['served_model_id']}` "
                      "(a serving alias, confirmed against the catalog by the contributor)\n")
        if (result["submission"].get("packaging") or {}).get("metadata_declared_after_execution"):
            extra += ("- Serving metadata was declared after the run, from its saved evidence; "
                      "nothing here is recorded as detected at run time\n")
    proposal_note = ""
    if result["model"].get("catalog_status") == "proposed":
        proposal_note = (
            f"\n**This bundle proposes `{result['model']['id']}` for the model catalog.** It is not "
            "a catalogued model yet, and merging this result does not make it one: the proposal in "
            "`model-proposal.json` is a request for review, and adding the catalog entry is a "
            "separate decision.\n")
    body = (
        f"Adds the complete evidence bundle at `{prefix}`.\n\n"
        f"- Model: {result['model']['id']} at `{result['model']['revision']}`\n"
        f"- Runtime: {result['runtime']['framework']} {result['runtime']['version']}\n"
        f"- Profile: `{result['benchmark']['profile_id']}`\n"
        f"- Execution device: {device_label}\n"
        + extra +
        f"- Successful requests: {metrics['requests']['succeeded']} / {metrics['requests']['attempted']}\n"
        f"- Output tokens/s: {metrics['output_tokens_per_second']:.2f}\n"
        + proposal_note +
        f"\nThe contributor confirms {device_label} execution and has reviewed the bundle for secrets. "
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
    result, contents, digest = bundle_snapshot(root, run, allow_unacceptable=True)
    prefix = destination(result)
    title, body = description(result, prefix)
    unacceptable = [error for error in validate_bundle(root, run) if diagnosable(error)]
    preview = {"files": [{"path": f"{prefix}/{name}", "bytes": len(content),
                          "sha256": hashlib.sha256(content).hexdigest()} for name, content in contents.items()],
               "result": result, "title": title, "body": body, "bundle_digest": digest,
               "repository": None, "can_submit": False, "message": "",
               "acceptance": unacceptable}
    if unacceptable:
        # Shown, not submitted: the files are here to inspect and download, and
        # the registry's own answer is stated rather than hidden behind a
        # refusal to render anything.
        preview["message"] = ("This bundle is complete and schema-valid, and the public "
                              "registry will not take it: " + " ".join(unacceptable)
                              + " You can review and download it; submission stays closed.")
        return preview
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
    # Your own evidence, on your own machine. Downloading it is not publishing.
    result, contents, _ = bundle_snapshot(root, run, allow_unacceptable=True)
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
