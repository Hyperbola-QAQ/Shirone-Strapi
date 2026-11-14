#!/usr/bin/env bash
set -euo pipefail

# Rebuild disposable test inputs from public, digest-pinned parents. This does
# not attest the result, prove full filesystem equivalence, or authorize release.
[[ "${SHIRONE_CI_DISPOSABLE:-}" == yes ]] || { echo 'disposable runner required' >&2; exit 2; }
[[ $# == 2 && "$1" =~ ^[0-9a-f]{40}$ && "$2" == /* ]] || {
  echo 'usage: rebuild-hosted-bases-candidate.sh EXPECTED_COMMIT NEW_OUTPUT_DIRECTORY' >&2; exit 2;
}
commit=$1 output=$2
root=$(git rev-parse --show-toplevel)
cd "$root"
[[ "$(git rev-parse HEAD)" == "$commit" && -z "$(git status --porcelain --untracked-files=all)" ]] || {
  echo 'clean exact source commit required' >&2; exit 1;
}
node_tag="shirone-node-base-experiment:${commit:0:12}-$$"
pg_tag="shirone-pg-base-experiment:${commit:0:12}-$$"
umask 077
mkdir -m 700 "$output"
scratch=$(mktemp -d /tmp/shirone-hosted-base.XXXXXX)
container=''
cleanup() {
  if [[ -n "$container" ]]; then docker rm "$container" >/dev/null 2>&1 || true; fi
  find "$scratch" -depth -delete
}
trap cleanup EXIT

echo 'SHIRONE_STAGE=base_pull_node' >&2
node_parent=$(bash deploy/ci/pull-hosted-base.sh node)
echo 'SHIRONE_STAGE=base_pull_postgres' >&2
pg_parent=$(bash deploy/ci/pull-hosted-base.sh postgres)
case "$node_parent" in
  docker.m.daocloud.io/library/node@sha256:25330af3531fb5e23318554a0aa911125b6e91b1b777edf7655501d207c067a2|docker.io/library/node@sha256:25330af3531fb5e23318554a0aa911125b6e91b1b777edf7655501d207c067a2) ;;
  *) echo 'invalid selected Node parent' >&2; exit 1 ;;
esac
case "$pg_parent" in
  ghcr.m.daocloud.io/cloudnative-pg/postgresql@sha256:e6e1f467b52112f7e08aa380de81473106ff54b1834d90bcb4265a2a018e1921|ghcr.io/cloudnative-pg/postgresql@sha256:e6e1f467b52112f7e08aa380de81473106ff54b1834d90bcb4265a2a018e1921) ;;
  *) echo 'invalid selected PostgreSQL parent' >&2; exit 1 ;;
esac

node_archive="$scratch/node.tar.xz"
echo 'SHIRONE_STAGE=node_download' >&2
node_sha=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce
fetch_node() {
  curl --fail --silent --show-error --location --connect-timeout 15 --max-time 180 "$1" -o "$node_archive" &&
    printf '%s  %s\n' "$node_sha" "$node_archive" | sha256sum --check --status
}
if ! fetch_node 'https://npmmirror.com/mirrors/node/v22.23.4/node-v22.23.4-linux-x64.tar.xz'; then
  echo 'Domestic Node mirror failed or checksum mismatched; retrying the official source with identical checksum.' >&2
  fetch_node 'https://nodejs.org/dist/v22.23.4/node-v22.23.4-linux-x64.tar.xz' || exit 1
fi
mkdir "$scratch/node-context"
tar -xJOf "$node_archive" node-v22.23.4-linux-x64/bin/node > "$scratch/node-context/node"
printf '%s  %s\n' 94efd062ed066c1a0b332e18249859c905e920eeada5df55d24c99b919629a90 "$scratch/node-context/node" | sha256sum --check --status
echo 'SHIRONE_STAGE=node_base_build' >&2
node_base_manifest=''
if [[ "${SHIRONE_OCI_BASE_CONTEXT:-}" == yes ]]; then
  # A docker-container builder cannot resolve a tag from the host image store.
  # Export the actual tested base to OCI, validate it, and load the same bytes
  # into Docker only for the version/trust-store checks below.
  docker buildx build --platform linux/amd64 --provenance=false --network=none --build-arg "BASE_IMAGE=$node_parent" \
    -f deploy/ci/Dockerfile.node-reconstruction-candidate -t "$node_tag" \
    --output "type=oci,dest=$output/node-base.oci.tar" "$scratch/node-context" >/dev/null
  python3 deploy/ci/verify-oci-archive-candidate.py "$output/node-base.oci.tar" > "$output/node-base.identity.json"
  node_base_manifest=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["manifest_digest"])' "$output/node-base.identity.json")
  docker load --input "$output/node-base.oci.tar" >/dev/null
