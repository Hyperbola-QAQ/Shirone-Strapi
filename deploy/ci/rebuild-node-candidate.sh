#!/usr/bin/env bash
set -euo pipefail

# Isolated local experiment only. A successful build is not release approval:
# this candidate does not implement or verify the CA-remediation layer.
if [[ "$#" != 2 || ( "$1" != --check && "$1" != --build ) ]]; then
  echo 'usage: bash rebuild-node-candidate.sh --check|--build NODE_ARCHIVE' >&2
  exit 2
fi
mode=$1 archive=$2
[[ -f "$archive" ]] || { echo 'Node archive not found' >&2; exit 2; }
archive=$(realpath "$archive")
expected_archive=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce
expected_node=94efd062ed066c1a0b332e18249859c905e920eeada5df55d24c99b919629a90
printf '%s  %s\n' "$expected_archive" "$archive" | sha256sum --check --status || {
  echo 'Node archive checksum mismatch' >&2; exit 1;
}
scratch=$(mktemp -d /tmp/shirone-node-rebuild.XXXXXX)
trap 'find "$scratch" -depth -delete' EXIT
tar -xJOf "$archive" node-v22.23.4-linux-x64/bin/node > "$scratch/node"
printf '%s  %s\n' "$expected_node" "$scratch/node" | sha256sum --check --status || {
  echo 'Node executable checksum mismatch' >&2; exit 1;
}
if [[ "$mode" == --check ]]; then
  echo 'Archive and executable checksums match; CA remediation and release eligibility remain unverified.'
  exit 0
fi
parent='docker.m.daocloud.io/library/node@sha256:25330af3531fb5e23318554a0aa911125b6e91b1b777edf7655501d207c067a2'
docker image inspect "$parent" >/dev/null || { echo 'Preverified parent image is unavailable locally' >&2; exit 1; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
docker build --network=none --pull=false --file "$script_dir/Dockerfile.node-reconstruction-candidate" \
  --tag shirone-node-reconstruction:local-candidate "$scratch"
echo 'Local candidate only: CA remediation, equivalence and release eligibility are unverified.'
