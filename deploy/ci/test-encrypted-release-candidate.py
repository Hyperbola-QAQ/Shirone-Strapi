#!/usr/bin/env python3
"""Offline age integration tests. The GH verifier here is explicitly a mock."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('encrypted-release-candidate.py')
spec = importlib.util.spec_from_file_location('encrypted_release', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SOURCE = 'a' * 40
SIGNER = 'b' * 40
MANIFEST = 'sha256:' + 'c' * 64


def fake_oci(path):
    return {'archive_digest': 'sha256:' + hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'manifest_digest': MANIFEST}


@unittest.skipUnless(shutil.which('age') and shutil.which('age-keygen'), 'age is not installed')
class EncryptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.key = self.dir / 'test-key'
        self.other_key = self.dir / 'wrong-test-key'
        for key in (self.key, self.other_key):
            subprocess.run(['age-keygen', '-o', str(key)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.recipient = subprocess.run(['age-keygen', '-y', str(self.key)], check=True,
                                        capture_output=True, text=True).stdout.strip()
        self.recipient_hash = module.recipient_fingerprint(self.recipient)
        self.manifests = {n: MANIFEST for n in module.envelope.COMPONENTS}
        inputs = {}
        for name in module.envelope.COMPONENTS:
            inputs[name] = self.dir / (name + '.input')
            inputs[name].write_bytes((name + ' fixture').encode())
        self.plain = self.dir / 'release.tar'
        patcher = patch.object(module.envelope.oci, 'verify', side_effect=fake_oci)
        patcher.start()
        self.addCleanup(patcher.stop)
        module.envelope.build(self.plain, SOURCE, self.recipient_hash, inputs)
        self.cipher = self.dir / 'release.age'
        module.encrypt(self.plain, self.cipher, self.recipient, SOURCE, self.manifests)
        self.cipher_hash = 'sha256:' + hashlib.sha256(self.cipher.read_bytes()).hexdigest()
        self.output = self.dir / 'decrypted.tar'
        self.mock_dir = self.dir / 'mock-bin'
        self.mock_dir.mkdir()
        self.gh = self.mock_dir / 'gh'
        self.old_path = os.environ.get('PATH', '')
        os.environ['PATH'] = str(self.mock_dir) + os.pathsep + self.old_path
        self.addCleanup(os.environ.__setitem__, 'PATH', self.old_path)

    def mock_gh(self, code):
        # Test stub only: a zero exit here is NOT a cryptographic signature.
        self.gh.write_text('#!/bin/sh\nexit ' + str(code) + '\n')
        self.gh.chmod(0o700)

    def decrypt(self, key=None, digest=None, source=SOURCE):
        return module.decrypt(self.cipher, self.output, key or self.key, self.recipient, source, SIGNER,
                              digest or self.cipher_hash, self.manifests)

    def test_real_age_roundtrip_after_mock_attestation(self):
        self.mock_gh(0)
        self.decrypt()
        self.assertEqual(self.plain.read_bytes(), self.output.read_bytes())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_verifier_receives_all_fixed_identity_guards(self):
        capture = self.dir / 'gh-arguments'
        self.gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CI_GH_CAPTURE"\nexit 0\n')
        self.gh.chmod(0o700)
        with patch.dict(os.environ, {'CI_GH_CAPTURE': str(capture)}):
            self.decrypt()
        args = capture.read_text().splitlines()
        self.assertEqual(args[:2], ['attestation', 'verify'])
        self.assertIn(module.REPO, args)
        self.assertIn(module.WORKFLOW, args)
        self.assertIn('refs/heads/main', args)
        self.assertIn(SOURCE, args)
        self.assertIn(SIGNER, args)
        self.assertIn('--deny-self-hosted-runners', args)

    def test_failed_attestation_does_not_create_plaintext(self):
        self.mock_gh(1)
        with self.assertRaises(ValueError): self.decrypt(key=self.dir / 'missing-key')
        self.assertFalse(self.output.exists())

    def test_missing_verifier_does_not_create_plaintext(self):
        # Disable PATH entirely to prevent accidentally finding a real gh.
        os.environ['PATH'] = str(self.mock_dir)
        with self.assertRaises(OSError): self.decrypt()
        self.assertFalse(self.output.exists())

    def test_unexpected_ciphertext_digest_is_rejected_before_verifier(self):
        with self.assertRaisesRegex(ValueError, 'unexpected ciphertext identity'):
            self.decrypt(digest='sha256:' + 'd' * 64)
        self.assertFalse(self.output.exists())

    def test_wrong_key_fails_real_age(self):
        self.mock_gh(0)
        with self.assertRaisesRegex(ValueError, 'cryptographic operation failed'):
            self.decrypt(key=self.other_key)

    def test_truncated_ciphertext_fails_real_age(self):
        self.mock_gh(0)
        self.cipher.write_bytes(self.cipher.read_bytes()[:-20])
        digest = 'sha256:' + hashlib.sha256(self.cipher.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'cryptographic operation failed'):
            self.decrypt(digest=digest)

    def test_same_size_ciphertext_tampering_fails_real_age(self):
        self.mock_gh(0)
        content = bytearray(self.cipher.read_bytes())
        content[-10] ^= 1
        self.cipher.write_bytes(content)
        digest = 'sha256:' + hashlib.sha256(content).hexdigest()
        with self.assertRaisesRegex(ValueError, 'cryptographic operation failed'):
            self.decrypt(digest=digest)

    def test_stale_source_rejected_after_mock_attestation(self):
        self.mock_gh(0)
        with self.assertRaises(ValueError): self.decrypt(source='d' * 40)

    def test_world_readable_identity_rejected(self):
        self.mock_gh(0)
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'private key must be owned'): self.decrypt()
        self.assertFalse(self.output.exists())

    def test_output_never_overwritten(self):
        self.mock_gh(0)
        self.output.write_bytes(b'user data')
        with self.assertRaises(FileExistsError): self.decrypt()
        self.assertEqual(self.output.read_bytes(), b'user data')


if __name__ == '__main__':
    unittest.main()
