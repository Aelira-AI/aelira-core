"""Browser route SSRF tests with controlled HTTP and Chromium fixtures."""

import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

from src.security.browser_ssrf import (
    BROWSER_EGRESS_ARGS,
    BLOCKED_ABORT_CODE,
    BLOCKED_CHANNEL_CONSOLE_MARKER,
    BrowserNetworkIsolation,
    BrowserScanIncompleteError,
    assert_browser_channels_safe,
    install_browser_ssrf_guard,
    make_ssrf_route_handler,
    resolve_browser_navigation_url,
)

requires_browser = pytest.mark.skipif(
    not (os.getenv("RUN_E2E_TESTS") or os.getenv("RUN_CONTROLLED_BROWSER_TESTS")),
    reason="Controlled browser fixture requires Playwright Chromium",
)


class FakeRequest:
    def __init__(
        self, url, method="GET", headers=None, body=None, resource_type="document"
    ):
        self.url = url
        self.method = method
        self._headers = headers or {}
        self.post_data_buffer = body
        self.resource_type = resource_type

    def all_headers(self):
        return self._headers


class FakeRoute:
    def __init__(
        self, url, method="GET", headers=None, body=None, resource_type="document"
    ):
        self.request = FakeRequest(url, method, headers, body, resource_type)
        self.fulfilled = None
        self.aborted = None

    def fulfill(self, **kwargs):
        self.fulfilled = kwargs

    def abort(self, error_code=None):
        self.aborted = error_code


def fake_response(url, status=200, body=b"ok", headers=None):
    response = requests.Response()
    response.url = url
    response.status_code = status
    response._content = body
    response._content_consumed = True
    response.headers.update(headers or {})
    return response


def blocklist_validator(url):
    if "private" in url:
        raise ValueError("URL target is not allowed")
    return url


def test_private_document_aborts_before_fetch():
    route = FakeRoute("http://private.example/secret")
    calls = []
    handler = make_ssrf_route_handler(
        blocklist_validator, fetcher=lambda *args, **kwargs: calls.append(args)
    )
    handler(route)
    assert route.aborted == BLOCKED_ABORT_CODE
    assert route.fulfilled is None
    assert calls == []


