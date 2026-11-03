#!/usr/bin/env bash
set -euo pipefail

# Install only into a new disposable runner directory, without changing PATH or
# the machine. The caller must use the resulting absolute binary path.
[[ $# == 1 && "$1" == /* && ! -e "$1" ]] || { echo 'new absolute directory required' >&2; exit 2; }
umask 077
mkdir -m 700 "$1"
dir=$1
package="$dir/age.pkg.tar.zst"
archive="$dir/age.tar.gz"
arch_hash=c82edb230b872f8bd2d30f240722806038ae3613ccac43fa99df5e7e8a2d4196
official_hash=cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10
fetch() { curl --fail --silent --show-error --location --connect-timeout 15 --max-time 180 "$1" -o "$2"; }
if fetch 'https://mirrors.tuna.tsinghua.edu.cn/archlinux/extra/os/x86_64/age-1.3.2-1-x86_64.pkg.tar.zst' "$package" &&
   printf '%s  %s\n' "$arch_hash" "$package" | sha256sum --check --status &&
   tar --zstd -xf "$package" -C "$dir" usr/bin/age &&
   [[ "$("$dir/usr/bin/age" --version 2>/dev/null)" == 1.3.2 || "$("$dir/usr/bin/age" --version 2>/dev/null)" == v1.3.2 ]]; then
  install -m 700 "$dir/usr/bin/age" "$dir/age"
else
  echo 'Domestic age mirror unavailable, checksum mismatched, or binary incompatible; using the official release with the same version.' >&2
  fetch 'https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-amd64.tar.gz' "$archive"
  printf '%s  %s\n' "$official_hash" "$archive" | sha256sum --check --status || { echo 'official age checksum mismatch' >&2; exit 1; }
  tar -xzf "$archive" -C "$dir" age/age
  version=$("$dir/age/age" --version 2>/dev/null)
  [[ "$version" == v1.3.2 || "$version" == 1.3.2 ]] || { echo 'unexpected age version' >&2; exit 1; }
  install -m 700 "$dir/age/age" "$dir/age-bin"
  mv "$dir/age-bin" "$dir/age-executable"
  printf '%s\n' "$dir/age-executable"
  exit 0
fi
printf '%s\n' "$dir/age"
