#!/usr/bin/env python3
"""Replay signed Ubuntu Packages hashes for every added/upgraded builder deb."""
import hashlib
from pathlib import Path
import re
import subprocess

provenance = Path("/provenance")
lists = provenance / "apt-lists"
debs = provenance / "toolchain-debs"
subprocess.run(
    ["sha256sum", "-c", str(provenance / "apt-index-sha256.txt")],
    cwd=lists, check=True, stdout=subprocess.DEVNULL,
)
subprocess.run(
    ["sha256sum", "-c", str(provenance / "toolchain-debs.sha256")],
    cwd=debs, check=True, stdout=subprocess.DEVNULL,
)
expected = {}
for line in (provenance / "toolchain-delta.tsv").read_text().splitlines():
    package, version, architecture = line.split("\t")
    expected[(package.split(":")[0], version, architecture)] = None
for deb in sorted(debs.glob("*.deb")):
    fields = subprocess.check_output(
        ["dpkg-deb", "-f", str(deb), "Package", "Version", "Architecture"],
        text=True,
    ).splitlines()
    key = tuple(field.split(": ", 1)[1] for field in fields)
    if key not in expected:
        raise RuntimeError(f"Archive absent from package delta: {deb.name}")
    if expected[key] is not None:
        raise RuntimeError(f"Duplicate builder archive: {deb.name}")
    expected[key] = hashlib.sha256(deb.read_bytes()).hexdigest()
if any(value is None for value in expected.values()):
    raise RuntimeError("Missing builder package archives")

verified_releases = set()
matched = set()
for index in sorted(lists.glob("*_Packages*")):
    match = re.fullmatch(
        r"(.+_dists_[^_]+)_([^_]+)_binary-([^_]+)_Packages(?:\..+)?",
        index.name,
    )
    if match is None:
        raise RuntimeError(f"Unrecognized Packages index: {index.name}")
    release = lists / f"{match.group(1)}_InRelease"
    if release not in verified_releases:
        subprocess.run(
            ["gpgv", "--keyring", str(provenance / "ubuntu-archive-keyring.gpg"),
             str(release)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        verified_releases.add(release)
    signed_text = release.read_text()
    if "-----BEGIN PGP SIGNATURE-----" not in signed_text:
        raise RuntimeError(f"Unsigned Release: {release.name}")
    entries = {}
    for line in signed_text.split("\nSHA256:\n", 1)[1].splitlines():
        parts = line.split()
        if len(parts) != 3:
            break
        entries[parts[2]] = (parts[0], int(parts[1]))
    meta_key = f"{match.group(2)}/binary-{match.group(3)}/Packages"
    expected_hash, expected_size = entries[meta_key]
    contents = subprocess.check_output(["/usr/lib/apt/apt-helper", "cat-file", str(index)])
    if len(contents) != expected_size or hashlib.sha256(contents).hexdigest() != expected_hash:
        raise RuntimeError(f"Signed Packages hash mismatch: {index.name}")
    for stanza in contents.decode().split("\n\n"):
        package = re.search(r"(?m)^Package: (.+)$", stanza)
        version = re.search(r"(?m)^Version: (.+)$", stanza)
        architecture = re.search(r"(?m)^Architecture: (.+)$", stanza)
        digest = re.search(r"(?m)^SHA256: ([0-9a-f]{64})$", stanza)
        if not (package and version and architecture and digest):
            continue
        key = (package.group(1), version.group(1), architecture.group(1))
        if key in expected and expected[key] == digest.group(1):
            matched.add(key)
missing = set(expected) - matched
if missing:
    raise RuntimeError(f"Builder archives absent from signed Packages indexes: {sorted(missing)}")
print(f"ubuntu_binary_chain=pass packages={len(matched)} releases={len(verified_releases)}")
