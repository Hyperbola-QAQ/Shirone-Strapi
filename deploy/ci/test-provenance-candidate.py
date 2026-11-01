#!/usr/bin/env python3
"""Isolated Git/CLI contract tests only; no real attestation is created."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('verify-provenance-candidate.py').resolve()
OLD = 'sha256:' + 'a' * 64
NEW = 'sha256:' + 'b' * 64
PREFIX = '192.168.86.100:30500/shirone/cms@'


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='shirone-provenance-')
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'CI test')
        self.git('config', 'user.email', 'ci@example.invalid')
        self.path = self.repo / 'deploy/k8s/cms.yaml'
        self.path.parent.mkdir(parents=True)
        self.path.write_text(self.manifest(OLD))
        self.git('add', '.')
        self.git('commit', '-qm', 'source')
        self.source = self.git('rev-parse', 'HEAD')
        self.path.write_text(self.manifest(NEW))
        self.commit_deployment()
        self.git('init', '--bare', '-q', str(self.root / 'remote.git'))
        self.git('remote', 'add', 'origin', str(self.root / 'remote.git'))
        self.git('push', '-q', '-u', 'origin', 'main')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        gh = self.bin / 'gh'
        gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GH_ARGS_FILE"\nexit "${GH_EXIT:-0}"\n')
        gh.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, text=True).strip()

    def manifest(self, digest, extra=''):
        return ('apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: shirone-cms\n'
                'spec:\n  template:\n    spec:\n      containers:\n      - name: cms\n'
                f'        image: {PREFIX}{digest}\n{extra}')

    def commit_deployment(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'deployment')
        self.deployment = self.git('rev-parse', 'HEAD')

    def verify(self, source=None, deployment=None, signer=None, digest=NEW, gh_exit='0'):
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                   GH_ARGS_FILE=str(self.root / 'gh-args'), GH_EXIT=gh_exit)
        return subprocess.run(['python3', str(SCRIPT), 'cms', source or self.source,
                               deployment or self.deployment, signer or self.source, digest],
                              cwd=self.repo, env=env, capture_output=True, text=True)

    def test_valid_contract_and_pinned_gh_flags(self):
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.root / 'gh-args').read_text().splitlines()
        self.assertIn('--deny-self-hosted-runners', args)
        self.assertIn('Hyperbola-QAQ/Shirone-Strapi/.github/workflows/shirone-release.yml', args)
        self.assertIn('oci://ghcr.io/hyperbola-qaq/shirone-cms@' + NEW, args)
        self.assertEqual(args[args.index('--source-digest') + 1], self.source)
        self.assertEqual(args[args.index('--signer-digest') + 1], self.source)

    def test_gh_rejection_fails_closed(self):
        self.assertNotEqual(self.verify(gh_exit='1').returncode, 0)

    def test_dirty_tree_rejected(self):
        (self.repo / 'untracked').write_text('x')
        self.assertNotEqual(self.verify().returncode, 0)

    def test_wrong_source_and_digest_rejected(self):
        self.assertNotEqual(self.verify(source=self.deployment).returncode, 0)
        self.assertNotEqual(self.verify(digest=OLD).returncode, 0)
        self.assertNotEqual(self.verify(signer='main').returncode, 0)

    def test_other_manifest_change_rejected(self):
        self.git('reset', '--hard', '-q', self.source)
        self.path.write_text(self.manifest(NEW, '  replicas: 2\n'))
        self.commit_deployment()
        self.git('push', '-q', '--force', 'origin', 'main')
        self.assertNotEqual(self.verify().returncode, 0)

    def test_extra_file_rejected(self):
        self.git('reset', '--hard', '-q', self.source)
        self.path.write_text(self.manifest(NEW))
        (self.repo / 'extra').write_text('x')
        self.commit_deployment()
        self.git('push', '-q', '--force', 'origin', 'main')
        self.assertNotEqual(self.verify().returncode, 0)

    def test_duplicate_yaml_key_rejected(self):
        self.git('reset', '--hard', '-q', self.source)
        self.path.write_text(self.manifest(NEW) + 'kind: Deployment\n')
        self.commit_deployment()
        self.git('push', '-q', '--force', 'origin', 'main')
        self.assertNotEqual(self.verify().returncode, 0)


if __name__ == '__main__':
    unittest.main()
