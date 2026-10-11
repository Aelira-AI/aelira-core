#!/usr/bin/env bash
# Online preparation; preserve signed APT indexes, exact packages, and source.
set -euo pipefail
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "${AELIRA_TIFF_HOST_ARCH:-}" = "$arch"
export DEBIAN_FRONTEND=noninteractive LC_ALL=C TZ=UTC
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/base-packages.tsv
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
apt-get update > /provenance/apt-update.log 2>&1
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg git build-essential debhelper dh-autoreconf \
  dpkg-dev freeglut3-dev libglu1-mesa-dev libjbig-dev libjpeg-dev \
  liblzma-dev libzstd-dev libwebp-dev libxi-dev zlib1g-dev \
  libdeflate-dev liblerc-dev python3-sphinx pkgconf binutils fakeroot \
  > /provenance/tool-install.log 2>&1
cd /source-input
apt-get source --download-only tiff=4.7.0-3ubuntu5 > /provenance/source-fetch.log 2>&1
curl -fsSLo tiff_4.7.2.orig.tar.xz https://download.osgeo.org/libtiff/tiff-4.7.2.tar.xz
curl -fsSLo tiff_4.7.2.orig.tar.xz.sig https://download.osgeo.org/libtiff/tiff-4.7.2.tar.xz.sig
curl -fsSLo rouault-gitlab.gpg https://gitlab.com/rouault.gpg
printf '%s  %s\n' \
  '2b8b5c4e79cb2a4651796602e45d6179b7f18bc283feab417b8e881ef7e81862' tiff_4.7.0.orig.tar.bz2 \
  '92bdb0cfa5541b772d6f5105e21594147e0d728730f010a947dbef375aed7dd0' tiff_4.7.0-3ubuntu5.debian.tar.xz \
  '4996f0c4f93094719b1ca5c6279b20e588773ba8a247533e486416fb662ddb88' tiff_4.7.2.orig.tar.xz \
  'cc5ca256639eb151041dcae3a4359ec105111f8c610123c9f96fefc456c4c0df' tiff_4.7.2.orig.tar.xz.sig \
  '37bfe1497fcd9d638d777306ab2c4476c650d013afc66f1ada58ab408c79bcd8' rouault-gitlab.gpg \
  > /provenance/source-input.sha256
sha256sum -c /provenance/source-input.sha256 > /provenance/source-hashes.log
gpg --show-keys --with-colons rouault-gitlab.gpg | grep -Fx 'fpr:::::::::B1FA7D81EEB8E66399178B9733EBBFC47B3DD87D:'
gpg --batch --no-default-keyring --keyring /source-input/rouault-keyring.gpg --import rouault-gitlab.gpg
gpg --batch --no-default-keyring --keyring /source-input/rouault-keyring.gpg \
  --verify tiff_4.7.2.orig.tar.xz.sig tiff_4.7.2.orig.tar.xz > /provenance/upstream-signature.log 2>&1
chmod 0644 /source-input/rouault-keyring.gpg
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/toolchain-packages.tsv
awk -F '\t' 'NR == FNR {base[$1] = $2 FS $3; next} !($1 in base) || base[$1] != $2 FS $3' \
  /provenance/base-packages.tsv /provenance/toolchain-packages.tsv > /provenance/toolchain-delta.tsv
mkdir -p /provenance/toolchain-debs
cd /provenance/toolchain-debs
while IFS="$(printf '\t')" read -r package version architecture; do
  apt-get download "$package:$architecture=$version" >> /provenance/toolchain-download.log 2>&1
done < /provenance/toolchain-delta.tsv
find . -maxdepth 1 -type f -name '*.deb' -exec sha256sum {} + | sort > /provenance/toolchain-debs.sha256
cp /usr/share/keyrings/ubuntu-archive-keyring.gpg /provenance/
cp /etc/apt/sources.list.d/ubuntu.sources /provenance/
apt-get indextargets > /provenance/apt-index-targets.txt
mkdir -p /provenance/apt-lists
find /var/lib/apt/lists -maxdepth 1 -type f \( -name '*InRelease' -o -name '*_Sources*' -o -name '*_Packages*' \) -exec cp {} /provenance/apt-lists/ \;
(cd /provenance/apt-lists && find . -type f -exec sha256sum {} + | sort) > /provenance/apt-index-sha256.txt
