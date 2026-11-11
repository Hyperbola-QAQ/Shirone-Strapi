#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('release_git', HERE / 'automatic-release-git.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
fixture_spec = importlib.util.spec_from_file_location('fixture', HERE / 'test-provenance-candidate.py')
fixture = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixture)


class GitReleaseTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CandidateTests(methodName='test_valid_contract_and_pinned_gh_flags')
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.old = fixture.PREFIX + fixture.NEW
        self.new = fixture.PREFIX + 'sha256:' + 'c' * 64

    def run_commit(self):
        return release.commit_manifest(self.f.repo, 'cms', self.f.deployment, self.old, self.new)

    def test_real_git_commits_only_expected_image_and_updates_remote(self):
        result = self.run_commit()
        self.assertEqual(release.remote_head(self.f.repo), result)
        self.assertEqual(self.f.git('diff', self.f.deployment, result, '--name-only'), 'deploy/k8s/cms.yaml')
        self.assertEqual(self.f.path.read_text(), self.f.manifest('sha256:' + 'c' * 64))

    def test_dirty_tree_or_wrong_old_image_rejected(self):
        (self.f.repo / 'unrelated').write_text('keep')
        with self.assertRaises(ValueError): self.run_commit()
        (self.f.repo / 'unrelated').unlink()
        with self.assertRaises(ValueError):
            release.commit_manifest(self.f.repo, 'cms', self.f.deployment, fixture.PREFIX + fixture.OLD, self.new)
        self.assertEqual(self.f.git('rev-parse', 'HEAD'), self.f.deployment)

    def test_stale_remote_and_multiple_image_lines_rejected(self):
        self.f.git('push', '-q', '--force', 'origin', self.f.source + ':refs/heads/main')
        with self.assertRaises(ValueError): self.run_commit()
        self.f.git('push', '-q', 'origin', self.f.deployment + ':refs/heads/main')
        self.f.path.write_text(self.f.path.read_text() + '  image: ' + self.old + '\n')
        self.f.git('add', '.')
        self.f.git('commit', '-qm', 'multiple')
        self.f.deployment = self.f.git('rev-parse', 'HEAD')
        self.f.git('push', '-q', 'origin', 'main')
        with self.assertRaises(ValueError): self.run_commit()

    def test_concurrent_remote_rewind_cannot_be_overwritten(self):
        original_git = release.git
        def race(repo, *args):
            if args and args[0] == 'push':
                self.f.git('--git-dir', str(self.f.root / 'remote.git'), 'update-ref',
                           'refs/heads/main', self.f.source, self.f.deployment)
            return original_git(repo, *args)
        with patch.object(release, 'git', side_effect=race), self.assertRaises(subprocess.CalledProcessError):
            self.run_commit()
        self.assertEqual(release.remote_head(self.f.repo), self.f.source)

    def test_commit_hook_cannot_add_hidden_manifest_changes(self):
        hook = self.f.repo / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nprintf "\\n# unexpected\\n" >> deploy/k8s/cms.yaml\ngit add deploy/k8s/cms.yaml\n')
        hook.chmod(0o755)
        with self.assertRaises(ValueError): self.run_commit()
        self.assertEqual(release.remote_head(self.f.repo), self.f.deployment)


if __name__ == '__main__':
    unittest.main()
