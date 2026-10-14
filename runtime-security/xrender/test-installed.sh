#!/usr/bin/env bash
# Offline installed-package, ABI and Xvfb pixel-readback acceptance.
set -euo pipefail
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "${AELIRA_XRENDER_HOST_ARCH:-}" = "$arch"
triplet=$(dpkg-architecture -qDEB_HOST_MULTIARCH)
old=$(readlink -f "/usr/lib/$triplet/libXrender.so.1")
test -f "$old"
test "$(dpkg-query -W -f='${Version}' libxrender1)" = '1:0.9.12-1build1'
test "$(dpkg-deb -f /candidate.deb Package)" = libxrender1
test "$(dpkg-deb -f /candidate.deb Version)" = '1:0.9.12-1build1+aelira1'
test "$(dpkg-deb -f /candidate.deb Architecture)" = "$arch"
mkdir -p /tmp/candidate /evidence
dpkg-deb -x /candidate.deb /tmp/candidate
new=$(readlink -f "/tmp/candidate/usr/lib/$triplet/libXrender.so.1")
test -f "$new"
sha256sum "$new" > /evidence/candidate-library.sha256
readelf -d "$old" | grep SONAME > /evidence/original-soname.txt
readelf -d "$new" | grep SONAME > /evidence/candidate-soname.txt
diff -u /evidence/original-soname.txt /evidence/candidate-soname.txt
readelf -h "$old" > /evidence/original-elf-header.txt
readelf -h "$new" > /evidence/candidate-elf-header.txt
grep 'Machine:' /evidence/original-elf-header.txt | sed 's/^ *//' > /evidence/original-machine.txt
grep 'Machine:' /evidence/candidate-elf-header.txt | sed 's/^ *//' > /evidence/candidate-machine.txt
diff -u /evidence/original-machine.txt /evidence/candidate-machine.txt
nm -D --defined-only "$old" | awk '{print $2, $3}' | sort -u > /evidence/original-symbols.txt
nm -D --defined-only "$new" | awk '{print $2, $3}' | sort -u > /evidence/candidate-symbols.txt
test -s /evidence/original-symbols.txt
diff -u /evidence/original-symbols.txt /evidence/candidate-symbols.txt
dpkg -i /candidate.deb > /evidence/dpkg-install.log 2>&1
test "$(dpkg-query -W -f='${Status}' libxrender1)" = 'install ok installed'
test "$(dpkg-query -W -f='${Version}' libxrender1)" = '1:0.9.12-1build1+aelira1'
installed=$(readlink -f "/usr/lib/$triplet/libXrender.so.1")
test "$(sha256sum "$installed" | cut -d' ' -f1)" = "$(sha256sum "$new" | cut -d' ' -f1)"
sha256sum "$installed" > /evidence/installed-library.sha256
dpkg -L libxrender1 > /evidence/installed-files.txt
gcc -std=c11 -D_GNU_SOURCE -Wall -Wextra -Werror /recipe/xrender-client.c \
  -o /tmp/xrender-client -lXrender -lX11 -ldl
ldd /tmp/xrender-client > /evidence/client-ldd.txt
unset LD_LIBRARY_PATH
export DISPLAY=:99
Xvfb "$DISPLAY" -screen 0 800x600x24 -nolisten tcp \
  > /evidence/xvfb.log 2>&1 &
xvfb_pid=$!
trap 'kill "$xvfb_pid" 2>/dev/null || true; wait "$xvfb_pid" 2>/dev/null || true' EXIT
ready=0
for attempt in $(seq 1 50); do
  if xdpyinfo >/dev/null 2>&1; then ready=1; break; fi
  sleep 0.1
done
test "$ready" = 1
timeout 30 /tmp/xrender-client > /evidence/xrender-client.log 2>&1
grep -qF 'rendered red pixels' /evidence/xrender-client.log
grep -qF 'Loaded XRender library: /lib/' /evidence/xrender-client.log \
  || grep -qF 'Loaded XRender library: /usr/lib/' /evidence/xrender-client.log
loaded=$(sed -n 's/^Loaded XRender library: //p' /evidence/xrender-client.log)
test "$(readlink -f "$loaded")" = "$installed"
test "$(sha256sum "$loaded" | cut -d' ' -f1)" = "$(sha256sum "$new" | cut -d' ' -f1)"
sha256sum "$loaded" > /evidence/loaded-library.sha256
printf 'installed_xrender_acceptance=pass architecture=%s version=%s\n' \
  "$arch" "$(dpkg-query -W -f='${Version}' libxrender1)"
