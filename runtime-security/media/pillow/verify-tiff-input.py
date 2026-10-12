#!/usr/bin/env python3
"""Bind Pillow to the independently accepted native TIFF component bytes."""

import hashlib
from pathlib import Path
import subprocess
import sys

directory = Path(sys.argv[1])
arch = sys.argv[2]
accepted = {
    "amd64": {
        "libtiff6": "222b765e38f7a8db036b3f2fc3c3f14cab5226587c9dbba84f3f66c142661b29",
        "libtiffxx6": "e83fa20d9b30aaa3c31723e8b56c3b561fea1a9471aec429139907f8f74500c2",
        "libtiff-dev": "5853d0040198a31298ecb22e78440889de7b51bf5bcbf855989a11aca2079172",
    },
    "arm64": {
        "libtiff6": "64cbba6dd3595f477b3005618693f392a860e7810f269fd929f69e529a0ca719",
        "libtiffxx6": "18f86a6a7b10748e13b2ecb864554a18b4843563b4e08f2c56c83fe3a76d6a9b",
        "libtiff-dev": "b57d7300f4b5f10f4d450c92a4cab24b18f241374ae1ff3b899850ba6a4ff37e",
    },
}
assert arch in accepted, arch
assert {p.name for p in directory.glob("*.deb")} == {
    f"{package}_4.7.2-0aelira1_{arch}.deb" for package in accepted[arch]
}
for package, digest in accepted[arch].items():
    path = directory / f"{package}_4.7.2-0aelira1_{arch}.deb"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, path
    fields = subprocess.check_output(
        ["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"], text=True
    ).splitlines()
    actual = tuple(line.split(": ", 1)[1] for line in fields)
    assert actual == (package, "4.7.2-0aelira1", arch), (path, actual)
print(f"accepted_tiff_input=pass architecture={arch} packages=3")
