"""The prototype displays the existing catalog/profiles; it is not another registry."""

import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from preview_launcher import PROFILE_SPECS, render_preview


class LauncherPreviewTests(unittest.TestCase):
    def render_catalog(self, models):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog" / "models"
            catalog.mkdir(parents=True)
            (catalog / "test.yaml").write_text(yaml.safe_dump({"models": models}), encoding="utf-8")
            shutil.copytree(ROOT / "design" / "launcher", root / "design" / "launcher")
            return render_preview(root)

    def test_new_catalog_model_and_existing_profiles_are_used_without_code_changes(self):
        html = self.render_catalog([{"id": "another/model", "modality": "text-generation", "licence": "Apache-2.0"}])
        data = json.loads(re.search(r'<script id="preview-data" type="application/json">(.*?)</script>', html).group(1))
        self.assertEqual(data["models"], [{"id": "another/model", "parameter_class": None, "licence": "Apache-2.0"}])
        self.assertEqual(data["profiles"], PROFILE_SPECS)
        self.assertIn("No downloads or benchmarks run here.", html)

    def test_catalog_text_cannot_break_out_of_embedded_json(self):
        name = '</script><script>alert("catalog")</script>'
        html = self.render_catalog([{"id": name, "modality": "text-generation", "licence": "test"}])
        encoded = re.search(r'<script id="preview-data" type="application/json">(.*?)</script>', html).group(1)
        self.assertNotIn("<", encoded)
        self.assertEqual(json.loads(encoded)["models"][0]["id"], name)


if __name__ == "__main__":
    unittest.main()
