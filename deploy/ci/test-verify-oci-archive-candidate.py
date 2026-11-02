#!/usr/bin/env python3
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('verify-oci-archive-candidate.py')
spec = importlib.util.spec_from_file_location('oci_verify', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def encoded(value):
    return json.dumps(value, separators=(',', ':'), sort_keys=True).encode()


def descriptor(media, data):
    return {'mediaType': media, 'digest': 'sha256:' + hashlib.sha256(data).hexdigest(), 'size': len(data)}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'image.tar'
        self.layer = b'example layer'
        self.config = encoded({'os': 'linux', 'architecture': 'amd64', 'rootfs': {
            'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(self.layer).hexdigest()]}})
        self.config_desc = descriptor('application/vnd.oci.image.config.v1+json', self.config)
        self.layer_desc = descriptor('application/vnd.oci.image.layer.v1.tar', self.layer)
        self.manifest = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                         'config': self.config_desc, 'layers': [self.layer_desc]}

    def create(self, manifest=None, platform=None, extra=(), corrupt_layer=False, duplicate=False):
        manifest_bytes = encoded(manifest if manifest is not None else self.manifest)
        md = descriptor('application/vnd.oci.image.manifest.v1+json', manifest_bytes)
        md['platform'] = platform or {'os': 'linux', 'architecture': 'amd64'}
        index = encoded({'schemaVersion': 2, 'manifests': [md]})
        entries = [('oci-layout', encoded({'imageLayoutVersion': '1.0.0'})), ('index.json', index),
                   ('blobs/sha256/' + md['digest'][7:], manifest_bytes),
                   ('blobs/sha256/' + self.config_desc['digest'][7:], self.config),
                   ('blobs/sha256/' + self.layer_desc['digest'][7:], b'changed layer' if corrupt_layer else self.layer)]
        entries.extend(extra)
        if duplicate:
            entries.append(entries[0])
        with tarfile.open(self.path, 'w') as archive:
            for name, content in entries:
                info = tarfile.TarInfo(name)
                if content is None:
                    info.type = tarfile.SYMTYPE
                    info.linkname = '/etc/passwd'
                    archive.addfile(info)
                else:
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
        return md['digest']

    def test_valid_and_distinct_digests(self):
        manifest_digest = self.create()
        result = module.verify(self.path)
        self.assertEqual(result['manifest_digest'], manifest_digest)
        self.assertEqual(result['archive_digest'], 'sha256:' + hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertNotEqual(result['archive_digest'], manifest_digest)

    def test_wrong_platform_rejected(self):
        self.create(platform={'os': 'linux', 'architecture': 'arm64'})
        with self.assertRaises(ValueError): module.verify(self.path)

    def test_descriptor_size_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['layers'][0]['size'] += 1
        self.create(manifest=manifest)
        with self.assertRaises(ValueError): module.verify(self.path)

    def test_corrupt_layer_rejected(self):
        self.create(corrupt_layer=True)
        with self.assertRaises(ValueError): module.verify(self.path)

    def test_duplicate_path_rejected(self):
        self.create(duplicate=True)
        with self.assertRaises(ValueError): module.verify(self.path)

    def test_traversal_rejected(self):
        self.create(extra=[('../outside', b'bad')])
        with self.assertRaises(ValueError): module.verify(self.path)

    def test_symlink_rejected(self):
        self.create(extra=[('blobs/sha256/' + 'a' * 64, None)])
        with self.assertRaises(ValueError): module.verify(self.path)

    def test_unreferenced_blob_rejected(self):
        self.create(extra=[('blobs/sha256/' + 'a' * 64, b'extra')])
        with self.assertRaises(ValueError): module.verify(self.path)

    def test_unsupported_media_type_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['layers'][0]['mediaType'] = 'application/not-an-image'
        self.create(manifest=manifest)
        with self.assertRaises(ValueError): module.verify(self.path)

    def test_duplicate_json_key_rejected(self):
        with self.assertRaises(ValueError):
            module.parse_json(b'{"schemaVersion":2,"schemaVersion":1}')

    def test_artifact_subject_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['subject'] = self.layer_desc
        self.create(manifest=manifest)
        with self.assertRaises(ValueError): module.verify(self.path)


if __name__ == '__main__':
    unittest.main()
