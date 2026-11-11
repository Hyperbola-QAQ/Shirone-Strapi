#!/usr/bin/env python3
"""Exclusive controller lock and write-once run reservation; no deployment."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat

SHA = re.compile(r'^[0-9a-f]{40}$')


class ReleaseLock:
    """Keep this object alive until all promotion and cleanup actions finish.

    A reservation is never removed automatically: a failed or interrupted run
    requires reconciliation, and must not be replayed merely because its lock
    was released by process exit. Use a private, durable controller directory.
    """
    def __init__(self, directory):
        self.path = Path(directory)
        self.directory = None
        self.lock = None

    def __enter__(self):
        self.directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(self.directory)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('controller directory must be private and owned by the controller')
            self.lock = os.open('release.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                                0o600, dir_fd=self.directory)
            info = os.fstat(self.lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('controller lock is not private')
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def reserve(self, run_id, source):
        if self.lock is None or type(run_id) is not int or run_id < 1 or not isinstance(source, str) or not SHA.fullmatch(source):
            raise ValueError('invalid reservation')
        payload = json.dumps({'schema': 1, 'run_id': run_id, 'source': source}, sort_keys=True).encode() + b'\n'
        name = f'run-{run_id}.json'
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self.directory)
        try:
            with os.fdopen(fd, 'wb', closefd=True) as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.fsync(self.directory)
        except BaseException:
            # A possibly incomplete receipt intentionally remains: the
            # operator must reconcile it instead of permitting a blind replay.
            raise

    def __exit__(self, *_):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
        if self.directory is not None:
            os.close(self.directory)
            self.directory = None
