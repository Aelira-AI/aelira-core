#!/usr/bin/env bash
set -euo pipefail
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "$AELIRA_XDG_HOST_ARCH" = "$arch"
export DEBIAN_FRONTEND=noninteractive LC_ALL=C TZ=UTC
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/base-packages.tsv
# Debian trixie-security has moved past the reviewed Thunderbird, so that exact
# build comes from a fixed snapshot of trixie-security. The snapshot's signed
# InRelease is checked against the archive keyring like any other source; only
# its Valid-Until expiry is waived. Nothing else may be installed from it.
snapshot=20261025T035125Z
cat > /etc/apt/sources.list.d/aelira-thunderbird-snapshot.sources <<EOF
Types: deb
URIs: http://snapshot.debian.org/archive/debian-security/$snapshot
Suites: trixie-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
Check-Valid-Until: no
EOF
cat > /etc/apt/preferences.d/aelira-thunderbird-snapshot <<'EOF'
Package: thunderbird
Pin: origin "snapshot.debian.org"
Pin-Priority: 100

Package: *
Pin: origin "snapshot.debian.org"
Pin-Priority: -1
EOF
apt-get update -o APT::Update::Error-Mode=any > /provenance/apt-update.log 2>&1
apt-get install -y --no-install-recommends thunderbird=1:140.17.0esr-1~deb13u1 \
  xvfb python3 ca-certificates xdg-utils > /provenance/tool-install.log 2>&1
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/toolchain-packages.tsv
awk -F '\t' 'NR==FNR {base[$1]=$2 FS $3; next} !($1 in base) || base[$1]!=$2 FS $3' \
  /provenance/base-packages.tsv /provenance/toolchain-packages.tsv > /provenance/toolchain-delta.tsv
mkdir /provenance/toolchain-debs /provenance/apt-lists
cd /provenance/toolchain-debs
while IFS="$(printf '\t')" read -r name version architecture; do
  apt-get download "$name:$architecture=$version" >> /provenance/toolchain-download.log 2>&1
done < /provenance/toolchain-delta.tsv
find . -maxdepth 1 -type f -name '*.deb' -exec sha256sum {} + | sort > /provenance/toolchain-debs.sha256
cp /usr/share/keyrings/debian-archive-keyring.gpg /provenance/
find /var/lib/apt/lists -maxdepth 1 -type f \( -name '*InRelease' -o -name '*_Packages*' \) \
  -exec cp {} /provenance/apt-lists/ \;
(cd /provenance/apt-lists && find . -type f -exec sha256sum {} + | sort) > /provenance/apt-index-sha256.txt
