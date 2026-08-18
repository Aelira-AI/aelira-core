"""
Security Utilities
Centralized security functions for SSRF protection and prompt injection defense.
"""

import ipaddress
import os
import re
import socket
from collections.abc import Mapping
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

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
PERSISTED_CANVAS_ORIGIN_ERROR = (
    "Canvas connection origin is invalid or no longer authorized; reconnect Canvas"
)


def validate_canvas_instance_origin(
    url: str,
    environment: str | None = None,
    *,
    _resolve_dns: bool = True,
) -> str:
    """Return a canonical, SSRF-safe Canvas origin.

    Canvas instance values are origins, not arbitrary URLs. Every resolved
    address must be globally routable before the origin can be used in an
    OAuth authorization or token request. Development and test may use the
    explicit ``localhost`` hostname over HTTP for local Canvas fixtures.
    """
    parsed = urlparse(url)
    env = (environment or os.getenv("ENV", "development")).lower()
    allow_localhost = env in {"development", "test"}

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Canvas instance must be an HTTP(S) origin")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Canvas instance must not contain user information")
    if parsed.query or parsed.fragment or parsed.params or parsed.path not in {"", "/"}:
        raise ValueError("Canvas instance must be a root origin")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Canvas instance must contain a valid hostname")
    hostname = hostname.lower()
    is_localhost = hostname == "localhost"
    if parsed.scheme != "https" and not (allow_localhost and is_localhost):
        raise ValueError("Canvas instance must use HTTPS")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Canvas instance has an invalid port") from exc

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is None and not is_localhost:
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Canvas instance has an invalid hostname") from exc
        labels = ascii_hostname.rstrip(".").split(".")
        if (
            len(ascii_hostname) > 253
            or len(labels) < 2
            or any(not _HOST_LABEL.fullmatch(label) for label in labels)
        ):
            raise ValueError("Canvas instance has an invalid hostname")
        hostname = ascii_hostname.rstrip(".")

    if _resolve_dns and not (allow_localhost and is_localhost):
        try:
            addr_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("Could not resolve Canvas instance hostname") from exc
        if not addr_infos:
            raise ValueError("Could not resolve Canvas instance hostname")
        for addr_info in addr_infos:
            address = ipaddress.ip_address(str(addr_info[4][0]).split("%", 1)[0])
            if (
                not address.is_global
                or address.is_multicast
                or address.is_unspecified
                or address.is_loopback
                or address.is_link_local
                or address.is_private
                or address.is_reserved
            ):
                raise ValueError("Canvas instance target is not allowed")

    host_for_url = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if parsed.scheme == "https" else 80
    port_suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme}://{host_for_url}{port_suffix}"


def require_canvas_oauth_allowed_origin(
    url: str,
    environment: str | None = None,
    configured_origins: str | None = None,
) -> str:
    """Return ``url`` only when it satisfies the operator trust boundary.

    Staging and production deployments must explicitly authorize every Canvas
    root origin. Values are canonicalized with the same validation as requests,
    then compared as complete origins; wildcards and hostname suffixes have no
    meaning. Development and test retain their validated local/test fixture
    behavior when no allowlist is configured.
    """
    env = (environment or os.getenv("ENV", "development")).lower()
    # Canonicalize the untrusted request without resolving its caller-chosen
    # hostname. DNS/network validation occurs only after exact authorization.
    requested_origin = validate_canvas_instance_origin(
        url, environment=env, _resolve_dns=False
    )
    raw_origins = (
        os.getenv("CANVAS_OAUTH_ALLOWED_ORIGINS", "")
        if configured_origins is None
        else configured_origins
    )
    entries = [entry.strip() for entry in raw_origins.split(",") if entry.strip()]

    if not entries:
        if env in {"staging", "production"}:
            raise ValueError(
                "CANVAS_OAUTH_ALLOWED_ORIGINS must authorize the Canvas instance"
            )
        return validate_canvas_instance_origin(url, environment=env)

    allowed_origins = {
        validate_canvas_instance_origin(entry, environment=env) for entry in entries
    }
    if requested_origin not in allowed_origins:
        raise ValueError("Canvas instance origin is not authorized by the operator")
    return validate_canvas_instance_origin(url, environment=env)


def require_persisted_canvas_origin(persisted_value: object) -> str:
    """Return a currently authorized origin from persisted Canvas metadata.

    Persisted credentials are untrusted legacy input: operator trust can be
    withdrawn after a credential was created. Validate on every use and expose
    one stable reconnect instruction rather than validation internals.
    """
    if isinstance(persisted_value, str):
        origin = persisted_value
    else:
        metadata = (
            persisted_value
            if isinstance(persisted_value, Mapping)
            else getattr(persisted_value, "provider_metadata", None)
        )
        origin = (
            metadata.get("canvas_instance_url")
            if isinstance(metadata, Mapping)
            else None
        )

    if not isinstance(origin, str) or not origin.strip():
        raise ValueError(PERSISTED_CANVAS_ORIGIN_ERROR)

    try:
        return require_canvas_oauth_allowed_origin(origin)
    except (TypeError, ValueError) as exc:
        raise ValueError(PERSISTED_CANVAS_ORIGIN_ERROR) from exc


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
