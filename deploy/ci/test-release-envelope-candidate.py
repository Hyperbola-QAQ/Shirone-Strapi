#!/usr/bin/env python3
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('release-envelope-candidate.py')
spec = importlib.util.spec_from_file_location('release_envelope', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SOURCE = 'a' * 40
RECIPIENT = 'sha256:' + 'b' * 64
MANIFEST = 'sha256:' + 'c' * 64


def fake_oci(path):
    return {'archive_digest': 'sha256:' + hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'manifest_digest': MANIFEST}


class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.files = {}
        for n in module.COMPONENTS:
            self.files[n] = self.dir / (n + '.input')
            self.files[n].write_bytes((n + ' fixture').encode())
        self.envelope = self.dir / 'release.tar'
        self.expected = {n: MANIFEST for n in module.COMPONENTS}
        patcher = patch.object(module.oci, 'verify', side_effect=fake_oci)
        patcher.start()
        self.addCleanup(patcher.stop)
        module.build(self.envelope, SOURCE, RECIPIENT, self.files)

    def alter(self, edit):
        with tarfile.open(self.envelope, 'r:') as tar:
            entries = [(m.name, tar.extractfile(m).read()) for m in tar]
        entries = edit(entries)
        with tarfile.open(self.envelope, 'w') as tar:
            for name, data in entries:
                item = tarfile.TarInfo(name)
                if data is None:
                    item.type = tarfile.SYMTYPE
                    item.linkname = '/etc/passwd'
                    tar.addfile(item)
                else:
                    item.size = len(data)
                    tar.addfile(item, io.BytesIO(data))

    def test_valid_new_private_directory(self):
        target = self.dir / 'new'
        module.inspect(self.envelope, SOURCE, RECIPIENT, self.expected, target)
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        for n in module.COMPONENTS:
            self.assertEqual((target / module.FILES[n]).read_bytes(), self.files[n].read_bytes())
            self.assertEqual((target / module.FILES[n]).stat().st_mode & 0o777, 0o600)

    def test_existing_destination_is_not_modified(self):
        target = self.dir / 'existing'
        target.mkdir()
        (target / 'user-data').write_text('unchanged')
        with self.assertRaises(FileExistsError): module.inspect(self.envelope, SOURCE, RECIPIENT, self.expected, target)
        self.assertEqual((target / 'user-data').read_text(), 'unchanged')

    def test_existing_output_is_not_overwritten(self):
        before = self.envelope.read_bytes()
        with self.assertRaises(FileExistsError): module.build(self.envelope, SOURCE, RECIPIENT, self.files)
        self.assertEqual(self.envelope.read_bytes(), before)

    def test_source_replay_rejected_before_writes(self):
        target = self.dir / 'replay'
        with self.assertRaises(ValueError): module.inspect(self.envelope, 'd' * 40, RECIPIENT, self.expected, target)
        self.assertFalse(target.exists())

    def test_wrong_recipient_rejected(self):
        with self.assertRaises(ValueError): module.inspect(self.envelope, SOURCE, 'sha256:' + 'e' * 64, self.expected)

    def test_wrong_manifest_rejected(self):
        expected = dict(self.expected, cms='sha256:' + 'd' * 64)
        with self.assertRaises(ValueError): module.inspect(self.envelope, SOURCE, RECIPIENT, expected)

    def test_replaced_archive_rejected(self):
        self.alter(lambda es: [(n, v[:-1] + b'X' if n == 'cms.oci.tar' else v) for n, v in es])
        with self.assertRaises(ValueError): module.inspect(self.envelope, SOURCE, RECIPIENT, self.expected)

    def test_duplicate_path_rejected(self):
        self.alter(lambda es: es + [es[0]])
        with self.assertRaises(ValueError): module.inspect(self.envelope, SOURCE, RECIPIENT, self.expected)

    def test_extra_or_traversal_path_rejected(self):
        self.alter(lambda es: es + [('../outside', b'data')])
        with self.assertRaises(ValueError): module.inspect(self.envelope, SOURCE, RECIPIENT, self.expected)

    def test_symlink_member_rejected(self):
        self.alter(lambda es: [(n, None if n == 'cms.oci.tar' else v) for n, v in es])
        with self.assertRaises(ValueError): module.inspect(self.envelope, SOURCE, RECIPIENT, self.expected)

    def test_duplicate_metadata_keys_rejected(self):
        self.alter(lambda es: [(n, b'{"version":1,"version":1}' if n == 'release.json' else v) for n, v in es])
        with self.assertRaises(ValueError): module.inspect(self.envelope, SOURCE, RECIPIENT, self.expected)

    def test_oversized_member_rejected(self):
        with patch.object(module, 'MAX_COMPONENT', 2):
            with self.assertRaises(ValueError): module.inspect(self.envelope, SOURCE, RECIPIENT, self.expected)

    def test_symlink_input_rejected(self):
        link = self.dir / 'link'
        os.symlink(self.files['cms'], link)
        with self.assertRaises(OSError): module.build(self.dir / 'other.tar', SOURCE, RECIPIENT, dict(self.files, cms=link))

    def test_outer_symlink_rejected(self):
        link = self.dir / 'link-envelope'
        os.symlink(self.envelope, link)
        with self.assertRaises(OSError): module.inspect(link, SOURCE, RECIPIENT, self.expected)

    def test_missing_authentication_acknowledgement_rejected_by_cli(self):
        args = [sys.executable, str(SCRIPT), 'inspect', str(self.envelope), '--source', SOURCE,
                '--recipient', RECIPIENT]
        for n in module.COMPONENTS:
            args.extend(['--' + n + '-manifest', MANIFEST])
        self.assertNotEqual(subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode, 0)


if __name__ == '__main__':
    unittest.main()
