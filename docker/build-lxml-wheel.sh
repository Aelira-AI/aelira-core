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

fetch() (
    # fetch URL SHA256 DEST [FALLBACK_URL...]
    # Fallback addresses are tried in order only after every attempt at the
    # previous address failed, and each must deliver the same pinned bytes.
    sha="$2" dest="$3"
    first="$1"
    shift 3
    fallbacks=$#
    # Only a completed, verified attempt may become an archive. Discard any
    # previous destination and isolate partial responses from later attempts.
    rm -f "$dest"
    partial=$(mktemp "${dest}.partial.XXXXXX")
    trap 'rm -f "$partial"' EXIT
    trap 'exit 1' HUP INT TERM
    index=0
    for url in "$first" "$@"; do
        # Name fallbacks by position only: addresses can carry credentials.
        label=
        if [ "$index" -gt 0 ]; then label=" fallback=$index/$fallbacks"; fi
        attempt=1
        while [ "$attempt" -le 3 ]; do
            : > "$partial"
            status=0
            # Three attempts, each capped at 60 seconds, plus two one-second waits.
            # Suppress response/error text: it can contain proxy credentials or URLs.
            curl --fail --silent --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
                --connect-timeout 10 --max-time 60 --output "$partial" "$url" 2>/dev/null || status=$?
            if [ "$status" -eq 0 ] &&
                printf '%s  %s\n' "$sha" "$partial" | sha256sum --check --strict --quiet >/dev/null 2>&1; then
                mv "$partial" "$dest"
                exit 0
            fi
            observed=$(sha256sum < "$partial")
            observed=${observed%% *}
            bytes=$(wc -c < "$partial" | tr -d '[:space:]')
            printf 'lxml source %s%s attempt=%s/3 curl_status=%s bytes=%s sha256=%s expected=%s\n' \
                "${dest##*/}" "$label" "$attempt" "$status" "$bytes" "$observed" "$sha" >&2
            if [ "$attempt" -lt 3 ]; then sleep 1; fi
            attempt=$((attempt + 1))
        done
        index=$((index + 1))
    done
    exit 1
)

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
# zlib.net intermittently answers automated clients with a small page, so the
# identical release asset from the zlib repository is the verified fallback.
fetch https://zlib.net/fossils/zlib-1.3.2.tar.gz \
    bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16 \
    lxml-6.1.3/libs/zlib-1.3.2.tar.gz \
    https://github.com/madler/zlib/releases/download/v1.3.2/zlib-1.3.2.tar.gz
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
