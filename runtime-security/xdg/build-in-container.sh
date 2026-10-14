#!/usr/bin/env bash
set -euo pipefail
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "$AELIRA_XDG_HOST_ARCH" = "$arch"
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1791849600 TMPDIR=/work/tmp
mkdir /work/tmp
python3 -B /recipe/verify-provenance.py > /work/signed-chain.json
python3 -B /recipe/test-source.py --input /source-input --scratch /work/source-controls > /work/source-controls.json
python3 -B /recipe/build-source.py --input /source-input --out /work/packages > /work/source-receipt.json
cd /work/packages/xdg-utils-1.2.1-aelira
bash /recipe/test-mailto-native.sh baseline .pc/aelira-attachment-origin.patch/scripts/xdg-email.in
bash /recipe/test-mailto-native.sh patched scripts/xdg-email.in
dpkg-buildpackage -b -us -uc -j2
printf 'native_xdg_package=pass architecture=%s version=1.2.1-2ubuntu2+aelira2\n' "$arch"
