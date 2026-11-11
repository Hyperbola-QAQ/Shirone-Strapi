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
import tempfile
from urllib.parse import urlencode

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


def command(*args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout


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
                            ciphertext_digest, recipient_fingerprint, checkout=self.checkout)
        return {'source': source, 'signer': self.policy['signer'], 'ciphertext_digest': ciphertext_digest,
                'manifests': signed['manifests']}

    def snapshot(self, admission):
        directory = self.previous_record.parent.stat()
        if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid() or directory.st_mode & 0o077:
            raise ValueError('previous release record directory is not private')
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
        if (not isinstance(previous, dict) or set(previous) != {'source', 'components', 'rollback_compatible', 'search_probe'} or
                not isinstance(previous['source'], str) or not re.fullmatch(r'[0-9a-f]{40}', previous['source']) or
                previous['rollback_compatible'] is not True or not isinstance(previous['components'], dict) or
                set(previous['components']) != {'cms', 'ssr'}):
            raise ValueError('previous release record is incomplete')
        probe = previous['search_probe']
        if (not isinstance(probe, dict) or set(probe) != {'query', 'url'} or
                not isinstance(probe['query'], str) or not 1 <= len(probe['query']) <= 120 or
                not isinstance(probe['url'], str) or not probe['url'].startswith('/posts/') or
                '\n' in probe['url'] or len(probe['url']) > 2048):
            raise ValueError('invalid published search probe')
        # Conservative compatibility gate: no CMS or its dependency inputs may
        # change. CMS migrations need a separate reviewed recovery procedure.
        changed = self._git('diff', '--name-only', previous['source'], admission['source'], '--',
                            'cms', 'Dockerfile', 'package.json', 'pnpm-lock.yaml',
                            'deploy/ci/build-tested-oci-candidate.sh',
                            'deploy/ci/build-encrypted-release-candidate.sh',
                            'deploy/ci/rebuild-hosted-bases-candidate.sh',
                            'deploy/ci/Dockerfile.node-reconstruction-candidate')
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
        return {'rollback_safe': True, 'components': components, 'source': admission['source'],
                'previous_source': previous['source'], 'search_probe': probe}

    def preview(self, admission, snapshot):
        if gitops.remote_head(self.checkout) != self.head:
            raise ValueError('main changed before promotion')
        # A pre-existing outage or withdrawn probe must stop the release
        # before any manifest push or production change.
        self.verify_all(admission, snapshot)
        ops = Path('/home/hyperbola/Work/ansible-debian-k8s')
        inventory = json.loads(command('ansible-inventory', '-i', str(ops / 'inventory/hosts.yml'), '--host', 'hyqaq-imini'))
        if inventory.get('ansible_host') != '192.168.86.11' or inventory.get('ansible_user') != 'hyperbola':
            raise ValueError('Ansible inventory identity differs')
        with tempfile.TemporaryDirectory(prefix='shirone-release-preview-') as scratch_name:
            scratch = Path(scratch_name)
            plain = scratch / 'release.tar'
            promotion.crypto.decrypt(self.ciphertext, plain, self.key, self.recipient,
                                     admission['source'], admission['signer'], admission['ciphertext_digest'],
                                     admission['manifests'])
            images = scratch / 'images'
            promotion.env.inspect(plain, admission['source'],
                                  promotion.crypto.recipient_fingerprint(self.recipient),
                                  admission['manifests'], images, verify_oci=True)
            manifest_dir = scratch / 'deploy/k8s'
            manifest_dir.mkdir(parents=True)
            for component in ('cms', 'ssr'):
                playbook = ops / f'update_shirone_{component}_image.yml'
                reviewed = self.checkout / 'deploy/ansible' / f'update-{component}-image.yml'
                if playbook.read_bytes() != reviewed.read_bytes():
                    raise ValueError('Ansible playbook differs from reviewed source')
                # Execute the exact reviewed copy from the isolated workspace,
                # as promote-reviewed.sh does, so unrelated encrypted project
                # group vars are not loaded for this narrow playbook.
                isolated_playbook = scratch / f'update-{component}-image.yml'
                isolated_playbook.write_bytes(playbook.read_bytes())
                if isolated_playbook.read_bytes() != reviewed.read_bytes():
                    raise ValueError('isolated playbook copy differs')
                manifest = (self.checkout / 'deploy/k8s' / f'{component}.yaml').read_text()
                old = snapshot['components'][component]['image']
                target = f"192.168.86.100:30500/shirone/{component}@{admission['manifests'][component]}"
                if manifest.count(old) != 1:
                    raise ValueError('unexpected preflight manifest')
                (manifest_dir / f'{component}.yaml').write_text(manifest.replace(old, target))
                environment = dict(os.environ, ANSIBLE_CONFIG=str(ops / 'ansible.cfg'),
                                   ANSIBLE_HOST_KEY_CHECKING='True', ANSIBLE_BECOME='false', SHIRONE_WORKSPACE=str(scratch))
                for name in ('ANSIBLE_SSH_ARGS', 'ANSIBLE_SSH_COMMON_ARGS', 'ANSIBLE_SSH_EXTRA_ARGS', 'ANSIBLE_SSH_EXECUTABLE'):
                    environment.pop(name, None)
                command('ansible-playbook', '-i', '192.168.86.11,', '-u', 'hyperbola', str(isolated_playbook),
                        '-e', '{"ansible_ssh_common_args":"-o StrictHostKeyChecking=yes"}',
                        '-e', 'shirone_preview_hosts=192.168.86.11',
                        '-e', f"shirone_{component}_uid={snapshot['components'][component]['uid']}",
                        '-e', f'shirone_previous_image={old}', cwd=scratch, env=environment)

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
        command('ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                'hyperbola@192.168.86.11', 'kubectl', '--kubeconfig=/home/hyperbola/.kube/config',
                '-n', 'mizuki', 'rollout', 'status', f'deployment/shirone-{component}', '--timeout=180s')
        expected = f"192.168.86.100:30500/shirone/{component}@{admission['manifests'][component]}"
        current = self._live(component)
        if current != {'uid': snapshot['components'][component]['uid'], 'image': expected}:
            raise ValueError('live image or deployment UID differs')

    def verify_all(self, admission, snapshot):
        for url in ('https://hyperbola.cc/', 'https://strapi.hyperbola.cc/admin/'):
            command('curl', '--fail', '--silent', '--show-error', '--max-time', '20', '--output', '/dev/null', url)
        probe = snapshot['search_probe']
        result = json.loads(command('curl', '--fail', '--silent', '--show-error', '--location', '--max-time', '20',
                                    'https://hyperbola.cc/api/search/?' + urlencode({'q': probe['query']})))
        if not isinstance(result, dict) or not isinstance(result.get('hits'), list) or not any(
                isinstance(hit, dict) and hit.get('url') == probe['url'] for hit in result['hits']):
            raise ValueError('published search probe is missing')

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
            command('ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                    'hyperbola@192.168.86.11', 'kubectl', '--kubeconfig=/home/hyperbola/.kube/config',
                    '-n', 'mizuki', 'rollout', 'status', f'deployment/shirone-{component}', '--timeout=180s')
            if self._live(component) != snapshot['components'][component]:
                raise ValueError('restored deployment differs')
            manifest = (self.checkout / 'deploy/k8s' / f'{component}.yaml').read_text()
            images = re.findall(r'^[ \t]*image:[ \t]*([^\s]+)[ \t]*$', manifest, re.MULTILINE)
            if images != [snapshot['components'][component]['image']]:
                raise ValueError('restored manifest differs')
        self.verify_all(admission, snapshot)

    def _write_record(self, source, components, search_probe):
        # The caller holds the controller lock and has verified live state.
        # Atomic replacement avoids exposing a partially written record; if
        # interrupted, the write-once reservation requires reconciliation.
        parent = self.previous_record.parent
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('release record directory is not private')
        fd, name = tempfile.mkstemp(prefix='.previous-release-', dir=parent)
        try:
            with os.fdopen(fd, 'w') as file:
                json.dump({'source': source, 'components': components, 'rollback_compatible': True,
                           'search_probe': search_probe}, file, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.previous_record)
            dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def finalize(self, admission, snapshot):
        components = {name: self._live(name) for name in ('cms', 'ssr')}
        for name, current in components.items():
            expected = f"192.168.86.100:30500/shirone/{name}@{admission['manifests'][name]}"
            if current != {'uid': snapshot['components'][name]['uid'], 'image': expected}:
                raise ValueError('deployment changed before release record update')
        self._write_record(admission['source'], components, snapshot['search_probe'])

    def restored(self, admission, snapshot):
        # Snapshot retains the old source even if finalize had succeeded before
        # a later error, avoiding incorrectly recording the new build on rollback.
        self._write_record(snapshot['previous_source'], snapshot['components'], snapshot['search_probe'])
