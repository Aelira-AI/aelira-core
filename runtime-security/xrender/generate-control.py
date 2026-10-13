#!/usr/bin/env python3
"""Generate sanitizer controls from the final patched source function."""
import hashlib
import json
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text()
out = Path(sys.argv[2])
start = source.index("Status\nXRenderQueryFormats (Display *dpy)")
end = source.index("\nint\nXRenderQuerySubpixelOrder", start)
function = source[start:end]
guard = "\t(rep.numSubpixel <= rep.numScreens) &&\n"
assert function.count(guard) == 1
template = Path(__file__).with_name("control-template.c").read_text()
assert template.count("@EXACT_FUNCTION@") == 1
(out / "xrender-bound-control.c").write_text(template.replace("@EXACT_FUNCTION@", function))
(out / "xrender-baseline-control.c").write_text(template.replace("@EXACT_FUNCTION@", function.replace(guard, "")))
(out / "control-source.json").write_text(json.dumps({
    "sourceSha256": hashlib.sha256(source.encode()).hexdigest(),
    "functionSha256": hashlib.sha256(function.encode()).hexdigest(),
    "baselineTransformation": "Remove only upstream rep.numSubpixel <= rep.numScreens guard",
    "scope": "Exact final source function with mocked protocol reply; installed consumer is separate",
}, indent=2) + "\n")
