#!/usr/bin/env python3
"""Test builder selection without contacting any registry or Docker daemon."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('setup-oci-builder-candidate.sh')
DIGEST = 'sha256:97719ccb2ac86fb44fd36f330a6d302f63dddd5326f3ada24f9c05ccac0d9098'


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        stub = root / 'docker'
        stub.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$MOCK_LOG"
case "$*" in
  *docker.m.daocloud.io/moby/buildkit* ) [ "${FAIL_MIRROR:-0}" != 1 ] || exit 1 ;;
esac
case "$*" in 'image inspect '*) printf 'linux/amd64\\n';; esac
''')
        stub.chmod(0o700)
        self.log = root / 'calls'
        self.env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], MOCK_LOG=str(self.log))

    def test_domestic_mirror_first(self):
        run = subprocess.run(['bash', str(SCRIPT), 'shirone-ci-123-1'], env=self.env, capture_output=True)
        self.assertEqual(run.returncode, 0)
        calls = self.log.read_text()
        self.assertIn('docker.m.daocloud.io/moby/buildkit@' + DIGEST, calls)
        self.assertNotIn('docker.io/moby/buildkit@' + DIGEST, calls)
        self.assertIn('--driver docker-container', calls)

    def test_official_fallback_preserves_digest(self):
        self.env['FAIL_MIRROR'] = '1'
        run = subprocess.run(['bash', str(SCRIPT), 'shirone-ci-123-1'], env=self.env, capture_output=True)
        self.assertEqual(run.returncode, 0)
        calls = self.log.read_text()
        self.assertIn('docker.m.daocloud.io/moby/buildkit@' + DIGEST, calls)
        self.assertIn('docker.io/moby/buildkit@' + DIGEST, calls)

    def test_rejects_untrusted_name_before_docker(self):
        run = subprocess.run(['bash', str(SCRIPT), 'invalid'], env=self.env, capture_output=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
