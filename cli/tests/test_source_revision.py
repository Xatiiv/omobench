"""Which source is serving the panel, and how it knows.

Installing a newer companion does not move the panel to a newer revision: a
managed installation keeps running the source it was unpacked with until an
upgrade switches it over, and a checkout somebody selected runs whatever is in
that checkout. The panel therefore reports its own source rather than letting
it be inferred from a version file describing a different program.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from omobench.ui import Controller


ROOT = Path(__file__).resolve().parents[2]
REVISION = "a" * 40


def make_root(root: Path) -> None:
    for relative in ("benchmarks", "catalog", "schema", "tools"):
        shutil.copytree(ROOT / relative, root / relative,
                        ignore=shutil.ignore_patterns("__pycache__"))
    (root / "results").mkdir()


class SourceRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        make_root(self.root)
        self.controller = Controller(self.root)
        self.addCleanup(self.controller.close)

    def marker(self, contents: str) -> None:
        (self.root / Controller.SOURCE_REVISION_MARKER).write_text(contents, encoding="utf-8")

    # -- an installed, managed source -----------------------------------------

    def test_a_managed_source_reports_the_revision_the_installer_recorded(self) -> None:
        self.marker(REVISION + "\n")
        answer = self.controller.source_revision()
        self.assertEqual(answer["revision"], REVISION)
        self.assertEqual(answer["source"], "installer")
        self.assertIn("installer", answer["detail"])
        self.assertEqual(answer["path"], str(self.root))

    def test_the_marker_is_the_installer_side_name(self) -> None:
        """Two programs that never import each other have to agree on it."""
        self.assertEqual(Controller.SOURCE_REVISION_MARKER, ".omobench-source-revision")
        marker = (ROOT / "windows" / "installer" / "src" / "ManagedEnvironment.cs").read_text(
            encoding="utf-8")
        self.assertIn('RevisionMarkerName = ".omobench-source-revision"', marker)

    def test_a_marker_that_is_not_a_revision_is_not_believed(self) -> None:
        for rubbish in ("", "   ", "main", "not-a-sha", "zzzz" * 10, "a" * 41):
            with self.subTest(contents=rubbish):
                self.marker(rubbish)
                answer = self.controller.source_revision()
                self.assertNotEqual(answer["source"], "installer")

    # -- a checkout of the contributor's own ----------------------------------

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(self.root), *arguments],
                       capture_output=True, check=True)

    def make_checkout(self) -> str:
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        # Everything the panel root carries, committed, so the checkout
        # starts clean and "edited since" means what the next test does.
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        head = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True)
        return head.stdout.strip()

    def test_a_checkout_reports_its_commit_and_says_it_is_a_checkout(self) -> None:
        head = self.make_checkout()
        answer = self.controller.source_revision()
        self.assertEqual(answer["revision"], head)
        self.assertEqual(answer["source"], "checkout")
        self.assertFalse(answer["dirty"])
        self.assertIn("checkout of your own", answer["detail"])

    def test_an_edited_checkout_says_what_is_running_is_not_that_commit(self) -> None:
        self.make_checkout()
        (self.root / "README.md").write_text("edited\n", encoding="utf-8")
        answer = self.controller.source_revision()
        self.assertTrue(answer["dirty"])
        self.assertIn("not that commit exactly", answer["detail"])

    def test_the_installer_marker_wins_over_git(self) -> None:
        """A managed source is what the installer put there, whatever a
        surrounding repository happens to say."""
        self.make_checkout()
        self.marker(REVISION + "\n")
        answer = self.controller.source_revision()
        self.assertEqual(answer["revision"], REVISION)
        self.assertEqual(answer["source"], "installer")

    # -- neither --------------------------------------------------------------

    def test_a_source_that_records_nothing_says_so_rather_than_guessing(self) -> None:
        answer = self.controller.source_revision()
        self.assertIsNone(answer["revision"])
        self.assertEqual(answer["source"], "unknown")
        self.assertIn("neither a managed installation nor a git checkout", answer["detail"])

    def test_the_panel_payload_carries_it(self) -> None:
        self.marker(REVISION + "\n")
        self.assertEqual(self.controller.setup()["source_revision"]["revision"], REVISION)


if __name__ == "__main__":
    unittest.main()
