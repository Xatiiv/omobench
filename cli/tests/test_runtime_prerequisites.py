"""The managed runtime binary must be able to load, not merely exist.

A second machine passed every check OmoBench had -- platform, glibc version,
file hashes, execute permission, and the Python runner import check -- and then
failed at launch with

    llama-server: error while loading shared libraries: libgomp.so.1:
    cannot open shared object file: No such file or directory

because `libgomp1` was not installed. The Python check establishes that
OmoBench and GuideLLM import; `llama-server` is a separate native executable
and shares none of that. The failure arrived as "the managed model server
exited early with code 127", after the model had already been downloaded.

The central test here does not simulate that. It compiles a real shared
library, links a real executable against it, deletes the library, and asks the
real `ldd` -- so what is asserted is the loader's own answer to the same
question, on a genuine ELF. Nothing is installed and no system package is
touched.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from omobench import launcher, managed_setup


class ParsingTests(unittest.TestCase):
    """What the check makes of ldd output, without running ldd."""

    def check(self, stdout, *, returncode=0, stderr="", path=None):
        class Result:
            def __init__(self):
                self.stdout, self.stderr, self.returncode = stdout, stderr, returncode

        target = path or Path(__file__)  # any real file; ldd is not run
        return managed_setup.check_runtime_libraries(target, runner=lambda command: Result())

    def test_a_resolvable_binary_passes(self):
        status = self.check(
            "\tlinux-vdso.so.1 (0x00007ffc)\n"
            "\tlibgomp.so.1 => /lib/x86_64-linux-gnu/libgomp.so.1 (0x00007f00)\n"
            "\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f01)\n")
        self.assertTrue(status.ok)
        self.assertTrue(status.checked)
        self.assertEqual(status.missing, [])

    def test_the_reported_failure_is_named_with_its_package(self):
        status = self.check(
            "\tlinux-vdso.so.1 (0x00007ffc)\n"
            "\tlibgomp.so.1 => not found\n"
            "\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f01)\n",
            returncode=1)
        self.assertFalse(status.ok)
        self.assertTrue(status.checked)
        self.assertEqual(status.missing, ["libgomp.so.1"])
        self.assertEqual(status.packages, ["libgomp1"])
        self.assertIn("libgomp.so.1", status.message)
        self.assertIn("sudo apt update && sudo apt install -y libgomp1", status.detail)

    def test_the_advice_promises_no_automatic_install_and_no_redownload(self):
        status = self.check("\tlibgomp.so.1 => not found\n", returncode=1)
        self.assertIn("does not run it for you", status.detail)
        self.assertIn("nothing is downloaded again", status.detail.lower())

    def test_several_missing_libraries_are_all_named_once(self):
        status = self.check(
            "\tlibgomp.so.1 => not found\n"
            "\tlibstdc++.so.6 => not found\n"
            "\tlibgomp.so.1 => not found\n", returncode=1)
        self.assertEqual(status.missing, ["libgomp.so.1", "libstdc++.so.6"])
        self.assertEqual(status.packages, ["libgomp1", "libstdc++6"])

    def test_a_library_with_no_known_package_is_still_named(self):
        status = self.check("\tlibsomething_unusual.so.9 => not found\n", returncode=1)
        self.assertFalse(status.ok)
        self.assertEqual(status.missing, ["libsomething_unusual.so.9"])
        self.assertEqual(status.packages, [])
        self.assertIn("libsomething_unusual.so.9", status.message)

    def test_an_unusable_check_does_not_become_a_failed_prerequisite(self):
        """Not being able to ask is not the same as a missing library."""
        status = self.check("", returncode=1, stderr="not a dynamic executable")
        self.assertTrue(status.ok)
        self.assertFalse(status.checked)

    def test_a_missing_ldd_does_not_block_a_launch(self):
        def absent(command):
            raise FileNotFoundError(command[0])

        status = managed_setup.check_runtime_libraries(Path(__file__), runner=absent)
        self.assertTrue(status.ok)
        self.assertFalse(status.checked)
        self.assertIn("ldd is not available", status.message)

    def test_a_missing_executable_is_reported_as_such(self):
        status = managed_setup.check_runtime_libraries(
            Path(tempfile.gettempdir()) / "omobench-no-such-runtime-binary")
        self.assertFalse(status.ok)
        self.assertIn("missing", status.message)


class RealLoaderTests(unittest.TestCase):
    """The real ldd, a real ELF, and a library that is genuinely absent."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("ldd") is None:
            raise unittest.SkipTest("ldd is not available on this system")
        if shutil.which("cc") is None:
            raise unittest.SkipTest("no C compiler is available to build the fixture")
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)

        # A shared library of our own, so nothing system-wide is involved.
        (root / "fixture.c").write_text("int omobench_fixture(void) { return 0; }\n")
        (root / "main.c").write_text(textwrap.dedent("""
            int omobench_fixture(void);
            int main(void) { return omobench_fixture(); }
        """))
        library = root / "libomobenchfixture.so.1"
        build = subprocess.run(
            ["cc", "-shared", "-fPIC", "-Wl,-soname,libomobenchfixture.so.1",
             "-o", str(library), str(root / "fixture.c")],
            capture_output=True, text=True)
        if build.returncode != 0:
            raise unittest.SkipTest("could not build the fixture library: " + build.stderr[-300:])
        cls.executable = root / "fixture-runtime"
        # Linked against the file itself rather than through -l, which would
        # want a libomobenchfixture.so symlink this fixture has no reason to
        # create. The soname above is what the loader will look for at run
        # time, and rpath is where it will look.
        link = subprocess.run(
            ["cc", "-o", str(cls.executable), str(root / "main.c"),
             str(library), "-Wl,-rpath," + str(root)],
            capture_output=True, text=True)
        if link.returncode != 0:
            raise unittest.SkipTest("could not link the fixture executable: " + link.stderr[-300:])
        cls.library = library

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def test_while_the_library_is_present_the_check_passes(self):
        self.assertTrue(self.library.is_file())
        status = managed_setup.check_runtime_libraries(self.executable)
        self.assertTrue(status.ok, status.message)
        self.assertTrue(status.checked)
        self.assertEqual(status.missing, [])

    def test_removing_the_library_is_caught_by_name_before_anything_runs(self):
        """The shape of the libgomp failure, reproduced rather than mocked."""
        moved = self.library.with_suffix(".hidden")
        self.library.rename(moved)
        try:
            status = managed_setup.check_runtime_libraries(self.executable)
            self.assertFalse(status.ok, status.message)
            self.assertTrue(status.checked)
            self.assertEqual(status.missing, ["libomobenchfixture.so.1"])
            self.assertIn("libomobenchfixture.so.1", status.message)
            self.assertIn("does not install system packages", status.detail)
        finally:
            moved.rename(self.library)

    def test_the_binary_really_would_have_failed_to_start(self):
        """Confirms the check is answering the question the launcher asks.

        Without this, the test above would only prove that ldd and the check
        agree -- not that a binary ldd calls broken actually refuses to run.
        """
        moved = self.library.with_suffix(".hidden")
        self.library.rename(moved)
        try:
            attempt = subprocess.run([str(self.executable)], capture_output=True, text=True)
            self.assertNotEqual(attempt.returncode, 0)
            self.assertIn("error while loading shared libraries", attempt.stderr)
        finally:
            moved.rename(self.library)


