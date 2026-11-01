#!/usr/bin/env python3
"""Delete one exact owned test/production Gateway object with a UID precondition."""
import json
import re
import subprocess
import sys
import urllib.request

ALLOWED = {
    ('higress-system', 'deployment', 'shirone-ipv4-gateway'): '/apis/apps/v1/namespaces/higress-system/deployments/',
    ('higress-system', 'service', 'shirone-ipv4-gateway'): '/api/v1/namespaces/higress-system/services/',
    ('higress-system', 'gateway', 'shirone-ipv4'): '/apis/gateway.networking.k8s.io/v1/namespaces/higress-system/gateways/',
    ('higress-system', 'networkpolicy', 'shirone-ipv4-gateway-ingress'): '/apis/networking.k8s.io/v1/namespaces/higress-system/networkpolicies/',
    ('higress-system', 'envoyfilter', 'shirone-ipv4-proxy-protocol'): '/apis/networking.istio.io/v1alpha3/namespaces/higress-system/envoyfilters/',
    ('higress-system', 'envoyfilter', 'shirone-ipv4-response-buffer'): '/apis/networking.istio.io/v1alpha3/namespaces/higress-system/envoyfilters/',
    ('mizuki', 'networkpolicy', 'shirone-cms-ipv4-gateway-ingress'): '/apis/networking.k8s.io/v1/namespaces/mizuki/networkpolicies/',
    ('mizuki', 'httproute', 'shirone-ipv4-site'): '/apis/gateway.networking.k8s.io/v1/namespaces/mizuki/httproutes/',
    ('mizuki', 'httproute', 'shirone-ipv4-admin'): '/apis/gateway.networking.k8s.io/v1/namespaces/mizuki/httproutes/',
}
if len(sys.argv) != 5 or tuple(sys.argv[1:4]) not in ALLOWED or not re.fullmatch(r'[0-9a-f-]{36}', sys.argv[4]):
    raise SystemExit('invalid exact resource or UID')
namespace, kind, name, uid = sys.argv[1:]
kube = '/home/hyperbola/.kube/config'
obj = json.loads(subprocess.check_output(['kubectl', '--request-timeout=15s', '--kubeconfig='+kube, '-n', namespace, 'get', kind, name, '-o', 'json'], timeout=20))
if obj['metadata']['uid'] != uid or obj['metadata'].get('labels', {}).get('app.kubernetes.io/part-of') != 'shirone-strapi':
    raise SystemExit('UID or ownership differs')
p = subprocess.Popen(['kubectl', '--kubeconfig='+kube, 'proxy', '--address=127.0.0.1', '--port=0'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    line = p.stdout.readline().strip()
    m = re.fullmatch(r'Starting to serve on 127\.0\.0\.1:(\d+)', line)
    if not m:
        raise RuntimeError('local API proxy did not start')
    url = 'http://127.0.0.1:' + m.group(1) + ALLOWED[(namespace, kind, name)] + name
    body = json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}, 'propagationPolicy': 'Foreground'}).encode()
    request = urllib.request.Request(url, data=body, method='DELETE', headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status not in (200, 202):
            raise RuntimeError('deletion was not accepted')
    print(kind + '/' + name + ' deleted with UID precondition')
finally:
    p.terminate()
    p.wait(timeout=10)
