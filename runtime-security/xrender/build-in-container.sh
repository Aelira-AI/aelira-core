#!/usr/bin/env bash
# Fresh offline package and source build in the frozen native builder.
set -euo pipefail
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "${AELIRA_XRENDER_HOST_ARCH:-}" = "$arch"
export LC_ALL=C.UTF-8 TZ=UTC HOME=/work
umask 022
(cd /recipe && sha256sum -c patch.sha256) > /work/patch-hash.log
python3 /recipe/verify-provenance.py > /work/signed-chain.log
cp /source-input/libxrender_0.9.12.orig.tar.gz \
   /source-input/libxrender_0.9.12.orig.tar.gz.asc \
   /source-input/libxrender_0.9.12-1build1.diff.gz \
   /source-input/libxrender_0.9.12-1build1.dsc /work/
cd /work
dpkg-source -x libxrender_0.9.12-1build1.dsc > source-unpack.log 2>&1
cd libxrender-0.9.12
test "$(dpkg-parsechangelog -S Version)" = '1:0.9.12-1build1'
export SOURCE_DATE_EPOCH="$(dpkg-parsechangelog -S Timestamp)"
test "$SOURCE_DATE_EPOCH" -gt 0
printf '%s\n' "$SOURCE_DATE_EPOCH" > /work/source-date-epoch.txt
patch --batch --fuzz=0 -p1 < /recipe/upstream-0564bce.patch > /work/patch-apply.log
sha256sum src/Xrender.c > /work/final-source.sha256
test "$(sha256sum src/Xrender.c | cut -d' ' -f1)" = \
  c5c93efd29a9e13cabe4f6fe99863c13a14d6c006e90267f747e9da9a8b800c8
{ cat /recipe/changelog-entry.txt; cat debian/changelog; } > debian/changelog.new
mv debian/changelog.new debian/changelog
test "$(dpkg-parsechangelog -S Version)" = '1:0.9.12-1build1+aelira1'
find debian -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
dpkg-checkbuilddeps
dpkg-source -b . > /work/source-build.log 2>&1
DEB_BUILD_OPTIONS=parallel=4 dpkg-buildpackage -b -us -uc \
  > /work/package-build.log 2>&1
python3 /recipe/generate-control.py src/Xrender.c /work
for optimization in 0 2; do
  gcc -std=c11 -Wall -Wextra -Werror -Wno-sign-compare -O"$optimization" -g \
    -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer \
    -I./src -I/usr/include/X11/extensions \
    /work/xrender-bound-control.c -o "/work/xrender-bound-O$optimization"
  ASAN_OPTIONS=detect_leaks=1 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 "/work/xrender-bound-O$optimization" \
    > "/work/patched-O$optimization.log" 2>&1
done
gcc -std=c11 -Wall -Wextra -Werror -Wno-sign-compare -O0 -g \
  -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer \
  -I./src -I/usr/include/X11/extensions \
  /work/xrender-baseline-control.c -o /work/xrender-baseline
set +e
ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 /work/xrender-baseline trigger \
  > /work/baseline-failure.log 2>&1
baseline_status=$?
set -e
test "$baseline_status" -ne 0
grep -q 'AddressSanitizer: heap-buffer-overflow' /work/baseline-failure.log
printf '%s\n' "$baseline_status" > /work/baseline-exit-status.txt
cd /work
sha256sum libxrender_0.9.12.orig.tar.gz \
  libxrender_0.9.12-1build1+aelira1.diff.gz \
  libxrender_0.9.12-1build1+aelira1.dsc \
  ./*.deb > SHA256SUMS
