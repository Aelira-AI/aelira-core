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
