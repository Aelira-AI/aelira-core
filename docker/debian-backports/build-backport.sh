#!/bin/sh
# Rebuild one Debian source package with reviewed security patches.
#
# Usage: build-backport.sh INPUT_DIR OUTPUT_DIR
#
# INPUT_DIR holds backport.conf, the pinned Debian source files and patches/.
# backport.conf has one whitespace-separated directive per line:
#   source NAME                     Debian source package name
#   source_version VERSION          the pinned Debian version
#   rebuilt_version VERSION         the version this build produces: the source
#                                   version, optionally Debian's binNMU suffix
#                                   (+bN, so it sorts above the installed
#                                   binaries), then +aeliraN
#   changelog_date RFC2822-DATE     date of the generated changelog entry
#   disclosure PATH                 repository document naming every CVE
#   dsc FILE SHA256                 the Debian source control file
#   input FILE SHA256               each file the .dsc lists
#   patch FILE SHA256 OFFSETS CVES  applied in order with --fuzz=0; OFFSETS is
#                                   "0" or a comma list of allowed hunk offsets;
#                                   CVES is a comma list
#   assert CVE FILE TEXT            TEXT must occur in FILE after patching
#   control NAME                    run controls/NAME/run.sh after patching
#   tests                           run the package's own build-time test
#                                   suite instead of building with nocheck
#   reproducible                    build a second time and require every
#                                   .deb and the receipt to be byte-identical
#   check NAME                      run controls/NAME/run.sh after the build,
#                                   with the built packages as fourth argument
#   profile NAME                    Debian build profile (repeatable)
#   build arch|full                 dpkg-buildpackage -B or -b
#   binary PACKAGE                  every binary package the build must emit
#   library PACKAGE SONAME_FILE     shared library recorded in the receipt
#   runtime PACKAGE...              packages installed in the runtime image
#   dev PACKAGE...                  packages installed in the developer image
#
# The output directory receives the .deb files and receipt.txt, which lists
# every input, patch and package hash and the CVEs the patches fix.
set -eu

input=$1
out=$2
conf="$input/backport.conf"
test -f "$conf"
test ! -e "$out"
mkdir -p "$out"

value() { awk -v k="$1" '$1 == k { $1 = ""; sub(/^ +/, ""); print; n++ } END { exit n != 1 }' "$conf"; }
source_name=$(value source)
# A fixed scratch path, not mktemp: compilers record build paths in debug
# information, and the GNU build-id is hashed over the unstripped objects, so
# a random path changes the shipped libraries between otherwise equal builds.
# Each builder stage is a fresh container, so the path is never shared.
work="/tmp/debian-backport-$source_name"
test ! -e "$work"
mkdir "$work"
source_version=$(value source_version)
version=$(value rebuilt_version)
changelog_date=$(value changelog_date)
build_type=$(value build)
case "$build_type" in arch) build_flag=-B ;; full) build_flag=-b ;; *) exit 1 ;; esac
case "$version" in "$source_version"+aelira[0-9]*|"$source_version"+b[0-9]*+aelira[0-9]*) ;; *) echo 'rebuilt_version must extend source_version' >&2; exit 1 ;; esac

# Every pinned file must match before anything is unpacked.
awk '$1 == "dsc" || $1 == "input" { print $3 "  " $2 } $1 == "patch" { print $3 "  patches/" $2 }' "$conf" \
    > "$work/pins.sha256"
test "$(awk '$1 == "dsc"' "$conf" | wc -l)" -eq 1
test -s "$work/pins.sha256"
(cd "$input" && sha256sum --check --strict "$work/pins.sha256")

# dpkg-source checks every file the .dsc lists against the .dsc's own
# checksums. The .dsc and its inputs were authenticated through Debian's
# signed archive index when they were pinned; see the backport document.
dsc=$(awk '$1 == "dsc" { print $2 }' "$conf")
mkdir "$work/src"
for file in $(awk '$1 == "dsc" || $1 == "input" { print $2 }' "$conf"); do
    cp "$input/$file" "$work/src/"
done
cd "$work/src"
dpkg-source --no-check -x "$dsc" tree > /dev/null
cd tree
test "$(dpkg-parsechangelog -S Source)" = "$source_name"
test "$(dpkg-parsechangelog -S Version)" = "$source_version"
tree=$(pwd)

profiles=$(awk '$1 == "profile" { print $2 }' "$conf" | paste -sd, -)
# The builder stage installs the package's declared build dependencies from
# the same pinned Debian archive as the base image. Nothing here ships.
if test "${BACKPORT_INSTALL_BUILD_DEPS:-0}" = 1; then
    arch_only=""
    test "$build_type" = arch && arch_only=--arch-only
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get build-dep -y -qq --no-install-recommends $arch_only ${profiles:+-P "$profiles"} . \
        > "$work/build-deps.log"
