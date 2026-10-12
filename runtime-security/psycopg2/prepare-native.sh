#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 1 ]]; then
  echo "usage: $0 NEW_ABSOLUTE_SCRATCH" >&2
  exit 2
fi
scratch=$1
[[ "$scratch" = /* && ! -e "$scratch" ]] || { echo "scratch must be a new absolute path" >&2; exit 2; }
[[ "$(id -u)" != 0 ]] || { echo "native host root is refused" >&2; exit 2; }
recipe=$(cd "$(dirname "$0")" && pwd -P)
base='ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
host_arch=$(uname -m)
case "$host_arch" in x86_64) architecture=amd64;; aarch64) architecture=arm64;; *) echo "unsupported native architecture: $host_arch" >&2; exit 2;; esac
docker_arch=$(docker info --format '{{.Architecture}}')
[[ "$docker_arch" = "$host_arch" || ( "$host_arch" = x86_64 && "$docker_arch" = amd64 ) || ( "$host_arch" = aarch64 && "$docker_arch" = arm64 ) ]] || { echo "Docker is not native" >&2; exit 2; }
docker pull "$base" > /dev/null
image_arch=$(docker image inspect "$base" --format '{{.Architecture}}')
[[ "$image_arch" = "$architecture" ]] || { echo "pinned base is not native $architecture" >&2; exit 2; }
mkdir -p "$scratch/frozen/python" "$scratch/frozen/recipe" "$scratch/build-a" "$scratch/build-b"
for name in accept-native.sh build-offline-container.sh consumer.py fetch-native.sh \
  inputs.sha256 prepare-native.sh run-synthetic-db.sh verify-frozen.py verify_installed.py; do
  cp "$recipe/$name" "$scratch/frozen/recipe/$name"
done
docker run --rm --network bridge --cpus 4 --memory 8g --pids-limit 512 \
  --mount "type=bind,src=$scratch/frozen,dst=/frozen" \
  --mount "type=bind,src=$scratch/frozen/recipe,dst=/recipe,readonly" \
  "$base" bash /recipe/fetch-native.sh | tee "$scratch/apt-download.log"
[[ "$(cat "$scratch/frozen/architecture.txt")" = "$architecture" ]] || { echo "APT architecture mismatch" >&2; exit 1; }
while read -r digest filename; do
  [[ -n "$digest" ]] || continue
  case "$filename" in
    psycopg2_binary-2.9.13.tar.gz) url='https://files.pythonhosted.org/packages/ed/76/7b4383014be0fcc6c1c0e24292845a14e1672cf17fca62ca0a2bd5f4563d/psycopg2_binary-2.9.13.tar.gz';;
    pip-26.2.1-py3-none-any.whl) url='https://files.pythonhosted.org/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/pip-26.2.1-py3-none-any.whl';;
    setuptools-84.0.0-py3-none-any.whl) url='https://files.pythonhosted.org/packages/95/9c/c510029fc6ef33a6275cd2c5d3cecd6613dfd6aa401d57c54f1c18852ccf/setuptools-84.0.0-py3-none-any.whl';;
    *) echo "unexpected Python input: $filename" >&2; exit 1;;
  esac
  curl --fail --location --silent --show-error --retry 3 --output "$scratch/frozen/python/$filename" "$url"
  printf '%s  %s\n' "$digest" "$scratch/frozen/python/$filename" | sha256sum -c -
done < "$recipe/inputs.sha256"
(
  cd "$scratch/frozen"
  find apt-lists debs -maxdepth 1 -type f ! -name lock -print0 \
    | sort -z | xargs -0 sha256sum > frozen.sha256
  find python recipe keyring -type f -print0 | sort -z | xargs -0 sha256sum >> frozen.sha256
  sha256sum -c frozen.sha256 > /dev/null
)
docker run --rm --network none \
  --mount "type=bind,src=$scratch/frozen,dst=/frozen" \
  "$base" chmod -R a-w /frozen
echo "frozen-input-sha256 $(sha256sum "$scratch/frozen/frozen.sha256")"
