#!/bin/sh
# Build the pinned Pillow release against a pinned, hash-verified libtiff.
#
# The published Pillow wheels bundle libtiff 4.7.1 in a private library
# directory. This builds the same Pillow release from its source archive with
# libtiff 4.7.2 linked statically into Pillow's own extension module, and the
# remaining image libraries (JPEG, JPEG 2000, WebP, AVIF, FreeType, Raqm,
# FriBidi, HarfBuzz, LittleCMS, zlib, XCB) taken from the system packages,
# which receive Debian security updates. Every source archive is verified
# against a pinned SHA-256 before it
# is used; the builder never downloads unverified files.
set -eu

out="$1"
# A fixed build path keeps the compiled output reproducible.
work=/tmp/pillow-build
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

# A system libtiff development package would be found before the static build
# below. The wheel must not link it, so refuse to build when one is present.
for header in /usr/include/tiff.h /usr/include/*/tiff.h; do
    if [ -e "$header" ]; then
        echo "system libtiff headers present: $header" >&2
        exit 1
    fi
done

fetch https://download.osgeo.org/libtiff/tiff-4.7.2.tar.xz \
    4996f0c4f93094719b1ca5c6279b20e588773ba8a247533e486416fb662ddb88 \
    tiff-4.7.2.tar.xz
fetch https://files.pythonhosted.org/packages/ce/a4/9cc30690a394a8305e8be4c9036aa16d0dfddadd09079860b582f562496d/pillow-13.0.0.tar.gz \
    cdcd1ba258178f7356d74170c9a81ba4eab6a5d55748bc402a5a2a6fe6be3510 \
    pillow-13.0.0.tar.gz

# libtiff keeps the codecs the published wheel's copy had: JPEG, Deflate (zlib),
# LZMA and Zstandard. Tools, tests, contrib code and documentation are not
# built, and no further codec libraries are linked. No debug information and
# mapped source paths keep the output reproducible.
tar -xJf tiff-4.7.2.tar.xz
prefix="$work/tiff-prefix"
(
    cd tiff-4.7.2
    CFLAGS="-O2 -fPIC -g0 -ffile-prefix-map=$work=." \
        ./configure --prefix="$prefix" --disable-shared --enable-static \
        --with-pic --disable-dependency-tracking --disable-docs \
        --disable-tools --disable-tests --disable-contrib --disable-cxx \
        --disable-libdeflate --disable-jbig --disable-lerc --disable-webp \
        --disable-opengl --disable-sphinx
    # Every codec that is meant to be present must have been detected.
    for define in ZIP_SUPPORT JPEG_SUPPORT LZMA_SUPPORT ZSTD_SUPPORT; do
        grep -q "^#define $define 1" libtiff/tif_config.h libtiff/tiffconf.h \
            || { echo "libtiff built without $define" >&2; exit 1; }
    done
    for define in LIBDEFLATE_SUPPORT JBIG_SUPPORT LERC_SUPPORT WEBP_SUPPORT; do
        if grep -q "^#define $define 1" libtiff/tif_config.h libtiff/tiffconf.h; then
            echo "libtiff unexpectedly built with $define" >&2
            exit 1
        fi
    done
    make --jobs="$(nproc)"
    make install
)

tar -xzf pillow-13.0.0.tar.gz
cd pillow-13.0.0
# Pillow looks in TIFF_ROOT first, finds only the static library there and
# links it into _imaging. The static archive needs the libraries it was built
# against, which are listed explicitly and kept even if not referenced first.
# Every required feature of the published wheel is enabled, so a missing
# development package fails the build instead of silently dropping it.
# libimagequant was not in the wheel and stays disabled.
CFLAGS="-O2 -fPIC -g0 -ffile-prefix-map=$work=." \
LDFLAGS="-Wl,--no-as-needed -llzma -lzstd" \
TIFF_ROOT="$prefix" \
    python -m pip wheel --no-deps --no-build-isolation --wheel-dir "$out" \
        -C zlib=enable -C jpeg=enable -C tiff=enable -C freetype=enable \
        -C lcms=enable -C webp=enable -C jpeg2000=enable -C xcb=enable \
        -C avif=enable -C raqm=enable -C imagequant=disable \
        .
