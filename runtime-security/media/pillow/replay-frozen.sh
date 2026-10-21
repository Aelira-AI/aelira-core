#!/usr/bin/env bash
# Replay two fresh native wheels from an existing authenticated frozen builder.
set -euo pipefail
live_recipe=$(cd "$(dirname "$0")" && pwd)
prepared=${1:?usage: replay-frozen.sh PREPARED_OUTPUT EMPTY_REPLAY_OUTPUT}
replay=${2:?usage: replay-frozen.sh PREPARED_OUTPUT EMPTY_REPLAY_OUTPUT}
prepared=$(cd "$prepared" && pwd)
[[ "$(uname -s)" = Linux ]] || exit 1
case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=amd64 ;; *) exit 1 ;; esac
case "$(docker info --format '{{.Architecture}}'):$arch" in
  aarch64:arm64|arm64:arm64|x86_64:amd64|amd64:amd64) ;; *) exit 1 ;;
esac
builder=$(cat "$prepared/provenance/frozen-builder-id.txt")
[[ "$builder" = sha256:* ]]
archive="$prepared/provenance/frozen-builder.tar"
expected=$(cut -d ' ' -f1 "$prepared/provenance/frozen-builder.tar.sha256")
test "$(sha256sum "$archive" | cut -d ' ' -f1)" = "$expected"
if ! docker image inspect "$builder" > /dev/null 2>&1; then
  docker load -i "$archive" > /dev/null
fi
[[ "$(docker image inspect --format '{{.Id}}' "$builder")" = "$builder" ]]
[[ "$(docker image inspect --format '{{.Architecture}}' "$builder")" = "$arch" ]]
python3 "$live_recipe/recipe-lock.py" compare "$prepared/recipe" "$live_recipe"
mkdir -p "$replay"
replay=$(cd "$replay" && pwd)
[[ -z "$(ls -A "$replay")" ]] || { echo 'Replay output must be empty' >&2; exit 1; }
trap 'rc=$?; trap - EXIT; printf "%s\n" "$rc" > "$replay/build-exit.txt"; exit "$rc"' EXIT
printf '%s\n' "$(uname -m)" > "$replay/host-machine.txt"
docker info --format '{{.Architecture}}' > "$replay/daemon-architecture.txt"
cp -R "$prepared/recipe" "$replay/recipe"
cp -R "$prepared/input" "$replay/input"
cp -R "$prepared/provenance" "$replay/provenance"
recipe="$replay/recipe"
python3 "$recipe/recipe-lock.py" verify "$recipe"
python3 "$recipe/proof.py" inventory "$replay" "$arch"
sha256sum "$recipe/manifest.json" | cut -d ' ' -f1 > "$replay/recipe-manifest.sha256"
printf '%s\n' "$builder" > "$replay/frozen-builder-id.txt"
for run in a b; do
  python3 "$recipe/recipe-lock.py" verify "$recipe"
  python3 "$recipe/proof.py" check-inventory "$replay" "$arch"
  mkdir "$replay/build-$run"
  docker run --rm --platform "linux/$arch" --user "$(id -u):$(id -g)" \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --cpus 4 --memory 8g --pids-limit 512 \
    --tmpfs /tmp:rw,nosuid,nodev,size=1g \
    -e HOME=/work -e AELIRA_PILLOW_HOST_ARCH="$arch" \
    -v "$replay/recipe:/recipe:ro" -v "$replay/input:/input:ro" \
    -v "$replay/provenance:/provenance:ro" -v "$replay/build-$run:/work" \
    "$builder" bash /recipe/build-in-container.sh \
    > "$replay/build-$run/console.log" 2>&1
done
python3 "$recipe/proof.py" check-inventory "$replay" "$arch"
wheel_a=("$replay"/build-a/wheels/pillow-13.0.0-*.whl)
wheel_b=("$replay"/build-b/wheels/pillow-13.0.0-*.whl)
[[ ${#wheel_a[@]} = 1 && ${#wheel_b[@]} = 1 ]]
cmp "${wheel_a[0]}" "${wheel_b[0]}"
sha256sum "${wheel_a[0]}" "${wheel_b[0]}" > "$replay/reproducible-sha256.txt"
python3 "$recipe/recipe-lock.py" verify "$recipe"
printf '0\n' > "$replay/build-exit.txt"
python3 "$recipe/proof.py" build "$replay" "$arch"
printf 'native_pillow_frozen_replay=pass architecture=%s builder=%s\n' "$arch" "$builder"
