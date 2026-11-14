#!/usr/bin/env python3
"""Exercise actual reconstruction build command lines with a fake Docker CLI."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
NODE = 'docker.io/library/node@sha256:25330af3531fb5e23318554a0aa911125b6e91b1b777edf7655501d207c067a2'
PG = 'ghcr.io/cloudnative-pg/postgresql@sha256:e6e1f467b52112f7e08aa380de81473106ff54b1834d90bcb4265a2a018e1921'


class BaseSelectionTests(unittest.TestCase):
    def test_selected_official_parent_reaches_each_build_invocation(self):
        script = (ROOT / 'rebuild-hosted-bases-candidate.sh').read_text()
        commands = []
        lines = iter(script.splitlines())
        for line in lines:
            if line.strip().startswith(('docker build ', 'docker buildx build ')):
                command = line.strip()
                while command.endswith('\\'):
                    command = command[:-1] + next(lines).strip()
                commands.append(command)
        self.assertEqual(len(commands), 4)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = root / 'docker'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_OUTPUT"\n')
            fake.chmod(0o700)
            output = root / 'args'
            env = dict(os.environ, PATH=td + ':' + os.environ['PATH'], TEST_OUTPUT=str(output),
                       node_parent=NODE, pg_parent=PG, node_tag='node:test', pg_tag='pg:test',
                       scratch=td, output=td)
            for command in commands:
                subprocess.run(['bash', '-c', command], env=env, check=True)
                args = output.read_text().splitlines()
                self.assertIn('--build-arg', args)
                component = 'node' if 'node-context' in command else 'pg'
                self.assertIn('BASE_IMAGE=' + (NODE if component == 'node' else PG), args)
        self.assertIn('ARG BASE_IMAGE\nFROM ${BASE_IMAGE}',
                      (ROOT / 'Dockerfile.node-reconstruction-candidate').read_text())
        self.assertIn('ARG BASE_IMAGE\nFROM ${BASE_IMAGE}', script)


if __name__ == '__main__':
    unittest.main()
