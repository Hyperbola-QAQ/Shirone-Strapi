#!/usr/bin/env bash
set -euo pipefail
: "${GH_TOKEN:?}" "${GITHUB_ACTOR:?}" "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}" "${GITHUB_OUTPUT:?}" "${RUNNER_TEMP:?}"
: "${NODE_IMAGE:?}" "${CMS_REPO:?}" "${SSR_REPO:?}" "${RECONCILER_REPO:?}"
[[ "$GITHUB_RUN_ID" =~ ^[0-9]+$ && "$GITHUB_RUN_ATTEMPT" =~ ^[0-9]+$ ]] || exit 2
for repo in "$CMS_REPO" "$SSR_REPO" "$RECONCILER_REPO"; do
  [[ "$repo" =~ ^ghcr\.io/hyperbola-qaq/shirone-(cms|ssr|reconciler)$ ]] || exit 2
done
printf '%s' "$GH_TOKEN" | docker login ghcr.io -u "$GITHUB_ACTOR" --password-stdin
trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT
unset GH_TOKEN
docker pull --platform linux/amd64 "$NODE_IMAGE"
suffix="candidate-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
build() {
  local name=$1 repo=$2 dockerfile=$3 context=$4 meta="$RUNNER_TEMP/shirone-$1-metadata.json" digest
  docker buildx build --platform linux/amd64 --provenance=false --push \
    --build-arg "NODE_IMAGE=$NODE_IMAGE" --metadata-file "$meta" \
    -t "$repo:$suffix" -f "$dockerfile" "$context"
  digest=$(jq -er '.["containerimage.digest"]' "$meta")
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
  printf '%s_digest=%s\n' "$name" "$digest" >> "$GITHUB_OUTPUT"
}
build cms "$CMS_REPO" cms/Dockerfile cms
build ssr "$SSR_REPO" Dockerfile .
build reconciler "$RECONCILER_REPO" deploy/Dockerfile.meili-key-reconciler .
