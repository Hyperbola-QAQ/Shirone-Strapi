#!/usr/bin/env python3
"""Fail-closed admission for a *candidate* automatic release; never deploys.

An independently maintained local policy must authorize the exact source and
signer commits. The GitHub main branch and a valid attestation alone are not
authorization. The caller must retain an exclusive controller lock throughout
subsequent promotion; this program is not an automation service.
"""
import argparse
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('release_metadata', HERE / 'verify-release-metadata-candidate.py')
metadata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metadata)
REPO = 'Hyperbola-QAQ/Shirone-Strapi'
SHA = re.compile(r'^[0-9a-f]{40}$')
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')


def gh(*args):
    result = subprocess.run(['gh', 'api', *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return json.loads(result.stdout)


def admit(policy, run_id, signed_metadata, ciphertext_digest, recipient):
    expected = {'source', 'signer', 'workflow_id'}
    if not isinstance(policy, dict) or set(policy) != expected:
        raise ValueError('invalid policy')
    source, signer, workflow_id = policy['source'], policy['signer'], policy['workflow_id']
    if not all(isinstance(x, str) and SHA.fullmatch(x) for x in (source, signer)):
        raise ValueError('unapproved source or signer')
    if type(workflow_id) is not int or workflow_id < 1 or type(run_id) is not int or run_id < 1:
        raise ValueError('invalid workflow or run')
    if not DIGEST.fullmatch(ciphertext_digest) or not DIGEST.fullmatch(recipient):
        raise ValueError('invalid digest')
    run = gh(f'repos/{REPO}/actions/runs/{run_id}')
    expected_run = {'id': run_id, 'workflow_id': workflow_id, 'head_sha': source,
                    'head_branch': 'main', 'status': 'completed', 'conclusion': 'success'}
    if any(run.get(k) != v for k, v in expected_run.items()) or run.get('event') not in ('push', 'workflow_dispatch'):
        raise ValueError('run differs from authorized release')
    workflow = gh(f'repos/{REPO}/actions/workflows/shirone-release.yml')
    if workflow.get('id') != workflow_id or workflow.get('path') != '.github/workflows/shirone-release.yml':
        raise ValueError('unexpected workflow identity')
    remote = subprocess.run(['git', 'ls-remote', '--exit-code', 'origin', 'refs/heads/main'], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout.split()
    if remote != [source, 'refs/heads/main']:
        raise ValueError('remote main is not the approved source')
    # Verifies a real file-subject attestation with the independent source and
    # signer, and binds recipient, ciphertext digest and all image digests.
    return metadata.verify(signed_metadata, source, signer, recipient, ciphertext_digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', required=True, type=Path)
    parser.add_argument('--run-id', required=True, type=int)
    parser.add_argument('--metadata', required=True, type=Path)
    parser.add_argument('--ciphertext-digest', required=True)
    parser.add_argument('--recipient-fingerprint', required=True)
    args = parser.parse_args()
    try:
        # Never grant authority to a workflow artifact or a group-writable
        # policy. The controller user and its parent directories must also be
        # protected; this check alone cannot establish trust in the host.
        fd = os.open(args.policy, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or not 0 < info.st_size <= 4096:
                raise ValueError('unprotected policy')
            raw = os.read(fd, 4097)
            if len(raw) != info.st_size:
                raise ValueError('policy changed while reading')
        finally:
            os.close(fd)
        policy = json.loads(raw, object_pairs_hook=metadata.unique)
        result = admit(policy, args.run_id, args.metadata, args.ciphertext_digest, args.recipient_fingerprint)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, json.JSONDecodeError):
        print('Release admission failed; no deployment was attempted.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
