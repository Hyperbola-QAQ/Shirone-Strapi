import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('registry_copy', Path(__file__).with_name('runner-registry-copy.py'))
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


class RestrictedCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'shirone-release-123'
        self.directory.mkdir(mode=0o700)
        self.archive = self.directory / 'cms.oci.tar'
        self.archive.write_bytes(b'synthetic OCI archive')
        self.archive.chmod(0o600)
        self.archive_digest = 'sha256:' + hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.raw_manifest = b'{"schemaVersion":2}'
        self.manifest_digest = 'sha256:' + hashlib.sha256(self.raw_manifest).hexdigest()
        self.auth = base64.b64encode(b'ca-image-maintenance:SYNTHETIC_SECRET').decode()
        self.calls = []

    def skopeo(self, args, **kwargs):
        self.calls.append(args)
        serial = json.dumps({'args': args, 'env': kwargs['env']})
        self.assertNotIn('SYNTHETIC_SECRET', serial)
        self.assertNotIn(self.auth, serial)
        authfd = kwargs['pass_fds'][0]
        self.assertEqual(json.loads(os.pread(authfd, 4096, 0))['auths'][registry.REGISTRY]['auth'], self.auth)
        if args[1] == 'copy':
            self.assertTrue(args[-1].startswith('docker://' + registry.REGISTRY + '/shirone/cms:candidate-123-'))
            self.assertEqual(hashlib.sha256(Path(args[-2].removeprefix('oci-archive:')).read_bytes()).hexdigest(),
                             self.archive_digest.removeprefix('sha256:'))
            return subprocess.CompletedProcess(args, 0, b'')
        return subprocess.CompletedProcess(args, 0, self.raw_manifest)

    def test_copy_keeps_synthetic_registry_credential_only_in_memfd(self):
        with patch.object(registry, 'RUNNER_TEMP', str(self.root)), \
             patch.object(registry, 'read_authorization', return_value=self.auth), \
             patch.object(registry.subprocess, 'run', side_effect=self.skopeo):
            registry.copy(123, 'cms', self.archive_digest, self.manifest_digest, os.getuid())
        self.assertEqual([args[1] for args in self.calls], ['copy', 'inspect', 'inspect'])
        self.assertEqual([p.name for p in self.directory.iterdir()], ['cms.oci.tar'])

    def test_untrusted_tmpdir_is_not_used(self):
        with patch.dict(os.environ, {'TMPDIR': str(self.root), 'TEMP': str(self.root), 'TMP': str(self.root)}), \
             patch.object(registry, 'RUNNER_TEMP', str(self.root)), \
             patch.object(registry, 'read_authorization', return_value=self.auth), \
             patch.object(registry.subprocess, 'run', side_effect=self.skopeo), \
             patch.object(registry.tempfile, 'TemporaryDirectory', wraps=registry.tempfile.TemporaryDirectory) as temporary:
            registry.copy(123, 'cms', self.archive_digest, self.manifest_digest, os.getuid())
        self.assertEqual(temporary.call_args.kwargs['dir'], '/tmp')

    def test_archive_mismatch_never_reads_registry_credential(self):
        with patch.object(registry, 'RUNNER_TEMP', str(self.root)), \
             patch.object(registry, 'read_authorization') as authorization:
            with self.assertRaises(ValueError):
                registry.copy(123, 'cms', 'sha256:' + '0' * 64, self.manifest_digest, os.getuid())
            authorization.assert_not_called()

    def test_root_only_containerd_authorization_parses_without_printing(self):
        fixture = self.root / 'hosts.toml'
        fixture.write_text('[host."https://' + registry.REGISTRY + '".header]\n'
                           'authorization = ["Basic ' + self.auth + '"]\n')
        fixture.chmod(0o600)
        raw_stat = fixture.stat()
        protected = SimpleNamespace(st_mode=raw_stat.st_mode, st_uid=0, st_size=raw_stat.st_size)
        with patch.object(registry, 'HOSTS', str(fixture)), patch.object(registry.os, 'fstat', return_value=protected):
            self.assertEqual(registry.read_authorization(), self.auth)

    def test_refuses_symlink_and_unapproved_component(self):
        self.archive.unlink()
        self.archive.symlink_to('/etc/passwd')
        with patch.object(registry, 'RUNNER_TEMP', str(self.root)), \
             patch.object(registry, 'read_authorization') as authorization:
            with self.assertRaises(OSError):
                registry.copy(123, 'cms', self.archive_digest, self.manifest_digest, os.getuid())
            with self.assertRaises(ValueError):
                registry.copy(123, 'other', self.archive_digest, self.manifest_digest, os.getuid())
            authorization.assert_not_called()


if __name__ == '__main__':
    unittest.main()
