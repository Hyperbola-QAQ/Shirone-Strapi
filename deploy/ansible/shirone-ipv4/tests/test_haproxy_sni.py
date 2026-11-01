#!/usr/bin/env python3
"""Exercise TLS SNI routing with disposable, loopback-only test backends."""
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from jinja2 import Environment, StrictUndefined

IMAGE = '192.168.86.100:30500/ca-clean/haproxy:6cfd14c4e904-20261021'
TEMPLATE = Path(__file__).resolve().parents[1] / 'haproxy-sni.cfg.j2'
PROXY = b'\r\n\r\n\0\r\nQUIT\n'


def listener():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(64)
    return sock


def client_hello(host):
    context = ssl.create_default_context()
    if host is None:
        context.check_hostname = False
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    obj = context.wrap_bio(incoming, outgoing, server_hostname=host)
    try:
        obj.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


class Routing(unittest.TestCase):
    proxy_enabled = False
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='shirone-haproxy-test-')
        cls.name = 'shirone-haproxy-test-' + uuid.uuid4().hex[:12]
        cls.http, cls.fallback, cls.tunnel = listener(), listener(), listener()
        cls.sockets = [cls.http, cls.fallback, cls.tunnel]
        cls.events = []
        cls.stopped = threading.Event()
        for sock, name in zip(cls.sockets, ['http', 'fallback', 'tunnel']):
            threading.Thread(target=cls.serve, args=(sock, name), daemon=True).start()
        # Find unused ports; HAProxy binds only loopback inside the host network.
        front_http, front_https = listener(), listener()
        cls.front_http, cls.front_https = front_http.getsockname()[1], front_https.getsockname()[1]
        front_http.close(); front_https.close()
        substitutions = {
            'shirone_edge_bind_address': '127.0.0.1', 'shirone_edge_http_port': cls.front_http,
            'shirone_edge_https_port': cls.front_https, 'shirone_edge_traefik_address': '127.0.0.1',
            'shirone_edge_traefik_http_port': cls.http.getsockname()[1],
            'shirone_edge_traefik_https_port': cls.fallback.getsockname()[1],
            'shirone_edge_tunnel_port': cls.tunnel.getsockname()[1],
            'shirone_edge_send_proxy': cls.proxy_enabled,
        }
        config = Environment(undefined=StrictUndefined, keep_trailing_newline=True).from_string(TEMPLATE.read_text()).render(**substitutions)
        path = Path(cls.tmp.name) / 'haproxy.cfg'
        path.write_text(config)
        # Allow the unprivileged image to read the synthetic test configuration.
        Path(cls.tmp.name).chmod(0o755)
        args = ['docker', 'run', '--rm', '--network', 'host', '-v', f'{path}:/tmp/haproxy.cfg:ro', IMAGE]
        subprocess.run(args + ['haproxy', '-c', '-f', '/tmp/haproxy.cfg'], check=True, capture_output=True)
        subprocess.run(['docker', 'run', '-d', '--rm', '--name', cls.name, '--network', 'host',
                        '-v', f'{path}:/tmp/haproxy.cfg:ro', IMAGE,
                        'haproxy', '-f', '/tmp/haproxy.cfg'], check=True, capture_output=True)
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', cls.front_https), timeout=.1):
                    break
            except OSError:
                time.sleep(.05)
        else:
            raise RuntimeError('test HAProxy failed to listen')

    @classmethod
    def serve(cls, sock, name):
        sock.settimeout(.2)
        while not cls.stopped.is_set():
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(.3)
                try:
                    data = conn.recv(16384)
                    while data and len(data) < len(PROXY) and PROXY.startswith(data):
                        part = conn.recv(16384)
                        if not part:
                            break
                        data += part
                    if data.startswith(PROXY):
                        while len(data) < 16:
                            part = conn.recv(16384)
                            if not part:
                                break
                            data += part
                        if len(data) < 16:
                            continue
                        header_end = 16 + int.from_bytes(data[14:16], 'big')
                        while len(data) <= header_end:
                            part = conn.recv(16384)
                            if not part:
                                break
                            data += part
                        if len(data) <= header_end:
                            continue
                    # HAProxy TCP health checks connect without data.
                    if data:
                        cls.events.append((name, data))
                        conn.sendall(name.encode())
                except OSError:
                    pass

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['docker', 'stop', '-t', '1', cls.name], capture_output=True)
        cls.stopped.set()
        for sock in cls.sockets:
            sock.close()
        cls.tmp.cleanup()

    def send(self, port, payload):
        with socket.create_connection(('127.0.0.1', port), timeout=3) as sock:
            sock.settimeout(7)
            self.last_client_port = sock.getsockname()[1]
            sock.sendall(payload)
            return sock.recv(64)

    def test_allowed_names_are_raw_tls(self):
        for host in ['hyperbola.cc', 'www.hyperbola.cc', 'blog.hyperbola.cc', 'strapi.hyperbola.cc']:
            hello = client_hello(host)
            self.assertEqual(self.send(self.front_https, hello), b'tunnel')
            name, data = self.events[-1]
            self.assertEqual(name, 'tunnel')
            if self.proxy_enabled:
                self.assertTrue(data.startswith(PROXY))
                header_end = 16 + int.from_bytes(data[14:16], 'big')
                self.assertEqual(data[header_end:], hello)
                self.assertEqual(data[16:20], socket.inet_aton('127.0.0.1'))
                self.assertEqual(int.from_bytes(data[24:26], 'big'), self.last_client_port)
                self.assertEqual(int.from_bytes(data[26:28], 'big'), self.front_https)
            else:
                self.assertEqual(data, hello)

    def test_other_names_preserve_proxy_v2(self):
        for host in ['unrelated.hyperbola.cc', None]:
            hello = client_hello(host)
            self.assertEqual(self.send(self.front_https, hello), b'fallback')
            name, data = self.events[-1]
            self.assertEqual(name, 'fallback')
            self.assertTrue(data.startswith(PROXY))
            self.assertIn(hello, data)

    def test_http_retains_existing_backend(self):
        self.assertEqual(self.send(self.front_http, b'GET / HTTP/1.0\r\n\r\n'), b'http')
        self.assertTrue(self.events[-1][1].startswith(PROXY))

class ProxyRouting(Routing):
    proxy_enabled = True


if __name__ == '__main__':
    unittest.main()
