#!/usr/bin/env bash
set -euo pipefail
: "${GH_TOKEN:?}" "${GITHUB_SHA:?}" "${GITHUB_OUTPUT:?}" "${RUNNER_TEMP:?}" "${DOCKER_CONFIG:?}"
[[ "${GITHUB_REPOSITORY:-}" == Hyperbola-QAQ/Shirone-Strapi && "${GITHUB_REF:-}" == refs/heads/main ]] || exit 2
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ && "$(git rev-parse HEAD)" == "$GITHUB_SHA" ]] || exit 2
remote_sha=$(gh api 'repos/Hyperbola-QAQ/Shirone-Strapi/git/ref/heads/main' --jq .object.sha)
[[ "$remote_sha" == "$GITHUB_SHA" ]] || { echo 'main changed; dispatch a new reviewed run' >&2; exit 1; }
[[ "$DOCKER_CONFIG" == "$RUNNER_TEMP/"* && "$RUNNER_TEMP" == /* ]] || exit 2
mkdir -m 700 -p "$DOCKER_CONFIG"
# All packages must already exist, be private, and grant this repository the
# required read/write access. A 403/404 or unknown visibility fails closed.
for package in shirone-base-node shirone-base-postgresql shirone-cms shirone-ssr shirone-reconciler; do
  visibility=$(gh api "user/packages/container/$package" --jq .visibility)
  [[ "$visibility" == private ]] || { echo "package $package is not verified private" >&2; exit 1; }
done
