import importlib.util
import http.server
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).with_name('bootstrap-controller-checkout.py'))
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def test_clone_commands_do_not_inherit_git_override_or_credentials(self):
        injected = {'GIT_DIR': '/unexpected', 'GIT_COMMON_DIR': '/unexpected', 'GIT_CONFIG_COUNT': '1',
                    'GIT_CONFIG_KEY_0': 'credential.helper', 'GIT_SSL_NO_VERIFY': 'true',
                    'GIT_ASKPASS': '/unexpected', 'GIT_TRACE': '/unexpected'}
        with patch.dict(os.environ, injected), \
             patch.object(bootstrap.subprocess, 'run', return_value=Mock(stdout='')) as command:
            bootstrap.run('git', 'status')
            env = command.call_args.kwargs['env']
            for name in injected: self.assertNotIn(name, env)
            self.assertEqual(env['GIT_CONFIG_GLOBAL'], '/dev/null')
            self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
            self.assertIn('StrictHostKeyChecking=yes', env['GIT_SSH_COMMAND'])

    def setup_repo(self, root):
        remote, writer = root / 'remote.git', root / 'writer'
        def git(*args, cwd=None):
            return subprocess.run(['git', *args], cwd=cwd, check=True, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()
        git('init', '--bare', '--initial-branch=main', str(remote))
        git('clone', str(remote), str(writer))
        (writer / 'content').write_text('synthetic fixture')
        git('add', 'content', cwd=writer)
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture', cwd=writer)
        git('push', 'origin', 'main', cwd=writer)
        return remote, git('rev-parse', 'HEAD', cwd=writer)

    def test_real_git_clone_checks_commit_and_sets_only_dedicated_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, head = self.setup_repo(root)
            parent = root / 'private'
            parent.mkdir(mode=0o700)
            with patch.object(bootstrap, 'PUBLIC_URL', str(remote)):
                bootstrap.prepare(parent / 'repo', head, 'Release Test', 'release@example.invalid')
            self.assertEqual(bootstrap.run('git', '-C', str(parent / 'repo'), 'rev-parse', 'HEAD'), head)
            self.assertEqual(bootstrap.run('git', '-C', str(parent / 'repo'), 'remote', 'get-url', 'origin'), bootstrap.PUSH_URL)
            self.assertEqual(bootstrap.run('git', '-C', str(parent / 'repo'), 'config', '--local', 'core.hooksPath'), '/dev/null')
            self.assertEqual((parent / 'repo/content').read_text(), 'synthetic fixture')

    def test_wrong_source_and_existing_directory_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, head = self.setup_repo(root)
            parent = root / 'private'
            parent.mkdir(mode=0o700)
            with patch.object(bootstrap, 'PUBLIC_URL', str(remote)):
                with self.assertRaises(ValueError):
                    bootstrap.prepare(parent / 'repo', 'f' * 40, 'Release Test', 'release@example.invalid')
            self.assertEqual(list(parent.iterdir()), [])
            existing = parent / 'repo'
            existing.mkdir()
            (existing / 'keep').write_text('keep')
            with self.assertRaises(FileExistsError):
                bootstrap.prepare(existing, head, 'Release Test', 'release@example.invalid')
            self.assertEqual((existing / 'keep').read_text(), 'keep')

    def test_real_git_common_dir_injection_cannot_change_another_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, head = self.setup_repo(root)
            victim = root / 'victim'
            subprocess.run(['git', 'init', str(victim)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['git', '-C', str(victim), 'remote', 'add', 'origin', 'https://example.invalid/sentinel'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            parent = root / 'private'
            parent.mkdir(mode=0o700)
            with patch.object(bootstrap, 'PUBLIC_URL', str(remote)), \
                 patch.dict(os.environ, {'GIT_COMMON_DIR': str(victim / '.git')}):
                bootstrap.prepare(parent / 'repo', head, 'Release Test', 'release@example.invalid')
            origin = subprocess.run(['git', '-C', str(victim), 'remote', 'get-url', 'origin'],
                                    check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()
            self.assertEqual(origin, 'https://example.invalid/sentinel')

    def test_anonymous_clone_does_not_read_inherited_netrc(self):
        seen = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get('Authorization'))
                self.send_response(401)
                self.send_header('WWW-Authenticate', 'Basic realm="fixture"')
                self.end_headers()
            def log_message(self, *args): pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'; home.mkdir(mode=0o700)
            netrc = home / '.netrc'
            netrc.write_text('machine 127.0.0.1 login synthetic password synthetic\n')
            netrc.chmod(0o600)
            parent = Path(tmp) / 'private'; parent.mkdir(mode=0o700)
            url = f'http://127.0.0.1:{server.server_port}/fixture.git'
            with patch.object(bootstrap, 'PUBLIC_URL', url), \
                 patch.dict(os.environ, {'HOME': str(home), 'NO_PROXY': '127.0.0.1'}):
                with self.assertRaises(subprocess.CalledProcessError):
                    bootstrap.prepare(parent / 'repo', 'a' * 40, 'Test', 'test@example.invalid')
        self.assertTrue(seen)
        self.assertTrue(all(value is None for value in seen))

    def test_insecure_parent_and_invalid_identity_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.chmod(root, 0o755)
            with self.assertRaises(ValueError):
                bootstrap.prepare(root / 'repo', 'a' * 40, 'Release Test', 'release@example.invalid')
            os.chmod(root, 0o700)
            with self.assertRaises(ValueError):
                bootstrap.prepare(root / 'repo', 'a' * 40, 'Test\nInjected', 'release@example.invalid')


if __name__ == '__main__':
    unittest.main()
