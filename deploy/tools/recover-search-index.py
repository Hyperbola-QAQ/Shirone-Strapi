#!/usr/bin/env python3
"""Guarded reset of the disposable search store. Default: read-only preview.

The script never reads Secrets or primary data. It refuses state it cannot prove.
"""
import argparse
import json
import re
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--namespace', required=True)
parser.add_argument('--cms-uid', required=True)
parser.add_argument('--search-uid', required=True)
parser.add_argument('--kubeconfig', required=True)
parser.add_argument('--apply', action='store_true')
parser.add_argument('--confirm', default='')
args = parser.parse_args()
if args.namespace != 'mizuki' and not re.fullmatch(r'shirone-recovery-test-[a-f0-9]{12}', args.namespace):
    raise SystemExit('unsupported namespace')
if args.apply and args.confirm != 'discard-rebuildable-search-index':
    raise SystemExit('explicit search-index discard confirmation is required')


def kube(*cmd):
    argv = ['kubectl', '--request-timeout=15s', '--kubeconfig=' + args.kubeconfig,
            '-n', args.namespace, *cmd]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=25)
    if proc.returncode:
        raise RuntimeError('kubectl operation failed: ' + ' '.join(cmd[:3]) + ': ' + proc.stderr.strip())
    return proc.stdout


def obj(kind, name=None):
    return json.loads(kube('get', kind, *([name] if name else []), '-o', 'json'))


def deployment(name, uid, replicas):
    d = obj('deployment', name)
    if d['metadata']['uid'] != uid or d['spec'].get('replicas', 1) != replicas:
        raise RuntimeError(name + ': unexpected identity or desired replicas')
    if d['spec'].get('strategy', {}).get('type') != 'Recreate':
        raise RuntimeError(name + ': requires Recreate strategy')
    expected = {'app': name}
    if d['spec'].get('selector') != {'matchLabels': expected} or any(
        d['spec']['template']['metadata']['labels'].get(k) != v for k, v in expected.items()
    ):
        raise RuntimeError(name + ': unsafe selector')
    return d


def inspect_search_pod_spec(pod):
    volumes = {v['name']: v for v in pod.get('volumes', [])}
    containers = {c['name']: c for c in pod.get('containers', [])}
    if set(containers) != {'search', 'restricted-key-reconciler'}:
        raise RuntimeError('search: unexpected containers')
    if 'emptyDir' not in volumes.get('data', {}) or 'persistentVolumeClaim' in volumes['data']:
        raise RuntimeError('search: data is not emptyDir')
    mounts = containers['search'].get('volumeMounts', [])
    if not any(m['name'] == 'data' and m['mountPath'] == '/meili_data' for m in mounts):
        raise RuntimeError('search: emptyDir mount mismatch')


def no_hpa():
    hpas = obj('hpa')['items']
    for h in hpas:
        ref = h.get('spec', {}).get('scaleTargetRef', {})
        if ref.get('kind') == 'Deployment' and ref.get('name') in {'shirone-cms', 'shirone-search'}:
            raise RuntimeError('HPA controls ' + ref['name'])


def owned_pods(deploy_name, deploy_uid):
    sets = obj('replicasets')['items']
    owned = {r['metadata']['uid'] for r in sets if any(
        owner.get('uid') == deploy_uid and owner.get('controller') is True
        for owner in r['metadata'].get('ownerReferences', [])
    )}
    pods = obj('pods')['items']
    matches = [p for p in pods if p['metadata'].get('labels', {}).get('app') == deploy_name]
    for p in matches:
        refs = [r for r in p['metadata'].get('ownerReferences', []) if r.get('controller') is True]
        if len(refs) != 1 or refs[0].get('uid') not in owned:
            raise RuntimeError(deploy_name + ': conflicting or unowned matching pod')
    return matches


def ready(p):
    return (not p['metadata'].get('deletionTimestamp') and p.get('status', {}).get('phase') == 'Running'
            and any(c.get('type') == 'Ready' and c.get('status') == 'True'
                    for c in p.get('status', {}).get('conditions', [])))


def node_uid_when_ready(name):
    if not name:
        raise RuntimeError('workload Pod has no assigned node')
    node = obj('node', name)
    if not any(c.get('type') == 'Ready' and c.get('status') == 'True'
               for c in node.get('status', {}).get('conditions', [])):
        raise RuntimeError('workload node is not Ready: ' + name)
    return node['metadata']['uid']


