#!/usr/bin/env python3
"""Install pinned controller tools into a new private user-owned directory.

No credentials are generated, copied, read or printed. The caller must supply
an existing private parent directory. This does not configure or start a service.
"""
import argparse
import ctypes
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

PACKAGES = (
    ('ansible-core', '2.19.11-0+deb13u1', 'e6008ff6bb4de9601b167753dc8557e0c53c50e5e3f78c39c6b7f89fafa594bb'),
    ('python3-cryptography', '43.0.0-3+deb13u1', 'edfc3aab6cbc4941aa19798059867696eeae90eb41b6dc2c5197c521da548972'),
    ('python3-jinja2', '3.1.6-1', '3271804134e18dbc8dbd146f58399817789373e86a58be44d10ce159cf8c4600'),
    ('python3-resolvelib', '1.1.0-1', '341185635136e19e642f63e409e910ac1ba706e71a8a3cbffd196d6dab792e03'),
    ('python3-yaml', '6.0.2-1+b2', '0019c4e7848c83a0d6bcb78cca902b5cdb1714aa6370ba01955b1fed6f667056'),
    ('python3-packaging', '25.0-1', 'd70c8469f6e9c6105e1ebcd3ebdc963f11dadbeddd89d2d1a2a3cb1c97d2ecbc'),
    ('python3-markupsafe', '2.1.5-1+b3', 'f82dbd759e6b9c111dc764b3ce7744633d0c97948aaa77294984b80e9db44a24'),
    ('python3-cffi-backend', '1.17.1-3', '7cfe53f3eff772127fd22e503876e3dd2b34b4c2a68fe9a524ce214e5bb7927a'),
    ('skopeo', '1.18.0+ds1-1+b5', '7ca44a3f31f3cddca9d9dc858029dd08c30faf6e93456753825509e6a59192e2'),
    ('libgpgme11t64', '1.24.2-3', '6650203af59ccf73b22cd6904fb179a799cb70d62885b19ae725c0655c04241c'),
    ('golang-github-containers-common', '0.62.2+ds1-2', 'de4a8a0fe1d55a259b7f615c67a9fe25fb98715fff51d0922bfad73e1045bce1'),
)
GH_ARCH = ('https://mirrors.tuna.tsinghua.edu.cn/archlinux/extra/os/x86_64/github-cli-2.102.1-1-x86_64.pkg.tar.zst', '720950e1349ad07851de0b6c5ae2620294cfaeffbead2eb74fe1e050ce3e0f14')
GH_OFFICIAL = ('https://github.com/cli/cli/releases/download/v2.102.1/gh_2.102.1_linux_amd64.tar.gz', '77b9e297e94c50336823d0f84de7ef1be60cba704601fa0d98f129f47a94987b')
AGE_ARCH = ('https://mirrors.tuna.tsinghua.edu.cn/archlinux/extra/os/x86_64/age-1.3.2-1-x86_64.pkg.tar.zst', 'c82edb230b872f8bd2d30f240722806038ae3613ccac43fa99df5e7e8a2d4196')
AGE_OFFICIAL = ('https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-amd64.tar.gz', 'cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10')


