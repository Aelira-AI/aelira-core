"""Expose the image's pinned Chromium through Pa11y's stable path."""

from __future__ import annotations

import os
from pathlib import Path


def _browser() -> Path:
    """Return the pinned Chrome for Testing build, else Playwright's Chromium."""
    pinned = os.environ.get("AELIRA_CHROMIUM_EXECUTABLE")
    if pinned:
        if not Path(pinned).is_file():
            raise RuntimeError(f"AELIRA_CHROMIUM_EXECUTABLE is not a file: {pinned}")
        return Path(pinned)

    browser_cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browser_cache:
        raise RuntimeError(
            "AELIRA_CHROMIUM_EXECUTABLE or PLAYWRIGHT_BROWSERS_PATH is required"
        )
    browsers = sorted(Path(browser_cache).glob("chromium-*/**/chrome"))
    if len(browsers) != 1 or not browsers[0].is_file():
        raise RuntimeError(
            f"Expected one Playwright Chromium executable, found {len(browsers)}"
        )
    return browsers[0]


def main() -> None:
    configured_path = os.environ.get("PA11Y_CHROMIUM_PATH")
    if not configured_path:
        raise RuntimeError("PA11Y_CHROMIUM_PATH is required")

    link = Path(configured_path)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(_browser())


if __name__ == "__main__":
    main()
