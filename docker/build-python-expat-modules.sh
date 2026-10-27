#!/bin/sh
# Rebuild only CPython's Expat extension pair against Debian's shared Expat.
# The interpreter, libpython, headers, config and every other official file
# come from the pinned official Python image.
set -eu

out="$1"
. "$(dirname -- "$0")/fetch-pinned.sh"
work=/tmp/python-expat-build
archive="$work/Python-3.14.8.tar.xz"
if [ -e "$work" ] || [ -e "$out" ]; then
    echo "Python Expat build paths already exist" >&2
    exit 1
fi
mkdir -p "$work" "$out"
trap 'rm -rf "$work"' EXIT

fetch https://www.python.org/ftp/python/3.14.8/Python-3.14.8.tar.xz \
    c2215904f02b175596dc49351585104f4bc20341e1c47378b26a2c274360ce73 \
    "$archive"
tar -xJf "$archive" -C "$work"
cd "$work/Python-3.14.8"

# Match the official image's CPython configure options. This make invocation
# builds only the two extension targets; it does not replace official Python.
SOURCE_DATE_EPOCH=0 PYTHONHASHSEED=0 \
    ./configure --build="$(gcc -dumpmachine)" \
        --enable-loadable-sqlite-extensions --enable-optimizations \
        --enable-option-checking=fatal --enable-shared --with-lto \
        --with-ensurepip --with-system-expat
suffix="$(python3-config --extension-suffix)"
SOURCE_DATE_EPOCH=0 PYTHONHASHSEED=0 \
    make -j2 "Modules/pyexpat${suffix}" "Modules/_elementtree${suffix}"
cp "Modules/pyexpat${suffix}" "Modules/_elementtree${suffix}" "$out/"
