"""Exact historical bytes, not a hash alias or relaxed acceptance rules."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_validate_results import validate_results


class ProfileHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.id = 'chat-small-v1'
        self.old = b'id: chat-small-v1\nacceptance:\n  minimum_successful_requests: 47\n  maximum_error_rate_percent: 0.5\n'
        self.digest = 'sha256:' + hashlib.sha256(self.old).hexdigest()
        (self.root / (self.id + '.yaml')).write_bytes(self.old.replace(b'47', b'40'))
        self.entry = dict(profile_id=self.id, sha256=self.digest,
                          content_base64=base64.b64encode(self.old).decode())

    def ledger(self, entries):
        (self.root / 'history.json').write_text(json.dumps(dict(format_version=1, entries=entries)))

    def load(self):
        return validate_results.load_profile_acceptance(self.root, self.id, self.digest)

    def test_original_thresholds_are_used_without_changing_current_profile(self):
        self.ledger([self.entry])
        before = (self.root / (self.id + '.yaml')).read_bytes()
        acceptance, error = self.load()
        self.assertIsNone(error)
        self.assertEqual(acceptance, dict(minimum_successful_requests=47, maximum_error_rate_percent=0.5))
        self.assertEqual(before, (self.root / (self.id + '.yaml')).read_bytes())

    def test_unknown_hash_is_not_an_automatic_line_ending_alias(self):
        self.assertIsNotNone(self.load()[1])

    def test_corrupt_snapshot_refused(self):
        self.entry['content_base64'] = base64.b64encode(self.old + b'\n').decode()
        self.ledger([self.entry])
        self.assertIsNotNone(self.load()[1])

    def test_wrong_profile_and_duplicate_entries_refused(self):
        for entries in [[dict(self.entry, profile_id='other')], [self.entry, self.entry]]:
            with self.subTest(entries=entries):
                self.ledger(entries)
                self.assertIsNotNone(self.load()[1])

    def test_embedded_profile_id_must_match(self):
        raw = self.old.replace(b'id: chat-small-v1', b'id: other')
        self.digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
        self.ledger([dict(self.entry, sha256=self.digest, content_base64=base64.b64encode(raw).decode())])
        self.assertIsNotNone(self.load()[1])

    def test_malformed_ledger_fails_closed(self):
        for value in ['null', '{}', '{', '[1]', '{"format_version": 2, "entries": []}']:
            with self.subTest(value=value):
                (self.root / 'history.json').write_text(value)
                self.assertIsNotNone(self.load()[1])

    def test_current_profile_does_not_depend_on_history(self):
        (self.root / 'history.json').write_text('{broken')
        current = validate_results.sha256(self.root / (self.id + '.yaml'))
        self.assertIsNone(validate_results.load_profile_acceptance(self.root, self.id, current)[1])

    def test_shipped_snapshots_reconstruct_from_committed_profiles(self):
        root = validate_results.ROOT
        entries = json.loads((validate_results.PROFILES_PATH / 'history.json').read_text())['entries']
        self.assertEqual(len(entries), 2)
        for entry in entries:
            with self.subTest(digest=entry['sha256']):
                blob = subprocess.check_output(['git', 'show', entry['source_revision'] +
                    ':benchmarks/profiles/' + entry['profile_id'] + '.yaml'], cwd=root)
                snapshot = base64.b64decode(entry['content_base64'])
                self.assertEqual(snapshot, blob.replace(b'\n', b'\r\n'))
                self.assertEqual(entry['sha256'], 'sha256:' + hashlib.sha256(snapshot).hexdigest())
                acceptance, error = validate_results.load_profile_acceptance(
                    validate_results.PROFILES_PATH, entry['profile_id'], entry['sha256'])
                self.assertIsNone(error)
                self.assertEqual(acceptance, dict(minimum_successful_requests=40, maximum_error_rate_percent=1))
