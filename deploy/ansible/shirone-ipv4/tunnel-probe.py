#!/usr/bin/env python3
"""Probe a loopback tunnel expecting PROXY v2 followed by verified HTTPS."""
import socket
import ssl
import struct
import sys

if len(sys.argv) != 3 or sys.argv[1] not in {'hyperbola.cc', 'strapi.hyperbola.cc'} or sys.argv[2] not in {'/', '/admin'}:
    raise SystemExit('invalid probe host or path')
host, path = sys.argv[1:]
with socket.create_connection(('127.0.0.1', 25443), timeout=12) as conn:
    # PROXY v2: IPv4/TCP, synthetic test source and destination.
    conn.sendall(b'\r\n\r\n\0\r\nQUIT\n\x21\x11\0\x0c' + socket.inet_aton('198.51.100.10') + socket.inet_aton('175.178.150.147') + struct.pack('!HH', 51000, 443))
    with ssl.create_default_context().wrap_socket(conn, server_hostname=host) as tls:
        tls.sendall(f'GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode())
        data = b''
        while b'\r\n' not in data and len(data) < 1024:
            block = tls.recv(1024)
            if not block:
                break
            data += block
        if not data.startswith((b'HTTP/1.1 200 ', b'HTTP/1.1 301 ', b'HTTP/1.1 302 ')):
            raise SystemExit('HTTPS probe did not return a successful response')
print('PROXY v2 and validated HTTPS succeeded')
