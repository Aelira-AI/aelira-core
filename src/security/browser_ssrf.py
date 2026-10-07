"""Connection-bound SSRF guard for Playwright web scanning.

Every browser HTTP(S) request is fetched through a public-only Requests
adapter and fulfilled from its bytes. A reject-only local proxy also contains
Chromium's speculative connections, which can precede route callbacks.
Redirects are refused because a fulfilled 3xx bypasses route callbacks,
while directly fulfilling the final body changes URL/origin semantics.
Scanner contexts block service workers and WebSockets before pages are created.
"""

import logging
import re
import socket
import threading
from urllib.parse import urlparse

from src.education.scan_completeness import IncompleteScanError
from src.utils.security import (
    safe_requests_get,
    safe_requests_request,
    validate_url_not_private,
)

logger = logging.getLogger(__name__)

BLOCKED_ABORT_CODE = "blockedbyclient"
BLOCKED_CHANNEL_CONSOLE_MARKER = "__AELIRA_BROWSER_CHANNEL_BLOCKED__"
BROWSER_EGRESS_ARGS = (
    "--disable-quic",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
)
MAX_REDIRECT_HOPS = (
    5  # Top-level resolver limit; browser subresource redirects are refused.
)


class BrowserScanIncompleteError(IncompleteScanError):
    """A required browser dependency was blocked, so scoring must stop."""


REQUIRED_RESOURCE_TYPES = {
    "document",
    "script",
    "stylesheet",
    "image",
    "media",
    "font",
    "xhr",
    "fetch",
    "eventsource",
}
_HTTP_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)
_WS_URL_PATTERN = re.compile(r"^wss?://", re.IGNORECASE)
_REQUEST_HEADERS_TO_DROP = {"host", "content-length", "transfer-encoding", "connection"}
_RESPONSE_HEADERS_TO_DROP = {
    "connection",
    "content-encoding",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "set-cookie",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_BLOCK_UNROUTED_CHANNELS_SCRIPT = """(() => {
  const report = globalThis.__aeliraUnsupportedNetworkChannel;
  const nativeConsoleError = console.error.bind(console);
  let attempted = false;
  Object.defineProperty(globalThis, '__aeliraUnsupportedNetworkChannelAttempted', {
    configurable: false,
    get: () => attempted
  });
  // Worker globals do not receive BrowserContext.add_init_script. A worker
  // could create WebTransport even though RTCPeerConnection is Window-only.
  // Refuse workers until their own execution realm has an equivalent guard.
  for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection',
                      'mozRTCPeerConnection', 'WebTransport', 'Worker',
                      'SharedWorker']) {
    if (name in globalThis) {
      Object.defineProperty(globalThis, name, {
        configurable: false,
        writable: false,
        value: function () {
          attempted = true;
          nativeConsoleError('__AELIRA_BROWSER_CHANNEL_BLOCKED__');
          void report(name);
          throw new Error('Unsupported network channel in accessibility scan');
        }
      });
    }
  }
})();"""


def assert_browser_channels_safe(page, blocked_required):
    """Attest the main realm through CDP, beyond page-controlled ``eval``."""
    if blocked_required:
        raise BrowserScanIncompleteError("Required browser resource was blocked")
    try:
        if len(page.frames) != 1:
            raise BrowserScanIncompleteError("Child browser realm is unverifiable")
        session = page.context.new_cdp_session(page)
        try:
            response = session.send(
                "Runtime.evaluate",
                {
                    "expression": "this.__aeliraUnsupportedNetworkChannelAttempted",
                    "returnByValue": True,
                    "silent": True,
                },
            )
        finally:
            session.detach()
        result = response.get("result", {})
        if (
            response.get("exceptionDetails")
            or result.get("type") != "boolean"
            or result.get("value") is not False
        ):
            raise BrowserScanIncompleteError("Unsupported browser channel was used")
        if blocked_required:
            raise BrowserScanIncompleteError("Required browser resource was blocked")
    except BrowserScanIncompleteError:
        raise
    except Exception as exc:
        raise BrowserScanIncompleteError(
            "Browser channel guard could not verify a frame"
        ) from exc


class BrowserNetworkIsolation:
    """Reject Chromium's native HTTP connections to scanned destinations.

    Playwright routing intercepts requests, but Chromium can preconnect before
    a route callback runs. A scanner-owned proxy accepts and discards those
    connections. ``<-loopback>`` removes Chromium's implicit localhost bypass,
    so private and link-local URLs cannot reach their targets directly either.
    Only the separate connection-bound Python transport fetches page bytes.
    """

    def __init__(self):
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._listener.bind(("127.0.0.1", 0))
            self._listener.listen(16)
            self._listener.settimeout(0.25)
        except Exception:
            self._listener.close()
            raise
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._reject_connections, daemon=True)
        try:
            self._thread.start()
        except Exception:
            self._listener.close()
            raise

    def _reject_connections(self):
        while not self._closed.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            connection.close()

    def new_context(self, browser):
        if self._closed.is_set():
            raise RuntimeError("Browser network isolation is closed")
        port = self._listener.getsockname()[1]
        return browser.new_context(
            service_workers="block",
            proxy={"server": f"http://127.0.0.1:{port}", "bypass": "<-loopback>"},
        )

    def close(self):
        self._closed.set()
        self._listener.close()
        self._thread.join(timeout=1)