fi
dpkg-checkbuilddeps ${profiles:+-P"$profiles"} $(test "$build_type" = arch && echo -B)

# Format 3.0 (quilt) sources arrive with Debian's patches applied. Format 1.0
# sources whose rules use quilt apply them at build time, so apply them now:
# the reviewed patches were dry-run against the tree with them applied.
export QUILT_PATCHES=debian/patches
if test -f debian/patches/series && ! test -d .pc; then
    quilt push -a > "$work/debian-patches.log" || test $? -eq 2
fi

mkdir "$work/patch-logs"
awk '$1 == "patch" { print $2, $4 }' "$conf" > "$work/patches.list"
while read -r file offsets; do
    log="$work/patch-logs/$file.log"
    patch --batch --forward --fuzz=0 --dry-run -p1 < "$input/patches/$file" > "$log" || {
        cat "$log" >&2; echo "$file does not apply" >&2; exit 1; }
    if grep -Ei 'fuzz|FAILED|reversed|ignored' "$log" >&2; then
        echo "$file does not apply cleanly" >&2
        exit 1
    fi
    allowed=$(printf '%s\n' "$offsets" | tr ',' '\n' | sed '/^0$/d')
    found=$(sed -n 's/.*(offset \(-\{0,1\}[0-9]*\) lines\{0,1\}).*/\1/p' "$log" | sort -u)
    for offset in $found; do
        printf '%s\n' "$allowed" | grep -qx -- "$offset" || {
            echo "$file applies at unreviewed offset $offset" >&2; exit 1; }
    done
    patch --batch --forward --fuzz=0 -p1 < "$input/patches/$file" > /dev/null
done < "$work/patches.list"

awk '$1 == "assert" { $1 = ""; sub(/^ +/, ""); print }' "$conf" > "$work/asserts.list"
while read -r cve file text; do
    test "$(grep -cF -- "$text" "$file")" -eq 1 || {
        echo "$cve source assertion failed in $file" >&2; exit 1; }
done < "$work/asserts.list"

# Behavioural controls compile the exact patched source under sanitizers.
# Each must pass on the patched source and fail with only the fix removed.
for control in $(awk '$1 == "control" { print $2 }' "$conf"); do
    mkdir "$work/control-$control"
    sh "$input/controls/$control/run.sh" "$tree" "$work/control-$control" "$input/controls/$control" \
        > "$work/control-$control.log" 2>&1 || {
        tail -c 8192 "$work/control-$control.log" >&2; echo "control $control failed" >&2; exit 1; }
    grep -qx "control $control: patched passed, baseline failed" "$work/control-$control.log"
    cat "$work/control-$control.log"
done

fixed=$(awk '$1 == "patch" { print $5 }' "$conf" | tr ',' '\n' | sort -u | paste -sd, -)
{
    printf '%s (%s) trixie; urgency=high\n\n' "$source_name" "$version"
    printf '  * Backport reviewed security fixes: %s.\n\n' "$(printf '%s' "$fixed" | sed 's/,/, /g')"
    printf ' -- Aelira Security <security@aelira.ai>  %s\n\n' "$changelog_date"
    cat debian/changelog
} > "$work/changelog"
mv "$work/changelog" debian/changelog
test "$(dpkg-parsechangelog -S Version)" = "$version"

if test -n "$profiles"; then
    export DEB_BUILD_PROFILES="$(printf '%s' "$profiles" | tr ',' ' ')"
    set -- -P"$profiles"
else
    set --
fi
# Debian's own rules keep its configure options, hardening and symbols files.
# -nc keeps the reviewed patches applied; the tree is fresh from the .dsc.
nocheck=nocheck
grep -qx tests "$conf" && nocheck=""
# SOURCE_DATE_EPOCH is the generated changelog entry's date, fixed by the
# recipe. dpkg-deb clamps package file times to it; a value of 0 left the
# times of files written during the build unclamped, so .deb bytes varied.
# dpkg-deb only lowers times later than it, so a date still in the future
# would leave build times in the packages; refuse that.
source_date_epoch=$(dpkg-parsechangelog -S Timestamp)
test "$source_date_epoch" -gt 0
test "$source_date_epoch" -le "$(date +%s)" || {
    echo 'changelog_date is in the future, so package times would not be clamped' >&2; exit 1; }
DEB_BUILD_OPTIONS="$nocheck parallel=$(nproc)" SOURCE_DATE_EPOCH="$source_date_epoch" \
    dpkg-buildpackage "$build_flag" -nc -us -uc "$@" > "$work/build.log" 2>&1 || {
    tail -c 16384 "$work/build.log" >&2; exit 1; }
