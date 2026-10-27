#!/usr/bin/env python3
"""Download the pinned Piper voice using only Python's standard library."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

VOICE_ASSETS = (
    (
        "en_US-lessac-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx?download=true",
        "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
    ),
    (
        "en_US-lessac-medium.onnx.json",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json?download=true",
        "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
    ),
)
# Waits between the three download attempts.
RETRY_DELAYS = (1, 2)


def _copy_hashed(source, staged: Path) -> str:
    digest = hashlib.sha256()
    with staged.open("wb") as target:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            target.write(chunk)
    return digest.hexdigest()


def fetch_asset(filename: str, url: str, expected_sha256: str, staged: Path) -> None:
    """Write one asset to staged, accepting only bytes with the pinned SHA-256.

    A copy named by its SHA-256 in the build-inputs cache is used first and
    must pass the same check. Otherwise url gets three attempts.
    """
    inputs = Path(os.environ.get("AELIRA_BUILD_INPUTS", "/build-inputs"))
    cached = inputs / "sha256" / expected_sha256
    if cached.is_file():
        with cached.open("rb") as source:
            if _copy_hashed(source, staged) == expected_sha256:
                print(f"{filename} cache=hit", file=sys.stderr)
                return
        print(f"{filename} cache=mismatch", file=sys.stderr)
    for attempt, delay in enumerate((*RETRY_DELAYS, None), start=1):
        try:
            with urlopen(url, timeout=60) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"HTTP {response.status} downloading {filename}")
                if _copy_hashed(response, staged) == expected_sha256:
                    return
            error: Exception = ValueError(f"SHA-256 mismatch for {filename}")
        except (OSError, http.client.HTTPException) as failure:
            error = failure
        if delay is None:
            raise error
        print(
            f"{filename} attempt {attempt}/3 failed: {type(error).__name__}",
            file=sys.stderr,
        )
        time.sleep(delay)


def download_voice(
    output_dir: Path,
    *,
    assets: tuple[tuple[str, str, str], ...] = VOICE_ASSETS,
    epoch: int = 0,
) -> None:
    """Verify every asset before replacing final files; discard failed downloads."""
    output_dir.mkdir(parents=True, exist_ok=True)
    # Stage on the destination filesystem so each final rename is atomic.
    with tempfile.TemporaryDirectory(prefix=".piper-", dir=output_dir) as staging:
        for filename, url, expected_sha256 in assets:
            staged = Path(staging) / filename
            fetch_asset(filename, url, expected_sha256, staged)
            # Do not persist download time or random temporary filenames in images.
            staged.chmod(0o644)
            os.utime(staged, (epoch, epoch))
        for filename, _, _ in assets:
            os.replace(Path(staging) / filename, output_dir / filename)
    os.utime(output_dir, (epoch, epoch))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "piper-voices",
    )
    args = parser.parse_args()
    download_voice(args.output_dir, epoch=int(os.environ.get("SOURCE_DATE_EPOCH", "0")))


if __name__ == "__main__":
    main()
