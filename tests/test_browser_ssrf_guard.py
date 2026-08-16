"""Browser-level SSRF guard for Playwright web scanning.

The API validates the initial scan URL, but Chromium historically followed
redirects and loaded subresources with no further validation. Playwright's
routing never re-enters the route handler for server redirect hops (verified
empirically against the pinned version: a fulfilled 302 is followed without
interception), so the guard must detect redirects itself via
``route.fetch(max_redirects=0)`` and validate every hop before the browser
is allowed to proceed.

Unit tests here drive the handler with fake Route objects; the end-to-end
tests (marked ``e2e``) run real Chromium against local HTTP servers.
"""

import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.security.browser_ssrf import (
    BLOCKED_ABORT_CODE,
    MAX_REDIRECT_HOPS,
    install_browser_ssrf_guard,
    make_ssrf_route_handler,
)

# Real-browser tests follow the repo convention: skipped unless the full
# environment (Playwright browsers installed) is available.
requires_browser = pytest.mark.skipif(
    not os.getenv("RUN_E2E_TESTS"),
    reason="E2E test requires Playwright browsers (set RUN_E2E_TESTS=1 to enable)",
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}


class FakeRequest:
    def __init__(self, url, method="GET"):
        self.url = url
        self.method = method


class FakeRoute:
    """Scripted Route double: maps (url, method) -> FakeResponse."""

    def __init__(self, url, responses=None, method="GET", fetch_error=None):
        self.request = FakeRequest(url, method)
        self._responses = responses or {}
        self._fetch_error = fetch_error
        self.fetch_calls = []
        self.fulfilled = None
        self.aborted = None

    def fetch(self, url=None, method=None, max_redirects=None):
        target = url or self.request.url
        self.fetch_calls.append(
            {"url": target, "method": method, "max_redirects": max_redirects}
        )
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._responses[target]

    def fulfill(self, response=None, status=None, headers=None):
        self.fulfilled = {"response": response, "status": status, "headers": headers}

    def abort(self, error_code=None):
        self.aborted = error_code


def blocklist_validator(url):
    """Test validator: rejects any URL whose host part contains 'private'."""
    if "private" in url:
        raise ValueError("URL target is not allowed")
    return url


# ---------------------------------------------------------------------------
# Unit tests: handler behavior
# ---------------------------------------------------------------------------


