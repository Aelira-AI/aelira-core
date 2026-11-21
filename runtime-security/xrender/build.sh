#!/usr/bin/env bash
# Native preparation followed by two isolated offline package builds.
set -euo pipefail
recipe=$(cd "$(dirname "$0")" && pwd)
output=${1:?usage: build.sh NEW_OUTPUT_DIRECTORY}
test ! -e "$output" && test ! -L "$output"
mkdir "$output"
output=$(cd "$output" && pwd)
phase=architecture
trap 'result=$?; printf "{\"schema\":\"aelira-xrender-build-status-v1\",\"phase\":\"%s\",\"exit\":%s}\n" "$phase" "$result" > "$output/build-status.json"' EXIT
test "$(uname -s)" = Linux
test "$(id -u)" != 0
case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=amd64 ;; *) exit 1 ;; esac
test "${AELIRA_XRENDER_EXPECT_ARCH:-$arch}" = "$arch"
case "$(docker info --format '{{.Architecture}}'):$arch" in
  aarch64:arm64|arm64:arm64|x86_64:amd64|amd64:amd64) ;;
  *) echo 'Native Docker daemon required' >&2; exit 1 ;;
esac
base='ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
mkdir "$output/provenance" "$output/source-input" "$output/frozen-recipe"
for file in README.md build.sh ci-native.sh prepare-native.sh build-in-container.sh \
            verify-provenance.py inputs.sha256 patch.sha256 upstream-0564bce.patch \
            changelog-entry.txt generate-control.py control-template.c \
            test-installed.sh xrender-client.c; do
  test -f "$recipe/$file" && test ! -L "$recipe/$file"
  cp "$recipe/$file" "$output/frozen-recipe/"
done
recipe="$output/frozen-recipe"
chmod 0755 "$recipe"
(cd "$recipe" && sha256sum *) > "$output/provenance/recipe-sha256.txt"
builder=${AELIRA_XRENDER_BUILDER_IMAGE:-}
if test -z "$builder"; then
  phase=base-pull
  timeout 10m docker pull --platform "linux/$arch" "$base" > "$output/provenance/base-pull-private.log" 2>&1
  test "$(docker image inspect --format '{{.Architecture}}' "$base")" = "$arch"
  phase=online-preparation
  name="aelira-xrender-prepare-$arch-$$"
  marker="$output/provenance/prepare-complete.txt"
  # The prepare container runs detached and its completion marker, not its
  # exit, is the success signal (issue #1037): the old attached run on a
  # degraded archive mirror held the job open with zero console output
  # until the workflow cancelled it at the job timeout with no verdict.
  # The marker wall below fires well inside the job cap so the failure
  # names the stalled step instead.
  prepare_started=$(date +%s)
  timeout 10m docker run --name "$name" --detach --platform "linux/$arch" --security-opt no-new-privileges \
    --cpus 4 --memory 4g --pids-limit 256 -e AELIRA_XRENDER_HOST_ARCH="$arch" \
    -v "$recipe:/recipe:ro" -v "$output/source-input:/source-input" \
    -v "$output/provenance:/provenance" "$base" bash /recipe/prepare-native.sh
  prepare_deadline=$(( prepare_started + 2100 ))
  prepare_polls=0
  while [[ ! -f "$marker" ]]; do
    now=$(date +%s)
    state=$(docker inspect -f '{{.State.Status}}' "$name")
    if [[ "$state" != running ]]; then
      # The script can complete between the loop condition and this inspect;
      # the marker wins before any failure is declared.
      if [[ -f "$marker" ]]; then break; fi
      exit_code=$(docker inspect -f '{{.State.ExitCode}}' "$name")
      echo "xrender-prepare: container ended as $state (exit $exit_code) without writing the completion marker" >&2
    elif (( now >= prepare_deadline )); then
      if [[ -f "$marker" ]]; then break; fi
      echo "xrender-prepare: completion marker still absent 35 minutes after container start; container still running" >&2
    else
      prepare_polls=$(( prepare_polls + 1 ))
      if (( prepare_polls % 4 == 0 )); then
        # shellcheck disable=SC2012  # names are this harness's own; sorting by mtime is the point
        echo "xrender-prepare: running for $(( now - prepare_started ))s; latest provenance file: $(ls -1t "$output/provenance" | head -n 1 || true)"
      fi
      sleep 15
      continue
    fi
    docker logs "$name" > "$output/provenance/prepare-private.log" 2>&1 || true
    tail -n 40 "$output/provenance/prepare-private.log" >&2 || true
    # shellcheck disable=SC2012  # names are this harness's own; sorting by mtime is the point
    echo "xrender-prepare: newest provenance file: $(ls -1t "$output/provenance" | head -n 1 || true)" >&2
    timeout 2m docker rm -f "$name" >/dev/null 2>&1 || true
    exit 1
  done
  echo "xrender-prepare: completion marker present after $(( $(date +%s) - prepare_started ))s; releasing the container"
  # The marker means every prepare step finished. Stop the container, but
  # tolerate a wedged child that ignores the stop: docker commit works on a
  # running container, and the filesystem is complete once the marker is
  # written. Removal is best effort for the same reason.
  timeout 3m docker stop --time 15 "$name" >/dev/null 2>&1 || true
  builder=$(docker commit "$name")
  docker logs "$name" > "$output/provenance/prepare-private.log" 2>&1 || true
  timeout 2m docker rm -f "$name" >/dev/null 2>&1 || true
