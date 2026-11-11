#!/usr/bin/env python3
"""Single polling iteration for the isolated release-controller candidate.

This is not an installed service.  It requires a dedicated checkout, protected
state and existing controller credentials; never point it at a personal tree.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_controller', HERE / 'automatic-release-controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
SHA = re.compile(r'[0-9a-f]{40}')
ORIGIN = 'git@github.com:Hyperbola-QAQ/Shirone-Strapi.git'


def git(checkout, *args):
    env = dict(os.environ, GIT_SSH_COMMAND='ssh -o BatchMode=yes -o StrictHostKeyChecking=yes')
    return subprocess.run(['git', *args], cwd=checkout, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, env=env).stdout.strip()


def eligible_run(response, head):
    if not isinstance(response, dict) or not isinstance(response.get('workflow_runs'), list):
        raise ValueError('invalid workflow response')
    runs = [x for x in response['workflow_runs'] if isinstance(x, dict)
            and x.get('head_sha') == head and x.get('head_branch') == 'main'
            and x.get('event') in ('push', 'workflow_dispatch')
            and x.get('status') == 'completed' and x.get('conclusion') == 'success'
            and type(x.get('id')) is int and x['id'] > 0]
    return max((x['id'] for x in runs), default=None)


def clear_hold(state):
    (state / 'poll-hold').unlink()
    directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def retry_download_if_safe(checkout, state, head, run_id):
    """Allow at most two later polls after a known pre-transaction failure."""
    if (os.path.lexists(state / f'run-{run_id}.json') or
            os.path.lexists(state / f'source-{head}.json') or
            any(state.glob(f'run-{run_id}-*.json')) or
            git(checkout, 'status', '--porcelain', '--untracked-files=all') or
            git(checkout, 'rev-parse', 'HEAD') != head):
        return
    for attempt in range(1, 4):
        try:
            fd = os.open(state / f'download-failure-{head}-{attempt}',
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, 'w') as file:
            file.write(f'{run_id}\n')
            file.flush()
            os.fsync(file.fileno())
        directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        if attempt < 3:
            clear_hold(state)
        return


def poll(checkout, state, key, recipient_file, previous_record):
    checkout, state = Path(checkout), Path(state)
    info = state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('state directory must be private')
    fd = os.open(state / 'poll.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        lock = os.fstat(fd)
        if not stat.S_ISREG(lock.st_mode) or lock.st_uid != os.getuid() or lock.st_mode & 0o077:
            raise ValueError('invalid poll lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (state / 'poll-hold').exists():
            raise ValueError('previous polling attempt needs reconciliation')
        if git(checkout, 'remote', 'get-url', 'origin') != ORIGIN:
            raise ValueError('unexpected origin')
        if git(checkout, 'symbolic-ref', '--quiet', '--short', 'HEAD') != 'main':
            raise ValueError('main checkout required')
        if git(checkout, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('checkout is not clean')
        original_head = git(checkout, 'rev-parse', 'HEAD')
        git(checkout, 'fetch', '--no-tags', 'origin', 'main')
        git(checkout, 'merge', '--ff-only', 'FETCH_HEAD')
        head = git(checkout, 'rev-parse', 'HEAD')
        if not SHA.fullmatch(head):
            raise ValueError('invalid source')
        # The caller and imported controller were loaded before the fetch.
        # Restart on a later poll instead of executing a mixed source version.
        if head != original_head:
            return 'checkout updated; retry next poll'
        response = controller.gate.gh('repos/Hyperbola-QAQ/Shirone-Strapi/actions/workflows/shirone-release.yml/runs?branch=main&status=success&per_page=100')
        run_id = eligible_run(response, head)
        if run_id is None:
            return 'no matching release'
        reservation = state / f'source-{head}.json'
        if reservation.exists():
            receipt = json.loads(reservation.read_text())
            reserved_run = receipt.get('run_id') if isinstance(receipt, dict) else None
            if type(reserved_run) is int and reserved_run > 0 and (state / f'run-{reserved_run}-complete.json').is_file():
                return 'already completed'
            raise ValueError('existing reservation needs reconciliation')
        recipient = Path(recipient_file).read_text().strip()
        if not re.fullmatch(r'age1[023456789acdefghjklmnpqrstuvwxyz]{58}', recipient):
            raise ValueError('invalid recipient')
        hold = os.open(state / 'poll-hold', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(hold, 'w') as file:
            file.write(f'{run_id} {head}\n')
            file.flush()
            os.fsync(file.fileno())
        directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        # The marker deliberately survives every failure or interruption. The
        # controller reserves the source before promotion and never retries it.
        try:
            controller.run(run_id, checkout, state, key, recipient, previous_record)
        except controller.DownloadUnavailable:
            retry_download_if_safe(checkout, state, head, run_id)
            raise
        clear_hold(state)
        return 'completed'
    finally:
        os.close(fd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-test-only', required=True, action='store_true')
    for name in ('checkout', 'state', 'key', 'recipient-file', 'previous-record'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if (args.checkout.resolve() / 'deploy/ci') != HERE:
        raise ValueError('poll script must run from its own controller checkout')
    print(poll(args.checkout, args.state, args.key, args.recipient_file, args.previous_record))
