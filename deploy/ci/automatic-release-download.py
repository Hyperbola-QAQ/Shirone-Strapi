#!/usr/bin/env python3
"""Download one exact GitHub workflow artifact to a private new directory.

Transport only: the caller must verify both attestations and OCI content.
No credentials, signed URLs or command error bodies are printed.
"""
from concurrent.futures import ThreadPoolExecutor
import http.client
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO = 'Hyperbola-QAQ/Shirone-Strapi'
MAX_ARCHIVE = 4 * 1024 * 1024 * 1024
CHUNK = 16 * 1024 * 1024
WORKERS = 6


class TransportUnavailable(RuntimeError):
    """A classified temporary network failure before any transaction."""


API_SPEC = importlib.util.spec_from_file_location('download_github_api', Path(__file__).with_name('github-api-safe.py'))
api = importlib.util.module_from_spec(API_SPEC)
API_SPEC.loader.exec_module(api)


def artifact_info(artifact_id):
    try:
        return api.call(f'repos/{REPO}/actions/artifacts/{artifact_id}')
    except api.ApiUnavailable:
        raise TransportUnavailable('artifact metadata unavailable') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def github_credential():
    # A job token is opaque: GH_TOKEN takes precedence and must never be
    # logged or written to disk. Reject header controls and ambiguous blanks.
    token = os.environ.get('GH_TOKEN')
    if token is not None:
        if not 1 <= len(token) <= 8192 or any(ord(ch) < 33 or ord(ch) > 126 for ch in token):
            raise ValueError('invalid GitHub credential')
        return token
    # Without GH_TOKEN, tolerate a local environment-manager banner only when
    # gh yields exactly one recognized credential line.
    result = subprocess.run(['gh', 'auth', 'token', '--hostname', 'github.com'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            check=True, timeout=30, text=True)
    tokens = [line for line in result.stdout.splitlines()
              if re.fullmatch(r'(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)', line)]
    if len(tokens) != 1:
        raise ValueError('unable to identify a unique GitHub credential')
    return tokens[0]


def artifact_location(artifact_id):
    token = github_credential()
    request = urllib.request.Request(
        f'https://api.github.com/repos/{REPO}/actions/artifacts/{artifact_id}/zip',
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                 'User-Agent': 'shirone-release-verification', 'X-GitHub-Api-Version': '2022-11-28'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=30):
            raise ValueError('unexpected artifact response')
    except urllib.error.HTTPError as error:
        if error.code in (408, 429) or 500 <= error.code <= 599:
            raise TransportUnavailable('artifact location temporarily unavailable') from None
        if error.code != 302:
            raise ValueError('artifact location request failed') from None
        location = error.headers.get('Location', '')
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise TransportUnavailable('artifact location temporarily unavailable') from None
    parsed = urllib.parse.urlsplit(location)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('invalid artifact location')
    return location


def fetch_range(url, start, end, total, path, retries=3, refresh_location=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}',
                                              'User-Agent': 'shirone-release-verification',
                                              'Accept-Encoding': 'identity'})
            with opener.open(request, timeout=90) as response:
                if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{total}':
                    raise ValueError('unexpected artifact byte range')
                with path.open('xb') as output:
                    remaining = end - start + 1
                    while remaining:
                        block = response.read(min(1024 * 1024, remaining))
                        if not block:
                            raise TransportUnavailable('artifact range ended early')
                        output.write(block)
                        remaining -= len(block)
                    if response.read(1):
                        raise ValueError('artifact range contains extra bytes')
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead, TransportUnavailable) as error:
            if path.exists():
                path.unlink()
            if isinstance(error, urllib.error.HTTPError) and error.code in (401, 403) and refresh_location is not None and attempt + 1 < retries:
                # Signed storage URLs are short-lived. Reacquire an authorized
                # URL without logging either the old or the replacement URL.
                url = refresh_location()
                continue
            if isinstance(error, urllib.error.HTTPError) and error.code not in (408, 429) and not 500 <= error.code <= 599:
                raise ValueError('artifact range request rejected') from None
            if attempt + 1 == retries:
                raise TransportUnavailable('artifact range temporarily unavailable') from None
            time.sleep(2 * (attempt + 1))
        except Exception:
            if path.exists():
                path.unlink()
            raise


def unpack(archive, destination):
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        if len(entries) != 2 or {entry.filename for entry in entries} != {'metadata.json', 'release.age'}:
            raise ValueError('unexpected artifact members')
        for entry in entries:
            limit = 16384 if entry.filename == 'metadata.json' else MAX_ARCHIVE
            mode = entry.external_attr >> 16
            if (entry.is_dir() or stat.S_ISLNK(mode) or not 0 < entry.file_size <= limit or
                    entry.flag_bits & 1):
                raise ValueError('invalid artifact member')
            with z.open(entry) as source, (destination / entry.filename).open('xb') as output:
                remaining = entry.file_size
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError('truncated artifact member')
                    output.write(block)
                    remaining -= len(block)
                if source.read(1):
                    raise ValueError('unexpected artifact member length')


def download(run_id, attempt, parent, artifact_id):
    if any(type(value) is not int or value < 1 for value in (run_id, attempt, artifact_id)):
        raise ValueError('invalid artifact identity')
    parent = Path(parent)
    info = parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('download parent must be private')
    artifact = artifact_info(artifact_id)
    size = artifact.get('size_in_bytes')
    if (artifact.get('id') != artifact_id or artifact.get('expired') is not False or
            artifact.get('name') != f'shirone-encrypted-release-{run_id}-{attempt}' or
            not isinstance(artifact.get('workflow_run'), dict) or artifact['workflow_run'].get('id') != run_id or
            type(size) is not int or not 0 < size <= MAX_ARCHIVE):
        raise ValueError('artifact does not match the selected run')
    with tempfile.TemporaryDirectory(prefix='artifact-attempt-', dir=parent) as scratch:
        work = Path(scratch)
        spans = [(start, min(start + CHUNK, size) - 1) for start in range(0, size, CHUNK)]
        def part(item):
            index, (start, end) = item
            location = artifact_location(artifact_id)
            fetch_range(location, start, end, size, work / f'part-{index:05d}',
                        refresh_location=lambda: artifact_location(artifact_id))
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            list(executor.map(part, enumerate(spans)))
        archive = work / 'artifact.zip'
        with archive.open('xb') as output:
            for index in range(len(spans)):
                with (work / f'part-{index:05d}').open('rb') as source:
                    while block := source.read(1024 * 1024):
                        output.write(block)
        if archive.stat().st_size != size:
            raise ValueError('artifact size mismatch')
        unpacked = work / 'unpacked'
        unpacked.mkdir(mode=0o700)
        unpack(archive, unpacked)
        final = parent / f'artifact-{run_id}-{attempt}'
        # Never overwrite or delete a pre-existing destination.
        os.mkdir(final, 0o700)
        for path in unpacked.iterdir():
            os.rename(path, final / path.name)
        return final
