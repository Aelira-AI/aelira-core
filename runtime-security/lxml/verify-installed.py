"""Verify one installed source wheel, its RECORD and distro XML linkage."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata as md
import json
from pathlib import Path
import re
import subprocess
import zipfile

from lxml import etree


def run(*args: str) -> str:
    return subprocess.run(
        args, check=True, text=True, capture_output=True
    ).stdout.strip()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    dist = md.distribution("lxml")
    assert dist.version == "6.1.3"
    assert (
        len(
            [
                d
                for d in md.distributions()
                if (d.metadata.get("Name") or "").lower() == "lxml"
            ]
        )
        == 1
    )
    assert tuple(etree.LXML_VERSION[:3]) == (6, 1, 3)
    assert tuple(etree.LIBXML_COMPILED_VERSION[:3]) == (2, 15, 2)
    assert tuple(etree.LIBXML_VERSION[:3]) == (2, 15, 2)
    assert tuple(etree.LIBXSLT_COMPILED_VERSION[:3]) == (1, 1, 45)
    assert tuple(etree.LIBXSLT_VERSION[:3]) == (1, 1, 45)
    assert (
        run("dpkg-query", "-W", "-f=${Version}", "libxml2-16")
        == "2.15.2+dfsg-0.1ubuntu0.3"
    )
    assert run("dpkg-query", "-W", "-f=${Version}", "libxslt1.1") == "1.1.45-0.1"
    root = Path(dist.locate_file(""))
    assert not (root / "lxml.libs").exists()
    assert not any(root.glob("libxml2.so.*"))
    record = dist.read_text("RECORD")
    assert record
    checked = 0
    record_rows = list(csv.reader(record.splitlines()))
    assert len(record_rows) == len({row[0] for row in record_rows})
    for filename, digest, size in record_rows:
        if not digest:
            continue
        algorithm, expected = digest.split("=", 1)
        assert algorithm == "sha256"
        payload = Path(dist.locate_file(filename)).read_bytes()
        actual = (
            base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
            .decode()
            .rstrip("=")
        )
        assert actual == expected and len(payload) == int(size), filename
        checked += 1
    wheel = list(Path("/wheels").glob("lxml-6.1.3-*.whl"))
    assert len(wheel) == 1
    with zipfile.ZipFile(wheel[0]) as archive:
        wheel_record = [
            name for name in archive.namelist() if name.endswith(".dist-info/RECORD")
        ]
        assert wheel_record == ["lxml-6.1.3.dist-info/RECORD"]
        wheel_rows = list(
            csv.reader(archive.read(wheel_record[0]).decode().splitlines())
        )
        wheel_names = {row[0] for row in wheel_rows}
        assert len(wheel_names) == len(wheel_rows)
        additions = {
            "lxml-6.1.3.dist-info/" + name
            for name in ("INSTALLER", "REQUESTED", "direct_url.json")
        }
        extra_rows = [row for row in record_rows if row[0] not in wheel_names]
        assert {row[0] for row in extra_rows if row[1]} == additions
        for filename, digest, size in extra_rows:
            if filename in additions:
                continue
            compiled = re.fullmatch(
                r"(.+)/__pycache__/([^/]+)\.cpython-314\.pyc", filename
            )
            assert compiled and not digest and not size
            assert compiled.group(1) + "/" + compiled.group(2) + ".py" in wheel_names
        assert {row[0]: row[1:] for row in record_rows if row[0] in wheel_names} == {
            row[0]: row[1:] for row in wheel_rows
        }
        for name in archive.namelist():
            if name.endswith("/RECORD"):
                continue
            assert archive.read(name) == Path(dist.locate_file(name)).read_bytes(), name
    assert checked == sum(bool(row[1]) for row in wheel_rows) + len(additions)
    assert (
        Path(dist.locate_file("lxml-6.1.3.dist-info/INSTALLER")).read_bytes()
        == b"pip\n"
    )
    assert Path(dist.locate_file("lxml-6.1.3.dist-info/REQUESTED")).read_bytes() == b""
    direct = Path(dist.locate_file("lxml-6.1.3.dist-info/direct_url.json")).read_bytes()
    url = "file:///wheels/" + wheel[0].name
    wheel_sha = sha(wheel[0])
    direct_values = (
        {
            "archive_info": {
                "hash": "sha256=" + wheel_sha,
                "hashes": {"sha256": wheel_sha},
            },
            "url": url,
        },
        {"archive_info": {}, "url": url},
    )
    assert any(
        direct == json.dumps(value, sort_keys=True).encode() for value in direct_values
    )
    addition_rows = []
    for filename, digest, size in record_rows:
        if filename in additions:
            addition_rows.append(
                {
                    "path": filename,
                    "sha256": sha(Path(dist.locate_file(filename))),
                    "size": int(size),
                }
            )
    addition_rows.sort(key=lambda row: row["path"])
    ext = Path(etree.__file__)
    needed = run("readelf", "-d", str(ext))
    for soname in ("libxml2.so.16", "libxslt.so.1", "libexslt.so.0"):
        assert f"Shared library: [{soname}]" in needed, soname
    assert "libxml2.so.2]" not in needed
    extensions = sorted((root / "lxml").rglob("*.so"))
    assert len(extensions) >= 7
    extension_hashes = {}
    libraries = {}
    for extension in extensions:
        extension_hashes[str(extension.relative_to(root))] = sha(extension)
        dynamic = run("readelf", "-d", str(extension))
        assert "libxml2.so.2]" not in dynamic
        ldd = run("ldd", str(extension))
        assert "not found" not in ldd
        for line in ldd.splitlines():
            match = re.match(r"\s*(\S+) => (/\S+) \(", line)
            if not match:
                continue
            name, target = match.groups()
            path = Path(target).resolve(strict=True)
            assert str(path).startswith(("/lib/", "/usr/lib/"))
            try:
                owner = run("dpkg-query", "-S", str(path)).split(": ", 1)[0]
            except subprocess.CalledProcessError:
                owner = run("dpkg-query", "-S", target).split(": ", 1)[0]
            candidate = {"path": str(path), "sha256": sha(path), "owner": owner}
            assert name not in libraries or libraries[name] == candidate
            libraries[name] = candidate
    assert {"libxml2.so.16", "libxslt.so.1", "libexslt.so.0"} <= libraries.keys()
    assert "libxml2-16" in libraries["libxml2.so.16"]["owner"]
    print(
        json.dumps(
            {
                "distribution": "lxml",
                "version": dist.version,
                "record_hashes_verified": checked,
                "record_additions": addition_rows,
                "wheel_sha256": wheel_sha,
                "extensions_sha256": extension_hashes,
                "compiled_libxml": etree.LIBXML_COMPILED_VERSION,
                "runtime_libxml": etree.LIBXML_VERSION,
                "compiled_libxslt": etree.LIBXSLT_COMPILED_VERSION,
                "runtime_libxslt": etree.LIBXSLT_VERSION,
                "libraries": libraries,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
