#!/bin/sh
# Replace stock Debian packages with the rebuilt backports in the same image
# layer that first installed them, so no layer holds the unpatched libraries.
#
# Usage: install-backports.sh runtime|dev
#
# /tmp/debian-backports/SOURCE/ holds each build's .deb files, backport.conf
# and receipt.txt. The conf line named by the mode lists the binary packages
# the image must contain. Any other binary package from the same source that
# is already installed is replaced too, so no source is left at mixed
# versions. Receipts are kept in /usr/share/doc/aelira-debian-backports/.
set -eu

mode=$1
case "$mode" in runtime|dev) ;; *) echo 'usage: install-backports.sh runtime|dev' >&2; exit 1 ;; esac
root=/tmp/debian-backports
arch=$(dpkg --print-architecture)
installed() { dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null | grep -q '^ii'; }

sources=$(find "$root" -mindepth 2 -maxdepth 2 -name receipt.txt -exec dirname {} \; | sort)
test -n "$sources"
debs=""
for dir in $sources; do
    conf="$dir/backport.conf"
    version=$(awk '$1 == "rebuilt_version" { print $2 }' "$conf")
    test "$(sed -n 's/^rebuilt_version=//p' "$dir/receipt.txt")" = "$version"
    test "$(sed -n 's/^architecture=//p' "$dir/receipt.txt")" = "$arch"
    (cd "$dir" && sed -n 's/^package_sha256=\([^ ]*\) \([a-f0-9]*\)$/\2  \1/p' receipt.txt \
        | sha256sum --check --strict --quiet)
    wanted=$(awk -v m="$mode" '$1 == m { $1 = ""; print }' "$conf")
    test -n "$wanted"
    for package in $(awk '$1 == "binary" { print $2 }' "$conf"); do
        if printf ' %s ' $wanted | grep -q " $package " || installed "$package"; then
            deb=$(find "$dir" -maxdepth 1 -name "${package}_${version#*:}_*.deb")
            test "$(printf '%s\n' "$deb" | grep -c .)" -eq 1
            debs="$debs $deb"
        fi
    done
done
# shellcheck disable=SC2086
apt-get install -y --no-install-recommends $debs

for dir in $sources; do
    conf="$dir/backport.conf"
    source=$(awk '$1 == "source" { print $2 }' "$conf")
    version=$(awk '$1 == "rebuilt_version" { print $2 }' "$conf")
    for package in $(awk -v m="$mode" '$1 == m { $1 = ""; print }' "$conf"); do
        installed "$package" || { echo "$package is not installed" >&2; exit 1; }
    done
    for package in $(awk '$1 == "binary" { print $2 }' "$conf"); do
        if installed "$package"; then
            test "$(dpkg-query -W -f='${Version}' "$package")" = "$version" || {
                echo "$package is not at $version" >&2; exit 1; }
        fi
    done
    sed -n 's/^library=\([^ ]*\) \([^ ]*\) \([a-f0-9]*\)$/\1 \2 \3/p' "$dir/receipt.txt" | \
        while read -r package path hash; do
            if installed "$package"; then
                printf '%s  %s\n' "$hash" "$path" | sha256sum --check --strict --quiet
            fi
        done
    # A binary package of this source that the recipe does not ship would
    # otherwise stay at the stock version unnoticed.
    dpkg-query -W -f='${Package}\t${source:Package}\t${Version}\t${db:Status-Abbrev}\n' | \
        awk -F'\t' -v s="$source" -v v="$version" '$2 == s && $4 ~ /^ii/ && $3 != v { print; bad=1 } END { exit bad }' || {
        echo "packages from source $source are installed at another version" >&2; exit 1; }
    mkdir -p "/usr/share/doc/aelira-debian-backports/$source"
    cp "$dir/receipt.txt" "/usr/share/doc/aelira-debian-backports/$source/"
done
rm -rf "$root"
