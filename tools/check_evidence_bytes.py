#!/usr/bin/env python3
"""Check that published evidence in this checkout has the bytes Git committed.

Every file in a bundle (results/<model>/<date>-<run>/<file>) is recorded by
SHA-256 in its result.json. A Windows checkout made with core.autocrlf=true
before .gitattributes switched conversion off for those files rewrote LF as
CRLF, so their hashes stopped matching. Git cannot be relied on to notice: its
status compares file timestamps first, so a converted file still reads as
unmodified.

This tool compares each tracked evidence file's raw bytes with its Git blob.

    python tools/check_evidence_bytes.py                         # report only
    python tools/check_evidence_bytes.py --restore-line-endings  # repair

Reporting changes nothing. --restore-line-endings rewrites a file with its
committed bytes only when the *only* difference is that line endings were
converted (the committed bytes contain no CR, and replacing every LF with CRLF
reproduces the file exactly), and only when nothing is staged for that path. A
file with any other difference is someone's edit: it is reported and never
touched. Only a file that exists at HEAD with an unchanged, normal (stage-0)
index entry is ever eligible: staged additions, modifications, deletions and
mode changes, unresolved merges, missing and untracked files are reported and
never touched.

It does not normalize anything before hashing, does not change recorded
hashes, and does not touch Git history.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATHSPEC = "results/*/*/*"

MATCHES = "matches"
LINE_ENDINGS = "line-endings-converted"
EDITED = "differs"
MISSING = "missing"
STAGED = "staged-change"
UNMERGED = "unmerged"
NOT_REGULAR = "not-a-regular-file"

# Only an ordinary file, executable or not, is ever rewritten. A symlink or a
# submodule entry is not evidence bytes this tool understands.
REGULAR_MODES = {"100644", "100755"}


@dataclass
class Finding:
    path: str
    state: str


def git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    """Run git with byte I/O, so no text-mode newline translation applies."""
    completed = subprocess.run(["git", *args], cwd=root, input=input,
                               capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: "
                           + completed.stderr.decode("utf-8", "replace").strip())
    return completed.stdout


@dataclass(frozen=True)
class Entry:
    mode: str
    blob: str


def tracked_evidence(root: Path) -> dict[str, dict[int, Entry]]:
    """Every evidence path in the index, with each stage it has.

    Stage 0 is a normal entry; stages 1-3 are the sides of an unresolved
    merge. Keeping the stage is what lets a conflict be recognised and left
    alone rather than read as one ordinary file.
    """
    listed = git(root, "ls-files", "-s", "-z", "--", EVIDENCE_PATHSPEC)
    entries: dict[str, dict[int, Entry]] = {}
    for record in listed.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, blob, stage = meta.decode().split()
        entries.setdefault(path.decode("utf-8"), {})[int(stage)] = Entry(mode, blob)
    return entries


def head_entries(root: Path) -> dict[str, Entry]:
    """Mode and blob of every evidence path at HEAD."""
    # ls-tree takes literal path prefixes, not glob pathspecs, so list
    # results/ and keep what EVIDENCE_PATHSPEC matches in the index: in a git
    # pathspec "*" also matches "/", so results/*/*/* is any path below
    # results/ with at least three separators.
    listed = git(root, "ls-tree", "-r", "-z", "HEAD", "--", "results")
    entries = {}
    for record in listed.split(b"\0"):
        if not record:
            continue
        meta, _, raw = record.partition(b"\t")
        path = raw.decode("utf-8")
        if path.count("/") < 3:
            continue
        mode, _kind, blob = meta.decode().split()
        entries[path] = Entry(mode, blob)
    return entries


def blob_bytes(root: Path, blob: str) -> bytes:
    return git(root, "cat-file", "blob", blob)


def classify(committed: bytes, on_disk: bytes) -> str:
    if on_disk == committed:
        return MATCHES
    if b"\r" not in committed and committed.replace(b"\n", b"\r\n") == on_disk:
        return LINE_ENDINGS
    return EDITED


def inspect(root: Path) -> list[Finding]:
    """Classify every evidence path in HEAD or the index.

    Only a path that exists at HEAD as a regular file, with exactly one normal
    (stage-0) index entry whose blob and mode are HEAD's, is compared with the
    working tree, and only such a path can ever be restored. Anything staged --
    an addition, a modification, a deletion, a mode change -- and any unresolved
    merge is somebody's work in progress and is reported, never touched.
    """
    at_head = head_entries(root)
    in_index = tracked_evidence(root)
    findings = []
    for path in sorted(set(at_head) | set(in_index)):
        stages = in_index.get(path, {})
        head = at_head.get(path)
        if set(stages) - {0}:
            findings.append(Finding(path, UNMERGED))
            continue
        index = stages.get(0)
        if head is None or index is None or index != head:
            # Staged addition (not at HEAD), staged deletion (not in the index),
            # or a staged change of content or mode.
            findings.append(Finding(path, STAGED))
            continue
        if index.mode not in REGULAR_MODES:
            findings.append(Finding(path, NOT_REGULAR))
            continue
        target = root / path
        if not target.is_file() or target.is_symlink():
            findings.append(Finding(path, MISSING))
            continue
        findings.append(Finding(path, classify(blob_bytes(root, index.blob), target.read_bytes())))
    return findings


def restore(root: Path, path: str, blob: str) -> None:
    """Write the committed bytes back, atomically, then let Git re-stat it."""
    target = root / path
    data = blob_bytes(root, blob)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".restore-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    # Only Git's stat cache: the bytes are already right. It exits non-zero
    # while any merge is unresolved, which is no reason to report a failure.
    subprocess.run(["git", "update-index", "-q", "--refresh", "--", path], cwd=root,
                   capture_output=True, check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--restore-line-endings", action="store_true",
                        help="rewrite files whose only difference is converted line endings")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.root.resolve()

    findings = inspect(root)
    by_state: dict[str, list[str]] = {}
    for finding in findings:
        by_state.setdefault(finding.state, []).append(finding.path)

    print(f"{len(findings)} tracked evidence file(s); "
          f"{len(by_state.get(MATCHES, []))} match their committed bytes.")
    for state, label in ((LINE_ENDINGS, "line endings converted by checkout"),
                         (EDITED, "content differs (a local edit; left alone)"),
                         (STAGED, "staged addition, change or deletion (left alone)"),
                         (UNMERGED, "unresolved merge (left alone)"),
                         (NOT_REGULAR, "not a regular file (left alone)"),
                         (MISSING, "missing from the working tree (left alone)")):
        for path in by_state.get(state, []):
            print(f"  {label}: {path}")

    converted = by_state.get(LINE_ENDINGS, [])
    if args.restore_line_endings and converted:
        at_head = head_entries(root)
        for path in converted:
            # inspect() only reports LINE_ENDINGS for a path whose stage-0 index
            # entry equals HEAD's, so HEAD's blob is the committed bytes.
            restore(root, path, at_head[path].blob)
        print(f"Restored the committed bytes of {len(converted)} file(s).")
        converted = []

    problems = converted + [path for state in (EDITED, STAGED, UNMERGED, NOT_REGULAR, MISSING)
                            for path in by_state.get(state, [])]
    if by_state.get(LINE_ENDINGS) and not args.restore_line_endings:
        print("Run again with --restore-line-endings to restore the files converted by checkout.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
