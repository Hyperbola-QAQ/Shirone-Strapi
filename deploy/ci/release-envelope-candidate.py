#!/usr/bin/env python3
"""Local release envelope candidate, not attestation or production authorization.

The caller MUST verify the outer ciphertext attestation and finish authenticated
decryption before invoking inspect/extract. No network, keys or deletion.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import io

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('oci_candidate', HERE / 'verify-oci-archive-candidate.py')
oci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oci)
COMPONENTS = ('cms', 'ssr', 'reconciler')
FILES = {name: f'{name}.oci.tar' for name in COMPONENTS}
COMMIT = re.compile(r'^[0-9a-f]{40}$')
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')
MAX_METADATA = 65536
MAX_COMPONENT = 8 * 1024 * 1024 * 1024 - 1
MAX_TOTAL = 24 * 1024 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def check_identity(source, recipient):
    require(bool(COMMIT.fullmatch(source)), 'expected full source commit')
    require(bool(DIGEST.fullmatch(recipient)), 'expected full recipient public-key fingerprint')


def hash_stream(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    return 'sha256:' + digest.hexdigest()


def open_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_COMPONENT,
                'component must be a bounded regular file')
        return os.fdopen(fd, 'rb'), info
    except Exception:
        os.close(fd)
        raise


def open_envelope(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_TOTAL + 1024 * 1024,
                'envelope must be a bounded regular file')
        return os.fdopen(fd, 'rb')
    except Exception:
        os.close(fd)
        raise


def tar_entry(name, size):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.uid, info.gid, info.mtime = size, 0o600, 0, 0, 0
    return info


def build(output, source, recipient, components):
    check_identity(source, recipient)
    require(set(components) == set(COMPONENTS), 'exactly three components required')
    info = {}
    for name in COMPONENTS:
        path = components[name]
        with open_regular(path)[0] as stream:
            digest = hash_stream(stream)
            size = stream.tell()
        image = oci.verify(path)
        require(image['archive_digest'] == digest, 'component changed during inspection')
        info[name] = {'file': FILES[name], 'size': size, 'archive_digest': digest,
                      'manifest_digest': image['manifest_digest']}
    require(sum(x['size'] for x in info.values()) <= MAX_TOTAL, 'envelope exceeds total size limit')
    metadata = json.dumps({'version': 1, 'source_commit': source, 'recipient_fingerprint': recipient,
                           'components': info}, sort_keys=True, separators=(',', ':')).encode()
    require(len(metadata) <= MAX_METADATA, 'metadata exceeds size limit')
    # Exclusive create: never overwrite an existing file. On failure a partial
    # private file can remain; the caller must manage its own cleanup.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as out, tarfile.open(fileobj=out, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        tar.addfile(tar_entry('release.json', len(metadata)), io.BytesIO(metadata))
        for name in COMPONENTS:
            with open_regular(components[name])[0] as stream:
                require(hash_stream(stream) == info[name]['archive_digest'], 'component changed before packaging')
                stream.seek(0)
                tar.addfile(tar_entry(FILES[name], info[name]['size']), stream)
    # Re-read the complete result: no successful output on a packaging race.
    inspect(output, source, recipient, {n: info[n]['manifest_digest'] for n in COMPONENTS})
    return info


def no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, 'duplicate metadata key')
        out[key] = value
    return out


def inspect(envelope, source, recipient, expected_manifests, destination=None):
    check_identity(source, recipient)
    require(set(expected_manifests) == set(COMPONENTS) and
            all(DIGEST.fullmatch(expected_manifests[n]) for n in COMPONENTS), 'three expected manifest digests required')
    seen = {}
    with open_envelope(envelope) as outer, tarfile.open(fileobj=outer, mode='r:') as tar:
        for member in tar:
            require(member.name in ('release.json', *FILES.values()), 'unexpected or unsafe envelope path')
            require(member.name not in seen, 'duplicate envelope path')
            require(member.isfile() and not member.sparse, 'envelope members must be regular files')
            require(0 <= member.size <= (MAX_METADATA if member.name == 'release.json' else MAX_COMPONENT),
                    'envelope member exceeds size limit')
            seen[member.name] = member
        require(set(seen) == {'release.json', *FILES.values()}, 'missing envelope members')
        require(sum(seen[f].size for f in FILES.values()) <= MAX_TOTAL, 'envelope exceeds total size limit')
        with tar.extractfile(seen['release.json']) as stream:
            metadata = json.loads(stream.read(MAX_METADATA + 1), object_pairs_hook=no_duplicates)
        require(isinstance(metadata, dict) and set(metadata) == {'version', 'source_commit', 'recipient_fingerprint', 'components'}
                and type(metadata['version']) is int and metadata['version'] == 1,
                'unexpected metadata structure')
        require(metadata['source_commit'] == source and metadata['recipient_fingerprint'] == recipient,
                'unexpected source or recipient; possible replay')
        components = metadata['components']
        require(isinstance(components, dict) and set(components) == set(COMPONENTS), 'unexpected components')
        for name in COMPONENTS:
            item = components[name]
            require(isinstance(item, dict) and set(item) == {'file', 'size', 'archive_digest', 'manifest_digest'},
                    'unexpected component metadata')
            require(item['file'] == FILES[name] and type(item['size']) is int and
                    item['size'] == seen[FILES[name]].size and item['size'] > 0 and
                    isinstance(item['archive_digest'], str) and DIGEST.fullmatch(item['archive_digest']) and
                    item['manifest_digest'] == expected_manifests[name], 'unexpected component identity')
            with tar.extractfile(seen[FILES[name]]) as stream:
                require(hash_stream(stream) == item['archive_digest'], 'archive digest mismatch')
        if destination is None:
            return metadata
        # Create a new private directory exclusively. Never touch an existing
        # directory, follow a final symlink, or remove partial files on errors.
        os.mkdir(destination, 0o700)
        directory = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(directory)
            require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
                    'destination directory is not private')
            for name in COMPONENTS:
                filename = FILES[name]
                fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=directory)
                with os.fdopen(fd, 'wb') as out, tar.extractfile(seen[filename]) as stream:
                    digest = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        out.write(block)
                        digest.update(block)
                    require('sha256:' + digest.hexdigest() == components[name]['archive_digest'],
                            'archive changed during extraction')
            # OCI verification reads only newly created private files. It checks
            # every descriptor and diff_id and does not extract layers. Pin the
            # private directory by its open descriptor instead of re-resolving
            # its externally mutable path.
            for name in COMPONENTS:
                image = oci.verify(f'/proc/self/fd/{directory}/{FILES[name]}')
                require(image['archive_digest'] == components[name]['archive_digest'] and
                        image['manifest_digest'] == expected_manifests[name], 'extracted OCI identity mismatch')
        finally:
            os.close(directory)
        return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    for action in ('build', 'inspect', 'extract'):
        p = subs.add_parser(action)
        p.add_argument('envelope')
        p.add_argument('--source', required=True)
        p.add_argument('--recipient', required=True, help='sha256 fingerprint of independently trusted public key')
        if action == 'build':
            for name in COMPONENTS:
                p.add_argument('--' + name, required=True)
        else:
            p.add_argument('--authenticated-input', action='store_true', required=True,
                           help='caller confirms external attestation and full authenticated decryption')
            for name in COMPONENTS:
                p.add_argument('--' + name + '-manifest', required=True)
            if action == 'extract':
                p.add_argument('--destination', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'build':
            result = build(args.envelope, args.source, args.recipient, {n: getattr(args, n) for n in COMPONENTS})
        else:
            result = inspect(args.envelope, args.source, args.recipient,
                             {n: getattr(args, n + '_manifest') for n in COMPONENTS},
                             args.destination if args.command == 'extract' else None)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, tarfile.TarError, json.JSONDecodeError) as exc:
        print(f'Envelope operation failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
