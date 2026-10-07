"""Run Pa11y's bundled HTML_CodeSniffer on a connection-guarded browser page.

The Pa11y CLI creates its own Chromium context, so it cannot scan untrusted
URLs safely. This wrapper loads the same pinned HTMLCS runner script into the
WebScanner-style guarded Playwright context and returns Pa11y's issue schema.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from src.security.browser_ssrf import (
    BROWSER_EGRESS_ARGS,
    BrowserNetworkIsolation,
    BrowserScanIncompleteError,
    assert_browser_channels_safe,
    install_browser_ssrf_guard,
    resolve_browser_navigation_url,
)

logger = logging.getLogger(__name__)
PA11Y_VERSION = "9.0.1"
HTMLCS_VERSION = "2.5.1"


class Pa11yScanError(BrowserScanIncompleteError):
    """The secondary engine did not produce a complete, verified result."""


class Pa11yUnsafeNetworkError(Pa11yScanError):
    """A requested scan would bypass the guarded browser."""


@dataclass
class Pa11yIssue:
    code: str
    type: str
    selector: str
    context: str
    message: str
    type_code: int
    runner: str


@dataclass
class Pa11yResult:
    url: str
    total_issues: int
    issues_by_severity: Dict[str, int]
    issues: List[Pa11yIssue]
    engine: str = "htmlcs"
    runner: str = "htmlcs"
    scan_duration_ms: Optional[int] = None
    page_title: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "total_issues": self.total_issues,
            "issues_by_severity": self.issues_by_severity,
            "issues": [
                {
                    "code": issue.code,
                    "type": issue.type,
                    "selector": issue.selector,
                    "context": issue.context,
                    "message": issue.message,
                    "type_code": issue.type_code,
                    "runner": issue.runner,
                }
                for issue in self.issues
            ],
            "engine": self.engine,
            "runner": self.runner,
            "scan_duration_ms": self.scan_duration_ms,
            "page_title": self.page_title,
        }


_RUN_HTMLCS = r"""([standard, timeoutMs]) => new Promise((resolve, reject) => {
    if (!window.HTMLCS || typeof window.HTMLCS.process !== 'function') {
        reject(new Error('HTMLCS unavailable'));
        return;
    }
    const timer = setTimeout(() => reject(new Error('HTMLCS timed out')), timeoutMs);
    const selector = (element) => {
        if (!element || element.nodeType !== 1) return 'html';
        if (element.id) return '#' + CSS.escape(element.id);
        const parts = [];
        for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
            let part = node.tagName.toLowerCase();
            if (node.parentElement) {
                const siblings = Array.from(node.parentElement.children)
                    .filter(sibling => sibling.tagName === node.tagName);
                if (siblings.length > 1) {
                    part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
                }
            }
            parts.unshift(part);
        }
        return parts.join(' > ');
    };
    try {
        window.HTMLCS.process(standard, document, error => {
            clearTimeout(timer);
            if (error) {
                reject(new Error('HTMLCS evaluation failed'));
                return;
            }
            resolve(window.HTMLCS.getMessages().map(issue => ({
                code: String(issue.code || ''),
                type_code: Number(issue.type),
                message: String(issue.msg || ''),
                selector: selector(issue.element),
                context: issue.element && issue.element.outerHTML
                    ? issue.element.outerHTML.slice(0, 500) : ''
            })));
        });
    } catch (_) {
        clearTimeout(timer);
        reject(new Error('HTMLCS evaluation failed'));
    }
})"""


class Pa11yScanner:
    """A Pa11y-compatible HTMLCS scanner with guarded browser networking."""

    def __init__(
        self,
        timeout: int = 60,
        pa11y_bin: str = "pa11y",
        config_path: Optional[str] = None,
        *,
        htmlcs_script_path: Optional[str] = None,
        chromium_executable: Optional[str] = None,
        allow_trusted_local_file: bool = False,
    ):
        self.timeout = timeout
        self.pa11y_bin = pa11y_bin
        self.config_path = config_path or os.getenv(
            "PA11Y_CONFIG_PATH",
            str(Path(__file__).resolve().parents[2] / "config" / "pa11y.json"),
        )
        self.htmlcs_script_path = htmlcs_script_path
        self.chromium_executable = chromium_executable
        self.allow_trusted_local_file = allow_trusted_local_file

    def _bundled_htmlcs_script(self) -> Path:
        if self.htmlcs_script_path:
            script = Path(self.htmlcs_script_path)
            if not script.is_file():
                raise Pa11yScanError("HTML_CodeSniffer script is unavailable")
            return script

        binary = shutil.which(self.pa11y_bin)
        if not binary:
            raise Pa11yScanError("Pa11y runtime is unavailable")
        resolved = Path(binary).resolve()
        package = next(
            (
                parent
                for parent in resolved.parents
                if (parent / "package.json").is_file()
                and _package_matches(parent / "package.json", "pa11y", PA11Y_VERSION)
            ),
            None,
        )
        if package is None:
            raise Pa11yScanError("Pinned Pa11y runtime is unavailable")
        for dependency_root in (package / "node_modules", package.parent):
            dependency = dependency_root / "html_codesniffer"
            script = dependency / "build" / "HTMLCS.js"
            if script.is_file() and _package_matches(
                dependency / "package.json", "html_codesniffer", HTMLCS_VERSION
            ):
                return script
        raise Pa11yScanError("Pinned HTML_CodeSniffer is unavailable")

    def _browser_launch(self) -> tuple[str, list[str]]:
        try:
            config = json.loads(Path(self.config_path).read_text())
            launch = config["chromeLaunchConfig"]
            executable = self.chromium_executable or launch["executablePath"]
            args = launch.get("args", [])
            if not Path(executable).is_file() or not isinstance(args, list):
                raise ValueError
            return executable, args
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise Pa11yScanError(
                "Guarded browser configuration is unavailable"
            ) from exc

    def _scan_guarded(self, url: str, standard: str) -> Pa11yResult:
        started = time.monotonic()
        script = self._bundled_htmlcs_script()
        executable, args = self._browser_launch()
        blocked = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=executable,
                    args=[*args, *BROWSER_EGRESS_ARGS],
                    headless=True,
                    timeout=min(self.timeout * 1000, 30000),
                )
                isolation = None
                try:
                    isolation = BrowserNetworkIsolation()
                    context = isolation.new_context(browser)
                    blocked = install_browser_ssrf_guard(context)
                    page = context.new_page()
                    navigation_url = resolve_browser_navigation_url(url, context)
                    page.goto(
                        navigation_url,
                        wait_until="networkidle",
                        timeout=min(self.timeout * 1000, 30000),
                    )
                    if blocked:
                        raise BrowserScanIncompleteError(
                            "Required browser resource blocked"
                        )
                    # Evaluate the trusted local bundle through automation, as
                    # Pa11y does. A script tag would obey the site's CSP and
                    # make a valid page appear unscannable.
                    page.evaluate(
                        "window.__aeliraDefine = window.define; window.define = undefined"
                    )
                    try:
                        page.evaluate(script.read_text())
                    finally:
                        page.evaluate(
                            "window.define = window.__aeliraDefine; delete window.__aeliraDefine"
                        )
                    remaining_ms = max(
                        1,
                        self.timeout * 1000 - int((time.monotonic() - started) * 1000),
                    )
                    raw_issues = page.evaluate(_RUN_HTMLCS, [standard, remaining_ms])
                    assert_browser_channels_safe(page, blocked)
                    title = page.title()
                finally:
                    try:
                        browser.close()
                    finally:
                        if isolation is not None:
                            isolation.close()
        except BrowserScanIncompleteError:
            raise
        except Exception as exc:
            if blocked:
                raise Pa11yScanError("Required browser resource blocked") from exc
            logger.error("Guarded HTML_CodeSniffer scan failed: %s", type(exc).__name__)
            raise Pa11yScanError("HTML_CodeSniffer scan did not complete") from exc

        if not isinstance(raw_issues, list):
            raise Pa11yScanError("HTML_CodeSniffer returned an invalid result")
        severities = {1: "error", 2: "warning", 3: "notice"}
        counts = {"error": 0, "warning": 0, "notice": 0}
        issues = []
        for raw in raw_issues:
            if not isinstance(raw, dict) or raw.get("type_code") not in severities:
                raise Pa11yScanError("HTML_CodeSniffer returned an invalid issue")
            severity = severities[raw["type_code"]]
            counts[severity] += 1
            issues.append(
                Pa11yIssue(
                    code=str(raw.get("code", "")),
                    type=severity,
                    selector=str(raw.get("selector", "")),
                    context=str(raw.get("context", "")),
                    message=str(raw.get("message", "")),
                    type_code=raw["type_code"],
                    runner="htmlcs",
                )
            )
        logger.info("Guarded HTML_CodeSniffer scan completed; issues=%s", len(issues))
        return Pa11yResult(
            url=url,
            total_issues=len(issues),
            issues_by_severity=counts,
            issues=issues,
            engine="htmlcs",
            runner="htmlcs",
            scan_duration_ms=int((time.monotonic() - started) * 1000),
            page_title=title,
        )

    async def scan(
        self, url: str, runner: str = "htmlcs", standard: str = "WCAG2AA"
    ) -> Pa11yResult:
        """Run the pinned HTMLCS engine in a connection-guarded browser."""
        parsed = urlsplit(url)
        if runner != "htmlcs" or standard not in {"WCAG2A", "WCAG2AA", "WCAG2AAA"}:
            raise Pa11yScanError("Unsupported guarded secondary engine")
        if parsed.scheme not in {"http", "https"} and not (
            self.allow_trusted_local_file
            and parsed.scheme == "file"
            and parsed.netloc in {"", "localhost"}
        ):
            raise Pa11yUnsafeNetworkError("Unsupported scan URL")
        return await asyncio.to_thread(self._scan_guarded, url, standard)

    async def verify_installation(self) -> bool:
        try:
            self._bundled_htmlcs_script()
            self._browser_launch()
            return True
        except Pa11yScanError:
            logger.warning("Guarded HTML_CodeSniffer runtime is unavailable")
            return False


def _package_matches(path: Path, name: str, version: str) -> bool:
    try:
        package = json.loads(path.read_text())
        return package.get("name") == name and package.get("version") == version
    except (OSError, ValueError):
        return False
