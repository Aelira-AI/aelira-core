#!/usr/bin/env python3
"""Prepare the exact cJSON backport in a new directory, without downloading."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

INPUTS = {
    "cjson_1.7.19-2.dsc": (1929, "4c2a8c0418ca719fc4f7039d2a889b67d820da2a288a9fb2c8e9c863cdf01aff"),
    "cjson_1.7.19.orig.tar.gz": (356247, "7fa616e3046edfa7a28a32d5f9eacfd23f92900fe1f8ccd988c1662f30454562"),
    "cjson_1.7.19-2.debian.tar.xz": (5484, "eb82fa71fc33ae882af35ba0a7f5588584f834683848ebfed9f1e14cde6ee344"),
}
REPAIR_INPUTS = {
    "ubuntu-1.7.19-security-backport.patch": (53720, "6e2d15d4e4296875cf41b4ebcadcf8c88e39376fd51b4419dba16592c840ba78"),
    "changelog-entry.txt": (742, "fb4476e682c888f34ad9089288e5aa51a089d6d37d0546fb5766b2ca686b6b77"),
}
ORIGINAL = {
    "cJSON.c": "298581a04a36c0165da4b0aade235c23088cb2faa58651d720ea2f3706ed0b0d",
    "cJSON_Utils.c": "bf3092696763a9a446c39365d7c35f23e5acfdca1bd1da33ef66c1161f9243e9",
}
PATCHED = {
    "cJSON.c": "b8c86c757520247c4bfd7e8ccd41f51fe3e9cde163d3c280208df8b8823c95c3",
    "cJSON_Utils.c": "fdad7b6223b403dff824a2db1712c0221c380a4615aaf9d4f5b678676f12a757",
}
OUTPUTS = ("cjson_1.7.19-2+aelira3.dsc", "cjson_1.7.19-2+aelira3.debian.tar.xz",
           "cjson_1.7.19.orig.tar.gz")


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
    source = output / "cjson-1.7.19-aelira"
    env = dict(os.environ, LC_ALL="C", TZ="UTC", SOURCE_DATE_EPOCH="1791849600")
    with (output / "build-private.log").open("xb") as log:
        def run(args, cwd):
            subprocess.run(args, cwd=cwd, env=env, check=True, timeout=180,
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        run(["dpkg-source", "-x", "cjson_1.7.19-2.dsc", source.name], output)
        verify_tree(source, ORIGINAL)
        patches = source / "debian/patches"
        (patches / "aelira-security-backport.patch").write_bytes(
            repairs["ubuntu-1.7.19-security-backport.patch"])
        series = patches / "series"
        original_series = read_regular(series, 65536)
        if b"aelira-security-backport.patch" in original_series:
            raise ValueError("Backport is already in the series")
        series.write_bytes(original_series.rstrip(b"\n") + b"\naelira-security-backport.patch\n")
        run(["dpkg-source", "--before-build", "."], source)
        verify_tree(source, PATCHED)
        changelog = source / "debian/changelog"
        changelog.write_bytes(repairs["changelog-entry.txt"] + read_regular(changelog, 1024 * 1024))
        run(["dpkg-source", "-b", "."], source)
    artifacts = {}
    for name in OUTPUTS:
        data = read_regular(output / name, 1024 * 1024)
        artifacts[name] = {"sha256": digest(data), "size": len(data)}
    receipt = {
        "schema": "aelira-cjson-source-v1", "status": "pass",
        "version": "1.7.19-2+aelira3",
        "inputs": {name: {"sha256": digest(data), "size": len(data)}
                   for name, data in {**originals, **repairs}.items()},
        "patched_sources": PATCHED, "artifacts": artifacts,
        "scope": "Exact source preparation; signed-index authentication and native acceptance are separate",
    }
    (output / "source-receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = prepare(args.input, args.out)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"schema": "aelira-cjson-source-v1", "status": "fail",
                          "error_type": type(error).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
