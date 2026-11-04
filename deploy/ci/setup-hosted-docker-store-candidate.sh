#!/usr/bin/env bash
set -euo pipefail

# Disposable GitHub-hosted VM only. The classic image store cannot load the
# OCI archives used by this pipeline. Never run this on a persistent host.
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]] || {
  echo 'A disposable GitHub-hosted runner is required' >&2; exit 2;
}
sudo python3 - <<'PY'
import json, os, pathlib, stat, tempfile
p = pathlib.Path('/etc/docker/daemon.json')
p.parent.mkdir(parents=True, exist_ok=True)
if p.is_symlink():
    raise SystemExit('refusing symlinked Docker configuration')
if p.exists():
    if not stat.S_ISREG(p.stat().st_mode):
        raise SystemExit('Docker configuration is not a regular file')
    d = json.loads(p.read_text())
else:
    d = {}
if not isinstance(d, dict) or not isinstance(d.get('features', {}), dict):
    raise SystemExit('unexpected Docker configuration')
d.setdefault('features', {})['containerd-snapshotter'] = True
fd, name = tempfile.mkstemp(prefix='.shirone-daemon-', dir=p.parent)
try:
    os.fchmod(fd, 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(d, f, sort_keys=True)
        f.write('\n')
    os.replace(name, p)
finally:
    if os.path.exists(name):
        os.unlink(name)
PY
sudo systemctl restart docker
docker info --format '{{json .DriverStatus}}' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert any(len(x)==2 and x[0]=="driver-type" and x[1]=="io.containerd.snapshotter.v1" for x in d)'
