#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('verify-release-metadata-candidate.py')
spec = importlib.util.spec_from_file_location('metadata_candidate', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SOURCE = 'a' * 40
SIGNER = 'b' * 40
RECIPIENT = 'sha256:' + 'c' * 64
CIPHERTEXT = 'sha256:' + 'd' * 64
MANIFEST = 'sha256:' + 'e' * 64


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'metadata.json'
        self.data = {'schema': 1, 'source_commit': SOURCE, 'recipient_fingerprint': RECIPIENT,
                     'ciphertext_digest': CIPHERTEXT,
                     'manifests': dict.fromkeys(('cms', 'ssr', 'reconciler'), MANIFEST)}
        self.path.write_text(json.dumps(self.data))

    def checked(self):
        return module.verify(self.path, SOURCE, SIGNER, RECIPIENT, CIPHERTEXT)

    @patch.object(module.subprocess, 'run')
    def test_signature_identity_and_success(self, gh):
        gh.return_value.returncode = 0
        self.assertEqual(self.checked(), self.data)
        argv = gh.call_args.args[0]
        for expected in ('--source-digest', SOURCE, '--signer-digest', SIGNER,
                         '--signer-workflow', module.WORKFLOW, '--deny-self-hosted-runners'):
            self.assertIn(expected, argv)

    @patch.object(module.subprocess, 'run')
    def test_bad_signature_rejected(self, gh):
        gh.return_value.returncode = 1
        with self.assertRaises(ValueError): self.checked()

    @patch.object(module.subprocess, 'run')
    def test_incorrect_bound_values_rejected(self, gh):
        gh.return_value.returncode = 0
        for key, bad in [('source_commit', 'f' * 40), ('recipient_fingerprint', MANIFEST), ('ciphertext_digest', MANIFEST)]:
            changed = dict(self.data, **{key: bad})
            self.path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError): self.checked()

    @patch.object(module.subprocess, 'run')
    def test_duplicate_key_and_unexpected_component_rejected(self, gh):
        gh.return_value.returncode = 0
        self.path.write_text('{"schema":1,"schema":1}')
        with self.assertRaises(ValueError): self.checked()
        changed = dict(self.data, manifests={'cms': MANIFEST, 'ssr': MANIFEST, 'other': MANIFEST})
        self.path.write_text(json.dumps(changed))
        with self.assertRaises(ValueError): self.checked()

    @patch.object(module.subprocess, 'run')
    def test_symlink_rejected(self, gh):
        gh.return_value.returncode = 0
        other = self.path.with_name('link')
        other.symlink_to(self.path)
        with self.assertRaises(OSError): module.verify(other, SOURCE, SIGNER, RECIPIENT, CIPHERTEXT)


if __name__ == '__main__':
    unittest.main()
