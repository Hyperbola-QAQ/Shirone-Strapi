#!/usr/bin/env bash
set -euo pipefail

[[ $# == 1 && "$1" =~ ^[0-9a-f]{40}$ ]] || { echo 'expected source commit required' >&2; exit 2; }
expected=$1
[[ "$(git rev-parse HEAD)" == "$expected" ]] || { echo 'checkout differs from expected source' >&2; exit 1; }
shallow=$(git rev-parse --is-shallow-repository)
case "$shallow" in
  true)  depth=(--unshallow) ;;
  false) depth=() ;;
  *) echo 'cannot determine checkout history' >&2; exit 1 ;;
esac
# The repository is public. Disable credential helpers and prompting: this
# explicitly fetches only main, never all branches or archived site history.
GIT_TERMINAL_PROMPT=0 git -c credential.helper= fetch --no-tags "${depth[@]}" \
  origin '+refs/heads/main:refs/remotes/origin/main'
[[ "$(git rev-parse refs/remotes/origin/main)" == "$expected" ]] || {
  echo 'main changed while fetching history' >&2; exit 1;
}
