#!/usr/bin/env bash
set -euo pipefail
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "$AELIRA_XDG_HOST_ARCH" = "$arch"
export DEBIAN_FRONTEND=noninteractive LC_ALL=C TZ=UTC
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/base-packages.tsv
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
apt-get update -o APT::Update::Error-Mode=any > /provenance/apt-update.log 2>&1
apt-get install -y --no-install-recommends build-essential debhelper dpkg-dev \
  fakeroot quilt python3 binutils gpgv xmlto w3m > /provenance/tool-install.log 2>&1
cd /source-input
apt-get source --download-only xdg-utils=1.2.1-2ubuntu2 > /provenance/source-fetch.log 2>&1
sha256sum -c /recipe/inputs.sha256 > /provenance/source-hashes.log
apt-get build-dep -y ./xdg-utils_1.2.1-2ubuntu2.dsc > /provenance/build-dep.log 2>&1
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/toolchain-packages.tsv
awk -F '\t' 'NR==FNR {base[$1]=$2 FS $3; next} !($1 in base) || base[$1]!=$2 FS $3' \
  /provenance/base-packages.tsv /provenance/toolchain-packages.tsv > /provenance/toolchain-delta.tsv
mkdir /provenance/toolchain-debs /provenance/apt-lists
cd /provenance/toolchain-debs
while IFS="$(printf '\t')" read -r name version architecture; do
  apt-get download "$name:$architecture=$version" >> /provenance/toolchain-download.log 2>&1
done < /provenance/toolchain-delta.tsv
find . -maxdepth 1 -type f -name '*.deb' -exec sha256sum {} + | sort > /provenance/toolchain-debs.sha256
cp /usr/share/keyrings/ubuntu-archive-keyring.gpg /provenance/
find /var/lib/apt/lists -maxdepth 1 -type f \
  \( -name '*InRelease' -o -name '*_Sources*' -o -name '*_Packages*' \) \
  -exec cp {} /provenance/apt-lists/ \;
(cd /provenance/apt-lists && find . -type f -exec sha256sum {} + | sort) > /provenance/apt-index-sha256.txt
python3 -B /recipe/verify-provenance.py > /provenance/signed-chain.json
