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
import pwd
import re
import socket
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


def local_target():
    mode = os.environ.get('SHIRONE_CONTROLLER_LOCAL', '0')
    if mode not in ('0', '1'):
        raise ValueError('invalid local controller mode')
    if mode == '0':
        return False
    if socket.gethostname() != 'hyqaq-imini' or pwd.getpwuid(os.geteuid()).pw_name != 'hyperbola':
        raise ValueError('local controller is not on the reviewed host')
    addresses = json.loads(command('/usr/bin/ip', '-j', 'address', 'show'))
    if not any(item.get('local') == '192.168.86.11' for interface in addresses
               for item in interface.get('addr_info', [])):
        raise ValueError('reviewed management address missing')
    return True


def kubectl(*args):
    target = ('kubectl', '--kubeconfig=/home/hyperbola/.kube/config', '-n', 'mizuki', *args)
    if local_target():
        return command(*target)
    return command('ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                   'hyperbola@192.168.86.11', *target)


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
        self.pending_commit = None
        self.recovery_checkout = None
        self.preserved_checkout = None

    def _git(self, *args):
        return gitops.git(self.checkout, *args)

    def _live(self, component):
        raw = kubectl('get', 'deployment', f'shirone-{component}', '-o', 'json')
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
                connection = ('-c', 'local') if local_target() else ()
                command('ansible-playbook', '-i', '192.168.86.11,', '-u', 'hyperbola', *connection, str(isolated_playbook),
                        '-e', '{"ansible_ssh_common_args":"-o StrictHostKeyChecking=yes"}',
                        '-e', 'shirone_preview_hosts=192.168.86.11',
                        '-e', f"shirone_{component}_uid={snapshot['components'][component]['uid']}",
                        '-e', f'shirone_previous_image={old}', cwd=scratch, env=environment)

    def promote(self, component, admission, snapshot):
        old = snapshot['components'][component]['image']
        target = f"192.168.86.100:30500/shirone/{component}@{admission['manifests'][component]}"
        if old == target:
            return
        self.pending_commit = (component, self.head, old, target)
        self.head = gitops.commit_manifest(self.checkout, component, self.head, old, target)
        self.pending_commit = None
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
        kubectl('rollout', 'status', f'deployment/shirone-{component}', '--timeout=180s')
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

    def _reconcile_pending_commit(self, accepted_only=False):
        """Recognize an exact committed and pushed change after a lost reply.

        An unpushed exact commit is fenced by a same-tree child of its parent,
        pushed with a server-enforced lease. This prevents a delayed original
        push from winning after recovery. Any other state remains untouched.
        """
        if self.pending_commit is None:
            return
        component, previous, old, target = self.pending_commit
        if self._git('status', '--porcelain', '--untracked-files=all'):
            if accepted_only:
                raise ValueError('compensation checkout is dirty; manual reconciliation required')
            current = self._git('rev-parse', 'HEAD')
            remote = gitops.remote_head(self.checkout)
            if current != previous and remote == current:
                self._verify_pending_tree(component, previous, current, old, target)
                self._use_recovery_clone(current)
            else:
                self._recover_preserved_checkout(component, previous, old)
            return
        if self._git('symbolic-ref', '--quiet', '--short', 'HEAD') != 'main':
            raise ValueError('pending commit checkout is not main')
        current = self._git('rev-parse', 'HEAD')
        remote = gitops.remote_head(self.checkout)
        if current == previous and remote == previous:
            if accepted_only:
                raise ValueError('compensation was not confirmed on remote')
            self.head = previous
            self.pending_commit = None
            return
        if accepted_only and remote != current:
            raise ValueError('compensation push outcome is uncertain')
        if remote not in (previous, current):
            raise ValueError('pending commit remote state is uncertain')
        try:
            expected = self._verify_pending_tree(component, previous, current, old, target)
            if (self.checkout / f'deploy/k8s/{component}.yaml').read_bytes() != expected:
                raise ValueError('pending checkout differs from commit')
        except ValueError:
            if remote == previous:
                self._recover_preserved_checkout(component, previous, old)
                return
            raise
        if remote == previous:
            # Whichever of the delayed push and this fence first updates the
            # ref wins the same lease. A fence has exactly the previous tree;
            # it never publishes the candidate or overwrites another writer.
            tree = self._git('rev-parse', f'{previous}^{{tree}}')
            fence = self._git('commit-tree', tree, '-p', previous,
                              '-m', 'ci: fence uncertain release push')
            if not re.fullmatch(r'[0-9a-f]{40}', fence):
                raise ValueError('invalid pending commit fence')
            try:
                self._git('push', f'--force-with-lease=refs/heads/main:{previous}',
                          'origin', f'{fence}:refs/heads/main')
            except (subprocess.CalledProcessError, TimeoutError):
                # A lost response can mean either our fence or the original
                # push succeeded. Inspect the remote once; never force either.
                pass
            remote = gitops.remote_head(self.checkout)
            if remote == fence:
                if self._git('status', '--porcelain', '--untracked-files=all') or self._git('rev-parse', 'HEAD') != current:
                    raise ValueError('checkout changed before accepting fence')
                # Only the fully checked controller-generated commit is being
                # abandoned locally; --keep refuses concurrent file changes.
                self._git('reset', '--keep', fence)
                if gitops.remote_head(self.checkout) != fence:
                    raise ValueError('remote changed after accepting fence')
                self.head = fence
                self.pending_commit = None
                return
            if remote != current:
                raise ValueError('pending commit fence outcome is uncertain')
        self.head = current
        self.pending_commit = None

    def _verify_pending_tree(self, component, previous, current, old, target):
        relative = f'deploy/k8s/{component}.yaml'
        parents = self._git('rev-list', '--parents', '-n', '1', current).split()
        changed = self._git('diff-tree', '--no-commit-id', '--name-only', '-r', previous, current).splitlines()
        if parents != [current, previous] or changed != [relative]:
            raise ValueError('pending commit is not the expected image-only direct child')
        def blob(revision):
            return subprocess.run(['git', '-C', str(self.checkout), 'show', f'{revision}:{relative}'],
                                  check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        before, after = blob(previous), blob(current)
        pattern = re.compile(rb'^([ \t]*image:[ \t]*)([^\s]+)([ \t]*)$', re.MULTILINE)
        matches = list(pattern.finditer(before))
        if len(matches) != 1 or matches[0].group(2) != old.encode():
            raise ValueError('pending commit source image differs')
        found = matches[0]
        expected = before[:found.start(2)] + target.encode() + before[found.end(2):]
        if after != expected:
            raise ValueError('pending commit includes unexpected changes')
        return expected

    def _recover_preserved_checkout(self, component, previous, old):
        """Fence and recover from a clone, leaving all local work untouched."""
        current = self._git('rev-parse', 'HEAD')
        if (self._git('symbolic-ref', '--quiet', '--short', 'HEAD') != 'main' or
                gitops.remote_head(self.checkout) != previous or
                (current != previous and self._git('rev-list', '--parents', '-n', '1', current).split() != [current, previous])):
            raise ValueError('pending checkout or remote changed')
        relative = f'deploy/k8s/{component}.yaml'
        before = subprocess.run(['git', '-C', str(self.checkout), 'show', f'{previous}:{relative}'],
                                check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        pattern = re.compile(rb'^([ \t]*image:[ \t]*)([^\s]+)([ \t]*)$', re.MULTILINE)
        matches = list(pattern.finditer(before))
        if len(matches) != 1 or matches[0].group(2) != old.encode():
            raise ValueError('dirty pending source image differs')
        # Nothing in the original index or working tree is modified, even if
        # it contains unrelated user edits or partially written files. The
        # clean clone below is created from the validated remote parent only.
        tree = self._git('rev-parse', f'{previous}^{{tree}}')
        fence = self._git('commit-tree', tree, '-p', previous, '-m', 'ci: fence dirty release checkout')
        if not re.fullmatch(r'[0-9a-f]{40}', fence):
            raise ValueError('invalid dirty checkout fence')
        try:
            self._git('push', f'--force-with-lease=refs/heads/main:{previous}',
                      'origin', f'{fence}:refs/heads/main')
        except (subprocess.CalledProcessError, TimeoutError):
            pass
        if gitops.remote_head(self.checkout) != fence:
            raise ValueError('dirty checkout fence outcome is uncertain')
        self._use_recovery_clone(fence)

    def _use_recovery_clone(self, revision):
        if self.recovery_checkout is not None:
            raise ValueError('nested recovery checkout requires manual reconciliation')
        origin = self._git('remote', 'get-url', 'origin')
        name, email = self._git('config', 'user.name'), self._git('config', 'user.email')
        self.recovery_checkout = tempfile.TemporaryDirectory(prefix='shirone-release-recovery-')
        clone = Path(self.recovery_checkout.name) / 'checkout'
        try:
            subprocess.run(['git', 'clone', '--no-local', '--single-branch', '--branch', 'main', '--', origin, str(clone)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            gitops.git(clone, 'config', 'user.name', name)
            gitops.git(clone, 'config', 'user.email', email)
            if (gitops.git(clone, 'rev-parse', 'HEAD') != revision or gitops.remote_head(clone) != revision or
                    gitops.git(clone, 'status', '--porcelain', '--untracked-files=all')):
                raise ValueError('recovery clone differs from fenced remote')
        except Exception:
            self.recovery_checkout.cleanup()
            self.recovery_checkout = None
            raise
        self.preserved_checkout = self.checkout
        self.checkout = clone
        self.head = revision
        self.pending_commit = None

    def restore(self, admission, snapshot):
        self._reconcile_pending_commit()
        for component in ('ssr', 'cms'):
            old = snapshot['components'][component]
            target = f"192.168.86.100:30500/shirone/{component}@{admission['manifests'][component]}"
            live = self._live(component)
            if live['uid'] != old['uid'] or live['image'] not in (old['image'], target):
                raise ValueError('unknown live state; manual reconciliation required')
            manifest = (self.checkout / 'deploy/k8s' / f'{component}.yaml').read_text()
            images = re.findall(r'^[ \t]*image:[ \t]*([^\s]+)[ \t]*$', manifest, re.MULTILINE)
            if images == [target] and target != old['image']:
                self.pending_commit = (component, self.head, target, old['image'])
                try:
                    self.head = gitops.commit_manifest(self.checkout, component, self.head, target, old['image'])
                    self.pending_commit = None
                except Exception:
                    # Never retry a compensation. Continue only if the exact
                    # direct-child image-only commit is already on remote.
                    self._reconcile_pending_commit(accepted_only=True)
            elif images != [old['image']]:
                raise ValueError('unknown manifest state; manual reconciliation required')
            if live['image'] == target and target != old['image']:
                command('bash', str(self.checkout / 'deploy/ci/promote-reviewed.sh'), component, self.head, old['uid'],
                        target, old['image'], '--apply', cwd=self.checkout)

    def verify_restored(self, admission, snapshot):
        if (gitops.remote_head(self.checkout) != self.head or self._git('rev-parse', 'HEAD') != self.head or
                self._git('status', '--porcelain', '--untracked-files=all')):
            raise ValueError('restored Git state is not current and clean')
        for component in ('cms', 'ssr'):
            kubectl('rollout', 'status', f'deployment/shirone-{component}', '--timeout=180s')
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
        if self.recovery_checkout is not None:
            self.recovery_checkout.cleanup()
            self.recovery_checkout = None
            self.checkout = self.preserved_checkout
