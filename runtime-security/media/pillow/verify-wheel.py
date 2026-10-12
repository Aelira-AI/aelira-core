#!/usr/bin/env python3
"""Verify a source wheel's RECORD, native ELF architecture and system links."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

LINKS = {
    "_imaging": (
        "libtiff.so.6",
        "libjpeg.so.8",
        "libopenjp2.so.7",
        "libz.so.1",
        "libxcb.so.1",
    ),
    "_avif": ("libavif.so.16",),
    "_webp": ("libwebp.so.7", "libwebpmux.so.3", "libwebpdemux.so.2"),
    "_imagingcms": ("liblcms2.so.2",),
    "_imagingft": ("libfreetype.so.6", "libraqm.so.0"),
}


def validate(wheel: Path, arch: str) -> dict[str, object]:
    tag = {"arm64": "linux_aarch64", "amd64": "linux_x86_64"}[arch]
    if wheel.name != f"pillow-12.3.0-cp314-cp314-{tag}.whl":
        raise ValueError("wheel name/architecture mismatch")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(
            name.startswith("pillow.libs/") for name in names
        ):
            raise ValueError("duplicate or private wheel members")
        record = "pillow-12.3.0.dist-info/RECORD"
        metadata = "pillow-12.3.0.dist-info/METADATA"
        if b"Version: 12.3.0\n" not in archive.read(metadata):
            raise ValueError("wheel metadata version mismatch")
        rows = list(csv.reader(io.StringIO(archive.read(record).decode())))
        if len(rows) != len(names) or {row[0] for row in rows} != set(names):
            raise ValueError("wheel RECORD coverage mismatch")
        hashed = 0
        for name, digest, size in rows:
            if name == record:
                if digest or size:
                    raise ValueError("RECORD self-entry malformed")
                continue
            payload = archive.read(name)
            actual = (
                base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                .rstrip(b"=")
                .decode()
            )
            if (
                digest != f"sha256={actual}"
                or type(size) is not str
                or not size.isdecimal()
                or int(size) != len(payload)
            ):
                raise ValueError("wheel RECORD payload mismatch")
            hashed += 1
        if hashed != 119:
            raise ValueError("wheel RECORD entry count mismatch")
        machine = "AArch64" if arch == "arm64" else "Advanced Micro Devices X86-64"
        with tempfile.TemporaryDirectory() as scratch:
            for module, libraries in LINKS.items():
                matches = [
                    name
                    for name in names
                    if name.startswith(f"PIL/{module}.") and name.endswith(".so")
                ]
                if len(matches) != 1:
                    raise ValueError("wheel ELF module missing/duplicate")
                object_path = Path(scratch) / f"{module}.so"
                object_path.write_bytes(archive.read(matches[0]))
                header = subprocess.check_output(
                    ["readelf", "-h", str(object_path)], text=True
                )
                if machine not in header:
                    raise ValueError("wheel ELF architecture mismatch")
                dynamic = subprocess.check_output(
                    ["readelf", "-d", str(object_path)], text=True
                )
                if any(f"[{library}]" not in dynamic for library in libraries):
                    raise ValueError("wheel ELF system linkage mismatch")
    return {
        "record_entries": hashed,
        "architecture": arch,
        "elf_modules": len(LINKS),
        "libtiff_needed": "libtiff.so.6",
    }


if __name__ == "__main__":
    proof = validate(Path(sys.argv[1]), sys.argv[2])
    print(
        f"wheel_record=pass entries={proof['record_entries']} arch={proof['architecture']} "
        f"elf_modules={proof['elf_modules']} libtiff_needed={proof['libtiff_needed']}"
    )
