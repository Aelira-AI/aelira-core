"""Guarded HTML_CodeSniffer evidence for the secondary web scan engine."""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.scanners.pa11y_scanner import (
    Pa11yScanError,
    Pa11yScanner,
    Pa11yUnsafeNetworkError,
)
from src.security.browser_ssrf import BrowserScanIncompleteError


@pytest.mark.asyncio
async def test_unsupported_runner_refuses_before_browser_launch(monkeypatch):
    scanner = Pa11yScanner()
    monkeypatch.setattr(
        scanner,
        "_scan_guarded",
        lambda *args: pytest.fail("unsupported runner reached browser"),
    )
    with pytest.raises(Pa11yScanError, match="Unsupported guarded secondary engine"):
        await scanner.scan("https://example.test", runner="axe")


@pytest.mark.asyncio
async def test_untrusted_file_scheme_refuses_before_browser_launch(monkeypatch):
    scanner = Pa11yScanner()
    monkeypatch.setattr(
        scanner, "_scan_guarded", lambda *args: pytest.fail("file reached browser")
    )
    with pytest.raises(Pa11yUnsafeNetworkError):
        await scanner.scan("file:///tmp/untrusted.html")


def test_guard_failure_is_an_incomplete_scan():
    assert issubclass(Pa11yScanError, BrowserScanIncompleteError)


def _start_server(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.mark.e2e
@pytest.mark.skipif(
    not (os.getenv("RUN_E2E_TESTS") or os.getenv("RUN_CONTROLLED_BROWSER_TESTS")),
    reason="Guarded HTMLCS fixture requires Chromium and pinned Pa11y",
)
class TestGuardedHtmlcs:
    @pytest.fixture()
    def scanner_and_servers(self, monkeypatch):
        from src.security import browser_ssrf
        from src.utils import public_http, security

        victim_hits = []

        class Victim(BaseHTTPRequestHandler):
            def do_GET(self):
                victim_hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"private")

            def log_message(self, *args):
                pass

        victim, victim_thread = _start_server(Victim)
        victim_port = victim.server_port

        class Public(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/blocked":
                    html = (
                        '<html lang="en"><title>Blocked</title><body>'
                        f'<img src="http://127.0.0.1:{victim_port}/secret">'
                        "</body></html>"
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(html)))
                    self.end_headers()
                    self.wfile.write(html)
                elif self.path == "/socket":
                    html = (
                        '<html lang="en"><head><title>Socket</title></head><body>'
                        f'<script>new WebSocket("ws://127.0.0.1:{victim_port}/ws")</script>'
                        "<p>Ready</p></body></html>"
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(html)))
                    self.end_headers()
                    self.wfile.write(html)
                elif self.path == "/image.png":
                    # Small valid PNG; the image response itself is incidental
                    # to HTMLCS, but exercises the guarded browser subresource.
                    import base64

                    pixels = base64.b64decode(
                        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                        "AAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg=="
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(pixels)))
                    self.end_headers()
                    self.wfile.write(pixels)
                else:
                    html = (
                        b'<html lang="en"><head><title>Public</title></head><body>'
                        b'<img src="/image.png"><p>Welcome</p></body></html>'
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    if self.path == "/csp":
                        self.send_header(
                            "Content-Security-Policy",
                            "default-src 'none'; script-src 'none'",
                        )
                    self.send_header("Content-Length", str(len(html)))
                    self.end_headers()
                    self.wfile.write(html)

            def log_message(self, *args):
                pass

        public, public_thread = _start_server(Public)

        def fixture_validator(url):
            if f":{victim_port}" in url:
                raise ValueError("URL target is not allowed")
            return url

        monkeypatch.setattr(security, "validate_url_not_private", fixture_validator)
        monkeypatch.setattr(browser_ssrf, "validate_url_not_private", fixture_validator)
        monkeypatch.setattr(public_http, "_is_forbidden_address", lambda address: False)

        scanner = Pa11yScanner(
            timeout=30,
            pa11y_bin=os.environ["AELIRA_TEST_PA11Y_BIN"],
            chromium_executable=os.environ["AELIRA_TEST_CHROMIUM_EXECUTABLE"],
        )
        try:
            yield scanner, public.server_port, victim_hits
        finally:
            public.shutdown()
            victim.shutdown()
            public.server_close()
            victim.server_close()
            public_thread.join(timeout=3)
            victim_thread.join(timeout=3)

    @pytest.mark.asyncio
    async def test_public_page_produces_real_htmlcs_issues(self, scanner_and_servers):
        scanner, port, victim_hits = scanner_and_servers
        result = await scanner.scan(f"http://127.0.0.1:{port}/public", runner="htmlcs")

        assert result.engine == result.runner == "htmlcs"
        assert result.total_issues > 0
        assert any("1_1_1" in issue.code for issue in result.issues)
        assert all(issue.runner == "htmlcs" for issue in result.issues)
        assert result.page_title == "Public"
        assert victim_hits == []

    @pytest.mark.asyncio
    async def test_private_subresource_refuses_secondary_score(
        self, scanner_and_servers
    ):
        scanner, port, victim_hits = scanner_and_servers
        with pytest.raises(BrowserScanIncompleteError):
            await scanner.scan(f"http://127.0.0.1:{port}/blocked", runner="htmlcs")
        assert victim_hits == []

    @pytest.mark.asyncio
    async def test_strict_csp_page_still_runs_trusted_htmlcs(self, scanner_and_servers):
        scanner, port, _ = scanner_and_servers
        result = await scanner.scan(f"http://127.0.0.1:{port}/csp", runner="htmlcs")
        assert any("1_1_1" in issue.code for issue in result.issues)

    @pytest.mark.asyncio
    async def test_websocket_dependency_refuses_secondary_score(
        self, scanner_and_servers
    ):
        scanner, port, victim_hits = scanner_and_servers
        with pytest.raises(BrowserScanIncompleteError):
            await scanner.scan(f"http://127.0.0.1:{port}/socket", runner="htmlcs")
        assert victim_hits == []
