#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

controller = load('automatic-release-controller')
fixture = load('test-automatic-release-transaction')


class ControllerTests(unittest.TestCase):
    def test_run_downloads_exact_artifact_and_holds_transaction_lock(self):
        source = 'a' * 40
        info = {'id': 7, 'head_sha': source, 'workflow_id': 3, 'run_attempt': 2,
                'head_branch': 'main', 'status': 'completed', 'conclusion': 'success', 'event': 'push'}
        artifacts = {'total_count': 1, 'artifacts': [{'name': 'shirone-encrypted-release-7-2', 'id': 9, 'expired': False}]}
        adapter = fixture.Adapter()
        def fake_download(run, attempt, parent, artifact_id):
            self.assertEqual((run, attempt, artifact_id), (7, 2, 9))
            path = Path(parent) / 'artifact'; path.mkdir()
            return path
        with tempfile.TemporaryDirectory() as state, \
             patch.object(controller.gate, 'gh', side_effect=[info, artifacts]), \
             patch.object(controller.download, 'download', side_effect=fake_download), \
             patch.object(controller.adapter_module, 'Adapter', return_value=adapter) as create:
            controller.run(7, '/isolated/repo', state, '/isolated/key', 'recipient', '/isolated/record')
            self.assertEqual(create.call_args.args[2], 9)
            self.assertEqual(adapter.calls[-1], 'finalize')
            self.assertTrue((Path(state) / 'run-7-complete.json').exists())
            self.assertFalse(any(p.name.startswith('shirone-controller-download-') for p in Path(state).iterdir()))

    def test_ineligible_run_never_downloads(self):
        with patch.object(controller.gate, 'gh', return_value={'id': 7, 'head_branch': 'elsewhere'}), \
             patch.object(controller.download, 'download') as retrieve, self.assertRaises(ValueError):
            controller.run(7, '/repo', '/state', '/key', 'recipient', '/record')
        retrieve.assert_not_called()

    def test_download_failure_is_reported_before_transaction(self):
        source = 'a' * 40
        info = {'id': 7, 'head_sha': source, 'workflow_id': 3, 'run_attempt': 1,
                'head_branch': 'main', 'status': 'completed', 'conclusion': 'success', 'event': 'push'}
        artifacts = {'total_count': 1, 'artifacts': [{'name': 'shirone-encrypted-release-7-1', 'id': 9, 'expired': False}]}
        with tempfile.TemporaryDirectory() as state, \
             patch.object(controller.gate, 'gh', side_effect=[info, artifacts]), \
             patch.object(controller.download, 'download', side_effect=controller.download.TransportUnavailable('offline')), \
             patch.object(controller.transaction, 'execute') as execute:
            with self.assertRaises(controller.DownloadUnavailable):
                controller.run(7, '/repo', state, '/key', 'recipient', '/record')
            execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
