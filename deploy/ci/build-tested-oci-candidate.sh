#!/usr/bin/env bash
set -euo pipefail

# Offline output candidate only. It neither signs nor uploads nor authorizes a
# release. Run solely in a fresh disposable runner with no production secrets.
[[ "${SHIRONE_CI_DISPOSABLE:-}" == yes ]] || { echo 'disposable runner required' >&2; exit 2; }
[[ $# == 2 ]] || { echo 'usage: build-tested-oci-candidate.sh EXPECTED_COMMIT NEW_OUTPUT_DIRECTORY' >&2; exit 2; }
commit=$1 output=$2
[[ "$commit" =~ ^[0-9a-f]{40}$ && "$output" == /* ]] || { echo 'invalid commit or output path' >&2; exit 2; }
: "${NODE_IMAGE:?}" "${POSTGRES_TEST_IMAGE:?}" "${MEILI_TEST_IMAGE:?}"
root=$(git rev-parse --show-toplevel)
cd "$root"
[[ "$(git rev-parse HEAD)" == "$commit" && -z "$(git status --porcelain --untracked-files=all)" ]] || {
  echo 'a clean checkout of the exact expected commit is required' >&2; exit 1;
}
for image in "$NODE_IMAGE" "$POSTGRES_TEST_IMAGE" "$MEILI_TEST_IMAGE"; do
  docker image inspect "$image" >/dev/null
done
[[ "$(pnpm --version)" == 12.16.1 ]] || { echo 'pnpm 12.16.1 required' >&2; exit 2; }
node -e 'const n=+process.versions.node.split(".")[0]; if(n<20||n>24)process.exit(1)'
umask 077
mkdir -m 700 "$output"
[[ -d "$output" && ! -L "$output" ]] || exit 1

echo 'SHIRONE_STAGE=dependency_install' >&2
pnpm install --frozen-lockfile
pnpm --dir cms install --frozen-lockfile
echo 'SHIRONE_STAGE=source_tests' >&2
pnpm build
pnpm test
pnpm check
pnpm type-check
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.email GIT_CONFIG_VALUE_0=shirone-ci@example.invalid
pnpm --dir cms test

tag="shirone-oci-ci-${commit:0:12}-$$"
declare -A ids digests
cleanup() {
  for component in cms ssr reconciler; do
    docker image rm "$tag-$component" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT
build_node_args=(--build-arg "NODE_IMAGE=$NODE_IMAGE")
if [[ -n "${NODE_BASE_OCI_ARCHIVE:-}" ]]; then
  # Validate every descriptor and diff_id before extracting the layout. The
  # verifier allows only regular OCI members, with no links or unsafe paths.
  python3 deploy/ci/verify-oci-archive-candidate.py "$NODE_BASE_OCI_ARCHIVE" > "$output/node-base.identity.json"
  node_manifest=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["manifest_digest"])' "$output/node-base.identity.json")
  [[ "$node_manifest" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
  mkdir -m 700 "$output/node-base-layout"
  tar -xf "$NODE_BASE_OCI_ARCHIVE" -C "$output/node-base-layout"
  build_node_args=(--build-arg NODE_IMAGE=shironebase \
    --build-context "shironebase=oci-layout://$output/node-base-layout@$node_manifest")
fi
for component in cms ssr reconciler; do
  echo "SHIRONE_STAGE=oci_$component" >&2
  case "$component" in
    cms) dockerfile=cms/Dockerfile; context=cms ;;
    ssr) dockerfile=Dockerfile; context=. ;;
    reconciler) dockerfile=deploy/Dockerfile.meili-key-reconciler; context=. ;;
  esac
  archive="$output/$component.oci.tar"
  docker buildx build --platform linux/amd64 --provenance=false \
    "${build_node_args[@]}" --file "$dockerfile" \
    --tag "$tag-$component" --output "type=oci,dest=$archive" "$context"
  python3 deploy/ci/verify-oci-archive-candidate.py "$archive" > "$output/$component.identity.json"
  digests[$component]=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["archive_digest"])' "$output/$component.identity.json")
  config=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["config_digest"])' "$output/$component.identity.json")
  manifest=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["manifest_digest"])' "$output/$component.identity.json")
  docker load --input "$archive" >/dev/null
  ids[$component]=$(docker image inspect --format '{{.Id}}' "$tag-$component")
  # Docker's classic image store identifies images by config digest; its
  # containerd store identifies single-platform images by manifest digest.
  [[ "${ids[$component]}" == "$config" || "${ids[$component]}" == "$manifest" ]] || {
    echo 'loaded image differs from both verified OCI identities' >&2; exit 1;
  }
done

# Run the real integration suites by content-addressed image ID, not by a tag
# another process could retarget. All inputs are local and disposable.
echo 'SHIRONE_STAGE=integration_content' >&2
SHIRONE_DISPOSABLE=yes CMS_TEST_IMAGE="${ids[cms]}" SSR_TEST_IMAGE="${ids[ssr]}" \
  POSTGRES_TEST_IMAGE="$POSTGRES_TEST_IMAGE" MEILI_TEST_IMAGE="$MEILI_TEST_IMAGE" \
  RECONCILER_TEST_IMAGE="${ids[reconciler]}" node deploy/tests/strapi-search-e2e.cjs
echo 'SHIRONE_STAGE=integration_media' >&2
SHIRONE_DISPOSABLE=yes CMS_TEST_IMAGE="${ids[cms]}" SSR_TEST_IMAGE="${ids[ssr]}" \
  POSTGRES_TEST_IMAGE="$POSTGRES_TEST_IMAGE" node deploy/tests/admin-media-e2e.cjs
echo 'SHIRONE_STAGE=integration_recovery' >&2
MEILI_DISPOSABLE=yes MEILI_TEST_IMAGE="$MEILI_TEST_IMAGE" node deploy/tests/meili-uncertain-recovery.cjs

for component in cms ssr reconciler; do
  current="sha256:$(sha256sum "$output/$component.oci.tar" | cut -d' ' -f1)"
  [[ "$current" == "${digests[$component]}" ]] || { echo 'archive changed after testing' >&2; exit 1; }
done
printf '%s\n' 'Exact OCI images passed isolated integration; base equivalence, attestation and release are not approved.'
