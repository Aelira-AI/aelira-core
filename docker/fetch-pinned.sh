# Verified download helper shared by the image's source builders. Source it,
# then call: fetch URL SHA256 DEST [FALLBACK_URL...]
#
# A file named by its SHA-256 under $AELIRA_BUILD_INPUTS/sha256/ is used
# first. CI passes its cache of pinned inputs in as the build-inputs build
# context; without it the directory is empty. The cached copy is accepted only
# when its bytes match the pin, so a missing, stale or altered entry falls
# through to the network addresses with the same check.

fetch() (
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
    # A pin that is not 64 lowercase hex digits can never name a cache entry.
    case "$sha" in
        *[!0-9a-f]*) cached= ;;
        *) cached="${AELIRA_BUILD_INPUTS:-/build-inputs}/sha256/$sha" ;;
    esac
    if [ "${#sha}" -eq 64 ] && [ -n "$cached" ] && [ -f "$cached" ]; then
        if cp "$cached" "$partial" &&
            printf '%s  %s\n' "$sha" "$partial" | sha256sum --check --strict --quiet >/dev/null 2>&1; then
            mv "$partial" "$dest"
            printf 'source %s cache=hit\n' "${dest##*/}" >&2
            exit 0
        fi
        observed=$(sha256sum < "$partial")
        printf 'source %s cache=mismatch sha256=%s expected=%s\n' \
            "${dest##*/}" "${observed%% *}" "$sha" >&2
    fi
    index=0
    for url in "$first" "$@"; do
        # Name fallbacks by position only: addresses can carry credentials.
        label=
        if [ "$index" -gt 0 ]; then label=" fallback=$index/$fallbacks"; fi
        attempt=1
        while [ "$attempt" -le 3 ]; do
            : > "$partial"
            status=0
            # Three attempts, each abandoned after a minute below 1 KiB/s or 15
            # minutes in total, plus two one-second waits. Some release hosts
            # take 20 seconds to send the first byte, and the largest archive
            # is about 100 MB. Suppress response and error text: it can
            # contain proxy credentials or URLs.
            curl --fail --silent --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
                --connect-timeout 30 --speed-limit 1024 --speed-time 60 --max-time 900 \
                --output "$partial" "$url" 2>/dev/null || status=$?
            if [ "$status" -eq 0 ] &&
                printf '%s  %s\n' "$sha" "$partial" | sha256sum --check --strict --quiet >/dev/null 2>&1; then
                mv "$partial" "$dest"
                exit 0
            fi
            observed=$(sha256sum < "$partial")
            observed=${observed%% *}
            bytes=$(wc -c < "$partial" | tr -d '[:space:]')
            printf 'source %s%s attempt=%s/3 curl_status=%s bytes=%s sha256=%s expected=%s\n' \
                "${dest##*/}" "$label" "$attempt" "$status" "$bytes" "$observed" "$sha" >&2
            if [ "$attempt" -lt 3 ]; then sleep 1; fi
            attempt=$((attempt + 1))
        done
        index=$((index + 1))
    done
    exit 1
)
