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

`ldd` is not an inert reader: its own manual page warns that it may run the
program being inspected. The probe is therefore bounded to a binary whose
bytes are re-hashed immediately beforehand, and refuses to look at anything
else. These tests cover that boundary as well as the library check itself.

The central test does not simulate the failure. It compiles a real shared
library, links a real executable against it, deletes the library, and asks the
real `ldd` -- so what is asserted is the loader's own answer, on a genuine ELF.
Nothing is installed and no system package is touched.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from omobench import launcher, managed_setup


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Result:
    """What a runner hands back, shaped like subprocess.CompletedProcess."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class ProbeBoundaryTests(unittest.TestCase):
    """What the probe will and will not look at."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.binary = Path(self.temporary.name) / "llama-server"
        self.binary.write_bytes(b"not really an ELF, and never inspected")
        self.addCleanup(self.temporary.cleanup)

    def test_without_a_verified_checksum_nothing_is_probed(self):
        """An arbitrary path cannot reach ldd through this function."""
        probed = []
        managed_setup.check_runtime_libraries(
            self.binary, runner=lambda path: probed.append(path) or Result())
        self.assertEqual(probed, [], "ldd was invoked without an established checksum")

        status = managed_setup.check_runtime_libraries(self.binary)
        self.assertFalse(status.checked)
        self.assertTrue(status.ok)
        self.assertFalse(status.integrity_verified)
        self.assertIn("not checked", status.message)

    def test_a_replaced_binary_is_refused_before_it_is_inspected(self):
        """A download-time checksum is not enough; this re-hashes at probe time."""
        recorded = digest(self.binary)
        self.binary.write_bytes(b"swapped after the installer verified it")
        probed = []
        status = managed_setup.check_runtime_libraries(
            self.binary, expected_sha256=recorded,
            runner=lambda path: probed.append(path) or Result())
        self.assertEqual(probed, [], "a replaced binary was handed to ldd")
        self.assertFalse(status.ok)
        self.assertTrue(status.checked)
        self.assertFalse(status.integrity_verified)
        self.assertIn("not the one that was installed", status.message)
        self.assertEqual(status.missing, [], "a replaced binary is not a missing library")

    def test_a_matching_checksum_lets_the_probe_run(self):
        probed = []
        status = managed_setup.check_runtime_libraries(
            self.binary, expected_sha256=digest(self.binary),
            runner=lambda path: probed.append(path) or Result(
                stdout="\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x1)\n"))
        self.assertEqual(probed, [str(self.binary)])
        self.assertTrue(status.ok)
        self.assertTrue(status.checked)
        self.assertTrue(status.integrity_verified)

    def test_a_missing_executable_is_reported_as_such(self):
        status = managed_setup.check_runtime_libraries(
            Path(self.temporary.name) / "absent", expected_sha256="0" * 64)
        self.assertFalse(status.ok)
        self.assertIn("missing", status.message)


