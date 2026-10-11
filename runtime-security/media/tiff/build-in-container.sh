#!/usr/bin/env bash
# Offline build from the frozen native builder and saved authenticated inputs.
set -euo pipefail
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "${AELIRA_TIFF_HOST_ARCH:-}" = "$arch"
export DEBIAN_FRONTEND=noninteractive TZ=UTC LC_ALL=C.UTF-8
umask 022
cd /source-input
sha256sum -c /provenance/source-input.sha256
python3 /recipe/verify-ubuntu-source.py > /work/ubuntu-source-chain.log
python3 /recipe/verify-ubuntu-binaries.py > /work/ubuntu-binary-chain.log
gpg --batch --no-default-keyring --keyring /source-input/rouault-keyring.gpg \
  --fingerprint --with-colons | grep -Fx 'fpr:::::::::B1FA7D81EEB8E66399178B9733EBBFC47B3DD87D:'
gpg --batch --no-default-keyring --keyring /source-input/rouault-keyring.gpg \
  --verify tiff_4.7.2.orig.tar.xz.sig tiff_4.7.2.orig.tar.xz
cd /work
cp /source-input/tiff_4.7.0.orig.tar.bz2 .
cp /source-input/tiff_4.7.0-3ubuntu5.debian.tar.xz .
cp /source-input/tiff_4.7.0-3ubuntu5.dsc .
cp /source-input/tiff_4.7.2.orig.tar.xz .
dpkg-source -x tiff_4.7.0-3ubuntu5.dsc > source-unpack.log 2>&1
tar -xf tiff_4.7.2.orig.tar.xz
cp -a tiff-4.7.0/debian tiff-4.7.2/
cp /recipe/patch-series tiff-4.7.2/debian/patches/series
cp /recipe/ubuntu-patches.md tiff-4.7.2/debian/patches/README.aelira
cp /recipe/libtiff6.symbols tiff-4.7.2/debian/libtiff6.symbols
{ cat /recipe/changelog-entry.txt; cat tiff-4.7.0/debian/changelog; } > tiff-4.7.2/debian/changelog
cd tiff-4.7.2
test "$(dpkg-parsechangelog -S Version)" = 4.7.2-0aelira1
# The authenticated Ubuntu source changelog supplies a past fixed timestamp.
export SOURCE_DATE_EPOCH="$(dpkg-parsechangelog -l /work/tiff-4.7.0/debian/changelog -S Timestamp)"
test "$SOURCE_DATE_EPOCH" = 1773823259
find debian -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
dpkg-checkbuilddeps
DEB_BUILD_OPTIONS=parallel=4 dpkg-buildpackage -b -us -uc -d > /work/build.log 2>&1
dpkg-buildpackage -S -us -uc > /work/source-build.log 2>&1
cd /work
sha256sum tiff_4.7.2.orig.tar.xz tiff_4.7.2-0aelira1.debian.tar.xz \
  tiff_4.7.2-0aelira1.dsc *.deb *.buildinfo *.changes > SHA256SUMS
