#!/usr/bin/env python3
"""Real isolated Git transaction with simulated registry, Ansible and cluster."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

mod = load('automatic-release-adapter')
tx = load('automatic-release-transaction')
fixture = load('test-provenance-candidate')


class RealGitAdapterTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CandidateTests(methodName='test_valid_contract_and_pinned_gh_flags')
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.source = self.f.deployment
        self.live = {
            'cms': {'uid': 'a' * 8 + '-' + 'a' * 4 + '-' + 'a' * 4 + '-' + 'a' * 4 + '-' + 'a' * 12,
                    'image': fixture.PREFIX + fixture.NEW},
            'ssr': {'uid': 'b' * 8 + '-' + 'b' * 4 + '-' + 'b' * 4 + '-' + 'b' * 4 + '-' + 'b' * 12,
                    'image': fixture.PREFIX.replace('/cms@', '/ssr@') + fixture.OLD},
        }
        self.original = {k: dict(v) for k, v in self.live.items()}
        self.targets = {'cms': 'sha256:' + 'c' * 64, 'ssr': 'sha256:' + 'd' * 64, 'reconciler': 'sha256:' + 'e' * 64}
        self.record = self.f.root / 'previous.json'
        self.record.write_text(json.dumps({'source': self.source, 'components': self.original, 'rollback_compatible': True,
                                           'search_probe': {'query': 'fixture', 'url': '/posts/fixture/'}}))
        self.record.chmod(0o600)
        self.adapter = mod.Adapter(self.f.repo, {'source': self.source, 'signer': self.source, 'workflow_id': 1},
                                   1, self.f.root / 'metadata', self.f.root / 'cipher', self.f.root / 'key', 'recipient', self.record)
        self.adapter._live = lambda name: dict(self.live[name])
        self.adapter.admit = lambda run, source: {'source': source, 'signer': source,
                                                  'ciphertext_digest': 'sha256:' + 'f' * 64, 'manifests': self.targets}
        # Admission preview has its own integration requirements; the fixture
        # below exercises real Git and simulated Ansible promotion/rollback.
        self.adapter.preview = lambda admission, snapshot: None
        self.adapter.verify_all = lambda admission, snapshot: None
        self.calls = []

    def promote(self, component, source, head, signer, uid, old, *args, **kwargs):
        self.calls.append('promote-' + component)
        self.live[component]['image'] = fixture.PREFIX.replace('/cms@', '/' + component + '@') + self.targets[component]
        if component == 'ssr' and getattr(self, 'fail_ssr', False):
            raise TimeoutError('response lost after change')

    def command(self, *args, cwd=None):
        if args[0] == 'ssh' and 'rollout' in args:
            return ''
        if args[0] == 'bash' and args[1].endswith('promote-reviewed.sh'):
            component, uid, new = args[2], args[4], args[6]
            self.calls.append('restore-' + component)
            self.live[component] = {'uid': uid, 'image': new}
            return ''
        raise AssertionError(args)

    def run_transaction(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(mod.promotion, 'promote', side_effect=self.promote), \
             patch.object(mod, 'command', side_effect=self.command):
            tx.execute(self.adapter, directory, 12, self.source)

    def test_actual_manifest_commits_drive_existing_promotion_entrypoint(self):
        self.run_transaction()
        self.assertEqual(self.calls, ['promote-cms', 'promote-ssr'])
        self.assertEqual(self.live['cms']['image'], fixture.PREFIX + self.targets['cms'])
        self.assertEqual(mod.gitops.remote_head(self.f.repo), self.adapter.head)
        self.assertEqual(json.loads(self.record.read_text())['components'], self.live)

    def test_uncertain_second_apply_restores_both_manifests_and_live_images(self):
        self.fail_ssr = True
        with self.assertRaisesRegex(RuntimeError, 'restored and verified'):
            self.run_transaction()
        self.assertEqual(self.live, self.original)
        self.assertEqual(self.calls, ['promote-cms', 'promote-ssr', 'restore-ssr', 'restore-cms'])
        self.assertEqual(mod.gitops.remote_head(self.f.repo), self.adapter.head)
        self.assertEqual(json.loads(self.record.read_text())['components'], self.original)

    def test_lost_push_response_is_reconciled_and_both_components_restored(self):
        real_commit = mod.gitops.commit_manifest
        def commit_then_lose_reply(repo, component, expected, old, new):
            result = real_commit(repo, component, expected, old, new)
            if component == 'ssr' and new.endswith(self.targets['ssr']):
                raise TimeoutError('push succeeded but reply was lost')
            return result
        with patch.object(mod.gitops, 'commit_manifest', side_effect=commit_then_lose_reply):
            with self.assertRaisesRegex(RuntimeError, 'restored and verified'):
                self.run_transaction()
        self.assertEqual(self.live, self.original)
        self.assertEqual(self.calls, ['promote-cms', 'restore-cms'])
        self.assertEqual(mod.gitops.remote_head(self.f.repo), self.adapter.head)
        self.assertEqual(json.loads(self.record.read_text())['components'], self.original)

    def test_unpushed_pending_commit_is_fenced_without_publishing_it(self):
        real_commit = mod.gitops.commit_manifest
        def lose_push(repo, component, expected, old, new):
            if component != 'cms':
                return real_commit(repo, component, expected, old, new)
            path = self.f.repo / 'deploy/k8s/cms.yaml'
            path.write_text(path.read_text().replace(old, new))
            self.f.git('add', 'deploy/k8s/cms.yaml')
            self.f.git('commit', '-qm', 'simulated failed push')
            raise TimeoutError('push status uncertain')
        with patch.object(mod.gitops, 'commit_manifest', side_effect=lose_push):
            with self.assertRaisesRegex(RuntimeError, 'restored and verified'):
                self.run_transaction()
        self.assertNotEqual(self.f.git('rev-parse', 'HEAD'), self.source)
        self.assertEqual(mod.gitops.remote_head(self.f.repo), self.adapter.head)
        self.assertEqual(self.f.git('rev-parse', 'HEAD^{tree}'), self.f.git('rev-parse', f'{self.source}^{{tree}}'))
        self.assertEqual(self.live, self.original)

    def test_lost_fence_reply_recovers_after_verifying_remote(self):
        real_commit = mod.gitops.commit_manifest
        real_git = mod.gitops.git
        def lose_push(repo, component, expected, old, new):
            if component != 'cms': return real_commit(repo, component, expected, old, new)
            path = self.f.repo / 'deploy/k8s/cms.yaml'
            path.write_text(path.read_text().replace(old, new))
            self.f.git('add', 'deploy/k8s/cms.yaml')
            self.f.git('commit', '-qm', 'simulated failed push')
            raise TimeoutError('push status uncertain')
        def lose_fence_reply(repo, *args):
            result = real_git(repo, *args)
            if args and args[0] == 'push' and 'HEAD:refs/heads/main' not in args:
                raise TimeoutError('fence succeeded but reply was lost')
            return result
        with patch.object(mod.gitops, 'commit_manifest', side_effect=lose_push), \
             patch.object(mod.gitops, 'git', side_effect=lose_fence_reply):
            with self.assertRaisesRegex(RuntimeError, 'restored and verified'):
                self.run_transaction()
        self.assertEqual(mod.gitops.remote_head(self.f.repo), self.adapter.head)
        self.assertEqual(self.live, self.original)

    def test_original_push_wins_race_with_fence_and_is_reverted(self):
        real_commit = mod.gitops.commit_manifest
        real_git = mod.gitops.git
        def delay_push(repo, component, expected, old, new):
            if component != 'cms' or not new.endswith(self.targets['cms']):
                return real_commit(repo, component, expected, old, new)
            path = self.f.repo / 'deploy/k8s/cms.yaml'
            path.write_text(path.read_text().replace(old, new))
            self.f.git('add', 'deploy/k8s/cms.yaml')
            self.f.git('commit', '-qm', 'simulated delayed push')
            raise TimeoutError('push status uncertain')
        def race_fence(repo, *args):
            if args and args[0] == 'push' and 'HEAD:refs/heads/main' not in args:
                real_git(repo, 'push', f'--force-with-lease=refs/heads/main:{self.source}',
                         'origin', 'HEAD:refs/heads/main')
                raise TimeoutError('original push won the race')
            return real_git(repo, *args)
        with patch.object(mod.gitops, 'commit_manifest', side_effect=delay_push), \
             patch.object(mod.gitops, 'git', side_effect=race_fence):
            with self.assertRaisesRegex(RuntimeError, 'restored and verified'):
                self.run_transaction()
        self.assertEqual(mod.gitops.remote_head(self.f.repo), self.adapter.head)
        self.assertEqual(self.live, self.original)

    def test_fence_does_not_overwrite_concurrent_unrelated_commit(self):
        real_commit = mod.gitops.commit_manifest
        real_git = mod.gitops.git
        external = []
        def delay_push(repo, component, expected, old, new):
            if component != 'cms': return real_commit(repo, component, expected, old, new)
            path = self.f.repo / 'deploy/k8s/cms.yaml'
            path.write_text(path.read_text().replace(old, new))
            self.f.git('add', 'deploy/k8s/cms.yaml')
            self.f.git('commit', '-qm', 'simulated delayed push')
            raise TimeoutError('push status uncertain')
        def race_fence(repo, *args):
            if args and args[0] == 'push' and 'HEAD:refs/heads/main' not in args:
                tree = self.f.git('rev-parse', f'{self.source}^{{tree}}')
                external.append(self.f.git('commit-tree', tree, '-p', self.source, '-m', 'external change'))
                self.f.git('push', '-q', 'origin', f'{external[-1]}:refs/heads/main')
            return real_git(repo, *args)
        with patch.object(mod.gitops, 'commit_manifest', side_effect=delay_push), \
             patch.object(mod.gitops, 'git', side_effect=race_fence):
            with self.assertRaisesRegex(RuntimeError, 'restoration is unverified'):
                self.run_transaction()
        self.assertEqual(mod.gitops.remote_head(self.f.repo), external[0])
        self.assertNotEqual(self.f.git('rev-parse', 'HEAD'), external[0])
        self.assertEqual(self.live, self.original)

    def test_tampered_pushed_manifest_is_not_adopted(self):
        real_commit = mod.gitops.commit_manifest
        def tamper_after_push(repo, component, expected, old, new):
            real_commit(repo, component, expected, old, new)
            path = self.f.repo / f'deploy/k8s/{component}.yaml'
            path.write_text(path.read_text() + '\n# unrelated change\n')
            self.f.git('add', f'deploy/k8s/{component}.yaml')
            self.f.git('commit', '--amend', '--no-edit', '-q')
            self.f.git('push', '--force', '-q', 'origin', 'main')
            raise TimeoutError('push reply lost with concurrent modification')
        with patch.object(mod.gitops, 'commit_manifest', side_effect=tamper_after_push):
            with self.assertRaisesRegex(RuntimeError, 'restoration is unverified'):
                self.run_transaction()
        self.assertEqual(self.live, self.original)
        self.assertEqual(json.loads(self.record.read_text())['components'], self.original)

    def test_unbound_previous_record_stops_before_git_changes(self):
        data = json.loads(self.record.read_text())
        data['components']['cms']['image'] = fixture.PREFIX + fixture.OLD
        self.record.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.run_transaction()
        self.assertEqual(self.f.git('rev-parse', 'HEAD'), self.source)

    def test_cms_change_stops_before_promotion(self):
        (self.f.repo / 'cms').mkdir()
        (self.f.repo / 'cms/schema.js').write_text('// modified CMS\n')
        self.f.git('add', '.')
        self.f.git('commit', '-qm', 'CMS change')
        self.f.git('push', '-q', 'origin', 'main')
        self.source = self.f.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'CMS changed'):
            self.run_transaction()
        self.assertEqual(self.live, self.original)
        self.assertEqual(self.calls, [])

    def test_base_reconstruction_change_stops_before_promotion(self):
        path = self.f.repo / 'deploy/ci/Dockerfile.node-reconstruction-candidate'
        path.parent.mkdir(parents=True)
        path.write_text('FROM example\n')
        self.f.git('add', '.')
        self.f.git('commit', '-qm', 'base change')
        self.f.git('push', '-q', 'origin', 'main')
        self.source = self.f.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'CMS changed'):
            self.run_transaction()
        self.assertEqual(self.live, self.original)

    def test_public_previous_record_is_rejected(self):
        self.record.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'unprotected'):
            self.run_transaction()
        self.assertEqual(self.live, self.original)

    def test_external_change_before_record_update_is_not_certified(self):
        def change_after_checks(admission, snapshot):
            self.live['cms']['image'] = fixture.PREFIX + 'sha256:' + '9' * 64
        self.adapter.verify_all = change_after_checks
        with self.assertRaisesRegex(RuntimeError, 'restoration is unverified'):
            self.run_transaction()
        self.assertEqual(json.loads(self.record.read_text())['components'], self.original)

    def test_preview_uses_both_reviewed_playbooks_after_verified_envelope(self):
        destination = self.f.repo / 'deploy/ansible'
        destination.mkdir(parents=True)
        ops = Path('/home/hyperbola/Work/ansible-debian-k8s')
        for name in ('cms', 'ssr'):
            shutil.copyfile(ops / f'update_shirone_{name}_image.yml', destination / f'update-{name}-image.yml')
        calls = []
        def command(*args, **kwargs):
            calls.append(args[0])
            return json.dumps({'ansible_host': '192.168.86.11', 'ansible_user': 'hyperbola'}) if args[0] == 'ansible-inventory' else ''
        admission = {'source': self.source, 'signer': self.source, 'ciphertext_digest': 'sha256:' + 'f' * 64,
                     'manifests': self.targets}
        self.adapter.head = self.source
        with patch.object(mod, 'command', side_effect=command), \
             patch.object(mod.promotion.crypto, 'decrypt') as decrypt, \
             patch.object(mod.promotion.crypto, 'recipient_fingerprint', return_value='fingerprint'), \
             patch.object(mod.promotion.env, 'inspect') as inspect:
            mod.Adapter.preview(self.adapter, admission, {'components': self.original})
        self.assertEqual(calls, ['ansible-inventory', 'ansible-playbook', 'ansible-playbook'])
        decrypt.assert_called_once()
        self.assertTrue(inspect.call_args.kwargs['verify_oci'])

    def test_search_probe_requires_expected_published_url(self):
        snapshot = {'search_probe': {'query': 'fixture', 'url': '/posts/fixture/'}}
        responses = ['', '', json.dumps({'hits': [{'url': '/posts/fixture/'}]})]
        with patch.object(mod, 'command', side_effect=responses):
            mod.Adapter.verify_all(self.adapter, {}, snapshot)
        responses = ['', '', json.dumps({'hits': [{'url': '/posts/other/'}]})]
        with patch.object(mod, 'command', side_effect=responses), self.assertRaisesRegex(ValueError, 'search probe'):
            mod.Adapter.verify_all(self.adapter, {}, snapshot)

    def test_preview_rejects_unhealthy_baseline_before_other_work(self):
        self.adapter.head = self.source
        self.adapter.verify_all = lambda admission, snapshot: (_ for _ in ()).throw(ValueError('search unavailable'))
        with patch.object(mod, 'command') as command, self.assertRaisesRegex(ValueError, 'search unavailable'):
            mod.Adapter.preview(self.adapter, {}, {})
        command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
