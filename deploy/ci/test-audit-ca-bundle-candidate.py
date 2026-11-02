#!/usr/bin/env python3
"""Offline policy checks; does not assert whole-image equivalence."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ca_audit', Path(__file__).with_name('audit-ca-bundle-candidate.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
BAD = b'-----BEGIN CERTIFICATE-----bad-----END CERTIFICATE-----'
GOOD = b'-----BEGIN CERTIFICATE-----good-----END CERTIFICATE-----'
SOURCE = b'header\n' + BAD + b'\n' + GOOD + b'\n'
EXPECTED = b'header\n\n' + GOOD + b'\n'


class BundlePolicy(unittest.TestCase):
    def setUp(self):
        self.fingerprints = patch.object(audit, 'fingerprint', side_effect=lambda block: 'bad' if block == BAD else 'good')
        self.fingerprints.start()
        self.addCleanup(self.fingerprints.stop)

    def test_only_denied_certificate_removed(self):
        self.assertEqual(audit.audit(SOURCE, EXPECTED, {'bad'}), 1)

    def test_unremoved_certificate_rejected(self):
        with self.assertRaises(ValueError):
            audit.audit(SOURCE, SOURCE, {'bad'})

    def test_unrelated_change_rejected(self):
        with self.assertRaises(ValueError):
            audit.audit(SOURCE, EXPECTED.replace(GOOD, b''), {'bad'})

    def test_extra_bytes_rejected(self):
        with self.assertRaises(ValueError):
            audit.audit(SOURCE, EXPECTED + b'changed', {'bad'})

    def test_malformed_markers_rejected(self):
        source = SOURCE + b'-----BEGIN CERTIFICATE-----'
        with self.assertRaises(ValueError):
            audit.audit(source, EXPECTED + b'-----BEGIN CERTIFICATE-----', {'bad'})


if __name__ == '__main__':
    unittest.main()
