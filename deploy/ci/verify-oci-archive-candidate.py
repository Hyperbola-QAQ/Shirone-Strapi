#!/usr/bin/env python3
"""Read-only OCI archive verifier; candidate only, not release authorization.

The caller must independently verify the attestation over the archive file.
No archive members are extracted and no registry or production API is used.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import PurePosixPath
import re
import sys
import tarfile

try:
    from compression import zstd
except ImportError:  # Python before 3.14; reject zstd, never skip its verification.
    zstd = None

HASH = re.compile(r'^sha256:([0-9a-f]{64})$')
MAX_JSON = 10 * 1024 * 1024
MAX_ENTRIES = 10000
# Upper bounds apply to uncompressed bytes, including concatenated frames.
MAX_LAYER_UNCOMPRESSED = 8 * 1024 * 1024 * 1024
MAX_IMAGE_UNCOMPRESSED = 32 * 1024 * 1024 * 1024
OCI_INDEX = 'application/vnd.oci.image.index.v1+json'
MANIFESTS = {'application/vnd.oci.image.manifest.v1+json',
             'application/vnd.docker.distribution.manifest.v2+json'}
CONFIGS = {'application/vnd.oci.image.config.v1+json',
           'application/vnd.docker.container.image.v1+json'}
LAYERS = {'application/vnd.oci.image.layer.v1.tar',
          'application/vnd.oci.image.layer.v1.tar+gzip',
          'application/vnd.oci.image.layer.v1.tar+zstd',
          'application/vnd.docker.image.rootfs.diff.tar.gzip',
          'application/vnd.docker.image.rootfs.diff.tar'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def parse_json(data):
    require(len(data) <= MAX_JSON, 'JSON document exceeds size limit')
    value = json.loads(data, object_pairs_hook=unique_pairs)
    require(isinstance(value, dict), 'JSON object required')
    return value


def normalized(name):
    while name.startswith('./'):
        name = name[2:]
    if name in ('', '.'):
        return ''
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(part not in ('..', '.') for part in path.parts),
            'unsafe archive path')
    return str(path)


def sha_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return 'sha256:' + digest.hexdigest()


def verify(path):
    archive_digest = sha_file(path)
    members = {}
    with tarfile.open(path, mode='r:') as archive:
        for count, member in enumerate(archive, 1):
            require(count <= MAX_ENTRIES, 'too many archive entries')
            name = normalized(member.name)
            if not name:
                require(member.isdir(), 'archive root must be a directory')
                continue
            require(name not in members, 'duplicate archive path')
            require(member.isdir() or member.isfile(), 'links and special archive entries are forbidden')
            require(not member.sparse, 'sparse entries are forbidden')
            require(name in ('oci-layout', 'index.json', 'blobs', 'blobs/sha256') or
                    re.fullmatch(r'blobs/sha256/[0-9a-f]{64}', name), 'unexpected archive path')
            if member.isdir():
                require(name in ('blobs', 'blobs/sha256'), 'unexpected directory')
            else:
                require(name not in ('blobs', 'blobs/sha256'), 'expected directory')
            members[name] = member

        def read(name, max_size=MAX_JSON):
            require(name in members and members[name].isfile(), 'required file missing')
            member = members[name]
            require(member.size <= max_size, 'file exceeds size limit')
            stream = archive.extractfile(member)
            require(stream is not None, 'unable to read archive member')
            with stream:
                data = stream.read(max_size + 1)
            require(len(data) == member.size, 'truncated archive member')
            return data

        layout = parse_json(read('oci-layout'))
        require(layout == {'imageLayoutVersion': '1.0.0'}, 'unexpected OCI layout version')
        index = parse_json(read('index.json'))
        require(index.get('schemaVersion') == 2 and
                index.get('mediaType', OCI_INDEX) == OCI_INDEX, 'invalid OCI index')
        require('subject' not in index and 'artifactType' not in index, 'artifact indexes are unsupported')
        entries = index.get('manifests')
        require(isinstance(entries, list) and len(entries) == 1, 'exactly one platform manifest required')
        selected = entries[0]
        require(isinstance(selected, dict), 'invalid manifest descriptor')
        require(selected.get('mediaType') in MANIFESTS, 'unsupported or nested manifest')
        platform = selected.get('platform')
        require(isinstance(platform, dict) and platform.get('os') == 'linux' and
                platform.get('architecture') == 'amd64' and
                platform.get('variant', '') in ('', 'v1'), 'expected unique linux/amd64 manifest')
        seen = set()

        def descriptor(desc, media_types, json_document=False):
            require(isinstance(desc, dict) and desc.get('mediaType') in media_types,
                    'unsupported descriptor media type')
            digest = desc.get('digest')
            match = HASH.fullmatch(digest) if isinstance(digest, str) else None
            size = desc.get('size')
            require(match is not None and type(size) is int and size >= 0, 'invalid descriptor digest or size')
            name = 'blobs/sha256/' + match.group(1)
            require(name in members and members[name].isfile(), 'descriptor blob missing')
            member = members[name]
            require(member.size == size, 'descriptor size mismatch')
            stream = archive.extractfile(member)
            require(stream is not None, 'cannot read descriptor blob')
            hasher, content = hashlib.sha256(), bytearray()
            with stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    hasher.update(block)
                    if json_document:
                        require(len(content) + len(block) <= MAX_JSON, 'descriptor JSON too large')
                        content.extend(block)
            require(hasher.hexdigest() == match.group(1), 'descriptor digest mismatch')
            seen.add(name)
            return parse_json(bytes(content)) if json_document else None

        manifest = descriptor(selected, MANIFESTS, True)
        require(manifest.get('schemaVersion') == 2 and
                manifest.get('mediaType', selected['mediaType']) == selected['mediaType'], 'invalid image manifest')
        require('subject' not in manifest and 'artifactType' not in manifest, 'artifact manifests are unsupported')
        config = descriptor(manifest.get('config'), CONFIGS, True)
        require(config.get('os') == 'linux' and config.get('architecture') == 'amd64',
                'image configuration platform mismatch')
        layers = manifest.get('layers')
        require(isinstance(layers, list) and len(layers) > 0, 'image layers required')
        rootfs = config.get('rootfs')
        require(isinstance(rootfs, dict) and rootfs.get('type') == 'layers', 'invalid image root filesystem')
        diff_ids = rootfs.get('diff_ids')
        require(isinstance(diff_ids, list) and len(diff_ids) == len(layers) and
                all(isinstance(item, str) and HASH.fullmatch(item) for item in diff_ids),
                'root filesystem diff IDs do not match layers')
        total_uncompressed = 0
        for layer, diff_id in zip(layers, diff_ids):
            descriptor(layer, LAYERS)
            name = 'blobs/sha256/' + layer['digest'][7:]
            stream = archive.extractfile(members[name])
            require(stream is not None, 'cannot read layer for diff ID verification')
            media = layer['mediaType']
            try:
                with stream:
                    if media.endswith('+gzip') or media.endswith('.gzip'):
                        reader = gzip.GzipFile(fileobj=stream, mode='rb')
                    elif media.endswith('+zstd'):
                        require(zstd is not None, 'zstd decoder is unavailable')
                        # Limit decoder window allocation as well as output size.
                        reader = zstd.ZstdFile(stream, 'rb', options={zstd.DecompressionParameter.window_log_max: 27})
                    else:
                        reader = stream
                    hasher = hashlib.sha256()
                    layer_size = 0
                    with reader:
                        for block in iter(lambda: reader.read(1024 * 1024), b''):
                            layer_size += len(block)
                            total_uncompressed += len(block)
                            require(layer_size <= MAX_LAYER_UNCOMPRESSED and
                                    total_uncompressed <= MAX_IMAGE_UNCOMPRESSED,
                                    'uncompressed layer or image exceeds limit')
                            hasher.update(block)
                    require('sha256:' + hasher.hexdigest() == diff_id, 'uncompressed layer diff ID mismatch')
            except (OSError, EOFError) as exc:
                raise ValueError('layer decompression failed') from exc
            except Exception as exc:
                if isinstance(exc, ValueError):
                    raise
                # Decoder-specific exceptions are not stable across Python versions.
                raise ValueError('layer decompression failed') from exc
        blobs = {name for name in members if name.startswith('blobs/sha256/')}
        require(blobs == seen, 'unreferenced blobs forbidden')
        require(sha_file(path) == archive_digest, 'archive changed during verification')
        return {'archive_digest': archive_digest, 'manifest_digest': selected['digest'],
                'platform': 'linux/amd64', 'layer_count': len(layers)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.archive), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, tarfile.TarError, json.JSONDecodeError) as exc:
        print(f'OCI archive verification failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
