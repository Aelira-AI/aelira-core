#!/bin/sh
# Build the pinned PyAV source release against the shared FFmpeg 9.0.2
# prefix also used by the source-built OpenCV wheel and the image's ffmpeg
# and ffprobe programs.
set -eu

out="$1"
. "$(dirname -- "$0")/fetch-pinned.sh"
work=/tmp/pyav-build
prefix=/opt/ffmpeg9
if [ -e "$work" ] || [ -e "$prefix" ]; then
    echo "PyAV build paths already exist" >&2
    exit 1
fi
mkdir -p "$work" "$out"
trap 'rm -rf "$work"' EXIT
cd "$work"

# Official FFmpeg release archive and the PyPI source distribution. The
# FFmpeg release signature and signer are recorded in docs/DEPENDENCIES.md.
fetch https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz \
    8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e \
    ffmpeg-9.0.2.tar.xz
fetch https://files.pythonhosted.org/packages/8d/f4/f22114d30d3435e38c6af2b4870f37b864403dca6ae7af747a289ce0a18e/av-18.1.0.tar.gz \
    47bfc286e1bc9de7ab4681fc2b575cd2460a66919d31ffe1bd5aa54fae531a28 \
    av-18.1.0.tar.gz

tar -xJf ffmpeg-9.0.2.tar.xz
(
    cd ffmpeg-9.0.2
    # The ffmpeg and ffprobe programs replace Debian's ffmpeg package in the
    # API image. Only the file and pipe protocols are compiled in, so no
    # demuxer can follow a reference over the network. libmp3lame and
    # libvorbis keep MP3 and Ogg Vorbis speech output (Ogg defaults to FLAC
    # without libvorbis).
    ./configure --prefix="$prefix" --enable-shared --disable-static \
        --disable-programs --enable-ffmpeg --enable-ffprobe \
        --disable-doc --disable-autodetect --disable-debug \
        --disable-network --disable-protocols \
        --enable-protocol=file --enable-protocol=pipe \
        --enable-libdav1d --disable-decoder=av1 \
        --enable-libmp3lame --enable-libvorbis \
        --extra-cflags="-O2 -g0 -ffile-prefix-map=$work=." \
        --extra-ldflags="-Wl,--disable-new-dtags,-rpath,$prefix/lib"
    make --jobs="$(nproc)"
    make install
)

tar -xzf av-18.1.0.tar.gz
mkdir -p "$prefix/licenses/ffmpeg-9.0.2" "$prefix/licenses/pyav-18.1.0"
cp ffmpeg-9.0.2/LICENSE.md ffmpeg-9.0.2/COPYING.LGPLv2.1 \
    "$prefix/licenses/ffmpeg-9.0.2/"
cp av-18.1.0/LICENSE.txt "$prefix/licenses/pyav-18.1.0/"
(
    cd av-18.1.0
    PKG_CONFIG_PATH="$prefix/lib/pkgconfig" \
    CFLAGS="-O2 -g0 -ffile-prefix-map=$work=." \
    LDFLAGS="-Wl,--disable-new-dtags,-rpath,$prefix/lib" \
        python -m pip wheel --no-deps --no-build-isolation --no-cache-dir \
            --wheel-dir "$out" .
)

# OpenCV still needs the headers, pkg-config files and linker symlinks.
# docker/build-opencv-wheel.sh prunes them after the second wheel is built.
