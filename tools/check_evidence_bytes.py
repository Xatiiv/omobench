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
touched. Missing and untracked files are never touched either.

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


def tracked_evidence(root: Path) -> list[tuple[str, str]]:
    """(path, index blob id) for every tracked evidence file."""
    listed = git(root, "ls-files", "-s", "-z", "--", EVIDENCE_PATHSPEC)
    entries = []
    for record in listed.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        _mode, blob, _stage = meta.decode().split()
        entries.append((path.decode("utf-8"), blob))
    return entries


def head_blobs(root: Path) -> dict[str, str]:
    """Blob ids at HEAD, to recognise a path with a staged change."""
    listed = git(root, "ls-tree", "-r", "-z", "HEAD", "--", "results")
    blobs = {}
    for record in listed.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        blobs[path.decode("utf-8")] = meta.decode().split()[2]
    return blobs


def blob_bytes(root: Path, blob: str) -> bytes:
    return git(root, "cat-file", "blob", blob)


def classify(committed: bytes, on_disk: bytes) -> str:
    if on_disk == committed:
        return MATCHES
    if b"\r" not in committed and committed.replace(b"\n", b"\r\n") == on_disk:
        return LINE_ENDINGS
    return EDITED


def inspect(root: Path) -> list[Finding]:
    at_head = head_blobs(root)
    findings = []
    for path, blob in tracked_evidence(root):
        if at_head.get(path) not in (None, blob):
            findings.append(Finding(path, STAGED))
            continue
        target = root / path
        if not target.is_file():
            findings.append(Finding(path, MISSING))
            continue
        findings.append(Finding(path, classify(blob_bytes(root, blob), target.read_bytes())))
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
    git(root, "update-index", "-q", "--refresh", "--", path)


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
                         (STAGED, "staged change (left alone)"),
                         (MISSING, "missing from the working tree (left alone)")):
        for path in by_state.get(state, []):
            print(f"  {label}: {path}")

    converted = by_state.get(LINE_ENDINGS, [])
    if args.restore_line_endings and converted:
        blobs = dict(tracked_evidence(root))
        for path in converted:
            restore(root, path, blobs[path])
        print(f"Restored the committed bytes of {len(converted)} file(s).")
        converted = []

    problems = converted + by_state.get(EDITED, []) + by_state.get(STAGED, []) + by_state.get(MISSING, [])
    if by_state.get(LINE_ENDINGS) and not args.restore_line_endings:
        print("Run again with --restore-line-endings to restore the files converted by checkout.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
