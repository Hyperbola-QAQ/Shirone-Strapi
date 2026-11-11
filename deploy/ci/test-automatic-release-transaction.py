#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release_transaction', Path(__file__).with_name('automatic-release-transaction.py'))
transaction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transaction)


class Adapter:
    def __init__(self, fail=None, rollback_safe=True):
        self.calls, self.fail, self.rollback_safe = [], fail, rollback_safe

    def call(self, value):
        self.calls.append(value)
        if self.fail == value:
            raise ValueError('simulated uncertain failure')

    def admit(self, run, source):
        self.call('admit')
        return {'run': run, 'source': source}

    def snapshot(self, admission):
        self.call('snapshot')
        return {'rollback_safe': self.rollback_safe, 'old': 'fixture'}

    def preview(self, admission, snapshot): self.call('preview')
    def promote(self, name, admission, snapshot): self.call('promote-' + name)
    def verify(self, name, admission, snapshot): self.call('verify-' + name)
    def verify_all(self, admission, snapshot): self.call('verify-all')
    def restore(self, admission, snapshot): self.call('restore')
    def verify_restored(self, admission, snapshot): self.call('verify-restored')


class TransactionTests(unittest.TestCase):
    def execute(self, adapter, directory):
        return transaction.execute(adapter, directory, 12, 'a' * 40)

    def test_success_and_no_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = Adapter()
            self.execute(adapter, directory)
            self.assertEqual(adapter.calls, ['admit', 'snapshot', 'preview', 'promote-cms', 'verify-cms',
                                             'promote-ssr', 'verify-ssr', 'verify-all'])
            self.assertTrue((Path(directory) / 'run-12-complete.json').is_file())
            with self.assertRaises(FileExistsError): self.execute(Adapter(), directory)

    def test_failed_or_uncertain_promotions_restore_and_remain_reserved(self):
        for failure in ('promote-cms', 'verify-cms', 'promote-ssr', 'verify-ssr', 'verify-all'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                adapter = Adapter(failure)
                with self.assertRaisesRegex(RuntimeError, 'restored and verified'):
                    self.execute(adapter, directory)
                self.assertEqual(adapter.calls[-2:], ['restore', 'verify-restored'])
                self.assertTrue((Path(directory) / 'run-12-restored.json').is_file())
                with self.assertRaises(FileExistsError): self.execute(Adapter(), directory)

    def test_incompatible_schema_or_preview_failure_has_no_mutation(self):
        for adapter in (Adapter(rollback_safe=False), Adapter('preview')):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError): self.execute(adapter, directory)
                self.assertNotIn('promote-cms', adapter.calls)

    def test_restoration_failure_is_explicit_and_not_retried(self):
        class Broken(Adapter):
            def restore(self, admission, snapshot): raise OSError('unavailable')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'restoration is unverified'):
                self.execute(Broken('promote-cms'), directory)
            self.assertTrue((Path(directory) / 'run-12-restore-intent.json').is_file())
            self.assertFalse((Path(directory) / 'run-12-restored.json').exists())
            with self.assertRaises(FileExistsError): self.execute(Adapter(), directory)


if __name__ == '__main__':
    unittest.main()
