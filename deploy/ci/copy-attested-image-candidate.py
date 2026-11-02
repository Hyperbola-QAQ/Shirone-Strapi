#!/usr/bin/env python3
"""Inactive image-copy candidate; never invoked by CI or production deployment.

Dry-run verifies a fixed GHCR subject and its attestation. --apply copies to a
fresh staging tag, preserving all manifests and their digest, then verifies the
destination by digest. Requires authenticated gh and skopeo installations.
"""
import argparse
import hashlib
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

REPO = 'Hyperbola-QAQ/Shirone-Strapi'
WORKFLOW = REPO + '/.github/workflows/shirone-release.yml'
HASH = re.compile(r'^[0-9a-f]{40}$')
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')
DEST_CERT_DIR = Path('/etc/docker/certs.d/192.168.86.100:30500')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def destination_cert_dir():
    # Reuse only the administrator-managed CA directory. Reject symlinks,
    # group/world-writable paths, client keys and unexpected files.
    directory = DEST_CERT_DIR.lstat()
    require(stat.S_ISDIR(directory.st_mode) and directory.st_uid == 0 and not directory.st_mode & 0o022,
            'destination certificate directory must be root-owned and protected')
    require({entry.name for entry in DEST_CERT_DIR.iterdir()} == {'ca.crt'},
            'destination certificate directory must contain only ca.crt')
    ca = (DEST_CERT_DIR / 'ca.crt').lstat()
    require(stat.S_ISREG(ca.st_mode) and ca.st_uid == 0 and not ca.st_mode & 0o022,
            'destination CA must be a root-owned protected regular file')
    return str(DEST_CERT_DIR)


def inspect(image, cert_dir=None):
    # Keep registry TLS verification enabled. No credentials in argv or logs.
    args = ['skopeo', 'inspect', '--raw']
    if cert_dir:
        args.extend(['--cert-dir', cert_dir])
    args.append(image)
    return subprocess.run(args, check=True,
                          stdout=subprocess.PIPE).stdout


def assert_digest(data, expected):
    require('sha256:' + hashlib.sha256(data).hexdigest() == expected,
            'registry manifest digest does not match requested digest')


def check(component, source, signer, digest, apply=False):
    require(component in ('cms', 'ssr'), 'unsupported component')
    require(bool(HASH.fullmatch(source)) and bool(HASH.fullmatch(signer)), 'full trusted commit hashes required')
    require(bool(DIGEST.fullmatch(digest)), 'full SHA-256 digest required')
    src = f'ghcr.io/hyperbola-qaq/shirone-{component}@{digest}'
    dst_repo = f'192.168.86.100:30500/shirone/{component}'
    subprocess.run(['gh', 'attestation', 'verify', f'oci://{src}', '--repo', REPO,
                    '--signer-workflow', WORKFLOW, '--source-ref', 'refs/heads/main',
                    '--source-digest', source, '--signer-digest', signer,
                    '--deny-self-hosted-runners'], check=True)
    assert_digest(inspect(f'docker://{src}'), digest)
    if not apply:
        print('Source provenance and digest verified; dry run made no registry changes.')
        return
    cert_dir = destination_cert_dir()
    # Never overwrite a known release tag. A failed copy may leave this random
    # staging tag behind; it must not be used as a deployment identity.
    tag = 'candidate-' + uuid.uuid4().hex
    target = f'{dst_repo}:{tag}'
    print(f'Copying to staging tag {target}; failures may leave this tag behind.', flush=True)
    subprocess.run(['skopeo', 'copy', '--all', '--preserve-digests', '--dest-cert-dir', cert_dir,
                    f'docker://{src}', f'docker://{target}'], check=True)
    assert_digest(inspect(f'docker://{dst_repo}@{digest}', cert_dir), digest)
    assert_digest(inspect(f'docker://{target}', cert_dir), digest)
    print(f'Destination manifest and staging tag verified; deploy only by {dst_repo}@{digest}.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=('cms', 'ssr'))
    parser.add_argument('source_commit')
    parser.add_argument('trusted_signer_commit', help='independently reviewed signer revision')
    parser.add_argument('digest')
    parser.add_argument('--apply', action='store_true', help='explicitly copy to a new staging tag')
    args = parser.parse_args()
    try:
        check(args.component, args.source_commit, args.trusted_signer_commit, args.digest, args.apply)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'Candidate copy failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
