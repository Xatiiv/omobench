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

    def test_every_package_module_is_required_of_an_installed_payload(self):
        """Serving the panel is not the only way to 404.

        The route table only names web assets, so a Python module the panel
        imports could go missing from an installation without any route
        noticing -- and the panel would then import-error on the request that
        first needed it. Seven modules had already drifted off this list that
        way. Listing the package by directory rather than by hand is what
        keeps it honest.
        """
        required = set(required_paths())
        for module in sorted((WEB.parent).glob("*.py")):
            with self.subTest(module=module.name):
                self.assertIn(f"cli/src/omobench/{module.name}", required,
                              f"{module.name} is imported from the package but not required "
                              "of an installed payload")

    def test_every_shipped_profile_is_required_of_an_installed_payload(self):
        """A profile the panel offers but cannot read is a run nobody can package."""
        required = set(required_paths())
        for profile in sorted((ROOT / "benchmarks" / "profiles").glob("*.yaml")):
            with self.subTest(profile=profile.name):
                self.assertIn(f"benchmarks/profiles/{profile.name}", required)

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


class ExecutableScriptTests(unittest.TestCase):
    """Shell scripts that exec each other must be committed executable.

    `scripts/install-ci.sh` re-execs itself through `scripts/sites-env.sh`
    directly, not via `bash`, and `sites-env.sh` ends in `exec "$@"`. Both
    therefore need the executable bit *in the index*, not merely on a
    developer's disk. They were committed as 100644, so `npm run install:ci`
    failed on every fresh clone on Linux with

        scripts/sites-env.sh: Permission denied

    which left `node_modules/.bin/vinext` absent and `npm run build` reporting
    only "vinext is unavailable". A Windows checkout cannot notice: git sets
    core.filemode=false there, so the mode never round-trips through the
    working tree and only the index records the truth.
    """

    REQUIRED_EXECUTABLE = (
        "scripts/sites-env.sh",
        "scripts/install-ci.sh",
        "scripts/build-verified.sh",
    )

    def index_modes(self) -> dict[str, str]:
        import subprocess

        listed = subprocess.run(
            ["git", "ls-files", "-s", "--", "scripts"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        if listed.returncode != 0:
            raise unittest.SkipTest("git is not available to read the index")
        modes = {}
        for line in listed.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 4:
                modes[fields[3]] = fields[0]
        return modes

    def test_the_sites_scripts_are_executable_in_the_index(self):
        modes = self.index_modes()
        for path in self.REQUIRED_EXECUTABLE:
            with self.subTest(path=path):
                self.assertIn(path, modes, "script is not tracked")
                self.assertEqual(
                    modes[path], "100755",
                    f"{path} is committed as {modes[path]}; a fresh Linux clone cannot run it. "
                    "Fix with: git update-index --chmod=+x " + path)

    def test_every_tracked_shell_script_under_scripts_is_executable(self):
        """Not just the three: any new one has the same requirement."""
        modes = self.index_modes()
        for path, mode in sorted(modes.items()):
            if path.endswith(".sh"):
                with self.subTest(path=path):
                    self.assertEqual(mode, "100755", f"{path} is committed as {mode}")


class LineEndingTests(unittest.TestCase):
    """A profile's bytes are its identity, so they must not depend on the OS.

    Every result records `benchmark.profile_sha256`: the SHA-256 of the profile
    file in the checkout that produced the run, and the validator reconciles a
    bundle against that file by re-hashing it. Git's autocrlf rewrites these
    files to CRLF on a Windows checkout and still reports the tree as clean,
    because it compares normalized content -- so the same profile hashed
    differently on Windows than on Linux. `chat-small-extended-v1.yaml` was
    genuinely in that state: 7029edaf... on Linux, eedf8378... on Windows, and
    one local bundle recorded the CRLF value.

    The scope here is taken from `git check-attr`, not from a list repeated in
    this file: what is tested is exactly what .gitattributes claims, so the two
    cannot drift apart.
    """

    def pinned_to_lf(self) -> list[str]:
        """Every tracked path the repository's own rules pin to LF."""
        import subprocess

        listed = subprocess.run(["git", "ls-files"], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        if listed.returncode != 0:
            raise unittest.SkipTest("git is not available to read the index")
        paths = [line for line in listed.stdout.splitlines() if line]
        checked = subprocess.run(["git", "check-attr", "--stdin", "eol"], cwd=ROOT,
                                 input="\n".join(paths), capture_output=True, text=True,
                                 check=False)
        if checked.returncode != 0:
            raise unittest.SkipTest("git check-attr is unavailable")
        pinned = []
        for line in checked.stdout.splitlines():
            path, _, verdict = line.rpartition(": eol: ")
            if verdict.strip() == "lf" and path:
                pinned.append(path)
        return pinned

    def test_gitattributes_exists_and_pins_the_profiles(self):
        attributes = ROOT / ".gitattributes"
        self.assertTrue(attributes.is_file(),
                        "no .gitattributes: profile hashes would vary by platform")
        pinned = self.pinned_to_lf()
        for profile in sorted((ROOT / "benchmarks" / "profiles").glob("*.yaml")):
            relative = profile.relative_to(ROOT).as_posix()
            with self.subTest(path=relative):
                self.assertIn(relative, pinned)

    def test_the_rules_stay_narrow(self):
        """They cover source whose bytes matter, and nothing generated."""
        pinned = self.pinned_to_lf()
        self.assertTrue(pinned, "the rules match nothing at all")
        for path in pinned:
            with self.subTest(path=path):
                self.assertTrue(
                    path.startswith(("benchmarks/", "catalog/", "schema/", "scripts/")),
                    f"{path} is pinned but is outside the reviewed-source directories")
                self.assertTrue(path.endswith((".yaml", ".json", ".sh")), path)

    def test_every_pinned_path_really_is_lf_here(self):
        for path in self.pinned_to_lf():
            with self.subTest(path=path):
                self.assertNotIn(
                    b"\r\n", (ROOT / path).read_bytes(),
                    f"{path} is pinned to LF but has CRLF in this working tree, so anything "
                    "hashing it here disagrees with every other checkout")

    def test_an_unmodified_profile_is_byte_identical_to_its_committed_blob(self):
        """The hash a run records must be the hash of what was committed.

        Only profiles git reports as unmodified are compared: a profile being
        edited right now is legitimately different, and this is a check on
        checkout fidelity, not on whether the tree is clean. The failure it
        exists to catch is the silent one -- git calling a file unmodified
        while the bytes on disk, and therefore the hash a run would record,
        differ from the committed ones.
        """
        import subprocess

        for profile in sorted((ROOT / "benchmarks" / "profiles").glob("*.yaml")):
            relative = profile.relative_to(ROOT).as_posix()
            status = subprocess.run(["git", "status", "--porcelain", "--", relative],
                                    cwd=ROOT, capture_output=True, text=True, check=False)
            if status.returncode != 0:
                raise unittest.SkipTest("git is not available to read the working tree")
            if status.stdout.strip():
                continue  # being edited; its bytes are not meant to match HEAD
            with self.subTest(path=relative):
                blob = subprocess.run(["git", "show", f"HEAD:{relative}"], cwd=ROOT,
                                      capture_output=True, check=False)
                if blob.returncode != 0:
                    continue  # not committed yet
                self.assertEqual(
                    profile.read_bytes(), blob.stdout,
                    f"git reports {relative} unmodified, but the bytes on disk differ from "
                    "the committed ones, so this checkout would record a different "
                    "profile_sha256 than any other")
