#!/usr/bin/env bash
set -euo pipefail
# Ephemeral hosted runner only. Fixed versions and pinned checksums; no global install.
[[ $# == 1 && "$1" == /* && ! -e "$1" ]] || { echo 'new absolute directory required' >&2; exit 2; }
umask 077
mkdir -m 700 "$1"
dir=$1
node_file=node-v22.23.4-linux-x64.tar.xz
node_sha=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce
pnpm_sri='CR0d7nknV1Zhsl2rPCrjHK/BxU1rce1qZgDPDByxBDSkR+rhQBXxsj4ua+3u1535SpMuIWTSgyKA3OP7MvZW0Q=='
native_sri='RK//8ZLcXGW8A2k8bQOXZDBN1Qu92zN4dYnG1bzj1zoQ1fkZ6jNzZ1AvXtsfGVma795CJtvoaF5bLXf4l/FIUw=='
fetch() { curl --fail --silent --show-error --location --connect-timeout 15 --max-time 180 "$1" -o "$2"; }
node_ok() { fetch "$1" "$dir/$node_file" && printf '%s  %s\n' "$node_sha" "$dir/$node_file" | sha256sum --check --status; }
if ! node_ok "https://npmmirror.com/mirrors/node/v22.23.4/$node_file"; then
  echo 'Domestic Node mirror unavailable or checksum mismatch; using official source with identical checksum.' >&2
  node_ok "https://nodejs.org/dist/v22.23.4/$node_file" || exit 1
fi
pnpm_ok() { fetch "$1" "$dir/pnpm.tgz" && [[ "$(openssl dgst -sha512 -binary "$dir/pnpm.tgz" | openssl base64 -A)" == "$pnpm_sri" ]]; }
if ! pnpm_ok 'https://registry.npmmirror.com/pnpm/-/pnpm-12.16.1.tgz'; then
  echo 'Domestic pnpm mirror unavailable or checksum mismatch; using official source with identical checksum.' >&2
  pnpm_ok 'https://registry.npmjs.org/pnpm/-/pnpm-12.16.1.tgz' || exit 1
fi
native_ok() { fetch "$1" "$dir/pnpm-native.tgz" && [[ "$(openssl dgst -sha512 -binary "$dir/pnpm-native.tgz" | openssl base64 -A)" == "$native_sri" ]]; }
if ! native_ok 'https://registry.npmmirror.com/@pnpm/exe.linux-x64/-/exe.linux-x64-12.16.1.tgz'; then
  echo 'Domestic pnpm executable mirror unavailable or checksum mismatch; using official source with identical checksum.' >&2
  native_ok 'https://registry.npmjs.org/@pnpm/exe.linux-x64/-/exe.linux-x64-12.16.1.tgz' || exit 1
fi
mkdir "$dir/node" "$dir/pnpm" "$dir/bin"
tar -xJf "$dir/$node_file" -C "$dir/node" --strip-components=1
tar -xzf "$dir/pnpm.tgz" -C "$dir/pnpm"
tar -xOzf "$dir/pnpm-native.tgz" package/pnpm > "$dir/pnpm/package/pnpm-native"
chmod 700 "$dir/pnpm/package/pnpm-native"
chmod 700 "$dir/pnpm/package/bin/pnpm.mjs"
ln -s "$dir/node/bin/node" "$dir/bin/node"
ln -s "$dir/node/bin/npm" "$dir/bin/npm"
ln -s "$dir/node/bin/npx" "$dir/bin/npx"
ln -s "$dir/pnpm/package/bin/pnpm.mjs" "$dir/bin/pnpm"
export PATH="$dir/bin:$PATH"
[[ "$(node --version)" == v22.23.4 && "$(pnpm --version)" == 12.16.1 ]] || { echo 'unexpected runtime version' >&2; exit 1; }
printf '%s\n' "$dir/bin"
