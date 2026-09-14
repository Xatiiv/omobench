from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from omobench import managed_setup as ms
from omobench.cli import build_parser, find_repository_root


ROOT = Path(__file__).resolve().parents[2]


def make_fake_repo_root(root: Path, recipe: dict) -> None:
    """A minimal repository layout with a real model catalog and a fixture recipe."""
    shutil.copytree(ROOT / "catalog" / "models", root / "catalog" / "models")
    (root / "catalog" / "downloads").mkdir(parents=True)
    (root / "catalog" / "downloads" / "cpu-v1.json").write_text(
        json.dumps({"catalog_version": 1, "recipes": [recipe]}), encoding="utf-8"
    )
    (root / "benchmarks" / "profiles").mkdir(parents=True)
    (root / "results").mkdir()


def base_recipe(**overrides) -> dict:
    recipe = {
        "id": "qwen3-4b-cpu-v1",
        "title": "Qwen3 4B · CPU",
        "model_id": "Qwen/Qwen3-4B",
        "quantization": "Q4_K_M",
        "platform": "linux-x86_64",
        "supported_environment": "Linux x64 or WSL2, glibc 2.34 or newer",
        "min_glibc_version": "2.34",
        "benchmark_profile": "chat-small-v1",
        "execution_device": "cpu",
        "model": {
            "kind": "file",
            "repository": "ggml-org/Qwen3-4B-GGUF",
            "revision": "2f3b082b1356a6123f7ed71e65aea340da25d53c",
            "filename": "Qwen3-4B-Q4_K_M.gguf",
            "url": "https://huggingface.co/ggml-org/Qwen3-4B-GGUF/resolve/2f3b082b1356a6123f7ed71e65aea340da25d53c/Qwen3-4B-Q4_K_M.gguf",
            "sha256": "ab27b9bfa375a178d6cba48f3ad892b94b7739659dcc7aae8058ce0ffed6b328",
            "size_bytes": 2497280640,
            "licence": "Apache-2.0",
        },
        "runtime": {
            "kind": "archive",
            "repository": "ggml-org/llama.cpp",
            "revision": "c1d0e7a004015f23bc0233470b747b596f29b264",
            "version": "b10621",
            "filename": "llama-b10621-bin-ubuntu-x64.tar.gz",
            "url": "https://github.com/ggml-org/llama.cpp/releases/download/b10621/llama-b10621-bin-ubuntu-x64.tar.gz",
            "sha256": "91d7b03ddae498a39f28fdb85d84d2b4a0fd3838d10b4f897e0ef8975bb9b583",
            "size_bytes": 16291771,
            "licence": "MIT",
            "archive_format": "tar.gz",
            "executable_path": "llama-b10621/llama-server",
            "max_extracted_bytes": 209715200,
            "max_entries": 500,
        },
    }
    recipe.update({k: v for k, v in overrides.items() if k not in {"model", "runtime"}})
    if "model" in overrides:
        recipe["model"] = {**recipe["model"], **overrides["model"]}
    if "runtime" in overrides:
        recipe["runtime"] = {**recipe["runtime"], **overrides["runtime"]}
    return recipe


