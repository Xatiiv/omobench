"""Everything the panel serves must also be shipped to an installed user.

The panel reads its web assets from the installed package at request time, and
the Windows installer validates its payload against RuntimeManifest's required
list. If those two lists drift apart, a fresh installation serves a page whose
script or stylesheet 404s -- which is exactly how the shared dashboard broke on
a panel that was still running older code.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "cli" / "src" / "omobench" / "web"
MANIFEST = ROOT / "windows" / "installer" / "src" / "RuntimeManifest.cs"
UI = ROOT / "cli" / "src" / "omobench" / "ui.py"


def required_paths() -> list[str]:
    text = MANIFEST.read_text(encoding="utf-8")
    block = text.split("// required-begin")[1].split("// required-end")[0]
    return re.findall(r'"([^"]+)"', block)


def served_assets() -> dict[str, str]:
    """The static route table the panel actually answers from."""
    text = UI.read_text(encoding="utf-8")
    block = text.split("static = {", 1)[1].split("}", 1)[0]
    return dict(re.findall(r'"(/[^"]*)": \("([^"]+)"', block))


class ShippedAssetTests(unittest.TestCase):
    def test_every_served_asset_exists_in_the_package(self):
        for path, name in served_assets().items():
            with self.subTest(route=path):
                self.assertTrue((WEB / name).is_file(), f"{path} would 404: {name} is missing")

    def test_every_served_asset_is_required_by_the_installer_payload(self):
        required = set(required_paths())
        for path, name in served_assets().items():
            with self.subTest(route=path):
                self.assertIn(f"cli/src/omobench/web/{name}", required,
                              f"{name} is served but not required of an installed payload")

    def test_the_shared_dashboard_and_icon_are_among_them(self):
        routes = served_assets()
        self.assertEqual(routes["/dashboard.js"], "dashboard.js")
        self.assertEqual(routes["/dashboard.css"], "dashboard.css")
        self.assertEqual(routes["/favicon.svg"], "favicon.svg")

    def test_both_pages_declare_the_same_shared_assets(self):
        for name in ("guided.html", "index.html"):
            markup = (WEB / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertIn('<script src="/dashboard.js"', markup)
                self.assertIn('href="/dashboard.css"', markup)
                # A same-origin icon: the panel's own content security policy
                # is default-src 'self', which a data: URI violates.
                self.assertIn('href="/favicon.svg"', markup)
                self.assertNotIn('href="data:', markup)

    def test_no_page_references_an_asset_the_panel_does_not_serve(self):
        routes = set(served_assets())
        for name in ("guided.html", "index.html"):
            markup = (WEB / name).read_text(encoding="utf-8")
            for reference in re.findall(r'(?:src|href)="(/[^"]*)"', markup):
                with self.subTest(page=name, reference=reference):
                    self.assertTrue(reference in routes or reference in {"/", "/advanced", "/guided"},
                                    f"{name} references {reference}, which has no route")


if __name__ == "__main__":
    unittest.main()
