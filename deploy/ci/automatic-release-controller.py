#!/usr/bin/env python3
"""Inactive candidate controller; use only in an isolated test environment.

The reviewed main branch is the trust boundary. A caller must provide a
dedicated, clean checkout, private durable state and the protected previous
release record. This command is intentionally not scheduled or production-ready.
"""
import argparse
import importlib.util
import re
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


gate = load('automatic-release-gate')
download = load('automatic-release-download')
transaction = load('automatic-release-transaction')
adapter_module = load('automatic-release-adapter')


class DownloadUnavailable(RuntimeError):
    """Download failed before any release transaction was started."""


def run(run_id, checkout, state, key, recipient, previous_record):
    if type(run_id) is not int or run_id < 1:
        raise ValueError('invalid run ID')
    run_info = gate.gh(f'repos/{gate.REPO}/actions/runs/{run_id}')
    source, workflow_id, attempt = run_info.get('head_sha'), run_info.get('workflow_id'), run_info.get('run_attempt')
    if (run_info.get('id') != run_id or run_info.get('head_branch') != 'main' or
            run_info.get('status') != 'completed' or run_info.get('conclusion') != 'success' or
            run_info.get('event') not in ('push', 'workflow_dispatch') or
            not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{40}', source) or
            type(workflow_id) is not int or workflow_id < 1 or type(attempt) is not int or attempt < 1):
        raise ValueError('run is not an eligible main release')
    artifacts = gate.gh(f'repos/{gate.REPO}/actions/runs/{run_id}/artifacts?per_page=100')
    if type(artifacts.get('total_count')) is not int or artifacts['total_count'] > 100:
        raise ValueError('unexpected artifact list')
    name = f'shirone-encrypted-release-{run_id}-{attempt}'
    candidates = [x for x in artifacts.get('artifacts', []) if x.get('name') == name and x.get('expired') is False]
    if len(candidates) != 1 or type(candidates[0].get('id')) is not int:
        raise ValueError('run has no unique release artifact')
    artifact_id = candidates[0]['id']
    # GitHub CLI downloads by unique run/name. The gate verifies that exact ID
    # belongs to this run and attempt before accepting the file attestations.
    with tempfile.TemporaryDirectory(prefix='shirone-controller-download-', dir=state) as scratch:
        try:
            artifact = download.download(run_id, attempt, scratch, artifact_id)
        except download.TransportUnavailable:
            # Only classified transient transport failures are retryable; a
            # mismatched artifact, authentication failure or integrity error
            # remains held for explicit reconciliation.
            raise DownloadUnavailable('release download unavailable') from None
        policy = {'source': source, 'signer': source, 'workflow_id': workflow_id}
        adapter = adapter_module.Adapter(checkout, policy, artifact_id, artifact / 'metadata.json',
                                         artifact / 'release.age', key, recipient, previous_record)
        try:
            transaction.execute(adapter, state, run_id, source)
        except adapter_module.gate.ApiUnavailable:
            # The adapter imports its own isolated copy of the gate. Translate
            # its transient exception to the controller's gate type; poll still
            # checks that no transaction was reserved before retrying.
            raise gate.ApiUnavailable('release admission API unavailable') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-test-only', action='store_true', required=True,
                        help='explicitly acknowledge that this candidate is not production-ready')
    parser.add_argument('--run-id', type=int, required=True)
    for name in ('checkout', 'state', 'key', 'previous-record'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--recipient', required=True)
    args = parser.parse_args()
    run(args.run_id, args.checkout, args.state, args.key, args.recipient, args.previous_record)


if __name__ == '__main__':
    main()
