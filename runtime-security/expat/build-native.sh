#!/bin/sh
set -eu

# Run inside the frozen Ubuntu 26.04 builder on a native runner.
# The host architecture is asserted by the outer harness before Docker starts.
arch="$(dpkg --print-architecture)"
case "$arch" in arm64|amd64) ;; *) echo "Unsupported package architecture: $arch" >&2; exit 1 ;; esac
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) echo "Container kernel/package architecture mismatch" >&2; exit 1 ;; esac
test "${AELIRA_EXPAT_HOST_ARCH:-}" = "$arch" || { echo "Native host architecture assertion missing or mismatched" >&2; exit 1; }
(cd /recipe && sha256sum -c patches.sha256)
umask 022
export TZ=UTC LC_ALL=C.UTF-8
if test "${AELIRA_EXPAT_PREINSTALLED_BUILD_DEPS:-0}" = 1; then
  # Build twice with the same already provisioned image and no network.
  command -v dpkg-source >/dev/null
  command -v quilt >/dev/null
else
  sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq dpkg-dev quilt devscripts >/work/tool-install.log 2>&1
fi
cd /work
if test -f /source-input/expat_2.7.4-1ubuntu0.2.dsc; then
  cp /source-input/expat_2.7.4-1ubuntu0.2.dsc .
  cp /source-input/expat_2.7.4.orig.tar.gz .
  cp /source-input/expat_2.7.4-1ubuntu0.2.debian.tar.xz .
  printf '%s\n' 'Using saved Ubuntu source inputs' >source-fetch.log
else
  apt-get source --download-only expat=2.7.4-1ubuntu0.2 >source-fetch.log 2>&1
fi
sha256sum -c /recipe/inputs.sha256
if test "${AELIRA_EXPAT_PREINSTALLED_BUILD_DEPS:-0}" != 1; then
  apt-get build-dep -y -qq ./expat_2.7.4-1ubuntu0.2.dsc >build-dep.log 2>&1
fi
dpkg-source -x expat_2.7.4-1ubuntu0.2.dsc >source-unpack.log 2>&1
cd expat-2.7.4
dpkg-checkbuilddeps
# Anchor archive metadata to the authenticated Ubuntu source release date.
# The local changelog date may be ahead of UTC wall time during preparation;
# dpkg-deb clamps newer mtimes but leaves earlier build-time mtimes unchanged.
export SOURCE_DATE_EPOCH="$(date -u -d "$(dpkg-parsechangelog -S Date)" +%s)"
cp /recipe/ubuntu-93990.patch debian/patches/aelira-CVE-2026-93990.patch
cp /recipe/ubuntu-93990-tests.patch debian/patches/aelira-CVE-2026-93990-tests.patch
cp /recipe/upstream-102633.patch debian/patches/aelira-CVE-2026-102633.patch
cp /recipe/ubuntu-77214.patch debian/patches/aelira-CVE-2026-77214.patch
cat >>debian/patches/series <<'EOF'
aelira-CVE-2026-93990.patch
aelira-CVE-2026-93990-tests.patch
aelira-CVE-2026-102633.patch
aelira-CVE-2026-77214.patch
EOF
QUILT_PATCHES=debian/patches quilt push -a >../patch-apply.log 2>&1
{
  cat <<'EOF'
expat (2.7.4-1ubuntu0.2+aelira1) resolute; urgency=medium

  * Backport upstream UTF-16 surrogate validation (CVE-2026-93990).
  * Backport upstream allocation overflow validation (CVE-2026-102633).
  * Backport upstream XML_ParseBuffer capacity validation (CVE-2026-77214).
  * Retain Ubuntu's existing security patches and build configuration.

 -- Aelira <security@aelira.ai>  Mon, 12 Oct 2026 00:00:00 +0000

EOF
  cat debian/changelog
} >debian/changelog.new
mv debian/changelog.new debian/changelog
# dpkg-deb honors SOURCE_DATE_EPOCH; dpkg-source stores Debian file mtimes.
# Normalize those archive mtimes without changing any source file bytes.
find debian -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
dpkg-source -b . >../source-package-build.log 2>&1
DEB_BUILD_OPTIONS=parallel=4 dpkg-buildpackage -b -us -uc >../package-build.log 2>&1
make -C build check >../upstream-tests.log 2>&1
sha256sum "../libexpat1_2.7.4-1ubuntu0.2+aelira1_${arch}.deb" ../expat_2.7.4-1ubuntu0.2+aelira1.dsc ../expat_2.7.4-1ubuntu0.2+aelira1.debian.tar.xz >../output-sha256.txt
