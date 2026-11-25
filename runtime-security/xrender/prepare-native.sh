#!/usr/bin/env bash
# Online preparation in a pinned Ubuntu image; all build runs are offline.
# Every network step names itself on the container stream, carries its own
# timeout, and tees its output to the stream as well as to its provenance
# log, so a slow or stalled mirror is bounded and visible instead of silent.
# The completion marker at the end is this script's last action: the build
# harness treats the marker, not the container exit, as success, because a
# leftover child wedged in-kernel on a degraded archive.ubuntu.com socket
# has been observed to hold an attached docker run open long after every
# step here had completed (issues #1035, #1037).
set -euo pipefail
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "${AELIRA_XRENDER_HOST_ARCH:-}" = "$arch"
export DEBIAN_FRONTEND=noninteractive LC_ALL=C TZ=UTC
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/base-packages.tsv
# Binary indexes keep the base image's full component set (the X11 build
# dependencies and toolchain resolve across main and universe), but SOURCE
# indexes are restricted to main: the only source package this recipe
# resolves is libxrender 1:0.9.12-1build1 (resolute/main), and a blanket
# deb-src over every component made each update below carry the universe
# and multiverse source indexes (~16 MB and ~2 MB compressed) that nothing
# here reads — what pushed the pinned-snapshot update past its bound on
# slow snapshot windows. Trim the stanzas to main, then re-add the
# remaining components as binary-only stanzas. The guard matches the
# amd64/ubuntu.com layout only, so the arm64 ports file (main+restricted,
# no universe) keeps exactly today's behavior.
sed -i -e 's/^Types: deb$/Types: deb deb-src/' -e 's/^Components: main universe restricted multiverse$/Components: main/' /etc/apt/sources.list.d/ubuntu.sources
if grep -q '^URIs: http://archive.ubuntu.com/ubuntu/$' /etc/apt/sources.list.d/ubuntu.sources; then
  cat >> /etc/apt/sources.list.d/ubuntu.sources <<'AELIRA-BINARY-REST'

Types: deb
URIs: http://archive.ubuntu.com/ubuntu/
Suites: resolute resolute-updates resolute-backports
Components: restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: http://security.ubuntu.com/ubuntu/
Suites: resolute-security
Components: restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
AELIRA-BINARY-REST
fi

# Turn a degraded mirror fetch into a bounded, retried failure: 20 seconds
# per connection attempt, five attempts before apt gives the URI up.
apt_opts=(-o APT::Acquire::Retries=5 -o Acquire::http::Timeout=20)

# run_step <label> <timeout-seconds> <provenance-log> <command...>
# The provenance log keeps its existing name and contract; the same output
# also reaches the container stream so the console shows the running step.
run_step() {
  local label=$1 limit=$2 log=$3 status=0
  shift 3
  echo "xrender-prepare: $label: starting (timeout ${limit}s)"
  timeout -k 30 "$limit" "$@" 2>&1 | tee "$log" || status=$?
  if (( status != 0 )); then
    echo "xrender-prepare: $label: FAILED with status $status" >&2
    tail -n 20 "$log" >&2 || true
    exit "$status"
  fi
  echo "xrender-prepare: $label: done"
}

# Reproducibility pin: amd64 fetches come from a timestamped snapshot of
# the Ubuntu archive, so a mirror rotation or a pocket moving a pinned
# version cannot kill this job. Override the stamp with
# AELIRA_XRENDER_UBUNTU_SNAPSHOT; an explicitly empty value keeps the live
# mirrors, which is the offline-reviewer contract. arm64 stays live
# because snapshot.ubuntu.com serves ubuntu-ports with HTTP 401 (probed
# 2026-11-19): no ports snapshots exist to pin yet, so only amd64 can
# be stamped. Note the plain ${VAR-default}: an empty override must
# stay empty rather than fall back to the default stamp.
snapshot_stamp="${AELIRA_XRENDER_UBUNTU_SNAPSHOT-20261118T120000Z}"
if [[ "$arch" = amd64 && -n "$snapshot_stamp" ]]; then
  # The CA bundle baked into the pinned base image fails certificate
  # verification against snapshot.ubuntu.com. Lift trust with one round
  # trip to the live http mirrors (install/upgrade ca-certificates,
  # whose postinst reruns update-ca-certificates), then rewrite the
  # sources to the pinned https snapshot. Every fetch after this block
  # is timestamp-pinned and carries Acquire::https::CAInfo so apt uses
  # the freshly regenerated bundle rather than any stale default.
  run_step 'apt-get update for CA bootstrap' 300 /provenance/ca-bootstrap-update.log \
    apt-get "${apt_opts[@]}" update -o APT::Update::Error-Mode=any
  run_step 'ca-certificates from live mirrors' 300 /provenance/ca-bootstrap-install.log \
    apt-get "${apt_opts[@]}" install -y ca-certificates
  sed -i "s|^URIs: http://archive.ubuntu.com/ubuntu/$|URIs: https://snapshot.ubuntu.com/ubuntu/${snapshot_stamp}/|; s|^URIs: http://security.ubuntu.com/ubuntu/$|URIs: https://snapshot.ubuntu.com/ubuntu/${snapshot_stamp}/|" \
    /etc/apt/sources.list.d/ubuntu.sources
  grep '^URIs:' /etc/apt/sources.list.d/ubuntu.sources | tee /provenance/snapshot-pin.txt
  apt_opts+=(-o Acquire::https::CAInfo=/etc/ssl/certs/ca-certificates.crt)
