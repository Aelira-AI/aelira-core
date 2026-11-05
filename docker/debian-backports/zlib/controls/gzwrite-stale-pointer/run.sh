#!/bin/sh
# CVE-2026-76844: after a failed direct write, gz_write() must drop the
# caller's buffer from the stream state so a cleared stream never derives a
# position from it. The harness compiles the exact gz_write from the patched
# tree under AddressSanitizer and UndefinedBehaviorSanitizer with a failing
# gz_comp stub; removing only the fix text reproduces the heap-buffer-overflow.
name=gzwrite-stale-pointer tree=$1 work=$2 dir=$3
source_file=gzwrite.c
start='local z_size_t gz_write'
end='\n}\n'
includes="-I$tree"
baseline_error='AddressSanitizer: heap-buffer-overflow'
. "$(dirname "$0")/../../../control-run.sh"