class LoaderLogTests(unittest.TestCase):
    """What the launcher says when a server dies before opening a socket."""

    def write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8")
        handle.write(text)
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return Path(handle.name)

    def test_the_loader_error_is_quoted_with_its_package(self):
        log = self.write(
            "llama-server: error while loading shared libraries: libgomp.so.1: "
            "cannot open shared object file: No such file or directory\n")
        detail = launcher.loader_failure(log)
        self.assertIn("error while loading shared libraries", detail)
        self.assertIn("sudo apt update && sudo apt install -y libgomp1", detail)
        self.assertIn("does not install system packages", detail)

    def test_an_ordinary_crash_adds_nothing(self):
        log = self.write("ggml: out of memory while allocating the KV cache\n")
        self.assertEqual(launcher.loader_failure(log), "")

    def test_a_missing_or_unreadable_log_adds_nothing(self):
        self.assertEqual(launcher.loader_failure(None), "")
        self.assertEqual(
            launcher.loader_failure(Path(tempfile.gettempdir()) / "omobench-no-such-log"), "")


class LauncherWiringTests(unittest.TestCase):
    """The check must sit in front of the launch, not beside it.

    That the launcher actually refuses is exercised against the real fixture
    server in test_launcher.py; this guards the ordering, which a refactor
    could silently invert while every other test still passed.
    """

    def test_the_check_is_wired_into_the_launch_path(self):
        """A source-level assertion, so the wiring cannot quietly be removed."""
        source = Path(launcher.__file__).read_text(encoding="utf-8")
        check_at = source.index("check_runtime_libraries")
        popen_at = source.index("subprocess.Popen(command")
        self.assertLess(check_at, popen_at,
                        "the library check must run before the server is started")


if __name__ == "__main__":
    unittest.main()
