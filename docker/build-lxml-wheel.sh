#!/bin/sh
# Build the pinned lxml release against pinned, hash-verified libxml2/libxslt.
#
# The published lxml wheels bundle libxml2 2.14.x. This builds the same lxml
# release with libxml2 2.15.4 and libxslt 1.1.45 linked statically, using
# lxml's own static build. Every source archive is verified against a pinned
# SHA-256 before it is used; the builder never downloads unverified files.
set -eu

out="$1"
# A fixed build path keeps the compiled output reproducible.
work=/tmp/lxml-build
rm -rf "$work"
mkdir "$work"
trap 'rm -rf "$work"' EXIT
cd "$work"

fetch() {
    url="$1" sha="$2" dest="$3"
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
        --retry 3 --output "$dest" "$url"
    echo "$sha  $dest" | sha256sum --check --strict --quiet
}

fetch https://files.pythonhosted.org/packages/23/ad/28ecd7cb894d172f3c9c80a075eeeb2017ac62e3632cee05a5f9493547eb/lxml-6.1.3.tar.gz \
    45222d94ddd511536f3b2f7d9deae3b2339b4ce0f075f1ca25703b07cad9dd21 lxml-6.1.3.tar.gz
tar -xzf lxml-6.1.3.tar.gz
mkdir lxml-6.1.3/libs
fetch https://download.gnome.org/sources/libxml2/2.15/libxml2-2.15.4.tar.xz \
    98087fd181d9070724f3fbc65c7377db03038eb92bd882374daff44940138821 \
    lxml-6.1.3/libs/libxml2-2.15.4.tar.xz
fetch https://download.gnome.org/sources/libxslt/1.1/libxslt-1.1.45.tar.xz \
    9acfe68419c4d06a45c550321b3212762d92f41465062ca4ea19e632ee5d216e \
    lxml-6.1.3/libs/libxslt-1.1.45.tar.xz
fetch https://zlib.net/fossils/zlib-1.3.2.tar.gz \
    bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16 \
    lxml-6.1.3/libs/zlib-1.3.2.tar.gz
fetch https://ftp.gnu.org/pub/gnu/libiconv/libiconv-1.18.tar.gz \
    3b08f5f4f9b4eb82f151a7040bfd6fe6c6fb922efe4b1659c66ea933276965e8 \
    lxml-6.1.3/libs/libiconv-1.18.tar.gz

cd lxml-6.1.3
# The static libraries are linked into lxml's shared extension modules. No
# debug information and mapped source paths keep the output reproducible.
CFLAGS="-O2 -fPIC -g0 -ffile-prefix-map=$work=." \
STATIC_DEPS=true \
LIBXML2_VERSION=2.15.4 \
LIBXSLT_VERSION=1.1.45 \
ZLIB_VERSION=1.3.2 \
LIBICONV_VERSION=1.18 \
    python -m pip wheel --no-deps --no-build-isolation --wheel-dir "$out" .
