#!/usr/bin/env python3
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

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
