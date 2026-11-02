#!/usr/bin/env python3
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ca_overlay', Path(__file__).with_name('prepare-ca-overlay-candidate.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BAD = b'-----BEGIN CERTIFICATE-----BAD-----END CERTIFICATE-----'
GOOD = b'-----BEGIN CERTIFICATE-----GOOD-----END CERTIFICATE-----'


class Context:
    def load_verify_locations(self, cadata):
        assert 'GOOD' in cadata


class OverlayTests(unittest.TestCase):
    def make_export(self, root, malicious=False, denied=True):
        archive = root / 'export.tar'
        with tarfile.open(archive, 'w') as tar:
            name = '../etc/ssl/certs/evil.crt' if malicious else 'etc/ssl/certs/bundle.crt'
            info = tarfile.TarInfo(name)
            data = GOOD + b'\n' + (BAD if denied else GOOD) + b'\n'
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
            single = tarfile.TarInfo('usr/share/ca-certificates/mozilla/bad.crt')
            single.size = len(BAD if denied else GOOD)
            tar.addfile(single, io.BytesIO(BAD if denied else GOOD))
            alias = tarfile.TarInfo('etc/ssl/certs/bad.pem')
            alias.type, alias.linkname = tarfile.SYMTYPE, '/usr/share/ca-certificates/mozilla/bad.crt'
            tar.addfile(alias)
        return archive

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fp = patch.object(module, 'fp', side_effect=lambda b: next(iter(module.DENIED)) if b == BAD else 'good')
        self.fp.start()
        self.addCleanup(self.fp.stop)
        self.ctx = patch.object(module.ssl, 'SSLContext', return_value=Context())
        self.ctx.start()
        self.addCleanup(self.ctx.stop)

    def test_overlay_removes_only_bad_and_its_alias(self):
        out = self.root / 'result'
        module.generate(self.make_export(self.root), out)
        report = json.loads((out / 'report.json').read_text())
        self.assertEqual(report['changed'], ['etc/ssl/certs/bundle.crt'])
        self.assertEqual(report['denied_occurrences_removed'], 2)
        self.assertEqual(len(report['deleted']), 2)
        with tarfile.open(out / 'ca-files.tar') as tar:
            self.assertEqual(tar.extractfile(report['changed'][0]).read(), GOOD + b'\n\n')

    def test_existing_output_is_never_overwritten(self):
        with self.assertRaises(FileExistsError):
            module.generate(self.make_export(self.root), self.root)

    def test_unsafe_path_is_rejected(self):
        with self.assertRaises(ValueError):
            module.generate(self.make_export(self.root, malicious=True), self.root / 'result')

    def test_no_matching_certificate_is_rejected(self):
        with self.assertRaises(ValueError):
            module.generate(self.make_export(self.root, denied=False), self.root / 'result')


if __name__ == '__main__':
    unittest.main()
