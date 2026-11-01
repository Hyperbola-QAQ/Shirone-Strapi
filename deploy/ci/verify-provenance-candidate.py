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
    parents = run('git', 'rev-list', '--parents', '-n', '1', deployment).split()
    require(parents == [deployment, source], 'deployment must be a single-parent child of build commit')
    path = f'deploy/k8s/{component}.yaml'
    changed = run('git', 'diff-tree', '--no-commit-id', '--name-only', '-r', source, deployment).splitlines()
    require(changed == [path], 'deployment commit may change only its component manifest')
    before = list(yaml.load_all(run('git', 'show', f'{source}:{path}'), Loader=UniqueLoader))
    after = list(yaml.load_all(run('git', 'show', f'{deployment}:{path}'), Loader=UniqueLoader))
    require(len(before) == len(after) == 1, 'one manifest document required')
    candidate = copy.deepcopy(before)
    doc = candidate[0]
    require(doc.get('kind') == 'Deployment' and doc.get('metadata', {}).get('name') == f'shirone-{component}', 'unexpected deployment')
    containers = doc['spec']['template']['spec']['containers']
    require(len(containers) == 1 and containers[0]['name'] == component, 'unexpected containers')
    internal = f'192.168.86.100:30500/shirone/{component}@{digest}'
    require(containers[0]['image'] != internal, 'target image is unchanged')
    containers[0]['image'] = internal
    require(candidate == after, 'manifest changes exceed the target image')
    image = f'oci://ghcr.io/hyperbola-qaq/shirone-{component}@{digest}'
    # gh fetches and verifies the OCI subject and GitHub-backed attestation.
    # The destination image names the same digest; its registry must honor OCI
    # digest verification when copying/pulling. Never trust tags as identity.
    subprocess.run(['gh', 'attestation', 'verify', image, '--repo', REPO,
                    '--signer-workflow', WORKFLOW, '--source-ref', 'refs/heads/main',
                    '--source-digest', source, '--signer-digest', signer,
                    '--deny-self-hosted-runners'], check=True)
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
