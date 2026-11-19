#!/usr/bin/env bash
set -euo pipefail

# Run on an actual amd64 or arm64 Linux Docker host. Supply an empty output
# directory; CI can bootstrap signed Ubuntu inputs, while offline reviewers
# can supply both the saved source directory and a frozen builder image ID.
recipe_dir="$(cd "$(dirname "$0")" && pwd)"
output="${AELIRA_EXPAT_OUTPUT:?set an empty absolute output directory}"
[[ "$output" = /* && -d "$output" && -z "$(ls -A "$output")" ]]
[[ "$(uname -s)" = Linux ]] || { echo 'Native Linux host required' >&2; exit 1; }
case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; *) echo 'Unsupported native host architecture' >&2; exit 1 ;; esac
[[ "${AELIRA_EXPAT_EXPECT_ARCH:-$arch}" = "$arch" ]] || { echo 'Runner architecture mismatch' >&2; exit 1; }
docker_arch="$(docker info --format '{{.Architecture}}')"
case "$docker_arch:$arch" in x86_64:amd64|amd64:amd64|aarch64:arm64|arm64:arm64) ;; *) echo 'Docker daemon is not native to runner' >&2; exit 1 ;; esac
source_dir="${AELIRA_EXPAT_SOURCE_INPUT:-$output/source-input}"
builder="${AELIRA_EXPAT_BUILDER_IMAGE:-}"
run_id="${GITHUB_RUN_ID:-$(date +%s)}-$$-$arch"
mkdir -p "$output/provenance"
if [[ -z "$builder" ]]; then
  [[ "$source_dir" = "$output/source-input" ]] || { echo 'Saved source requires a frozen builder image' >&2; exit 1; }
  mkdir -p "$source_dir"
  base='ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
  timeout 10m docker pull --platform "linux/$arch" "$base"
  [[ "$(docker image inspect --format '{{.Architecture}}' "$base")" = "$arch" ]]
  prepare_name="expat-prepare-$run_id"
  marker="$output/provenance/prepare-complete.txt"
  # The prepare container runs detached and its completion marker, not its
  # exit, is the success signal (issue #1035): a leftover child wedged
  # in-kernel on a degraded archive.ubuntu.com socket held an attached
  # docker run open after every prepare step had completed, the foreground
  # timeout fired at 30 minutes with exit 124, and the builder image was
  # never committed.
  prepare_started=$(date +%s)
  timeout 10m docker run --name "$prepare_name" --detach --platform "linux/$arch" \
    --cpus 4 --memory 8g --pids-limit 512 -e AELIRA_EXPAT_HOST_ARCH="$arch" \
    -v "$recipe_dir:/recipe:ro" -v "$source_dir:/source-input" \
    -v "$output/provenance:/provenance" "$base" /bin/bash /recipe/prepare-native.sh
  prepare_deadline=$(( prepare_started + 2100 ))
  prepare_polls=0
  while [[ ! -f "$marker" ]]; do
    now=$(date +%s)
    state="$(docker inspect -f '{{.State.Status}}' "$prepare_name")"
    if [[ "$state" != running ]]; then
      # The script can complete between the loop condition and this inspect;
      # the marker wins before any failure is declared.
      if [[ -f "$marker" ]]; then break; fi
      exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$prepare_name")"
      echo "expat-prepare: container ended as $state (exit $exit_code) without writing the completion marker" >&2
    elif (( now >= prepare_deadline )); then
      if [[ -f "$marker" ]]; then break; fi
      echo "expat-prepare: completion marker still absent 35 minutes after container start; container still running" >&2
    else
      prepare_polls=$(( prepare_polls + 1 ))
      if (( prepare_polls % 4 == 0 )); then
        # shellcheck disable=SC2012  # names are this harness's own; sorting by mtime is the point
        echo "expat-prepare: running for $(( now - prepare_started ))s; latest provenance file: $(ls -1t "$output/provenance" | head -n 1 || true)"
      fi
      sleep 15
      continue
    fi
    docker logs "$prepare_name" > "$output/provenance/prepare-console.log" 2>&1 || true
    tail -n 40 "$output/provenance/prepare-console.log" >&2 || true
    # shellcheck disable=SC2012  # names are this harness's own; sorting by mtime is the point
    echo "expat-prepare: newest provenance file: $(ls -1t "$output/provenance" | head -n 1 || true)" >&2
    timeout 2m docker rm -f "$prepare_name" >/dev/null 2>&1 || true
    exit 1
  done
  echo "expat-prepare: completion marker present after $(( $(date +%s) - prepare_started ))s; releasing the container"
  # The marker means every prepare step finished. Stop the container, but
  # tolerate a wedged child that ignores the stop: docker commit works on a
  # running container, and the filesystem is complete once the marker is
  # written. Removal is best effort for the same reason.
  timeout 3m docker stop --time 15 "$prepare_name" >/dev/null 2>&1 || true
  builder="$(docker commit "$prepare_name")"
  docker logs "$prepare_name" > "$output/provenance/prepare-console.log" 2>&1 || true
  timeout 2m docker rm -f "$prepare_name" >/dev/null 2>&1 || true
fi
[[ "$builder" = sha256:* ]]
[[ "$(docker image inspect --format '{{.Architecture}}' "$builder")" = "$arch" ]]
printf '%s\n' "$builder" > "$output/provenance/frozen-builder-id.txt"
(cd "$source_dir" && sha256sum -c "$recipe_dir/inputs.sha256") > "$output/provenance/source-manifest-check.log"
(cd "$recipe_dir" && sha256sum -c patches.sha256) > "$output/provenance/patch-manifest-check.log"
docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
  --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --cap-drop ALL --security-opt no-new-privileges \
  "$builder" /bin/bash -euo pipefail -c 'test "$(dpkg --print-architecture)" = "$1"; test "$(dpkg-query -W libexpat1 | cut -f2)" = 2.7.4-1ubuntu0.2' sh "$arch"
docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
  --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --cap-drop ALL --security-opt no-new-privileges "$builder" \
  python3 -c 'import json; from xml.parsers import expat; print(json.dumps(dict(expat.features), sort_keys=True))' \
  > "$output/baseline-features.json"

for build in a b; do
  mkdir "$output/build-$build"
  timeout 50m docker run --rm --platform "linux/$arch" \
    --user "$(id -u):$(id -g)" --network none --read-only \
    --cap-drop ALL --security-opt no-new-privileges \
    --cpus 4 --memory 8g --pids-limit 512 \
    --tmpfs /tmp:rw,nosuid,nodev,size=1g \
    -e HOME=/work -e AELIRA_EXPAT_PREINSTALLED_BUILD_DEPS=1 \
    -e AELIRA_EXPAT_HOST_ARCH="$arch" \
    -v "$recipe_dir:/recipe:ro" -v "$source_dir:/source-input:ro" \
    -v "$output/build-$build:/work" "$builder" \
    /bin/sh /recipe/build-native.sh > "$output/build-$build/console.log" 2>&1
done

for artifact in \
  'expat_2.7.4-1ubuntu0.2+aelira1.dsc' \
  'expat_2.7.4-1ubuntu0.2+aelira1.debian.tar.xz' \
  "libexpat1_2.7.4-1ubuntu0.2+aelira1_${arch}.deb"; do
  cmp "$output/build-a/$artifact" "$output/build-b/$artifact"
  sha256sum "$output/build-a/$artifact" >> "$output/reproducible-sha256.txt"
done
for build in a b; do
  grep -Eq '^# TOTAL: 2$' "$output/build-$build/upstream-tests.log"
  grep -Eq '^# PASS:  2$' "$output/build-$build/upstream-tests.log"
  grep -Eq '^# FAIL:  0$' "$output/build-$build/upstream-tests.log"
  grep -Eq '^# ERROR: 0$' "$output/build-$build/upstream-tests.log"
done

deb="$output/build-a/libexpat1_2.7.4-1ubuntu0.2+aelira1_${arch}.deb"
mkdir "$output/extracted"
docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
  --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$deb:/candidate.deb:ro" -v "$output/extracted:/extracted" \
  "$builder" /bin/bash -euo pipefail -c 'dpkg-deb -x /candidate.deb /extracted; dpkg-deb -f /candidate.deb Package; dpkg-deb -f /candidate.deb Version; dpkg-deb -f /candidate.deb Architecture' \
  > "$output/package-metadata.txt"
grep -Fx 'libexpat1' "$output/package-metadata.txt"
grep -Fx '2.7.4-1ubuntu0.2+aelira1' "$output/package-metadata.txt"
grep -Fx "$arch" "$output/package-metadata.txt"
triplet="$(case "$arch" in arm64) echo aarch64-linux-gnu ;; amd64) echo x86_64-linux-gnu ;; esac)"
lib="$output/extracted/usr/lib/$triplet/libexpat.so.1.11.2"
[[ -f "$lib" ]]
lib_hash="$(sha256sum "$lib" | cut -d' ' -f1)"
printf '%s  %s\n' "$lib_hash" 'libexpat.so.1.11.2' > "$output/library-sha256.txt"
wide="$output/extracted/usr/lib/$triplet/libexpatw.so.1.11.2"
[[ -f "$wide" ]]
wide_hash="$(sha256sum "$wide" | cut -d' ' -f1)"
printf '%s  %s\n' "$wide_hash" 'libexpatw.so.1.11.2' >> "$output/library-sha256.txt"
docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
  --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$output/extracted:/candidate:ro" -v "$output:/evidence" \
  "$builder" /bin/bash -euo pipefail -c '
    triplet="$1"
    for name in libexpat libexpatw; do
      old="/usr/lib/$triplet/$name.so.1.11.2"
      new="/candidate/usr/lib/$triplet/$name.so.1.11.2"
      test -f "$old" && test -f "$new"
      readelf -d "$old" | grep SONAME > "/evidence/$name.original-soname.txt"
      readelf -d "$new" | grep SONAME > "/evidence/$name.candidate-soname.txt"
      diff -u "/evidence/$name.original-soname.txt" "/evidence/$name.candidate-soname.txt"
      nm -D --defined-only "$old" | awk "{print \$3}" | sort -u > "/evidence/$name.original-symbols.txt"
      nm -D --defined-only "$new" | awk "{print \$3}" | sort -u > "/evidence/$name.candidate-symbols.txt"
      test -s "/evidence/$name.original-symbols.txt"
      test -s "/evidence/$name.candidate-symbols.txt"
      diff -u "/evidence/$name.original-symbols.txt" "/evidence/$name.candidate-symbols.txt"
    done
  ' sh "$triplet" > "$output/abi-parity.log" 2>&1

install_name="expat-install-$run_id"
timeout 5m docker run --name "$install_name" --platform "linux/$arch" \
  --network none --cap-drop ALL --security-opt no-new-privileges \
  --cpus 2 --memory 2g --pids-limit 128 \
  -v "$deb:/candidate.deb:ro" -v "$recipe_dir:/recipe:ro" "$builder" \
  python3 /recipe/install-identity.py /candidate.deb "$arch" "$lib_hash" "$wide_hash" \
  > "$output/package-install.log" 2>&1
installed_image="$(docker commit "$install_name")"
printf '%s\n' "$installed_image" > "$output/installed-image-id.txt"
timeout 3m docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
  --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --cpus 2 --memory 2g --pids-limit 128 --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  -e AELIRA_EXPAT_HOST_ARCH="$arch" -e AELIRA_EXPAT_EXPECTED_LIB_SHA256="$lib_hash" \
  -e AELIRA_EXPAT_EXPECTED_WIDE_SHA256="$wide_hash" \
  -v "$recipe_dir:/recipe:ro" "$installed_image" \
  python3 /recipe/consumer-probe.py > "$output/installed-consumer.json"
python3 - "$output/baseline-features.json" "$output/installed-consumer.json" <<'PY'
import json
import sys
from pathlib import Path

baseline = json.loads(Path(sys.argv[1]).read_text())
candidate = json.loads(Path(sys.argv[2]).read_text())["features"]
assert baseline == candidate, (baseline, candidate)
PY
printf 'native_expat_acceptance=pass architecture=%s\n' "$arch"