def rename_noreplace(source, destination):
    """Atomically publish only if the destination does not already exist."""
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, 'renameat2'):
        raise OSError('atomic no-replace rename is unavailable')
    libc.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def run(*args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, check=True, timeout=240,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout


def check_hash(path, expected):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError('download checksum mismatch')


def fetch(url, destination, expected):
    run('curl', '--fail', '--silent', '--location', '--connect-timeout', '15', '--max-time', '180',
        '--output', str(destination), url)
    check_hash(destination, expected)


def debian_package(name, version, expected, directory):
    spec = f'{name}={version}'
    try:
        run('apt-get', 'download', spec, cwd=directory)
        matches = list(directory.glob(f'{name}_*.deb'))
        if len(matches) != 1:
            raise ValueError('unexpected package count')
        check_hash(matches[0], expected)
        return matches[0]
    except (subprocess.SubprocessError, ValueError):
        for path in directory.glob(f'{name}_*.deb'):
            path.unlink()
    # The path comes from the local signed APT metadata. Keep it confined to
    # the package pool and check the independently pinned digest afterward.
    metadata = run('apt-cache', 'show', spec).decode('utf-8')
    paths = re.findall(r'^Filename: (pool/[A-Za-z0-9+._~/\-]+\.deb)$', metadata, re.M)
    if not paths or len(set(paths)) != 1 or '..' in Path(paths[0]).parts:
        raise ValueError('invalid package metadata')
    path = directory / f'{name}.deb'
    for base in ('https://mirrors.tuna.tsinghua.edu.cn/debian/',
                 'https://mirrors.tuna.tsinghua.edu.cn/debian-security/',
                 'https://deb.debian.org/debian/',
                 'https://security.debian.org/debian-security/'):
        try:
            fetch(base + paths[0], path, expected)
            if 'tsinghua.edu.cn' not in base:
                print(f'Configured mirrors unavailable for {name}; used official Debian source with the same checksum.')
            return path
        except (subprocess.SubprocessError, ValueError):
            if path.exists(): path.unlink()
    raise RuntimeError('pinned Debian package unavailable')


def tool_archive(mirror, official, directory, label):
    path = directory / (label + '.archive')
    try:
        fetch(mirror[0], path, mirror[1])
        return path, 'arch'
    except (subprocess.SubprocessError, ValueError):
        if path.exists(): path.unlink()
        print(f'Domestic {label} mirror unavailable or checksum mismatch; using official same-version source.')
        fetch(official[0], path, official[1])
        return path, 'official'


def write_wrapper(path, command, exports=''):
    path.write_text('#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n' + exports + '\nexec ' + command + ' "$@"\n')
    path.chmod(0o700)


def install(destination):
    destination = Path(destination)
    if not destination.is_absolute() or destination.name in ('', '.', '..'):
        raise ValueError('absolute new destination required')
    parent = destination.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('destination parent must be private')
    if os.path.lexists(destination):
        raise FileExistsError('destination already exists')
    with tempfile.TemporaryDirectory(prefix='.shirone-tools-', dir=parent) as scratch:
        work = Path(scratch)
        downloads, root, bindir = (work / x for x in ('downloads', 'root', 'bin'))
        for path in (downloads, root, bindir): path.mkdir(mode=0o700)
        for name, version, digest in PACKAGES:
            package = debian_package(name, version, digest, downloads)
            run('dpkg-deb', '-x', str(package), str(root))
        for label, mirror, official, binaries in (
            ('gh', GH_ARCH, GH_OFFICIAL, (('gh', 'usr/bin/gh', 'gh_2.102.1_linux_amd64/bin/gh'),)),
            ('age', AGE_ARCH, AGE_OFFICIAL, (('age', 'usr/bin/age', 'age/age'),
                                             ('age-keygen', 'usr/bin/age-keygen', 'age/age-keygen'))),
        ):
            archive, kind = tool_archive(mirror, official, downloads, label)
            def stage_archive(archive_kind):
                for binary, arch_member, official_member in binaries:
                    member = arch_member if archive_kind == 'arch' else official_member
                    run('tar', '--zstd' if archive_kind == 'arch' else '-z', '-xf', str(archive), '-C', str(work), member)
                    shutil.copyfile(work / member, bindir / binary)
                    (bindir / binary).chmod(0o700)
                expected = b'2.102.1' if label == 'gh' else b'1.3.2'
                if expected not in run(str(bindir / label), '--version'):
                    raise ValueError('mirror tool version mismatch')
            try:
                stage_archive(kind)
            except (subprocess.SubprocessError, ValueError, OSError):
                if kind != 'arch': raise
                print(f'Domestic {label} binary incompatible; using official same-version source.')
                fetch(official[0], archive, official[1])
                stage_archive('official')
        policy = root / 'etc/containers/policy.json'
        if not policy.is_file():
            raise ValueError('pinned containers policy missing')
        py = 'export PYTHONPATH="$root/root/usr/lib/python3/dist-packages"'
        for name in ('ansible', 'ansible-playbook', 'ansible-inventory'):
            write_wrapper(bindir / name, f'"$root/root/usr/bin/{name}"', py)
        write_wrapper(bindir / 'skopeo', '"$root/root/usr/bin/skopeo" --policy "$root/root/etc/containers/policy.json"',
                      'export LD_LIBRARY_PATH="$root/root/usr/lib/x86_64-linux-gnu"')
        if b'2.19.11' not in run(str(bindir / 'ansible'), '--version'):
            raise ValueError('unexpected Ansible version')
        if b'1.18.0' not in run(str(bindir / 'skopeo'), '--version'):
            raise ValueError('unexpected skopeo version')
        if b'2.102.1' not in run(str(bindir / 'gh'), '--version'):
            raise ValueError('unexpected GitHub CLI version')
        flags = run(str(bindir / 'gh'), 'attestation', 'verify', '--help')
        for flag in (b'--signer-workflow', b'--source-ref', b'--source-digest', b'--signer-digest', b'--deny-self-hosted-runners'):
            if flag not in flags: raise ValueError('GitHub CLI attestation flag missing')
        if b'1.3.2' not in run(str(bindir / 'age'), '--version'):
            raise ValueError('unexpected age version')
        rename_noreplace(work, destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--install', action='store_true', required=True)
    args = parser.parse_args()
    install(args.destination)
