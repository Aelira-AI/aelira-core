#!/usr/bin/env python3
"""Freeze PyPI release metadata and exact source/build wheels over TLS."""

import hashlib
from pathlib import Path
import sys
from urllib.request import urlopen

from strict_json import loads

dest = Path(sys.argv[1])
products = (
    ("pillow", "12.3.0", ".tar.gz"),
    ("pip", "26.2.1", "-py3-none-any.whl"),
    ("setuptools", "84.0.0", "-py3-none-any.whl"),
    ("pybind11", "3.1.0", "-py3-none-any.whl"),
)
for name, version, suffix in products:
    with urlopen(
        f"https://pypi.org/pypi/{name}/{version}/json", timeout=30
    ) as response:
        metadata = response.read()
    data = loads(metadata)
    assert data["info"]["version"] == version
    matches = [entry for entry in data["urls"] if entry["filename"].endswith(suffix)]
    assert len(matches) == 1, (name, [entry["filename"] for entry in matches])
    entry = matches[0]
    with urlopen(entry["url"], timeout=90) as response:
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    assert digest == entry["digests"]["sha256"]
    if name == "pillow":
        assert entry["filename"] == "pillow-12.3.0.tar.gz"
        assert (
            digest == "3b8182a766685eaa002637e28b4ec8d6b18819a0c71f579bf0dbaa5830297cce"
        )
    (dest / f"{name}-{version}.json").write_bytes(metadata)
    (dest / entry["filename"]).write_bytes(payload)
    print(f"{name} {version} {entry['filename']} {digest}")