class ParsingTests(unittest.TestCase):
    """What the check makes of ldd output, without running ldd."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.binary = Path(self.temporary.name) / "llama-server"
        self.binary.write_bytes(b"fixture")
        self.addCleanup(self.temporary.cleanup)

    def check(self, stdout, *, returncode=0, stderr=""):
        return managed_setup.check_runtime_libraries(
            self.binary, expected_sha256=digest(self.binary),
            runner=lambda path: Result(stdout, stderr, returncode))

    def test_a_resolvable_binary_passes(self):
        status = self.check(
            "\tlinux-vdso.so.1 (0x00007ffc)\n"
            "\tlibgomp.so.1 => /lib/x86_64-linux-gnu/libgomp.so.1 (0x00007f00)\n"
            "\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f01)\n")
        self.assertTrue(status.ok)
        self.assertTrue(status.checked)
        self.assertEqual(status.missing, [])

    def test_the_reported_failure_is_named(self):
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

    def test_the_advice_promises_no_automatic_install_and_no_redownload(self):
        """Both wordings say the same two things, so assert the substance.

        On a Debian-derived system the advice is "OmoBench does not run it for
        you"; elsewhere it is "OmoBench does not install system packages for
        you". Either must be present, and so must the promise that the
        download is not repeated.
        """
        status = self.check("\tlibgomp.so.1 => not found\n", returncode=1)
        detail = status.detail.lower()
        self.assertTrue(
            "does not install system packages" in detail or "does not run it for you" in detail,
            status.detail)
        self.assertIn("nothing is downloaded again", detail)

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

    def test_malformed_output_is_not_mistaken_for_a_library_name(self):
        for noise in (
            "=> not found\n",                       # no name
            "libgomp.so.1 => not found extra\n",    # trailing junk
            "libgomp.so.1 =>not found\n\x00\x01",   # no space, binary bytes
            "a b c => not found d\n",
        ):
            with self.subTest(noise=noise[:24]):
                status = self.check(noise, returncode=1)
                self.assertNotIn("extra", "".join(status.missing))
                self.assertNotIn("d", status.missing)

    def test_a_flood_of_output_is_capped(self):
        flood = "".join(f"\tlib{index}.so.1 => not found\n" for index in range(500))
        status = self.check(flood, returncode=1)
        self.assertLessEqual(len(status.missing), managed_setup.LDD_MAX_REPORTED_LIBRARIES)
        self.assertLessEqual(len(status.detail), managed_setup.LDD_MAX_OUTPUT_CHARS)

    def test_a_missing_ldd_does_not_block_a_launch(self):
        def absent(path):
            raise FileNotFoundError("ldd")

        status = managed_setup.check_runtime_libraries(
            self.binary, expected_sha256=digest(self.binary), runner=absent)
        self.assertTrue(status.ok)
        self.assertFalse(status.checked)
        self.assertIn("ldd is not available", status.message)

    def test_a_timeout_is_reported_as_unchecked(self):
        def hang(path):
            raise subprocess.TimeoutExpired(cmd="ldd", timeout=20)

        status = managed_setup.check_runtime_libraries(
            self.binary, expected_sha256=digest(self.binary), runner=hang)
        self.assertTrue(status.ok)
        self.assertFalse(status.checked)
        self.assertIn("timed out", status.message)
        self.assertEqual(status.missing, [])


class DistributionGuidanceTests(unittest.TestCase):
    """apt belongs on Debian-derived systems and nowhere else."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.binary = Path(self.temporary.name) / "llama-server"
        self.binary.write_bytes(b"fixture")
        self.addCleanup(self.temporary.cleanup)

    def check(self):
        return managed_setup.check_runtime_libraries(
            self.binary, expected_sha256=digest(self.binary),
            runner=lambda path: Result("\tlibgomp.so.1 => not found\n", returncode=1))

    def test_debian_and_ubuntu_get_the_exact_command(self):
        original = managed_setup.debian_family
        managed_setup.debian_family = lambda: True
        try:
            status = self.check()
        finally:
            managed_setup.debian_family = original
        self.assertIn("sudo apt update && sudo apt install -y libgomp1", status.detail)

    def test_other_distributions_are_not_told_to_run_apt(self):
        original = managed_setup.debian_family
        managed_setup.debian_family = lambda: False
        try:
            status = self.check()
        finally:
            managed_setup.debian_family = original
        self.assertNotIn("sudo apt", status.detail)
        # The library and the Debian package name are still useful context.
        self.assertIn("libgomp.so.1", status.message)
        self.assertIn("libgomp1", status.detail)
        self.assertIn("package name differs", status.detail)

    def test_the_family_is_read_from_os_release_not_assumed(self):
        """Exercised against the real file on this machine."""
        verdict = managed_setup.debian_family()
        self.assertIsInstance(verdict, bool)
        release = Path("/etc/os-release")
        if release.is_file():
            text = release.read_text(encoding="utf-8", errors="replace").lower()
            expected = "id=ubuntu" in text or "id=debian" in text or "debian" in text
            self.assertEqual(verdict, expected)


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
        cls.checksum = digest(cls.executable)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def test_while_the_library_is_present_the_check_passes(self):
        self.assertTrue(self.library.is_file())
        status = managed_setup.check_runtime_libraries(
            self.executable, expected_sha256=self.checksum)
        self.assertTrue(status.ok, status.message)
        self.assertTrue(status.checked)
        self.assertTrue(status.integrity_verified)
        self.assertEqual(status.missing, [])

    def test_removing_the_library_is_caught_by_name_before_anything_runs(self):
        """The shape of the libgomp failure, reproduced rather than mocked."""
        moved = self.library.with_suffix(".hidden")
        self.library.rename(moved)
        try:
            status = managed_setup.check_runtime_libraries(
                self.executable, expected_sha256=self.checksum)
            self.assertFalse(status.ok, status.message)
            self.assertTrue(status.checked)
            self.assertEqual(status.missing, ["libomobenchfixture.so.1"])
            self.assertIn("libomobenchfixture.so.1", status.message)
            self.assertIn("does not install system packages", status.detail)
        finally:
            moved.rename(self.library)

    def test_the_binary_really_would_have_failed_to_start(self):
        """Confirms the check is answering the question the launcher asks."""
        moved = self.library.with_suffix(".hidden")
        self.library.rename(moved)
        try:
            attempt = subprocess.run([str(self.executable)], capture_output=True, text=True)
            self.assertNotEqual(attempt.returncode, 0)
            self.assertIn("error while loading shared libraries", attempt.stderr)
        finally:
            moved.rename(self.library)

    def test_a_tampered_runtime_is_refused_without_being_inspected(self):
        """End to end: the file changes, the recorded checksum does not."""
        original = self.executable.read_bytes()
        try:
            self.executable.write_bytes(original + b"\x00tampered")
            status = managed_setup.check_runtime_libraries(
                self.executable, expected_sha256=self.checksum)
            self.assertFalse(status.ok)
            self.assertFalse(status.integrity_verified)
            self.assertIn("not the one that was installed", status.message)
        finally:
            self.executable.write_bytes(original)
            self.executable.chmod(0o755)