else
  docker build --network=none --pull=false --build-arg "BASE_IMAGE=$node_parent" -f deploy/ci/Dockerfile.node-reconstruction-candidate -t "$node_tag" "$scratch/node-context" >/dev/null
fi

echo 'SHIRONE_STAGE=pg_base_build' >&2
container=$(docker create "$pg_parent")
docker export "$container" > "$scratch/pg-parent.tar"
docker rm "$container" >/dev/null
container=''
python3 deploy/ci/prepare-ca-overlay-candidate.py "$scratch/pg-parent.tar" "$scratch/pg-context"
python3 - "$scratch/pg-context/report.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
assert len(r['changed']) == 4 and len(r['deleted']) == 6 and r['denied_occurrences_removed'] == 8
PY
cat > "$scratch/pg-context/Dockerfile" <<'EOF'
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER 0
COPY ca-files.tar apply-ca-overlay.sh /tmp/
RUN sh /tmp/apply-ca-overlay.sh
USER 26
EOF
if [[ "${SHIRONE_OCI_BASE_CONTEXT:-}" == yes ]]; then
  # The container driver otherwise leaves the result only in its own cache;
  # the following runtime checks and integration tests use the Docker store.
  docker buildx build --network=none --pull=false --load --build-arg "BASE_IMAGE=$pg_parent" -t "$pg_tag" "$scratch/pg-context" >/dev/null
else
  docker build --network=none --pull=false --build-arg "BASE_IMAGE=$pg_parent" -t "$pg_tag" "$scratch/pg-context" >/dev/null
fi

echo 'SHIRONE_STAGE=base_checks' >&2
[[ "$(docker run --rm --network none --entrypoint node "$node_tag" --version)" == v22.23.4 ]]
[[ "$(docker run --rm --network none --entrypoint npm "$node_tag" --version)" == 10.9.9 ]]
pg_version=$(docker run --rm --network none --entrypoint postgres "$pg_tag" --version)
[[ "$pg_version" == *'PostgreSQL) 18.4 '* ]]
node_roots=$(docker run --rm --network none --entrypoint node "$node_tag" -e 'const c=require("crypto"),t=require("tls"); const p=t.rootCertificates.map(x=>new c.X509Certificate(x).fingerprint256.replaceAll(":","").toLowerCase()).sort().join("\n");console.log(c.createHash("sha256").update(p).digest("hex"),t.rootCertificates.length)')
pg_roots=$(docker run --rm --network none --entrypoint python3 "$pg_tag" -c 'import ssl,hashlib; r=ssl.create_default_context().get_ca_certs(binary_form=True); s="\n".join(sorted(hashlib.sha256(b).hexdigest() for b in r)); print(hashlib.sha256(s.encode()).hexdigest(),len(r))')
[[ "$node_roots" == 'f8a4aab1d0a7ba937cc911664db2949bf93df7149e134a6341ebd45dcea9b34a 116' ]]
[[ "$pg_roots" == '7f8254cff41f9dbe6c289c5664e233de811c8cb4cf21674fd078cf8d4d1a8fdd 148' ]]
node_id=$(docker image inspect --format '{{.Id}}' "$node_tag")
pg_id=$(docker image inspect --format '{{.Id}}' "$pg_tag")
if [[ "${SHIRONE_OCI_BASE_CONTEXT:-}" == yes ]]; then
  node_config=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["config_digest"])' "$output/node-base.identity.json")
  [[ "$node_id" == "$node_config" || "$node_id" == "$node_base_manifest" ]] || {
    echo 'Loaded Node base differs from verified OCI identity' >&2; exit 1;
  }
fi
python3 - "$output/bases.json" "$commit" "$node_id" "$pg_id" "$node_tag" "$pg_tag" "$node_base_manifest" <<'PY'
import json,sys
with open(sys.argv[1], 'x') as f:
    json.dump({'source_commit':sys.argv[2], 'node_image_id':sys.argv[3], 'postgres_image_id':sys.argv[4],
               'node_local_tag':sys.argv[5], 'postgres_local_tag':sys.argv[6], 'node_base_manifest':sys.argv[7],
               'release_approved':False}, f, sort_keys=True)
PY
printf '%s\n' 'Reconstructed isolated test bases; full equivalence and release authorization remain unverified.'
