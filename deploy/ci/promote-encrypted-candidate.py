#!/usr/bin/env python3
"""Inactive signed-encrypted OCI promotion candidate; preview by default.

The signer/source/recipient and all three image digests must be independently
reviewed. This candidate has not passed real GitHub attestation or cluster
release/rollback validation and is not a production authorization boundary.
"""
import argparse
import copy
import importlib.util
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


crypto = module('encrypted_release_candidate', 'encrypted-release-candidate.py')
env = crypto.envelope
registry = module('copy_attested_candidate', 'copy-attested-image-candidate.py')
policy = module('provenance_candidate', 'verify-provenance-candidate.py')
COMMIT = re.compile(r'^[0-9a-f]{40}$')
UID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(*args):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def validate_chain(component, source, deployment, digests):
    """Bind committed manifest-only changes to the verified envelope."""
    require(not run('git', 'status', '--porcelain', '--untracked-files=all'), 'worktree must be clean')
    require(run('git', 'symbolic-ref', '--quiet', '--short', 'HEAD') == 'main', 'main checkout required')
    require(run('git', 'rev-parse', 'HEAD') == deployment, 'deployment commit differs from HEAD')
    remote = run('git', 'ls-remote', '--exit-code', 'origin', 'refs/heads/main').split()
    require(remote == [deployment, 'refs/heads/main'], 'remote main differs')
    chain, current = [], deployment
    while current != source:
        require(len(chain) < 2, 'release chain exceeds component count')
        parents = run('git', 'rev-list', '--parents', '-n', '1', current).split()
        require(len(parents) == 2 and parents[0] == current, 'release chain must be linear')
        chain.append((parents[1], current))
        current = parents[1]
    seen, final = set(), None
    for previous, commit in reversed(chain):
        changed = run('git', 'diff-tree', '--no-commit-id', '--name-only', '-r', previous, commit).splitlines()
        require(len(changed) == 1, 'each release commit must change one file')
        match = re.fullmatch(r'deploy/k8s/(cms|ssr)\.yaml', changed[0])
        require(match is not None, 'only component manifests may change')
        item, path = match.group(1), changed[0]
        require(item not in seen, 'duplicate component in release chain')
        seen.add(item)
        before = list(policy.yaml.load_all(run('git', 'show', f'{previous}:{path}'), Loader=policy.UniqueLoader))
        after = list(policy.yaml.load_all(run('git', 'show', f'{commit}:{path}'), Loader=policy.UniqueLoader))
        require(len(before) == len(after) == 1, 'exactly one manifest required')
        desired = copy.deepcopy(before)
        doc = desired[0]
        require(doc.get('kind') == 'Deployment' and doc.get('metadata', {}).get('name') == f'shirone-{item}',
                'unexpected deployment')
        containers = doc['spec']['template']['spec']['containers']
        require(len(containers) == 1 and containers[0]['name'] == item, 'unexpected containers')
        target = f'192.168.86.100:30500/shirone/{item}@{digests[item]}'
        require(containers[0]['image'] != target, 'unchanged image')
        containers[0]['image'] = target
        require(desired == after, 'manifest changes exceed verified image')
        final = item
    require(final == component, 'last release commit is not requested component')


def promote(component, source, deployment, signer, uid, old, ciphertext, ciphertext_digest,
            key, recipient, digests, copy_image=False, apply=False):
    require(component in ('cms', 'ssr'), 'unsupported component')
    require(all(COMMIT.fullmatch(x) for x in (source, deployment, signer)), 'full commits required')
    require(bool(UID.fullmatch(uid)), 'deployment UID required')
    require(set(digests) == set(env.COMPONENTS) and all(env.DIGEST.fullmatch(x) for x in digests.values()),
            'all three independently reviewed image digests are required')
    pattern = rf'192\.168\.86\.100:30500/shirone/{component}@sha256:[0-9a-f]{{64}}'
    target = f'192.168.86.100:30500/shirone/{component}@{digests[component]}'
    require(bool(re.fullmatch(pattern, old)) and old != target, 'invalid old image')
    validate_chain(component, source, deployment, digests)
    # TemporaryDirectory is newly created and removed by this process only;
    # it must have sufficient space for the plaintext and extracted OCI files.
    with tempfile.TemporaryDirectory(prefix='shirone-encrypted-promotion-') as scratch:
        base = Path(scratch)
        plaintext = base / 'release.tar'
        crypto.decrypt(ciphertext, plaintext, key, recipient, source, signer, ciphertext_digest, digests)
        fingerprint = crypto.recipient_fingerprint(recipient)
        extracted = base / 'images'
        env.inspect(plaintext, source, fingerprint, digests, extracted, verify_oci=True)
        image = extracted / env.FILES[component]
        require(env.oci.verify(image)['manifest_digest'] == digests[component], 'archive manifest mismatch')
        cert_dir = registry.destination_cert_dir()
        if copy_image:
            # A fresh staging tag avoids overwriting another release. A failure
            # can leave that tag; an operator must review before cleanup.
            tag = f'192.168.86.100:30500/shirone/{component}:candidate-{uuid.uuid4().hex}'
            subprocess.run(['skopeo', 'copy', '--all', '--preserve-digests', '--dest-cert-dir', cert_dir,
                            f'oci-archive:{image}', f'docker://{tag}'], check=True)
            registry.assert_digest(registry.inspect(f'docker://{tag}', cert_dir), digests[component])
        registry.assert_digest(registry.inspect(f'docker://{target}', cert_dir), digests[component])
        command = ['bash', str(HERE / 'promote-reviewed.sh'), component, deployment, uid, old, target]
        if apply:
            command.append('--apply')
        subprocess.run(command, check=True)
    print('Candidate checks completed; independent real signature, runtime and rollback validation remain required.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=('cms', 'ssr'))
    for name in ('source', 'deployment', 'signer', 'uid', 'old', 'ciphertext', 'ciphertext_digest', 'key', 'recipient'):
        parser.add_argument('--' + name.replace('_', '-'), required=True)
    for component in env.COMPONENTS:
        parser.add_argument('--' + component + '-manifest', required=True)
    parser.add_argument('--copy', action='store_true', help='explicitly copy verified image to a new internal staging tag')
    parser.add_argument('--apply', action='store_true', help='explicitly request the guarded production update')
    args = parser.parse_args()
    try:
        promote(args.component, args.source, args.deployment, args.signer, args.uid, args.old,
                args.ciphertext, args.ciphertext_digest, args.key, args.recipient,
                {n: getattr(args, n + '_manifest') for n in env.COMPONENTS}, args.copy, args.apply)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, policy.yaml.YAMLError) as exc:
        print(f'Encrypted promotion candidate failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
