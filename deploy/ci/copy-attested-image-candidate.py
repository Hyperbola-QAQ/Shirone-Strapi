#!/usr/bin/env python3
"""Inactive image-copy candidate; never invoked by CI or production deployment.

Dry-run verifies a fixed GHCR subject and its attestation. --apply copies to a
fresh staging tag, preserving all manifests and their digest, then verifies the
destination by digest. Requires authenticated gh and skopeo installations.
"""
import argparse
import hashlib
import re
import subprocess
import sys
import uuid

REPO = 'Hyperbola-QAQ/Shirone-Strapi'
WORKFLOW = REPO + '/.github/workflows/shirone-release.yml'
HASH = re.compile(r'^[0-9a-f]{40}$')
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inspect(image):
    # Keep registry TLS verification enabled. No credentials in argv or logs.
    return subprocess.run(['skopeo', 'inspect', '--raw', image], check=True,
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
    # Never overwrite a known release tag. A failed copy may leave this random
    # staging tag behind; it must not be used as a deployment identity.
    tag = 'candidate-' + uuid.uuid4().hex
    target = f'{dst_repo}:{tag}'
    print(f'Copying to staging tag {target}; failures may leave this tag behind.', flush=True)
    subprocess.run(['skopeo', 'copy', '--all', '--preserve-digests',
                    f'docker://{src}', f'docker://{target}'], check=True)
    assert_digest(inspect(f'docker://{dst_repo}@{digest}'), digest)
    assert_digest(inspect(f'docker://{target}'), digest)
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
