#!/usr/bin/env python3
"""Conservative OCI historical-layer disclosure screening; never authorizes upload.

Screens every regular file in every referenced layer, including files later
deleted by whiteouts. Does not extract files or output names/content. A clean
result is NOT proof that contents are public-safe: manual review is mandatory.
Nested archives and special entries are flagged because their contents cannot
be fully inspected by this candidate. No network access is performed.
"""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('oci_verify', HERE / 'verify-oci-archive-candidate.py')
oci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oci)

MAX_FILES = 2_000_000
BLOCK = 1024 * 1024
OVERLAP = 4096
SECRET_PATTERNS = (
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    re.compile(rb'(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,255}'),
    re.compile(rb'(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{50,255}'),
    re.compile(rb'(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])'),
    re.compile(rb'(?i)(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*["\']?[^\s"\'${}<>]{12,}'),
)
SENSITIVE_PATH = re.compile(r'(?i)(?:^|/)(?:\.env(?:\.[^/]*)?|\.npmrc|\.pypirc|\.netrc|id_(?:rsa|ed25519|ecdsa)|(?:credentials|secrets?)\.(?:json|ya?ml|toml|ini)|\.docker/config\.json|\.kube/config)$')
NESTED_MAGIC = (b'\x1f\x8b', b'PK\x03\x04', b'PK\x05\x06', b'\xfd7zXZ\x00',
                b'\x28\xb5\x2f\xfd', b'7z\xbc\xaf\x27\x1c', b'BZh')


def scan_bytes(data, findings):
    for pattern in SECRET_PATTERNS:
        if pattern.search(data):
            findings.add('secret_pattern')


def scan_stream(stream, findings, detect_nested=True):
    overlap = b''
    first = True
    for block in iter(lambda: stream.read(BLOCK), b''):
        if first:
            if detect_nested and (block.startswith(NESTED_MAGIC) or (len(block) > 262 and block[257:262] == b'ustar')):
                findings.add('nested_archive_uninspected')
            first = False
        chunk = overlap + block
        scan_bytes(chunk, findings)
        overlap = chunk[-OVERLAP:]


def audit(path):
    identity = oci.verify(path)
    findings = set()
    count = 0
    with tarfile.open(path, 'r:') as archive:
        index = oci.parse_json(archive.extractfile('index.json').read())
        manifest_desc = index['manifests'][0]
        manifest_path = 'blobs/sha256/' + manifest_desc['digest'][7:]
        manifest_data = archive.extractfile(manifest_path).read()
        manifest = oci.parse_json(manifest_data)
        config_data = archive.extractfile('blobs/sha256/' + manifest['config']['digest'][7:]).read()
        # Image metadata may itself contain secret values or private labels.
        for data in (json.dumps(index).encode(), manifest_data, config_data):
            scan_bytes(data, findings)
        for layer in manifest['layers']:
            member = archive.getmember('blobs/sha256/' + layer['digest'][7:])
            media = layer['mediaType']
            def open_layer():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError('missing layer')
                if media.endswith('+gzip') or media.endswith('.gzip'):
                    return gzip.GzipFile(fileobj=stream, mode='rb')
                if media.endswith('+zstd'):
                    if oci.zstd is None:
                        raise ValueError('zstd decoder unavailable')
                    return oci.zstd.ZstdFile(stream, 'rb', options={oci.zstd.DecompressionParameter.window_log_max: 27})
                return stream

            # Inspect every decompressed byte, including metadata and trailing
            # bytes after the tar end marker, not just visible member payloads.
            with open_layer() as raw_reader:
                scan_stream(raw_reader, findings, detect_nested=False)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError('missing layer')
            with source:
                if media.endswith('+gzip') or media.endswith('.gzip'):
                    reader = gzip.GzipFile(fileobj=source, mode='rb')
                elif media.endswith('+zstd'):
                    if oci.zstd is None:
                        raise ValueError('zstd decoder unavailable')
                    reader = oci.zstd.ZstdFile(source, 'rb', options={oci.zstd.DecompressionParameter.window_log_max: 27})
                else:
                    reader = source
                with reader, tarfile.open(fileobj=reader, mode='r|') as layer_tar:
                    for item in layer_tar:
                        count += 1
                        if count > MAX_FILES:
                            raise ValueError('layer entry count exceeds limit')
                        # Treat names and link targets as potentially sensitive,
                        # but never echo them into logs or the result.
                        for value in (item.name, item.linkname):
                            scan_bytes(value.encode('utf-8', 'surrogateescape'), findings)
                            if SENSITIVE_PATH.search(value):
                                findings.add('sensitive_path')
                        scan_bytes(json.dumps(item.pax_headers).encode(), findings)
                        name = PurePosixPath(item.name)
                        if name.is_absolute() or '..' in name.parts:
                            findings.add('unsafe_layer_path')
                        if item.isfile():
                            stream = layer_tar.extractfile(item)
                            if stream is None:
                                raise ValueError('cannot inspect layer file')
                            with stream:
                                scan_stream(stream, findings)
                        elif not (item.isdir() or item.issym() or item.islnk()):
                            findings.add('special_entry_uninspected')
    if oci.sha_file(path) != identity['archive_digest']:
        raise ValueError('archive changed during screening')
    return {'archive_digest': identity['archive_digest'], 'layers': identity['layer_count'],
            'entries_screened': count, 'findings': sorted(findings),
            'manual_review_required': True, 'upload_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    args = parser.parse_args()
    try:
        result = audit(args.archive)
        print(json.dumps(result, sort_keys=True))
        return 1 if result['findings'] else 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, tarfile.TarError, json.JSONDecodeError):
        # Error text could contain a path or private content. Do not print it.
        print('OCI disclosure screening failed; archive not authorized for upload.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
