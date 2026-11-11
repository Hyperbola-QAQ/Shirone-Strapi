#!/usr/bin/env python3
"""Create and push one guarded image-only manifest commit. No deployment.

The caller must already hold the release lock, verify the signed release, and
use an isolated controller checkout. If any operation fails, inspect local and
remote Git state; a failed push may have succeeded before its response was lost.
"""
import re
import subprocess
from pathlib import Path

SHA = re.compile(r'^[0-9a-f]{40}$')


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()


def remote_head(repo):
    fields = git(repo, 'ls-remote', '--exit-code', 'origin', 'refs/heads/main').split()
    if len(fields) != 2 or fields[1] != 'refs/heads/main' or not SHA.fullmatch(fields[0]):
        raise ValueError('unexpected remote main')
    return fields[0]


def commit_manifest(repo, component, expected_head, old, new):
    repo = Path(repo)
    if component not in ('cms', 'ssr') or not SHA.fullmatch(expected_head):
        raise ValueError('invalid release identity')
    image = re.compile(rf'^192\.168\.86\.100:30500/shirone/{component}@sha256:[0-9a-f]{{64}}$')
    if not image.fullmatch(old) or not image.fullmatch(new) or old == new:
        raise ValueError('invalid image identity')
    if git(repo, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('checkout must be clean')
    if git(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD') != 'main' or git(repo, 'rev-parse', 'HEAD') != expected_head:
        raise ValueError('unexpected local main')
    if remote_head(repo) != expected_head:
        raise ValueError('remote main changed')
    relative = f'deploy/k8s/{component}.yaml'
    path = repo / relative
    if path.is_symlink() or not path.is_file():
        raise ValueError('manifest must be a regular file')
    text = path.read_text()
    expression = re.compile(r'^([ \t]*image:[ \t]*)([^\s]+)([ \t]*)$', re.MULTILINE)
    matches = list(expression.finditer(text))
    if len(matches) != 1 or matches[0].group(2) != old:
        raise ValueError('manifest does not contain exactly the expected image')
    found = matches[0]
    expected_text = text[:found.start(2)] + new + text[found.end(2):]
    path.write_text(expected_text)
    git(repo, 'add', '--', relative)
    git(repo, 'commit', '-m', f'deploy: update signed {component} image')
    new_head = git(repo, 'rev-parse', 'HEAD')
    parents = git(repo, 'rev-list', '--parents', '-n', '1', new_head).split()
    changed = git(repo, 'diff-tree', '--no-commit-id', '--name-only', '-r', expected_head, new_head).splitlines()
    actual_blob = subprocess.run(['git', '-C', str(repo), 'show', f'{new_head}:{relative}'], check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    if (parents != [new_head, expected_head] or changed != [relative] or actual_blob != expected_text.encode() or
            git(repo, 'status', '--porcelain', '--untracked-files=all')):
        raise ValueError('commit contains unexpected changes')
    # The explicit lease is a server-enforced CAS even if someone rewound
    # main; the verified direct-parent relation also guarantees fast-forward.
    git(repo, 'push', f'--force-with-lease=refs/heads/main:{expected_head}', 'origin', 'HEAD:refs/heads/main')
    if remote_head(repo) != new_head:
        raise ValueError('remote main changed after push')
    return new_head
