#!/usr/bin/env python3
"""Release transaction core for isolated adapters; not a production controller.

An adapter must implement admission, a verified snapshot, per-component
previews/promotions/checks, and guarded restoration. It must inspect actual
Git and cluster state during restoration and report success only after both
are verified restored. An intent may have succeeded even when its call raised.
"""
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('automatic_release_lock', HERE / 'automatic-release-lock.py')
locks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locks)


def record(lock, run_id, step, data=None):
    """Persist an intent or result before moving to the next operation."""
    name = f'run-{run_id}-{step}.json'
    payload = json.dumps({'step': step, 'data': data}, sort_keys=True).encode() + b'\n'
    if len(payload) > 16384:
        raise ValueError('transaction receipt is too large')
    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=lock.directory)
    with os.fdopen(fd, 'wb') as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())
    os.fsync(lock.directory)


def execute(adapter, directory, run_id, source):
    """Execute once; ambiguous interruptions require external reconciliation.

    Adapter implementations are trusted code and must fail closed, enforce
    independently reviewed source/signature, check current main, use the
    guarded Ansible entrypoints, and preserve credentials. No real adapter is
    provided here. Never install this core as an unattended service by itself.
    """
    with locks.ReleaseLock(directory) as lock:
        admission = adapter.admit(run_id, source)
        snapshot = adapter.snapshot(admission)
        # Snapshot must identify old images, deployment UIDs and original Git
        # state, and must establish schema compatibility for any restoration.
        if not isinstance(snapshot, dict) or snapshot.get('rollback_safe') is not True:
            raise ValueError('recovery compatibility was not established')
        adapter.preview(admission, snapshot)
        lock.reserve(run_id, source)
        record(lock, run_id, 'snapshot', snapshot)
        try:
            for component in ('cms', 'ssr'):
                record(lock, run_id, f'{component}-intent')
                adapter.promote(component, admission, snapshot)
                adapter.verify(component, admission, snapshot)
                record(lock, run_id, f'{component}-verified')
            adapter.verify_all(admission, snapshot)
            record(lock, run_id, 'complete')
        except Exception:
            # Inspect both Git and live deployments: the operation that raised
            # may have changed state before the response was lost.
            record(lock, run_id, 'restore-intent')
            try:
                adapter.restore(admission, snapshot)
                adapter.verify_restored(admission, snapshot)
                record(lock, run_id, 'restored')
            except Exception as restore_error:
                raise RuntimeError('Release failed; restoration is unverified and requires reconciliation') from restore_error
            raise RuntimeError('Release failed; previous state was restored and verified')
