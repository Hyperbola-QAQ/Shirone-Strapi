#!/usr/bin/env python3
"""Static guards only: do not claim a hosted run or an attestation occurred."""
import pathlib
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import unittest

import yaml

HERE = pathlib.Path(__file__).resolve().parent


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.load((HERE / 'hosted-encrypted-release-candidate.yml').read_text(), Loader=yaml.BaseLoader)

    def test_manual_only_and_disabled_by_location(self):
        self.assertEqual(set(self.doc['on']), {'workflow_dispatch'})
        self.assertEqual(self.doc['permissions'], {})
        self.assertNotEqual(HERE.parent.parent.name, '.github')

    def test_build_has_no_signing_or_package_privileges(self):
        job = self.doc['jobs']['build_test_encrypt']
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        self.assertEqual(job['permissions'], {'contents': 'read'})
        steps = job['steps']
        self.assertEqual(steps[0]['with']['persist-credentials'], 'false')
        store = next(s for s in steps if s.get('name') == 'Enable OCI image store on the disposable runner')
        self.assertIn('setup-hosted-docker-store-candidate.sh', store['run'])
        builder = next(s for s in steps if s.get('name') == 'Prepare a digest-pinned OCI builder')
        self.assertIn('setup-oci-builder-candidate.sh', builder['run'])
        build = next(s for s in steps if s.get('id') == 'build')
        self.assertIn('build-encrypted-release-candidate.sh', build['run'])
        self.assertIn('release-recipient.txt', build['run'])
        upload = next(s for s in steps if s.get('id') == 'upload')
        self.assertIn('release.age', upload['with']['path'])
        self.assertIn('metadata.json', upload['with']['path'])
        self.assertEqual(upload['with']['retention-days'], '1')
        self.assertEqual(upload['with']['if-no-files-found'], 'error')

    def test_sign_is_separate_gated_and_does_not_checkout(self):
        sign = self.doc['jobs']['sign']
        self.assertEqual(sign['needs'], 'build_test_encrypt')
        self.assertEqual(sign['permissions'], {'contents': 'read', 'attestations': 'write', 'id-token': 'write'})
        steps = sign['steps']
        self.assertFalse(any('checkout@' in step.get('uses', '') for step in steps))
        self.assertTrue(any('sha256sum' in step.get('run', '') for step in steps))
        download = next(s for s in steps if 'actions/download-artifact@' in s.get('uses', ''))
        self.assertEqual(download['with']['merge-multiple'], 'true')
        self.assertIn('ARTIFACT_ID', download['with']['artifact-ids'])
        attest = [s for s in steps if 'actions/attest@' in s.get('uses', '')]
        self.assertEqual(len(attest), 2)
        self.assertEqual({s['with']['subject-path'].split('/')[-1] for s in attest}, {'release.age', 'metadata.json'})
        self.assertTrue(all(s['with']['create-storage-record'] == 'false' for s in attest))

    def test_all_actions_use_reviewed_full_commit_ids(self):
        for job in self.doc['jobs'].values():
            for step in job['steps']:
                if 'uses' in step:
                    self.assertRegex(step['uses'], r'^actions/[a-z-]+@[0-9a-f]{40}$')

    def test_sign_job_independently_checks_metadata(self):
        sign = self.doc['jobs']['sign']
        command = next(s['run'] for s in sign['steps'] if s.get('name') == 'Check exact ciphertext')
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            artifact = root / 'shirone-sign'; artifact.mkdir()
            ciphertext = b'nonsecret-test-ciphertext'
            (artifact / 'release.age').write_bytes(ciphertext)
            digest = 'sha256:' + hashlib.sha256(ciphertext).hexdigest()
            key = 'age1' + 'q' * 58
            metadata = {'schema': 1, 'source_commit': 'a' * 40,
                        'recipient_fingerprint': 'sha256:' + hashlib.sha256(key.encode()).hexdigest(),
                        'ciphertext_digest': digest,
                        'manifests': dict.fromkeys(('cms', 'ssr', 'reconciler'), 'sha256:' + 'b' * 64)}
            response = {'type': 'file', 'encoding': 'base64', 'size': len(key) + 1,
                        'content': base64.b64encode((key + '\n').encode()).decode()}
            stub = root / 'gh'; stub.write_text('#!/bin/sh\nprintf "%s" "$FAKE_GH_RESPONSE"\n'); stub.chmod(0o700)
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], RUNNER_TEMP=str(root),
                       GITHUB_SHA='a' * 40, CIPHERTEXT_DIGEST=digest, FAKE_GH_RESPONSE=json.dumps(response))
            def run(data):
                body = json.dumps(data).encode(); (artifact / 'metadata.json').write_bytes(body)
                env['METADATA_DIGEST'] = 'sha256:' + hashlib.sha256(body).hexdigest()
                return subprocess.run(['bash', '-c', command], env=env, capture_output=True).returncode
            self.assertEqual(run(metadata), 0)
            for name, bad in [('source_commit', 'd' * 40), ('recipient_fingerprint', 'sha256:' + 'e' * 64),
                              ('ciphertext_digest', 'sha256:' + 'f' * 64), ('manifests', {'cms': 'sha256:' + 'b' * 64})]:
                with self.subTest(name=name):
                    self.assertNotEqual(run(dict(metadata, **{name: bad})), 0)


if __name__ == '__main__':
    unittest.main()
