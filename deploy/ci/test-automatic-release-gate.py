#!/usr/bin/env python3
"""Isolated admission checks; no GitHub or production deployment is used."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

path = Path(__file__).with_name('automatic-release-gate.py')
spec = importlib.util.spec_from_file_location('automatic_release_gate', path)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.source = 'a' * 40
        self.signer = 'b' * 40
        self.policy = {'source': self.source, 'signer': self.signer, 'workflow_id': 123}
        self.run = {'id': 55, 'workflow_id': 123, 'head_sha': self.source,
                    'head_branch': 'main', 'status': 'completed', 'conclusion': 'success', 'event': 'push'}
        self.workflow = {'id': 123, 'path': '.github/workflows/shirone-release.yml'}
        self.digest = 'sha256:' + 'c' * 64
        self.signed = {'manifests': {'cms': self.digest}}

    def execute(self, policy=None, run=None, workflow=None, remote=None):
        responses = [run or self.run, workflow or self.workflow]
        with patch.object(gate, 'gh', side_effect=responses), \
             patch.object(gate.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, remote or self.source + '\trefs/heads/main\n')), \
             patch.object(gate.metadata, 'verify', return_value=self.signed) as verify:
            result = gate.admit(policy or self.policy, 55, Path('/test/metadata'), self.digest, self.digest)
            verify.assert_called_once_with(Path('/test/metadata'), self.source, self.signer, self.digest, self.digest)
            return result

    def test_approved_run_reaches_independent_attestation_verifier(self):
        self.assertEqual(self.execute(), self.signed)

    def test_unapproved_or_incomplete_run_is_rejected(self):
        for name, value in [('id', 56), ('workflow_id', 124), ('head_sha', 'd' * 40),
                            ('head_branch', 'elsewhere'), ('status', 'in_progress'),
                            ('conclusion', 'failure'), ('event', 'pull_request')]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.execute(run=dict(self.run, **{name: value}))

    def test_wrong_workflow_or_stale_main_is_rejected(self):
        with self.assertRaises(ValueError):
            self.execute(workflow={'id': 123, 'path': '.github/workflows/evil.yml'})
        with self.assertRaises(ValueError):
            self.execute(remote='d' * 40 + '\trefs/heads/main\n')

    def test_invalid_policy_is_rejected_before_network(self):
        with patch.object(gate, 'gh') as gh, self.assertRaises(ValueError):
            gate.admit(dict(self.policy, signer='main'), 55, Path('/test'), self.digest, self.digest)
        gh.assert_not_called()

    def test_failed_attestation_never_admits(self):
        with patch.object(gate, 'gh', side_effect=[self.run, self.workflow]), \
             patch.object(gate.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, self.source + '\trefs/heads/main\n')), \
             patch.object(gate.metadata, 'verify', side_effect=ValueError('bad signature')), self.assertRaises(ValueError):
            gate.admit(self.policy, 55, Path('/test'), self.digest, self.digest)


if __name__ == '__main__':
    unittest.main()
