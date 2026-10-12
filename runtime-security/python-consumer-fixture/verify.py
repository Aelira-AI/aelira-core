"""Verify the official-wheel state before separately frozen component replacement."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import subprocess


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pins(path: Path) -> dict[str, str]:
    result = {}
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, version = line.split("==", 1)
        name = name.lower().replace("_", "-")
        assert name not in result
        result[name] = version
    assert result
    return result


def check_record(package: str) -> int:
    distribution = importlib.metadata.distribution(package)
    record = distribution.read_text("RECORD")
    assert record is not None
    count = 0
    for filename, expected, size in csv.reader(record.splitlines()):
        if not expected:
            continue
        algorithm, encoded = expected.split("=", 1)
        assert algorithm == "sha256"
        data = Path(distribution.locate_file(filename)).read_bytes()
        actual = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        )
        assert actual == encoded and len(data) == int(size)
        count += 1
    assert count > 10
    return count


def linked(extension: Path) -> dict[str, dict[str, str]]:
    lines = subprocess.run(
        ["ldd", str(extension)], check=True, capture_output=True, text=True, timeout=10
    ).stdout.splitlines()
    assert all("not found" not in line for line in lines)
    result = {}
    for line in lines:
        if " => " not in line:
            continue
        name, target = line.strip().split(" => ", 1)
        path = Path(target.split(" (", 1)[0]).resolve(strict=True)
        assert str(path).startswith(("/lib/", "/usr/lib/", "/opt/venv/"))
        result[name] = {
            "sha256": digest(path),
            "location": "wheel-private" if "/opt/venv/" in str(path) else "system",
        }
    return result


def main() -> None:
    public = pins(Path("/fixture/requirements.txt"))
    selected = pins(Path("/fixture/pins.txt"))
    selected.update({"pillow": "12.3.0", "psycopg2-binary": "2.9.13"})
    assert all(public.get(name) == version for name, version in selected.items())
    assert all(
        importlib.metadata.version(name) == version
        for name, version in selected.items()
    )
    site = Path("/opt/venv/lib/python3.14/site-packages")
    private_dirs = {
        name: site / name for name in ("pillow.libs", "psycopg2_binary.libs")
    }
    assert all(path.is_dir() and any(path.iterdir()) for path in private_dirs.values())
    from PIL import _imaging
    from psycopg2 import _psycopg

    loaded = {
        "pillow": linked(Path(_imaging.__file__)),
        "psycopg2": linked(Path(_psycopg.__file__)),
    }
    assert any(
        value["location"] == "wheel-private" for value in loaded["pillow"].values()
    )
    assert any(
        value["location"] == "wheel-private" for value in loaded["psycopg2"].values()
    )
    inventory = json.loads(Path("/fixture/source-inventory.json").read_text())
    expected = {item["path"]: item["sha256"] for item in inventory}
    modules = (
        "src.education.remediation.pdf_remediator",
        "src.education.handwritten_math_suitability",
    )
    module_hashes = {}
    for name in modules:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve(strict=True)
        assert path.is_relative_to(Path("/app/src"))
        relative = str(path.relative_to(Path("/app")))
        assert digest(path) == expected[relative]
        module_hashes[name] = expected[relative]
    import src.education.remediation.pdf_remediator as pdf

    assert pdf.HAS_PYMUPDF and pdf.HAS_PIKEPDF
    packages = {}
    for name in (
        "libpq5",
        "libssl3t64",
        "libtiff6",
        "libavif16",
        "libraqm0",
        "fonts-urw-base35",
    ):
        packages[name] = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", name],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    keyring = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
    assert digest(keyring) == digest(
        Path("/fixture/frozen/keyring/ubuntu-archive-keyring.gpg")
    )
    print(
        json.dumps(
            {
                "passed": True,
                "state": "official-wheel-before-replacement",
                "selected_public_pins": len(selected),
                "record_entries": {
                    "pillow": check_record("pillow"),
                    "psycopg2-binary": check_record("psycopg2-binary"),
                },
                "private_library_dirs_present": sorted(private_dirs),
                "loaded_modules_sha256": module_hashes,
                "loaded_libraries": loaded,
                "apt_versions": packages,
                "ubuntu_archive_keyring_sha256": digest(keyring),
                "pdf_optional_ocr_import": pdf.HAS_OCRMYPDF,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
