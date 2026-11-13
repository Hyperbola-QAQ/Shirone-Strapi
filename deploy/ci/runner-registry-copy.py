#!/usr/bin/python3 -I
"""Restricted, root-owned runner entrypoint for copying one OCI archive.

Only images under the two Shirone repositories are allowed. The caller must
independently verify release attestations and OCI metadata before invoking it.
Registry credentials stay in a root-only memory file, never argv or logs.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import tomllib

REGISTRY = '192.168.86.100:30500'
CERT_DIR = '/etc/containerd/certs.d/' + REGISTRY
HOSTS = CERT_DIR + '/hosts.toml'
RUNNER_TEMP = '/home/github-actions/actions-runner/_work/_temp'
SKOPEO = '/usr/bin/skopeo'
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')
MAX_ARCHIVE = 2 * 1024 * 1024 * 1024


def read_authorization():
    fd = os.open(HOSTS, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 16384:
            raise ValueError('unsafe registry configuration')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            config = tomllib.load(stream)
    finally:
        os.close(fd)
    headers = config['host']['https://' + REGISTRY]['header']['authorization']
    if not isinstance(headers, list) or len(headers) != 1 or not isinstance(headers[0], str) or not headers[0].startswith('Basic '):
        raise ValueError('unexpected registry authorization')
    encoded = headers[0][6:]
    decoded = base64.b64decode(encoded, validate=True)
    if not decoded.startswith(b'ca-image-maintenance:') or not decoded.split(b':', 1)[1] or b'\n' in decoded or b'\r' in decoded:
        raise ValueError('unexpected registry identity')
    return encoded


def source_fd(run_id, component, runner_uid):
    base = os.open(RUNNER_TEMP, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        directory = os.open(f'shirone-release-{run_id}', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=base)
        try:
            info = os.fstat(directory)
            if info.st_uid != runner_uid or info.st_mode & 0o077:
                raise ValueError('unsafe runner release directory')
            fd = os.open(component + '.oci.tar', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        finally:
            os.close(directory)
    finally:
        os.close(base)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != runner_uid or info.st_mode & 0o077 or info.st_nlink != 1 or not 0 < info.st_size <= MAX_ARCHIVE:
        os.close(fd)
        raise ValueError('unsafe runner OCI archive')
    return fd


def copy(run_id, component, archive_digest, manifest_digest, runner_uid):
    if type(run_id) is not int or run_id < 1 or component not in ('cms', 'ssr'):
        raise ValueError('invalid release identity')
    if not DIGEST.fullmatch(archive_digest) or not DIGEST.fullmatch(manifest_digest):
        raise ValueError('invalid image digest')
    fd = source_fd(run_id, component, runner_uid)
    try:
        with tempfile.TemporaryDirectory(prefix='shirone-registry-copy-', dir='/tmp') as scratch:
            archive = Path(scratch) / 'image.oci.tar'
            digest = hashlib.sha256()
            with os.fdopen(fd, 'rb', closefd=False) as source, archive.open('xb') as target:
                remaining = os.fstat(fd).st_size
                if not 0 < remaining <= MAX_ARCHIVE:
                    raise ValueError('unsafe archive size')
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError('archive truncated')
                    remaining -= len(block)
                    digest.update(block)
                    target.write(block)
                if source.read(1):
                    raise ValueError('archive grew during copy')
                target.flush()
                os.fsync(target.fileno())
            if 'sha256:' + digest.hexdigest() != archive_digest:
                raise ValueError('archive digest mismatch')
            auth = read_authorization()
            auth_fd = os.memfd_create('shirone-registry-auth', os.MFD_CLOEXEC)
            try:
                os.write(auth_fd, json.dumps({'auths': {REGISTRY: {'auth': auth}}}).encode())
                os.lseek(auth_fd, 0, os.SEEK_SET)
                auth_path = f'/proc/self/fd/{auth_fd}'
                tag = f'{REGISTRY}/shirone/{component}:candidate-{run_id}-{secrets.token_hex(12)}'
                env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root'}
                subprocess.run([SKOPEO, 'copy', '--all', '--preserve-digests', '--dest-authfile', auth_path,
                                '--dest-cert-dir', CERT_DIR, 'oci-archive:' + str(archive), 'docker://' + tag],
                               check=True, timeout=900, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=env, pass_fds=(auth_fd,))
                os.lseek(auth_fd, 0, os.SEEK_SET)
                raw = subprocess.run([SKOPEO, 'inspect', '--raw', '--authfile', auth_path, '--cert-dir', CERT_DIR,
                                      'docker://' + tag], check=True, timeout=120, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, pass_fds=(auth_fd,)).stdout
                if 'sha256:' + hashlib.sha256(raw).hexdigest() != manifest_digest:
                    raise ValueError('registry manifest digest mismatch')
            finally:
                os.close(auth_fd)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id', type=int)
    parser.add_argument('component', choices=('cms', 'ssr'))
    parser.add_argument('archive_digest')
    parser.add_argument('manifest_digest')
    args = parser.parse_args()
    try:
        runner_uid = pwd.getpwnam('github-actions').pw_uid
        if os.geteuid() != 0 or os.environ.get('SUDO_UID') != str(runner_uid):
            raise ValueError('must be invoked by the dedicated runner through sudo')
        copy(args.run_id, args.component, args.archive_digest, args.manifest_digest, runner_uid)
    except Exception:
        print('Restricted registry copy failed; inspect without exposing credentials.', file=sys.stderr)
        return 1
    print('Restricted registry copy verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
