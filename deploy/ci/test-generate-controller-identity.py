import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('identity', Path(__file__).with_name('generate-controller-identity.py'))
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


class IdentityTests(unittest.TestCase):
    def fake_command(self, *args):
        if '-o' in args:
            Path(args[args.index('-o') + 1]).write_text('temporary synthetic private fixture')
            return b''
        if '-y' in args:
            return ('age1' + 'q' * 58 + '\n').encode()
        if args[0] == 'ssh-keygen':
            target = Path(args[args.index('-f') + 1])
            target.write_text('temporary synthetic ssh fixture')
            target.with_suffix('.pub').write_text('ssh-ed25519 QUJDRA== shirone-release-controller\n')
            return b''
        raise AssertionError('unexpected command')

    def test_generates_private_files_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(identity, 'command', side_effect=self.fake_command):
            root = Path(tmp)
            identity.generate(root, '/isolated/age-keygen')
            target = root / 'identity'
            self.assertEqual({p.name for p in target.iterdir()},
                             {'identity.agekey', 'recipient.txt', 'deploy-key', 'deploy-key.pub'})
            self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in target.iterdir()))
            with self.assertRaises(FileExistsError): identity.generate(root, '/isolated/age-keygen')
            self.assertEqual((target / 'deploy-key').read_text(), 'temporary synthetic ssh fixture')

    def test_failure_removes_only_own_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(identity, 'command', side_effect=RuntimeError('failed')):
            root = Path(tmp)
            (root / 'keep').write_text('keep')
            with self.assertRaises(RuntimeError): identity.generate(root, '/isolated/age-keygen')
            self.assertEqual([p.name for p in root.iterdir()], ['keep'])

    def test_insecure_parent_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o755)
            with self.assertRaises(ValueError): identity.generate(tmp, '/isolated/age-keygen')


if __name__ == '__main__':
    unittest.main()
