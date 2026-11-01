#!/usr/bin/env python3
"""Create a separate, non-host-network gateway candidate from reviewed live input."""
import copy
import json
import sys

if len(sys.argv) != 3:
    raise SystemExit('expected source deployment UID and immutable image')
source = json.load(sys.stdin)
if (source.get('kind') != 'Deployment' or source.get('metadata', {}).get('name') != 'higress-gateway'
        or source['metadata'].get('namespace') != 'higress-system' or source['metadata'].get('uid') != sys.argv[1]):
    raise SystemExit('source gateway identity mismatch')
template = copy.deepcopy(source['spec']['template'])
labels = template['metadata']['labels']
if labels.get('app') != 'higress-gateway' or labels.get('ansible.kubernetes.io/higress-gateway') != 'true':
    raise SystemExit('source gateway labels differ')
containers = template['spec']['containers']
if len(containers) != 1 or containers[0].get('name') != 'higress-gateway' or containers[0].get('image') != sys.argv[2] or '@sha256:' not in sys.argv[2]:
    raise SystemExit('source gateway image or container differs')
labels['app'] = 'shirone-ipv4-higress'
labels.pop('ansible.kubernetes.io/higress-gateway')
template['spec']['hostNetwork'] = False
template['spec']['dnsPolicy'] = 'ClusterFirst'
template['spec'].pop('affinity', None)
for container in containers:
    for port in container.get('ports', []):
        if port.get('hostPort'):
            raise SystemExit('source requires a host port')
out = {
    'apiVersion': 'apps/v1', 'kind': 'Deployment',
    'metadata': {'name': 'shirone-ipv4-gateway', 'namespace': 'higress-system',
                 'labels': {'app.kubernetes.io/part-of': 'shirone-strapi'}},
    'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': 'shirone-ipv4-higress'}},
             'template': template},
}
json.dump(out, sys.stdout)
