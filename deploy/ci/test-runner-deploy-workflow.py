#!/usr/bin/env python3
"""Static contract checks; GitHub event execution still needs a real run."""
from pathlib import Path
import unittest
import yaml

HERE = Path(__file__).resolve().parent


class RunnerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.load((HERE / 'shirone-runner-deploy.candidate.yml').read_text(), Loader=yaml.BaseLoader)
        cls.build = yaml.load((HERE / 'hosted-encrypted-release-candidate.yml').read_text(), Loader=yaml.BaseLoader)

    def test_only_expected_workflow_and_main_can_trigger(self):
        self.assertEqual(set(self.doc['on']), {'workflow_run', 'workflow_dispatch'})
        event = self.doc['on']['workflow_run']
        self.assertEqual(event['workflows'], [self.build['name']])
        self.assertEqual(event['types'], ['completed'])
        self.assertEqual(event['branches'], ['main'])
        condition = self.doc['jobs']['deploy']['if']
        for part in ("github.repository == 'Hyperbola-QAQ/Shirone-Strapi'",
                     "github.ref == 'refs/heads/main'",
                     "github.event.workflow_run.conclusion == 'success'",
                     "github.event.workflow_run.head_branch == 'main'",
                     "github.event.workflow_run.head_repository.full_name == 'Hyperbola-QAQ/Shirone-Strapi'",
                     'github.event.workflow_run.head_sha == github.sha',
                     "github.event.workflow_run.event == 'push'",
                     "github.event.workflow_run.event == 'workflow_dispatch'"):
            self.assertIn(part, condition)

    def test_selected_run_and_short_lived_token_reach_existing_guard(self):
        job = self.doc['jobs']['deploy']
        self.assertEqual(job['env']['GH_TOKEN'], '${{ github.token }}')
        self.assertEqual(job['env']['RELEASE_RUN_ID'], "${{ github.event_name == 'workflow_run' && github.event.workflow_run.id || inputs.release_run_id }}")
        self.assertEqual(job['permissions'], {'actions': 'read', 'attestations': 'read', 'contents': 'write'})
        self.assertEqual(job['runs-on'], ['self-hosted', 'linux', 'shirone-deploy-imini'])
        self.assertEqual(job['steps'][0]['with']['persist-credentials'], 'false')
        self.assertEqual(job['steps'][0]['with']['fetch-depth'], '1')
        command = job['steps'][1]['run']
        self.assertIn('[[ "$RELEASE_RUN_ID" =~ ^[1-9][0-9]*$ ]]', command)
        self.assertIn('--run-id "$RELEASE_RUN_ID"', command)
        self.assertIn('gh api repos/Hyperbola-QAQ/Shirone-Strapi/git/ref/heads/main', command)
        self.assertIn('bash deploy/ci/fetch-main-history.sh "$GITHUB_SHA"', command)
        self.assertIn('automatic-release-controller.py --isolated-test-only', command)

    def test_manifest_only_commits_do_not_trigger_a_build(self):
        self.assertEqual(self.build['on']['push']['branches'], ['main'])
        self.assertEqual(self.build['on']['push']['paths-ignore'], ['deploy/k8s/**'])
        self.assertEqual(self.doc['concurrency']['cancel-in-progress'], 'false')

    def test_installed_manual_workflow_uses_the_same_main_only_fetch(self):
        installed = yaml.load((HERE.parent.parent / '.github/workflows/shirone-runner-deploy.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(installed['on']), {'workflow_dispatch'})
        steps = installed['jobs']['deploy']['steps']
        self.assertEqual(steps[0]['with']['fetch-depth'], '1')
        self.assertEqual(steps[0]['with']['persist-credentials'], 'false')
        self.assertIn('bash deploy/ci/fetch-main-history.sh "$GITHUB_SHA"', steps[1]['run'])


if __name__ == '__main__':
    unittest.main()
