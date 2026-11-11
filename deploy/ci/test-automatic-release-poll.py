#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('poll', Path(__file__).with_name('automatic-release-poll.py'))
poll = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poll)
HEAD = 'a' * 40
RECIPIENT = 'age1' + 'q' * 58


class PollTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        os.chmod(self.state, 0o700)
        self.recipient = self.state / 'recipient'
        self.recipient.write_text(RECIPIENT + '\n')
        self.commands = []

    def git(self, checkout, *args):
        self.commands.append(args)
        if args[:2] == ('remote', 'get-url'): return poll.ORIGIN
        if args[0] == 'symbolic-ref': return 'main'
        if args[0] == 'status': return ''
        if args[0] == 'rev-parse': return HEAD
        return ''

    def response(self):
        return {'workflow_runs': [{'id': 42, 'head_sha': HEAD, 'head_branch': 'main',
                                  'event': 'push', 'status': 'completed', 'conclusion': 'success'}]}

    def invoke(self):
        return poll.poll('/isolated/repo', self.state, '/isolated/key', self.recipient, '/isolated/record')

    def test_matching_release_runs_and_removes_hold_after_success(self):
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate, 'gh', return_value=self.response()), \
             patch.object(poll.controller, 'run') as run:
            self.assertEqual(self.invoke(), 'completed')
            run.assert_called_once()
            self.assertFalse((self.state / 'poll-hold').exists())
            self.assertIn(('merge', '--ff-only', 'FETCH_HEAD'), self.commands)

    def test_local_git_uses_only_dedicated_key_and_strict_host_checking(self):
        with patch.dict(poll.os.environ, {'SHIRONE_CONTROLLER_LOCAL': '1',
                                          'GIT_SSH_COMMAND': 'ssh -o StrictHostKeyChecking=no'}), \
             patch.object(poll.subprocess, 'run', return_value=SimpleNamespace(stdout='')) as run:
            poll.git('/isolated/repo', 'fetch', 'origin')
            ssh = run.call_args.kwargs['env']['GIT_SSH_COMMAND']
            self.assertIn('/home/hyperbola/.local/state/shirone-release/identity/deploy-key', ssh)
            self.assertIn('IdentitiesOnly=yes', ssh)
            self.assertIn('StrictHostKeyChecking=yes', ssh)
            self.assertNotIn('StrictHostKeyChecking=no', ssh)

    def test_interruption_holds_subsequent_attempt(self):
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate, 'gh', return_value=self.response()), \
             patch.object(poll.controller, 'run', side_effect=RuntimeError('failed')) as run:
            with self.assertRaises(RuntimeError): self.invoke()
            with self.assertRaisesRegex(ValueError, 'reconciliation'): self.invoke()
            run.assert_called_once()

    def test_download_failure_allows_two_bounded_retries_before_hold(self):
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate, 'gh', return_value=self.response()), \
             patch.object(poll.controller, 'run', side_effect=poll.controller.DownloadUnavailable('offline')) as run:
            for attempt in range(1, 4):
                with self.assertRaises(poll.controller.DownloadUnavailable): self.invoke()
                self.assertEqual((self.state / 'poll-hold').exists(), attempt == 3)
            with self.assertRaisesRegex(ValueError, 'reconciliation'): self.invoke()
            self.assertEqual(run.call_count, 3)

    def test_download_error_does_not_clear_hold_if_transaction_reserved(self):
        def reserved(*args):
            (self.state / f'source-{HEAD}.json').write_text('{}')
            raise poll.controller.DownloadUnavailable('offline')
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate, 'gh', return_value=self.response()), \
             patch.object(poll.controller, 'run', side_effect=reserved):
            with self.assertRaises(poll.controller.DownloadUnavailable): self.invoke()
            self.assertTrue((self.state / 'poll-hold').exists())

    def test_real_downloader_network_failure_is_bounded_before_transaction(self):
        run = {'id': 42, 'head_sha': HEAD, 'workflow_id': 3, 'run_attempt': 1,
               'head_branch': 'main', 'status': 'completed', 'conclusion': 'success', 'event': 'push'}
        artifact = {'id': 9, 'name': 'shirone-encrypted-release-42-1', 'expired': False,
                    'size_in_bytes': 8, 'workflow_run': {'id': 42}}
        listing = {'total_count': 1, 'artifacts': [artifact]}
        def gh(endpoint):
            if endpoint.endswith('/runs/42/artifacts?per_page=100'): return listing
            if endpoint.endswith('/runs/42'): return run
            return self.response()
        opener = SimpleNamespace(open=Mock(side_effect=urllib.error.URLError('offline')))
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate, 'gh', side_effect=gh), \
             patch.object(poll.controller.download, 'artifact_info', return_value=artifact), \
             patch.object(poll.controller.download, 'artifact_location', return_value='https://example.invalid/file'), \
             patch.object(poll.controller.download.urllib.request, 'build_opener', return_value=opener), \
             patch.object(poll.controller.download.time, 'sleep'):
            for attempt in range(1, 4):
                with self.assertRaises(poll.controller.DownloadUnavailable): self.invoke()
                self.assertEqual((self.state / 'poll-hold').exists(), attempt == 3)

    def test_unclassified_error_keeps_hold(self):
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate, 'gh', return_value=self.response()), \
             patch.object(poll.controller, 'run', side_effect=ValueError('invalid artifact')):
            with self.assertRaises(ValueError): self.invoke()
            self.assertTrue((self.state / 'poll-hold').exists())

    def test_real_api_503_is_bounded_before_transaction(self):
        def response(args, **kwargs):
            if 'workflows/shirone-release.yml/runs?' in args[-1]:
                body = json.dumps(self.response()).encode()
                return subprocess.CompletedProcess(args, 0, b'HTTP/2.0 200 OK\r\nX: y\r\n\r\n' + body, b'')
            return subprocess.CompletedProcess(args, 1, b'HTTP/2.0 503 Unavailable\r\n\r\n', b'')
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate.api.subprocess, 'run', side_effect=response):
            for attempt in range(1, 4):
                with self.assertRaises(poll.controller.gate.ApiUnavailable): self.invoke()
                self.assertEqual((self.state / 'poll-hold').exists(), attempt == 3)

    def test_actual_adapter_admission_api_failure_uses_same_bounded_retry(self):
        run = {'id': 42, 'head_sha': HEAD, 'workflow_id': 3, 'run_attempt': 1,
               'head_branch': 'main', 'status': 'completed', 'conclusion': 'success', 'event': 'push'}
        artifact = {'id': 9, 'name': 'shirone-encrypted-release-42-1', 'expired': False}
        def gh(endpoint):
            if endpoint.endswith('/runs/42/artifacts?per_page=100'):
                return {'total_count': 1, 'artifacts': [artifact]}
            if endpoint.endswith('/runs/42'): return run
            return self.response()
        def fake_download(run_id, attempt, parent, artifact_id):
            directory = Path(parent) / 'artifact'
            directory.mkdir()
            (directory / 'metadata.json').write_text('{}')
            (directory / 'release.age').write_bytes(b'ciphertext')
            return directory
        def adapter_git(repo, *args):
            return HEAD if args[0] == 'rev-parse' else ''
        failure = subprocess.CompletedProcess(['gh'], 1, b'HTTP/2.0 503 Unavailable\r\n\r\n', b'')
        adapter = poll.controller.adapter_module
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate, 'gh', side_effect=gh), \
             patch.object(poll.controller.download, 'download', side_effect=fake_download), \
             patch.object(adapter.gitops, 'git', side_effect=adapter_git), \
             patch.object(adapter.gate.api.subprocess, 'run', return_value=failure):
            for attempt in range(1, 4):
                with self.assertRaises(poll.controller.gate.ApiUnavailable): self.invoke()
                self.assertEqual((self.state / 'poll-hold').exists(), attempt == 3)

    def test_real_downloader_early_eof_is_bounded_before_transaction(self):
        run = {'id': 42, 'head_sha': HEAD, 'workflow_id': 3, 'run_attempt': 1,
               'head_branch': 'main', 'status': 'completed', 'conclusion': 'success', 'event': 'push'}
        artifact = {'id': 9, 'name': 'shirone-encrypted-release-42-1', 'expired': False,
                    'size_in_bytes': 8, 'workflow_run': {'id': 42}}
        def gh(endpoint):
            if endpoint.endswith('/runs/42/artifacts?per_page=100'):
                return {'total_count': 1, 'artifacts': [artifact]}
            if endpoint.endswith('/runs/42'): return run
            return self.response()
        class Response:
            status = 206
            headers = {'Content-Range': 'bytes 0-7/8'}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, amount): return b''
        opener = SimpleNamespace(open=Mock(return_value=Response()))
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate, 'gh', side_effect=gh), \
             patch.object(poll.controller.download, 'artifact_info', return_value=artifact), \
             patch.object(poll.controller.download, 'artifact_location', return_value='https://example.invalid/file'), \
             patch.object(poll.controller.download.urllib.request, 'build_opener', return_value=opener), \
             patch.object(poll.controller.download.time, 'sleep'):
            with self.assertRaises(poll.controller.DownloadUnavailable): self.invoke()
            self.assertFalse((self.state / 'poll-hold').exists())

    def test_different_source_or_ineligible_event_does_not_run(self):
        for change in ({'head_sha': 'b' * 40}, {'event': 'pull_request'}, {'conclusion': 'failure'}):
            response = self.response()
            response['workflow_runs'][0].update(change)
            self.assertIsNone(poll.eligible_run(response, HEAD))

    def test_dirty_checkout_stops_before_fetch(self):
        def dirty(checkout, *args):
            return ' M file' if args[0] == 'status' else self.git(checkout, *args)
        with patch.object(poll, 'git', side_effect=dirty), \
             patch.object(poll.controller, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'not clean'): self.invoke()
            run.assert_not_called()
            self.assertFalse(any(x[0] == 'fetch' for x in self.commands))

    def test_a_completed_source_is_not_replayed_with_a_new_run(self):
        (self.state / f'source-{HEAD}.json').write_text('{"run_id": 12}')
        (self.state / 'run-12-complete.json').write_text('{}')
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate, 'gh', return_value=self.response()), \
             patch.object(poll.controller, 'run') as run:
            self.assertEqual(self.invoke(), 'already completed')
            run.assert_not_called()

    def test_incomplete_reservation_is_not_replayed(self):
        (self.state / f'source-{HEAD}.json').write_text('{"run_id": 12}')
        with patch.object(poll, 'git', side_effect=self.git), \
             patch.object(poll.controller.gate, 'gh', return_value=self.response()), \
             patch.object(poll.controller, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'reconciliation'): self.invoke()
            run.assert_not_called()


