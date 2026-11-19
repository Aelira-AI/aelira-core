#!/bin/bash
set -euo pipefail

# Online, signed-APT preparation. /source-input and /provenance are writable.
# Every network step names itself on the container stream, carries its own
# timeout, and tees its output to the stream as well as to its provenance
# log, so a slow or stalled mirror is bounded and visible instead of silent.
# The completion marker below is this script's last action: the acceptance
# harness treats the marker, not the container exit, as success, because a
# leftover child wedged in-kernel on a degraded archive.ubuntu.com socket
# has been observed to hold an attached docker run open long after every
# step here had completed (issue #1035).
arch="$(dpkg --print-architecture)"
test "${AELIRA_EXPAT_HOST_ARCH:-}" = "$arch"
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

# Turn a degraded mirror fetch into a bounded, retried failure: 20 seconds
# per connection attempt, five attempts before apt gives the URI up.
apt_opts=(-o APT::Acquire::Retries=5 -o Acquire::http::Timeout=20)

# run_step <label> <timeout-seconds> <provenance-log> <command...>
# The provenance log keeps its existing name and contract; the same output
# also reaches the container stream so the console shows the running step.
run_step() {
  local label=$1 limit=$2 log=$3 status=0
  shift 3
  echo "expat-prepare: $label: starting (timeout ${limit}s)"
  timeout -k 30 "$limit" "$@" 2>&1 | tee "$log" || status=$?
  if (( status != 0 )); then
    echo "expat-prepare: $label: FAILED with status $status" >&2
    tail -n 20 "$log" >&2 || true
    exit "$status"
  fi
  echo "expat-prepare: $label: done"
}

dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/base-packages.tsv
run_step 'apt-get update' 300 /provenance/apt-update.log \
  apt-get "${apt_opts[@]}" update
run_step 'install build tooling and libexpat1' 480 /provenance/tool-install.log \
  apt-get "${apt_opts[@]}" install -y dpkg-dev quilt devscripts python3 binutils libexpat1=2.7.4-1ubuntu0.2
cd /source-input
run_step 'download expat 2.7.4-1ubuntu0.2 source' 300 /provenance/source-fetch.log \
  apt-get "${apt_opts[@]}" source --download-only expat=2.7.4-1ubuntu0.2
sha256sum -c /recipe/inputs.sha256 > /provenance/source-hashes.log
run_step 'install build dependencies' 480 /provenance/build-dep.log \
  apt-get "${apt_opts[@]}" build-dep -y ./expat_2.7.4-1ubuntu0.2.dsc
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /provenance/toolchain-packages.tsv
# The base digest retains its original packages. Save every added/upgraded
# package so the frozen toolchain can be reconstructed after runner teardown.
awk -F '\t' 'NR == FNR {base[$1] = $2 FS $3; next}
  !($1 in base) || base[$1] != $2 FS $3' \
  /provenance/base-packages.tsv /provenance/toolchain-packages.tsv \
  > /provenance/toolchain-delta.tsv
mkdir -p /provenance/toolchain-debs
cd /provenance/toolchain-debs
# Each package download is its own bounded step: 120 seconds per package,
# with the failure naming the package that could not be fetched.
echo 'expat-prepare: toolchain downloads: starting (120s per package)'
while IFS="$(printf '\\t')" read -r package version architecture; do
  status=0
  timeout -k 15 120 apt-get "${apt_opts[@]}" download "$package:$architecture=$version" 2>&1 \
    | tee -a /provenance/toolchain-download.log || status=$?
  if (( status != 0 )); then
    echo "expat-prepare: toolchain download FAILED for $package $version $architecture (status $status)" >&2
    tail -n 20 /provenance/toolchain-download.log >&2 || true
    exit "$status"
  fi
done < /provenance/toolchain-delta.tsv
echo 'expat-prepare: toolchain downloads: done'
find . -maxdepth 1 -type f -name '*.deb' -exec sha256sum {} + \
  > /provenance/toolchain-debs.unsorted.sha256
sort /provenance/toolchain-debs.unsorted.sha256 > /provenance/toolchain-debs.sha256
test -s /provenance/toolchain-debs.sha256
cp /usr/share/keyrings/ubuntu-archive-keyring.gpg /provenance/
cp /etc/apt/sources.list.d/ubuntu.sources /provenance/
apt-get indextargets > /provenance/apt-index-targets.txt
mkdir -p /provenance/apt-lists
find /var/lib/apt/lists -maxdepth 1 -type f \( -name '*InRelease' -o -name '*_Sources*' -o -name '*_Packages*' \) -exec cp {} /provenance/apt-lists/ \;
find /provenance/apt-lists -type f -exec sha256sum {} + > /provenance/apt-index-sha256.txt
# Completion marker, always the final action. The harness polls for this
# file and, once present, releases the container with force rather than
# waiting for its exit; a wedged teardown child must not lose a run whose
# work completed.
sync
printf 'complete\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$arch" > /provenance/prepare-complete.txt
