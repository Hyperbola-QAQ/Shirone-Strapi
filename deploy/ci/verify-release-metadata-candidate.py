#!/usr/bin/env python3
"""Verify signed release metadata before using it. Not deployment authorization.

Requires independently trusted source and signer commits, recipient fingerprint,
expected ciphertext digest and authenticated GitHub CLI. The workflow that will
sign these files is not active yet; local tests use a mock GitHub verifier.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

COMMIT = re.compile(r'^[0-9a-f]{40}$')
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')
REPO = 'Hyperbola-QAQ/Shirone-Strapi'
WORKFLOW = REPO + '/.github/workflows/shirone-release.yml'


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate metadata key')
        result[key] = value
    return result


def verify(path, source, signer, recipient, ciphertext):
    if not (COMMIT.fullmatch(source) and COMMIT.fullmatch(signer) and
            DIGEST.fullmatch(recipient) and DIGEST.fullmatch(ciphertext)):
        raise ValueError('invalid independently trusted identity')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 16384:
            raise ValueError('metadata must be a bounded regular file')
        before = os.read(fd, info.st_size + 1)
        if len(before) != info.st_size:
            raise ValueError('metadata changed during read')
        os.lseek(fd, 0, os.SEEK_SET)
        result = subprocess.run(['gh', 'attestation', 'verify', f'/proc/self/fd/{fd}',
                                 '--repo', REPO, '--signer-workflow', WORKFLOW,
                                 '--source-ref', 'refs/heads/main', '--source-digest', source,
                                 '--signer-digest', signer, '--deny-self-hosted-runners'],
                                pass_fds=(fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            raise ValueError('metadata attestation verification failed')
        os.lseek(fd, 0, os.SEEK_SET)
        after = os.read(fd, info.st_size + 1)
        if before != after or os.fstat(fd).st_size != info.st_size:
            raise ValueError('metadata changed during verification')
        data = json.loads(before, object_pairs_hook=unique)
        if not isinstance(data, dict) or set(data) != {'schema', 'source_commit', 'recipient_fingerprint', 'ciphertext_digest', 'manifests'}:
            raise ValueError('unexpected metadata shape')
        if type(data['schema']) is not int or data['schema'] != 1 or data['source_commit'] != source:
            raise ValueError('unexpected source or schema')
        if data['recipient_fingerprint'] != recipient or data['ciphertext_digest'] != ciphertext:
            raise ValueError('recipient or ciphertext differs from trusted expectation')
        manifests = data['manifests']
        if not isinstance(manifests, dict) or set(manifests) != {'cms', 'ssr', 'reconciler'}:
            raise ValueError('unexpected component set')
        if not all(isinstance(value, str) and DIGEST.fullmatch(value) for value in manifests.values()):
            raise ValueError('invalid component digest')
        return data
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata', type=Path)
    parser.add_argument('--source', required=True)
    parser.add_argument('--signer', required=True)
    parser.add_argument('--recipient-fingerprint', required=True)
    parser.add_argument('--ciphertext-digest', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.metadata, args.source, args.signer,
                                args.recipient_fingerprint, args.ciphertext_digest), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, json.JSONDecodeError):
        print('Metadata verification failed; no release is authorized.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
