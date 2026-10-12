#!/usr/bin/env bash
set -euo pipefail
test -d /frozen
test ! -e /frozen/apt-lists
mkdir -p /frozen/apt-lists /frozen/debs /frozen/keyring
cp /usr/share/keyrings/ubuntu-archive-keyring.gpg /frozen/keyring/ubuntu-archive-keyring.gpg
test -s /frozen/keyring/ubuntu-archive-keyring.gpg
apt-get update
cp -a /var/lib/apt/lists/. /frozen/apt-lists/
apt-get install -y --download-only --no-install-recommends \
  -o Dir::Cache::archives=/frozen/debs \
  build-essential python3-dev python3-venv libxml2-dev libxslt1-dev \
  libxml2-16 libxslt1.1 libxml2-utils binutils ca-certificates curl
test "$(find /frozen/debs -maxdepth 1 -name '*.deb' -type f | wc -l)" -gt 0
dpkg --print-architecture > /frozen/architecture.txt
# Only disposable APT housekeeping in this new scratch is removed. Refuse
# nonempty partial directories rather than silently dropping candidate bytes.
for area in /frozen/apt-lists /frozen/debs; do
  rm -f "$area/lock"
  for housekeeping in partial auxfiles; do
    if test -d "$area/$housekeeping"; then rmdir "$area/$housekeeping"; fi
  done
done
