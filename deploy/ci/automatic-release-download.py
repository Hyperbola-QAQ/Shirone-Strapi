#!/usr/bin/env python3
"""Download one exact GitHub workflow artifact to a private new directory.

This is transport only. Signature, ciphertext hash and OCI validation remain
mandatory. No credentials or command error bodies are printed.
"""
import os
from pathlib import Path
import stat
import subprocess
import tempfile

REPO = 'Hyperbola-QAQ/Shirone-Strapi'


def download(run_id, attempt, parent, attempts=2):
    if (type(run_id) is not int or run_id < 1 or type(attempt) is not int or attempt < 1 or
            type(attempts) is not int or not 1 <= attempts <= 3):
        raise ValueError('invalid download identity or retry count')
    parent = Path(parent)
    info = parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('download parent must be private')
    name = f'shirone-encrypted-release-{run_id}-{attempt}'
    for _ in range(attempts):
        # TemporaryDirectory owns only this attempt; failed partial downloads are
        # removed when the context exits, not arbitrary pre-existing files.
        with tempfile.TemporaryDirectory(prefix='artifact-attempt-', dir=parent) as scratch:
            try:
                result = subprocess.run(['gh', 'run', 'download', str(run_id), '--repo', REPO,
                                         '--name', name, '--dir', scratch],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        timeout=900, check=False)
            except subprocess.TimeoutExpired:
                continue
            if result.returncode != 0:
                continue
            items = list(Path(scratch).iterdir())
            if {p.name for p in items} != {'metadata.json', 'release.age'}:
                continue
            valid = True
            for path in items:
                item = path.lstat()
                limit = 16384 if path.name == 'metadata.json' else 4 * 1024 * 1024 * 1024
                if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or not 0 < item.st_size <= limit:
                    valid = False
                    break
            if not valid:
                continue
            final = parent / f'artifact-{run_id}-{attempt}'
            # An existing result may belong to another process or previous
            # attempt: never overwrite or delete it.
            os.mkdir(final, 0o700)
            try:
                for path in items:
                    os.rename(path, final / path.name)
            except BaseException:
                # Preserve a partial destination for explicit reconciliation.
                raise
            return final
    raise RuntimeError('artifact download failed; no release was authorized')
