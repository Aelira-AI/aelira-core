#!/bin/sh
# Rebuild Debian's trixie libxml2 package with the reviewed entity limit patch.
set -eu

input=/tmp/libxml2-input
work=/tmp/libxml2-build
out=/tmp/libxml2-debs
version='2.12.7+dfsg+really2.9.14-2.1+deb13u3+aelira1'
test ! -e "$work" && test ! -e "$out"

cd "$input"
printf '%s  %s\n' \
    4fe913dec8b1ab89d13b489b419a8203176ea39e931eaa0d25b17eafb9c279e9 source.tar.xz \
    3b6d265f482d6a8fbe3c056d2006fb3b563b4a838f7258b388ac5f0b29206921 debian.tar.xz \
    6c2b8a7b6fa470efcb1854e5c73bd7e1ae1663d8b111e27ce9232bbc1fe44f54 CVE-2026-6653.patch \
    | sha256sum --check --strict

mkdir "$work" "$out"
tar -xJf "$input/source.tar.xz" -C "$work"
cd "$work/libxml2-2.9.14"
tar -xJf "$input/debian.tar.xz"

# Apply all 40 distribution patches first. The Ubuntu backport must then
# apply without fuzz. Its parser.c hunks have a consistent three-line offset
# from another distribution edit; any other offset is rejected.
export QUILT_PATCHES=debian/patches
quilt push -a
patch --batch --forward --fuzz=0 --dry-run -p1 \
    < "$input/CVE-2026-6653.patch" > "$work/patch-dry-run.log"
if grep -Ei 'fuzz|FAILED' "$work/patch-dry-run.log" || \
    awk '/offset/ && !/offset -3 lines/ { found=1; print } END { exit !found }' \
        "$work/patch-dry-run.log"; then
    echo 'Entity patch did not apply cleanly' >&2
    exit 1
fi
cp "$input/CVE-2026-6653.patch" debian/patches/CVE-2026-6653.patch
printf '%s\n' CVE-2026-6653.patch >> debian/patches/series
quilt push

sed -i "1i libxml2 ($version) trixie; urgency=high\n\n  * Backport Ubuntu's reviewed entity amplification limit patch for CVE-2026-6653.\n\n -- Aelira Security <security@aelira.ai>  Sun, 18 Oct 2026 00:00:00 +0000\n" debian/changelog

# Debian's own rules retain its configure options, hardening, metadata and
# symbols. The build profile removes unused Python bindings; -B builds only
# architecture-dependent packages needed by the runtime/development images.
DEB_BUILD_PROFILES=nopython SOURCE_DATE_EPOCH=0 \
    dpkg-buildpackage -B -us -uc -P nopython

cp "$work"/libxml2_*_*.deb "$out/"
cp "$work"/libxml2-dev_*_*.deb "$out/"
cp "$work"/libxml2-utils_*_*.deb "$out/"
test "$(find "$out" -name '*.deb' | wc -l)" -eq 3

mkdir "$work/extracted"
dpkg-deb -x "$out"/libxml2_*.deb "$work/extracted"
multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"
library="/usr/lib/$multiarch/libxml2.so.2.9.14"
test -f "$work/extracted$library"
sha256sum "$work/extracted$library" | sed "s|$work/extracted||" \
    > "$out/libxml2.sha256"
(
    printf 'source_version=2.12.7+dfsg+really2.9.14-2.1+deb13u3\n'
    printf 'rebuilt_version=%s\n' "$version"
    printf 'architecture=%s\n' "$(dpkg --print-architecture)"
    printf 'source_sha256=4fe913dec8b1ab89d13b489b419a8203176ea39e931eaa0d25b17eafb9c279e9\n'
    printf 'debian_sha256=3b6d265f482d6a8fbe3c056d2006fb3b563b4a838f7258b388ac5f0b29206921\n'
    printf 'patch_sha256=6c2b8a7b6fa470efcb1854e5c73bd7e1ae1663d8b111e27ce9232bbc1fe44f54\n'
    sha256sum "$out"/*.deb | awk '{ sub(/^.*\//, "", $2); print "package_sha256=" $2 " " $1 }'
    awk '{ print "library_sha256=" $1; print "library_path=" $2 }' "$out/libxml2.sha256"
) > "$out/receipt.txt"
