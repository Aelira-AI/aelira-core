#!/usr/bin/env bash
# Native source packages and real installed-client acceptance.
set -euo pipefail
recipe=$(cd "$(dirname "$0")" && pwd)
output=${1:?usage: build.sh NEW_OUTPUT_DIRECTORY}
test ! -e "$output" && test ! -L "$output"
mkdir "$output"
output=$(cd "$output" && pwd)
phase=architecture
trap 'result=$?; printf "{\"schema\":\"aelira-xdg-build-status-v1\",\"phase\":\"%s\",\"exit\":%s}\n" "$phase" "$result" > "$output/build-status.json"' EXIT
test "$(uname -s)" = Linux
test "$(id -u)" != 0
case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=amd64 ;; *) exit 1 ;; esac
test "${AELIRA_XDG_EXPECT_ARCH:-$arch}" = "$arch"
case "$(docker info --format '{{.Architecture}}'):$arch" in
  aarch64:arm64|arm64:arm64|x86_64:amd64|amd64:amd64) ;; *) exit 1 ;;
esac
source_base='ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
client_base='debian@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f'
mkdir "$output/source-input" "$output/source-provenance" "$output/client-provenance" "$output/frozen-recipe"
for file in README.md build.sh prepare-native.sh prepare-client.sh build-in-container.sh \
  run-installed-client.sh build-source.py test-source.py test-mailto-native.sh \
  test-installed-client.py verify-provenance.py verify-client-provenance.py inputs.sha256 \
  changelog-entry.txt upstream-5f3f563.patch upstream-700e589.patch \
  ubuntu-1.2.1-mailto-backport.patch ubuntu-frontend-compatibility.patch; do
  test -f "$recipe/$file" && test ! -L "$recipe/$file"
  cp "$recipe/$file" "$output/frozen-recipe/"
done
recipe="$output/frozen-recipe"
(cd "$recipe" && sha256sum *) > "$output/recipe-sha256.txt"
source_builder=${AELIRA_XDG_SOURCE_BUILDER_IMAGE:-}
client_builder=${AELIRA_XDG_CLIENT_BUILDER_IMAGE:-}
for role in source client; do
  phase="$role-preparation"
  if test "$role" = source; then
    base=$source_base; builder=$source_builder; preparer=prepare-native.sh
  else
    base=$client_base; builder=$client_builder; preparer=prepare-client.sh
  fi
  provenance="$output/$role-provenance"
  if test -z "$builder"; then
    docker pull --platform "linux/$arch" "$base" > "$provenance/base-pull-private.log" 2>&1
    test "$(docker image inspect --format '{{.Architecture}}' "$base")" = "$arch"
    name="aelira-xdg-$role-prepare-$arch-$$"
    docker run --name "$name" --platform "linux/$arch" --security-opt no-new-privileges \
      --cpus 4 --memory 4g --pids-limit 256 -e AELIRA_XDG_HOST_ARCH="$arch" \
      -v "$recipe:/recipe:ro" -v "$output/source-input:/source-input" \
      -v "$provenance:/provenance" "$base" bash "/recipe/$preparer" \
      > "$provenance/prepare-private.log" 2>&1
    builder=$(docker commit "$name")
    docker rm "$name" > /dev/null
  else
    [[ "$builder" =~ ^sha256:[a-f0-9]{64}$ ]]
    if test "$role" = source; then
      inputs=${AELIRA_XDG_SOURCE_INPUT:?Frozen source inputs required}
      recorded=${AELIRA_XDG_SOURCE_PROVENANCE:?Frozen source provenance required}
      for file in xdg-utils_1.2.1.orig.tar.gz xdg-utils_1.2.1-2ubuntu2.debian.tar.xz xdg-utils_1.2.1-2ubuntu2.dsc; do
        cp "$inputs/$file" "$output/source-input/"
      done
      keyring=ubuntu-archive-keyring.gpg
    else
      recorded=${AELIRA_XDG_CLIENT_PROVENANCE:?Frozen client provenance required}
      keyring=debian-archive-keyring.gpg
    fi
    for file in apt-lists toolchain-debs "$keyring" apt-index-sha256.txt \
      toolchain-debs.sha256 base-packages.tsv toolchain-packages.tsv toolchain-delta.tsv; do
      cp -r "$recorded/$file" "$provenance/"
    done
  fi
  test "$(docker image inspect --format '{{.Architecture}}' "$builder")" = "$arch"
  printf '%s\n' "$base" > "$provenance/base-image.txt"
  printf '%s\n' "$builder" > "$provenance/builder-id.txt"
  docker save "$builder" -o "$provenance/builder.tar"
  if test "$role" = source; then source_builder=$builder; else client_builder=$builder; fi
