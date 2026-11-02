#!/usr/bin/env python3
"""Generate an experimental CA overlay from a docker-export tar, never deploy it.

Input must be a trusted, digest-verified image export. Output is an offline
build context. The caller must separately verify the resulting image and its
execution configuration; this tool cannot establish release eligibility.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import ssl
import tarfile

DENIED = {'86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b',
          'd7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4'}
PEM = re.compile(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', re.S)
PREFIXES = ('etc/ssl/', 'etc/pki/', 'usr/share/ca-certificates/', 'usr/local/share/ca-certificates/')
DESTINATION_PREFIXES = PREFIXES + ('usr/lib/python3/dist-packages/pip/_vendor/certifi/',
                                   'usr/local/lib/python3.13/dist-packages/certifi/',
                                   'usr/local/lib/python3.13/dist-packages/botocore/')


def name_of(name):
    path = PurePosixPath(name.removeprefix('./'))
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe image path')
    return str(path)


def is_cert_path(name):
    leaf = PurePosixPath(name).name.lower()
    return (leaf in {'cacert.pem', 'ca-bundle.crt', 'ca-certificates.crt', 'cert.pem', 'tls-ca-bundle.pem'}
            or (name.startswith(PREFIXES) and leaf.endswith(('.pem', '.crt', '.cer')) and 'key' not in leaf))


def fp(block):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(block.decode('ascii'))).hexdigest()


def generate(source, out):
    # Refuse existing output; it may contain unrelated user data.
    out.mkdir(mode=0o700)
    entries = {}
    with tarfile.open(source, 'r:*') as archive:
        for info in archive:
            name = name_of(info.name)
            if name in entries:
                raise ValueError('duplicate export path')
            data = archive.extractfile(info).read() if info.isfile() and is_cert_path(name) else None
            entries[name] = (info, data)
    changes, deleted, removed = {}, set(), 0
    for name, (info, data) in entries.items():
        if data is None or b'-----BEGIN CERTIFICATE-----' not in data:
            continue
        matches = list(PEM.finditer(data))
        remainder = PEM.sub(b'', data)
        if b'-----BEGIN CERTIFICATE-----' in remainder or b'-----END CERTIFICATE-----' in remainder:
            raise ValueError('malformed PEM input')
        bad = sum(fp(m.group()) in DENIED for m in matches)
        if not bad:
            continue
        updated = PEM.sub(lambda m: b'' if fp(m.group()) in DENIED else m.group(), data)
        if PEM.search(updated):
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=b'\n'.join(PEM.findall(updated)).decode('ascii'))
            changes[name] = (info, updated)
        else:
            deleted.add(name)
        removed += bad
    if not removed:
        raise ValueError('no denied certificates found in input')
    # Remove aliases transitively. Resolve paths lexically only, without following
    # host filesystem symlinks or operating on the extracted export.
    while True:
        found = set()
        for name, (info, _) in entries.items():
            if name in deleted or not (info.issym() or info.islnk()):
                continue
            target = PurePosixPath(info.linkname)
            if info.issym() and not target.is_absolute():
                target = PurePosixPath(name).parent / target
            import posixpath
            target_name = posixpath.normpath(str(target)).lstrip('/')
            if target_name in deleted:
                found.add(name)
        if not found:
            break
        deleted |= found
    # Every destination's ancestors must be actual directories, not image
    # symlinks, to prevent a tar extraction from escaping the intended path.
    for name in set(changes) | deleted:
        if not name.startswith(DESTINATION_PREFIXES):
            raise ValueError(f'refusing destination outside CA directories: {name}')
        for parent in PurePosixPath(name).parents:
            if str(parent) in ('.', ''):
                break
            if str(parent) in entries and not entries[str(parent)][0].isdir():
                raise ValueError('non-directory destination ancestor')
    with tarfile.open(out / 'ca-files.tar', 'w', format=tarfile.PAX_FORMAT) as tar:
        for name, (info, data) in sorted(changes.items()):
            import copy
            info = copy.copy(info)
            info.name, info.size = name, len(data)
            tar.addfile(info, io.BytesIO(data))
    # Dockerfile author can COPY the tar and script into a trusted candidate
    # image. -- protects names even if a future source contains leading dashes.
    commands = ['#!/bin/sh', 'set -eu', 'tar -xf /tmp/ca-files.tar -C /']
    commands += ['rm -f -- /' + shlex.quote(name) for name in sorted(deleted)]
    commands += ['rm -f -- /tmp/ca-files.tar /tmp/apply-ca-overlay.sh']
    (out / 'apply-ca-overlay.sh').write_text('\n'.join(commands) + '\n')
    (out / 'report.json').write_text(json.dumps({'changed': sorted(changes), 'deleted': sorted(deleted),
                                                'denied_occurrences_removed': removed}, indent=2) + '\n')
    print(f'Offline CA overlay prepared: {len(changes)} modified files, {len(deleted)} removed paths, {removed} certificates. Not release-approved.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('docker_export', type=Path)
    parser.add_argument('new_output_directory', type=Path)
    args = parser.parse_args()
    generate(args.docker_export, args.new_output_directory)
