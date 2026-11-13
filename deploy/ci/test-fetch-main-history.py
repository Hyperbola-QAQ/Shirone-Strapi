#!/usr/bin/env python3
"""Exercise main-only history fetching against an isolated real Git remote."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('fetch-main-history.sh').resolve()


class MainHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='shirone-main-history-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote, self.writer, self.checkout = (root / name for name in ('remote.git', 'writer', 'checkout'))
        self.git('init', '--bare', '--initial-branch=main', str(self.remote))
        self.git('init', '--initial-branch=main', str(self.writer))
        self.git('-C', str(self.writer), 'config', 'user.name', 'Test')
        self.git('-C', str(self.writer), 'config', 'user.email', 'test@example.invalid')
        self.commit('main.txt', 'first', 'first')
        self.ancestor = self.git('-C', str(self.writer), 'rev-parse', 'HEAD')
        self.commit('main.txt', 'second', 'second')
        self.commit('main.txt', 'third', 'third')
        self.head = self.git('-C', str(self.writer), 'rev-parse', 'HEAD')
        self.git('-C', str(self.writer), 'checkout', '--orphan', 'archive/mizuki')
        (self.writer / 'main.txt').unlink()
        self.git('-C', str(self.writer), 'rm', '--cached', 'main.txt')
        self.commit('archive.txt', 'independent archive', 'archive')
        self.archive = self.git('-C', str(self.writer), 'rev-parse', 'HEAD')
        self.git('-C', str(self.writer), 'checkout', 'main')
        self.git('-C', str(self.writer), 'remote', 'add', 'origin', self.remote.as_uri())
        self.git('-C', str(self.writer), 'push', 'origin', 'main', 'archive/mizuki')
        self.git('clone', '--depth', '1', '--single-branch', '--branch', 'main', self.remote.as_uri(), str(self.checkout))

    @staticmethod
    def git(*args):
        return subprocess.run(['git', *args], check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True).stdout.strip()

    def commit(self, name, content, message):
        (self.writer / name).write_text(content)
        self.git('-C', str(self.writer), 'add', name)
        self.git('-C', str(self.writer), 'commit', '-m', message)

    def run_fetch(self, expected):
        return subprocess.run(['bash', str(SCRIPT), expected], cwd=self.checkout,
                              env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_unshallows_only_main_and_preserves_previous_release_ancestry(self):
        self.assertEqual(self.git('-C', str(self.checkout), 'rev-parse', '--is-shallow-repository'), 'true')
        self.assertEqual(self.run_fetch(self.head).returncode, 0)
        self.assertEqual(self.git('-C', str(self.checkout), 'rev-parse', '--is-shallow-repository'), 'false')
        self.git('-C', str(self.checkout), 'merge-base', '--is-ancestor', self.ancestor, self.head)
        missing = subprocess.run(['git', '-C', str(self.checkout), 'cat-file', '-e', self.archive + '^{commit}'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertNotEqual(missing.returncode, 0, 'independent archive history must not be fetched')
        self.assertEqual(self.run_fetch(self.head).returncode, 0, 'reused full checkout must also work')

    def test_changed_remote_main_stops_without_moving_checkout(self):
        self.commit('main.txt', 'fourth', 'new source')
        self.git('-C', str(self.writer), 'push', 'origin', 'main')
        self.assertNotEqual(self.run_fetch(self.head).returncode, 0)
        self.assertEqual(self.git('-C', str(self.checkout), 'rev-parse', 'HEAD'), self.head)


if __name__ == '__main__':
    unittest.main()
