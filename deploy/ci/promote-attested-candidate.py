#!/usr/bin/env python3
"""Inactive promotion candidate; preview by default, not production-authorized.

Requires the separately reviewed source and signer commits, an already copied
destination image, authenticated gh/skopeo, and the existing Ansible entrypoint.
It performs no copy and is not wired into any workflow.
"""
import argparse
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('copy_attested_candidate', HERE / 'copy-attested-image-candidate.py')
copy_candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copy_candidate)
COMMIT = re.compile(r'^[0-9a-f]{40}$')
UID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def promote(component, source, deployment, signer, uid, old, target, apply=False):
    require(component in ('cms', 'ssr'), 'unsupported component')
    require(all(COMMIT.fullmatch(item) for item in (source, deployment, signer)), 'full commits required')
    require(bool(UID.fullmatch(uid)), 'full deployment UID required')
    pattern = rf'192\.168\.86\.100:30500/shirone/{component}@(sha256:[0-9a-f]{{64}})'
    old_match, target_match = re.fullmatch(pattern, old), re.fullmatch(pattern, target)
    require(old_match is not None and target_match is not None and old != target, 'invalid or unchanged image')
    digest = target_match.group(1)
    # This verifier checks the complete release commit chain, every component's
    # attestation and the exact expected final component/digest.
    subprocess.run([sys.executable, str(HERE / 'verify-provenance-candidate.py'),
                    component, source, deployment, signer, digest], check=True)
    cert_dir = copy_candidate.destination_cert_dir()
    raw = copy_candidate.inspect(f'docker://{target}', cert_dir)
    copy_candidate.assert_digest(raw, digest)
    # The existing playbook independently checks live UID, old image and
    # resourceVersion and defaults to server-side admission preview.
    command = ['bash', str(HERE / 'promote-reviewed.sh'), component, deployment, uid, old, target]
    if apply:
        command.append('--apply')
    subprocess.run(command, check=True)
    print('Candidate checks completed. Real signatures, copy, rollback and runtime behavior remain unverified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=('cms', 'ssr'))
    parser.add_argument('source_commit')
    parser.add_argument('deployment_commit')
    parser.add_argument('trusted_signer_commit', help='independently reviewed and protected workflow revision')
    parser.add_argument('deployment_uid')
    parser.add_argument('previous_image')
    parser.add_argument('target_image')
    parser.add_argument('--apply', action='store_true', help='explicitly request guarded production application')
    args = parser.parse_args()
    try:
        promote(args.component, args.source_commit, args.deployment_commit,
                args.trusted_signer_commit, args.deployment_uid,
                args.previous_image, args.target_image, args.apply)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'Candidate promotion failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
