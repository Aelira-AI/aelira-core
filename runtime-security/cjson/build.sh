#!/usr/bin/env bash
# Native online preparation, followed by two clean offline package builds.
set -euo pipefail
recipe=$(cd "$(dirname "$0")" && pwd)
output=${1:?usage: build.sh NEW_OUTPUT_DIRECTORY}
test ! -e "$output" && test ! -L "$output"
mkdir "$output"
output=$(cd "$output" && pwd)
phase=architecture
trap 'result=$?; printf "{\"schema\":\"aelira-cjson-build-status-v1\",\"phase\":\"%s\",\"exit\":%s}\n" "$phase" "$result" > "$output/build-status.json"' EXIT
test "$(uname -s)" = Linux
test "$(id -u)" != 0
case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=amd64 ;; *) exit 1 ;; esac
test "${AELIRA_CJSON_EXPECT_ARCH:-$arch}" = "$arch"
case "$(docker info --format '{{.Architecture}}'):$arch" in
  aarch64:arm64|arm64:arm64|x86_64:amd64|amd64:amd64) ;;
  *) echo 'Native Docker daemon required' >&2; exit 1 ;;
esac
base='ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
mkdir "$output/provenance" "$output/source-input"
mkdir "$output/frozen-recipe"
for file in README.md build.sh build-in-container.sh prepare-native.sh test-installed.sh \
            build-source.py test-source.py verify-ubuntu-source.py test-native.c \
            changelog-entry.txt ubuntu-1.7.19-security-backport.patch; do
  test -f "$recipe/$file" && test ! -L "$recipe/$file"
  cp "$recipe/$file" "$output/frozen-recipe/"
done
recipe="$output/frozen-recipe"
chmod 0755 "$recipe"
(cd "$recipe" && sha256sum *) > "$output/provenance/recipe-sha256.txt"
builder=${AELIRA_CJSON_BUILDER_IMAGE:-}
if test -z "$builder"; then
  phase=base-pull
  docker pull --platform "linux/$arch" "$base" > "$output/provenance/base-pull-private.log" 2>&1
  test "$(docker image inspect --format '{{.Architecture}}' "$base")" = "$arch"
  phase=online-preparation
  name="aelira-cjson-prepare-$arch-$$"
  docker run --name "$name" --platform "linux/$arch" --security-opt no-new-privileges \
    --cpus 4 --memory 4g --pids-limit 256 -e AELIRA_CJSON_HOST_ARCH="$arch" \
    -v "$recipe:/recipe:ro" -v "$output/source-input:/source-input" \
    -v "$output/provenance:/provenance" "$base" bash /recipe/prepare-native.sh \
    > "$output/provenance/prepare-private.log" 2>&1
  builder=$(docker commit "$name")
  docker rm "$name" > /dev/null
else
  phase=frozen-input-capture
  [[ "$builder" =~ ^sha256:[a-f0-9]{64}$ ]]
  source_input=${AELIRA_CJSON_SOURCE_INPUT:?Frozen builder requires source input}
  provenance_input=${AELIRA_CJSON_PROVENANCE_INPUT:?Frozen builder requires provenance}
  test "$(cat "$provenance_input/base-image.txt")" = "$base"
  test "$(cat "$provenance_input/builder-id.txt")" = "$builder"
  cp "$source_input/cjson_1.7.19-2.dsc" "$source_input/cjson_1.7.19.orig.tar.gz" \
    "$source_input/cjson_1.7.19-2.debian.tar.xz" "$output/source-input/"
  cp -r "$provenance_input/apt-lists" "$output/provenance/"
  cp "$provenance_input/toolchain-packages.tsv" "$provenance_input/ubuntu-archive-keyring.gpg" \
    "$output/provenance/"
fi
test "$(docker image inspect --format '{{.Architecture}}' "$builder")" = "$arch"
printf '%s\n' "$base" > "$output/provenance/base-image.txt"
printf '%s\n' "$builder" > "$output/provenance/builder-id.txt"
if test "${AELIRA_CJSON_SAVE_BUILDER:-1}" = 1; then
  docker save "$builder" -o "$output/provenance/builder.tar"
  sha256sum "$output/provenance/builder.tar" > "$output/provenance/builder-tar.sha256"
fi
for attempt in a b; do
  phase="offline-build-$attempt"
  mkdir "$output/build-$attempt"
  docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --cpus 4 --memory 4g --pids-limit 256 --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    -e HOME=/work -e AELIRA_CJSON_HOST_ARCH="$arch" \
    -v "$recipe:/recipe:ro" -v "$output/source-input:/source-input:ro" \
    -v "$output/provenance:/provenance:ro" -v "$output/build-$attempt:/work" \
    "$builder" bash /recipe/build-in-container.sh > "$output/build-$attempt/console-private.log" 2>&1
done
phase=reproducibility
for artifact in "$output/build-a/artifacts/"*.deb "$output/build-a/artifacts/"*.dsc \
                "$output/build-a/artifacts/"*.debian.tar.xz; do
  cmp "$artifact" "$output/build-b/artifacts/${artifact##*/}"
  (cd "$output/build-a/artifacts" && sha256sum "${artifact##*/}") \
    >> "$output/reproducible-sha256.txt"
done
phase=installed-control
docker run --rm --platform "linux/$arch" --network none --cap-drop ALL \
  --security-opt no-new-privileges --cpus 2 --memory 2g --pids-limit 128 \
  -e AELIRA_CJSON_HOST_ARCH="$arch" -v "$recipe:/recipe:ro" \
  -v "$output/build-a/artifacts:/packages:ro" -v "$output/build-a:/baseline:ro" \
  "$builder" bash /recipe/test-installed.sh \
  > "$output/installed-private.log" 2>&1
grep -Fx "installed_cjson_acceptance=pass architecture=$arch version=1.7.19-2+aelira3" \
  "$output/installed-private.log"
phase=complete
printf 'native_cjson_reproducible=pass architecture=%s builder=%s\n' "$arch" "$builder"
