"""Freeze public PyPI wheel bytes and metadata for actual Office libraries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import urllib.request


def main() -> None:
    root = Path(sys.argv[1])
    arch = sys.argv[2]
    assert arch in {"arm64", "amd64"}
    directory = root / "consumer-wheels"
    directory.mkdir()
    packages = [
        ("lxml", "6.1.3"),
        ("python-docx", "1.2.0"),
        ("python-pptx", "1.0.2"),
        ("openpyxl", "3.1.5"),
        ("pillow", "13.0.0"),
        ("XlsxWriter", "3.2.9"),
        ("typing_extensions", "4.15.0"),
        ("et_xmlfile", "2.0.0"),
    ]
    rows = []
    for package, version in packages:
        url = f"https://pypi.org/pypi/{package}/{version}/json"
        metadata = json.load(urllib.request.urlopen(url, timeout=30))
        assert metadata["info"]["version"] == version
        wheels = [
            item for item in metadata["urls"] if item["filename"].endswith(".whl")
        ]
        if package in {"lxml", "pillow"}:
            machine = "aarch64" if arch == "arm64" else "x86_64"
            wheels = [
                item
                for item in wheels
                if "cp314-cp314-manylinux" in item["filename"]
                and machine in item["filename"]
            ]
            if package == "lxml":
                # PyPI also publishes a compatible manylinux2014 wheel.
                # Select the newer glibc target explicitly for this Ubuntu base.
                wheels = [
                    item for item in wheels if "manylinux_2_26_" in item["filename"]
                ]
        else:
            wheels = [
                item for item in wheels if item["filename"].endswith("none-any.whl")
            ]
        assert len(wheels) == 1, (package, [item["filename"] for item in wheels])
        item = wheels[0]
        data = urllib.request.urlopen(item["url"], timeout=90).read()
        assert hashlib.sha256(data).hexdigest() == item["digests"]["sha256"]
        (directory / item["filename"]).write_bytes(data)
        rows.append(
            {
                "package": package,
                "version": version,
                "filename": item["filename"],
                "sha256": item["digests"]["sha256"],
                "url": item["url"],
                "metadata_url": url,
            }
        )
    (directory / "pypi-metadata.json").write_text(
        json.dumps(rows, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps({"architecture": arch, "wheels": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
