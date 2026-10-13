#!/usr/bin/env python3
"""Verify cJSON input bytes against an Ubuntu-signed Sources index offline."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def verify(lists, inputs):
    # This keyring comes from the pinned Ubuntu builder, not the source bundle.
    keyring = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
    expected_files = {"cjson_1.7.19-2.dsc", "cjson_1.7.19.orig.tar.gz",
                      "cjson_1.7.19-2.debian.tar.xz"}
    rows = []
    releases = {}
    for index in sorted(lists.glob("*_Sources*")):
        match = re.fullmatch(r"(.+_dists_[^_]+)_([^_]+)_source_Sources(?:\..+)?", index.name)
        if match is None or index.is_symlink() or not index.is_file():
            raise ValueError("Unrecognized source index")
        release = lists / (match.group(1) + "_InRelease")
        if release not in releases:
            if release.is_symlink() or release.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Invalid signed Release")
            result = subprocess.run(["gpgv", "--keyring", str(keyring), "--output", "-", str(release)],
                                    check=True, capture_output=True, timeout=30)
            text = result.stdout.decode("utf-8")
            section = text.split("\nSHA256:\n", 1)[1]
            entries = {}
            for line in section.splitlines():
                parts = line.split()
                if len(parts) != 3:
                    break
                digest, size, filename = parts
                if not re.fullmatch(r"[a-f0-9]{64}", digest) or filename in entries:
                    raise ValueError("Invalid signed checksum record")
                entries[filename] = (digest, int(size))
            releases[release] = entries
        digest, size = releases[release][match.group(2) + "/source/Sources"]
        if size > 256 * 1024 * 1024:
            raise ValueError("Sources index exceeds its bound")
        contents = subprocess.check_output(["/usr/lib/apt/apt-helper", "cat-file", str(index)], timeout=60)
        if len(contents) != size or hashlib.sha256(contents).hexdigest() != digest:
            raise ValueError("Sources index differs from signed Release")
        for stanza in contents.decode("utf-8").split("\n\n"):
            if not stanza.startswith("Package: cjson\n") or "\nVersion: 1.7.19-2\n" not in stanza:
                continue
            checked = {}
            section = stanza.split("\nChecksums-Sha256:\n", 1)[1]
            for line in section.splitlines():
                parts = line.split()
                if len(parts) != 3:
                    break
                source_digest, source_size, name = parts
                if name not in expected_files or name in checked:
                    raise ValueError("Unexpected source archive record")
                file = inputs / name
                if file.is_symlink() or not file.is_file() or file.stat().st_size != int(source_size):
                    raise ValueError("Source archive size mismatch")
                if hashlib.sha256(file.read_bytes()).hexdigest() != source_digest:
                    raise ValueError("Source archive hash mismatch")
                checked[name] = {"sha256": source_digest, "size": int(source_size)}
            if set(checked) != expected_files:
                raise ValueError("Incomplete signed source stanza")
            if rows and rows[0]["files"] != checked:
                raise ValueError("Conflicting source identities")
            rows.append({"index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
                         "release_sha256": hashlib.sha256(release.read_bytes()).hexdigest(), "files": checked})
    if not rows:
        raise ValueError("cjson 1.7.19-2 absent from authenticated Sources")
    return {"schema": "aelira-cjson-ubuntu-source-v1", "status": "pass", "records": rows,
            "keyring_sha256": hashlib.sha256(keyring.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lists", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.lists, args.input), sort_keys=True))
