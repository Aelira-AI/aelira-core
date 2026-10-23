#!/bin/sh
# CVE-2026-88807: XRenderQueryFormats must reject more subpixel entries than
# screens. The harness is runtime-security/xrender/control-template.c, adapted
# to include the extracted function.
name=xrender-query-formats tree=$1 work=$2 dir=$3
source_file=src/Xrender.c
start='Status\nXRenderQueryFormats (Display *dpy)'
end='\n}\n'
includes="-I$tree/src -I$tree/include -I$tree/include/X11/extensions"
baseline_error='AddressSanitizer: heap-buffer-overflow'
. "$(dirname "$0")/../../../control-run.sh"
