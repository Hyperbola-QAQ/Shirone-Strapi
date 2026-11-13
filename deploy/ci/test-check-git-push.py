#!/usr/bin/env python3
"""Verify push preview against an isolated real Git repository."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('push_preview', HERE / 'check-git-push.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class PushPreviewTests(unittest.TestCase):
    def test_real_dry_run_does_not_change_any_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args, cwd=root):
                return subprocess.run(['git', *args], cwd=cwd, check=True, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()
            git('init', '--bare', 'remote.git')
            git('init', '-b', 'main', 'work')
            work = root / 'work'
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                'commit', '--allow-empty', '-m', 'initial', cwd=work)
            git('remote', 'add', 'origin', str(root / 'remote.git'), cwd=work)
            git('push', 'origin', 'main', cwd=work)
            before_local = git('show-ref', cwd=work)
            before_remote = git('--git-dir', str(root / 'remote.git'), 'show-ref')
            previous = Path.cwd()
            try:
                os.chdir(work)
                preview.check()
            finally:
                os.chdir(previous)
            self.assertEqual(git('show-ref', cwd=work), before_local)
            self.assertEqual(git('--git-dir', str(root / 'remote.git'), 'show-ref'), before_remote)
            self.assertEqual(git('status', '--porcelain', cwd=work), '')

    def test_push_failure_is_not_reported_as_success(self):
        outputs = iter(['a' * 40, 'main', '', 'a' * 40 + '\trefs/heads/main', 'b' * 40, 'c' * 40])
        def command(*args, **kwargs):
            if args[0] == 'push':
                raise subprocess.CalledProcessError(128, 'git')
            return next(outputs)
        with patch.object(preview, 'command', side_effect=command):
            with self.assertRaises(subprocess.CalledProcessError):
                preview.check()


if __name__ == '__main__':
    unittest.main()
