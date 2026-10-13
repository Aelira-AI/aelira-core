#!/bin/sh
# Export pinned Git source and generate the two official runtime modules offline.
set -eu

if [ "$#" -ne 7 ]; then
  echo "usage: prepare-source.sh ABSOLUTE_GIT ABSOLUTE_UPSTREAM_REPO NEW_OUTPUT_DIRECTORY ABSOLUTE_DOCKER GENERATOR_IMAGE_ID NATIVE_ARCH PERL_SHA256:RECDESCENT_SHA256" >&2
  exit 2
fi

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$here/package_source.py" --git "$1" --repo "$2" \
  --patch "$here/siunitx-v3-adapter.patch" --out "$3" \
  --docker "$4" --generator-image-id "$5" --architecture "$6" \
  --perl-sha256 "${7%%:*}" --recdescent-sha256 "${7#*:}"