def wait_for(fn, label, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(2)
    raise RuntimeError('timed out while waiting for ' + label + '; inspect state before recovery')


def scale(name, uid, before, after, dry=False):
    d = deployment(name, uid, before)
    patch = [
        {'op': 'test', 'path': '/metadata/uid', 'value': uid},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': d['metadata']['resourceVersion']},
        {'op': 'test', 'path': '/spec/replicas', 'value': before},
        {'op': 'replace', 'path': '/spec/replicas', 'value': after},
    ]
    kube('patch', 'deployment', name, '--type=json', *(['--dry-run=server'] if dry else []),
         '-p', json.dumps(patch), '-o', 'name')


def main():
    cms = deployment('shirone-cms', args.cms_uid, 1)
    search = deployment('shirone-search', args.search_uid, 1)
    inspect_search_pod_spec(search['spec']['template']['spec'])
    no_hpa()
    old = {}
    old_nodes = {}
    for name, uid in [('shirone-cms', args.cms_uid), ('shirone-search', args.search_uid)]:
        pods = owned_pods(name, uid)
        if len(pods) != 1 or not ready(pods[0]):
            raise RuntimeError(name + ': expected one ready owned pod')
        if name == 'shirone-search':
            inspect_search_pod_spec(pods[0]['spec'])
        old[name] = pods[0]['metadata']['uid']
        node_name = pods[0]['spec'].get('nodeName')
        old_nodes[node_name] = node_uid_when_ready(node_name)
    def require_old_nodes_ready():
        for node_name, node_uid in old_nodes.items():
            if node_uid_when_ready(node_name) != node_uid:
                raise RuntimeError('workload node identity changed: ' + node_name)
    # Admission checks are not writes, even when --apply is supplied.
    scale('shirone-cms', args.cms_uid, 1, 0, dry=True)
    scale('shirone-search', args.search_uid, 1, 0, dry=True)
    print(json.dumps({'phase': 'validated', 'namespace': args.namespace,
                      'cms_pod': old['shirone-cms'], 'search_pod': old['shirone-search']}), flush=True)
    if not args.apply:
        return
    require_old_nodes_ready()
    scale('shirone-cms', args.cms_uid, 1, 0)
    def cms_gone():
        require_old_nodes_ready()
        return not owned_pods('shirone-cms', args.cms_uid)
    wait_for(cms_gone, 'CMS writer termination')
    print(json.dumps({'phase': 'cms_fenced'}), flush=True)
    def require_fenced_cms():
        require_old_nodes_ready()
        deployment('shirone-cms', args.cms_uid, 0)
        if owned_pods('shirone-cms', args.cms_uid):
            raise RuntimeError('CMS writer is no longer fenced')
        no_hpa()
    require_fenced_cms()
    scale('shirone-search', args.search_uid, 1, 0)
    def search_gone():
        require_fenced_cms()
        return not owned_pods('shirone-search', args.search_uid)
    wait_for(search_gone, 'old search pod termination')
    print(json.dumps({'phase': 'old_search_removed'}), flush=True)
    require_fenced_cms()
    scale('shirone-search', args.search_uid, 0, 1)
    def replacement(name, uid):
        pods = owned_pods(name, uid)
        return pods[0] if len(pods) == 1 and ready(pods[0]) and pods[0]['metadata']['uid'] != old[name] else None
    def ready_search():
        require_fenced_cms()
        return replacement('shirone-search', args.search_uid)
    new_search = wait_for(ready_search, 'new search pod readiness')
    require_fenced_cms()
    deployment('shirone-search', args.search_uid, 1)
    current_search = replacement('shirone-search', args.search_uid)
    if not current_search or current_search['metadata']['uid'] != new_search['metadata']['uid']:
        raise RuntimeError('replacement search Pod changed during recovery')
    node_uid_when_ready(current_search['spec'].get('nodeName'))
    print(json.dumps({'phase': 'search_ready', 'pod': new_search['metadata']['uid']}), flush=True)
    scale('shirone-cms', args.cms_uid, 0, 1)
    new_cms = wait_for(lambda: replacement('shirone-cms', args.cms_uid), 'new CMS pod readiness')
    print(json.dumps({'phase': 'workloads_restored', 'cms_pod': new_cms['metadata']['uid'],
                      'search_pod': new_search['metadata']['uid'],
                      'next': 'verify key reconciler, search marker, published Chinese search, and draft isolation'}), flush=True)


try:
    main()
except (RuntimeError, subprocess.SubprocessError, KeyError, ValueError) as error:
    print(json.dumps({'phase': 'hold', 'error': str(error),
                      'next': 'inspect current replicas, pods and UIDs; do not repeat a stale patch'}), file=sys.stderr, flush=True)
    raise SystemExit(1)
