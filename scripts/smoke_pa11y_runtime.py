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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.scanners.pa11y_scanner import Pa11yScanner

EXPECTED_PA11Y_VERSION = "9.0.1"
SUPPORTED_NODE_MAJORS = {24}
ROOT = Path(__file__).resolve().parents[1]
FIXTURE_RELATIVE_PATH = "scripts/fixtures/pa11y-smoke.html"


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


def main() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Pa11y smoke test must run as the final non-root user")

    node_major = int(
        _output(
            ["node", "--eval", "process.stdout.write(process.versions.node)"]
        ).split(".", 1)[0]
    )
    if node_major not in SUPPORTED_NODE_MAJORS:
        raise RuntimeError(f"Node {node_major} is unsupported by Pa11y 9")

    pa11y_version = _output(["pa11y", "--version"])
    if pa11y_version != EXPECTED_PA11Y_VERSION:
        raise RuntimeError(
            f"Expected Pa11y {EXPECTED_PA11Y_VERSION}, found {pa11y_version}"
        )

    config_path = Path(os.environ["PA11Y_CONFIG_PATH"])
    chromium_path = Path(os.environ["PA11Y_CHROMIUM_PATH"])
    if not config_path.is_file() or not chromium_path.resolve().is_file():
        raise RuntimeError("Pa11y config or Playwright Chromium is missing")

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
