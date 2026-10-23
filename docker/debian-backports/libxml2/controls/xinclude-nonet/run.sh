#!/bin/sh
# CVE-2026-86144: runs after the build. The probe must pass against the
# rebuilt library and fail against Debian's stock binary of the same source
# version, downloaded from the pinned archive.
set -eu
name=xinclude-nonet tree=$1 work=$2 dir=$3 built=$4
multiarch=$(dpkg-architecture -qDEB_HOST_MULTIARCH)
source_version=$BACKPORT_SOURCE_VERSION
dpkg-deb -x "$built"/libxml2_*.deb "$work/patched"
dpkg-deb -x "$built"/libxml2-dev_*.deb "$work/patched"
(cd "$work" && apt-get download -q "libxml2=$source_version" > /dev/null)
dpkg-deb -x "$work"/libxml2_*.deb "$work/stock"
gcc -O2 -Wall -Werror -I"$work/patched/usr/include/libxml2" "$dir/probe.c" \
    "$work/patched/usr/lib/$multiarch/libxml2.so.2" -o "$work/probe"
printf 'local-text' > "$work/local.txt"
LD_LIBRARY_PATH="$work/patched/usr/lib/$multiarch" "$work/probe" "$work/local.txt"
test "$(LD_LIBRARY_PATH="$work/patched/usr/lib/$multiarch" ldd "$work/probe" | awk '/libxml2/ { print $3 }')" = \
    "$work/patched/usr/lib/$multiarch/libxml2.so.2"
set +e
LD_LIBRARY_PATH="$work/stock/usr/lib/$multiarch" "$work/probe" "$work/local.txt"
status=$?
set -e
test "$status" -eq 1
echo "control $name: patched passed, baseline failed"