def test_public_response_preserves_request_and_decoded_body():
    route = FakeRoute(
        "https://public.example/post",
        method="POST",
        headers={"Content-Type": "application/json", "Host": "attacker.example"},
        body=b'{"x":1}',
    )
    calls = []

    def fetch(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return fake_response(
            url,
            status=201,
            body=b"decoded body",
            headers={
                "Content-Type": "text/plain",
                "Content-Encoding": "gzip",
                "Content-Length": "900",
                "Set-Cookie": "session=one; Path=/",
            },
        )

    make_ssrf_route_handler(blocklist_validator, fetcher=fetch)(route)
    assert route.aborted is None
    assert route.fulfilled == {
        "status": 201,
        "headers": {"Content-Type": "text/plain"},
        "body": b"decoded body",
    }
    assert calls[0][0:2] == ("POST", "https://public.example/post")
    assert calls[0][2]["data"] == b'{"x":1}'
    assert calls[0][2]["headers"] == {"Content-Type": "application/json"}
    assert calls[0][2]["follow_redirects"] is False


@pytest.mark.parametrize("location", ["/next", "http://private.example/secret"])
def test_browser_redirect_aborts_without_second_fetch(location):
    route = FakeRoute("http://public.example/start")
    calls = []

    def fetch(method, url, **kwargs):
        calls.append(url)
        return fake_response(url, 302, headers={"Location": location})

    make_ssrf_route_handler(blocklist_validator, fetcher=fetch)(route)
    assert route.aborted == BLOCKED_ABORT_CODE
    assert route.fulfilled is None
    assert calls == ["http://public.example/start"]


def test_failed_fetch_log_does_not_include_url_or_exception(caplog):
    route = FakeRoute("https://public.example/path?token=secret")

    def fetch(*args, **kwargs):
        raise RuntimeError("sensitive exception detail")

    make_ssrf_route_handler(blocklist_validator, fetcher=fetch)(route)
    assert route.aborted == BLOCKED_ABORT_CODE
    assert "token=secret" not in caplog.text
    assert "sensitive exception detail" not in caplog.text
    assert "fetch_failed" in caplog.text


@pytest.mark.parametrize("resource_type", ["image", "media", "eventsource"])
def test_blocked_required_dependency_is_recorded(resource_type):
    blocked = []
    route = FakeRoute("http://private.example/resource", resource_type=resource_type)
    make_ssrf_route_handler(
        blocklist_validator,
        fetcher=lambda *args, **kwargs: None,
        blocked_required=blocked,
    )(route)
    assert blocked == [{"resource_type": resource_type, "reason": "target_refused"}]


def test_repeated_cookies_are_synced_to_browser_context():
    class Context:
        def __init__(self):
            self.cookies = None

        def add_cookies(self, cookies):
            self.cookies = cookies

    context = Context()
    route = FakeRoute("https://public.example/page")
    response = fake_response(route.request.url)
    response.cookies.set("first", "1", domain="public.example", path="/")
    response.cookies.set("second", "2", domain="public.example", path="/")
    make_ssrf_route_handler(
        blocklist_validator,
        fetcher=lambda *args, **kwargs: response,
        context=context,
    )(route)
    assert route.aborted is None
    assert {cookie["name"] for cookie in context.cookies} == {"first", "second"}


def test_install_registers_http_and_websocket_guards():
    class Context:
        def __init__(self):
            self.routes = []
            self.ws_routes = []
            self.binding = None
            self.init_script = None
            self.events = {}

        def on(self, event, callback):
            self.events[event] = callback

        def expose_binding(self, name, callback):
            self.binding = (name, callback)

        def add_init_script(self, script):
            self.init_script = script

        def route(self, pattern, handler):
            self.routes.append((pattern, handler))

        def route_web_socket(self, pattern, handler):
            self.ws_routes.append((pattern, handler))

    context = Context()
    blocked = install_browser_ssrf_guard(context)
    assert blocked == []
    assert len(context.routes) == len(context.ws_routes) == 1
    assert context.binding[0] == "__aeliraUnsupportedNetworkChannel"
    assert "page" in context.events
    assert "console" in context.events
    context.events["console"](
        type("Message", (), {"text": BLOCKED_CHANNEL_CONSOLE_MARKER})()
    )
    assert blocked == [
        {"resource_type": "browser_channel", "reason": "unsupported_network_channel"}
    ]
    blocked.clear()
    assert "RTCPeerConnection" in context.init_script
    context.binding[1](None, "RTCPeerConnection")
    assert blocked == [
        {"resource_type": "browser_channel", "reason": "unsupported_network_channel"}
    ]
    blocked.clear()
    assert isinstance(context.routes[0][0], re.Pattern)
    assert context.routes[0][0].search("https://example.com/x")
    assert not context.routes[0][0].search("file:///fixture.html")
    assert context.ws_routes[0][0].search("wss://example.com/ws")

    class WebSocket:
        def __init__(self):
            self.message_handler = None

        def on_message(self, handler):
            self.message_handler = handler

    socket_route = WebSocket()
    context.ws_routes[0][1](socket_route)
    assert socket_route.message_handler is not None
    assert blocked == [
        {"resource_type": "websocket", "reason": "unsupported_network_channel"}
    ]


def test_file_navigation_preserves_fixture_url():
    assert (
        resolve_browser_navigation_url("file:///tmp/fixture.html")
        == "file:///tmp/fixture.html"
    )


def test_navigation_resolver_returns_final_url_and_closes_response(monkeypatch):
    from src.security import browser_ssrf

    response = fake_response("https://public.example/final")
    closed = []
    monkeypatch.setattr(response, "close", lambda: closed.append(True))
    monkeypatch.setattr(
        browser_ssrf,
        "safe_requests_get",
        lambda url, timeout, max_redirects: response,
    )

    assert (
        resolve_browser_navigation_url("https://public.example/start")
        == "https://public.example/final"
    )
    assert closed == [True]


def test_navigation_resolver_syncs_redirect_cookies_before_browser_load(monkeypatch):
    from src.security import browser_ssrf

    redirect = fake_response("https://public.example/start", status=302)
    redirect.cookies.set("gate", "1", path="/")
    final = fake_response("https://public.example/landing")
    final.history = [redirect]
    observed = []

    class Context:
        def add_cookies(self, cookies):
            observed.extend(cookies)

    monkeypatch.setattr(
        browser_ssrf,
        "safe_requests_get",
        lambda url, timeout, max_redirects: final,
    )
    assert (
        resolve_browser_navigation_url("https://public.example/start", Context())
        == "https://public.example/landing"
    )
    assert observed[0]["name"] == "gate"
    assert observed[0]["url"] == "https://public.example/"


def test_scanner_refuses_to_score_blocked_required_resource(monkeypatch):
    from src.education import web_scanner
    from src.security.browser_ssrf import BrowserScanIncompleteError

    scanner = web_scanner.WebScanner(
        scan_images=False,
        scan_multimedia=False,
        scan_math=False,
        max_depth=0,
        max_pages=1,
        use_ai_analysis=False,
    )
    scanner._browser_blocked_required = []
    monkeypatch.setattr(
        web_scanner, "resolve_browser_navigation_url", lambda url, context=None: url
    )

    class Page:
        def goto(self, *args, **kwargs):
            scanner._browser_blocked_required.append(
                {"resource_type": "script", "reason": "fetch_failed"}
            )

        def close(self):
            pass

    class Context:
        def new_page(self):
            return Page()

    with pytest.raises(BrowserScanIncompleteError):
        scanner._scan_page(Context(), "https://public.example/page")


def _scanner_with_fake_browser(monkeypatch, *, max_pages=1, exclude_patterns=None):
    from src.education import web_scanner

    scanner = web_scanner.WebScanner(
        scan_images=False,
        scan_multimedia=False,
        scan_math=False,
        max_depth=0,
        max_pages=max_pages,
        use_ai_analysis=False,
        llm_client=object(),
        database_url="postgresql://test:disposable-test-only@127.0.0.1/test",
        exclude_patterns=exclude_patterns,
    )
    closed = []

    class Context:
        def on(self, *args):
            pass

        def expose_binding(self, *args):
            pass

        def add_init_script(self, *args):
            pass

        def route(self, *args):
            pass

        def route_web_socket(self, *args):
            pass

    class Browser:
        def new_context(self, **kwargs):
            assert kwargs["service_workers"] == "block"
            assert kwargs["proxy"]["bypass"] == "<-loopback>"
            return Context()

        def close(self):
            closed.append("browser")

    class Isolation:
        def new_context(self, browser):
            return browser.new_context(
                service_workers="block",
                proxy={"server": "http://127.0.0.1:1", "bypass": "<-loopback>"},
            )

        def close(self):
            pass

    class Chromium:
        def launch(self, **kwargs):
            return Browser()

    class Playwright:
        chromium = Chromium()

        def stop(self):
            closed.append("playwright")

    class Starter:
        def start(self):
            return Playwright()

    monkeypatch.setattr(web_scanner, "sync_playwright", Starter)
    monkeypatch.setattr(web_scanner, "BrowserNetworkIsolation", Isolation)
    monkeypatch.setattr(
        web_scanner, "resolve_browser_navigation_url", lambda url, context: url
    )
    return scanner, closed


def test_zero_page_limit_refuses_before_browser_launch(monkeypatch):
    from src.education import web_scanner
    from src.security.browser_ssrf import BrowserScanIncompleteError

    scanner, _ = _scanner_with_fake_browser(monkeypatch, max_pages=0)
    monkeypatch.setattr(
        web_scanner,
        "sync_playwright",
        lambda: pytest.fail("browser launched for zero-page scan"),
    )
    with pytest.raises(BrowserScanIncompleteError, match="at least one page"):
        scanner.scan_website("https://public.example/start")


def test_excluded_root_refuses_after_browser_cleanup(monkeypatch):
    from src.security.browser_ssrf import BrowserScanIncompleteError

    scanner, closed = _scanner_with_fake_browser(
        monkeypatch, exclude_patterns=["/excluded"]
    )
    monkeypatch.setattr(
        scanner, "_scan_page", lambda *args: pytest.fail("excluded page evaluated")
    )
    monkeypatch.setattr(
        scanner,
        "_calculate_overall_score",
        lambda *args: pytest.fail("zero-page scan was scored"),
    )

    with pytest.raises(BrowserScanIncompleteError, match="No web pages"):
        scanner.scan_website("https://public.example/excluded")
    assert closed == ["browser", "playwright"]


def test_evaluated_page_produces_result(monkeypatch):
    from src.education import web_scanner

    scanner, closed = _scanner_with_fake_browser(monkeypatch)
    page = web_scanner.WebPageScanResult(
        url="https://public.example/start",
        title="Evaluated",
        scan_time=0.1,
        compliance_score=100.0,
        issues=[],
    )
    evaluated = []

    def scan_page(_context, url):
        evaluated.append(url)
        return page

    monkeypatch.setattr(scanner, "_scan_page", scan_page)
    result = scanner.scan_website("https://public.example/start")

    assert evaluated == ["https://public.example/start"]
    assert result.pages_scanned == 1
    assert result.pages[0].title == "Evaluated"
    assert result.overall_compliance_score == 100.0
    assert closed == ["browser", "playwright"]


# ---------------------------------------------------------------------------
# End-to-end: real Chromium against local servers
# ---------------------------------------------------------------------------


def _start_server(handler_cls):
    class CountingHTTPServer(HTTPServer):
        accepted_connections = 0

        def get_request(self):
            connection = super().get_request()
            self.accepted_connections += 1
            return connection

    server = CountingHTTPServer(("127.0.0.1", 0), handler_cls)
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
        public_hits = []
        cookie_hits = []

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
                public_hits.append(self.path)
                if self.path == "/tamper":
                    body = b"""<html><body><script>
                      const controller = globalThis.__playwright__binding__controller__;
                      if (controller) controller.callBinding = () => Promise.resolve();
                      console.error = () => {};
                      window.eval = () => false;
                      globalThis = {__aeliraUnsupportedNetworkChannelAttempted: false};
                      try { new Worker('data:text/javascript,postMessage(1)'); } catch {}
                    </script></body></html>"""
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/redirect-private":
                    self.send_response(302)
                    self.send_header(
                        "Location", f"http://127.0.0.1:{victim_port}/internal"
                    )
                    self.end_headers()
                elif self.path == "/redirect-public":
                    self.send_response(302)
                    self.send_header("Location", "/landing")
                    self.end_headers()
                elif self.path == "/redirect-cookie":
                    self.send_response(302)
                    self.send_header("Location", "/cookie-landing")
                    self.send_header("Set-Cookie", "gate=1; Path=/")
                    self.end_headers()
                elif self.path == "/cookie-landing":
                    body = (
                        b"<html><body>cookie gate passed</body></html>"
                        if "gate=1" in self.headers.get("Cookie", "")
                        else b"<html><body>cookie gate missing</body></html>"
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
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
                elif self.path == "/cookie-page":
                    body = (
                        b'<html><body><img src="/cookie-check">'
                        b'<img src="/scope/check"></body></html>'
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Set-Cookie", "first=1; Path=/")
                    self.send_header("Set-Cookie", "second=2; Path=/")
                    self.send_header("Set-Cookie", "scoped=3; Path=/scope")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/cookie-check":
                    cookie_hits.append((self.path, self.headers.get("Cookie", "")))
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif self.path == "/scope/check":
                    cookie_hits.append((self.path, self.headers.get("Cookie", "")))
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
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
            "public_server": public_server,
            "public_hits": public_hits,
            "victim_server": victim_server,
            "victim_hits": victim_hits,
            "cookie_hits": cookie_hits,
            "validator": validator,
        }
        public_server.shutdown()
        victim_server.shutdown()

    @pytest.fixture()
    def browser_context(self, servers, monkeypatch):
        from playwright.sync_api import sync_playwright
        from src.utils import public_http, security

        # Controlled loopback fixtures exercise the production transport and
        # browser routing without granting private targets in production.
        monkeypatch.setattr(security, "validate_url_not_private", servers["validator"])
        monkeypatch.setattr(public_http, "_is_forbidden_address", lambda address: False)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", *BROWSER_EGRESS_ARGS],
                executable_path=os.getenv("AELIRA_TEST_CHROMIUM_EXECUTABLE") or None,
            )
            isolation = BrowserNetworkIsolation()
            try:
                context = isolation.new_context(browser)
                blocked = install_browser_ssrf_guard(
                    context, validator=servers["validator"]
                )
                yield context, blocked
            finally:
                browser.close()
                isolation.close()

    def test_redirect_to_private_is_blocked(self, servers, browser_context):
        from playwright.sync_api import Error as PlaywrightError

        context, _ = browser_context
        page = context.new_page()
        with pytest.raises(ValueError):
            resolve_browser_navigation_url(
                f"http://127.0.0.1:{servers['public_port']}/redirect-private"
            )
        with pytest.raises(PlaywrightError):
            page.goto(
                f"http://127.0.0.1:{servers['public_port']}/redirect-private",
                timeout=15000,
            )
        assert servers["victim_hits"] == []
        assert servers["victim_server"].accepted_connections == 0

    def test_private_subresource_is_blocked_but_page_loads(
        self, servers, browser_context
    ):
        context, blocked = browser_context
        page = context.new_page()
        page.goto(
            f"http://127.0.0.1:{servers['public_port']}/subresource-private",
            wait_until="networkidle",
            timeout=15000,
        )
        assert "page" in page.content()
        assert servers["victim_hits"] == []
        assert servers["victim_server"].accepted_connections == 0
        assert any(item["resource_type"] == "image" for item in blocked)

    def test_unrouted_channels_fail_scan_without_private_udp(
        self, servers, browser_context
    ):
        context, blocked = browser_context
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.3)
        try:
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{servers['public_port']}/landing")
            results = page.evaluate(
                """async port => {
                    const results = [];
                    try {
                      const pc = new RTCPeerConnection({iceServers: [
                        {urls: `stun:127.0.0.1:${port}`}
                      ]});
                      pc.createDataChannel('probe');
                      await pc.setLocalDescription(await pc.createOffer());
                      results.push('rtc_allowed');
                    } catch { results.push('rtc_blocked'); }
                    try {
                      new WebTransport(`https://127.0.0.1:${port}/`);
                      results.push('webtransport_allowed');
                    } catch { results.push('webtransport_blocked'); }
                    try {
                      new Worker(URL.createObjectURL(new Blob(['postMessage(1)'])));
                      results.push('worker_allowed');
                    } catch { results.push('worker_blocked'); }
                    return results;
                }""",
                receiver.getsockname()[1],
            )
            assert results == [
                "rtc_blocked",
                "webtransport_blocked",
                "worker_blocked",
            ]
            deadline = time.monotonic() + 2
            while len(blocked) < 3 and time.monotonic() < deadline:
                time.sleep(0.02)
            # Each attempt may be reported both by the captured native console
            # method and by Playwright's binding.
            assert len(blocked) >= 3
            assert all(item["resource_type"] == "browser_channel" for item in blocked)
            with pytest.raises(socket.timeout):
                receiver.recvfrom(2048)
        finally:
            receiver.close()

    def test_binding_tamper_cannot_hide_blocked_worker(self, servers, browser_context):
        context, blocked = browser_context
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{servers['public_port']}/landing")
        attempted = page.evaluate("""() => {
                const controller = globalThis.__playwright__binding__controller__;
                if (controller) controller.callBinding = () => Promise.resolve();
                try { new Worker('data:text/javascript,postMessage(1)'); } catch {}
                globalThis = {__aeliraUnsupportedNetworkChannelAttempted: false};
                return __aeliraUnsupportedNetworkChannelAttempted;
            }""")
        assert attempted is True
        with pytest.raises(BrowserScanIncompleteError):
            assert_browser_channels_safe(page, blocked)

    def test_native_console_reports_block_despite_full_page_tamper(
        self, servers, browser_context
    ):
        context, blocked = browser_context
        page = context.new_page()
        page.goto(
            f"http://127.0.0.1:{servers['public_port']}/tamper",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        deadline = time.monotonic() + 2
        while not blocked and time.monotonic() < deadline:
            time.sleep(0.02)
        assert any(item["reason"] == "unsupported_network_channel" for item in blocked)
        assert servers["victim_server"].accepted_connections == 0

    def test_child_frame_refuses_unverifiable_score(self, servers, browser_context):
        context, blocked = browser_context
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{servers['public_port']}/landing")
        page.evaluate("""() => {
                const frame = document.createElement('iframe');
                document.body.appendChild(frame);
                frame.remove();
            }""")
        assert any(item["reason"] == "child_frame_unverifiable" for item in blocked)
        with pytest.raises(BrowserScanIncompleteError):
            assert_browser_channels_safe(page, blocked)

    def test_popup_realm_refuses_score_without_private_udp(
        self, servers, browser_context
    ):
        context, blocked = browser_context
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.3)
        try:
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{servers['public_port']}/landing")
            result = page.evaluate(
                """async port => {
                    const popup = window.open('about:blank', '_blank');
                    if (!popup) return 'missing';
                    try {
                      const pc = new popup.RTCPeerConnection({iceServers: [
                        {urls: `stun:127.0.0.1:${port}`}
                      ]});
                      pc.createDataChannel('probe');
                      await pc.setLocalDescription(await pc.createOffer());
                      await new Promise(resolve => setTimeout(resolve, 500));
                      pc.close();
                      return 'allowed';
                    } catch { return 'blocked'; }
                    finally { popup.close(); }
                }""",
                receiver.getsockname()[1],
            )
            assert result == "blocked"
            assert any(item["reason"] == "popup_unverifiable" for item in blocked)
            with pytest.raises(socket.timeout):
                receiver.recvfrom(2048)
            with pytest.raises(BrowserScanIncompleteError):
                assert_browser_channels_safe(page, blocked)
        finally:
            receiver.close()

    def test_public_redirect_still_followed(self, servers, browser_context):
        context, blocked = browser_context
        final_url = resolve_browser_navigation_url(
            f"http://127.0.0.1:{servers['public_port']}/redirect-public"
        )
        page = context.new_page()
        page.goto(
            final_url,
            wait_until="networkidle",
            timeout=15000,
        )
        assert page.url.endswith("/landing")
        assert "landed" in page.content()
        assert blocked == []
        # Every target TCP accept belongs to a validated Python GET. A
        # speculative native Chromium connection would add an unmatched accept.
        assert servers["public_hits"] == [
            "/redirect-public",
            "/landing",
            "/landing",
        ]
        assert servers["public_server"].accepted_connections == len(
            servers["public_hits"]
        )
        assert servers["victim_server"].accepted_connections == 0

    def test_public_redirect_cookie_reaches_final_browser_origin(
        self, servers, browser_context
    ):
        context, blocked = browser_context
        final_url = resolve_browser_navigation_url(
            f"http://127.0.0.1:{servers['public_port']}/redirect-cookie", context
        )
        page = context.new_page()
        page.goto(final_url, wait_until="networkidle", timeout=15000)
        assert "cookie gate passed" in page.content()
        assert blocked == []

    def test_response_cookies_reach_following_browser_request(
        self, servers, browser_context
    ):
        context, blocked = browser_context
        page = context.new_page()
        page.goto(
            f"http://127.0.0.1:{servers['public_port']}/cookie-page",
            wait_until="networkidle",
            timeout=15000,
        )
        assert servers["cookie_hits"]
        cookies_by_path = dict(servers["cookie_hits"])
        assert "first=1" in cookies_by_path["/cookie-check"]
        assert "second=2" in cookies_by_path["/cookie-check"]
        assert "scoped=3" not in cookies_by_path["/cookie-check"]
        assert "scoped=3" in cookies_by_path["/scope/check"]
        assert blocked == []


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
