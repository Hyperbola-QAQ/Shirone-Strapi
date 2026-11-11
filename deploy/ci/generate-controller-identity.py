#!/usr/bin/env python3
"""Generate fresh controller-specific identities without printing private keys.

This does not authorize the SSH key, configure GitHub access, change the
release recipient, or enable a service. A pre-existing identity is never
overwritten; an operator must reconcile any previous partial attempt.
"""
import ctypes
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


def rename_noreplace(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, 'renameat2'):
        raise OSError('atomic no-replace rename is unavailable')
    libc.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def command(*args):
    return subprocess.run(args, check=True, timeout=30, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL).stdout


def generate(state, age_keygen):
    state = Path(state)
    info = state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('identity parent must be private')
    destination = state / 'identity'
    if os.path.lexists(destination):
        raise FileExistsError('identity already exists')
    with tempfile.TemporaryDirectory(prefix='.identity-', dir=state) as temporary:
        root = Path(temporary)
        age = root / 'identity.agekey'
        deploy = root / 'deploy-key'
        old_umask = os.umask(0o077)
        try:
            command(str(age_keygen), '-o', str(age))
            public_age = command(str(age_keygen), '-y', str(age)).decode('ascii').strip()
            if not re.fullmatch(r'age1[023456789acdefghjklmnpqrstuvwxyz]{58}', public_age):
                raise ValueError('invalid age public identity')
            (root / 'recipient.txt').write_text(public_age + '\n')
            command('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(deploy), '-C', 'shirone-release-controller')
            if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+ shirone-release-controller\n',
                                (root / 'deploy-key.pub').read_text()):
                raise ValueError('invalid deploy public key')
            for path in root.iterdir():
                item = path.lstat()
                if not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid():
                    raise ValueError('unexpected identity file')
                path.chmod(0o600)
            rename_noreplace(root, destination)
            directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.umask(old_umask)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--age-keygen', required=True, type=Path)
    parser.add_argument('--generate', required=True, action='store_true')
    args = parser.parse_args()
    generate(args.state, args.age_keygen)
