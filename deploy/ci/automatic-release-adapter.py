#!/usr/bin/env python3
"""Candidate adapter for the existing signed-release and Ansible entrypoints.

Not scheduled or enabled. Run only in a dedicated clean controller checkout;
the caller supplies previously downloaded ciphertext and trusted identities.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess

HERE = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


gate = module('automatic-release-gate')
gitops = module('automatic-release-git')
promotion = module('promote-encrypted-candidate')
image_pattern = re.compile(r'^192\.168\.86\.100:30500/shirone/(cms|ssr)@sha256:[0-9a-f]{64}$')


def command(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout


class Adapter:
    def __init__(self, checkout, policy, artifact_id, metadata_path, ciphertext_path, key, recipient, previous_record):
        self.checkout = Path(checkout).resolve()
        self.policy = policy
        self.artifact_id = artifact_id
        self.metadata = Path(metadata_path).resolve()
        self.ciphertext = Path(ciphertext_path).resolve()
        self.key = Path(key).resolve()
        self.recipient = recipient
        self.previous_record = Path(previous_record)
        self.head = None

    def _git(self, *args):
        return gitops.git(self.checkout, *args)

    def _live(self, component):
        raw = command('ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                      'hyperbola@192.168.86.11', 'kubectl', '--kubeconfig=/home/hyperbola/.kube/config',
                      '-n', 'mizuki', 'get', 'deployment', f'shirone-{component}', '-o', 'json')
        deployment = json.loads(raw)
        containers = deployment['spec']['template']['spec']['containers']
        if len(containers) != 1 or containers[0]['name'] != component:
            raise ValueError('unexpected live deployment')
        image = containers[0]['image']
        match = image_pattern.fullmatch(image)
        uid = deployment['metadata']['uid']
        if not match or match.group(1) != component or not re.fullmatch(r'[0-9a-f-]{36}', uid):
            raise ValueError('unexpected live identity')
        return {'uid': uid, 'image': image}

    def admit(self, run_id, source):
        if source != self.policy.get('source') or self._git('rev-parse', 'HEAD') != source:
            raise ValueError('checkout differs from approved source')
        if self._git('status', '--porcelain', '--untracked-files=all'):
            raise ValueError('checkout is not clean')
        digest = hashlib.sha256()
        with self.ciphertext.open('rb') as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
        ciphertext_digest = 'sha256:' + digest.hexdigest()
        recipient_fingerprint = promotion.crypto.recipient_fingerprint(self.recipient)
        signed = gate.admit(self.policy, run_id, self.artifact_id, self.metadata,
                            ciphertext_digest, recipient_fingerprint)
        return {'source': source, 'signer': self.policy['signer'], 'ciphertext_digest': ciphertext_digest,
                'manifests': signed['manifests']}

    def snapshot(self, admission):
        fd = os.open(self.previous_record, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or not 0 < info.st_size <= 8192:
                raise ValueError('unprotected previous release record')
            raw = os.read(fd, 8193)
            if len(raw) != info.st_size:
                raise ValueError('previous record changed while reading')
        finally:
            os.close(fd)
        previous = json.loads(raw, object_pairs_hook=promotion.metadata_verifier.unique)
        if (not isinstance(previous, dict) or set(previous) != {'source', 'components', 'rollback_compatible'} or
                not isinstance(previous['source'], str) or not re.fullmatch(r'[0-9a-f]{40}', previous['source']) or
                previous['rollback_compatible'] is not True or not isinstance(previous['components'], dict) or
                set(previous['components']) != {'cms', 'ssr'}):
            raise ValueError('previous release record is incomplete')
        # Conservative compatibility gate: no CMS or its dependency inputs may
        # change. CMS migrations need a separate reviewed recovery procedure.
        changed = self._git('diff', '--name-only', previous['source'], admission['source'], '--',
                            'cms', 'Dockerfile', 'package.json', 'pnpm-lock.yaml')
        if changed:
            raise ValueError('CMS changed; automatic rollback compatibility is not established')
        components = {name: self._live(name) for name in ('cms', 'ssr')}
        if components != previous['components']:
            raise ValueError('previous release record does not match live deployments')
        for name, value in components.items():
            text = (self.checkout / 'deploy/k8s' / f'{name}.yaml').read_text()
            images = re.findall(r'^[ \t]*image:[ \t]*([^\s]+)[ \t]*$', text, re.MULTILINE)
            if images != [value['image']]:
                raise ValueError('live image and source manifest disagree')
        self.head = admission['source']
        return {'rollback_safe': True, 'components': components, 'source': admission['source']}

    def preview(self, admission, snapshot):
        if gitops.remote_head(self.checkout) != self.head:
            raise ValueError('main changed before promotion')

    def promote(self, component, admission, snapshot):
        old = snapshot['components'][component]['image']
        target = f"192.168.86.100:30500/shirone/{component}@{admission['manifests'][component]}"
        if old == target:
            return
        self.head = gitops.commit_manifest(self.checkout, component, self.head, old, target)
        # Existing tool verifies both attestations, decrypts and validates OCI,
        # copies by digest, then calls the guarded Ansible entrypoint.
        original = os.getcwd()
        try:
            os.chdir(self.checkout)
            promotion.promote(component, admission['source'], self.head, admission['signer'],
                              snapshot['components'][component]['uid'], old, self.metadata, self.ciphertext,
                              admission['ciphertext_digest'], self.key, self.recipient,
                              admission['manifests'], copy_image=True, apply=True)
        finally:
            os.chdir(original)

    def verify(self, component, admission, snapshot):
        expected = f"192.168.86.100:30500/shirone/{component}@{admission['manifests'][component]}"
        current = self._live(component)
        if current != {'uid': snapshot['components'][component]['uid'], 'image': expected}:
            raise ValueError('live image or deployment UID differs')

    def verify_all(self, admission, snapshot):
        for url in ('https://hyperbola.cc/', 'https://strapi.hyperbola.cc/admin/'):
            command('curl', '--fail', '--silent', '--show-error', '--max-time', '20', '--output', '/dev/null', url)

    def restore(self, admission, snapshot):
        for component in ('ssr', 'cms'):
            old = snapshot['components'][component]
            target = f"192.168.86.100:30500/shirone/{component}@{admission['manifests'][component]}"
            live = self._live(component)
            if live['uid'] != old['uid'] or live['image'] not in (old['image'], target):
                raise ValueError('unknown live state; manual reconciliation required')
            manifest = (self.checkout / 'deploy/k8s' / f'{component}.yaml').read_text()
            images = re.findall(r'^[ \t]*image:[ \t]*([^\s]+)[ \t]*$', manifest, re.MULTILINE)
            if images == [target] and target != old['image']:
                self.head = gitops.commit_manifest(self.checkout, component, self.head, target, old['image'])
            elif images != [old['image']]:
                raise ValueError('unknown manifest state; manual reconciliation required')
            if live['image'] == target and target != old['image']:
                command('bash', str(HERE / 'promote-reviewed.sh'), component, self.head, old['uid'],
                        target, old['image'], '--apply', cwd=self.checkout)

    def verify_restored(self, admission, snapshot):
        if (gitops.remote_head(self.checkout) != self.head or self._git('rev-parse', 'HEAD') != self.head or
                self._git('status', '--porcelain', '--untracked-files=all')):
            raise ValueError('restored Git state is not current and clean')
        for component in ('cms', 'ssr'):
            if self._live(component) != snapshot['components'][component]:
                raise ValueError('restored deployment differs')
            manifest = (self.checkout / 'deploy/k8s' / f'{component}.yaml').read_text()
            images = re.findall(r'^[ \t]*image:[ \t]*([^\s]+)[ \t]*$', manifest, re.MULTILINE)
            if images != [snapshot['components'][component]['image']]:
                raise ValueError('restored manifest differs')
        self.verify_all(admission, snapshot)
