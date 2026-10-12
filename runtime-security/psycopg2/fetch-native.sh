#!/usr/bin/env bash
set -euo pipefail

# Runs only in the pinned native Ubuntu base, with /frozen as new scratch.
test -d /frozen
test ! -e /frozen/apt-lists
mkdir -p /frozen/apt-lists /frozen/debs /frozen/keyring
cp /usr/share/keyrings/ubuntu-archive-keyring.gpg /frozen/keyring/ubuntu-archive-keyring.gpg
test -s /frozen/keyring/ubuntu-archive-keyring.gpg
apt-get update
cp -a /var/lib/apt/lists/. /frozen/apt-lists/
apt-get install -y --download-only --no-install-recommends \
  -o Dir::Cache::archives=/frozen/debs \
  build-essential python3-dev python3-venv libpq-dev ca-certificates curl
test "$(find /frozen/debs -maxdepth 1 -name '*.deb' -type f | wc -l)" -gt 0
dpkg --print-architecture > /frozen/architecture.txt
