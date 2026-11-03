#!/usr/bin/env python3
"""Isolated guards; no production credentials, registry, or cluster access."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('promote-encrypted-candidate.py')
spec = importlib.util.spec_from_file_location('promotion', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
SOURCE, DEPLOYMENT, SIGNER = 'a' * 40, 'b' * 40, 'c' * 40
UID = '11111111-2222-3333-4444-555555555555'
DIGESTS = {n: 'sha256:' + c * 64 for n, c in zip(p.env.COMPONENTS, '123')}
OLD = '192.168.86.100:30500/shirone/cms@sha256:' + '9' * 64


class Guards(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = []
        def install(obj, name, **kw):
            context = patch.object(obj, name, **kw)
            result = context.start()
            self.addCleanup(context.stop)
            return result
        self.chain = install(p, 'validate_chain', side_effect=lambda *a: self.calls.append('chain'))
        self.decrypt = install(p.crypto, 'decrypt', side_effect=lambda *a: self.calls.append('verified-decrypt'))
        install(p.crypto, 'recipient_fingerprint', return_value='sha256:' + 'd' * 64)
        install(p.env, 'inspect', side_effect=lambda *a, **kw: self.calls.append('envelope'))
        install(p.env.oci, 'verify', return_value={'manifest_digest': DIGESTS['cms']})
        install(p.registry, 'destination_cert_dir', return_value='/trusted/ca')
        install(p.registry, 'inspect', side_effect=lambda *a: b'manifest')
        self.check = install(p.registry, 'assert_digest', side_effect=lambda *a: self.calls.append('digest'))
        self.proc = install(p.subprocess, 'run', side_effect=lambda args, **kw: self.calls.append(args))

    def invoke(self, **kw):
        return p.promote('cms', SOURCE, DEPLOYMENT, SIGNER, UID, OLD, '/cipher',
                         'sha256:' + 'e' * 64, '/key', 'recipient', DIGESTS, **kw)

    def test_preview_checks_provenance_before_registry_and_never_applies(self):
        self.invoke()
        self.assertEqual(self.calls[:3], ['chain', 'verified-decrypt', 'envelope'])
        command = self.calls[-1]
        self.assertEqual(command[0], 'bash')
        self.assertNotIn('--apply', command)
        self.assertFalse(any(isinstance(x, list) and x[0] == 'skopeo' for x in self.calls))

    def test_attestation_or_decryption_failure_stops_everything(self):
        self.decrypt.side_effect = ValueError('verification failed')
        with self.assertRaises(ValueError): self.invoke(copy_image=True, apply=True)
        self.assertEqual(self.calls, ['chain'])
        self.proc.assert_not_called()

    def test_chain_failure_prevents_decryption_and_copy(self):
        self.chain.side_effect = ValueError('unreviewed commit')
        with self.assertRaises(ValueError): self.invoke(copy_image=True, apply=True)
        self.decrypt.assert_not_called()
        self.proc.assert_not_called()

    def test_wrong_internal_digest_blocks_deployment(self):
        self.check.side_effect = ValueError('bad digest')
        with self.assertRaises(ValueError): self.invoke(apply=True)
        self.proc.assert_not_called()

    def test_explicit_copy_checks_tag_then_immutable_digest(self):
        self.invoke(copy_image=True)
        copy_calls = [x for x in self.calls if isinstance(x, list) and x[0] == 'skopeo']
        self.assertEqual(len(copy_calls), 1)
        self.assertIn('--preserve-digests', copy_calls[0])
        self.assertEqual(self.calls.count('digest'), 2)
        self.assertNotIn('--apply', self.calls[-1])

    def test_explicit_apply_only_after_verification(self):
        self.invoke(apply=True)
        self.assertEqual(self.calls[-2], 'digest')
        self.assertEqual(self.calls[-1][-1], '--apply')

    def test_invalid_uid_or_image_rejected_before_any_action(self):
        with self.assertRaises(ValueError):
            p.promote('cms', SOURCE, DEPLOYMENT, SIGNER, 'bad', OLD, '/cipher',
                      'sha256:' + 'e' * 64, '/key', 'recipient', DIGESTS)
        with self.assertRaises(ValueError):
            p.promote('cms', SOURCE, DEPLOYMENT, SIGNER, UID, 'bad', '/cipher',
                      'sha256:' + 'e' * 64, '/key', 'recipient', DIGESTS)
        self.assertEqual(self.calls, [])

    def test_copy_failure_stops_deployment(self):
        self.proc.side_effect = subprocess.CalledProcessError(1, 'skopeo')
        with self.assertRaises(subprocess.CalledProcessError): self.invoke(copy_image=True, apply=True)
        self.assertEqual(self.calls.count('digest'), 0)


class RealGitChain(unittest.TestCase):
    def setUp(self):
        fixture_path = SCRIPT.with_name('test-provenance-candidate.py')
        spec = importlib.util.spec_from_file_location('chain_fixture', fixture_path)
        fixture_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture_module)
        self.fixture = fixture_module.CandidateTests(methodName='test_valid_contract_and_pinned_gh_flags')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.previous = os.getcwd()
        os.chdir(self.fixture.repo)
        self.addCleanup(os.chdir, self.previous)
        self.digests = {'cms': fixture_module.NEW, 'ssr': fixture_module.NEW,
                        'reconciler': 'sha256:' + 'd' * 64}

    def test_valid_chain_and_wrong_image(self):
        f = self.fixture
        p.validate_chain('cms', f.source, f.deployment, self.digests)
        self.digests['cms'] = 'sha256:' + '9' * 64
        with self.assertRaises(ValueError): p.validate_chain('cms', f.source, f.deployment, self.digests)

    def test_unrelated_extra_change_rejected(self):
        f = self.fixture
        f.git('reset', '--hard', '-q', f.source)
        f.path.write_text(f.manifest(self.digests['cms'], '  replicas: 2\n'))
        f.commit_deployment()
        f.git('push', '-q', '--force', 'origin', 'main')
        with self.assertRaises(ValueError): p.validate_chain('cms', f.source, f.deployment, self.digests)

    def test_sequential_changes_require_correct_final_component(self):
        f = self.fixture
        f.ssr.write_text(f.ssr.read_text().replace('sha256:' + 'a' * 64, self.digests['ssr']))
        f.commit_deployment()
        f.git('push', '-q', 'origin', 'main')
        p.validate_chain('ssr', f.source, f.deployment, self.digests)
        with self.assertRaises(ValueError): p.validate_chain('cms', f.source, f.deployment, self.digests)


if __name__ == '__main__':
    unittest.main()
