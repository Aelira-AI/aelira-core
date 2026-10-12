#!/usr/bin/env bash
# Online phase. All build inputs are retained before the builder is frozen.
set -euo pipefail
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "${AELIRA_PILLOW_HOST_ARCH:-}" = "$arch"
export DEBIAN_FRONTEND=noninteractive LC_ALL=C TZ=UTC
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/base-packages.tsv
apt-get update > /provenance/apt-update.log 2>&1
apt-get install -y --no-install-recommends \
  build-essential python3-dev python3-venv python3-pip pkg-config \
  ca-certificates curl binutils libjpeg-dev zlib1g-dev libfreetype-dev \
  liblcms2-dev libwebp-dev libopenjp2-7-dev libavif-dev libraqm-dev \
  libxcb1-dev libdeflate-dev libjbig-dev liblzma-dev libzstd-dev liblerc-dev \
  /input/tiff/libtiff6_4.7.2-0aelira1_${arch}.deb \
  /input/tiff/libtiffxx6_4.7.2-0aelira1_${arch}.deb \
  /input/tiff/libtiff-dev_4.7.2-0aelira1_${arch}.deb \
  > /provenance/tool-install.log 2>&1
test "$(dpkg-query -W -f='${Version}' libtiff6)" = '4.7.2-0aelira1'
python3 /recipe/verify-tiff-input.py /input/tiff "$arch"
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/toolchain-packages.tsv
awk -F '\t' 'NR == FNR {base[$1] = $2 FS $3; next} !($1 in base) || base[$1] != $2 FS $3' \
  /provenance/base-packages.tsv /provenance/toolchain-packages.tsv > /provenance/toolchain-delta.tsv
mkdir -p /input/apt-debs /input/apt-lists /input/python
specs=()
while IFS="$(printf '\t')" read -r package version architecture; do
  case "$package" in libtiff6|libtiffxx6|libtiff-dev) continue ;; esac
  specs+=("$package:$architecture=$version")
done < /provenance/toolchain-delta.tsv
(cd /input/apt-debs && apt-get download "${specs[@]}") \
  > /provenance/toolchain-download.log 2>&1
(cd /input/apt-debs && find . -maxdepth 1 -name '*.deb' -type f -exec sha256sum {} + | sort) \
  > /provenance/toolchain-debs.sha256
cp /usr/share/keyrings/ubuntu-archive-keyring.gpg /provenance/
apt-get indextargets > /provenance/apt-index-targets.txt
find /var/lib/apt/lists -maxdepth 1 -type f \
  \( -name '*InRelease' -o -name '*_Packages*' \) -exec cp {} /input/apt-lists/ \;
(cd /input/apt-lists && find . -type f -exec sha256sum {} + | sort) \
  > /provenance/apt-index-sha256.txt
python3 /recipe/fetch-python-inputs.py /input/python > /provenance/python-fetch.log
python3 /recipe/verify-frozen-inputs.py /input /provenance "$arch" \
  > /provenance/frozen-inputs-verified.log
