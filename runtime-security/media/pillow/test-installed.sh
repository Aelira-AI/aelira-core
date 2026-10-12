#!/usr/bin/env bash
# Build a disposable installed-consumer candidate from an explicit baseline ID.
set -euo pipefail
live_recipe=$(cd "$(dirname "$0")" && pwd)
output=${1:?usage: test-installed.sh ACCEPTED_BUILD_OUTPUT EXACT_BASELINE_IMAGE_ID}
baseline=${2:?usage: test-installed.sh ACCEPTED_BUILD_OUTPUT EXACT_BASELINE_IMAGE_ID}
output=$(cd "$output" && pwd)
stage=preflight
record_exit() {
  local rc=$1 temporary
  trap - EXIT
  set +e
  printf '%s\n' "$rc" > "$output/consumer-exit.txt" 2>/dev/null
  temporary=$(mktemp "$output/.consumer-stage.XXXXXXXX") || exit "$rc"
  if printf '{"schema":1,"stage":"%s","exit":%s}\n' "$stage" "$rc" > "$temporary"; then
    ln "$temporary" "$output/consumer-stage.json" >/dev/null 2>&1
  fi
  rm -f -- "$temporary"
  exit "$rc"
}
trap 'record_exit "$?"' EXIT
python3 "$live_recipe/recipe-lock.py" compare "$output/recipe" "$live_recipe"
recipe="$output/recipe"
[[ "$baseline" = sha256:* ]] || { echo 'Exact baseline image ID required' >&2; exit 1; }
case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=amd64 ;; *) exit 1 ;; esac
case "$(docker info --format '{{.Architecture}}'):$arch" in
  aarch64:arm64|arm64:arm64|x86_64:amd64|amd64:amd64) ;; *) exit 1 ;;
esac
[[ "$(docker image inspect --format '{{.Id}}' "$baseline")" = "$baseline" ]]
[[ "$(docker image inspect --format '{{.Architecture}}' "$baseline")" = "$arch" ]]
test -f "$output/reproducible-sha256.txt"
test -f "$output/build-a/wheel-verification.log"
test "$(cat "$output/build-exit.txt")" = 0
python3 "$recipe/recipe-lock.py" verify "$recipe"
python3 "$recipe/proof.py" check-build "$output" "$arch"
wheel_a=("$output"/build-a/wheels/pillow-12.3.0-*.whl)
wheel_b=("$output"/build-b/wheels/pillow-12.3.0-*.whl)
[[ ${#wheel_a[@]} = 1 && ${#wheel_b[@]} = 1 ]]
cmp "${wheel_a[0]}" "${wheel_b[0]}"
grep -F "$(sha256sum "${wheel_a[0]}" | cut -d ' ' -f1)" "$output/reproducible-sha256.txt" > /dev/null
name="aelira-pillow-consumer-$arch-$$"
stage=candidate_install
docker run --name "$name" --platform "linux/$arch" --network none \
  --security-opt no-new-privileges \
  --cpus 4 --memory 8g --pids-limit 512 \
  --user root -e AELIRA_PILLOW_HOST_ARCH="$arch" \
  -v "$recipe:/recipe:ro" -v "$output/input:/input:ro" \
  -v "$output/build-a/wheels:/wheel:ro" \
  --entrypoint /bin/bash "$baseline" /recipe/install-in-candidate.sh \
  > "$output/installed-image-build.log" 2>&1
stage=candidate_commit
candidate=$(docker commit "$name")
docker rm "$name" > /dev/null
printf '%s\n' "$candidate" > "$output/installed-image-id.txt"
printf '%s\n' "$baseline" > "$output/baseline-image-id.txt"
python3 "$recipe/recipe-lock.py" verify "$recipe"
stage=source_identity
consumer_hashes="$output/consumer-source-sha256.tsv"
: > "$consumer_hashes"
for pair in \
  'src/education/remediation/pdf_remediator.py:84666dcaf3c84341ebbc5a0f9a37338e2877c458da76c20460dd44b0c8d2b8e8' \
  'src/education/handwritten_math_suitability.py:310e239e7837e12e22a3ee14a831f38cf163360959675f7ae0ad149c4a5ddf50'; do
  path=${pair%%:*}
  expected=${pair##*:}
  actual=$(docker run --rm --platform "linux/$arch" --network none --read-only \
    --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
    --entrypoint /usr/bin/sha256sum "$candidate" "/app/$path" | cut -d ' ' -f1)
  [[ "$actual" = "$expected" ]] || { echo 'Aelira consumer source differs' >&2; exit 1; }
  printf '%s\t%s\n' "$path" "$actual" >> "$consumer_hashes"
done
stage=installed_verifier
set +e
docker run --rm --platform "linux/$arch" --network none --read-only \
  --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  -v "$output/build-a/wheels:/wheel:ro" \
  -e PYTHONPATH=/app -v "$recipe/verify-installed.py:/tmp/verify-installed.py:ro" \
  --entrypoint /opt/venv/bin/python "$candidate" /tmp/verify-installed.py \
  > "$output/installed-consumer.log" 2>&1
verifier_exit=$?
set -e
if [[ "$verifier_exit" -eq 40 ]]; then
  # This is a separate observation. Its exit cannot replace the verifier's 40.
  set +e
  observed_image=$(docker image inspect --format '{{.Id}}' "$candidate" 2>/dev/null)
  inspect_exit=$?
  if [[ "$inspect_exit" -eq 0 && "$observed_image" = "$candidate" ]]; then
    docker run --rm --platform "linux/$arch" --network none --read-only \
      --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
      --tmpfs /tmp:rw,nosuid,nodev,size=128m \
      -v "$output/build-a/wheels:/wheel:ro" \
      -e PYTHONPATH=/app -v "$recipe/verify-installed.py:/tmp/verify-installed.py:ro" \
      --entrypoint /opt/venv/bin/python "$candidate" /tmp/verify-installed.py \
      --import-diagnostic "$observed_image" \
      > "$output/pillow-import-diagnostic.json" \
      2> "$output/pillow-import-diagnostic-private.log"
    diagnostic_exit=$?
  else
    diagnostic_exit=125
  fi
  printf '%s\n' "$diagnostic_exit" > "$output/pillow-import-diagnostic-exit.txt"
  exit "$verifier_exit"
fi
if [[ "$verifier_exit" -ne 0 ]]; then
  exit "$verifier_exit"
fi
grep -Fx 'Pillow wheel-private library directory absent' "$output/installed-consumer.log"
stage=final_proof
python3 "$recipe/recipe-lock.py" verify "$recipe"
printf '0\n' > "$output/consumer-exit.txt"
python3 "$recipe/proof.py" installed "$output" "$arch"
printf 'native_pillow_installed=pass architecture=%s image=%s\n' "$arch" "$candidate"
stage=complete
