#!/usr/bin/env python3
"""Check fixed-image mirror fallback without downloading or touching Docker."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('pull-hosted-base.sh')
FAKE = '''#!/usr/bin/env python3
import os,sys
with open(os.environ['TEST_CALLS'],'a') as f: f.write(' '.join(sys.argv[1:])+'\\n')
args=sys.argv[1:]
mirror='daocloud.io/' in args[-1]
mode=os.environ['TEST_MODE']
if args[0]=='pull':
    if mode=='all_fail' or (mirror and mode=='mirror_failure'): sys.exit(1)
elif args[:2]==['image','inspect']:
    if '.Config.User' in args[3]: print('27' if mirror and mode=='bad_user' else '26')
    else: print('linux/arm64' if mirror and mode=='bad_arch' else 'linux/amd64')
else: sys.exit(3)
'''


class BasePullTests(unittest.TestCase):
    def exercise(self, image, mode):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docker = root / 'docker'
            docker.write_text(FAKE)
            docker.chmod(0o700)
            calls = root / 'calls'
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], TEST_MODE=mode, TEST_CALLS=str(calls))
            r = subprocess.run(['bash', str(SCRIPT), image], env=env, capture_output=True, text=True)
            return r, calls.read_text().splitlines() if calls.exists() else []

    def test_mirror_success_uses_pinned_digest_and_platform(self):
        for image in ('node', 'postgres'):
            with self.subTest(image=image):
                r, calls = self.exercise(image, 'ok')
                self.assertEqual(r.returncode, 0)
                self.assertIn('daocloud.io/', r.stdout)
                self.assertTrue(calls[0].startswith('pull --platform linux/amd64 '))
                self.assertIn('@sha256:', r.stdout)
                self.assertEqual(sum(x.startswith('pull ') for x in calls), 1)

    def test_failed_mirror_falls_back_to_exact_official_digest(self):
        for image in ('node', 'postgres'):
            with self.subTest(image=image):
                r, calls = self.exercise(image, 'mirror_failure')
                self.assertEqual(r.returncode, 0)
                pulls = [x.split()[-1] for x in calls if x.startswith('pull ')]
                self.assertEqual(len(pulls), 2)
                self.assertEqual(pulls[0].split('@')[1], pulls[1].split('@')[1])
                self.assertNotIn('daocloud.io/', r.stdout)

    def test_incompatible_platform_or_postgres_user_falls_back(self):
        for image, mode in [('node', 'bad_arch'), ('postgres', 'bad_user')]:
            with self.subTest(image=image):
                r, calls = self.exercise(image, mode)
                self.assertEqual(r.returncode, 0)
                self.assertEqual(sum(x.startswith('pull ') for x in calls), 2)
                self.assertNotIn('daocloud.io/', r.stdout)

    def test_both_fail_or_unknown_image_fail_closed(self):
        r, calls = self.exercise('node', 'all_fail')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(sum(x.startswith('pull ') for x in calls), 2)
        r, calls = self.exercise('other', 'ok')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