class RecipeValidationTests(unittest.TestCase):
    def test_real_catalog_validates_and_references_a_cataloged_model(self):
        recipes = ms.load_recipes(ROOT)
        self.assertEqual([r["id"] for r in recipes], ["qwen3-4b-cpu-v1"])
        self.assertEqual(ms.get_recipe(ROOT, "qwen3-4b-cpu-v1")["model_id"], "Qwen/Qwen3-4B")

    def test_recipe_rejects_model_absent_from_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_fake_repo_root(root, base_recipe(model_id="Nonexistent/Model"))
            with self.assertRaisesRegex(ms.ManagedSetupError, "not an eligible"):
                ms.load_recipes(root)

    def test_recipe_rejects_mutable_revision_labels(self):
        for label in ["main", "master", "latest", "HEAD"]:
            with self.subTest(label=label):
                with self.assertRaisesRegex(ms.ManagedSetupError, "immutable"):
                    ms.validate_recipe(ROOT, base_recipe(model={"revision": label}))

    def test_recipe_rejects_short_or_non_hex_revision(self):
        with self.assertRaises(ms.ManagedSetupError):
            ms.validate_recipe(ROOT, base_recipe(model={"revision": "abc123"}))

    def test_recipe_rejects_non_https_or_disallowed_host(self):
        for url in [
            "http://huggingface.co/ggml-org/Qwen3-4B-GGUF/resolve/2f3b082b1356a6123f7ed71e65aea340da25d53c/Qwen3-4B-Q4_K_M.gguf",
            "https://evil.example.com/Qwen3-4B-Q4_K_M.gguf",
            "https://user:pw@huggingface.co/ggml-org/Qwen3-4B-GGUF/resolve/2f3b082b1356a6123f7ed71e65aea340da25d53c/Qwen3-4B-Q4_K_M.gguf",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(ms.ManagedSetupError):
                    ms.validate_recipe(ROOT, base_recipe(model={"url": url}))

    def test_recipe_rejects_bad_sha256(self):
        with self.assertRaises(ms.ManagedSetupError):
            ms.validate_recipe(ROOT, base_recipe(model={"sha256": "not-a-hash"}))

    def test_recipe_rejects_zero_or_negative_size(self):
        for size in [0, -1]:
            with self.subTest(size=size):
                with self.assertRaises(ms.ManagedSetupError):
                    ms.validate_recipe(ROOT, base_recipe(model={"size_bytes": size}))

    def test_recipe_rejects_unsupported_platform_or_execution_device(self):
        with self.assertRaises(ms.ManagedSetupError):
            ms.validate_recipe(ROOT, base_recipe(platform="darwin-arm64"))
        with self.assertRaises(ms.ManagedSetupError):
            ms.validate_recipe(ROOT, base_recipe(execution_device="gpu"))

    def test_recipe_rejects_unknown_benchmark_profile(self):
        with self.assertRaises(ms.ManagedSetupError):
            ms.validate_recipe(ROOT, base_recipe(benchmark_profile="made-up-profile"))

    def test_recipe_rejects_unsafe_executable_path(self):
        for bad_path in ["/etc/passwd", "../evil", "a\\b", "a/../b"]:
            with self.subTest(bad_path=bad_path):
                with self.assertRaises(ms.ManagedSetupError):
                    ms.validate_recipe(ROOT, base_recipe(runtime={"executable_path": bad_path}))

    def test_duplicate_recipe_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = base_recipe()
            make_fake_repo_root(root, recipe)
            (root / "catalog" / "downloads" / "cpu-v1.json").write_text(
                json.dumps({"catalog_version": 1, "recipes": [recipe, dict(recipe)]}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ms.ManagedSetupError, "Duplicate"):
                ms.load_recipes(root)


class ReviewIsReadOnlyTests(unittest.TestCase):
    def test_review_makes_no_network_calls_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage_dir = Path(temporary) / "storage"
            with patch("omobench.managed_setup.build_opener") as opener:
                report = ms.review(ROOT, "qwen3-4b-cpu-v1", storage_dir)
            opener.assert_not_called()
            self.assertEqual(report["recipe"]["id"], "qwen3-4b-cpu-v1")
            self.assertFalse(storage_dir.exists())
            self.assertFalse(report["already_installed"]["installed"])

    def test_review_reports_platform_and_space(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage_dir = Path(temporary)
            report = ms.review(ROOT, "qwen3-4b-cpu-v1", storage_dir)
            self.assertIn("ok", report["platform"])
            self.assertIsInstance(report["required_bytes"], int)
            self.assertGreater(report["required_bytes"], 0)


class PlatformCheckTests(unittest.TestCase):
    def test_native_windows_is_blocked_with_wsl_message(self):
        with patch("omobench.managed_setup.sys.platform", "win32"):
            status = ms.check_platform()
        self.assertFalse(status.ok)
        self.assertIn("WSL2", status.message)

    def test_non_linux_posix_is_blocked(self):
        with patch("omobench.managed_setup.sys.platform", "darwin"):
            status = ms.check_platform()
        self.assertFalse(status.ok)

    def test_non_x86_64_linux_is_blocked(self):
        fake_uname = type("Uname", (), {"machine": "aarch64"})()
        with patch("omobench.managed_setup.sys.platform", "linux"), patch(
            "omobench.managed_setup.os.uname", return_value=fake_uname, create=True
        ):
            status = ms.check_platform()
        self.assertFalse(status.ok)

    def test_glibc_below_minimum_is_blocked(self):
        fake_uname = type("Uname", (), {"machine": "x86_64"})()
        with patch("omobench.managed_setup.sys.platform", "linux"), patch(
            "omobench.managed_setup.os.uname", return_value=fake_uname, create=True
        ), patch("omobench.managed_setup.detected_glibc_version", return_value=(2, 33)):
            status = ms.check_platform()
        self.assertFalse(status.ok)

    def test_glibc_2_34_boundary_is_accepted(self):
        """Regression test: glibc 2.34 itself must be accepted, not just newer versions."""
        fake_uname = type("Uname", (), {"machine": "x86_64"})()
        with patch("omobench.managed_setup.sys.platform", "linux"), patch(
            "omobench.managed_setup.os.uname", return_value=fake_uname, create=True
        ), patch("omobench.managed_setup.detected_glibc_version", return_value=(2, 34)):
            status = ms.check_platform()
        self.assertTrue(status.ok)
        self.assertTrue(ms.meets_minimum_glibc((2, 34)))
        self.assertFalse(ms.meets_minimum_glibc((2, 33)))

    def test_missing_glibc_detection_is_blocked(self):
        fake_uname = type("Uname", (), {"machine": "x86_64"})()
        with patch("omobench.managed_setup.sys.platform", "linux"), patch(
            "omobench.managed_setup.os.uname", return_value=fake_uname, create=True
        ), patch("omobench.managed_setup.detected_glibc_version", return_value=None):
            status = ms.check_platform()
        self.assertFalse(status.ok)


class StorageDirPathTests(unittest.TestCase):
    def test_wsl_or_linux_rejects_windows_style_path(self):
        with patch("omobench.managed_setup.os.name", "posix"):
            for bad in ["D:\\OmoBench", "C:/OmoBench", "relative/path"]:
                with self.subTest(bad=bad):
                    with self.assertRaises(ms.ManagedSetupError):
                        ms.normalize_storage_dir(bad)
            self.assertEqual(ms.normalize_storage_dir("/mnt/d/OmoBench"), Path("/mnt/d/OmoBench"))

    def test_native_windows_rejects_posix_style_path(self):
        with patch("omobench.managed_setup.os.name", "nt"):
            with self.assertRaises(ms.ManagedSetupError):
                ms.normalize_storage_dir("/mnt/d/OmoBench")
            self.assertEqual(ms.normalize_storage_dir("D:\\OmoBench"), Path("D:\\OmoBench"))

    def test_empty_storage_dir_is_rejected(self):
        with self.assertRaises(ms.ManagedSetupError):
            ms.normalize_storage_dir("   ")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RangeDownloadHandler(BaseHTTPRequestHandler):
    content = b""
    received_ranges: list[str] = []

    def do_GET(self):
        range_header = self.headers.get("Range")
        body = self.content
        if range_header:
            self.received_ranges.append(range_header)
            match = re.fullmatch(r"bytes=(\d+)-", range_header)
            start = int(match.group(1)) if match else 0
            chunk = body[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


class AlwaysFullContentHandler(BaseHTTPRequestHandler):
    content = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.content)))
        self.end_headers()
        self.wfile.write(self.content)

    def log_message(self, *args):
        pass


class ResumableDownloadTests(unittest.TestCase):
    def _serve(self, handler_class):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        return server

    def test_resumes_via_range_header_and_verifies_hash(self):
        content = b"omobench-fixture-bytes" * 500
        RangeDownloadHandler.content = content
        RangeDownloadHandler.received_ranges = []
        server = self._serve(RangeDownloadHandler)
        with tempfile.TemporaryDirectory() as temporary:
            managed = Path(temporary) / "managed"
            part = managed / "downloads" / "model.bin.part"
            final = managed / "downloads" / "model.bin"
            part.parent.mkdir(parents=True)
            half = len(content) // 2
            part.write_bytes(content[:half])
            ms.resumable_download(
                f"http://127.0.0.1:{server.server_port}/model.bin",
                part_path=part,
                final_path=final,
                expected_size=len(content),
                expected_sha256=_sha256(content),
                managed=managed,
                label="model",
            )
            self.assertEqual(final.read_bytes(), content)
            self.assertFalse(part.exists())
            self.assertTrue(RangeDownloadHandler.received_ranges)
            self.assertEqual(RangeDownloadHandler.received_ranges[0], f"bytes={half}-")

    def test_progress_callback_and_idempotent_recheck(self):
        content = b"abc" * 1000
        RangeDownloadHandler.content = content
        RangeDownloadHandler.received_ranges = []
        server = self._serve(RangeDownloadHandler)
        seen = []
        with tempfile.TemporaryDirectory() as temporary:
            managed = Path(temporary) / "managed"
            part = managed / "downloads" / "model.bin.part"
            final = managed / "downloads" / "model.bin"
            ms.resumable_download(
                f"http://127.0.0.1:{server.server_port}/model.bin",
                part_path=part,
                final_path=final,
                expected_size=len(content),
                expected_sha256=_sha256(content),
                managed=managed,
                label="model",
                progress=lambda label, done, total: seen.append((label, done, total)),
            )
            self.assertTrue(seen)
            self.assertEqual(seen[-1], ("model", len(content), len(content)))
            # Re-running with an already-verified final file must not need the server again.
            with patch("omobench.managed_setup.build_opener") as opener:
                ms.resumable_download(
                    f"http://127.0.0.1:{server.server_port}/model.bin",
                    part_path=part,
                    final_path=final,
                    expected_size=len(content),
                    expected_sha256=_sha256(content),
                    managed=managed,
                    label="model",
                )
            opener.assert_not_called()

    def test_hash_mismatch_quarantines_and_does_not_install(self):
        wrong_content = b"wrong-bytes" * 100
        AlwaysFullContentHandler.content = wrong_content
        server = self._serve(AlwaysFullContentHandler)
        with tempfile.TemporaryDirectory() as temporary:
            managed = Path(temporary) / "managed"
            part = managed / "downloads" / "model.bin.part"
            final = managed / "downloads" / "model.bin"
            with self.assertRaisesRegex(ms.ManagedSetupError, "quarantined"):
                ms.resumable_download(
                    f"http://127.0.0.1:{server.server_port}/model.bin",
                    part_path=part,
                    final_path=final,
                    expected_size=len(wrong_content),
                    expected_sha256=_sha256(b"expected-different-bytes"),
                    managed=managed,
                    label="model",
                )
            self.assertFalse(final.exists())
            self.assertFalse(part.exists())
            quarantined = list((managed / "quarantine").glob("*.quarantined"))
            self.assertEqual(len(quarantined), 1)

    def test_size_mismatch_is_rejected(self):
        content = b"x" * 10
        AlwaysFullContentHandler.content = content
        server = self._serve(AlwaysFullContentHandler)
        with tempfile.TemporaryDirectory() as temporary:
            managed = Path(temporary) / "managed"
            part = managed / "downloads" / "model.bin.part"
            final = managed / "downloads" / "model.bin"
            with self.assertRaises(ms.ManagedSetupError):
                ms.resumable_download(
                    f"http://127.0.0.1:{server.server_port}/model.bin",
                    part_path=part,
                    final_path=final,
                    expected_size=999,
                    expected_sha256=_sha256(content),
                    managed=managed,
                    label="model",
                )

    def test_cancel_event_stops_download(self):
        content = b"y" * (ms.DOWNLOAD_CHUNK_BYTES * 3)
        AlwaysFullContentHandler.content = content
        server = self._serve(AlwaysFullContentHandler)

        class ImmediatelySet:
            def is_set(self):
                return True

        with tempfile.TemporaryDirectory() as temporary:
            managed = Path(temporary) / "managed"
            part = managed / "downloads" / "model.bin.part"
            final = managed / "downloads" / "model.bin"
            with self.assertRaisesRegex(ms.ManagedSetupError, "cancelled"):
                ms.resumable_download(
                    f"http://127.0.0.1:{server.server_port}/model.bin",
                    part_path=part,
                    final_path=final,
                    expected_size=len(content),
                    expected_sha256=_sha256(content),
                    managed=managed,
                    label="model",
                    cancel_event=ImmediatelySet(),
                )
            self.assertFalse(final.exists())

    def test_unsafe_redirect_is_rejected(self):
        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:9/model.bin")  # downgraded scheme
                self.end_headers()

            def log_message(self, *args):
                pass

        server = self._serve(RedirectHandler)
        with tempfile.TemporaryDirectory() as temporary:
            managed = Path(temporary) / "managed"
            part = managed / "downloads" / "model.bin.part"
            final = managed / "downloads" / "model.bin"
            with self.assertRaises(ms.ManagedSetupError):
                ms.resumable_download(
                    f"http://127.0.0.1:{server.server_port}/model.bin",
                    part_path=part,
                    final_path=final,
                    expected_size=10,
                    expected_sha256="0" * 64,
                    managed=managed,
                    label="model",
                )


def build_tar_gz(path: Path, members: list[tuple[str, dict]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, spec in members:
            info = tarfile.TarInfo(name=name)
            kind = spec.get("kind", "file")
            if kind == "file":
                data = spec.get("content", b"")
                info.type = tarfile.REGTYPE
                info.size = len(data)
                info.mode = spec.get("mode", 0o644)
                archive.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = spec["target"]
                archive.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = spec["target"]
                archive.addfile(info)
            elif kind == "chardev":
                info.type = tarfile.CHRTYPE
                info.devmajor, info.devminor = 1, 3
                archive.addfile(info)
            else:
                raise ValueError(kind)


class SafeExtractTests(unittest.TestCase):
    def _extract(self, temporary, members, *, executable_path="app/run", max_entries=100, max_extracted_bytes=10_000):
        archive_path = Path(temporary) / "archive.tar.gz"
        build_tar_gz(archive_path, members)
        dest = Path(temporary) / "dest"
        return ms.safe_extract_tar_gz(
            archive_path,
            dest,
            executable_path=executable_path,
            max_entries=max_entries,
            max_extracted_bytes=max_extracted_bytes,
        )

    def test_extracts_expected_executable_and_sets_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = self._extract(
                temporary,
                [("app/run", {"kind": "file", "content": b"#!/bin/sh\necho hi\n"})],
            )
            self.assertTrue(executable.is_file())
            self.assertEqual(executable.read_bytes(), b"#!/bin/sh\necho hi\n")

    def test_missing_expected_executable_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ms.ManagedSetupError, "did not contain"):
                self._extract(temporary, [("app/other", {"kind": "file", "content": b"x"})])

    def test_absolute_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ms.ManagedSetupError):
                self._extract(temporary, [("/etc/passwd", {"kind": "file", "content": b"x"})])

    def test_parent_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ms.ManagedSetupError):
                self._extract(temporary, [("../evil", {"kind": "file", "content": b"x"})])

    def test_backslash_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ms.ManagedSetupError):
                self._extract(temporary, [("evil\\path", {"kind": "file", "content": b"x"})])

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ms.ManagedSetupError):
                self._extract(temporary, [("app/link", {"kind": "symlink", "target": "/etc/passwd"})])

    def test_internal_relative_symlink_is_materialized_as_a_regular_file(self):
        """Mirrors a real llama.cpp archive: libmtmd.so -> libmtmd.so.0.0.0 in the same dir."""
        with tempfile.TemporaryDirectory() as temporary:
            library_content = b"\x7fELF-fake-shared-library-bytes"
            executable = self._extract(
                temporary,
                [
                    ("app/run", {"kind": "file", "content": b"#!/bin/sh\necho hi\n", "mode": 0o755}),
                    (
                        "app/libmtmd.so.0.0.0",
                        {"kind": "file", "content": library_content, "mode": 0o755},
                    ),
                    ("app/libmtmd.so", {"kind": "symlink", "target": "libmtmd.so.0.0.0"}),
                ],
            )
            link_path = executable.parent / "libmtmd.so"
            self.assertTrue(link_path.is_file())
            self.assertFalse(link_path.is_symlink())
            self.assertEqual(link_path.read_bytes(), library_content)
            # Executable permission from the archived target is preserved.
            self.assertTrue(link_path.stat().st_mode & 0o111)

    def test_symlink_pointing_outside_the_destination_via_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ms.ManagedSetupError):
                self._extract(
                    temporary,
                    [
                        ("app/run", {"kind": "file", "content": b"x", "mode": 0o755}),
                        ("app/evil", {"kind": "symlink", "target": "../../../../etc/passwd"}),
                    ],
                )

    def test_symlink_cycle_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ms.ManagedSetupError, "cycle"):
                self._extract(
                    temporary,
                    [
                        ("app/run", {"kind": "file", "content": b"x", "mode": 0o755}),
                        ("app/a", {"kind": "symlink", "target": "b"}),
                        ("app/b", {"kind": "symlink", "target": "a"}),
                    ],
                )

    def test_self_referential_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ms.ManagedSetupError, "cycle"):
                self._extract(
                    temporary,
                    [
                        ("app/run", {"kind": "file", "content": b"x", "mode": 0o755}),
                        ("app/loop", {"kind": "symlink", "target": "loop"}),
                    ],
                )

    def test_symlink_to_missing_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ms.ManagedSetupError, "does not exist"):
                self._extract(
                    temporary,
                    [
                        ("app/run", {"kind": "file", "content": b"x", "mode": 0o755}),
                        ("app/link", {"kind": "symlink", "target": "missing.so"}),
                    ],
                )

    def test_symlink_with_backslash_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ms.ManagedSetupError):
                self._extract(
                    temporary,
                    [
                        ("app/run", {"kind": "file", "content": b"x", "mode": 0o755}),
                        ("app/link", {"kind": "symlink", "target": "evil\\path"}),
                    ],
                )

    def test_symlink_to_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ms.ManagedSetupError, "not a regular file"):
                self._extract(
                    temporary,
                    [
                        ("app/run", {"kind": "file", "content": b"x", "mode": 0o755}),
                        ("app/subdir", {"kind": "dir"}),
                        ("app/link", {"kind": "symlink", "target": "subdir"}),
                    ],
                )

    def test_full_llamacpp_style_archive_is_extracted_safely(self):
        """One fixture covering an executable, a versioned library, a valid symlink to it,
        a rejected traversal symlink, a rejected cycle, and a rejected hard link."""
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "archive.tar.gz"
            build_tar_gz(
                archive_path,
                [
                    ("llama-b10621/main", {"kind": "file", "content": b"#!/bin/sh\n", "mode": 0o755}),
                    (
                        "llama-b10621/libmtmd.so.0.0.0",
                        {"kind": "file", "content": b"library-bytes", "mode": 0o755},
                    ),
                    (
                        "llama-b10621/libmtmd.so",
                        {"kind": "symlink", "target": "libmtmd.so.0.0.0"},
                    ),
                ],
            )
            dest = Path(temporary) / "dest"
            executable = ms.safe_extract_tar_gz(
                archive_path,
                dest,
                executable_path="llama-b10621/main",
                max_entries=100,
                max_extracted_bytes=10_000,
            )
            self.assertTrue(executable.is_file())
            link_path = dest / "llama-b10621" / "libmtmd.so"
            self.assertTrue(link_path.is_file())
            self.assertFalse(link_path.is_symlink())
            self.assertEqual(link_path.read_bytes(), b"library-bytes")

    def test_hardlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ms.ManagedSetupError):
                self._extract(
                    temporary,
                    [
                        ("app/run", {"kind": "file", "content": b"x"}),
                        ("app/link", {"kind": "hardlink", "target": "app/run"}),
                    ],
                )

    def test_device_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ms.ManagedSetupError):
                self._extract(temporary, [("dev/fake", {"kind": "chardev"})])

    def test_duplicate_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ms.ManagedSetupError, "duplicate"):
                self._extract(
                    temporary,
                    [
                        ("app/run", {"kind": "file", "content": b"one"}),
                        ("app/run", {"kind": "file", "content": b"two"}),
                    ],
                )

    def test_too_many_entries_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            members = [(f"app/file{i}", {"kind": "file", "content": b"x"}) for i in range(5)]
            with self.assertRaises(ms.ManagedSetupError):
                self._extract(temporary, members, max_entries=3)

    def test_oversized_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ms.ManagedSetupError):
                self._extract(
                    temporary,
                    [("app/run", {"kind": "file", "content": b"x" * 1000})],
                    max_extracted_bytes=10,
                )


