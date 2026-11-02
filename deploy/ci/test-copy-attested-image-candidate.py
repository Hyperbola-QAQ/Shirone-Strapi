#!/usr/bin/env python3
"""Mock-only contract tests; no real registry, signature, copy or credentials."""
import contextlib
import hashlib
import importlib.util
import io
import tempfile
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('copy_candidate', Path(__file__).with_name('copy-attested-image-candidate.py'))
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
DATA = b'{"schemaVersion":2,"example":"manifest"}'
DIGEST = 'sha256:' + hashlib.sha256(DATA).hexdigest()
SOURCE = 'a' * 40
SIGNER = 'b' * 40


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.inspections = 0
        self.bad_inspection = 0
        self.fail_program = None
        self.fail_command = None

    def run_fake(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == self.fail_program or (self.fail_command and args[:2] == self.fail_command):
            raise subprocess.CalledProcessError(1, args)
        if args[:2] == ['skopeo', 'inspect']:
            self.inspections += 1
            return subprocess.CompletedProcess(args, 0, b'wrong' if self.inspections == self.bad_inspection else DATA)
        return subprocess.CompletedProcess(args, 0, b'')

    def check(self, **kwargs):
        with patch.object(candidate.subprocess, 'run', self.run_fake), \
                patch.object(candidate, 'destination_cert_dir', return_value='/protected/ca'), \
                contextlib.redirect_stdout(io.StringIO()):
            candidate.check(kwargs.get('component', 'cms'), kwargs.get('source', SOURCE),
                            kwargs.get('signer', SIGNER), kwargs.get('digest', DIGEST), kwargs.get('apply', False))

    def test_default_is_read_only_and_pins_identity(self):
        self.check()
        self.assertEqual([c[:2] for c in self.calls], [['gh', 'attestation'], ['skopeo', 'inspect']])
        gh = self.calls[0]
        self.assertIn('--deny-self-hosted-runners', gh)
        self.assertIn(candidate.WORKFLOW, gh)
        self.assertEqual(gh[gh.index('--source-digest') + 1], SOURCE)
        self.assertEqual(gh[gh.index('--signer-digest') + 1], SIGNER)
        self.assertIn('oci://ghcr.io/hyperbola-qaq/shirone-cms@' + DIGEST, gh)

    def test_apply_preserves_all_and_verifies_destination_and_tag(self):
        self.check(component='ssr', apply=True)
        self.assertEqual([c[:2] for c in self.calls], [['gh', 'attestation'], ['skopeo', 'inspect'],
                                                       ['skopeo', 'copy'], ['skopeo', 'inspect'], ['skopeo', 'inspect']])
        copy = self.calls[2]
        self.assertIn('--all', copy)
        self.assertIn('--preserve-digests', copy)
        self.assertEqual(copy[copy.index('--dest-cert-dir') + 1], '/protected/ca')
        self.assertTrue(copy[-1].startswith('docker://192.168.86.100:30500/shirone/ssr:candidate-'))
        self.assertEqual(self.calls[3][-1], 'docker://192.168.86.100:30500/shirone/ssr@' + DIGEST)
        for inspect_call in self.calls[3:]:
            self.assertEqual(inspect_call[inspect_call.index('--cert-dir') + 1], '/protected/ca')
            self.assertNotIn('--tls-verify=false', inspect_call)

    def test_unprotected_or_extra_ca_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'ca.crt').write_text('fake')
            (directory / 'client.key').write_text('fake')
            with patch.object(candidate, 'DEST_CERT_DIR', directory):
                with self.assertRaises(ValueError):
                    candidate.destination_cert_dir()

    def test_bad_inputs_fail_before_any_io(self):
        for params in ({'source': 'main'}, {'signer': 'main'}, {'digest': 'latest'}, {'component': '../cms'}):
            with self.assertRaises(ValueError):
                self.check(**params)
        self.assertFalse(self.calls)

    def test_attestation_failure_prevents_all_copy(self):
        self.fail_program = 'gh'
        with self.assertRaises(subprocess.CalledProcessError):
            self.check(apply=True)
        self.assertEqual(len(self.calls), 1)

    def test_wrong_source_digest_prevents_copy(self):
        self.bad_inspection = 1
        with self.assertRaises(ValueError):
            self.check(apply=True)
        self.assertFalse(any(c[:2] == ['skopeo', 'copy'] for c in self.calls))

    def test_copy_failure_fails_closed(self):
        self.fail_command = ['skopeo', 'copy']
        with self.assertRaises(subprocess.CalledProcessError):
            self.check(apply=True)

    def test_bad_destination_or_tag_digest_fails_closed(self):
        for n in (2, 3):
            self.inspections = 0
            self.bad_inspection = n
            with self.assertRaises(ValueError):
                self.check(apply=True)


if __name__ == '__main__':
    unittest.main()
