#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 1 ]]; then
  echo "usage: $0 PREPARED_ABSOLUTE_SCRATCH" >&2
  exit 2
fi
scratch=$1
[[ "$scratch" = /* && -f "$scratch/frozen/frozen.sha256" ]] || { echo "missing prepared frozen inputs" >&2; exit 2; }
[[ "$(id -u)" != 0 ]] || { echo "native host root is refused" >&2; exit 2; }
recipe=$(cd "$(dirname "$0")" && pwd -P)
(
  cd "$scratch/frozen"
  sha256sum -c --quiet frozen.sha256
) || { echo "frozen input hash mismatch" >&2; exit 1; }
for source in "$scratch/frozen/recipe"/*; do
  [[ -f "$source" ]] || continue
  name=${source##*/}
  cmp -s "$source" "$recipe/$name" || { echo "live source differs from frozen recipe: $name" >&2; exit 1; }
done
base='ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
host_arch=$(uname -m)
case "$host_arch" in x86_64) architecture=amd64;; aarch64) architecture=arm64;; *) exit 2;; esac
[[ "$(cat "$scratch/frozen/architecture.txt")" = "$architecture" ]] || { echo "frozen APT architecture mismatch" >&2; exit 1; }
[[ "$(docker image inspect "$base" --format '{{.Architecture}}')" = "$architecture" ]] || { echo "base image architecture mismatch" >&2; exit 1; }
for run in a b; do
  output="$scratch/build-$run"
  [[ -d "$output" && -z "$(find "$output" -mindepth 1 -print -quit)" ]] || { echo "build output is not empty: $output" >&2; exit 2; }
  docker run --rm --pull=never --network none --cpus 4 --memory 8g --pids-limit 512 \
    --security-opt no-new-privileges \
    --env SOURCE_DATE_EPOCH=1788993887 --env PYTHONHASHSEED=0 \
    --mount "type=bind,src=$scratch/frozen,dst=/inputs,readonly" \
    --mount "type=bind,src=$output,dst=/out" \
    "$base" bash /inputs/recipe/build-offline-container.sh \
    > "$scratch/offline-$run.log" 2>&1
  cat "$output/wheel.sha256"
done
first=("$scratch"/build-a/*.whl)
second=("$scratch"/build-b/*.whl)
[[ ${#first[@]} = 1 && ${#second[@]} = 1 && "${first[0]##*/}" = "${second[0]##*/}" ]] || exit 1
cmp "${first[0]}" "${second[0]}"
cmp "$scratch/build-a/authenticated-packages.json" "$scratch/build-b/authenticated-packages.json"
cmp "$scratch/build-a/installed-build-packages.txt" "$scratch/build-b/installed-build-packages.txt"
echo "native-$architecture two-clean-offline-wheels-byte-equal $(sha256sum "${first[0]}")"
