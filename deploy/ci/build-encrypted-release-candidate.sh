#!/usr/bin/env bash
set -euo pipefail

# Local integration candidate only. This does not attest, upload, deploy or
# authorize release, and the age recipient must be independently trusted.
[[ "${SHIRONE_CI_DISPOSABLE:-}" == yes ]] || { echo 'disposable runner required' >&2; exit 2; }
[[ $# == 3 && "$1" =~ ^[0-9a-f]{40}$ && "$3" == /* ]] || {
  echo 'usage: build-encrypted-release-candidate.sh EXPECTED_COMMIT AGE_RECIPIENT NEW_CIPHERTEXT' >&2; exit 2;
}
commit=$1 recipient=$2 ciphertext=$3
[[ "$recipient" =~ ^age1[023456789acdefghjklmnpqrstuvwxyz]{58}$ ]] || { echo 'invalid age recipient' >&2; exit 2; }
command -v age >/dev/null
root=$(git rev-parse --show-toplevel)
cd "$root"
[[ "$(git rev-parse HEAD)" == "$commit" && -z "$(git status --porcelain --untracked-files=all)" ]] || {
  echo 'clean exact source commit required' >&2; exit 1;
}
umask 077
scratch=$(mktemp -d /tmp/shirone-encrypted-release.XXXXXX)
node_tag=''
pg_tag=''
cleanup() {
  if [[ -n "$node_tag" ]]; then docker image rm "$node_tag" >/dev/null 2>&1 || true; fi
  if [[ -n "$pg_tag" ]]; then docker image rm "$pg_tag" >/dev/null 2>&1 || true; fi
  find "$scratch" -depth -delete
}
trap cleanup EXIT
echo 'SHIRONE_STAGE=bases' >&2
SHIRONE_CI_DISPOSABLE=yes bash deploy/ci/rebuild-hosted-bases-candidate.sh "$commit" "$scratch/bases" >&2
node_tag=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["node_local_tag"])' "$scratch/bases/bases.json")
pg_tag=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["postgres_local_tag"])' "$scratch/bases/bases.json")
node_id=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["node_image_id"])' "$scratch/bases/bases.json")
pg_id=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["postgres_image_id"])' "$scratch/bases/bases.json")
[[ "$(docker image inspect --format '{{.Id}}' "$node_tag")" == "$node_id" && "$(docker image inspect --format '{{.Id}}' "$pg_tag")" == "$pg_id" ]]
meili='docker.m.daocloud.io/getmeili/meilisearch@sha256:cad32322d74319f2076f41828094774fbbacacff6bda32e317933293b76db616'
echo 'SHIRONE_STAGE=meili_pull' >&2
docker pull --platform linux/amd64 "$meili" >/dev/null
echo 'SHIRONE_STAGE=source_tests' >&2
SHIRONE_CI_DISPOSABLE=yes NODE_IMAGE="$node_tag" POSTGRES_TEST_IMAGE="$pg_tag" MEILI_TEST_IMAGE="$meili" \
  NODE_BASE_OCI_ARCHIVE="${SHIRONE_OCI_BASE_CONTEXT:+$scratch/bases/node-base.oci.tar}" \
  bash deploy/ci/build-tested-oci-candidate.sh "$commit" "$scratch/images" >&2
[[ "$(docker image inspect --format '{{.Id}}' "$node_tag")" == "$node_id" && "$(docker image inspect --format '{{.Id}}' "$pg_tag")" == "$pg_id" ]]
fingerprint=$(python3 -c 'import hashlib,sys;print("sha256:"+hashlib.sha256(sys.argv[1].encode("ascii")).hexdigest())' "$recipient")
echo 'SHIRONE_STAGE=envelope' >&2
python3 deploy/ci/release-envelope-candidate.py build "$scratch/envelope.tar" \
  --source "$commit" --recipient "$fingerprint" --cms "$scratch/images/cms.oci.tar" \
  --ssr "$scratch/images/ssr.oci.tar" --reconciler "$scratch/images/reconciler.oci.tar" > "$scratch/envelope.json"
cms=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["cms"]["manifest_digest"])' "$scratch/envelope.json")
ssr=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["ssr"]["manifest_digest"])' "$scratch/envelope.json")
reconciler=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["reconciler"]["manifest_digest"])' "$scratch/envelope.json")
echo 'SHIRONE_STAGE=encrypt' >&2
python3 deploy/ci/encrypted-release-candidate.py encrypt --plaintext "$scratch/envelope.tar" \
  --ciphertext "$ciphertext" --recipient "$recipient" --source "$commit" \
  --cms-manifest "$cms" --ssr-manifest "$ssr" --reconciler-manifest "$reconciler" >/dev/null
ciphertext_hash="sha256:$(sha256sum "$ciphertext" | cut -d' ' -f1)"
# These non-secret identities must themselves be attested; workflow logs alone
# are not an authorization source. Emit exactly one strict JSON object.
python3 - "$commit" "$fingerprint" "$ciphertext_hash" "$cms" "$ssr" "$reconciler" <<'PY'
import json,re,sys
commit,recipient,ciphertext,*manifests = sys.argv[1:]
assert re.fullmatch(r'[0-9a-f]{40}', commit)
assert all(re.fullmatch(r'sha256:[0-9a-f]{64}', value) for value in [recipient,ciphertext,*manifests])
print(json.dumps({'schema': 1, 'source_commit': commit, 'recipient_fingerprint': recipient,
                  'ciphertext_digest': ciphertext,
                  'manifests': dict(zip(('cms','ssr','reconciler'), manifests))},
                 sort_keys=True, separators=(',', ':')))
PY
