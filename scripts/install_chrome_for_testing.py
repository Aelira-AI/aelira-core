#!/usr/bin/env python3
"""Install the hash-pinned Chrome for Testing build that Aelira drives.

Playwright pins its own Chromium, and that build can trail Chrome stable by
weeks. The web scanner points a browser at pages that visitors choose, so the
image and the release gates use the exact Chrome for Testing build named in
scripts/chrome-for-testing.json instead: a fixed version, fetched over HTTPS,
checked against a pinned SHA-256 and size before anything is unpacked.

Usage: install_chrome_for_testing.py --dest DIR [--arch amd64|arm64]
The browser is unpacked so that DIR/chrome is the executable. The platform
defaults to the host's. Standard library only, so any stock Python can run it.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import http.client
import json
import os
import platform
import shutil
import stat
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

MANIFEST = Path(__file__).with_name("chrome-for-testing.json")
CHUNK = 1024 * 1024
EXECUTABLE = "chrome"
# Waits between the three download attempts.
RETRY_DELAYS = (1, 2)


def load_manifest(path: Path = MANIFEST) -> dict:
    manifest = json.loads(path.read_text())
    for platform_name, entry in manifest["platforms"].items():
        if len(entry["sha256"]) != 64 or not entry["archive"].endswith(".zip"):
            raise ValueError(f"Malformed manifest entry for {platform_name}")
    return manifest


def select_platform(manifest: dict, arch: str | None) -> tuple[str, dict]:
    """Return the manifest platform for a Docker-style or host architecture."""
    arch = (arch or platform.machine()).lower()
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(arch, arch)
    for name, entry in manifest["platforms"].items():
        if entry["docker_arch"] == arch:
            return name, entry
    raise RuntimeError(f"No pinned Chrome for Testing build for architecture {arch}")


def archive_url(manifest: dict, platform_name: str, entry: dict) -> str:
    return (
        f"{manifest['base_url']}/{manifest['version']}/"
        f"{platform_name}/{entry['archive']}"
    )


def build_inputs_dir() -> Path:
    """The CI cache of pinned build inputs, when the build was given one."""
    return Path(os.environ.get("AELIRA_BUILD_INPUTS", "/build-inputs"))


def _copy_verified(source, target: Path, entry: dict) -> str | None:
    """Copy source to target; describe the mismatch unless size and SHA-256 match."""
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as handle:
        while chunk := source.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
            handle.write(chunk)
    if size == entry["size"] and digest.hexdigest() == entry["sha256"]:
        return None
    return (
        f"Chrome for Testing archive does not match the pin: "
        f"got {size} bytes, sha256 {digest.hexdigest()}; "
        f"expected {entry['size']} bytes, sha256 {entry['sha256']}"
    )


def download_verified(url: str, entry: dict, target: Path) -> None:
    """Write the pinned archive to target, failing unless size and SHA-256 match.

    A copy named by its SHA-256 in the build-inputs cache is used first and
    must pass the same check. Otherwise url gets three attempts.
    """
    cached = build_inputs_dir() / "sha256" / entry["sha256"]
    if cached.is_file():
        with cached.open("rb") as source:
            problem = _copy_verified(source, target, entry)
        if problem is None:
            print(f"{target.name} cache=hit", file=sys.stderr)
            return
        print(f"{target.name} cache=mismatch: {problem}", file=sys.stderr)
    for attempt, delay in enumerate((*RETRY_DELAYS, None), start=1):
        request = urllib.request.Request(url, headers={"User-Agent": "aelira-core"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
                problem = _copy_verified(response, target, entry)
        except (OSError, http.client.HTTPException) as error:
            problem = f"Chrome for Testing download failed: {type(error).__name__}"
        if problem is None:
            return
        if delay is None:
            raise RuntimeError(problem)
        print(f"{problem} (attempt {attempt}/3)", file=sys.stderr)
        time.sleep(delay)


def extract(archive: Path, dest: Path) -> None:
    """Unpack the single top-level directory of archive directly into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(archive) as bundle:
        names = [info.filename for info in bundle.infolist()]
        prefixes = {name.split("/", 1)[0] for name in names}
        if len(prefixes) != 1:
            raise RuntimeError(f"Expected one top-level directory, found {prefixes}")
        for info in bundle.infolist():
            relative = info.filename.split("/", 1)[1] if "/" in info.filename else ""
            if not relative:
                continue
            target = (root / relative).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError(
                    f"Archive entry escapes the destination: {info.filename}"
                )
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError(f"Unexpected symlink in archive: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle, CHUNK)
            target.chmod(0o755 if mode & stat.S_IXUSR else 0o644)
            # Archive timestamps keep the unpacked tree identical between builds.
            epoch = _archive_epoch(info)
            os.utime(target, (epoch, epoch))
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        directory.chmod(0o755)
        newest = max(
            (child.stat().st_mtime for child in directory.iterdir()), default=0
        )
        os.utime(directory, (newest, newest))
    executable = root / EXECUTABLE
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f"{executable} is missing or not executable after unpacking")


def _archive_epoch(info: zipfile.ZipInfo) -> int:
    return calendar.timegm((*info.date_time, 0, 0, 0))


def install(dest: Path, arch: str | None = None) -> Path:
    manifest = load_manifest()
    platform_name, entry = select_platform(manifest, arch)
    with tempfile.TemporaryDirectory(prefix="aelira-cft-") as scratch:
        archive = Path(scratch) / entry["archive"]
        download_verified(archive_url(manifest, platform_name, entry), entry, archive)
        extract(archive, dest)
    return dest / EXECUTABLE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--arch", help="amd64 or arm64; defaults to this host")
    args = parser.parse_args(argv)
    executable = install(args.dest, args.arch)
    print(f"Chrome for Testing {load_manifest()['version']} at {executable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
