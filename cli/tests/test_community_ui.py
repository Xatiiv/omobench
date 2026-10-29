import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

class CommunityUITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required to exercise the shipped UI scripts")
    def test_sign_in_and_review_controls_use_real_shared_script(self):
        result = subprocess.run(["node", str(Path(__file__).with_name("community_harness.mjs")),
                                 str(ROOT / "cli/src/omobench/web")], text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