else
  phase=frozen-input-capture
  [[ "$builder" =~ ^sha256:[a-f0-9]{64}$ ]]
  source_input=${AELIRA_XRENDER_SOURCE_INPUT:?Frozen builder requires source input}
  provenance_input=${AELIRA_XRENDER_PROVENANCE_INPUT:?Frozen builder requires provenance}
  test "$(cat "$provenance_input/base-image.txt")" = "$base"
  test "$(cat "$provenance_input/builder-id.txt")" = "$builder"
  cp "$source_input/"libxrender_0.9.12* "$output/source-input/"
  for file in apt-lists toolchain-debs ubuntu-archive-keyring.gpg apt-index-sha256.txt \
              toolchain-debs.sha256 toolchain-delta.tsv base-packages.tsv toolchain-packages.tsv; do
    cp -r "$provenance_input/$file" "$output/provenance/"
  done
fi
test "$(docker image inspect --format '{{.Architecture}}' "$builder")" = "$arch"
printf '%s\n' "$base" > "$output/provenance/base-image.txt"
printf '%s\n' "$builder" > "$output/provenance/builder-id.txt"
if test "${AELIRA_XRENDER_SAVE_BUILDER:-1}" = 1; then
  docker save "$builder" -o "$output/provenance/builder.tar"
fi
for attempt in a b; do
  phase="offline-build-$attempt"
  mkdir "$output/build-$attempt"
  docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --cpus 4 --memory 4g --pids-limit 256 --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    -e HOME=/work -e AELIRA_XRENDER_HOST_ARCH="$arch" \
    -v "$recipe:/recipe:ro" -v "$output/source-input:/source-input:ro" \
    -v "$output/provenance:/provenance:ro" -v "$output/build-$attempt:/work" \
    "$builder" bash /recipe/build-in-container.sh > "$output/build-$attempt/console-private.log" 2>&1
done
phase=reproducibility
for artifact in "$output/build-a/"*.deb \
                "$output/build-a/libxrender_0.9.12-1build1+aelira1.dsc" \
                "$output/build-a/libxrender_0.9.12-1build1+aelira1.diff.gz"; do
  cmp "$artifact" "$output/build-b/${artifact##*/}"
  (cd "$output/build-a" && sha256sum "${artifact##*/}") >> "$output/reproducible-sha256.txt"
done
phase=installed-control
mkdir "$output/installed"
installed_name="aelira-xrender-installed-$arch-$$"
set +e
docker run --name "$installed_name" --platform "linux/$arch" --network none --cap-drop ALL \
  --security-opt no-new-privileges --cpus 2 --memory 2g --pids-limit 128 \
  -e AELIRA_XRENDER_HOST_ARCH="$arch" -v "$recipe:/recipe:ro" \
  -v "$output/build-a/libxrender1_0.9.12-1build1+aelira1_$arch.deb:/candidate.deb:ro" \
  "$builder" bash /recipe/test-installed.sh \
  > "$output/installed-private.log" 2>&1
installed_status=$?
set -e
# Keep container-root test writes inside the disposable container. The host
# copies only test data after execution; cap-drop ALL remains enforced.
docker cp "$installed_name:/evidence/." "$output/installed/"
test "$installed_status" = 0
docker rm "$installed_name" > /dev/null
grep -Fx "installed_xrender_acceptance=pass architecture=$arch version=1:0.9.12-1build1+aelira1" \
  "$output/installed-private.log"
phase=complete
printf 'native_xrender_reproducible=pass architecture=%s builder=%s\n' "$arch" "$builder"