if test -z "$nocheck"; then
    grep -E 'tests passed|Test #|PASS|FAIL' "$work/build.log" | tail -n 40
    grep -q '100% tests passed' "$work/build.log" || {
        echo 'the package test suite did not report 100% passed' >&2; exit 1; }
fi

arch=$(dpkg --print-architecture)
multiarch=$(dpkg-architecture -qDEB_HOST_MULTIARCH)
deb_version=${version#*:}
for package in $(awk '$1 == "binary" { print $2 }' "$conf"); do
    found=""
    for candidate in "$package"_"$deb_version"_"$arch".deb "$package"_"$deb_version"_all.deb; do
        if test -f "$work/src/$candidate"; then found=$candidate; fi
    done
    test -n "$found" || { echo "missing binary package $package" >&2; exit 1; }
    cp "$work/src/$found" "$out/"
done
test "$(find "$out" -name '*.deb' | wc -l)" -eq "$(awk '$1 == "binary"' "$conf" | wc -l)"

# Behavioural checks that need the built library run against the packages.
export BACKPORT_SOURCE_VERSION="$source_version"
for control in $(awk '$1 == "check" { print $2 }' "$conf"); do
    mkdir "$work/check-$control"
    sh "$input/controls/$control/run.sh" "$tree" "$work/check-$control" "$input/controls/$control" "$out" \
        > "$work/check-$control.log" 2>&1 || {
        tail -c 8192 "$work/check-$control.log" >&2; echo "check $control failed" >&2; exit 1; }
    grep -qx "control $control: patched passed, baseline failed" "$work/check-$control.log"
    cat "$work/check-$control.log"
done

mkdir "$work/extracted"
{
    printf 'format=aelira-debian-backport-v1\n'
    printf 'source=%s\n' "$source_name"
    printf 'source_version=%s\n' "$source_version"
    printf 'rebuilt_version=%s\n' "$version"
    printf 'architecture=%s\n' "$arch"
    awk '$1 == "dsc" || $1 == "input" { print "input_sha256=" $2 " " $3 }' "$conf"
    awk '$1 == "patch" { print "patch_sha256=" $2 " " $3 " " $5 }' "$conf"
    printf '%s\n' "$fixed" | tr ',' '\n' | sed 's/^/fixed=/'
    awk '$1 == "assert" { print "assertion=" $2 " " $3 }' "$conf"
    awk '$1 == "control" || $1 == "check" { print "control=" $2 } $1 == "tests" { print "control=package-test-suite" }' "$conf"
    (cd "$out" && sha256sum ./*.deb) | awk '{ sub(/^\.\//, "", $2); print "package_sha256=" $2 " " $1 }'
    awk '$1 == "library" { print $2, $3 }' "$conf" | while read -r package file; do
        rm -rf "$work/extracted/$package"
        dpkg-deb -x "$out/${package}_${deb_version}_$arch.deb" "$work/extracted/$package"
        path="/usr/lib/$multiarch/$file"
        test -f "$work/extracted/$package$path" && ! test -L "$work/extracted/$package$path"
        printf 'library=%s %s %s\n' "$package" "$path" \
            "$(sha256sum "$work/extracted/$package$path" | cut -d' ' -f1)"
    done
} > "$out/receipt.txt"
cp "$conf" "$out/backport.conf"
cat "$out/receipt.txt"
rm -rf "$work"

# Reproducibility check: rebuild from scratch at the same path and require
# identical packages. Image builds compare manifest digests, so a package
# that differs between builds makes the whole image irreproducible.
if grep -qx reproducible "$conf" && test "${BACKPORT_REPRODUCIBILITY_PASS:-0}" != 1; then
    BACKPORT_REPRODUCIBILITY_PASS=1 sh "$0" "$input" "$out.second" > "$out.second.log" 2>&1 || {
        tail -c 8192 "$out.second.log" >&2; echo 'second build failed' >&2; exit 1; }
    if ! cmp -s "$out/receipt.txt" "$out.second/receipt.txt"; then
        diff "$out/receipt.txt" "$out.second/receipt.txt" >&2 || true
        echo "$source_name is not reproducible: a second build produced different packages" >&2
        exit 1
    fi
    (cd "$out" && sha256sum ./*.deb) > "$out.first.sha256"
    (cd "$out.second" && sha256sum ./*.deb) | cmp -s - "$out.first.sha256" || {
        echo "$source_name is not reproducible: package bytes differ" >&2; exit 1; }
    rm -rf "$out.second" "$out.second.log" "$out.first.sha256"
    echo "reproducible: $source_name second build matched every package"
fi
