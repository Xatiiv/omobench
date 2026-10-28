"""Review-bound community submissions, independent of git, gh, or a checkout.

Only new objects and a new submission ref are written, in the authenticated
account's public fork. Existing refs, evidence and repositories are never replaced.
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from urllib.parse import quote, urlencode

from .github_auth import UPSTREAM, identity
from .ui import UIError
from .ui_submission import bundle_snapshot, description, destination


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise UIError("GitHub returned an invalid source identity.")
    return value


def upstream_repository(api):
    repo = api.api(f"repos/{UPSTREAM}")
    if (not isinstance(repo, dict) or repo.get("full_name", "").lower() != UPSTREAM.lower()
            or repo.get("private") is not False or type(repo.get("id")) is not int
            or not isinstance(repo.get("default_branch"), str) or not repo["default_branch"]):
        raise UIError("The community registry could not be identified as the expected public repository.")
    return repo


def owned_repository(repo, actor, upstream):
    """A matching name is insufficient: require account and fork-network IDs."""
    if (not isinstance(repo, dict) or type(repo.get("id")) is not int
            or repo.get("private") is not False or identity(repo.get("owner")) != actor
            or not isinstance(repo.get("full_name"), str)
            or not re.fullmatch(re.escape(actor["login"]) + r"/[A-Za-z0-9_.-]+", repo["full_name"])):
        raise UIError("The upload repository is not a public repository owned by the signed-in account.")
    own_upstream = repo["id"] == upstream["id"] and actor["id"] == upstream["owner"]["id"]
    network = (repo.get("source") or {}).get("id") or (repo.get("parent") or {}).get("id")
    if not own_upstream and (repo.get("fork") is not True or network != upstream["id"]):
        raise UIError("That repository is unrelated to OmoBench. It will not be overwritten; use a different GitHub account or submit manually.")
    if repo.get("permissions", {}).get("push") is not True:
        raise UIError("GitHub has not granted write access to this fork. Check app access and review again.")
    return repo


def find_upload_repository(api, actor, upstream, previous):
    if previous.get("upload_repository"):
        repo = api.optional("repos/" + previous["upload_repository"])
        if repo is None:
            if previous.get("upload_id"):
                raise UIError("The previously selected fork was removed. Inspect the submission before continuing manually.")
            return None, previous["upload_repository"]
        repo = owned_repository(repo, actor, upstream)
        if previous.get("upload_id") and repo["id"] != previous["upload_id"]:
            raise UIError("The fork's identity changed. The replacement will not be used.")
        return repo, repo["full_name"]
    if upstream["owner"]["id"] == actor["id"]:
        return owned_repository(upstream, actor, upstream), upstream["full_name"]
    name = actor["login"] + "/" + UPSTREAM.split("/")[1]
    repo = api.optional("repos/" + name)
    if repo is not None:
        return owned_repository(repo, actor, upstream), repo["full_name"]
    # A contributor may have renamed their fork. Bounded, read-only discovery.
    for page in range(1, 6):
        forks = api.api(f"repos/{UPSTREAM}/forks?per_page=100&page={page}")
        if not isinstance(forks, list):
            raise UIError("GitHub did not return a valid fork listing.")
        for fork in forks:
            if (fork.get("owner") or {}).get("id") == actor["id"]:
                full_name = fork.get("full_name", "")
                if not re.fullmatch(re.escape(actor["login"]) + r"/[A-Za-z0-9_.-]+", full_name):
                    raise UIError("GitHub returned an invalid fork identity.")
                repo = owned_repository(api.api("repos/" + full_name), actor, upstream)
                return repo, full_name
        if len(forks) < 100:
            return None, name
    raise UIError("Could not finish checking existing forks. Use the bundle download and manual submission workflow.")


def check_previous(state, actor, digest):
    if not state:
        return
    if (state.get("mode") != "community" or state.get("account") != actor
            or state.get("bundle_digest") != digest or state.get("repository") != UPSTREAM):
        raise UIError("This run already has a submission under another account, route or bundle. Return to its original account and route; nothing will be overwritten.")


def prepare_community(root: Path, run: Path, session, state: dict):
    result, contents, digest = bundle_snapshot(root, run)
    prefix = destination(result)
    title, body = description(result, prefix)
    preview = {"mode": "community", "files": [
        {"path": f"{prefix}/{name}", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in contents.items()], "result": result, "title": title, "body": body,
        "bundle_digest": digest, "repository": UPSTREAM, "can_submit": False, "message": ""}
    try:
        api, actor, generation = session.client()
        if identity(api.api("user")) != actor:
            raise UIError("The GitHub account changed. Sign in and review again.")
        check_previous(state, actor, digest)
        upstream = upstream_repository(api)
        repo, upload_name = find_upload_repository(api, actor, upstream, state)
        tip = api.api(f"repos/{UPSTREAM}/commits/{quote(upstream['default_branch'], safe='')}")
        base_sha = sha(state.get("base_sha") or tip["sha"])
        base_tree = sha(state.get("base_tree") or tip["commit"]["tree"]["sha"])
        review = {"account": actor, "auth_generation": generation, "bundle_digest": digest,
                  "repository": UPSTREAM, "upstream_id": upstream["id"], "base_branch": upstream["default_branch"],
                  "base_sha": base_sha, "base_tree": base_tree, "upload_repository": upload_name,
                  "upload_id": repo["id"] if repo else None}
        if session.snapshot()["generation"] != generation:
            raise UIError("GitHub sign-in changed. Review again.")
        preview.update(account=actor, upload_repository=upload_name, base_branch=review["base_branch"],
                       needs_fork=repo is None, can_submit=True,
                       message="Uploads these five files publicly to " + upload_name +
                       ", then opens a pull request in " + UPSTREAM +
                       ". CI and maintainer review are required before the result appears in comparisons.")
        return preview, review
    except UIError as error:
        preview["message"] = str(error)
        return preview, None


def submit_community(root, run, session, review, state, save):
    result, contents, digest = bundle_snapshot(root, run)
    api, actor, generation = session.client()
    if (generation != review["auth_generation"] or actor != review["account"]
            or digest != review["bundle_digest"] or identity(api.api("user")) != actor):
        raise UIError("The account or evidence changed since review. Review the submission again.")
    check_previous(state, actor, digest)
    upstream = upstream_repository(api)
    if upstream["id"] != review["upstream_id"] or upstream["default_branch"] != review["base_branch"]:
        raise UIError("The registry changed since review. Review the destination again.")
    repo = api.optional("repos/" + review["upload_repository"])
    if repo is not None:
        owned_repository(repo, actor, upstream)
        if review["upload_id"] is not None and repo["id"] != review["upload_id"]:
            raise UIError("The upload repository changed since review. Nothing will be overwritten.")
    elif review["upload_id"] is not None:
        raise UIError("The reviewed fork is no longer available.")
    prefix = destination(result)
    if not state.get("commit_sha"):
        tip = api.api(f"repos/{UPSTREAM}/commits/{quote(review['base_branch'], safe='')}")
        if api.optional(f"repos/{UPSTREAM}/contents/{quote(prefix, safe='/')}?" + urlencode({"ref": sha(tip["sha"])})) is not None:
            raise UIError("This bundle already exists in the community registry. It will not be overwritten.")
    # Persist binding before the first remote write, including a possibly slow fork.
    state.update(mode="community", account=actor, repository=UPSTREAM,
                 upload_repository=review["upload_repository"], upload_id=repo["id"] if repo else None,
                 base_branch=review["base_branch"], base_sha=review["base_sha"], base_tree=review["base_tree"],
                 bundle_digest=digest, branch="submit/" + result["run_id"], started=True)
    save()

    def write(path, payload):
        # Closing the panel invalidates the session, preventing further writes.
        if session.snapshot()["generation"] != generation:
            raise UIError("GitHub sign-in ended. Local evidence and submission progress are preserved.")
        return api.api(path, payload)

    if repo is None:
        created = write(f"repos/{UPSTREAM}/forks", {"name": review["upload_repository"].split("/")[1], "default_branch_only": True})
        if not isinstance(created, dict) or created.get("full_name") != review["upload_repository"]:
            raise UIError("GitHub selected a different fork. Review again before any evidence is uploaded.")
        # The asynchronous creation response need not contain source/permissions.
        # Read the repository itself before trusting it as an upload destination.
        repo = api.optional("repos/" + review["upload_repository"])
        if repo is None:
            raise UIError("GitHub is still preparing your fork. Wait a moment, review again and retry. Local evidence is preserved.")
        owned_repository(repo, actor, upstream)
        state["upload_id"] = repo["id"]
        save()
    base = "repos/" + review["upload_repository"]
    if api.optional(f"{base}/git/commits/{review['base_sha']}") is None:
        raise UIError("GitHub is still preparing your fork. Wait a moment, review again and retry. Local evidence is preserved.")
    ref = api.optional(f"{base}/git/ref/heads/{quote(state['branch'], safe='')}")
    if ref is not None:
        if not state.get("commit_sha") or ref.get("object", {}).get("sha") != state["commit_sha"]:
            raise UIError("That submission branch already contains different work. It will not be overwritten.")
    elif state.get("branch_created"):
        raise UIError("The submission branch was removed. Inspect its PR before submitting manually.")
    else:
        if not state.get("commit_sha"):
            elements = []
            for name, data in contents.items():
                blob = write(f"{base}/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
                elements.append({"path": f"{prefix}/{name}", "mode": "100644", "type": "blob", "sha": sha(blob["sha"])})
            tree = write(f"{base}/git/trees", {"base_tree": review["base_tree"], "tree": elements})
            title, _ = description(result, prefix)
            commit = write(f"{base}/git/commits", {"message": title, "tree": sha(tree["sha"]), "parents": [review["base_sha"]]})
            state["commit_sha"] = sha(commit["sha"])
            save()  # A lost ref response can now be reconciled without a second commit.
        write(f"{base}/git/refs", {"ref": "refs/heads/" + state["branch"], "sha": state["commit_sha"]})
    state["branch_created"] = True
    save()
    query = urlencode({"head": actor["login"] + ":" + state["branch"], "base": review["base_branch"], "state": "all"})
    pulls = api.api(f"repos/{UPSTREAM}/pulls?{query}")
    if not isinstance(pulls, list) or len(pulls) > 1:
        raise UIError("GitHub returned an ambiguous PR history. Inspect the submission branch before continuing.")
    if pulls:
        pull = pulls[0]
    else:
        title, body = description(result, prefix)
        pull = write(f"repos/{UPSTREAM}/pulls", {"title": title, "body": body,
                     "head": actor["login"] + ":" + state["branch"], "head_repo": repo["name"], "base": review["base_branch"]})
    if (pull.get("head", {}).get("sha") != state["commit_sha"]
            or pull.get("head", {}).get("repo", {}).get("id") != state["upload_id"]
            or pull.get("base", {}).get("repo", {}).get("id") != upstream["id"]
            or pull.get("base", {}).get("ref") != review["base_branch"]
            or type(pull.get("number")) is not int or pull["number"] <= 0):
        raise UIError("The PR no longer matches this submission. Inspect it on GitHub before continuing.")
    state["pull_request_url"] = f"https://github.com/{UPSTREAM}/pull/{pull['number']}"
    state["pull_request_state"] = "merged" if pull.get("merged_at") else pull.get("state", "unknown")
    save()
