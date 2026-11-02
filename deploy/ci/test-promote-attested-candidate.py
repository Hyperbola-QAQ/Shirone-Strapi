#!/usr/bin/env python3
"""Mock contract tests only: no external commands or production resources."""
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('promote_candidate', Path(__file__).with_name('promote-attested-candidate.py'))
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
RAW = b'example raw image manifest'
DIGEST = 'sha256:' + hashlib.sha256(RAW).hexdigest()
SRC = 'a' * 40
DEPLOY = 'b' * 40
SIGNER = 'c' * 40
UID = '12345678-1234-1234-1234-123456789abc'
PREFIX = '192.168.86.100:30500/shirone/cms@'
OLD = PREFIX + 'sha256:' + 'd' * 64
NEW = PREFIX + DIGEST


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.inspect_count = 0
        self.fail_command = None
        self.bad_manifest = False

    def run_fake(self, args, **kwargs):
        self.calls.append(args)
        if self.fail_command and self.fail_command in args[1]:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0)

    def inspect_fake(self, image, cert_dir):
        self.inspect_count += 1
        self.assertEqual(image, 'docker://' + NEW)
        self.assertEqual(cert_dir, '/protected/ca')
        return b'wrong' if self.bad_manifest else RAW

    def invoke(self, **kwargs):
        with patch.object(candidate.subprocess, 'run', self.run_fake), \
                patch.object(candidate.copy_candidate, 'destination_cert_dir', return_value='/protected/ca'), \
                patch.object(candidate.copy_candidate, 'inspect', self.inspect_fake), \
                contextlib.redirect_stdout(io.StringIO()):
            candidate.promote(kwargs.get('component', 'cms'), kwargs.get('source', SRC),
                              kwargs.get('deployment', DEPLOY), kwargs.get('signer', SIGNER),
                              kwargs.get('uid', UID), kwargs.get('old', OLD),
                              kwargs.get('target', NEW), kwargs.get('apply', False))

    def test_preview_is_default_and_identity_is_bound(self):
        self.invoke()
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.calls[0][1].endswith('verify-provenance-candidate.py'))
        self.assertEqual(self.calls[0][2:], ['cms', SRC, DEPLOY, SIGNER, DIGEST])
        self.assertTrue(self.calls[1][1].endswith('promote-reviewed.sh'))
        self.assertEqual(self.calls[1][2:], ['cms', DEPLOY, UID, OLD, NEW])
        self.assertEqual(self.inspect_count, 1)

    def test_apply_requires_explicit_flag(self):
        self.invoke(apply=True)
        self.assertEqual(self.calls[-1][-1], '--apply')

    def test_invalid_inputs_stop_before_io(self):
        for values in ({'source': 'main'}, {'uid': '123'}, {'signer': 'main'},
                       {'target': OLD}, {'component': 'ssr'}, {'target': NEW + ':latest'}):
            with self.assertRaises(ValueError):
                self.invoke(**values)
        self.assertEqual(self.calls, [])

    def test_failed_provenance_stops_before_registry_and_deployment(self):
        self.fail_command = 'verify-provenance-candidate.py'
        with self.assertRaises(subprocess.CalledProcessError):
            self.invoke(apply=True)
        self.assertEqual(self.inspect_count, 0)
        self.assertEqual(len(self.calls), 1)

    def test_wrong_destination_digest_stops_before_deployment(self):
        self.bad_manifest = True
        with self.assertRaises(ValueError):
            self.invoke(apply=True)
        self.assertEqual(len(self.calls), 1)

    def test_existing_guard_failure_is_propagated(self):
        self.fail_command = 'promote-reviewed.sh'
        with self.assertRaises(subprocess.CalledProcessError):
            self.invoke(apply=True)


if __name__ == '__main__':
    unittest.main()
