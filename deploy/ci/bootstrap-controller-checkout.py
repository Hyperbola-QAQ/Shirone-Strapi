#!/usr/bin/env python3
"""Prepare a new dedicated checkout from a reviewed public main commit.

The HTTPS clone is anonymous. This does not provision or read credentials,
connect through SSH, push, generate identities, or start a controller.
"""
import argparse
import ctypes
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

PUBLIC_URL = 'https://github.com/Hyperbola-QAQ/Shirone-Strapi.git'
PUSH_URL = 'git@github.com:Hyperbola-QAQ/Shirone-Strapi.git'
SHA = re.compile(r'^[0-9a-f]{40}$')


def run(*args, cwd=None):
    # Git exposes many redirection variables (including GIT_COMMON_DIR). An
    # allow-by-exception list can silently redirect operations into another
    # repository, so remove every inherited Git override before adding the few
    # fixed safety settings below.
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('GIT_') and name not in ('SSH_ASKPASS', 'NETRC')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0',
               GIT_SSH_COMMAND='ssh -o BatchMode=yes -o StrictHostKeyChecking=yes')
    # libcurl may read HOME/.netrc even with credential helpers disabled. Give
    # each subprocess an owner-only empty home rather than the user's home.
    with tempfile.TemporaryDirectory(prefix='shirone-git-home-') as empty_home:
        env.update(HOME=empty_home, XDG_CONFIG_HOME=empty_home, CURL_HOME=empty_home)
        return subprocess.run(args, cwd=cwd, env=env, check=True, timeout=180,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout.strip()


def rename_noreplace(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, 'renameat2'):
        raise OSError('atomic no-replace rename is unavailable')
    libc.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def prepare(destination, expected, author_name, author_email):
    destination = Path(destination)
    if not destination.is_absolute() or not SHA.fullmatch(expected):
        raise ValueError('absolute destination and reviewed full commit required')
    if (not re.fullmatch(r'[^\x00-\x1f\x7f<>]{1,100}', author_name) or
            not re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+', author_email)):
        raise ValueError('explicit valid commit author required')
    parent = destination.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('checkout parent must be private')
    if os.path.lexists(destination):
        raise FileExistsError('checkout already exists')
    with tempfile.TemporaryDirectory(prefix='.checkout-', dir=parent) as scratch:
        checkout = Path(scratch) / 'repo'
        run('git', '-c', 'credential.helper=', 'clone', '--no-hardlinks', '--no-tags', '--single-branch',
            '--branch', 'main', '--', PUBLIC_URL, str(checkout))
        if (run('git', '-C', str(checkout), 'rev-parse', 'HEAD') != expected or
                run('git', '-C', str(checkout), 'symbolic-ref', '--short', 'HEAD') != 'main' or
                run('git', '-C', str(checkout), 'status', '--porcelain', '--untracked-files=all')):
            raise ValueError('public main does not match the reviewed commit')
        run('git', '-C', str(checkout), 'remote', 'set-url', 'origin', PUSH_URL)
        run('git', '-C', str(checkout), 'config', '--local', 'user.name', author_name)
        run('git', '-C', str(checkout), 'config', '--local', 'user.email', author_email)
        run('git', '-C', str(checkout), 'config', '--local', 'core.hooksPath', '/dev/null')
        if run('git', '-C', str(checkout), 'remote', 'get-url', 'origin') != PUSH_URL:
            raise ValueError('unexpected push origin')
        rename_noreplace(checkout, destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--author-name', required=True)
    parser.add_argument('--author-email', required=True)
    parser.add_argument('--prepare', action='store_true', required=True)
    args = parser.parse_args()
    prepare(args.destination, args.expected, args.author_name, args.author_email)
