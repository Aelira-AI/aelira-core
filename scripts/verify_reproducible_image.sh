#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "Usage: $0 <context> <dockerfile> <platform>" >&2
  exit 64
fi

context=$1
dockerfile=$2
platform=$3
# BuildKit normalizes image/layer timestamps from this value. Without a stable
# epoch, identical no-cache builds can produce different OCI manifests.
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}"

if [ ! -d "$context" ]; then
  echo "Build context is not a directory: $context" >&2
  exit 66
fi
if [ ! -f "$dockerfile" ]; then
  echo "Dockerfile does not exist: $dockerfile" >&2
  exit 66
fi

workdir=$(mktemp -d "${TMPDIR:-/tmp}/aelira-reproducible-image.XXXXXX")
trap 'rm -rf -- "$workdir"' EXIT

build_archive() {
  local destination=$1
  docker buildx build \
    --no-cache \
    --pull=false \
    --provenance=false \
    --sbom=false \
    --build-arg "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH" \
    --platform "$platform" \
    --file "$dockerfile" \
    --output "type=oci,dest=$destination,rewrite-timestamp=true" \
    "$context"
}

manifest_digest() {
  local archive=$1
  tar -xOf "$archive" index.json | python3 -c '
import json
import re
import sys

index = json.load(sys.stdin)
manifests = index.get("manifests")
if not isinstance(manifests, list) or len(manifests) != 1:
    raise SystemExit("OCI index must contain exactly one manifest")
digest = manifests[0].get("digest")
if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
    raise SystemExit("OCI index manifest digest is missing or malformed")
print(digest)
'
}

first_archive="$workdir/first.oci.tar"
second_archive="$workdir/second.oci.tar"
build_archive "$first_archive"
build_archive "$second_archive"
first_digest=$(manifest_digest "$first_archive")
second_digest=$(manifest_digest "$second_archive")

if [ "$first_digest" != "$second_digest" ]; then
  echo "Image is not reproducible for $platform: $first_digest != $second_digest" >&2
  exit 1
fi

echo "Reproducible image manifest for $platform: $first_digest"
