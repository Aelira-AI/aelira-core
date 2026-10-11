#!/usr/bin/env bash
# Native arm64/amd64, online preparation followed by two offline builds.
set -euo pipefail
recipe=$(cd "$(dirname "$0")" && pwd)
output=${1:?usage: build.sh EMPTY_OUTPUT_DIRECTORY}
mkdir -p "$output"
output=$(cd "$output" && pwd)
[[ -z "$(ls -A "$output")" ]] || { echo 'Output directory must be empty' >&2; exit 1; }
[[ "$(uname -s)" = Linux ]] || { echo 'Native Linux host required' >&2; exit 1; }
case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=amd64 ;; *) exit 1 ;; esac
[[ "${AELIRA_TIFF_EXPECT_ARCH:-$arch}" = "$arch" ]] || { echo 'Runner architecture mismatch' >&2; exit 1; }
case "$(docker info --format '{{.Architecture}}'):$arch" in
  aarch64:arm64|arm64:arm64|x86_64:amd64|amd64:amd64) ;;
  *) echo 'Docker daemon is not native to runner' >&2; exit 1 ;;
esac
base='ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
source_dir=${AELIRA_TIFF_SOURCE_INPUT:-$output/source-input}
builder=${AELIRA_TIFF_BUILDER_IMAGE:-}
mkdir -p "$output/provenance"
provenance_input=${AELIRA_TIFF_PROVENANCE_INPUT:-$output/provenance}
if [[ -z "$builder" ]]; then
  [[ "$source_dir" = "$output/source-input" ]] || { echo 'External inputs require a frozen builder' >&2; exit 1; }
  mkdir -p "$source_dir"
  docker pull --platform "linux/$arch" "$base" > "$output/provenance/base-pull.log"
  [[ "$(docker image inspect --format '{{.Architecture}}' "$base")" = "$arch" ]]
  name="aelira-tiff-prepare-$arch-$$"
  docker run --name "$name" --platform "linux/$arch" \
    --cpus 4 --memory 8g --pids-limit 512 --security-opt no-new-privileges \
    -e AELIRA_TIFF_HOST_ARCH="$arch" -v "$recipe:/recipe:ro" \
    -v "$source_dir:/source-input" -v "$output/provenance:/provenance" \
    "$base" bash /recipe/prepare-native.sh \
    > "$output/provenance/prepare-console.log" 2>&1
  builder=$(docker commit "$name")
  docker rm "$name" > /dev/null
  if [[ "${AELIRA_TIFF_SAVE_BUILDER:-1}" = 1 ]]; then
    docker save "$builder" -o "$output/provenance/frozen-builder.tar"
    sha256sum "$output/provenance/frozen-builder.tar" \
      > "$output/provenance/frozen-builder.tar.sha256"
  fi
else
  [[ -d "$source_dir" && -f "$provenance_input/source-input.sha256" &&
     -f "$provenance_input/ubuntu-archive-keyring.gpg" ]] ||
    { echo 'Frozen builder requires saved source and provenance inputs' >&2; exit 1; }
fi
[[ "$builder" = sha256:* ]]
[[ "$(docker image inspect --format '{{.Architecture}}' "$builder")" = "$arch" ]]
printf '%s\n' "$base" > "$output/provenance/base-image.txt"
printf '%s\n' "$builder" > "$output/provenance/frozen-builder-id.txt"
docker image inspect "$builder" > "$output/provenance/frozen-builder.inspect.json"
for build in a b; do
  mkdir "$output/build-$build"
  docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --cpus 4 --memory 8g --pids-limit 512 --tmpfs /tmp:rw,nosuid,nodev,size=1g \
    -e HOME=/work -e AELIRA_TIFF_HOST_ARCH="$arch" \
    -v "$recipe:/recipe:ro" -v "$source_dir:/source-input:ro" \
    -v "$provenance_input:/provenance:ro" -v "$output/build-$build:/work" \
    "$builder" bash /recipe/build-in-container.sh \
    > "$output/build-$build/console.log" 2>&1
done
for artifact in "$output/build-a"/*.deb "$output/build-a"/*.dsc "$output/build-a"/*.debian.tar.xz; do
  cmp "$artifact" "$output/build-b/${artifact##*/}"
  sha256sum "$artifact" >> "$output/reproducible-sha256.txt"
done
for build in a b; do
  grep -Eq '^# TOTAL: 109$' "$output/build-$build/build.log"
  grep -Eq '^# PASS:  109$' "$output/build-$build/build.log"
  grep -Eq '^# SKIP:  0$' "$output/build-$build/build.log"
  grep -Eq '^# FAIL:  0$' "$output/build-$build/build.log"
  grep -Eq '^# ERROR: 0$' "$output/build-$build/build.log"
done
docker run --rm --platform "linux/$arch" --network none \
  --security-opt no-new-privileges \
  --cpus 2 --memory 2g --pids-limit 128 \
  -e AELIRA_TIFF_HOST_ARCH="$arch" \
  -v "$recipe:/recipe:ro" -v "$output/build-a:/packages:ro" \
  "$builder" bash /recipe/test-installed.sh \
  > "$output/build-a/installed-console.log" 2>&1
grep -Fx "installed_tiff_acceptance=pass architecture=$arch version=4.7.2-0aelira1" \
  "$output/build-a/installed-console.log"
printf 'native_tiff_reproducible=pass architecture=%s builder=%s\n' "$arch" "$builder"
