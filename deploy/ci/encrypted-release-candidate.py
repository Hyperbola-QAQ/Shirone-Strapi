#!/usr/bin/env python3
"""Inactive age + GitHub attestation candidate; never invokes deployment.

Decrypt always invokes GitHub's cryptographic attestation verifier first.
The caller must independently trust the signer commit, recipient and image
identities. Local tests use a mock verifier and do not prove real signatures.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_envelope', HERE / 'release-envelope-candidate.py')
envelope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envelope)
REPO = 'Hyperbola-QAQ/Shirone-Strapi'
WORKFLOW = REPO + '/.github/workflows/shirone-release.yml'
RECIPIENT = re.compile(r'^age1[023456789acdefghjklmnpqrstuvwxyz]{58}$')
MAX_CIPHERTEXT = envelope.MAX_TOTAL + 128 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def recipient_fingerprint(public_key):
    require(bool(RECIPIENT.fullmatch(public_key)), 'expected one native age X25519 public key')
    return 'sha256:' + hashlib.sha256(public_key.encode('ascii')).hexdigest()


def fd_hash(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    h = hashlib.sha256()
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        h.update(block)
    os.lseek(fd, 0, os.SEEK_SET)
    return 'sha256:' + h.hexdigest()


def input_fd(path, max_size):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and 0 < st.st_size <= max_size, 'input must be a bounded regular file')
        return fd
    except Exception:
        os.close(fd)
        raise


def private_key_fd(path):
    fd = input_fd(path, 65536)
    try:
        st = os.fstat(fd)
        require(st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) & 0o077 == 0,
                'private key must be owned by the caller and inaccessible to group/others')
        return fd
    except Exception:
        os.close(fd)
        raise


def private_output(path):
    parent = Path(path).absolute().parent
    st = parent.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and
            stat.S_IMODE(st.st_mode) & 0o077 == 0, 'output parent must be a caller-owned private directory')
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)


def run_silent(argv, **kwargs):
    # Tools can print credentials, filenames or content on failures. Never echo
    # their output; surface only a fixed error category.
    result = subprocess.run(argv, stdout=kwargs.pop('stdout', subprocess.DEVNULL),
                            stderr=subprocess.DEVNULL, check=False, **kwargs)
    require(result.returncode == 0, 'external verification or cryptographic operation failed')


def verify_attestation(fd, source, signer):
    require(bool(envelope.COMMIT.fullmatch(source)) and bool(envelope.COMMIT.fullmatch(signer)), 'full commits required')
    run_silent(['gh', 'attestation', 'verify', f'/proc/self/fd/{fd}', '--repo', REPO,
                '--signer-workflow', WORKFLOW, '--source-ref', 'refs/heads/main',
                '--source-digest', source, '--signer-digest', signer,
                '--deny-self-hosted-runners'], pass_fds=(fd,))


def encrypt(plaintext, ciphertext, recipient, source, expected_manifests):
    fingerprint = recipient_fingerprint(recipient)
    fd = input_fd(plaintext, envelope.MAX_TOTAL + 1024 * 1024)
    try:
        before = fd_hash(fd)
        # No extraction here. OCI files were inspected during envelope build;
        # explicit expected manifest identities prevent selecting a stale build.
        envelope.inspect(fd, source, fingerprint, expected_manifests)
        require(fd_hash(fd) == before, 'plaintext changed during inspection')
        output = private_output(ciphertext)
        try:
            with os.fdopen(output, 'wb') as out:
                run_silent(['age', '--encrypt', '--recipient', recipient], stdin=fd, stdout=out, pass_fds=(fd,))
        except Exception:
            # Never delete a caller file, including a partial output; the new
            # private output must be inspected/removed by its owner.
            raise
        require(fd_hash(fd) == before, 'plaintext changed during encryption')
        return before
    finally:
        os.close(fd)


def decrypt(ciphertext, plaintext, key, recipient, source, signer, ciphertext_digest, expected_manifests):
    fingerprint = recipient_fingerprint(recipient)
    require(bool(envelope.DIGEST.fullmatch(ciphertext_digest)), 'expected ciphertext SHA-256 required')
    fd = input_fd(ciphertext, MAX_CIPHERTEXT)
    try:
        require(fd_hash(fd) == ciphertext_digest, 'unexpected ciphertext identity')
        verify_attestation(fd, source, signer)
        require(fd_hash(fd) == ciphertext_digest, 'ciphertext changed after attestation verification')
        # Key and output are opened only after the signed ciphertext passed.
        keyfd = private_key_fd(key)
        try:
            output = private_output(plaintext)
            with os.fdopen(output, 'wb') as out:
                run_silent(['age', '--decrypt', '--identity', f'/proc/self/fd/{keyfd}'],
                           stdin=fd, stdout=out, pass_fds=(fd, keyfd))
        finally:
            os.close(keyfd)
        require(fd_hash(fd) == ciphertext_digest, 'ciphertext changed during decryption')
        # age must finish successfully before parsing any plaintext. Inspection
        # does not extract OCI content and is not itself release authorization.
        envelope.inspect(plaintext, source, fingerprint, expected_manifests)
        return 'Verified attestation, complete age decryption and envelope identities; OCI extraction/validation remains required.'
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    for name in ('encrypt', 'decrypt'):
        p = sub.add_parser(name)
        p.add_argument('--plaintext', required=True)
        p.add_argument('--ciphertext', required=True)
        p.add_argument('--recipient', required=True)
        p.add_argument('--source', required=True)
        for component in envelope.COMPONENTS:
            p.add_argument('--' + component + '-manifest', required=True)
        if name == 'decrypt':
            p.add_argument('--identity', required=True)
            p.add_argument('--signer', required=True)
            p.add_argument('--ciphertext-digest', required=True)
    args = parser.parse_args()
    manifests = {n: getattr(args, n + '_manifest') for n in envelope.COMPONENTS}
    try:
        if args.operation == 'encrypt':
            print('Plaintext archive identity:', encrypt(args.plaintext, args.ciphertext, args.recipient, args.source, manifests))
        else:
            print(decrypt(args.ciphertext, args.plaintext, args.identity, args.recipient, args.source,
                          args.signer, args.ciphertext_digest, manifests))
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, tarfile.TarError, subprocess.SubprocessError) as exc:
        print(f'Encrypted release candidate failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
