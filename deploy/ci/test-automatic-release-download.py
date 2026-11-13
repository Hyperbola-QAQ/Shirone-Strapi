#!/usr/bin/env python3
import importlib.util
import io
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('release_download', Path(__file__).with_name('automatic-release-download.py'))
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


def archive(extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('metadata.json', '{}')
        z.writestr('release.age', b'encrypted fixture')
        if extra:
            z.writestr(extra, 'unexpected')
    return output.getvalue()


class DownloadTests(unittest.TestCase):
    def run_fixture(self, directory, data, *, artifact=None, broken=False):
        details = {'id': 44, 'name': 'shirone-encrypted-release-123-2', 'expired': False,
                   'workflow_run': {'id': 123}, 'size_in_bytes': len(data)}
        if artifact:
            details.update(artifact)
        def fetch(url, start, end, total, path, **kwargs):
            if broken:
                raise ValueError('test range failure')
            self.assertEqual((url, total), ('https://example.invalid/private', len(data)))
            path.write_bytes(data[start:end + 1])
        with patch.object(download, 'artifact_info', return_value=details), \
             patch.object(download, 'artifact_location', return_value='https://example.invalid/private'), \
             patch.object(download, 'fetch_range', side_effect=fetch), patch.object(download, 'CHUNK', 12):
            return download.download(123, 2, directory, 44)

    def test_job_credential_is_opaque_and_never_calls_gh(self):
        token = 'short-lived.token-with-unusual-format'
        with patch.dict(os.environ, {'GH_TOKEN': token}), patch.object(download.subprocess, 'run') as gh:
            self.assertEqual(download.github_credential(), token)
            gh.assert_not_called()

    def test_opaque_job_credential_is_used_for_artifact_request(self):
        token = 'opaque.token-with-dashes'
        class Opener:
            def open(self, request, timeout):
                self_test.assertEqual(request.get_header('Authorization'), 'Bearer ' + token)
                raise download.urllib.error.HTTPError(request.full_url, 302, '',
                    {'Location': 'https://example.invalid/private'}, None)
        self_test = self
        with patch.dict(os.environ, {'GH_TOKEN': token}), \
             patch.object(download.subprocess, 'run') as gh, \
             patch.object(download.urllib.request, 'build_opener', return_value=Opener()):
            self.assertEqual(download.artifact_location(44), 'https://example.invalid/private')
            gh.assert_not_called()

    def test_invalid_job_credential_fails_closed_without_gh_fallback(self):
        for token in ('', 'bad\nheader', 'white space', 'x' * 8193):
            with self.subTest(token=repr(token[:12])), patch.dict(os.environ, {'GH_TOKEN': token}), \
                 patch.object(download.subprocess, 'run') as gh:
                with self.assertRaises(ValueError):
                    download.github_credential()
                gh.assert_not_called()

    def test_local_gh_fallback_ignores_banner_and_rejects_ambiguous_output(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(download.subprocess, 'run') as gh:
            gh.return_value = subprocess.CompletedProcess([], 0, stdout='banner\nghp_abc123\n')
            self.assertEqual(download.github_credential(), 'ghp_abc123')
            gh.return_value = subprocess.CompletedProcess([], 0, stdout='ghp_abc123\nghs_def456\n')
            with self.assertRaises(ValueError):
                download.github_credential()

    def test_exact_artifact_downloads_and_unpacks_all_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_fixture(directory, archive())
            self.assertEqual(result.name, 'artifact-123-2')
            self.assertEqual((result / 'release.age').read_bytes(), b'encrypted fixture')
            self.assertEqual((result / 'metadata.json').read_text(), '{}')
            with self.assertRaises(FileExistsError):
                self.run_fixture(directory, archive())

    def test_failed_range_cleans_only_owned_temporary_data(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / 'keep'
            sentinel.write_text('safe')
            with self.assertRaises(ValueError):
                self.run_fixture(directory, archive(), broken=True)
            self.assertEqual(sentinel.read_text(), 'safe')
            self.assertEqual([p.name for p in Path(directory).iterdir()], ['keep'])

    def test_rejects_wrong_run_or_unexpected_zip_members(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self.run_fixture(directory, archive(), artifact={'workflow_run': {'id': 456}})
            with self.assertRaises(ValueError):
                self.run_fixture(directory, archive('../extra'))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_expired_range_refreshes_once_then_succeeds(self):
        class Response:
            status = 206
            headers = {'Content-Range': 'bytes 0-2/3'}
            def __init__(self): self.data = io.BytesIO(b'abc')
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, amount): return self.data.read(amount)
        class Opener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise download.urllib.error.HTTPError(request.full_url, 403, '', {}, None)
                self_test.assertEqual(request.full_url, 'https://example.invalid/refreshed')
                return Response()
        self_test = self
        opener = Opener()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(download.urllib.request, 'build_opener', return_value=opener):
            path = Path(directory) / 'part'
            with patch.object(download.urllib.request, 'urlopen') as unused:
                download.fetch_range('https://example.invalid/expired', 0, 2, 3, path,
                                     refresh_location=lambda: 'https://example.invalid/refreshed')
                unused.assert_not_called()
            self.assertEqual(path.read_bytes(), b'abc')
            self.assertEqual(opener.calls, 2)

    def test_download_refreshes_expired_url_and_validates_whole_zip(self):
        data = archive()
        details = {'id': 44, 'name': 'shirone-encrypted-release-123-2', 'expired': False,
                   'workflow_run': {'id': 123}, 'size_in_bytes': len(data)}
        class Response:
            status = 206
            headers = {'Content-Range': f'bytes 0-{len(data)-1}/{len(data)}'}
            def __init__(self): self.body = io.BytesIO(data)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return self.body.read(n)
        class Opener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise download.urllib.error.HTTPError(request.full_url, 403, '', {}, None)
                self_test.assertEqual(request.full_url, 'https://example.invalid/new')
                return Response()
        self_test = self
        opener = Opener()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(download, 'artifact_info', return_value=details), \
             patch.object(download, 'artifact_location', side_effect=['https://example.invalid/old', 'https://example.invalid/new']) as locations, \
             patch.object(download.urllib.request, 'build_opener', return_value=opener):
            result = download.download(123, 2, directory, 44)
            self.assertEqual((result / 'release.age').read_bytes(), b'encrypted fixture')
            self.assertEqual(locations.call_count, 2)

    def test_persistent_forbidden_stays_fail_closed(self):
        class Opener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                raise download.urllib.error.HTTPError(request.full_url, 403, '', {}, None)
        opener = Opener()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(download.urllib.request, 'build_opener', return_value=opener):
            path = Path(directory) / 'part'
            with self.assertRaisesRegex(ValueError, 'artifact range request rejected'):
                download.fetch_range('https://example.invalid/expired', 0, 2, 3, path,
                                     refresh_location=lambda: 'https://example.invalid/refreshed')
            self.assertEqual(opener.calls, 3)
            self.assertFalse(path.exists())

    def test_rejects_truncated_or_wrong_range_and_cleans_partial(self):
        class Response:
            status = 206
            headers = {'Content-Range': 'bytes 0-1/3'}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, amount): return b''
        class Opener:
            def open(self, *args, **kwargs): return Response()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(download.urllib.request, 'build_opener', return_value=Opener()), \
             patch.object(download.time, 'sleep'):
            path = Path(directory) / 'part'
            with self.assertRaises(ValueError):
                download.fetch_range('https://example.invalid', 0, 2, 3, path)
            self.assertFalse(path.exists())
            Response.headers['Content-Range'] = 'bytes 0-2/3'
            with self.assertRaises(download.TransportUnavailable):
                download.fetch_range('https://example.invalid', 0, 2, 3, path)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
