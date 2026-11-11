#!/usr/bin/env python3
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release_lock', Path(__file__).with_name('automatic-release-lock.py'))
release_lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_lock)


class LockTests(unittest.TestCase):
    def test_exclusive_and_replay_remains_blocked_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            with release_lock.ReleaseLock(directory) as lock:
                lock.reserve(42, 'a' * 40)
                with self.assertRaises(BlockingIOError):
                    with release_lock.ReleaseLock(directory):
                        pass
            with release_lock.ReleaseLock(directory) as lock:
                with self.assertRaises(FileExistsError):
                    lock.reserve(42, 'a' * 40)
                lock.reserve(43, 'b' * 40)

    def test_unprotected_directory_and_symlink_lock_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            with self.assertRaises(ValueError):
                with release_lock.ReleaseLock(directory):
                    pass
            os.chmod(directory, 0o700)
            os.symlink('/dev/null', Path(directory) / 'release.lock')
            with self.assertRaises(OSError):
                with release_lock.ReleaseLock(directory):
                    pass

    def test_invalid_run_never_reserves(self):
        with tempfile.TemporaryDirectory() as directory:
            with release_lock.ReleaseLock(directory) as lock:
                for run, source in [(True, 'a' * 40), (1, '../x'), (-1, 'a' * 40)]:
                    with self.assertRaises(ValueError):
                        lock.reserve(run, source)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ['release.lock'])


if __name__ == '__main__':
    unittest.main()
