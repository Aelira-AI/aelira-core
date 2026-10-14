#!/usr/bin/env python3
"""Prepare the exact xdg-email backport in a new directory, without downloading."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

INPUTS = {
    "xdg-utils_1.2.1.orig.tar.gz": (
        307637,
        "f6b648c064464c2636884c05746e80428110a576f8daacf46ef2e554dcfdae75",
    ),
    "xdg-utils_1.2.1-2ubuntu2.debian.tar.xz": (
        13868,
        "f18c229aa67118738323440a110e74889091d1ccd39a09d86b0ac1b376ababd0",
    ),
    "xdg-utils_1.2.1-2ubuntu2.dsc": (
        2244,
        "ef3de2ce6c505e26a76684520b8afaf64a065757203a229bc078e7555407b5d6",
    ),
}
REPAIR_INPUTS = {
    "upstream-5f3f563.patch": (
        5173,
        "f91cc8064b3f23388842134447f7ab865230ee4d294b86a45405aa6c60bfc937",
    ),
    "ubuntu-1.2.1-mailto-backport.patch": (
        1541,
        "d6e0b12a4b92a8074c33a8d558406b49157af7a0a47ea219b95beb6383487f61",
    ),
    "ubuntu-frontend-compatibility.patch": (
        2705,
        "cf6d2bfca5c184fb95fe4cad5d603ddfe58fc57886c91d7a12358005afbcd043",
    ),
    "changelog-entry.txt": (
        419,
        "4d648e5aa3b13b8bd103a50ed0e9eddee5b9e5b953e86070d9fb354b4065fa79",
    ),
}
ORIGINAL = {
    "scripts/xdg-email.in": "82a3de31c1b4b07e5ba4d246f159538326f38676aa088958cf70423c926c43a3",
    "autotests/t-xdg-email.sh": "1f56b89e8b01206477be6e8992a637e44e0a828c6acc40691667a769d1c62cbd",
}
PATCHED = {
    "scripts/xdg-email.in": "ebff44d09d67d423cdb3408528b073fe624fd06e0cf373579c0bd41454a84399",
    "autotests/t-xdg-email.sh": "b4b2a584c7d676ae37f4b39ace131f8433db13b59c834fab3acd86dec460277f",
}
OUTPUTS = (
    "xdg-utils_1.2.1-2ubuntu2+aelira2.dsc",
    "xdg-utils_1.2.1-2ubuntu2+aelira2.debian.tar.xz",
    "xdg-utils_1.2.1.orig.tar.gz",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path, limit):
    """Read bounded, non-symlink data once; use these same bytes afterwards."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Expected bounded regular file")
        data = stream.read(limit + 1)
        if len(data) > limit or len(data) != info.st_size:
            raise ValueError("File changed or exceeded its bound")
        return data


def verify_inputs(root, manifest):
    result = {}
    for name, (size, expected) in manifest.items():
        data = read_regular(root / name, size)
        if len(data) != size or digest(data) != expected:
            raise ValueError("Input identity mismatch: " + name)
        result[name] = data
    return result


def verify_tree(source, manifest):
    for name, expected in manifest.items():
        if digest(read_regular(source / name, 1024 * 1024)) != expected:
            raise ValueError("Source identity mismatch: " + name)


def prepare(inputs, output):
    if not inputs.is_absolute() or not output.is_absolute():
        raise ValueError("Absolute input and output paths required")
    if inputs.resolve() != inputs or not inputs.is_dir():
        raise ValueError("Input directory must be canonical")
    if output.parent.resolve() != output.parent or not output.parent.is_dir():
        raise ValueError("Output parent must be canonical and exist")
    if output.exists() or output.is_symlink():
        raise ValueError("Output directory must be new")
    # All inputs are checked before extraction or output creation.
    originals = verify_inputs(inputs, INPUTS)
    repairs = verify_inputs(Path(__file__).resolve().parent, REPAIR_INPUTS)
    output.mkdir(mode=0o700)
    for name, data in originals.items():
        (output / name).write_bytes(data)
    source = output / "xdg-utils-1.2.1-aelira"
    env = dict(os.environ, LC_ALL="C", TZ="UTC", SOURCE_DATE_EPOCH="1791849600")
    with (output / "build-private.log").open("xb") as log:

        def run(args, cwd):
            subprocess.run(
                args,
                cwd=cwd,
                env=env,
                check=True,
                timeout=180,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )

        run(["dpkg-source", "-x", "xdg-utils_1.2.1-2ubuntu2.dsc", source.name], output)
        verify_tree(source, ORIGINAL)
        patches = source / "debian/patches"
        series = patches / "series"
        original_series = read_regular(series, 65536)
        if original_series != b"debian-changes\nkde-6-xdg-email.patch\n":
            raise ValueError("Original Ubuntu patch series changed")
        (patches / "aelira-attachment-origin.patch").write_bytes(
            repairs["upstream-5f3f563.patch"]
        )
        (patches / "aelira-mailto-delimiters.patch").write_bytes(
            repairs["ubuntu-1.2.1-mailto-backport.patch"]
        )
        (patches / "aelira-frontend-compatibility.patch").write_bytes(
            repairs["ubuntu-frontend-compatibility.patch"]
        )
        series.write_bytes(
            original_series
            + b"aelira-attachment-origin.patch\naelira-mailto-delimiters.patch\naelira-frontend-compatibility.patch\n"
        )
        run(["dpkg-source", "--before-build", "."], source)
        verify_tree(source, PATCHED)
        changelog = source / "debian/changelog"
        changelog.write_bytes(
            repairs["changelog-entry.txt"] + read_regular(changelog, 1024 * 1024)
        )
        run(["dpkg-source", "-b", "."], source)
    artifacts = {}
    for name in OUTPUTS:
        data = read_regular(output / name, 1024 * 1024)
        artifacts[name] = {"sha256": digest(data), "size": len(data)}
    receipt = {
        "schema": "aelira-xdg-source-v1",
        "status": "pass",
        "version": "1.2.1-2ubuntu2+aelira2",
        "inputs": {
            name: {"sha256": digest(data), "size": len(data)}
            for name, data in {**originals, **repairs}.items()
        },
        "patched_sources": PATCHED,
        "artifacts": artifacts,
        "scope": "Exact source preparation; signed-index authentication and native acceptance are separate",
    }
    (output / "source-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    )
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = prepare(args.input, args.out)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {
                    "schema": "aelira-xdg-source-v1",
                    "status": "fail",
                    "error_type": type(error).__name__,
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
