"""Verify the frozen Ubuntu DEBs against signed Release and Packages indexes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def run(*args: str) -> bytes:
    return subprocess.run(args, check=True, stdout=subprocess.PIPE).stdout


def fields(stanza: str) -> dict[str, str]:
    return dict(re.findall(r"^([A-Za-z0-9-]+): (.*)$", stanza, re.MULTILINE))


def main() -> None:
    root = Path(sys.argv[1]).resolve(strict=True)
    lists = root / "apt-lists"
    keyring = root / "keyring" / "ubuntu-archive-keyring.gpg"
    assert keyring.is_file() and keyring.stat().st_size > 1024
    # The pinned build image is the trust anchor. Replay uses the retained bytes.
    assert (
        keyring.read_bytes()
        == Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg").read_bytes()
    )
    release_rows = []
    package_rows: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    releases: dict[str, dict[str, tuple[str, int]]] = {}
    for release in sorted(lists.glob("*_InRelease")):
        # This is the exact Signed-By keyring in the pinned Ubuntu base.
        run("gpgv", "--keyring", str(keyring), str(release))
        body = release.read_text()
        assert "-----BEGIN PGP SIGNED MESSAGE-----" in body
        sha_block = body.split("\nSHA256:\n", 1)[1]
        entries: dict[str, tuple[str, int]] = {}
        for line in sha_block.splitlines():
            match = re.fullmatch(r" ([0-9a-f]{64})\s+(\d+)\s+(\S+)", line)
            if not match:
                break
            entries[match.group(3)] = (match.group(1), int(match.group(2)))
        assert entries
        releases[release.name.removesuffix("_InRelease")] = entries
        release_rows.append(
            {
                "file": release.name,
                "sha256": hashlib.sha256(release.read_bytes()).hexdigest(),
                "signed_indexes": len(entries),
            }
        )
    assert release_rows, "missing signed Release files"
    index_rows = []
    for index in sorted(lists.glob("*_Packages*")):
        name = index.name
        match = re.fullmatch(
            r"(.+_dists_[^_]+)_([^_]+)_(binary-[^_]+)_Packages(?:\..+)?", name
        )
        assert match, f"unrecognized package index {name}"
        release_prefix, component, binary = match.groups()
        path = f"{component}/{binary}/Packages"
        expected, size = releases[release_prefix][path]
        data = run("/usr/lib/apt/apt-helper", "cat-file", str(index))
        assert len(data) == size and hashlib.sha256(data).hexdigest() == expected, name
        index_rows.append(
            {
                "file": name,
                "release": release_prefix + "_InRelease",
                "path": path,
                "sha256": expected,
                "size": size,
            }
        )
        for stanza in data.decode().split("\n\n"):
            item = fields(stanza)
            if (
                not {"Package", "Version", "Architecture", "SHA256", "Size", "Filename"}
                <= item.keys()
            ):
                continue
            key = (item["Package"], item["Version"], item["Architecture"])
            package_rows.setdefault(key, []).append(item)
    assert index_rows, "missing package indexes"
    deb_rows = []
    for deb in sorted((root / "debs").glob("*.deb")):
        metadata = fields(
            run(
                "dpkg-deb", "-f", str(deb), "Package", "Version", "Architecture"
            ).decode()
        )
        package, version, architecture = (
            metadata[key] for key in ("Package", "Version", "Architecture")
        )
        key = (package, version, architecture)
        digest = hashlib.sha256(deb.read_bytes()).hexdigest()
        matches = [
            p
            for p in package_rows.get(key, [])
            if p["SHA256"] == digest and int(p["Size"]) == deb.stat().st_size
        ]
        assert matches, f"DEB absent from authenticated index: {deb.name}"
        deb_rows.append(
            {
                "file": deb.name,
                "package": package,
                "version": version,
                "architecture": architecture,
                "sha256": digest,
                "size": deb.stat().st_size,
                "signed_index_filename": matches[0]["Filename"],
            }
        )
    assert deb_rows, "missing DEB inventory"
    print(
        json.dumps(
            {
                "keyring_sha256": hashlib.sha256(keyring.read_bytes()).hexdigest(),
                "release": release_rows,
                "indexes": index_rows,
                "debs": deb_rows,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
