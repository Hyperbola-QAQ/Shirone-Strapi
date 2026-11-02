#!/usr/bin/env bash
set -euo pipefail
: "${GH_TOKEN:?}" "${GITHUB_ACTOR:?}" "${CMS_REPO:?}" "${SSR_REPO:?}" "${RECONCILER_REPO:?}" "${RUNNER_TEMP:?}" "${DOCKER_CONFIG:?}"
: "${CMS_DIGEST:?}" "${SSR_DIGEST:?}" "${RECONCILER_DIGEST:?}" "${POSTGRES_TEST_IMAGE:?}" "${MEILI_TEST_IMAGE:?}"
for digest in "$CMS_DIGEST" "$SSR_DIGEST" "$RECONCILER_DIGEST"; do
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
done
cms="$CMS_REPO@$CMS_DIGEST"; ssr="$SSR_REPO@$SSR_DIGEST"; reconciler="$RECONCILER_REPO@$RECONCILER_DIGEST"
[[ "$DOCKER_CONFIG" == "$RUNNER_TEMP/"* && "$RUNNER_TEMP" == /* ]] || exit 2
mkdir -m 700 -p "$DOCKER_CONFIG"
tool_dir=$(mktemp -d "$RUNNER_TEMP/shirone-release-node.XXXXXX")
trap 'docker logout ghcr.io >/dev/null 2>&1 || true; find "$tool_dir" -depth -delete' EXIT
printf '%s' "$GH_TOKEN" | docker login ghcr.io -u "$GITHUB_ACTOR" --password-stdin
for image in "$cms" "$ssr" "$reconciler" "$POSTGRES_TEST_IMAGE" "$MEILI_TEST_IMAGE"; do
  docker pull --platform linux/amd64 "$image"
  [[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" == linux/amd64 ]] || exit 1
done
docker logout ghcr.io >/dev/null
unset GH_TOKEN
node_file=node-v22.23.4-linux-x64.tar.xz
node_hash=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce
fetch_node() {
  curl --fail --location --silent --show-error --connect-timeout 15 --max-time 180 "$1" -o "$tool_dir/$node_file" &&
    printf '%s  %s\n' "$node_hash" "$tool_dir/$node_file" | sha256sum --check --status
}
if ! fetch_node "https://npmmirror.com/mirrors/node/v22.23.4/$node_file"; then
  echo 'Domestic Node mirror failed or checksum mismatched; trying the official source with the same checksum.' >&2
  fetch_node "https://nodejs.org/dist/v22.23.4/$node_file" || { echo 'Node verification failed' >&2; exit 1; }
fi
mkdir "$tool_dir/node"
tar -xJf "$tool_dir/$node_file" -C "$tool_dir/node" --strip-components=1
export PATH="$tool_dir/node/bin:$PATH"
[[ "$(node --version)" == v22.23.4 ]] || exit 1
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.email GIT_CONFIG_VALUE_0=shirone-ci@example.invalid
SHIRONE_DISPOSABLE=yes CMS_TEST_IMAGE="$cms" SSR_TEST_IMAGE="$ssr" \
  POSTGRES_TEST_IMAGE="$POSTGRES_TEST_IMAGE" MEILI_TEST_IMAGE="$MEILI_TEST_IMAGE" \
  RECONCILER_TEST_IMAGE="$reconciler" node deploy/tests/strapi-search-e2e.cjs
SHIRONE_DISPOSABLE=yes CMS_TEST_IMAGE="$cms" SSR_TEST_IMAGE="$ssr" \
  POSTGRES_TEST_IMAGE="$POSTGRES_TEST_IMAGE" node deploy/tests/admin-media-e2e.cjs
MEILI_DISPOSABLE=yes MEILI_TEST_IMAGE="$MEILI_TEST_IMAGE" node deploy/tests/meili-uncertain-recovery.cjs
