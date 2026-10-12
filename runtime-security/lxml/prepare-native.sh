#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 5 ]]; then
  echo "usage: $0 NEW_ABSOLUTE_SCRATCH PUBLIC_GIT_CHECKOUT REVIEWED_COMMIT REVIEWED_TREE REVIEWED_RECIPE_SHA256" >&2
  exit 2
fi
scratch=$1 repo=$2 expected_commit=$3 expected_tree=$4 expected_recipe=$5
[[ "$scratch" = /* && ! -e "$scratch" && "$repo" = /* ]] || exit 2
[[ "$(id -u)" != 0 ]] || { echo "native host root refused" >&2; exit 2; }
recipe=$(cd "$(dirname "$0")" && pwd -P)
[[ "$(git -C "$repo" rev-parse --is-inside-work-tree)" = true ]] || exit 2
[[ "$(git -C "$repo" remote get-url origin)" = 'https://github.com/Aelira-AI/aelira-core.git' ]] || exit 2
[[ "$(git -C "$repo" rev-parse HEAD)" = "$expected_commit" ]] || exit 2
[[ "$(git -C "$repo" rev-parse HEAD^{tree})" = "$expected_tree" ]] || exit 2
[[ "$(python3 -I "$recipe/input-gate.py" recipe-id "$recipe")" = "$expected_recipe" ]] || {
  echo "externally selected recipe identity mismatch" >&2; exit 2;
}
base='ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
case "$(uname -m)" in x86_64) arch=amd64;; aarch64) arch=arm64;; *) exit 2;; esac
case "$(docker info --format '{{.Architecture}}'):$arch" in x86_64:amd64|amd64:amd64|aarch64:arm64|arm64:arm64);; *) exit 2;; esac
docker pull "$base" >/dev/null
[[ "$(docker image inspect "$base" --format '{{.Architecture}}')" = "$arch" ]] || exit 2
mkdir -p "$scratch/frozen/python" "$scratch/frozen/recipe" "$scratch/frozen/source" \
  "$scratch/frozen/verifier" "$scratch/build-a" "$scratch/build-b"
for name in Dockerfile.verifier README.md accept-native.sh build-offline-container.sh \
  compatibility.py consumer-container.sh consumer.py fetch-native.sh \
  fetch-consumer-wheels.py input-gate.py inputs.sha256 prepare-native.sh \
  preflight-container.sh run-consumer.sh verify-consumer-wheels.py \
  verify-apt-closure.py verify-frozen.py verify-installed.py; do
  cp "$recipe/$name" "$scratch/frozen/recipe/$name"
done
for source in src/education/docx_processor.py src/education/pptx_processor.py \
  src/education/xlsx_processor.py src/education/remediation/pptx_reading_order.py \
  requirements.txt; do
  mkdir -p "$scratch/frozen/source/$(dirname "$source")"
  git -C "$repo" show "$expected_commit:$source" > "$scratch/frozen/source/$source"
done
printf '%s\n' "$expected_commit" > "$scratch/frozen/source-commit.txt"
printf '%s\n' "$expected_tree" > "$scratch/frozen/source-tree.txt"

# Bootstrap only from independently authenticated online APT and the pinned
# base. The verifier image never sees a candidate DEB during its construction.
verifier_tag="aelira-lxml-verifier:$(basename "$scratch" | tr -c 'a-zA-Z0-9_.-' '-')-$arch"
docker build --no-cache --pull=false --network=default -f "$recipe/Dockerfile.verifier" \
  -t "$verifier_tag" "$recipe" > "$scratch/verifier-build.log" 2>&1
verifier_id=$(docker image inspect "$verifier_tag" --format '{{.Id}}')
[[ "$verifier_id" = sha256:* ]] || exit 1
[[ "$(docker image inspect "$verifier_id" --format '{{.Architecture}}')" = "$arch" ]] || exit 1
printf '%s\n' "$verifier_id" > "$scratch/frozen/verifier/image-id.txt"
docker save -o "$scratch/frozen/verifier/image.tar" "$verifier_id"
docker run --rm --pull=never --network none "$verifier_id" dpkg-query -W \
  > "$scratch/frozen/verifier/packages.txt"

docker run --rm --network bridge --cpus 4 --memory 8g --pids-limit 512 \
  --mount "type=bind,src=$scratch/frozen,dst=/frozen" \
  --mount "type=bind,src=$scratch/frozen/recipe,dst=/recipe,readonly" \
  "$base" bash /recipe/fetch-native.sh > "$scratch/apt-download.log" 2>&1
[[ "$(cat "$scratch/frozen/architecture.txt")" = "$arch" ]] || exit 1
base_keyring=$(sha256sum "$scratch/frozen/keyring/ubuntu-archive-keyring.gpg" | cut -d ' ' -f1)
verifier_keyring=$(docker run --rm --pull=never --network none "$verifier_id" \
  sha256sum /usr/share/keyrings/ubuntu-archive-keyring.gpg | cut -d ' ' -f1)
[[ "$base_keyring" = "$verifier_keyring" ]] || { echo "verifier keyring changed from pinned base" >&2; exit 1; }
while read -r hash filename; do
  case "$filename" in
    lxml-6.1.3.tar.gz) url='https://files.pythonhosted.org/packages/23/ad/28ecd7cb894d172f3c9c80a075eeeb2017ac62e3632cee05a5f9493547eb/lxml-6.1.3.tar.gz';;
    pip-26.2.1-py3-none-any.whl) url='https://files.pythonhosted.org/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/pip-26.2.1-py3-none-any.whl';;
    setuptools-84.0.0-py3-none-any.whl) url='https://files.pythonhosted.org/packages/95/9c/c510029fc6ef33a6275cd2c5d3cecd6613dfd6aa401d57c54f1c18852ccf/setuptools-84.0.0-py3-none-any.whl';;
    *) echo "unexpected Python input" >&2; exit 1;;
  esac
  curl --fail --location --silent --show-error --retry 3 \
    --output "$scratch/frozen/python/$filename" "$url"
  printf '%s  %s\n' "$hash" "$scratch/frozen/python/$filename" | sha256sum -c -
done < "$recipe/inputs.sha256"
python3 -I "$recipe/fetch-consumer-wheels.py" "$scratch/frozen" "$arch"
python3 -I "$recipe/input-gate.py" freeze "$scratch/frozen" > "$scratch/freeze-summary.json"
manifest_sha=$(sha256sum "$scratch/frozen/frozen.sha256" | cut -d ' ' -f1)
python3 -I "$recipe/input-gate.py" check "$scratch/frozen" "$recipe" "$repo" \
  "$expected_commit" "$expected_tree" "$expected_recipe" "$manifest_sha" \
  > "$scratch/host-source-check.json"
docker run --rm --network none --mount "type=bind,src=$scratch/frozen,dst=/frozen" \
  "$base" chmod -R a-w /frozen
echo "frozen-manifest-sha256 $manifest_sha verifier-image-id $verifier_id"