class TestSsrfRouteHandler:
    def test_private_request_aborted_without_fetch(self):
        route = FakeRoute("http://private.example/steal")
        make_ssrf_route_handler(blocklist_validator)(route)

        assert route.aborted == BLOCKED_ABORT_CODE
        assert route.fetch_calls == []
        assert route.fulfilled is None

    def test_public_request_fulfilled_with_fetched_response(self):
        response = FakeResponse(200)
        route = FakeRoute(
            "http://public.example/page",
            responses={"http://public.example/page": response},
        )
        make_ssrf_route_handler(blocklist_validator)(route)

        assert route.aborted is None
        assert route.fulfilled["response"] is response
        assert route.fetch_calls[0]["max_redirects"] == 0

    def test_redirect_to_private_target_aborted(self):
        route = FakeRoute(
            "http://public.example/start",
            responses={
                "http://public.example/start": FakeResponse(
                    302, {"location": "http://private.example/internal"}
                ),
            },
        )
        make_ssrf_route_handler(blocklist_validator)(route)

        assert route.aborted == BLOCKED_ABORT_CODE
        assert route.fulfilled is None
        # The private hop must never be fetched
        assert all("private" not in c["url"] for c in route.fetch_calls)

    def test_redirect_to_public_target_collapsed_to_validated_location(self):
        route = FakeRoute(
            "http://public.example/start",
            responses={
                "http://public.example/start": FakeResponse(
                    302, {"location": "/moved"}
                ),
                "http://public.example/moved": FakeResponse(200),
            },
        )
        make_ssrf_route_handler(blocklist_validator)(route)

        assert route.aborted is None
        assert route.fulfilled["status"] == 302
        assert route.fulfilled["headers"]["Location"] == "http://public.example/moved"

    def test_multi_hop_redirect_validates_every_hop(self):
        validated = []

        def recording_validator(url):
            validated.append(url)
            return blocklist_validator(url)

        route = FakeRoute(
            "http://a.example/1",
            responses={
                "http://a.example/1": FakeResponse(
                    301, {"location": "http://b.example/2"}
                ),
                "http://b.example/2": FakeResponse(
                    302, {"location": "http://c.example/3"}
                ),
                "http://c.example/3": FakeResponse(200),
            },
        )
        make_ssrf_route_handler(recording_validator)(route)

        assert validated == [
            "http://a.example/1",
            "http://b.example/2",
            "http://c.example/3",
        ]
        assert route.fulfilled["headers"]["Location"] == "http://c.example/3"

    def test_redirect_loop_aborted(self):
        route = FakeRoute(
            "http://public.example/loop",
            responses={
                "http://public.example/loop": FakeResponse(302, {"location": "/loop"}),
            },
        )
        make_ssrf_route_handler(blocklist_validator)(route)

        assert route.aborted == BLOCKED_ABORT_CODE
        assert len(route.fetch_calls) <= MAX_REDIRECT_HOPS + 1

    def test_fetch_error_fails_closed(self):
        route = FakeRoute(
            "http://public.example/page", fetch_error=RuntimeError("net down")
        )
        make_ssrf_route_handler(blocklist_validator)(route)

        assert route.aborted == BLOCKED_ABORT_CODE
        assert route.fulfilled is None

    def test_post_downgraded_to_get_on_302(self):
        route = FakeRoute(
            "http://public.example/form",
            method="POST",
            responses={
                "http://public.example/form": FakeResponse(302, {"location": "/done"}),
                "http://public.example/done": FakeResponse(200),
            },
        )
        make_ssrf_route_handler(blocklist_validator)(route)

        assert route.fetch_calls[1]["method"] == "GET"

    def test_post_preserved_on_307(self):
        route = FakeRoute(
            "http://public.example/form",
            method="POST",
            responses={
                "http://public.example/form": FakeResponse(307, {"location": "/retry"}),
                "http://public.example/retry": FakeResponse(200),
            },
        )
        make_ssrf_route_handler(blocklist_validator)(route)

        assert route.fetch_calls[1]["method"] == "POST"

    def test_default_validator_blocks_link_local_metadata(self):
        route = FakeRoute("http://169.254.169.254/latest/meta-data/")
        make_ssrf_route_handler()(route)

        assert route.aborted == BLOCKED_ABORT_CODE
        assert route.fetch_calls == []

    def test_default_validator_blocks_loopback(self):
        route = FakeRoute("http://127.0.0.1:8000/admin")
        make_ssrf_route_handler()(route)

        assert route.aborted == BLOCKED_ABORT_CODE
        assert route.fetch_calls == []


# ---------------------------------------------------------------------------
# Unit tests: installation
# ---------------------------------------------------------------------------


class TestInstallBrowserSsrfGuard:
    def test_registers_handler_for_http_and_https_only(self):
        class FakeContext:
            def __init__(self):
                self.routes = []

            def route(self, pattern, handler):
                self.routes.append((pattern, handler))

        context = FakeContext()
        install_browser_ssrf_guard(context)

        assert len(context.routes) == 1
        pattern, handler = context.routes[0]
        assert isinstance(pattern, re.Pattern)
        assert pattern.search("http://example.com/x")
        assert pattern.search("https://example.com/x")
        # file:// fixtures used by the e2e suite must not be routed
        assert not pattern.search("file:///tmp/fixture.html")
        assert callable(handler)


# ---------------------------------------------------------------------------
# End-to-end: real Chromium against local servers
# ---------------------------------------------------------------------------


