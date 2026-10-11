#!/bin/sh
set -eu

# Online, signed-APT preparation. /source-input and /provenance are writable.
arch="$(dpkg --print-architecture)"
test "${AELIRA_EXPAT_HOST_ARCH:-}" = "$arch"
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/base-packages.tsv
apt-get update > /provenance/apt-update.log 2>&1
apt-get install -y dpkg-dev quilt devscripts python3 binutils libexpat1=2.7.4-1ubuntu0.2 > /provenance/tool-install.log 2>&1
cd /source-input
apt-get source --download-only expat=2.7.4-1ubuntu0.2 > /provenance/source-fetch.log 2>&1
sha256sum -c /recipe/inputs.sha256 > /provenance/source-hashes.log
apt-get build-dep -y ./expat_2.7.4-1ubuntu0.2.dsc > /provenance/build-dep.log 2>&1
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/toolchain-packages.tsv
# The base digest retains its original packages. Save every added/upgraded
# package so the frozen toolchain can be reconstructed after runner teardown.
awk -F '\t' 'NR == FNR {base[$1] = $2 FS $3; next}
  !($1 in base) || base[$1] != $2 FS $3' \
  /provenance/base-packages.tsv /provenance/toolchain-packages.tsv \
  > /provenance/toolchain-delta.tsv
mkdir -p /provenance/toolchain-debs
cd /provenance/toolchain-debs
while IFS="$(printf '\t')" read -r package version architecture; do
  apt-get download "$package:$architecture=$version" >> /provenance/toolchain-download.log 2>&1
done < /provenance/toolchain-delta.tsv
find . -maxdepth 1 -type f -name '*.deb' -exec sha256sum {} + \
  > /provenance/toolchain-debs.unsorted.sha256
sort /provenance/toolchain-debs.unsorted.sha256 > /provenance/toolchain-debs.sha256
test -s /provenance/toolchain-debs.sha256
cp /usr/share/keyrings/ubuntu-archive-keyring.gpg /provenance/
cp /etc/apt/sources.list.d/ubuntu.sources /provenance/
apt-get indextargets > /provenance/apt-index-targets.txt
mkdir -p /provenance/apt-lists
find /var/lib/apt/lists -maxdepth 1 -type f \( -name '*InRelease' -o -name '*_Sources*' -o -name '*_Packages*' \) -exec cp {} /provenance/apt-lists/ \;
find /provenance/apt-lists -type f -exec sha256sum {} + > /provenance/apt-index-sha256.txt
