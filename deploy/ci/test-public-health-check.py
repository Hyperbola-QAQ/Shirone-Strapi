#!/usr/bin/env python3
"""Exercise retry behavior against a local synthetic HTTP service."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
import unittest

SCRIPT = Path(__file__).with_name('public-health-check.sh')


class HealthTests(unittest.TestCase):
    def probe(self, responses):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(1)
                self.send_response(responses[min(len(calls) - 1, len(responses) - 1)])
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(['bash', str(SCRIPT), f'http://127.0.0.1:{server.server_port}/'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            return result.returncode, len(calls)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_transient_500_recovers(self):
        self.assertEqual(self.probe([500, 500, 200]), (0, 3))

    def test_persistent_500_is_bounded(self):
        status, calls = self.probe([500])
        self.assertNotEqual(status, 0)
        self.assertEqual(calls, 5)

    def test_forbidden_fails_without_retry(self):
        status, calls = self.probe([403])
        self.assertNotEqual(status, 0)
        self.assertEqual(calls, 1)


if __name__ == '__main__':
    unittest.main()
