#!/usr/bin/env python3
"""List, key and prefetch the pinned files that the API image build downloads.

The pins stay where they are reviewed: the `fetch URL SHA256 DEST` calls in the
docker/ source builders, scripts/chrome-for-testing.json and the Piper voice
table in scripts/download_piper_voice.py. This tool only reads them, so the CI
cache can never name a file the build would not itself accept.

    build_inputs.py list --arch amd64          one JSON object per pinned file
    build_inputs.py key --arch amd64           cache key for exactly those pins
    build_inputs.py prefetch --arch amd64 --dest DIR

`prefetch` leaves DIR/sha256/<digest> for every pin it could fetch, using the
same verifying helper as the image build, and removes anything else. A file
that cannot be fetched is reported and skipped: the build then tries the
upstream address itself, so prefetch never makes a build less likely to work.
Standard library only.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "docker" / "fetch-pinned.sh"
SOURCE_BUILDERS = (
    "docker/build-lxml-wheel.sh",
    "docker/build-pillow-wheel.sh",
    "docker/build-psycopg2-wheel.sh",
    "docker/build-pyav-wheel.sh",
    "docker/build-opencv-wheel.sh",
    "docker/build-python-expat-modules.sh",
)
CHROME_MANIFEST = ROOT / "scripts" / "chrome-for-testing.json"
PIPER_DOWNLOADER = ROOT / "scripts" / "download_piper_voice.py"
ARCHES = ("amd64", "arm64")
SHA256 = re.compile(r"[0-9a-f]{64}")
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Pin:
    sha256: str
    urls: tuple[str, ...]
    name: str
    source: str


def builder_pins(path: Path, root: Path = ROOT) -> list[Pin]:
    """Return the `fetch` calls of one source builder, continuation lines joined."""
    text = path.read_text().replace("\\\n", " ")
    pins = []
    for line in text.splitlines():
        if not line.lstrip().startswith("fetch "):
            continue
        words = shlex.split(line, comments=True)
        url, sha256, _dest, *fallbacks = words[1:]
        if not SHA256.fullmatch(sha256):
            raise ValueError(f"{path}: fetch pin is not a SHA-256: {sha256!r}")
        for address in (url, *fallbacks):
            if not address.startswith("https://"):
                raise ValueError(f"{path}: fetch address is not HTTPS: {address!r}")
        name = url.rsplit("/", 1)[-1].split("?", 1)[0]
        pins.append(Pin(sha256, (url, *fallbacks), name, str(path.relative_to(root))))
    return pins


def chrome_pin(arch: str, manifest_path: Path = CHROME_MANIFEST) -> Pin:
    manifest = json.loads(manifest_path.read_text())
    for platform_name, entry in manifest["platforms"].items():
        if entry["docker_arch"] == arch:
            url = (
                f"{manifest['base_url']}/{manifest['version']}/"
                f"{platform_name}/{entry['archive']}"
            )
            return Pin(
                entry["sha256"],
                (url,),
                entry["archive"],
                str(manifest_path.relative_to(ROOT)),
            )
    raise ValueError(f"No pinned Chrome for Testing build for {arch}")


def piper_pins(path: Path = PIPER_DOWNLOADER) -> list[Pin]:
    """Read VOICE_ASSETS without importing the downloader."""
    for node in ast.parse(path.read_text()).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "VOICE_ASSETS"
        ):
            assets = ast.literal_eval(node.value)
            return [
                Pin(sha256, (url,), filename, str(path.relative_to(ROOT)))
                for filename, url, sha256 in assets
            ]
    raise ValueError(f"{path}: VOICE_ASSETS not found")


def pins(arch: str) -> list[Pin]:
    if arch not in ARCHES:
        raise ValueError(f"Unsupported architecture: {arch}")
    found = [pin for name in SOURCE_BUILDERS for pin in builder_pins(ROOT / name)]
    found.append(chrome_pin(arch))
    found.extend(piper_pins())
    by_digest: dict[str, Pin] = {}
    for pin in found:
        if not SHA256.fullmatch(pin.sha256):
            raise ValueError(f"{pin.source}: pin is not a SHA-256: {pin.sha256!r}")
        # One digest names one set of bytes, wherever it is pinned.
        by_digest.setdefault(pin.sha256, pin)
    return sorted(by_digest.values(), key=lambda pin: pin.sha256)


def cache_key(arch: str) -> str:
    material = "".join(f"{pin.sha256}\n" for pin in pins(arch))
    return f"build-inputs-v1-{arch}-{hashlib.sha256(material.encode()).hexdigest()}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_with_helper(pin: Pin, target: Path) -> bool:
    """Fetch one pin into target through docker/fetch-pinned.sh."""
    with tempfile.TemporaryDirectory(prefix=".fetch-", dir=target.parent) as work:
        staged = Path(work) / "files" / pin.name
        staged.parent.mkdir()
        # An empty cache directory makes the helper go straight to the network,
        # and the staged name keeps its log lines readable.
        env = {**os.environ, "AELIRA_BUILD_INPUTS": work}
        result = subprocess.run(
            [
                "sh",
                "-c",
                '. "$1"; shift; fetch "$@"',
                "sh",
                str(HELPER),
                pin.urls[0],
                pin.sha256,
                str(staged),
                *pin.urls[1:],
            ],
            env=env,
            check=False,
        )
        if result.returncode != 0:
            return False
        os.replace(staged, target)
    return True


def prefetch(dest: Path, arch: str, fetch=fetch_with_helper) -> dict[str, int]:
    store = dest / "sha256"
    store.mkdir(parents=True, exist_ok=True)
    wanted = pins(arch)
    counts = {"kept": 0, "fetched": 0, "failed": 0, "removed": 0}
    names = {pin.sha256 for pin in wanted}
    for entry in sorted(store.iterdir()):
        # Anything that is not a current pin is stale; it would only grow the cache.
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
            counts["removed"] += 1
        elif entry.name not in names or not entry.is_file() or entry.is_symlink():
            entry.unlink()
            counts["removed"] += 1
    for pin in wanted:
        target = store / pin.sha256
        if target.is_file():
            if file_sha256(target) == pin.sha256:
                counts["kept"] += 1
                continue
            target.unlink()
            counts["removed"] += 1
        if (
            fetch(pin, target)
            and target.is_file()
            and file_sha256(target) == pin.sha256
        ):
            counts["fetched"] += 1
        else:
            target.unlink(missing_ok=True)
            counts["failed"] += 1
            print(
                f"build-inputs: {pin.name} not cached; the build fetches it upstream",
                file=sys.stderr,
            )
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "key", "prefetch"))
    parser.add_argument("--arch", required=True, choices=ARCHES)
    parser.add_argument("--dest", type=Path)
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="exit 3 when any pin could not be fetched",
    )
    args = parser.parse_args(argv)
    if args.command == "list":
        for pin in pins(args.arch):
            print(
                json.dumps(
                    {
                        "sha256": pin.sha256,
                        "name": pin.name,
                        "urls": list(pin.urls),
                        "source": pin.source,
                    }
                )
            )
    elif args.command == "key":
        print(cache_key(args.arch))
    else:
        if args.dest is None:
            parser.error("prefetch needs --dest")
        counts = prefetch(args.dest, args.arch)
        print("build-inputs: " + " ".join(f"{k}={v}" for k, v in counts.items()))
        if args.require_complete and counts["failed"]:
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