class ProcessCleanupTests(unittest.TestCase):
    """A probe that hangs must not leave anything behind."""

    def test_a_hanging_probe_is_killed_with_its_whole_process_group(self):
        if os.name == "nt":
            self.skipTest("the managed runtime path is Linux only")
        if shutil.which("bash") is None:
            self.skipTest("bash is unavailable")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        marker = Path(temporary.name) / "grandchild.pid"
        # A stand-in for ldd that spawns a child of its own, the way ldd
        # invokes the loader, and then hangs. Both must be gone afterwards.
        fake = Path(temporary.name) / "slow-ldd"
        fake.write_text(
            "#!/usr/bin/env bash\n"
            f"( echo $$ > {marker}; sleep 60 ) &\n"
            "sleep 60\n")
        fake.chmod(0o755)

        import signal
        import subprocess as sp

        process = sp.Popen([str(fake)], stdout=sp.PIPE, stderr=sp.PIPE, start_new_session=True)
        group = os.getpgid(process.pid)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not marker.is_file():
            time.sleep(0.05)
        self.assertTrue(marker.is_file(), "the fixture never spawned its child")
        grandchild = int(marker.read_text().strip())

        # The cleanup run_ldd performs on timeout.
        os.killpg(group, signal.SIGKILL)
        with __import__("contextlib").suppress(sp.TimeoutExpired):
            process.communicate(timeout=5)

        gone_deadline = time.monotonic() + 10
        while time.monotonic() < gone_deadline:
            try:
                os.kill(grandchild, 0)
            except OSError:
                break
            time.sleep(0.05)
        with self.assertRaises(OSError):
            os.kill(grandchild, 0)

    def test_run_ldd_uses_an_argument_vector_and_no_shell(self):
        """A path with shell metacharacters is a path, not a command."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        hostile = Path(temporary.name) / "a b; touch pwned; echo .bin"
        hostile.write_bytes(b"fixture")
        sentinel = Path(temporary.name) / "pwned"
        status = managed_setup.check_runtime_libraries(
            hostile, expected_sha256=digest(hostile))
        self.assertFalse(sentinel.exists(), "the path was interpreted by a shell")
        # ldd cannot read it, so the probe is inconclusive -- never "verified".
        self.assertFalse(status.checked)
        self.assertTrue(status.ok)


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
        self.assertIn("libgomp1", detail)
        self.assertIn("does not install system packages", detail)

    def test_an_ordinary_crash_adds_nothing(self):
        log = self.write("ggml: out of memory while allocating the KV cache\n")
        self.assertEqual(launcher.loader_failure(log), "")

    def test_a_missing_or_unreadable_log_adds_nothing(self):
        self.assertEqual(launcher.loader_failure(None), "")
        self.assertEqual(
            launcher.loader_failure(Path(tempfile.gettempdir()) / "omobench-no-such-log"), "")


class LauncherWiringTests(unittest.TestCase):
    """The check must sit in front of the launch, not beside it."""

    def test_the_check_is_wired_into_the_launch_path(self):
        source = Path(launcher.__file__).read_text(encoding="utf-8")
        check_at = source.index("check_runtime_libraries")
        popen_at = source.index("subprocess.Popen(command")
        self.assertLess(check_at, popen_at,
                        "the library check must run before the server is started")

    def test_the_launcher_passes_the_checksum_it_just_verified(self):
        source = Path(launcher.__file__).read_text(encoding="utf-8")
        verify_at = source.index("verify_installed(")
        check_at = source.index("check_runtime_libraries")
        self.assertLess(verify_at, check_at, "integrity must be established first")
        self.assertIn('expected_sha256=installed["executable_sha256"]', source)


if __name__ == "__main__":
    unittest.main()
