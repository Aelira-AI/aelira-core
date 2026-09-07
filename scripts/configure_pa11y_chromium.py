"""Expose Playwright's versioned Chromium through Pa11y's stable image path."""

from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    configured_path = os.environ.get("PA11Y_CHROMIUM_PATH")
    browser_cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not configured_path or not browser_cache:
        raise RuntimeError(
            "PA11Y_CHROMIUM_PATH and PLAYWRIGHT_BROWSERS_PATH are required"
        )

    link = Path(configured_path)
    link.parent.mkdir(parents=True, exist_ok=True)
    browsers = sorted(Path(browser_cache).glob("chromium-*/**/chrome"))
    if len(browsers) != 1 or not browsers[0].is_file():
        raise RuntimeError(
            f"Expected one Playwright Chromium executable, found {len(browsers)}"
        )
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(browsers[0])


if __name__ == "__main__":
    main()
