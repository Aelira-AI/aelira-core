#!/usr/bin/env python3
"""Replay signed Ubuntu index and PyPI digest chains from retained inputs."""

import hashlib
from pathlib import Path
import re
import subprocess
import sys

from strict_json import load

inputs = Path(sys.argv[1])
provenance = Path(sys.argv[2])
arch = sys.argv[3]
subprocess.run(
    [
        "python3",
        str(Path(__file__).with_name("verify-tiff-input.py")),
        str(inputs / "tiff"),
        arch,
    ],
    check=True,
)
for manifest, directory in (
    ("apt-index-sha256.txt", "apt-lists"),
    ("toolchain-debs.sha256", "apt-debs"),
):
    subprocess.run(
        ["sha256sum", "-c", str(provenance / manifest)],
        cwd=inputs / directory,
        check=True,
        stdout=subprocess.DEVNULL,
    )

expected = {}
for line in (provenance / "toolchain-delta.tsv").read_text().splitlines():
    package, version, architecture = line.split("\t")
    if package in ("libtiff6", "libtiffxx6", "libtiff-dev"):
        continue
    key = (package.split(":")[0], version, architecture)
    assert key not in expected, key
    expected[key] = None
for deb in sorted((inputs / "apt-debs").glob("*.deb")):
    fields = subprocess.check_output(
        ["dpkg-deb", "-f", str(deb), "Package", "Version", "Architecture"], text=True
    ).splitlines()
    key = tuple(field.split(": ", 1)[1] for field in fields)
    assert key in expected and expected[key] is None, (deb, key)
    expected[key] = hashlib.sha256(deb.read_bytes()).hexdigest()
assert all(expected.values()), "Missing builder package archive"

verified_releases = set()
matched = set()
lists = inputs / "apt-lists"
for index in sorted(lists.glob("*_Packages*")):
    match = re.fullmatch(
        r"(.+_dists_[^_]+)_([^_]+)_binary-([^_]+)_Packages(?:\..+)?", index.name
    )
    assert match, index.name
    release = lists / f"{match.group(1)}_InRelease"
    if release not in verified_releases:
        subprocess.run(
            [
                "gpgv",
                "--keyring",
                str(provenance / "ubuntu-archive-keyring.gpg"),
                str(release),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        verified_releases.add(release)
    signed_text = release.read_text()
    assert "-----BEGIN PGP SIGNATURE-----" in signed_text
    entries = {}
    for line in signed_text.split("\nSHA256:\n", 1)[1].splitlines():
        parts = line.split()
        if len(parts) != 3:
            break
        entries[parts[2]] = (parts[0], int(parts[1]))
    meta_key = f"{match.group(2)}/binary-{match.group(3)}/Packages"
    expected_hash, expected_size = entries[meta_key]
    content = subprocess.check_output(
        ["/usr/lib/apt/apt-helper", "cat-file", str(index)]
    )
    assert (
        len(content) == expected_size
        and hashlib.sha256(content).hexdigest() == expected_hash
    )
    for stanza in content.decode().split("\n\n"):
        fields = dict(
            re.findall(r"(?m)^(Package|Version|Architecture|SHA256): (.+)$", stanza)
        )
        key = (fields.get("Package"), fields.get("Version"), fields.get("Architecture"))
        if key in expected and expected[key] == fields.get("SHA256"):
            matched.add(key)
assert (
    set(expected) == matched
), f"Unsigned/missing package objects: {set(expected) - matched}"

python_files = set()
for name, version, suffix in (
    ("pillow", "13.0.0", ".tar.gz"),
    ("pip", "26.2.1", "-py3-none-any.whl"),
    ("setuptools", "84.0.0", "-py3-none-any.whl"),
    ("pybind11", "3.1.0", "-py3-none-any.whl"),
):
    metadata_file = inputs / "python" / f"{name}-{version}.json"
    data = load(metadata_file)
    assert data["info"]["version"] == version
    matches = [entry for entry in data["urls"] if entry["filename"].endswith(suffix)]
    assert len(matches) == 1
    entry = matches[0]
    path = inputs / "python" / entry["filename"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["digests"]["sha256"]
    if name == "pillow":
        assert entry["filename"] == "pillow-13.0.0.tar.gz"
        assert entry["digests"]["sha256"] == (
            "cdcd1ba258178f7356d74170c9a81ba4eab6a5d55748bc402a5a2a6fe6be3510"
        )
    python_files.update((metadata_file.name, path.name))
assert {p.name for p in (inputs / "python").iterdir()} == python_files
print(
    f"frozen_input_chain=pass architecture={arch} signed_packages={len(matched)} "
    f"signed_releases={len(verified_releases)} pypi_inputs=4 tiff_packages=3"
)