class RealGitPolling(unittest.TestCase):
    def test_dedicated_checkout_fast_forwards_and_uses_exact_new_head(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            remote, repo, writer, state = (root / x for x in ('remote.git', 'repo', 'writer', 'state'))
            state.mkdir(mode=0o700)
            def command(*args, cwd=None):
                return subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True).stdout.strip()
            command('git', 'init', '--bare', '--initial-branch=main', str(remote))
            command('git', 'clone', str(remote), str(writer))
            (writer / 'content').write_text('old')
            command('git', 'add', 'content', cwd=writer)
            command('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-m', 'initial', cwd=writer)
            command('git', 'push', 'origin', 'main', cwd=writer)
            command('git', 'clone', str(remote), str(repo))
            (writer / 'content').write_text('new')
            command('git', 'add', 'content', cwd=writer)
            command('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-m', 'next', cwd=writer)
            command('git', 'push', 'origin', 'main', cwd=writer)
            head = command('git', 'rev-parse', 'HEAD', cwd=writer)
            recipient = root / 'recipient'
            recipient.write_text(RECIPIENT + '\n')
            response = {'workflow_runs': [{'id': 42, 'head_sha': head, 'head_branch': 'main',
                                          'event': 'push', 'status': 'completed', 'conclusion': 'success'}]}
            with patch.object(poll, 'ORIGIN', str(remote)), \
                 patch.object(poll.controller.gate, 'gh', return_value=response), \
                 patch.object(poll.controller, 'run') as run:
                self.assertEqual(poll.poll(repo, state, root / 'key', recipient, root / 'record'),
                                 'checkout updated; retry next poll')
                run.assert_not_called()
                self.assertEqual(command('git', 'rev-parse', 'HEAD', cwd=repo), head)
                self.assertEqual(poll.poll(repo, state, root / 'key', recipient, root / 'record'), 'completed')
                self.assertEqual(run.call_args.args[0], 42)


if __name__ == '__main__':
    unittest.main()
