"""
Security Utilities
Centralized security functions for SSRF protection and prompt injection defense.
"""

import ipaddress
import socket
from urllib.parse import urlparse

# Patterns that could manipulate LLM behavior
PROMPT_INJECTION_PATTERNS = [
    "ignore above",
    "ignore previous",
    "ignore all",
    "disregard",
    "forget everything",
    "new instructions",
    "you are now",
    "act as",
    "pretend",
    "system:",
    "assistant:",
    "user:",
]


def redact_url_credentials(url: str) -> str:
    """Strip the password out of a connection URL so it is safe to log.

    Service URLs carry their credentials inline (redis://:pw@host,
    postgresql://user:pw@host). Logging one verbatim writes the password to
    stdout, ships it to Loki, and attaches it to any Sentry breadcrumb that
    captures the line. Keep the shape — scheme, user, host, port, path — which
    is the part that is actually useful when diagnosing a connection.

    Anything unparseable is reported as "<unparseable url>" rather than passed
    through, so a malformed value cannot leak by falling back to the original.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if parsed.password is None:
            return url

        userinfo = f"{parsed.username or ''}:***@"
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"

        rebuilt = f"{parsed.scheme}://{userinfo}{host}{parsed.path}"
        if parsed.query:
            rebuilt = f"{rebuilt}?{parsed.query}"
        return rebuilt
    except Exception:
        return "<unparseable url>"


def validate_url_not_private(url: str) -> str:
    """
    Validate that a URL does not resolve to a private/reserved IP address.
    Defends against SSRF attacks targeting internal infrastructure.

    Args:
        url: The URL to validate

    Returns:
        The validated URL

    Raises:
        ValueError: If URL resolves to a private/reserved IP or is malformed
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid URL format")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must contain a valid hostname")

    # Resolve hostname to IP addresses
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise ValueError("Could not resolve hostname")

    for addr_info in addr_infos:
        ip_str = addr_info[4][0]
        ip = ipaddress.ip_address(ip_str)

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_reserved
            or ip.is_link_local
            or ip_str == "0.0.0.0"
        ):
            raise ValueError("URL target is not allowed")

    return url


def safe_requests_get(
    url: str,
    timeout: float = 10,
    max_redirects: int = 5,
    **kwargs,
):
    """GET a URL with SSRF-guarded redirects.

    validate_url_not_private() alone is not enough: requests follows
    redirects by default, so an allowed public URL could 302 to localhost,
    a private network, or cloud metadata. This helper disables automatic
    redirects and re-validates every hop before following it.

    Note: each hop is validated at request time via DNS resolution; a
    hostile resolver that answers differently between validation and
    connection (DNS rebinding) is out of scope here and mitigated by
    running scans from an egress-restricted network.
    """
    import requests as _requests

    kwargs.pop("allow_redirects", None)
    current = url
    for _ in range(max_redirects + 1):
        validate_url_not_private(current)
        response = _requests.get(
            current, timeout=timeout, allow_redirects=False, **kwargs
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            if not location:
                return response
            # Resolve relative redirects against the current URL
            from urllib.parse import urljoin

            current = urljoin(current, location)
            continue
        return response
    raise ValueError(f"Too many redirects (>{max_redirects})")


def sanitize_for_prompt(text: str, max_length: int = 500) -> str:
    """
    Sanitize user-controlled text before interpolation into AI prompts.
    Truncates length and strips known prompt injection patterns.

    Args:
        text: The text to sanitize
        max_length: Maximum allowed length (default 500)

    Returns:
        Sanitized text safe for prompt interpolation
    """
    if not text:
        return ""

    safe_text = text[:max_length]
    for pattern in PROMPT_INJECTION_PATTERNS:
        safe_text = safe_text.replace(pattern, "")
        safe_text = safe_text.replace(pattern.upper(), "")
        safe_text = safe_text.replace(pattern.title(), "")

    safe_text = safe_text.strip()

    # Wrap in structural delimiters so LLM distinguishes user content from instructions
    return f"<user_content>{safe_text}</user_content>"
