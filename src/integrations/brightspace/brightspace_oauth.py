"""
D2L Brightspace OAuth 2.0 Service

Handles OAuth 2.0 authentication for Brightspace Valence API access.

Brightspace OAuth Documentation:
- https://docs.valence.desire2learn.com/basic/oauth2.html
- Standard OAuth 2.0 flow (no dual tokens like Moodle)
- Requires application registration in Brightspace
- Uses standard authorization_code grant type
"""

import os
import logging
import httpx
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

logger = logging.getLogger(__name__)

BRIGHTSPACE_TOKEN_URL = "https://auth.brightspace.com/core/connect/token"
BRIGHTSPACE_TOKEN_TIMEOUT_SECONDS = 15.0


class BrightspaceOAuthError(Exception):
    """Sanitized Brightspace OAuth transport or response failure."""


def _validated_token_endpoint() -> str:
    """Return the one approved centralized Brightspace token endpoint."""
    try:
        parsed = urlsplit(BRIGHTSPACE_TOKEN_URL)
        port = parsed.port
    except (TypeError, ValueError):
        raise BrightspaceOAuthError(
            "Invalid Brightspace OAuth token endpoint"
        ) from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "auth.brightspace.com"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/core/connect/token"
        or parsed.query
        or parsed.fragment
    ):
        raise BrightspaceOAuthError("Invalid Brightspace OAuth token endpoint")
    return "https://auth.brightspace.com/core/connect/token"


async def _post_token(data: Dict[str, str]) -> Dict[str, Any]:
    """POST secrets only to the fixed endpoint with a hardened HTTP client."""
    token_url = _validated_token_endpoint()
    try:
        async with httpx.AsyncClient(
            timeout=BRIGHTSPACE_TOKEN_TIMEOUT_SECONDS,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = await client.post(
                token_url,
                data=data,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid token response")
            return payload
    except Exception:
        logger.warning("Brightspace OAuth token request failed")
        raise BrightspaceOAuthError("Brightspace OAuth token request failed") from None


def get_brightspace_authorization_url(
    brightspace_instance_url: str,
    redirect_uri: str,
    state: str,
    client_id: Optional[str] = None,
    scope: str = "core:*:* content:*:*",
) -> str:
    """
    Generate Brightspace OAuth 2.0 authorization URL.

    Args:
        brightspace_instance_url: Brightspace instance URL (e.g., "https://university.brightspace.com")
        redirect_uri: OAuth callback URL
        state: CSRF protection state parameter
        client_id: OAuth client ID (defaults to BRIGHTSPACE_CLIENT_ID env var)
        scope: OAuth scopes (default: "core:*:*" for full API access)

    Returns:
        Authorization URL to redirect user to

    Raises:
        ValueError: If client ID is not configured
    """
    client_id = client_id or os.getenv("BRIGHTSPACE_CLIENT_ID")
    if not client_id:
        raise ValueError("Brightspace OAuth client ID not configured")

    # Brightspace cloud uses centralized OAuth at auth.brightspace.com
    auth_url = "https://auth.brightspace.com/oauth2/auth"

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": scope,
    }

    return f"{auth_url}?{urlencode(params)}"


async def exchange_brightspace_code_for_token(
    brightspace_instance_url: str,
    authorization_code: str,
    redirect_uri: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Tuple[str, str, datetime]:
    """
    Exchange Brightspace authorization code for access token.

    Args:
        brightspace_instance_url: Brightspace instance URL
        authorization_code: Authorization code from Brightspace callback
        redirect_uri: OAuth callback URL (must match authorization request)
        client_id: OAuth client ID
        client_secret: OAuth client secret

    Returns:
        Tuple of (access_token, refresh_token, expires_at)

    Raises:
        httpx.HTTPError: If token exchange fails
    """
    client_id = client_id or os.getenv("BRIGHTSPACE_CLIENT_ID")
    client_secret = client_secret or os.getenv("BRIGHTSPACE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError("Brightspace OAuth credentials not configured")

    data = await _post_token(
        {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    )

    try:
        access_token = data["access_token"]
        refresh_token = data.get("refresh_token", "")
        expires_in = data.get("expires_in", 3600)
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            raise ValueError("invalid token response")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    except Exception:
        raise BrightspaceOAuthError(
            "Invalid Brightspace OAuth token response"
        ) from None

    logger.info("Brightspace OAuth token obtained")
    return access_token, refresh_token, expires_at


async def refresh_brightspace_token(
    brightspace_instance_url: str,
    refresh_token: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Tuple[str, Optional[str], datetime]:
    """
    Refresh Brightspace access token using refresh token.

    Args:
        brightspace_instance_url: Brightspace instance URL
        refresh_token: Refresh token
        client_id: OAuth client ID
        client_secret: OAuth client secret

    Returns:
        Tuple of (new_access_token, new_refresh_token, expires_at)
    """
    client_id = client_id or os.getenv("BRIGHTSPACE_CLIENT_ID")
    client_secret = client_secret or os.getenv("BRIGHTSPACE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError("Brightspace OAuth credentials not configured")

    data = await _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    )

    try:
        new_access_token = data["access_token"]
        new_refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 3600)
        if not isinstance(new_access_token, str) or (
            new_refresh_token is not None and not isinstance(new_refresh_token, str)
        ):
            raise ValueError("invalid token response")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    except Exception:
        raise BrightspaceOAuthError(
            "Invalid Brightspace OAuth token response"
        ) from None

    logger.info("Brightspace OAuth token refreshed")
    return new_access_token, new_refresh_token, expires_at