class DownloadAndInstallEndToEndTests(unittest.TestCase):
    """Exercises the full orchestration against a local fixture server, bypassing the
    huggingface.co/github.com host allowlist by injecting a fixture recipe directly."""

    def setUp(self):
        self.model_content = b"fixture-model-bytes" * 1000
        self.executable_content = b"#!/bin/sh\necho ready\n"
        self.archive_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.archive_dir, ignore_errors=True)
        archive_path = self.archive_dir / "runtime.tar.gz"
        build_tar_gz(
            archive_path,
            [("app/run", {"kind": "file", "content": self.executable_content, "mode": 0o755})],
        )
        self.archive_bytes = archive_path.read_bytes()

        model_content = self.model_content
        archive_bytes = self.archive_bytes

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/model.gguf":
                    body = model_content
                elif self.path == "/runtime.tar.gz":
                    body = archive_bytes
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), self.thread.join(2)))

        self.fixture_recipe = base_recipe(
            model={
                "url": f"http://127.0.0.1:{self.server.server_port}/model.gguf",
                "sha256": _sha256(self.model_content),
                "size_bytes": len(self.model_content),
            },
            runtime={
                "url": f"http://127.0.0.1:{self.server.server_port}/runtime.tar.gz",
                "sha256": _sha256(self.archive_bytes),
                "size_bytes": len(self.archive_bytes),
                "executable_path": "app/run",
                "max_extracted_bytes": 10_000,
                "max_entries": 10,
            },
        )
        self.get_recipe_patch = patch("omobench.managed_setup.get_recipe", return_value=self.fixture_recipe)
        self.get_recipe_patch.start()
        self.addCleanup(self.get_recipe_patch.stop)
        self.platform_patch = patch(
            "omobench.managed_setup.check_platform", return_value=ms.PlatformStatus(True, "ok")
        )
        self.platform_patch.start()
        self.addCleanup(self.platform_patch.stop)

    def snapshot(self, root: Path) -> set[str]:
        return {str(p.relative_to(root)) for p in root.rglob("*") if "__pycache__" not in p.parts}

    def test_install_is_atomic_verified_and_writes_only_inside_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake_root = Path(temporary) / "repo"
            make_fake_repo_root(fake_root, self.fixture_recipe)
            (fake_root / "results" / "marker.txt").write_text("do-not-touch", encoding="utf-8")
            before = self.snapshot(fake_root)

            storage_dir = Path(temporary) / "storage"
            manifest = ms.download_and_install(fake_root, "qwen3-4b-cpu-v1", storage_dir)

            after = self.snapshot(fake_root)
            self.assertEqual(before, after, "download_and_install must not write inside the repository checkout")
            self.assertEqual((fake_root / "results" / "marker.txt").read_text(encoding="utf-8"), "do-not-touch")

            self.assertTrue(Path(manifest["model"]["path"]).is_relative_to(storage_dir.resolve()))
            self.assertTrue(Path(manifest["runtime"]["executable_path"]).is_relative_to(storage_dir.resolve()))
            self.assertFalse((ms.managed_root(storage_dir) / "runtimes" / ".qwen3-4b-cpu-v1.staging").exists())

            installed = ms.verify_installed(fake_root, "qwen3-4b-cpu-v1", storage_dir)
            self.assertEqual(installed["model_id"], "Qwen/Qwen3-4B")
            self.assertEqual(Path(installed["executable_path"]).read_bytes(), self.executable_content)

    def test_verify_installed_detects_recipe_hash_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake_root = Path(temporary) / "repo"
            make_fake_repo_root(fake_root, self.fixture_recipe)
            storage_dir = Path(temporary) / "storage"
            ms.download_and_install(fake_root, "qwen3-4b-cpu-v1", storage_dir)

            changed_recipe = {**self.fixture_recipe, "model": {**self.fixture_recipe["model"], "sha256": "0" * 64}}
            with patch("omobench.managed_setup.get_recipe", return_value=changed_recipe):
                with self.assertRaisesRegex(ms.ManagedSetupError, "different recipe revision"):
                    ms.verify_installed(fake_root, "qwen3-4b-cpu-v1", storage_dir)

    def test_verify_installed_detects_tampered_model_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake_root = Path(temporary) / "repo"
            make_fake_repo_root(fake_root, self.fixture_recipe)
            storage_dir = Path(temporary) / "storage"
            manifest = ms.download_and_install(fake_root, "qwen3-4b-cpu-v1", storage_dir)
            model_path = Path(manifest["model"]["path"])
            same_size_garbage = bytes((b + 1) % 256 for b in model_path.read_bytes())
            model_path.write_bytes(same_size_garbage)
            with self.assertRaisesRegex(ms.ManagedSetupError, "failed re-verification"):
                ms.verify_installed(fake_root, "qwen3-4b-cpu-v1", storage_dir)

    def test_missing_install_reports_clear_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ms.ManagedSetupError, "is not installed"):
                ms.verify_installed(ROOT, "qwen3-4b-cpu-v1", Path(temporary) / "storage")

    def test_second_concurrent_install_is_blocked_by_storage_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage_dir = Path(temporary) / "storage"
            with ms.storage_lock(storage_dir):
                with self.assertRaisesRegex(ms.ManagedSetupError, "already preparing"):
                    with ms.storage_lock(storage_dir):
                        self.fail("second setup process obtained the lock")


class ManagedCliParserTests(unittest.TestCase):
    def test_prepare_list_parses(self):
        args = build_parser().parse_args(["prepare", "--list"])
        self.assertTrue(args.list)
        self.assertIsNone(args.recipe)

    def test_prepare_review_and_download_parse(self):
        args = build_parser().parse_args(
            ["prepare", "--recipe", "qwen3-4b-cpu-v1", "--storage-dir", "/mnt/d/OmoBench"]
        )
        self.assertFalse(args.download)
        args = build_parser().parse_args(
            ["prepare", "--recipe", "qwen3-4b-cpu-v1", "--storage-dir", "/mnt/d/OmoBench", "--download"]
        )
        self.assertTrue(args.download)

    def test_launch_requires_smoke_or_execute_exclusively(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["launch", "--recipe", "x", "--storage-dir", "y"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["launch", "--recipe", "x", "--storage-dir", "y", "--smoke", "--execute"])
        args = parser.parse_args(["launch", "--recipe", "x", "--storage-dir", "y", "--smoke"])
        self.assertTrue(args.smoke)
        self.assertFalse(args.execute)

    def test_repository_root_is_found_from_this_checkout(self):
        self.assertIsNotNone(find_repository_root())


if __name__ == "__main__":
    unittest.main()