def _start_server(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


@pytest.mark.e2e
@requires_browser
class TestBrowserSsrfGuardE2E:
    """Real-browser proof that the guard blocks redirect and subresource SSRF.

    Both servers are loopback, so an injected validator marks the "victim"
    server as private while allowing the "public" one; the mechanism under
    test is identical to production, only the classification is stubbed.
    """

    @pytest.fixture()
    def servers(self):
        victim_hits = []

        class Victim(BaseHTTPRequestHandler):
            def do_GET(self):
                victim_hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "5")
                self.end_headers()
                self.wfile.write(b"loot!")

            def log_message(self, *args):
                pass

        victim_server, victim_port = _start_server(Victim)

        class Public(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect-private":
                    self.send_response(302)
                    self.send_header(
                        "Location", f"http://127.0.0.1:{victim_port}/internal"
                    )
                    self.end_headers()
                elif self.path == "/redirect-public":
                    self.send_response(302)
                    self.send_header("Location", "/landing")
                    self.end_headers()
                elif self.path == "/subresource-private":
                    body = (
                        f'<html><body><img src="http://127.0.0.1:{victim_port}'
                        f'/pixel.png"><p>page</p></body></html>'
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:  # /landing and anything else
                    body = b"<html><body><h1>landed</h1></body></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *args):
                pass

        public_server, public_port = _start_server(Public)

        def validator(url):
            if f":{victim_port}" in url:
                raise ValueError("URL target is not allowed")
            return url

        yield {
            "public_port": public_port,
            "victim_hits": victim_hits,
            "validator": validator,
        }
        public_server.shutdown()
        victim_server.shutdown()

    @pytest.fixture()
    def browser_context(self, servers):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context()
            install_browser_ssrf_guard(context, validator=servers["validator"])
            yield context
            browser.close()

    def test_redirect_to_private_is_blocked(self, servers, browser_context):
        from playwright.sync_api import Error as PlaywrightError

        page = browser_context.new_page()
        with pytest.raises(PlaywrightError):
            page.goto(
                f"http://127.0.0.1:{servers['public_port']}/redirect-private",
                timeout=15000,
            )
        assert servers["victim_hits"] == []

    def test_private_subresource_is_blocked_but_page_loads(
        self, servers, browser_context
    ):
        page = browser_context.new_page()
        page.goto(
            f"http://127.0.0.1:{servers['public_port']}/subresource-private",
            wait_until="networkidle",
            timeout=15000,
        )
        assert "page" in page.content()
        assert servers["victim_hits"] == []

    def test_public_redirect_still_followed(self, servers, browser_context):
        page = browser_context.new_page()
        page.goto(
            f"http://127.0.0.1:{servers['public_port']}/redirect-public",
            wait_until="networkidle",
            timeout=15000,
        )
        assert page.url.endswith("/landing")
        assert "landed" in page.content()


@pytest.mark.e2e
@requires_browser
class TestWebScannerInstallsGuard:
    """The scanner itself must install the guard with the real validator."""

    def test_scan_website_refuses_private_target_at_browser_level(self):
        import os

        os.environ["DATABASE_URL"] = os.getenv(
            "DATABASE_URL", "postgresql://test:test@localhost/test"
        )
        from src.education.web_scanner import WebScanner

        hits = []

        class Internal(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "8")
                self.end_headers()
                self.wfile.write(b"internal")

            def log_message(self, *args):
                pass

        server, port = _start_server(Internal)
        try:
            scanner = WebScanner(
                scan_images=False,
                scan_multimedia=False,
                scan_math=False,
                max_depth=0,
                max_pages=1,
                use_ai_analysis=False,
                capture_screenshots=False,
            )
            with pytest.raises(Exception):
                scanner.scan_website(f"http://127.0.0.1:{port}/")
            assert hits == []
        finally:
            server.shutdown()
