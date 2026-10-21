#!/usr/bin/env bash
# Native online preparation, then two clean network-disabled builds.
set -euo pipefail
recipe=$(cd "$(dirname "$0")" && pwd)
output=${1:?usage: build.sh EMPTY_OUTPUT_DIRECTORY ACCEPTED_TIFF_BUILD_A_DIRECTORY}
tiff=${2:?usage: build.sh EMPTY_OUTPUT_DIRECTORY ACCEPTED_TIFF_BUILD_A_DIRECTORY}
[[ "$(uname -s)" = Linux ]] || { echo 'Native Linux required' >&2; exit 1; }
case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=amd64 ;; *) exit 1 ;; esac
case "$(docker info --format '{{.Architecture}}'):$arch" in
  aarch64:arm64|arm64:arm64|x86_64:amd64|amd64:amd64) ;;
  *) echo 'Docker daemon is not native to host' >&2; exit 1 ;;
esac
[[ -d "$tiff" ]] || { echo 'Accepted TIFF build-a directory missing' >&2; exit 1; }
mkdir -p "$output"
output=$(cd "$output" && pwd)
[[ -z "$(ls -A "$output")" ]] || { echo 'Output directory must be empty' >&2; exit 1; }
mkdir -p "$output/input/tiff" "$output/provenance"
trap 'rc=$?; trap - EXIT; printf "%s\n" "$rc" > "$output/build-exit.txt"; exit "$rc"' EXIT
printf '%s\n' "$(uname -m)" > "$output/host-machine.txt"
docker info --format '{{.Architecture}}' > "$output/daemon-architecture.txt"
repo=$(/usr/bin/git -C "$recipe" rev-parse --show-toplevel)
python3 "$recipe/recipe-lock.py" snapshot "$recipe" "$output/recipe" "$repo"
recipe="$output/recipe"
for package in libtiff6 libtiffxx6 libtiff-dev; do
  file="${package}_4.7.2-0aelira1_${arch}.deb"
  cp "$tiff/$file" "$output/input/tiff/$file"
done
(cd "$output/input/tiff" && sha256sum ./*.deb > SHA256SUMS)
python3 "$recipe/verify-tiff-input.py" "$output/input/tiff" "$arch"
base='ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
docker pull --platform "linux/$arch" "$base" > "$output/provenance/base-pull.log"
[[ "$(docker image inspect --format '{{.Architecture}}' "$base")" = "$arch" ]]
name="aelira-pillow-prepare-$arch-$$"
docker run --name "$name" --platform "linux/$arch" \
  --cpus 4 --memory 8g --pids-limit 512 --security-opt no-new-privileges \
  -e AELIRA_PILLOW_HOST_ARCH="$arch" \
  -v "$recipe:/recipe:ro" -v "$output/input:/input" \
  -v "$output/provenance:/provenance" \
  "$base" bash /recipe/prepare-native.sh \
  > "$output/provenance/prepare-console.log" 2>&1
builder=$(docker commit "$name")
docker rm "$name" > /dev/null
[[ "$builder" = sha256:* ]]
printf '%s\n' "$base" > "$output/provenance/base-image.txt"
printf '%s\n' "$builder" > "$output/provenance/frozen-builder-id.txt"
docker image inspect "$builder" > "$output/provenance/frozen-builder.inspect.json"
docker save "$builder" -o "$output/provenance/frozen-builder.tar"
sha256sum "$output/provenance/frozen-builder.tar" > "$output/provenance/frozen-builder.tar.sha256"
python3 "$recipe/proof.py" inventory "$output" "$arch"
for run in a b; do
  python3 "$recipe/proof.py" check-inventory "$output" "$arch"
  python3 "$recipe/recipe-lock.py" verify "$recipe"
  mkdir "$output/build-$run"
  docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
    --network none --read-only \
    --cap-drop ALL --security-opt no-new-privileges \
    --cpus 4 --memory 8g --pids-limit 512 \
    --tmpfs /tmp:rw,nosuid,nodev,size=1g \
    -e HOME=/work -e AELIRA_PILLOW_HOST_ARCH="$arch" \
    -v "$recipe:/recipe:ro" -v "$output/input:/input:ro" \
    -v "$output/provenance:/provenance:ro" -v "$output/build-$run:/work" \
    "$builder" bash /recipe/build-in-container.sh \
    > "$output/build-$run/console.log" 2>&1
done
python3 "$recipe/proof.py" check-inventory "$output" "$arch"
python3 "$recipe/recipe-lock.py" verify "$recipe"
wheel_a=("$output"/build-a/wheels/pillow-13.0.0-*.whl)
wheel_b=("$output"/build-b/wheels/pillow-13.0.0-*.whl)
[[ ${#wheel_a[@]} = 1 && ${#wheel_b[@]} = 1 ]]
cmp "${wheel_a[0]}" "${wheel_b[0]}"
sha256sum "${wheel_a[0]}" "${wheel_b[0]}" > "$output/reproducible-sha256.txt"
printf '0\n' > "$output/build-exit.txt"
python3 "$recipe/proof.py" build "$output" "$arch"
printf 'native_pillow_reproducible=pass architecture=%s builder=%s\n' "$arch" "$builder"
