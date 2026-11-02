#!/usr/bin/env python3
"""Inactive provenance candidate. Not a deployment authorization boundary yet.

Requires PyYAML and authenticated gh OCI registry access. The operator must
independently trust the signer revision; this tool never derives it from the
checkout being verified. No production actions are performed.
"""
import argparse
import copy
import re
import subprocess
import sys

import yaml

REPO = 'Hyperbola-QAQ/Shirone-Strapi'
WORKFLOW = REPO + '/.github/workflows/shirone-release.yml'
HASH = re.compile(r'^[0-9a-f]{40}$')
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError('duplicate YAML mapping key')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def run(*args):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check(component, source, deployment, signer, digest):
    require(all(HASH.fullmatch(x) for x in (source, deployment, signer)), 'commits must be full SHA-1 values')
    require(bool(DIGEST.fullmatch(digest)), 'image digest must be a full SHA-256')
    require(source != deployment, 'build and deployment commits must differ')
    require(not run('git', 'status', '--porcelain', '--untracked-files=all'), 'worktree must be clean')
    require(run('git', 'symbolic-ref', '--quiet', '--short', 'HEAD') == 'main', 'main checkout required')
    require(run('git', 'rev-parse', 'HEAD') == deployment, 'deployment commit is not HEAD')
    remote = run('git', 'ls-remote', '--exit-code', 'origin', 'refs/heads/main').split()
    require(len(remote) == 2 and remote[0] == deployment and remote[1] == 'refs/heads/main', 'remote main differs')
    # Walk the first-parent chain explicitly and reject merges, unrelated
    # commits, repeated component promotions, and arbitrarily long histories.
    chain = []
    current = deployment
    while current != source:
        require(len(chain) < 2, 'at most one image change per component is allowed')
        parents = run('git', 'rev-list', '--parents', '-n', '1', current).split()
        require(len(parents) == 2 and parents[0] == current, 'release chain must be linear')
        chain.append((parents[1], current))
        current = parents[1]
    seen = set()
    final = None
    for parent, commit in reversed(chain):
        changed = run('git', 'diff-tree', '--no-commit-id', '--name-only', '-r', parent, commit).splitlines()
        require(len(changed) == 1, 'each release commit must change one manifest')
        match = re.fullmatch(r'deploy/k8s/(cms|ssr)\.yaml', changed[0])
        require(match is not None, 'only component manifests may change')
        item = match.group(1)
        require(item not in seen, 'a component cannot be promoted twice in one release')
        seen.add(item)
        path = changed[0]
        before = list(yaml.load_all(run('git', 'show', f'{parent}:{path}'), Loader=UniqueLoader))
        after = list(yaml.load_all(run('git', 'show', f'{commit}:{path}'), Loader=UniqueLoader))
        require(len(before) == len(after) == 1, 'one manifest document required')
        candidate = copy.deepcopy(before)
        doc = candidate[0]
        require(doc.get('kind') == 'Deployment' and doc.get('metadata', {}).get('name') == f'shirone-{item}', 'unexpected deployment')
        containers = doc['spec']['template']['spec']['containers']
        updated_containers = after[0]['spec']['template']['spec']['containers']
        require(len(containers) == len(updated_containers) == 1 and containers[0]['name'] == item, 'unexpected containers')
        internal = updated_containers[0]['image']
        image_match = re.fullmatch(rf'192\.168\.86\.100:30500/shirone/{item}@(sha256:[0-9a-f]{{64}})', internal)
        require(image_match is not None and containers[0]['image'] != internal, 'invalid or unchanged target image')
        containers[0]['image'] = internal
        require(candidate == after, 'manifest changes exceed the target image')
        item_digest = image_match.group(1)
        image = f'oci://ghcr.io/hyperbola-qaq/shirone-{item}@{item_digest}'
        # Every subject in the chain is independently verified. Registry copy
        # must preserve the content digest and clients must verify on pull.
        subprocess.run(['gh', 'attestation', 'verify', image, '--repo', REPO,
                        '--signer-workflow', WORKFLOW, '--source-ref', 'refs/heads/main',
                        '--source-digest', source, '--signer-digest', signer,
                        '--deny-self-hosted-runners'], check=True)
        final = (item, item_digest)
    require(final == (component, digest), 'last release commit does not match requested component and digest')
    print('Candidate provenance checks passed; this is not authorization to deploy.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=('cms', 'ssr'))
    parser.add_argument('source_commit')
    parser.add_argument('deployment_commit')
    parser.add_argument('trusted_signer_commit', help='independently reviewed workflow revision, not read from repository')
    parser.add_argument('digest')
    args = parser.parse_args()
    try:
        check(args.component, args.source_commit, args.deployment_commit, args.trusted_signer_commit, args.digest)
    except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError, subprocess.CalledProcessError) as exc:
        print(f'Candidate provenance verification failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
