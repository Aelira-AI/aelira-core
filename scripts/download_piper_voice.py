#!/usr/bin/env python3
"""Download the pinned Piper voice using only Python's standard library."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
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
            digest = hashlib.sha256()
            with urlopen(url, timeout=60) as response, staged.open("wb") as target:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"HTTP {response.status} downloading {filename}")
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    target.write(chunk)
            if digest.hexdigest() != expected_sha256:
                raise ValueError(f"SHA-256 mismatch for {filename}")
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
