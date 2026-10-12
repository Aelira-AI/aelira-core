"""Verify the source-built driver's identity, RECORD and dynamic dependencies."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess

import psycopg2


def main() -> None:
    distribution = importlib.metadata.distribution("psycopg2-binary")
    assert distribution.version == "2.9.13"
    root = Path(distribution.locate_file(""))
    assert not (root / "psycopg2_binary.libs").exists()
    record = distribution.read_text("RECORD")
    assert record is not None
    verified = 0
    for filename, digest, size in csv.reader(record.splitlines()):
        if not digest:
            continue
        algorithm, expected = digest.split("=", 1)
        assert algorithm == "sha256"
        data = Path(distribution.locate_file(filename)).read_bytes()
        actual = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        )
        assert actual == expected
        assert len(data) == int(size)
        verified += 1
    extension = Path(psycopg2._psycopg.__file__)
    linkage = subprocess.run(
        ["ldd", str(extension)], check=True, capture_output=True, text=True, timeout=10
    ).stdout
    assert "not found" not in linkage
    libraries: dict[str, str] = {}
    for line in linkage.splitlines():
        if " => " not in line:
            continue
        name, target = line.strip().split(" => ", 1)
        path = Path(target.split(" (", 1)[0])
        assert path.is_absolute()
        assert str(path).startswith(("/lib/", "/usr/lib/"))
        libraries[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert {"libpq.so.5", "libssl.so.3", "libcrypto.so.3"} <= libraries.keys()
    assert "psycopg2_binary.libs" not in linkage
    assert (
        len(
            [
                item
                for item in importlib.metadata.distributions()
                if (item.metadata.get("Name") or "").lower().replace("_", "-")
                == "psycopg2-binary"
            ]
        )
        == 1
    )
    assert not any(
        (item.metadata.get("Name") or "").lower().replace("_", "-") == "psycopg2"
        for item in importlib.metadata.distributions()
    )
    owners: dict[str, str] = {}
    for name in libraries:
        path_text = next(
            line.strip().split(" => ", 1)[1].split(" (", 1)[0]
            for line in linkage.splitlines()
            if line.strip().startswith(name + " => ")
        )
        path = str(Path(path_text).resolve(strict=True))
        result = subprocess.run(
            ["dpkg-query", "-S", path],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        owners[name] = result.stdout.strip().split(": ", 1)[0]
    print(
        json.dumps(
            {
                "package": "psycopg2-binary",
                "version": distribution.version,
                "record_hashes_verified": verified,
                "extension_sha256": hashlib.sha256(extension.read_bytes()).hexdigest(),
                "compiled_libpq": psycopg2.__libpq_version__,
                "runtime_libpq": psycopg2.extensions.libpq_version(),
                "loaded_library_sha256": libraries,
                "loaded_library_packages": owners,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