def _sync_response_cookies(context, response):
    """Apply every Set-Cookie, including repeated headers, to the browser jar."""
    if context is None or not response.cookies:
        return
    browser_cookies = []
    for cookie in response.cookies:
        item = {
            "name": cookie.name,
            "value": cookie.value,
            "path": cookie.path or "/",
            "secure": cookie.secure,
            "httpOnly": any(key.lower() == "httponly" for key in cookie._rest),
        }
        if cookie.domain_specified:
            item["domain"] = cookie.domain
        else:
            parsed = urlparse(response.url)
            path = cookie.path or "/"
            if not path.startswith("/") or "?" in path or "#" in path:
                raise ValueError("Unsupported cookie path")
            # Playwright accepts either URL or domain+path. A URL keeps this
            # cookie host-only; its default path is the parent of /_cookie.
            url_path = "/" if path == "/" else f"{path}/_cookie"
            item["url"] = f"{parsed.scheme}://{parsed.netloc}{url_path}"
            item.pop("path")
        if cookie.expires is not None:
            item["expires"] = cookie.expires
        same_site = next(
            (value for key, value in cookie._rest.items() if key.lower() == "samesite"),
            None,
        )
        if same_site in {"Strict", "Lax", "None"}:
            item["sameSite"] = same_site
        browser_cookies.append(item)
    context.add_cookies(browser_cookies)


def _safe_response_headers(response):
    # Requests has decoded response.content. Never pass compressed framing to
    # Chromium for those decoded bytes; Playwright supplies the new length.
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in _RESPONSE_HEADERS_TO_DROP
    }


def make_ssrf_route_handler(
    validator=None,
    *,
    fetcher=safe_requests_request,
    context=None,
    blocked_required=None,
):
    """Build a route handler with injectable validation and transport for tests."""
    validator = validator or validate_url_not_private

    def handle_route(route):
        response = None
        try:
            request = route.request
            validator(request.url)
            headers = {
                key: value
                for key, value in request.all_headers().items()
                if key.lower() not in _REQUEST_HEADERS_TO_DROP
            }
            response = fetcher(
                request.method,
                request.url,
                timeout=30,
                follow_redirects=False,
                headers=headers,
                data=request.post_data_buffer,
            )
            if (
                300 <= response.status_code < 400
                and response.status_code != 304
                and response.headers.get("Location")
            ):
                raise ValueError("browser redirect cannot be safely fulfilled")
            response_body = response.content
            _sync_response_cookies(context, response)
            route.fulfill(
                status=response.status_code,
                headers=_safe_response_headers(response),
                body=response_body,
            )
        except Exception as exc:
            reason = "target_refused" if isinstance(exc, ValueError) else "fetch_failed"
            logger.warning("Browser scan request blocked: %s", reason)
            resource_type = getattr(route.request, "resource_type", "document")
            if (
                blocked_required is not None
                and resource_type in REQUIRED_RESOURCE_TYPES
            ):
                blocked_required.append(
                    {"resource_type": resource_type, "reason": reason}
                )
            try:
                route.abort(BLOCKED_ABORT_CODE)
            except Exception:
                pass
        finally:
            if response is not None:
                response.close()

    return handle_route


def install_browser_ssrf_guard(
    context, validator=None, *, fetcher=safe_requests_request
):
    """Install HTTP and WebSocket guards before creating scanner pages."""
    blocked_required = []

    def record_unrouted_channel(_source, name):
        blocked_required.append(
            {
                "resource_type": "browser_channel",
                "reason": "unsupported_network_channel",
            }
        )

    context.expose_binding("__aeliraUnsupportedNetworkChannel", record_unrouted_channel)
    context.add_init_script(_BLOCK_UNROUTED_CHANNELS_SCRIPT)

    def record_console_marker(message):
        if message.text == BLOCKED_CHANNEL_CONSOLE_MARKER:
            blocked_required.append(
                {
                    "resource_type": "browser_channel",
                    "reason": "unsupported_network_channel",
                }
            )

    context.on("console", record_console_marker)

    def watch_page(page):
        # A child frame can replace its document or detach before its guard
        # state is inspected. Popups can disappear likewise. Refuse these
        # unsupported realms even when no channel attempt was reported.

        def attached(frame):
            if frame.parent_frame is not None:
                blocked_required.append(
                    {
                        "resource_type": "browser_channel",
                        "reason": "child_frame_unverifiable",
                    }
                )

        page.on("frameattached", attached)

        def popup(_page):
            blocked_required.append(
                {"resource_type": "browser_channel", "reason": "popup_unverifiable"}
            )

        page.on("popup", popup)

    context.on("page", watch_page)
    context.route(
        _HTTP_URL_PATTERN,
        make_ssrf_route_handler(
            validator,
            fetcher=fetcher,
            context=context,
            blocked_required=blocked_required,
        ),
    )

    def block_web_socket(ws):
        blocked_required.append(
            {"resource_type": "websocket", "reason": "unsupported_network_channel"}
        )
        # Not connecting to the server makes this a local mock endpoint. Do
        # not synchronously close from the route callback: that can deadlock
        # Playwright while page navigation waits for the callback to finish.
        ws.on_message(lambda _message: None)

    context.route_web_socket(_WS_URL_PATTERN, block_web_socket)
    return blocked_required


def resolve_browser_navigation_url(url, context=None):
    """Follow top-level HTTP redirects through pinned public connections."""
    if urlparse(url).scheme == "file":
        return url
    response = safe_requests_get(url, timeout=30, max_redirects=MAX_REDIRECT_HOPS)
    try:
        if context is not None:
            for hop in [*response.history, response]:
                _sync_response_cookies(context, hop)
        return response.url
    finally:
        response.close()
