"""Prove the final image user can launch Pa11y against a local fixture."""

from __future__ import annotations

import json
import asyncio
import os
import subprocess
import tempfile
import threading
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.scanners.pa11y_scanner import Pa11yScanner
from src.security.browser_runtime import chromium_launch_options

EXPECTED_PA11Y_VERSION = "9.0.1"
EXPECTED_BROWSERS_VERSION = "3.2.4"
SUPPORTED_NODE_MAJORS = {24}
# Node 24.22.1 (2026-10-21) is the security release for CVE-2026-105481,
# CVE-2026-105482, CVE-2026-105483, CVE-2026-106069, CVE-2026-106073,
# CVE-2026-106074 and CVE-2026-111497, and carries OpenSSL 3.5.9. It also
# drops emSign ECC Root CA - G3 from the built-in root store (#726).
MINIMUM_NODE = (24, 22, 1)
MINIMUM_NODE_OPENSSL = (3, 5, 9)
ROOT = Path(__file__).resolve().parents[1]
CHROME_MANIFEST = ROOT / "scripts" / "chrome-for-testing.json"
FIXTURE_RELATIVE_PATH = "scripts/fixtures/pa11y-smoke.html"
PA11Y_MODULES = Path("/opt/pa11y-runtime/node_modules")
FORBIDDEN_RUNTIME_PACKAGES = ("basic-ftp", "extract-zip")


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


def _output(args: list[str]) -> str:
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _pinned_chrome_version() -> str:
    return json.loads(CHROME_MANIFEST.read_text())["version"]


def main() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Pa11y smoke test must run as the final non-root user")

    node_version = _output(
        ["node", "--eval", "process.stdout.write(process.versions.node)"]
    )
    node_major = _version_tuple(node_version)[0]
    if node_major not in SUPPORTED_NODE_MAJORS:
        raise RuntimeError(f"Node {node_major} is unsupported by Pa11y 9")
    if _version_tuple(node_version) < MINIMUM_NODE:
        raise RuntimeError(
            f"Node {node_version} predates the security release "
            f"{'.'.join(map(str, MINIMUM_NODE))}"
        )
    node_openssl = _output(
        ["node", "--eval", "process.stdout.write(process.versions.openssl)"]
    )
    if _version_tuple(node_openssl) < MINIMUM_NODE_OPENSSL:
        raise RuntimeError(f"Node's OpenSSL {node_openssl} is below 3.5.9")

    pa11y_version = _output(["pa11y", "--version"])
    if pa11y_version != EXPECTED_PA11Y_VERSION:
        raise RuntimeError(
            f"Expected Pa11y {EXPECTED_PA11Y_VERSION}, found {pa11y_version}"
        )

    browsers_path = PA11Y_MODULES / "@puppeteer" / "browsers" / "package.json"
    browsers_version = json.loads(browsers_path.read_text())["version"]
    if browsers_version != EXPECTED_BROWSERS_VERSION:
        raise RuntimeError(
            f"Expected @puppeteer/browsers {EXPECTED_BROWSERS_VERSION}, "
            f"found {browsers_version}"
        )
    for package in FORBIDDEN_RUNTIME_PACKAGES:
        installed = sorted(PA11Y_MODULES.rglob(f"{package}/package.json"))
        if installed:
            raise RuntimeError(
                f"Unexpected Pa11y runtime package {package}: {installed}"
            )

    config_path = Path(os.environ["PA11Y_CONFIG_PATH"])
    chromium_path = Path(os.environ["PA11Y_CHROMIUM_PATH"])
    if not config_path.is_file() or not chromium_path.resolve().is_file():
        raise RuntimeError("Pa11y config or pinned Chromium is missing")
    # Pa11y and every Playwright launch must run the one pinned build.
    pinned_executable = Path(os.environ["AELIRA_CHROMIUM_EXECUTABLE"])
    if chromium_path.resolve() != pinned_executable.resolve():
        raise RuntimeError(
            f"Pa11y runs {chromium_path.resolve()}, not {pinned_executable}"
        )
    chrome_version = _output([str(pinned_executable), "--version"])
    if _pinned_chrome_version() not in chrome_version.split():
        raise RuntimeError(
            f"Expected Chrome for Testing {_pinned_chrome_version()}, "
            f"found {chrome_version!r}"
        )

    fixture = ROOT / FIXTURE_RELATIVE_PATH
    if not fixture.is_file():
        raise RuntimeError(f"Pa11y smoke fixture is missing: {FIXTURE_RELATIVE_PATH}")

    handler = partial(_QuietHandler, directory=str(fixture.parent))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/{fixture.name}"
        # Chromium writes host-specific caches during launch. Keep them in a
        # disposable home so this build-time proof cannot perturb the final
        # image layer or break reproducible builds.
        with tempfile.TemporaryDirectory(prefix="aelira-pa11y-smoke-") as smoke_home:
            smoke_env = os.environ.copy()
            smoke_env["HOME"] = smoke_home
            smoke_env["XDG_CACHE_HOME"] = str(Path(smoke_home) / "cache")
            smoke_env["XDG_CONFIG_HOME"] = str(Path(smoke_home) / "config")
            completed = subprocess.run(
                ["pa11y", "--config", str(config_path), "--reporter", "json", url],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
                env=smoke_env,
            )
            # Prove the production secondary-engine path also launches with
            # its pinned HTMLCS script and a guarded browser. This fixture has
            # no external resource dependencies.
            with patch.dict(os.environ, smoke_env):
                guarded = asyncio.run(
                    Pa11yScanner(
                        timeout=60,
                        config_path=str(config_path),
                        allow_trusted_local_file=True,
                    ).scan(fixture.as_uri(), runner="htmlcs")
                )
            if guarded.engine != "htmlcs" or guarded.runner != "htmlcs":
                raise RuntimeError("Guarded HTML_CodeSniffer smoke scan failed")
            # The scanner's Playwright launches must also land on the pin.
            with patch.dict(os.environ, smoke_env):
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=True, **chromium_launch_options()
                    )
                    try:
                        launched_version = browser.version
                    finally:
                        browser.close()
            if launched_version != _pinned_chrome_version():
                raise RuntimeError(
                    f"Playwright launched Chromium {launched_version}, "
                    f"expected {_pinned_chrome_version()}"
                )
        if completed.returncode not in {0, 2}:
            raise RuntimeError(
                f"Pa11y browser launch failed ({completed.returncode}): "
                f"{completed.stderr.strip()}"
            )
        report = json.loads(completed.stdout)
        if not isinstance(report, list):
            raise RuntimeError("Pa11y smoke output was not a JSON issue list")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


if __name__ == "__main__":
    main()
