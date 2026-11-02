#!/usr/bin/env python3
"""Read-only PEM bundle audit; not full image verification or release approval."""
import argparse
import hashlib
from pathlib import Path
import re
import ssl
import sys

DENIED = frozenset({
    '86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b',
    'd7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4',
})
PEM = re.compile(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', re.S)


def fingerprint(block):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(block.decode('ascii'))).hexdigest()


def audit(source, candidate, denied=DENIED):
    matches = list(PEM.finditer(source))
    if not matches:
        raise ValueError('source has no PEM certificates')
    removed = 0
    def replace(match):
        nonlocal removed
        if fingerprint(match.group()) in denied:
            removed += 1
            return b''
        return match.group()
    expected = PEM.sub(replace, source)
    if candidate != expected:
        raise ValueError('candidate differs beyond approved certificate removal')
    # Detect malformed trailing PEM markers, not only complete blocks.
    remainder = PEM.sub(b'', candidate)
    if b'-----BEGIN CERTIFICATE-----' in remainder or b'-----END CERTIFICATE-----' in remainder:
        raise ValueError('malformed candidate PEM markers')
    for match in PEM.finditer(candidate):
        if fingerprint(match.group()) in denied:
            raise ValueError('denied certificate remains')
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    try:
        count = audit(args.source.read_bytes(), args.candidate.read_bytes())
        print(f'Bundle-only audit passed; {count} denied certificates removed. '
              'Image CA aliases, other files and release eligibility remain unverified.')
        return 0
    except (OSError, ValueError, UnicodeError) as exc:
        print(f'Bundle audit failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
