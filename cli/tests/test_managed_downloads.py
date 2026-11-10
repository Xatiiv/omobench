"""The real downloader, against a real HTTP server, over small files.

Everything slow and risky about managed setup is the download, and the parts
that can go wrong -- a connection that dies halfway, a cancel, bytes that do
not hash to what the catalog says -- are exactly the parts a 2.5 GB fetch is
the worst way to test. So this drives `managed_setup.resumable_download`
itself, over loopback, against files measured in kilobytes.

**What this does not establish.** It does not verify that a real 2.5 GB model
download from Hugging Face resumes correctly. The code path is the same one;
the network, the CDN, the redirects and the hours are not. Treat a passing run
here as evidence about the downloader's logic, not about any actual model
download.
"""

from __future__ import annotations

import hashlib
import http.server
import tempfile
import threading
import unittest
from pathlib import Path

from omobench import managed_setup as ms


# Four download chunks' worth, so "cancel partway" and "resume from here" are
# real positions in a loop rather than one indivisible read. Still kilobytes:
# 4 MiB over loopback, not 2.5 GB over the internet.
BODY = bytes(range(256)) * 16384       # 4 MiB
DIGEST = hashlib.sha256(BODY).hexdigest()


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves one fixture, honours Range, and can be told to cut the response.

    `server.cut_after` makes it send that many bytes and close, which is what a
    dropped connection looks like from the client's side.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102 - quiet during tests
        pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        if self.path != "/fixture.bin":
            self.send_error(404)
            return
        start = 0
        header = self.headers.get("Range")
        if header and header.startswith("bytes="):
            start = int(header[len("bytes="):].split("-")[0])
            self.server.ranges_served.append(start)
        body = BODY[start:]
        cut = self.server.cut_after
        self.send_response(206 if start else 200)
        self.send_header("Content-Type", "application/octet-stream")
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(BODY) - 1}/{len(BODY)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if cut is None:
            # A cancelled download hangs up mid-response, which is an ordinary
            # thing for a client to do and not an error worth a traceback.
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            return
        # Send part of it and hang up. The client sees a short read, which is
        # the thing a resume has to recover from.
        try:
            self.wfile.write(body[:cut])
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


class RealDownloadTests(unittest.TestCase):
    """Fixtures, not a model. See this module's docstring."""

    def setUp(self) -> None:
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        self.server.cut_after = None
        self.server.ranges_served = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.managed = Path(temporary.name)
        self.part = self.managed / "downloads" / "fixture.bin.part"
        self.final = self.managed / "cache" / "fixture.bin"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/fixture.bin"

    def download(self, *, sha256: str = DIGEST, size: int = len(BODY), **extra) -> list:
        seen: list = []
        ms.resumable_download(
            self.url, part_path=self.part, final_path=self.final,
            expected_size=size, expected_sha256=sha256,
            managed=self.managed, label="fixture",
            progress=lambda label, done, total: seen.append((done, total)),
            **extra,
        )
        return seen

    # -- the ordinary case ----------------------------------------------------

    def test_a_download_lands_verified_with_real_byte_counts(self) -> None:
        seen = self.download()
        self.assertEqual(self.final.read_bytes(), BODY)
        self.assertFalse(self.part.exists(), "the partial file is promoted, not left behind")
        # The counts are the downloader's own, never a manufactured percentage.
        self.assertEqual(seen[-1], (len(BODY), len(BODY)))
        self.assertTrue(all(done <= len(BODY) for done, _ in seen))

    def test_an_already_verified_file_is_not_fetched_again(self) -> None:
        self.download()
        self.server.cut_after = 0      # any request now would produce nothing
        self.download()
        self.assertEqual(self.final.read_bytes(), BODY)

    def test_a_cached_file_that_no_longer_verifies_is_quarantined_not_used(self) -> None:
        self.final.parent.mkdir(parents=True, exist_ok=True)
        self.final.write_bytes(b"x" * len(BODY))
        self.download()
        self.assertEqual(self.final.read_bytes(), BODY)
        quarantined = list((self.managed / "quarantine").glob("**/*"))
        self.assertTrue([path for path in quarantined if path.is_file()],
                        "the file that failed re-verification is kept, not deleted")

    # -- interrupted, then resumed --------------------------------------------

    def test_a_dropped_connection_resumes_from_the_bytes_already_on_disk(self) -> None:
        cut = 1024 * 1024
        self.server.cut_after = cut
        with self.assertRaises(ms.ManagedSetupError) as refused:
            self.download()
        self.assertIn("Retry to resume it", str(refused.exception))
        self.assertEqual(self.part.stat().st_size, cut,
                         "the bytes that did arrive are kept")

        self.server.cut_after = None
        self.download()
        self.assertEqual(self.final.read_bytes(), BODY)
        self.assertEqual(self.server.ranges_served, [cut],
                         "the second attempt asked for the rest, not the whole file")

    def test_a_partial_file_larger_than_the_expected_size_starts_over(self) -> None:
        """A leftover from a different, larger file is not a head start."""
        self.part.parent.mkdir(parents=True, exist_ok=True)
        self.part.write_bytes(b"z" * (len(BODY) + 1024))
        self.download()
        self.assertEqual(self.final.read_bytes(), BODY)
        self.assertEqual(self.server.ranges_served, [],
                         "nothing was resumed from, because nothing usable was there")

    # -- refused -------------------------------------------------------------

    def test_bytes_that_do_not_hash_as_promised_are_quarantined(self) -> None:
        wrong = "b" * 64
        with self.assertRaises(ms.ManagedSetupError) as refused:
            self.download(sha256=wrong)
        message = str(refused.exception)
        self.assertIn("failed SHA-256 verification", message)
        self.assertIn("quarantined, not installed", message)
        self.assertFalse(self.final.exists(), "nothing reached the cache")
        self.assertFalse(self.part.exists(), "and the bad bytes are not left to be resumed")
        kept = [path for path in (self.managed / "quarantine").glob("**/*") if path.is_file()]
        self.assertTrue(kept, "the rejected bytes are kept for inspection")
        self.assertIn(BODY, [path.read_bytes() for path in kept])

    def test_more_bytes_than_promised_is_a_failure_not_a_bigger_file(self) -> None:
        with self.assertRaises(ms.ManagedSetupError) as refused:
            self.download(size=len(BODY) // 2, sha256=DIGEST)
        self.assertIn("exceeded the expected size", str(refused.exception))
        self.assertFalse(self.final.exists())

    def test_a_cancelled_download_stops_and_says_so(self) -> None:
        cancel = threading.Event()
        cancel.set()                    # cancelled before the first chunk
        with self.assertRaises(ms.ManagedSetupError) as refused:
            self.download(cancel_event=cancel)
        self.assertIn("cancelled", str(refused.exception))
        self.assertFalse(self.final.exists(), "a cancelled download installs nothing")

    def test_cancelling_midway_keeps_what_arrived_and_installs_nothing(self) -> None:
        class CancelAfterFirstChunk:
            def __init__(self) -> None:
                self.calls = 0

            def is_set(self) -> bool:
                self.calls += 1
                return self.calls > 2

        with self.assertRaises(ms.ManagedSetupError):
            self.download(cancel_event=CancelAfterFirstChunk())
        self.assertFalse(self.final.exists())
        # Whatever arrived stays on disk, so retrying resumes rather than
        # starting the whole download again.
        if self.part.exists():
            self.assertLess(self.part.stat().st_size, len(BODY))

    def test_a_missing_file_is_an_http_error_not_a_silent_empty_download(self) -> None:
        with self.assertRaises(ms.ManagedSetupError) as refused:
            ms.resumable_download(
                f"http://127.0.0.1:{self.server.server_port}/absent.bin",
                part_path=self.part, final_path=self.final,
                expected_size=len(BODY), expected_sha256=DIGEST,
                managed=self.managed, label="fixture")
        self.assertIn("HTTP 404", str(refused.exception))
        self.assertFalse(self.final.exists())


if __name__ == "__main__":
    unittest.main()
