#!/usr/bin/env python3
"""Reject obvious Git push failures without changing any remote or local ref.

A successful dry run does not prove server-side branch policy will accept a
real update. The caller must supply the same transient Git authentication used
for the subsequent release.
"""
import re
import subprocess
import sys


def command(*args, stdin=None):
    return subprocess.run(['git', *args], input=stdin, text=True, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()


def check():
    head = command('rev-parse', '--verify', 'HEAD')
    if not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ValueError('invalid Git head')
    if command('symbolic-ref', '--quiet', '--short', 'HEAD') != 'main':
        raise ValueError('main checkout required')
    if command('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('clean checkout required')
    remote = command('ls-remote', '--exit-code', 'origin', 'refs/heads/main').split()
    if remote != [head, 'refs/heads/main']:
        raise ValueError('remote main changed')
    tree = command('rev-parse', '--verify', 'HEAD^{tree}')
    temporary_commit = command('-c', 'user.name=Shirone release automation',
                               '-c', 'user.email=shirone-release@users.noreply.github.com',
                               'commit-tree', tree, '-p', head, stdin='Push permission preview\n')
    if not re.fullmatch(r'[0-9a-f]{40}', temporary_commit):
        raise ValueError('invalid preview commit')
    command('push', '--dry-run', f'--force-with-lease=refs/heads/main:{head}',
            'origin', f'{temporary_commit}:refs/heads/main')


if __name__ == '__main__':
    try:
        check()
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Git push preview failed; deployment stopped.', file=sys.stderr)
        raise SystemExit(1)
    print('Git push preview passed; server-side acceptance is not guaranteed.')
