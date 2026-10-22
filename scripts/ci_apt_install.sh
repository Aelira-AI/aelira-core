#!/usr/bin/env bash
# Install Ubuntu packages on a GitHub-hosted runner without depending on one
# mirror's speed.
#
# Hosted runners fetch from azure.archive.ubuntu.com. When that mirror slows to
# a few KB/s, apt never sees an error (its timeouts only cover idle reads), so a
# plain install runs until the step or job limit kills it. Each attempt here is
# bounded instead. The first uses the runner's mirrors; later attempts drop the
# Azure mirror and fetch from archive.ubuntu.com. Packages already downloaded
# stay in the archive directory, so a retry only fetches what is missing.
#
# APT_ARCHIVE_DIR, when set, is used as apt's package cache so a workflow can
# restore and save it with actions/cache. A restored package is only installed
# if its hash matches the signed index apt has just downloaded, so a cache entry
# cannot substitute a different package.
#
# Usage: ci_apt_install.sh [--no-install-recommends] package...
set -euo pipefail

attempts="${APT_ATTEMPTS:-3}"
minutes="${APT_ATTEMPT_MINUTES:-8}"
mirrors_file="${APT_MIRRORS_FILE:-/etc/apt/apt-mirrors.txt}"
sources_dir="${APT_SOURCES_DIR:-/etc/apt}"
fallback="${APT_FALLBACK_MIRROR:-http://archive.ubuntu.com/ubuntu/}"

install_args=(-y)
packages=()
for argument in "$@"; do
  case "$argument" in
    --no-install-recommends) install_args+=("$argument") ;;
    -*) echo "ci_apt_install: unsupported option $argument" >&2; exit 2 ;;
    *) packages+=("$argument") ;;
  esac
done
if [ "${#packages[@]}" -eq 0 ]; then
  echo "ci_apt_install: no packages given" >&2
  exit 2
fi

apt_options=(
  -o Acquire::Retries=2
  -o Acquire::http::Timeout=20
  -o Acquire::https::Timeout=20
)
if [ -n "${APT_ARCHIVE_DIR:-}" ]; then
  mkdir -p "$APT_ARCHIVE_DIR"
  apt_options+=(-o "Dir::Cache::Archives=$APT_ARCHIVE_DIR")
fi

use_fallback_mirror() {
  # Runner images list mirrors in a mirror+file list; older images name the
  # mirror directly in their sources. Rewrite whichever is present.
  if [ -f "$mirrors_file" ]; then
    printf '%s\tpriority:1\n' "$fallback" | sudo tee "$mirrors_file" >/dev/null
  fi
  local source
  while IFS= read -r -d '' source; do
    sudo sed -i 's#https\?://azure\.archive\.ubuntu\.com/ubuntu/\?#'"$fallback"'#g' "$source"
  done < <(find "$sources_dir" -maxdepth 2 -type f \( -name '*.list' -o -name '*.sources' \) -print0)
  echo "ci_apt_install: switched to $fallback"
}

# Only the downloads are time-bounded. Installing runs once everything is
# local, so a timeout can never interrupt dpkg part-way through.
downloaded=false
for attempt in $(seq 1 "$attempts"); do
  if [ "$attempt" -eq 2 ]; then
    use_fallback_mirror
  fi
  echo "ci_apt_install: attempt $attempt of $attempts, ${minutes} minutes each for update and download"
  if timeout "${minutes}m" sudo apt-get "${apt_options[@]}" update \
    && timeout "${minutes}m" sudo apt-get "${apt_options[@]}" install --download-only "${install_args[@]}" "${packages[@]}"; then
    downloaded=true
    break
  fi
  echo "ci_apt_install: attempt $attempt did not finish" >&2
done
if [ "$downloaded" != true ]; then
  echo "ci_apt_install: packages were not downloaded after $attempts attempts" >&2
  exit 1
fi

sudo apt-get "${apt_options[@]}" install --no-download "${install_args[@]}" "${packages[@]}"
if [ -n "${APT_ARCHIVE_DIR:-}" ]; then
  # apt's partial directory belongs to its sandbox user; the cache action
  # runs as the runner user and must be able to read what it saves.
  sudo rm -rf "$APT_ARCHIVE_DIR/partial" "$APT_ARCHIVE_DIR/lock"
  sudo chmod -R a+rX "$APT_ARCHIVE_DIR"
fi
