#!/usr/bin/env bash
set -euo pipefail

# Source-only candidate; no production credentials, private network or deploy.
# The disposable GitHub-hosted runner supplies RUNNER_TEMP.
[[ "${RUNNER_TEMP:-}" = /* && -d "$RUNNER_TEMP" ]] || { echo 'RUNNER_TEMP must be an existing absolute directory' >&2; exit 2; }
work=$(mktemp -d "$RUNNER_TEMP/shirone-hosted.XXXXXX")
trap 'find "$work" -depth -delete' EXIT
node_file=node-v22.23.4-linux-x64.tar.xz
node_hash=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce
node_mirror="https://npmmirror.com/mirrors/node/v22.23.4/$node_file"
node_official="https://nodejs.org/dist/v22.23.4/$node_file"
fetch_node() {
  curl --fail --location --silent --show-error --connect-timeout 15 --max-time 180 "$1" -o "$work/$node_file" &&
    printf '%s  %s\n' "$node_hash" "$work/$node_file" | sha256sum --check --status
}
if ! fetch_node "$node_mirror"; then
  echo 'Configured domestic Node mirror unavailable or checksum mismatch; trying official source with the same version and checksum.' >&2
  fetch_node "$node_official" || { echo 'Node checksum validation failed' >&2; exit 1; }
fi
mkdir "$work/node" "$work/pnpm"
tar -xJf "$work/$node_file" -C "$work/node" --strip-components=1
export PATH="$work/node/bin:$PATH"
[[ "$(node --version)" == v22.23.4 ]] || { echo 'unexpected Node version' >&2; exit 1; }
pnpm_sri='CR0d7nknV1Zhsl2rPCrjHK/BxU1rce1qZgDPDByxBDSkR+rhQBXxsj4ua+3u1535SpMuIWTSgyKA3OP7MvZW0Q=='
fetch_pnpm() {
  curl --fail --location --silent --show-error --connect-timeout 15 --max-time 180 "$1" -o "$work/pnpm.tgz" &&
    [[ "$(openssl dgst -sha512 -binary "$work/pnpm.tgz" | openssl base64 -A)" == "$pnpm_sri" ]]
}
if ! fetch_pnpm 'https://registry.npmmirror.com/pnpm/-/pnpm-12.16.1.tgz'; then
  echo 'Configured domestic pnpm mirror unavailable or integrity mismatch; trying official source with the same version and checksum.' >&2
  fetch_pnpm 'https://registry.npmjs.org/pnpm/-/pnpm-12.16.1.tgz' || { echo 'pnpm integrity validation failed' >&2; exit 1; }
fi
tar -xzf "$work/pnpm.tgz" -C "$work/pnpm"
pnpm_cmd=(node "$work/pnpm/package/bin/pnpm.cjs")
[[ "$("${pnpm_cmd[@]}" --version)" == 12.16.1 ]] || { echo 'unexpected pnpm version' >&2; exit 1; }
"${pnpm_cmd[@]}" install --frozen-lockfile
"${pnpm_cmd[@]}" --dir cms install --frozen-lockfile
"${pnpm_cmd[@]}" test
"${pnpm_cmd[@]}" check
"${pnpm_cmd[@]}" type-check
"${pnpm_cmd[@]}" --dir cms test
"${pnpm_cmd[@]}" build
"${pnpm_cmd[@]}" --dir cms build
echo 'Hosted source validation passed; exact production images were not built or published.'
