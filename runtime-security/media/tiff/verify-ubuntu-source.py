#!/usr/bin/env python3
"""Replay the Ubuntu signed Sources -> DSC -> source-archive chain offline."""
import hashlib
from pathlib import Path
import re
import subprocess

lists = Path("/provenance/apt-lists")
source = Path("/source-input")
keyring = Path("/provenance/ubuntu-archive-keyring.gpg")
subprocess.run(
    ["sha256sum", "-c", "/provenance/apt-index-sha256.txt"],
    cwd=lists, check=True, stdout=subprocess.DEVNULL,
)
source_identity = None
verified_releases = set()
for index in sorted(lists.glob("*_Sources*")):
    match = re.fullmatch(r"(.+_dists_[^_]+)_([^_]+)_source_Sources(?:\..+)?", index.name)
    if match is None:
        raise RuntimeError(f"Unrecognized Sources index: {index.name}")
    release = lists / f"{match.group(1)}_InRelease"
    if not release.is_file():
        raise RuntimeError(f"Missing signed Release for {index.name}")
    if release not in verified_releases:
        subprocess.run(["gpgv", "--keyring", str(keyring), str(release)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        verified_releases.add(release)
    signed_text = release.read_text()
    if "-----BEGIN PGP SIGNATURE-----" not in signed_text:
        raise RuntimeError(f"Unsigned Release: {release.name}")
    sha_section = signed_text.split("\nSHA256:\n", 1)[1]
    entries = {}
    for line in sha_section.splitlines():
        parts = line.split()
        if len(parts) != 3:
            break
        entries[parts[2]] = (parts[0], int(parts[1]))
    meta_key = f"{match.group(2)}/source/Sources"
    expected_hash, expected_size = entries[meta_key]
    contents = subprocess.check_output(["/usr/lib/apt/apt-helper", "cat-file", str(index)])
    if len(contents) != expected_size or hashlib.sha256(contents).hexdigest() != expected_hash:
        raise RuntimeError(f"Signed Sources hash mismatch: {index.name}")
    for stanza in contents.decode().split("\n\n"):
        if not stanza.startswith("Package: tiff\n") or "\nVersion: 4.7.0-3ubuntu5\n" not in stanza:
            continue
        checksums = stanza.split("\nChecksums-Sha256:\n", 1)[1]
        checked = set()
        identity = {}
        for line in checksums.splitlines():
            parts = line.split()
            if len(parts) != 3:
                break
            digest, size, filename = parts
            item = source / filename
            if not item.is_file() or item.stat().st_size != int(size):
                raise RuntimeError(f"Missing or wrong source size: {filename}")
            if hashlib.sha256(item.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"Source hash mismatch: {filename}")
            checked.add(filename)
            identity[filename] = (digest, size)
        required = {
            "tiff_4.7.0-3ubuntu5.dsc",
            "tiff_4.7.0.orig.tar.bz2",
            "tiff_4.7.0-3ubuntu5.debian.tar.xz",
        }
        if not required <= checked:
            raise RuntimeError("Incomplete signed Ubuntu source stanza")
        if source_identity is not None and source_identity != identity:
            raise RuntimeError("Conflicting signed Ubuntu tiff source stanzas")
        source_identity = identity
if source_identity is None:
    raise RuntimeError("Ubuntu tiff 4.7.0-3ubuntu5 absent from signed Sources")
print(f"ubuntu_source_chain=pass releases={len(verified_releases)}")
