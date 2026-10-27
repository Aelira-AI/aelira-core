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
  local tag_args=()
  if [ -n "${AELIRA_RETAIN_IMAGE_NAME:-}" ]; then
    tag_args=(--tag "$AELIRA_RETAIN_IMAGE_NAME")
  fi
  # The API Dockerfiles take CI's cache of pinned downloads as a named context.
  # Builders verify every cached file, so both builds still see the same bytes.
  local input_args=()
  if [ -n "${AELIRA_BUILD_INPUTS_CONTEXT:-}" ]; then
    input_args=(--build-context "build-inputs=$AELIRA_BUILD_INPUTS_CONTEXT")
  fi
  docker buildx build \
    --no-cache \
    --pull=false \
    --provenance=false \
    --sbom=false \
    --build-arg "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH" \
    --platform "$platform" \
    --file "$dockerfile" \
    "${tag_args[@]}" \
    "${input_args[@]}" \
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

# CI may retain the first of the two proven-equal archives for native runtime
# checks. The default path still removes both archives with the work directory.
if [ -n "${AELIRA_RETAIN_ARCHIVE:-}" ]; then
  mkdir -p "$(dirname "$AELIRA_RETAIN_ARCHIVE")"
  if [ -e "$AELIRA_RETAIN_ARCHIVE" ]; then
    echo "Refusing to overwrite retained OCI archive: $AELIRA_RETAIN_ARCHIVE" >&2
    exit 73
  fi
  mv -- "$first_archive" "$AELIRA_RETAIN_ARCHIVE"
  echo "Retained reproducible OCI archive: $AELIRA_RETAIN_ARCHIVE"
fi
