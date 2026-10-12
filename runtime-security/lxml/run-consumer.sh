#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 7 ]]; then
  echo "usage: $0 PREPARED_SCRATCH PUBLIC_GIT_CHECKOUT REVIEWED_COMMIT REVIEWED_TREE REVIEWED_RECIPE_SHA256 REVIEWED_FROZEN_SHA256 REVIEWED_WHEEL_SHA256" >&2
  exit 2
fi
scratch=$1 repo=$2 expected_commit=$3 expected_tree=$4 expected_recipe=$5 expected_manifest=$6 expected_wheel=$7
[[ "$scratch" = /* && "$repo" = /* && -f "$scratch/frozen/frozen.sha256" && -f "$scratch/build-a/wheel.sha256" ]] || exit 2
[[ "$expected_wheel" =~ ^[0-9a-f]{64}$ ]] || exit 2
recipe=$(cd "$(dirname "$0")" && pwd -P)
python3 -I "$recipe/input-gate.py" check "$scratch/frozen" "$recipe" "$repo" \
  "$expected_commit" "$expected_tree" "$expected_recipe" "$expected_manifest" \
  > "$scratch/host-consumer-source-check.json"
case "$(uname -m)" in x86_64) arch=amd64;; aarch64) arch=arm64;; *) exit 2;; esac
case "$(docker info --format '{{.Architecture}}'):$arch" in x86_64:amd64|amd64:amd64|aarch64:arm64|arm64:arm64);; *) exit 2;; esac
[[ "$(cat "$scratch/frozen/architecture.txt")" = "$arch" ]] || exit 1
wheel=("$scratch"/build-a/lxml-6.1.3-*.whl)
other=("$scratch"/build-b/lxml-6.1.3-*.whl)
[[ ${#wheel[@]} = 1 && ${#other[@]} = 1 ]] || exit 1
cmp "${wheel[0]}" "${other[0]}"
[[ "$(sha256sum "${wheel[0]}" | cut -d ' ' -f1)" = "$expected_wheel" ]] || exit 1
[[ "$(cut -d ' ' -f1 "$scratch/build-a/wheel.sha256")" = "$expected_wheel" ]] || exit 1
[[ "$(cut -d ' ' -f1 "$scratch/build-b/wheel.sha256")" = "$expected_wheel" ]] || exit 1
base='ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
[[ "$(docker image inspect "$base" --format '{{.Architecture}}')" = "$arch" ]] || exit 1
verifier_id=$(cat "$scratch/frozen/verifier/image-id.txt")
[[ "$verifier_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
if ! docker image inspect "$verifier_id" > /dev/null 2>&1; then
  docker load -i "$scratch/frozen/verifier/image.tar" > "$scratch/verifier-consumer-load.log"
fi
[[ "$(docker image inspect "$verifier_id" --format '{{.Id}}')" = "$verifier_id" ]] || exit 1
[[ "$(docker image inspect "$verifier_id" --format '{{.Architecture}}')" = "$arch" ]] || exit 1
docker run --rm --pull=never --network none "$verifier_id" dpkg-query -W \
  | cmp - "$scratch/frozen/verifier/packages.txt"
[[ ! -e "$scratch/consumer" ]] || { echo "consumer output already exists" >&2; exit 2; }
mkdir "$scratch/consumer"
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
  --cpus 2 --memory 4g --pids-limit 256 \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
  --mount "type=bind,src=$scratch/frozen,dst=/inputs,readonly" \
  --mount "type=bind,src=$scratch/consumer,dst=/out" \
  "$verifier_id" bash /inputs/recipe/preflight-container.sh "$expected_manifest" \
  > "$scratch/preflight-consumer.log" 2>&1
[[ "$(cat "$scratch/consumer/preinstall-signed-inputs.txt")" = "$expected_manifest" ]] || exit 1
docker run --rm --pull=never --network none --cpus 2 --memory 4g --pids-limit 256 \
  --security-opt no-new-privileges --cap-drop ALL \
  --mount "type=bind,src=$scratch/frozen/apt-lists,dst=/source-apt-lists,readonly" \
  "$base" bash -euc 'cp -a /source-apt-lists/. /var/lib/apt/lists/; apt-get -s install --no-install-recommends build-essential python3-dev python3-venv libxml2-dev libxslt1-dev libxml2-16 libxslt1.1 libxml2-utils binutils ca-certificates curl' \
  > "$scratch/consumer/apt-simulation.txt" 2> "$scratch/consumer/apt-simulation.stderr"
python3 -I "$recipe/verify-apt-closure.py" \
  "$scratch/consumer/apt-simulation.txt" "$scratch/consumer/preinstall-authenticated-packages.json" \
  > "$scratch/consumer/apt-closure.json"
printf '%s\n' "$expected_manifest" > "$scratch/consumer/preinstall-authenticated.txt"
printf 'source_commit=%s\nsource_tree=%s\nrecipe_sha256=%s\nfrozen_sha256=%s\nwheel_sha256=%s\nverifier_image_id=%s\n' \
  "$expected_commit" "$expected_tree" "$expected_recipe" "$expected_manifest" \
  "$expected_wheel" "$verifier_id" > "$scratch/consumer/source-bound-identity.txt"
docker run --rm --pull=never --network none --cpus 4 --memory 8g --pids-limit 512 \
  --security-opt no-new-privileges \
  --mount "type=bind,src=$scratch/frozen,dst=/inputs,readonly" \
  --mount "type=bind,src=$scratch/build-a,dst=/wheels,readonly" \
  --mount "type=bind,src=$scratch/consumer,dst=/out" \
  "$base" bash /inputs/recipe/consumer-container.sh > "$scratch/consumer.log" 2>&1
echo "native-$arch-source-wheel-office-consumers-passed"
