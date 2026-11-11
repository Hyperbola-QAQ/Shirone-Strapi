#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release_download', Path(__file__).with_name('automatic-release-download.py'))
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


class DownloadTests(unittest.TestCase):
    def fake(self, args, **kwargs):
        path = Path(args[args.index('--dir') + 1])
        (path / 'metadata.json').write_text('{}')
        (path / 'release.age').write_bytes(b'encrypted fixture')
        return subprocess.CompletedProcess(args, 0)

    def test_downloads_only_exact_run_attempt_to_new_private_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(download.subprocess, 'run', side_effect=self.fake) as run:
            result = download.download(123, 2, directory)
            self.assertEqual(result.name, 'artifact-123-2')
            self.assertEqual({p.name for p in result.iterdir()}, {'metadata.json', 'release.age'})
            self.assertIn('shirone-encrypted-release-123-2', run.call_args.args[0])
            with self.assertRaises(FileExistsError): download.download(123, 2, directory)

    def test_partial_failure_does_not_delete_unrelated_data(self):
        def fail(args, **kwargs):
            (Path(args[args.index('--dir') + 1]) / 'partial').write_text('x')
            return subprocess.CompletedProcess(args, 1)
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / 'keep'
            sentinel.write_text('safe')
            with patch.object(download.subprocess, 'run', side_effect=fail), self.assertRaises(RuntimeError):
                download.download(1, 1, directory)
            self.assertEqual(sentinel.read_text(), 'safe')
            self.assertEqual([p.name for p in Path(directory).iterdir()], ['keep'])

    def test_unexpected_files_are_rejected(self):
        def extra(args, **kwargs):
            self.fake(args, **kwargs)
            (Path(args[args.index('--dir') + 1]) / 'extra').write_text('x')
            return subprocess.CompletedProcess(args, 0)
        with tempfile.TemporaryDirectory() as directory, patch.object(download.subprocess, 'run', side_effect=extra), self.assertRaises(RuntimeError):
            download.download(1, 1, directory)


if __name__ == '__main__':
    unittest.main()
