"""Read GitHub API JSON without exposing credential or response diagnostics."""
import json
import re
import subprocess


class ApiUnavailable(RuntimeError):
    """An explicitly identified transient API transport or server failure."""


def call(endpoint):
    try:
        result = subprocess.run(['gh', 'api', '--include', endpoint], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, check=False, timeout=60)
    except subprocess.TimeoutExpired:
        raise ApiUnavailable('GitHub API timeout') from None
    match = re.search(rb'(?m)^HTTP/[0-9.]+ ([0-9]{3})[^\r\n]*\r?\n', result.stdout)
    status = int(match.group(1)) if match else None
    if result.returncode != 0:
        if status in (408, 429) or (status is not None and 500 <= status <= 599):
            raise ApiUnavailable('GitHub API temporarily unavailable') from None
        if status is None and b'error connecting to api.github.com' in result.stderr.lower():
            raise ApiUnavailable('GitHub API connection unavailable') from None
        raise ValueError('GitHub API request rejected') from None
    if match is None or status is None or not 200 <= status < 300:
        raise ValueError('invalid GitHub API response')
    rest = result.stdout[match.end():]
    separator = rest.find(b'\r\n\r\n')
    width = 4
    if separator < 0:
        separator = rest.find(b'\n\n')
        width = 2
    if separator < 0:
        raise ValueError('invalid GitHub API headers')
    return json.loads(rest[separator + width:])
