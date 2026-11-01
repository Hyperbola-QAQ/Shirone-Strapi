#!/usr/bin/env bash
set -euo pipefail

# Manual CD candidate. No scheduler or GitHub runner calls this script.
if [[ "$#" -lt 5 || "$#" -gt 6 ]]; then
  printf '%s\n' 'usage: bash promote-reviewed.sh cms|ssr MAIN_COMMIT DEPLOYMENT_UID PREVIOUS_IMAGE TARGET_IMAGE [--apply]' >&2
  exit 2
fi
component=$1 expected_commit=$2 deployment_uid=$3 previous_image=$4 target_image=$5 mode=${6:-preview}
[[ "$component" == cms || "$component" == ssr ]] || { echo 'invalid component' >&2; exit 2; }
[[ "$mode" == preview || "$mode" == --apply ]] || { echo 'invalid mode' >&2; exit 2; }
[[ "$expected_commit" =~ ^[0-9a-f]{40}$ && "$deployment_uid" =~ ^[0-9a-f-]{36}$ ]] || { echo 'invalid commit or UID' >&2; exit 2; }
image_pattern="^192\\.168\\.86\\.100:30500/shirone/${component}@sha256:[0-9a-f]{64}$"
[[ "$previous_image" =~ $image_pattern && "$target_image" =~ $image_pattern && "$previous_image" != "$target_image" ]] || { echo 'invalid image digest' >&2; exit 2; }
repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
[[ -z "$(git status --porcelain)" ]] || { echo 'worktree is not clean' >&2; exit 1; }
[[ "$(git symbolic-ref --quiet --short HEAD)" == main && "$(git rev-parse HEAD)" == "$expected_commit" ]] || { echo 'expected clean main commit required' >&2; exit 1; }
remote_ref=$(git ls-remote --exit-code origin refs/heads/main)
[[ "${remote_ref%%$'\t'*}" == "$expected_commit" ]] || { echo 'remote main changed or does not match' >&2; exit 1; }

manifest="deploy/k8s/${component}.yaml"
manifest_image=$(python3 - "$manifest" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
images = re.findall(r'^\s+image:\s*([^\s]+)\s*$', text, re.M)
if len(images) != 1:
    raise SystemExit('expected exactly one image in the manifest')
print(images[0])
PY
)
[[ "$manifest_image" == "$target_image" ]] || { echo 'target image differs from committed manifest' >&2; exit 1; }
ops=/home/hyperbola/Work/ansible-debian-k8s
playbook="$ops/update_shirone_${component}_image.yml"
[[ -f "$playbook" && -f "$ops/inventory/hosts.yml" ]] || { echo 'Ansible source missing' >&2; exit 1; }
# The project inventory includes encrypted group vars that these narrowly scoped
# playbooks do not use. Verify the real inventory identity without printing any
# variables, then run the exact, reviewed copy with SSH-key authentication. This
# avoids loading unrelated Vault material and never disables host-key checking.
cmp -s "$playbook" "$repo_root/deploy/ansible/update-${component}-image.yml" || { echo 'Ansible source differs from reviewed copy' >&2; exit 1; }
export ANSIBLE_CONFIG="$ops/ansible.cfg" ANSIBLE_HOST_KEY_CHECKING=True ANSIBLE_BECOME=false
unset ANSIBLE_SSH_ARGS ANSIBLE_SSH_COMMON_ARGS ANSIBLE_SSH_EXTRA_ARGS ANSIBLE_SSH_EXECUTABLE
control_ip=$(ansible-inventory -i "$ops/inventory/hosts.yml" --host hyqaq-imini | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("ansible_host")=="192.168.86.11" and d.get("ansible_user")=="hyperbola"; print(d["ansible_host"])')
scratch=$(mktemp -d /tmp/shirone-promote.XXXXXX)
trap 'if [[ -f "$scratch/playbook.yml" ]]; then rm "$scratch/playbook.yml"; fi; rmdir "$scratch"' EXIT
cp "$playbook" "$scratch/playbook.yml"
cmp -s "$playbook" "$scratch/playbook.yml" || { echo 'playbook copy differs' >&2; exit 1; }
args=(-i "$control_ip," -u hyperbola "$scratch/playbook.yml" -e '{"ansible_ssh_common_args":"-o StrictHostKeyChecking=yes"}' -e "shirone_preview_hosts=$control_ip" -e "shirone_${component}_uid=$deployment_uid" -e "shirone_previous_image=$previous_image")
if [[ "$mode" == --apply ]]; then args+=(-e "shirone_apply_${component}_image=true"); fi
export SHIRONE_WORKSPACE="$repo_root"
cd "$scratch"
listed_hosts=$(ansible "$control_ip" -i "$control_ip," -u hyperbola --list-hosts)
[[ "$listed_hosts" == $'  hosts (1):\n    '"$control_ip" ]] || { echo 'reviewed control-plane host not selected' >&2; exit 1; }
ansible-playbook "${args[@]}"
if [[ "$mode" == --apply ]]; then
  ansible "$control_ip" -i "$control_ip," -u hyperbola -e '{"ansible_ssh_common_args":"-o StrictHostKeyChecking=yes"}' -m command -a "kubectl --kubeconfig=/home/hyperbola/.kube/config -n mizuki rollout status deployment/shirone-${component} --timeout=180s"
  curl --fail --silent --show-error --max-time 20 --output /dev/null https://hyperbola.cc/
  curl --fail --silent --show-error --max-time 20 --output /dev/null https://strapi.hyperbola.cc/admin
  printf '%s\n' 'Rollout and public health checks passed; verify publication/search and recorded digest.'
else
  printf '%s\n' 'Admission preview completed; no production update requested.'
fi
