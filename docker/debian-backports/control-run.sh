#!/bin/sh
# Shared sanitizer control runner, sourced by controls/NAME/run.sh.
# Requires: name, tree, work, dir, source_file, start, end, includes,
# baseline_error (text the baseline sanitizer report must contain).
set -eu
perl "$(dirname "$0")/../../../control-source.pl" "$tree/$source_file" "$start" "$end" \
    "$dir/guard.txt" "$work"
flags="-std=gnu11 -g -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer -w"
for variant in patched baseline; do
    for optimization in 0 2; do
        # shellcheck disable=SC2086
        gcc $flags -O"$optimization" $includes -DFUNCTION_FILE="\"$work/$variant.c\"" \
            "$dir/control.c" -o "$work/$variant-O$optimization"
    done
done
for optimization in 0 2; do
    ASAN_OPTIONS=detect_leaks=1 "$work/patched-O$optimization"
done
for optimization in 0 2; do
    if ASAN_OPTIONS=detect_leaks=0 "$work/baseline-O$optimization" trigger > "$work/baseline-O$optimization.log" 2>&1; then
        echo "baseline -O$optimization did not fail" >&2
        exit 1
    fi
    grep -q "$baseline_error" "$work/baseline-O$optimization.log" || {
        cat "$work/baseline-O$optimization.log" >&2; exit 1; }
done
echo "control $name: patched passed, baseline failed"
