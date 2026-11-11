import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('safe_api', Path(__file__).with_name('github-api-safe.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class SafeApiTests(unittest.TestCase):
    def run_api(self, code, out, err=b''):
        result = subprocess.CompletedProcess(['gh'], code, out, err)
        with patch.object(api.subprocess, 'run', return_value=result):
            return api.call('repos/example/example')

    def test_success_parses_headers_and_json(self):
        self.assertEqual(self.run_api(0, b'HTTP/2.0 200 OK\r\nX-Test: yes\r\n\r\n{"id": 1}'), {'id': 1})

    def test_server_failure_and_known_connection_failure_are_transient(self):
        for out, err in ((b'HTTP/2.0 503 Unavailable\r\n\r\n', b''),
                         (b'', b'error connecting to api.github.com')):
            with self.assertRaises(api.ApiUnavailable): self.run_api(1, out, err)

    def test_auth_failure_and_unknown_error_are_not_transient(self):
        for out, err in ((b'HTTP/2.0 401 Unauthorized\r\n\r\n', b''),
                         (b'HTTP/2.0 403 Forbidden\r\n\r\n', b''), (b'', b'unknown failure')):
            with self.assertRaises(ValueError): self.run_api(1, out, err)

    def test_timeout_is_transient(self):
        with patch.object(api.subprocess, 'run', side_effect=subprocess.TimeoutExpired('gh', 60)):
            with self.assertRaises(api.ApiUnavailable): api.call('repos/example/example')


if __name__ == '__main__':
    unittest.main()
