#!/usr/bin/env bash
# Online preparation inside a disposable pinned Ubuntu container.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LC_ALL=C TZ=UTC
arch=$(dpkg --print-architecture)
test "$arch" = "${AELIRA_CJSON_HOST_ARCH:?}"
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
apt-get update > /provenance/apt-update-private.log 2>&1
apt-get install -y --no-install-recommends build-essential dpkg-dev debhelper \
  cmake pkgconf fakeroot python3 gnupg binutils ca-certificates \
  > /provenance/tool-install-private.log 2>&1
apt-get build-dep -y cjson=1.7.19-2 > /provenance/build-dep-private.log 2>&1
cd /source-input
apt-get source --download-only cjson=1.7.19-2 > /provenance/source-fetch-private.log 2>&1
mkdir /provenance/apt-lists
find /var/lib/apt/lists -maxdepth 1 -type f \
  \( -name '*InRelease' -o -name '*_Sources*' \) -exec cp {} /provenance/apt-lists/ \;
cp /usr/share/keyrings/ubuntu-archive-keyring.gpg /provenance/
cp /etc/apt/sources.list.d/ubuntu.sources /provenance/
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/toolchain-packages.tsv
python3 -B /recipe/verify-ubuntu-source.py --lists /provenance/apt-lists \
  --input /source-input > /provenance/ubuntu-source.json
