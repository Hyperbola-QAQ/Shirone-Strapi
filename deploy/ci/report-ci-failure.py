#!/usr/bin/env python3
"""Report fixed diagnostic categories without printing private build logs."""
import json
import sys

STAGES = frozenset(('bases', 'base_pull', 'base_pull_node', 'base_pull_postgres', 'node_download', 'node_base_build',
                    'pg_base_build', 'base_checks', 'meili_pull',
                    'dependency_install', 'source_tests', 'oci_cms', 'oci_ssr',
                    'oci_reconciler', 'integration_content', 'integration_media',
                    'integration_recovery', 'envelope', 'encrypt'))
PATTERNS = {
    'network_timeout': ('timed out', 'timeout', 'deadline exceeded'),
    'network_connection': ('connection reset', 'connection refused', 'unexpected eof', 'no such host'),
    'disk_space': ('no space left on device', 'disk quota exceeded'),
    'access_denied': ('permission denied', 'unauthorized', '403 forbidden', '401 unauthorized'),
    'integrity': ('checksum mismatch', 'integrity check failed', 'digest mismatch'),
}


def report(path):
    stage = 'unknown'
    found = set()
    with open(path, 'rb') as f:
        for raw in f:
            # Truncate a single line before decoding to avoid excessive memory.
            line = raw[:65536].decode('utf-8', 'replace').strip()
            if line.startswith('SHIRONE_STAGE=') and line[14:] in STAGES:
                stage = line[14:]
            lowered = line.lower()
            for name, patterns in PATTERNS.items():
                if any(pattern in lowered for pattern in patterns):
                    found.add(name)
    return {'stage': stage, 'categories': sorted(found) or ['unclassified']}


if __name__ == '__main__':
    try:
        print(json.dumps(report(sys.argv[1]), sort_keys=True))
    except (OSError, IndexError):
        print('{"categories":["unavailable"],"stage":"unknown"}')
