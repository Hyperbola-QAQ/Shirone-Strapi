#!/usr/bin/env bash
set -euo pipefail

# GitHub's default Docker driver cannot use the OCI exporter. Use a dedicated
# ephemeral docker-container builder, pinned to an independently checked
# linux/amd64 BuildKit manifest. The configured domestic mirror is tried first;
# its failure never changes the version or digest used by the official fallback.
[[ $# == 1 && "$1" =~ ^shirone-ci-[0-9]+-[0-9]+$ ]] || { echo 'invalid builder name' >&2; exit 2; }
builder=$1
digest=sha256:97719ccb2ac86fb44fd36f330a6d302f63dddd5326f3ada24f9c05ccac0d9098
image="docker.m.daocloud.io/moby/buildkit@$digest"
if ! docker pull --platform linux/amd64 "$image"; then
  echo 'Domestic BuildKit mirror unavailable; using the official image with the same digest.' >&2
  image="docker.io/moby/buildkit@$digest"
  docker pull --platform linux/amd64 "$image"
fi
[[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" == linux/amd64 ]]
docker buildx create --name "$builder" --driver docker-container --driver-opt "image=$image" --use
docker buildx inspect "$builder" --bootstrap
