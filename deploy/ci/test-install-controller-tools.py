import hashlib
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', Path(__file__).with_name('install-controller-tools.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_digest_validation_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file'
            path.write_bytes(b'fixture')
            installer.check_hash(path, hashlib.sha256(b'fixture').hexdigest())
            with self.assertRaises(ValueError): installer.check_hash(path, '0' * 64)

    def test_existing_destination_and_insecure_parent_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'tools'
            target.mkdir()
            (target / 'keep').write_text('safe')
            with self.assertRaises(FileExistsError): installer.install(target)
            self.assertEqual((target / 'keep').read_text(), 'safe')
            os.chmod(root, 0o755)
            with self.assertRaises(ValueError): installer.install(root / 'other')

    def test_failed_package_leaves_no_partial_destination(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(installer, 'debian_package', side_effect=RuntimeError('offline')):
            root = Path(tmp)
            with self.assertRaises(RuntimeError): installer.install(root / 'tools')
            self.assertFalse((root / 'tools').exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_atomic_publish_never_replaces_a_racing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left, right, target = (root / name for name in ('left', 'right', 'target'))
            for directory in (left, right):
                directory.mkdir()
                (directory / 'marker').write_text(directory.name)
            def publish(source):
                try:
                    installer.rename_noreplace(source, target)
                    return True
                except FileExistsError:
                    return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(publish, (left, right)))
            self.assertEqual(sorted(outcomes), [False, True])
            self.assertIn((target / 'marker').read_text(), ('left', 'right'))
            self.assertEqual(sum(path.exists() for path in (left, right)), 1)

    def test_mirror_failure_falls_back_to_matching_official_archive(self):
        calls = []
        def fake_fetch(url, destination, digest):
            calls.append((url, digest))
            if len(calls) == 1: raise subprocess.CalledProcessError(1, 'curl')
            destination.write_bytes(b'fixture')
        with tempfile.TemporaryDirectory() as tmp, patch.object(installer, 'fetch', side_effect=fake_fetch):
            path, kind = installer.tool_archive(('https://mirror.invalid/file', 'a' * 64),
                                                ('https://official.invalid/file', 'b' * 64), Path(tmp), 'test')
            self.assertEqual(kind, 'official')
            self.assertEqual(path.read_bytes(), b'fixture')
            self.assertEqual(calls[-1], ('https://official.invalid/file', 'b' * 64))

    def test_unsafe_apt_metadata_is_never_fetched(self):
        def fake_run(*args, **kwargs):
            if args[0] == 'apt-get': raise subprocess.CalledProcessError(1, 'apt-get')
            return b'Filename: pool/../../sensitive.deb\n'
        with tempfile.TemporaryDirectory() as tmp, patch.object(installer, 'run', side_effect=fake_run), \
             patch.object(installer, 'fetch') as fetch:
            with self.assertRaises(ValueError):
                installer.debian_package('package', '1', '0' * 64, Path(tmp))
            fetch.assert_not_called()

    def test_success_stages_and_verifies_a_complete_private_tree(self):
        def fake_package(name, version, digest, directory):
            path = directory / (name + '.deb')
            path.write_bytes(b'pinned synthetic package')
            return path
        def fake_archive(mirror, official, directory, label):
            path = directory / (label + '.archive')
            path.write_bytes(b'pinned synthetic archive')
            return path, 'official'
        def fake_run(*args, **kwargs):
            if args[0] == 'dpkg-deb':
                root = Path(args[-1])
                (root / 'etc/containers').mkdir(parents=True, exist_ok=True)
                (root / 'etc/containers/policy.json').write_text('{}')
                return b''
            if args[0] == 'tar':
                root, member = Path(args[args.index('-C') + 1]), args[-1]
                target = root / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'synthetic binary')
                return b''
            if args[0].endswith('/ansible'): return b'ansible [core 2.19.11]'
            if args[0].endswith('/skopeo'): return b'skopeo version 1.18.0'
            if args[0].endswith('/age'): return b'v1.3.2'
            if args[0].endswith('/gh'):
                if '--version' in args: return b'gh version 2.102.1'
                return b'--signer-workflow --source-ref --source-digest --signer-digest --deny-self-hosted-runners'
            raise AssertionError('unexpected command')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(installer, 'PACKAGES', (('fixture', '1', '0' * 64),)), \
             patch.object(installer, 'debian_package', side_effect=fake_package), \
             patch.object(installer, 'tool_archive', side_effect=fake_archive), \
             patch.object(installer, 'run', side_effect=fake_run):
            root = Path(tmp)
            installer.install(root / 'tools')
            self.assertEqual({p.name for p in (root / 'tools/bin').iterdir()},
                             {'gh', 'age', 'age-keygen', 'ansible', 'ansible-playbook', 'ansible-inventory', 'skopeo'})
            self.assertEqual([p.name for p in root.iterdir()], ['tools'])


if __name__ == '__main__':
    unittest.main()
