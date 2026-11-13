#!/usr/bin/env bash
set -euo pipefail
[[ "$#" == 1 ]] || exit 2
# curl retries transient transport errors and HTTP 408/429/500/502/503/504.
# Permanent responses (such as 403) still fail immediately.
curl --fail --silent --show-error --retry 4 --retry-delay 1 \
  --retry-max-time 60 --max-time 10 --output /dev/null "$1"
