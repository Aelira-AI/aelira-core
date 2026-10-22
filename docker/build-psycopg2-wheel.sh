#!/bin/sh
# Build the pinned psycopg2-binary release from its source archive against the
# system libpq.
#
# The published psycopg2-binary wheels bundle their own libpq, OpenSSL and
# Kerberos libraries in psycopg2_binary.libs, and a system package update never
# reaches those copies. Building the same release from source links the
# extension module to Debian's maintained libpq5, so the TLS and Kerberos code
# it loads is the code the operating system patches. The source archive is
# verified against a pinned SHA-256 before it is unpacked; nothing unverified is
# downloaded or built.
set -eu

out="$1"
# A fixed build path keeps the compiled output reproducible.
work=/tmp/psycopg2-build
rm -rf "$work"
mkdir "$work"
trap 'rm -rf "$work"' EXIT
cd "$work"

fetch() {
    url="$1" sha="$2" dest="$3"
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
        --retry 3 --retry-all-errors --output "$dest" "$url"
    echo "$sha  $dest" | sha256sum --check --strict --quiet
}

fetch https://files.pythonhosted.org/packages/ed/76/7b4383014be0fcc6c1c0e24292845a14e1672cf17fca62ca0a2bd5f4563d/psycopg2_binary-2.9.13.tar.gz \
    e324ecf60f952d21dd11413b8bbed0951bbd99579a06fd06f28bfc37737cd373 \
    psycopg2_binary-2.9.13.tar.gz
tar -xzf psycopg2_binary-2.9.13.tar.gz

cd psycopg2-binary-2.9.13
# No debug information and mapped source paths keep the output reproducible.
CFLAGS="-O2 -fPIC -g0 -ffile-prefix-map=$work=." \
    python -m pip wheel --no-deps --no-build-isolation --no-cache-dir \
        --wheel-dir "$out" .