fi

run_step 'apt-get update' 900 /provenance/apt-update.log \
  apt-get "${apt_opts[@]}" update -o APT::Update::Error-Mode=any
run_step 'install build tooling and libxrender' 900 /provenance/tool-install.log \
  apt-get "${apt_opts[@]}" install -y --no-install-recommends \
  build-essential debhelper dpkg-dev fakeroot quilt python3 binutils \
  libxrender1=1:0.9.12-1build1 libxrender-dev=1:0.9.12-1build1 libx11-dev libxext-dev \
  xvfb x11-utils
cd /source-input
run_step 'download libxrender 1:0.9.12-1build1 source' 600 /provenance/source-fetch.log \
  apt-get "${apt_opts[@]}" source --download-only libxrender=1:0.9.12-1build1
sha256sum -c /recipe/inputs.sha256 > /provenance/source-hashes.log
run_step 'install build dependencies' 900 /provenance/build-dep.log \
  apt-get "${apt_opts[@]}" build-dep -y ./libxrender_0.9.12-1build1.dsc
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/toolchain-packages.tsv
awk -F '\t' 'NR == FNR {base[$1] = $2 FS $3; next} !($1 in base) || base[$1] != $2 FS $3' \
  /provenance/base-packages.tsv /provenance/toolchain-packages.tsv \
  > /provenance/toolchain-delta.tsv
mkdir -p /provenance/toolchain-debs
cd /provenance/toolchain-debs
# Each package download is its own bounded step: 300 seconds per package,
# with the failure naming the package that could not be fetched. Large
# compiler debs need minutes on a mildly degraded mirror, while
# APT::Acquire::Retries=5 with Acquire::http::Timeout=20 still kills a
# truly dead connection in about 100 seconds.
echo 'xrender-prepare: toolchain downloads: starting (300s per package)'
while IFS="$(printf '\t')" read -r package version architecture; do
  status=0
  timeout -k 15 300 apt-get "${apt_opts[@]}" download "$package:$architecture=$version" 2>&1 \
    | tee -a /provenance/toolchain-download.log || status=$?
  if (( status != 0 )); then
    echo "xrender-prepare: toolchain download FAILED for $package $version $architecture (status $status)" >&2
    tail -n 20 /provenance/toolchain-download.log >&2 || true
    exit "$status"
  fi
done < /provenance/toolchain-delta.tsv
echo 'xrender-prepare: toolchain downloads: done'
find . -maxdepth 1 -type f -name '*.deb' -exec sha256sum {} + | sort \
  > /provenance/toolchain-debs.sha256
test -s /provenance/toolchain-debs.sha256
cp /usr/share/keyrings/ubuntu-archive-keyring.gpg /provenance/
cp /etc/apt/sources.list.d/ubuntu.sources /provenance/
apt-get indextargets > /provenance/apt-index-targets.txt
mkdir -p /provenance/apt-lists
find /var/lib/apt/lists -maxdepth 1 -type f \
  \( -name '*InRelease' -o -name '*_Sources*' -o -name '*_Packages*' \) \
  -exec cp {} /provenance/apt-lists/ \;
(cd /provenance/apt-lists && find . -type f -exec sha256sum {} + | sort) \
  > /provenance/apt-index-sha256.txt
python3 /recipe/verify-provenance.py > /provenance/signed-chain.log
# Completion marker, always the final action. The harness polls for this
# file and, once present, releases the container with force rather than
# waiting for its exit; a wedged teardown child must not lose a run whose
# work completed.
sync
printf 'complete\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$arch" > /provenance/prepare-complete.txt
