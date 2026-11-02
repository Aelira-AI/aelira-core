#!/usr/bin/env python3
"""Freeze PyPI release metadata and exact source/build wheels over TLS."""

import hashlib
from pathlib import Path
import sys
from urllib.request import urlopen

from strict_json import loads

dest = Path(sys.argv[1])
EXPECTED_DIGESTS = {
    "pillow-13.0.0.tar.gz": "cdcd1ba258178f7356d74170c9a81ba4eab6a5d55748bc402a5a2a6fe6be3510",
    "pip-26.2.1-py3-none-any.whl": "71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e",
    "setuptools-84.0.0-py3-none-any.whl": "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670",
    "pybind11-3.1.0-py3-none-any.whl": "b8488090f8acffbcb6b5d6a85571a6827a0a2981ffb75e5a0b27b87c4a6b7dd0",
}
products = (
    ("pillow", "13.0.0", ".tar.gz"),
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
    filename = entry["filename"]
    assert filename in EXPECTED_DIGESTS, f"Unexpected or unpinned filename: {filename}"
    expected_digest = EXPECTED_DIGESTS[filename]
    assert entry["digests"]["sha256"] == expected_digest, (
        f"PyPI reported digest {entry['digests']['sha256']} differs from committed {expected_digest}"
    )
    with urlopen(entry["url"], timeout=90) as response:
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    assert digest == expected_digest, (
        f"Downloaded payload digest {digest} differs from committed {expected_digest}"
    )
    assert digest == entry["digests"]["sha256"]
    (dest / f"{name}-{version}.json").write_bytes(metadata)
    (dest / entry["filename"]).write_bytes(payload)
    print(f"{name} {version} {entry['filename']} {digest}")
