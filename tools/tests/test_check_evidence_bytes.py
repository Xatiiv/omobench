"""Evidence bytes survive a core.autocrlf=true checkout, and affected checkouts can recover.

Each test builds a throwaway repository, commits LF evidence under
results/<model>/<run>/, and clones it with core.autocrlf=true -- the setting
that rewrote published evidence on Windows. Git applies that conversion on
every platform, so these tests reproduce the Windows checkout on Linux CI too.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import check_evidence_bytes as checker  # noqa: E402

EVIDENCE = "results/org-model/2026-01-01-run-abc/"
LF_FILES = {
    EVIDENCE + "result.json": b'{\n  "run_id": "run-abc"\n}\n',
    EVIDENCE + "reproduce.md": b"# Reproduce\n\nomobench run\n",
    EVIDENCE + "guidellm.stdout.log": b"line one\nline two\n",
}
GENUINE_CRLF = (EVIDENCE + "environment.json", b'{\r\n  "os": "windows"\r\n}\r\n')
NOT_EVIDENCE = ("results/README.md", b"# Results\n\nAbout this folder.\n")


def git(cwd: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True).stdout


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class AutocrlfCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temporary, ignore_errors=True)

    def origin(self, attributes: bytes) -> Path:
        origin = self.temporary / "origin"
        origin.mkdir()
        git(origin, "init", "-q")
        git(origin, "config", "core.autocrlf", "false")
        (origin / ".gitattributes").write_bytes(attributes)
        for path, data in {**LF_FILES, GENUINE_CRLF[0]: GENUINE_CRLF[1], NOT_EVIDENCE[0]: NOT_EVIDENCE[1]}.items():
            (origin / path).parent.mkdir(parents=True, exist_ok=True)
            (origin / path).write_bytes(data)
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", "evidence")
        return origin

    def clone(self, origin: Path, name: str) -> Path:
        target = self.temporary / name
        git(self.temporary, "-c", "core.autocrlf=true", "clone", "-q", str(origin), str(target))
        git(target, "config", "core.autocrlf", "true")
        return target

    def committed(self, clone: Path, path: str) -> bytes:
        return git(clone, "cat-file", "blob", f"HEAD:{path}")

    def test_the_repository_rule_keeps_every_evidence_byte(self):
        clone = self.clone(self.origin((ROOT / ".gitattributes").read_bytes()), "fixed")
        for path in [*LF_FILES, GENUINE_CRLF[0]]:
            with self.subTest(path=path):
                self.assertEqual((clone / path).read_bytes(), self.committed(clone, path))
        # Control: autocrlf really is converting in this clone. A file the rule
        # does not cover gets CRLF, so the evidence above was protected by the
        # rule rather than by an inactive setting.
        self.assertEqual((clone / NOT_EVIDENCE[0]).read_bytes(), NOT_EVIDENCE[1].replace(b"\n", b"\r\n"))
        self.assertTrue(all(f.state == checker.MATCHES for f in checker.inspect(clone)))

    def test_committing_a_bundle_keeps_its_carriage_returns(self):
        origin = self.origin((ROOT / ".gitattributes").read_bytes())
        clone = self.clone(origin, "fixed")
        added = EVIDENCE.replace("run-abc", "run-def") + "guidellm.stdout.log"
        data = b"progress\r\nprogress\r\ndone\n"
        (clone / added).parent.mkdir(parents=True)
        (clone / added).write_bytes(data)
        git(clone, "add", added)
        git(clone, "commit", "-q", "-m", "new bundle")
        self.assertEqual(self.committed(clone, added), data)

    def test_without_the_rule_checkout_converts_and_recovery_restores_exact_bytes(self):
        clone = self.clone(self.origin(b""), "affected")
        converted = [p for p in LF_FILES if (clone / p).read_bytes() != self.committed(clone, p)]
        self.assertEqual(sorted(converted), sorted(LF_FILES), "the unprotected checkout did not convert")

        # Git still calls the tree clean, which is why the tool reads bytes.
        self.assertEqual(git(clone, "status", "--porcelain", "--", "results").strip(), b"")

        # Someone's real edit, an untracked note, a staged change and a deletion.
        edited = EVIDENCE + "reproduce.md"
        (clone / edited).write_bytes((clone / edited).read_bytes() + b"my note\r\n")
        (clone / "results" / "notes.txt").write_bytes(b"mine\n")
        staged = EVIDENCE + "guidellm.stdout.log"
        (clone / staged).write_bytes(b"staged edit\n")
        git(clone, "add", staged)
        missing = GENUINE_CRLF[0]
        (clone / missing).unlink()

        states = {f.path: f.state for f in checker.inspect(clone)}
        self.assertEqual(states[EVIDENCE + "result.json"], checker.LINE_ENDINGS)
        self.assertEqual(states[edited], checker.EDITED)
        self.assertEqual(states[staged], checker.STAGED)
        self.assertEqual(states[missing], checker.MISSING)

        self.assertEqual(checker.main(["--root", str(clone)]), 1)  # reporting changes nothing
        self.assertNotEqual((clone / EVIDENCE / "result.json").read_bytes(),
                            self.committed(clone, EVIDENCE + "result.json"))

        self.assertEqual(checker.main(["--root", str(clone), "--restore-line-endings"]), 1)
        self.assertEqual((clone / EVIDENCE / "result.json").read_bytes(),
                         self.committed(clone, EVIDENCE + "result.json"))
        self.assertTrue((clone / edited).read_bytes().endswith(b"my note\r\n"))
        self.assertEqual((clone / staged).read_bytes(), b"staged edit\n")
        self.assertFalse((clone / missing).exists())
        self.assertEqual((clone / "results" / "notes.txt").read_bytes(), b"mine\n")

    def test_genuine_carriage_returns_are_never_called_a_conversion(self):
        committed = b"a\r\nb\n"
        self.assertEqual(checker.classify(committed, committed.replace(b"\n", b"\r\n")), checker.EDITED)
        self.assertEqual(checker.classify(b"a\nb\n", b"a\r\nb\r\n"), checker.LINE_ENDINGS)
        self.assertEqual(checker.classify(b"a\nb\n", b"a\r\nb\n"), checker.EDITED)
        self.assertEqual(checker.classify(b"a\n", b"a\n"), checker.MATCHES)


class PublishedEvidenceAttributeTests(unittest.TestCase):
    """The real repository: every bundle file has conversion off, nothing else does."""

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_every_tracked_bundle_file_has_conversion_off(self):
        listed = subprocess.run(["git", "ls-files", "-z", "results"], cwd=ROOT,
                                capture_output=True, check=False)
        if listed.returncode != 0:
            self.skipTest("not a git checkout")
        paths = [p for p in listed.stdout.split(b"\0") if p]
        checked = subprocess.run(["git", "check-attr", "-z", "--stdin", "text"], cwd=ROOT,
                                 input=b"\0".join(paths) + b"\0", capture_output=True, check=True).stdout
        fields = checked.split(b"\0")
        verdicts = {fields[i].decode(): fields[i + 2].decode() for i in range(0, len(fields) - 2, 3)}
        bundle_files = [p for p in verdicts if p.count("/") == 3]
        self.assertTrue(bundle_files, "no published bundle files found")
        for path in bundle_files:
            with self.subTest(path=path):
                self.assertEqual(verdicts[path], "unset")
        self.assertEqual(verdicts.get("results/README.md"), "unspecified")


if __name__ == "__main__":
    unittest.main()
