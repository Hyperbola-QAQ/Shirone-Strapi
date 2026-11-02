#!/usr/bin/env python3
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('oci_disclosure', Path(__file__).with_name('audit-oci-disclosure-candidate.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def encoded(value):
    return json.dumps(value, separators=(',', ':'), sort_keys=True).encode()


def desc(media, data):
    return {'mediaType': media, 'digest': 'sha256:' + hashlib.sha256(data).hexdigest(), 'size': len(data)}


def make_layer(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as tar:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return output.getvalue()


def make_image(path, raw_layers, config_extra=None):
    layers = [gzip.compress(raw, mtime=0) for raw in raw_layers]
    layer_descs = [desc('application/vnd.oci.image.layer.v1.tar+gzip', layer) for layer in layers]
    config = {'os': 'linux', 'architecture': 'amd64', 'rootfs': {'type': 'layers',
              'diff_ids': ['sha256:' + hashlib.sha256(raw).hexdigest() for raw in raw_layers]}}
    config.update(config_extra or {})
    config_bytes = encoded(config)
    config_desc = desc('application/vnd.oci.image.config.v1+json', config_bytes)
    manifest = encoded({'schemaVersion': 2, 'config': config_desc, 'layers': layer_descs})
    manifest_desc = desc('application/vnd.oci.image.manifest.v1+json', manifest)
    manifest_desc['platform'] = {'os': 'linux', 'architecture': 'amd64'}
    index = encoded({'schemaVersion': 2, 'manifests': [manifest_desc]})
    blobs = [(manifest_desc, manifest), (config_desc, config_bytes)] + list(zip(layer_descs, layers))
    entries = [('oci-layout', encoded({'imageLayoutVersion': '1.0.0'})), ('index.json', index)]
    entries.extend(('blobs/sha256/' + descriptor['digest'][7:], data) for descriptor, data in blobs)
    with tarfile.open(path, 'w') as tar:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


class DisclosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'fixture.tar'

    def test_clean_still_requires_review(self):
        make_image(self.path, [make_layer([('app/readme.txt', b'public text')])])
        result = mod.audit(self.path)
        self.assertEqual(result['findings'], [])
        self.assertTrue(result['manual_review_required'])
        self.assertFalse(result['upload_authorized'])

    def test_deleted_secret_in_historical_layer_detected(self):
        fake = b'-----BEGIN OPENSSH PRIVATE KEY-----\nplaceholder\n-----END OPENSSH PRIVATE KEY-----'
        make_image(self.path, [make_layer([('app/deleted', fake)]),
                               make_layer([('app/.wh.deleted', b'')])])
        result = mod.audit(self.path)
        self.assertIn('secret_pattern', result['findings'])
        self.assertNotIn('deleted', json.dumps(result))

    def test_metadata_detected_without_echo(self):
        fake = 'ghp_' + 'x' * 36
        make_image(self.path, [make_layer([('ok', b'hello')])], {'config': {'Env': [fake]}})
        result = mod.audit(self.path)
        self.assertIn('secret_pattern', result['findings'])
        self.assertNotIn(fake, json.dumps(result))

    def test_suspect_path_and_nested_archive_flagged(self):
        make_image(self.path, [make_layer([('home/.env', b'EXAMPLE=x'), ('lib/data.gz', gzip.compress(b'x'))])])
        self.assertEqual(mod.audit(self.path)['findings'], ['nested_archive_uninspected', 'sensitive_path'])

    def test_secret_across_read_boundary(self):
        prefix = b'x' * (mod.BLOCK - 12)
        fake = b'-----BEGIN PRIVATE KEY-----\nplaceholder'
        make_image(self.path, [make_layer([('data', prefix + fake)])])
        self.assertIn('secret_pattern', mod.audit(self.path)['findings'])

    def test_traversal_flagged(self):
        make_image(self.path, [make_layer([('../outside', b'irrelevant')])])
        self.assertIn('unsafe_layer_path', mod.audit(self.path)['findings'])

    def test_bytes_after_tar_end_are_screened(self):
        trailing = b'ghp_' + b'x' * 36
        make_image(self.path, [make_layer([('ok', b'hello')]) + trailing])
        self.assertIn('secret_pattern', mod.audit(self.path)['findings'])


if __name__ == '__main__':
    unittest.main()
