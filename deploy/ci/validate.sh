#!/usr/bin/env bash
set -euo pipefail

# Candidate CI entrypoint. Run only in a dedicated, disposable environment;
# builds execute repository code and Docker grants its caller host privileges.
if [[ "${SHIRONE_CI_DISPOSABLE:-}" != yes ]]; then
  printf '%s\n' 'SHIRONE_CI_DISPOSABLE=yes is required' >&2
  exit 2
fi
repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
if [[ "$(pnpm --version)" != 12.16.1 ]]; then
  printf '%s\n' 'pnpm 12.16.1 is required' >&2
  exit 2
fi
node -e 'let v=+process.versions.node.split(".")[0]; if(v<20||v>24) process.exit(1)'
for image in POSTGRES_TEST_IMAGE MEILI_TEST_IMAGE NODE_IMAGE; do
  if [[ -z "${!image:-}" ]]; then
    printf 'required image variable missing: %s\n' "$image" >&2
    exit 2
  fi
  docker image inspect "${!image}" >/dev/null
done

# The checked-in .npmrc files prefer the configured domestic mirror. Lockfiles
# are mandatory; never fall back by changing versions or ignoring integrity.
pnpm install --frozen-lockfile
pnpm --dir cms install --frozen-lockfile
pnpm test
pnpm check
pnpm type-check
pnpm --dir cms test

tag="shirone-ci-$(git rev-parse --short=12 HEAD)-$$"
cms_image="${tag}-cms"
ssr_image="${tag}-ssr"
reconciler_image="${tag}-reconciler"
cleanup() { docker image rm "$cms_image" "$ssr_image" "$reconciler_image" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker build --build-arg "NODE_IMAGE=$NODE_IMAGE" -t "$cms_image" -f cms/Dockerfile cms
docker build --build-arg "NODE_IMAGE=$NODE_IMAGE" -t "$ssr_image" .
docker build --build-arg "NODE_IMAGE=$NODE_IMAGE" -t "$reconciler_image" -f deploy/Dockerfile.meili-key-reconciler .
# Bootstrap tests need an email, but must never change the user's Git config.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.email GIT_CONFIG_VALUE_0=shirone-ci@example.invalid
SHIRONE_DISPOSABLE=yes CMS_TEST_IMAGE="$cms_image" SSR_TEST_IMAGE="$ssr_image" \
  POSTGRES_TEST_IMAGE="$POSTGRES_TEST_IMAGE" MEILI_TEST_IMAGE="$MEILI_TEST_IMAGE" \
  RECONCILER_TEST_IMAGE="$reconciler_image" node deploy/tests/strapi-search-e2e.cjs
SHIRONE_DISPOSABLE=yes CMS_TEST_IMAGE="$cms_image" SSR_TEST_IMAGE="$ssr_image" \
  POSTGRES_TEST_IMAGE="$POSTGRES_TEST_IMAGE" node deploy/tests/admin-media-e2e.cjs
MEILI_DISPOSABLE=yes MEILI_TEST_IMAGE="$MEILI_TEST_IMAGE" node deploy/tests/meili-uncertain-recovery.cjs
printf '%s\n' 'Source, builds and disposable integration tests passed.'
