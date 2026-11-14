#!/usr/bin/env bash
set -euo pipefail
# Public parents only; both routes use the exact same pinned manifest digest.
case "${1:-}" in
  node)
    mirror='docker.m.daocloud.io/library/node@sha256:25330af3531fb5e23318554a0aa911125b6e91b1b777edf7655501d207c067a2'
    official='docker.io/library/node@sha256:25330af3531fb5e23318554a0aa911125b6e91b1b777edf7655501d207c067a2'
    ;;
  postgres)
    mirror='ghcr.m.daocloud.io/cloudnative-pg/postgresql@sha256:e6e1f467b52112f7e08aa380de81473106ff54b1834d90bcb4265a2a018e1921'
    official='ghcr.io/cloudnative-pg/postgresql@sha256:e6e1f467b52112f7e08aa380de81473106ff54b1834d90bcb4265a2a018e1921'
    ;;
  *) echo 'unsupported base image' >&2; exit 2 ;;
esac
check_image() {
  docker pull --platform linux/amd64 "$1" >/dev/null || return 1
  [[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$1")" == linux/amd64 ]] || return 1
  if [[ "$2" == postgres ]]; then
    [[ "$(docker image inspect --format '{{.Config.User}}' "$1")" == 26 ]] || return 1
  fi
}
if check_image "$mirror" "$1"; then
  printf '%s\n' "$mirror"
elif check_image "$official" "$1"; then
  echo 'Domestic base mirror failed or was incompatible; using the official same-digest source.' >&2
  printf '%s\n' "$official"
else
  echo 'Both pinned base image sources failed or were incompatible.' >&2
  exit 1
fi
