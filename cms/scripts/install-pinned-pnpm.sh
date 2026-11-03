#!/bin/sh
set -eu

# Build-stage only. Never trust registry metadata as the integrity authority:
# both package tarballs are checked against reviewed SHA-512 values.
work=$(mktemp -d /tmp/shirone-pnpm.XXXXXX)
cleanup() { node -e 'require("node:fs").rmSync(process.argv[1],{recursive:true,force:true})' "$work"; }
trap cleanup EXIT
verify() {
  node - "$1" "$2" <<'NODE'
const fs = require('node:fs');
const crypto = require('node:crypto');
const paths = fs.readdirSync(process.argv[2]).filter(p => p.endsWith('.tgz'));
if (paths.length !== 1) process.exit(1);
const digest = crypto.createHash('sha512').update(fs.readFileSync(process.argv[2] + '/' + paths[0])).digest('base64');
if (digest !== process.argv[3]) process.exit(1);
NODE
}
pack() {
  directory=$1 mirror=$2 official=$3 integrity=$4
  mkdir "$directory"
  if npm pack --ignore-scripts --silent --pack-destination "$directory" "$mirror" >/dev/null && verify "$directory" "$integrity"; then
    return 0
  fi
  echo 'Domestic pnpm package unavailable or integrity mismatch; retrying official source with identical version and checksum.' >&2
  node -e 'require("node:fs").rmSync(process.argv[1],{recursive:true,force:true})' "$directory"
  mkdir "$directory"
  npm pack --ignore-scripts --silent --pack-destination "$directory" "$official" >/dev/null
  verify "$directory" "$integrity"
}
pack "$work/wrapper" 'https://registry.npmmirror.com/pnpm/-/pnpm-12.16.1.tgz' 'https://registry.npmjs.org/pnpm/-/pnpm-12.16.1.tgz' 'CR0d7nknV1Zhsl2rPCrjHK/BxU1rce1qZgDPDByxBDSkR+rhQBXxsj4ua+3u1535SpMuIWTSgyKA3OP7MvZW0Q=='
pack "$work/native" 'https://registry.npmmirror.com/@pnpm/exe.linux-x64/-/exe.linux-x64-12.16.1.tgz' 'https://registry.npmjs.org/@pnpm/exe.linux-x64/-/exe.linux-x64-12.16.1.tgz' 'RK//8ZLcXGW8A2k8bQOXZDBN1Qu92zN4dYnG1bzj1zoQ1fkZ6jNzZ1AvXtsfGVma795CJtvoaF5bLXf4l/FIUw=='
mkdir /opt/shirone-pnpm
tar -xzf "$work"/wrapper/*.tgz -C /opt/shirone-pnpm
tar -xOzf "$work"/native/*.tgz package/pnpm > /opt/shirone-pnpm/package/pnpm-native
chmod 755 /opt/shirone-pnpm/package/pnpm-native
printf '%s\n' '#!/bin/sh' 'exec node /opt/shirone-pnpm/package/bin/pnpm.mjs "$@"' > /usr/local/bin/pnpm
chmod 755 /usr/local/bin/pnpm
[ "$(pnpm --version)" = '12.16.1' ] || { echo 'Unexpected pnpm executable version' >&2; exit 1; }
