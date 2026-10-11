#!/usr/bin/env bash
# Run offline in the frozen native builder, with /packages holding build-a output.
set -euo pipefail
work=${AELIRA_TIFF_TEST_WORK:-/tmp/tiff-installed}
mkdir -p "$work"
cd "$work"
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "${AELIRA_TIFF_HOST_ARCH:-}" = "$arch"
version=4.7.2-0aelira1
for package in libtiff6 libtiffxx6 libtiff-dev libtiff-tools; do
  deb="/packages/${package}_${version}_${arch}.deb"
  test -f "$deb"
  test "$(dpkg-deb -f "$deb" Package)" = "$package"
  test "$(dpkg-deb -f "$deb" Version)" = "$version"
  test "$(dpkg-deb -f "$deb" Architecture)" = "$arch"
done
export DEBIAN_FRONTEND=noninteractive
dpkg -i \
  /packages/libtiff6_${version}_${arch}.deb /packages/libtiffxx6_${version}_${arch}.deb \
  /packages/libtiff-dev_${version}_${arch}.deb /packages/libtiff-tools_${version}_${arch}.deb \
  > "$work/package-install.log" 2>&1
for package in libtiff6 libtiffxx6 libtiff-dev libtiff-tools; do
  test "$(dpkg-query -W -f='${Version} ${Architecture}' "$package")" = "$version $arch"
done
triplet=$(dpkg-architecture -qDEB_HOST_MULTIARCH)
lib="/usr/lib/$triplet/libtiff.so.6.3.0"
test -f "$lib"
test "$(readlink -f "/usr/lib/$triplet/libtiff.so.6")" = "$lib"
readelf -d "$lib" > "$work/dynamic-section.log"
grep -Eq '\(SONAME\).*\[libtiff.so.6\]' "$work/dynamic-section.log"
for needed in libz.so.1 libdeflate.so.0 libjpeg.so.8 libjbig.so.0 libLerc.so.4 liblzma.so.5 libzstd.so.1 libwebp.so.7; do
  grep -Fq "[$needed]" "$work/dynamic-section.log"
done
awk 'NR > 1 { s=$1; if (s ~ /^LIBTIFF_/) sub(/@.*/, "", s); else sub(/@/, "@@", s); print s }' \
  /recipe/libtiff6.symbols | sort -u > "$work/declared-symbols.txt"
nm -D --defined-only "$lib" | awk '{print $3}' | sort -u > "$work/actual-symbols.txt"
comm -23 "$work/declared-symbols.txt" "$work/actual-symbols.txt" > "$work/missing-symbols.txt"
test ! -s "$work/missing-symbols.txt"
cc -Wall -Wextra -Werror /recipe/package-consumer.c -o "$work/package-consumer" -ltiff
"$work/package-consumer" "$work/two-page.tif"
tiffcp -c lzw "$work/two-page.tif" "$work/two-page-lzw.tif"
tiffcp -c zip "$work/two-page.tif" "$work/two-page-zip.tif"
"$work/package-consumer" --verify "$work/two-page-lzw.tif" 5
"$work/package-consumer" --verify "$work/two-page-zip.tif" 8
cc -Wall -Wextra -Werror /recipe/codec-consumer.c -o "$work/codec-consumer" -ltiff
"$work/codec-consumer" "$work"
sha256sum "$lib" "$work/two-page.tif" "$work/two-page-lzw.tif" "$work/two-page-zip.tif"
printf 'installed_tiff_acceptance=pass architecture=%s version=%s\n' "$arch" "$version"
