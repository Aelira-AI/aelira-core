"""Which Chromium executable the scanner's Playwright launches.

The API image ships one hash-pinned Chrome for Testing build (see
scripts/chrome-for-testing.json) and names it in AELIRA_CHROMIUM_EXECUTABLE,
because Playwright's own bundled Chromium can trail Chrome stable. Every
Playwright launch that touches page content spreads these options so no code
path falls back to an unpatched browser. Without the variable, which is the
normal state for local development, Playwright's own Chromium is used.
"""

import os
from pathlib import Path
from typing import Dict

CHROMIUM_EXECUTABLE_ENV = "AELIRA_CHROMIUM_EXECUTABLE"


def chromium_launch_options() -> Dict[str, str]:
    """Return ``executable_path`` for Playwright launches, or nothing.

    A configured path that is not a file is an error rather than a silent
    fallback: falling back would quietly run the browser this setting exists
    to replace.
    """
    configured = os.environ.get(CHROMIUM_EXECUTABLE_ENV, "").strip()
    if not configured:
        return {}
    if not Path(configured).is_file():
        raise RuntimeError(
            f"{CHROMIUM_EXECUTABLE_ENV} does not point to a file: {configured}"
        )
    return {"executable_path": configured}