done
phase=signed-client-inputs
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --memory 1g --pids-limit 64 --tmpfs /tmp:rw,nosuid,nodev,size=32m \
  -e HOME=/tmp -v "$recipe:/recipe:ro" -v "$output/client-provenance:/provenance:ro" \
  "$source_builder" python3 -B /recipe/verify-client-provenance.py > "$output/client-provenance/signed-chain.json"
for attempt in a b; do
  phase="offline-build-$attempt"
  mkdir "$output/build-$attempt"
  docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --cpus 4 --memory 4g --pids-limit 256 --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    -e HOME=/work -e AELIRA_XDG_HOST_ARCH="$arch" -v "$recipe:/recipe:ro" \
    -v "$output/source-input:/source-input:ro" -v "$output/source-provenance:/provenance:ro" \
    -v "$output/build-$attempt:/work:rw" "$source_builder" bash /recipe/build-in-container.sh \
    > "$output/build-$attempt/console-private.log" 2>&1
done
phase=reproducibility
for name in xdg-utils_1.2.1.orig.tar.gz xdg-utils_1.2.1-2ubuntu2+aelira2.dsc \
  xdg-utils_1.2.1-2ubuntu2+aelira2.debian.tar.xz xdg-utils_1.2.1-2ubuntu2+aelira2_all.deb; do
  cmp "$output/build-a/packages/$name" "$output/build-b/packages/$name"
done
phase=installed-client
name="aelira-xdg-client-installed-$arch-$$"
docker run --name "$name" --platform "linux/$arch" --network none --security-opt no-new-privileges \
  -v "$output/build-a/packages/xdg-utils_1.2.1-2ubuntu2+aelira2_all.deb:/candidate.deb:ro" \
  "$client_builder" bash -c 'set -eu; dpkg -i /candidate.deb; sha256sum /usr/bin/xdg-email' \
  > "$output/client-install-private.log" 2>&1
installed=$(docker commit "$name")
docker rm "$name" > /dev/null
printf '%s\n' "$installed" > "$output/installed-client-id.txt"
mkdir "$output/installed" "$output/identity-fixture"
for file in passwd group; do
  docker run --rm --network none --read-only "$client_builder" cat "/etc/$file" > "$output/identity-fixture/$file"
done
if ! awk -F: -v uid="$(id -u)" '$3==uid {found=1} END {exit !found}' "$output/identity-fixture/passwd"; then
  printf 'aelira-test:x:%s:%s:Offline fixture:/work:/bin/sh\n' "$(id -u)" "$(id -g)" >> "$output/identity-fixture/passwd"
fi
if ! awk -F: -v gid="$(id -g)" '$3==gid {found=1} END {exit !found}' "$output/identity-fixture/group"; then
  printf 'aelira-test:x:%s:\n' "$(id -g)" >> "$output/identity-fixture/group"
fi
docker run --rm --platform "linux/$arch" --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user "$(id -u):$(id -g)" --cpus 2 --memory 2g \
  --pids-limit 256 --tmpfs /tmp:rw,nosuid,nodev,size=512m -e AELIRA_XDG_HOST_ARCH="$arch" \
  -v "$recipe:/recipe:ro" -v "$output/installed:/work:rw" \
  -v "$output/identity-fixture/passwd:/etc/passwd:ro" -v "$output/identity-fixture/group:/etc/group:ro" \
  "$installed" bash /recipe/run-installed-client.sh > "$output/client-console-private.log" 2>&1
phase=installed-image
docker save "$installed" -o "$output/installed-client.tar"
phase=complete
printf 'native_xdg_acceptance=pass architecture=%s version=1.2.1-2ubuntu2+aelira2\n' "$arch"
