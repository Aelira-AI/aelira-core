#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 6 ]]; then
  echo "usage: $0 PREPARED_SCRATCH PUBLIC_GIT_CHECKOUT REVIEWED_COMMIT REVIEWED_TREE REVIEWED_RECIPE_SHA256 REVIEWED_FROZEN_SHA256" >&2
  exit 2
fi
scratch=$1 repo=$2 expected_commit=$3 expected_tree=$4 expected_recipe=$5 expected_manifest=$6
[[ "$scratch" = /* && "$repo" = /* && -f "$scratch/frozen/frozen.sha256" ]] || exit 2
[[ "$(id -u)" != 0 ]] || exit 2
recipe=$(cd "$(dirname "$0")" && pwd -P)
python3 -I "$recipe/input-gate.py" check "$scratch/frozen" "$recipe" "$repo" \
  "$expected_commit" "$expected_tree" "$expected_recipe" "$expected_manifest" \
  > "$scratch/host-accept-source-check.json"
case "$(uname -m)" in x86_64) arch=amd64;; aarch64) arch=arm64;; *) exit 2;; esac
case "$(docker info --format '{{.Architecture}}'):$arch" in x86_64:amd64|amd64:amd64|aarch64:arm64|arm64:arm64);; *) exit 2;; esac
[[ "$(cat "$scratch/frozen/architecture.txt")" = "$arch" ]] || exit 1
base='ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
[[ "$(docker image inspect "$base" --format '{{.Architecture}}')" = "$arch" ]] || exit 1
verifier_id=$(cat "$scratch/frozen/verifier/image-id.txt")
[[ "$verifier_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
if ! docker image inspect "$verifier_id" > /dev/null 2>&1; then
  docker load -i "$scratch/frozen/verifier/image.tar" > "$scratch/verifier-load.log"
fi
[[ "$(docker image inspect "$verifier_id" --format '{{.Id}}')" = "$verifier_id" ]] || exit 1
[[ "$(docker image inspect "$verifier_id" --format '{{.Architecture}}')" = "$arch" ]] || exit 1
docker run --rm --pull=never --network none "$verifier_id" dpkg-query -W \
  | cmp - "$scratch/frozen/verifier/packages.txt"
for run in a b; do
  output="$scratch/build-$run"
  [[ -d "$output" && -z "$(find "$output" -mindepth 1 -print -quit)" ]] || {
    echo "build output is not empty" >&2; exit 2;
  }
  # This independently authenticated image verifies all candidate bytes before
  # the builder is created. The verifier never installs a cached candidate DEB.
  docker run --rm --pull=never --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
    --cpus 2 --memory 4g --pids-limit 256 \
    --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
    --mount "type=bind,src=$scratch/frozen,dst=/inputs,readonly" \
    --mount "type=bind,src=$output,dst=/out" \
    "$verifier_id" bash /inputs/recipe/preflight-container.sh "$expected_manifest" \
    > "$scratch/preflight-$run.log" 2>&1
  [[ "$(cat "$output/preinstall-signed-inputs.txt")" = "$expected_manifest" ]] || exit 1
  # Resolve the exact package closure from the pinned untouched base and the
  # already authenticated indexes. No candidate DEB is mounted or installed.
  docker run --rm --pull=never --network none --cpus 2 --memory 4g --pids-limit 256 \
    --security-opt no-new-privileges --cap-drop ALL \
    --mount "type=bind,src=$scratch/frozen/apt-lists,dst=/source-apt-lists,readonly" \
    "$base" bash -euc 'cp -a /source-apt-lists/. /var/lib/apt/lists/; apt-get -s install --no-install-recommends build-essential python3-dev python3-venv libxml2-dev libxslt1-dev libxml2-16 libxslt1.1 libxml2-utils binutils ca-certificates curl' \
    > "$output/apt-simulation.txt" 2> "$output/apt-simulation.stderr"
  python3 -I "$recipe/verify-apt-closure.py" \
    "$output/apt-simulation.txt" "$output/preinstall-authenticated-packages.json" \
    > "$output/apt-closure.json"
  printf '%s\n' "$expected_manifest" > "$output/preinstall-authenticated.txt"
  docker run --rm --pull=never --network none --cpus 4 --memory 8g --pids-limit 512 \
    --security-opt no-new-privileges \
    --mount "type=bind,src=$scratch/frozen,dst=/inputs,readonly" \
    --mount "type=bind,src=$output,dst=/out" \
    "$base" bash /inputs/recipe/build-offline-container.sh > "$scratch/offline-$run.log" 2>&1
  cat "$output/wheel.sha256"
done
first=("$scratch"/build-a/lxml-6.1.3-*.whl)
second=("$scratch"/build-b/lxml-6.1.3-*.whl)
[[ ${#first[@]} = 1 && ${#second[@]} = 1 && "${first[0]##*/}" = "${second[0]##*/}" ]] || exit 1
cmp "${first[0]}" "${second[0]}"
cmp "$scratch/build-a/authenticated-packages.json" "$scratch/build-b/authenticated-packages.json"
cmp "$scratch/build-a/installed-build-packages.txt" "$scratch/build-b/installed-build-packages.txt"
echo "native-$arch two-clean-offline-wheels-byte-equal $(sha256sum "${first[0]}")"
