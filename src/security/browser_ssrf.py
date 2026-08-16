"""Browser-level SSRF guard for Playwright web scanning.

The API validates the initial scan URL (web_scan_routes), but that check
alone leaves Chromium free to be steered onto private infrastructure after
navigation starts: a public URL can 302 to a loopback or link-local address,
and a public page can load subresources from the internal network.

Playwright routing cannot solve this with a plain ``route.continue_()``
handler: the route handler is NOT re-invoked for server redirect hops, and a
fulfilled 3xx is likewise followed by the browser without interception
(verified empirically against the pinned Playwright). So this guard:

1. validates every intercepted request URL before anything is fetched;
2. fetches with ``max_redirects=0`` so the browser never follows a server
   redirect on its own;
3. walks the redirect chain itself, validating every hop, honoring the
   301/302/303 POST->GET downgrade and the 307/308 method preservation;
4. hands the browser a single collapsed redirect to the validated final URL,
   so page.url and relative-link resolution stay correct.

The guard is registered for http/https only: the e2e accessibility suite
scans ``file://`` fixtures (which the API never accepts as scan targets, and
Chromium itself refuses http->file redirects), and non-network schemes have
no SSRF surface.

Failure policy is fail-closed: any validation error, fetch error, or
unexpected exception aborts the request.

Residual risk (documented, not silently accepted): each hop is validated by
resolving DNS at check time; a resolver that answers differently between
validation and the browser's own connection (DNS rebinding) is mitigated by
running scans from an egress-restricted network, the same caveat as
``safe_requests_get``. WebSocket connections are not routed by Playwright
and are not covered here.
"""

import logging
import re
from urllib.parse import urljoin

from src.utils.security import validate_url_not_private

logger = logging.getLogger(__name__)

# net::ERR_BLOCKED_BY_CLIENT — shows up clearly in scanner logs as a
# deliberate block rather than a network fault.
BLOCKED_ABORT_CODE = "blockedbyclient"

MAX_REDIRECT_HOPS = 5

_HTTP_URL_PATTERN = re.compile(r"^https?://")


def _redirect_location(response):
    """Return the Location header for a redirect response, else None."""
    if 300 <= response.status < 400 and response.status != 304:
        headers = {k.lower(): v for k, v in response.headers.items()}
        return headers.get("location")
    return None


def make_ssrf_route_handler(validator=validate_url_not_private):
    """Build a Playwright route handler that validates every hop.

    ``validator`` takes a URL and raises ValueError for disallowed targets;
    it is injectable so tests can classify local servers as public/private.
    """

    def handle_route(route):
        request = route.request
        try:
            current_url = request.url
            validator(current_url)
            method = request.method
            response = route.fetch(max_redirects=0)

            hops = 0
            location = _redirect_location(response)
            while location is not None:
                hops += 1
                if hops > MAX_REDIRECT_HOPS:
                    raise ValueError(f"Too many redirects (>{MAX_REDIRECT_HOPS})")
                next_url = urljoin(current_url, location)
                validator(next_url)
                if response.status in (301, 302, 303) and method not in (
                    "GET",
                    "HEAD",
                ):
                    method = "GET"
                current_url = next_url
                response = route.fetch(url=current_url, method=method, max_redirects=0)
                location = _redirect_location(response)

            if current_url == request.url:
                route.fulfill(response=response)
            else:
                # Collapse the validated chain into one redirect so the
                # browser lands on the final URL with correct semantics.
                route.fulfill(status=302, headers={"Location": current_url})
        except Exception as e:
            logger.warning(
                "Blocked browser request during scan (SSRF guard): %s (%s)",
                request.url,
                e,
            )
            try:
                route.abort(BLOCKED_ABORT_CODE)
            except Exception:
                pass  # route may already be handled; nothing safe to do

    return handle_route


def install_browser_ssrf_guard(context, validator=validate_url_not_private):
    """Install the SSRF guard on a Playwright BrowserContext.

    Must be called before the first page is created so every page in the
    context (navigation, subresources, iframes) is covered.
    """
    context.route(_HTTP_URL_PATTERN, make_ssrf_route_handler(validator))
