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
from typing import Optional, Tuple
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


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

    # Brightspace cloud uses centralized token endpoint at auth.brightspace.com
    token_url = "https://auth.brightspace.com/core/connect/token"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        response.raise_for_status()
        data = response.json()

        # Extract tokens
        access_token = data["access_token"]
        refresh_token = data.get("refresh_token", "")

        # Calculate expiration (default 1 hour if not specified)
        expires_in = data.get("expires_in", 3600)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        logger.info(f"Brightspace OAuth token obtained, expires at {expires_at}")

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

    # Brightspace cloud uses centralized token endpoint at auth.brightspace.com
    token_url = "https://auth.brightspace.com/core/connect/token"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        response.raise_for_status()
        data = response.json()

        new_access_token = data["access_token"]
        new_refresh_token = data.get("refresh_token")  # May be None if not rotated

        # Calculate expiration
        expires_in = data.get("expires_in", 3600)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        logger.info(f"Brightspace OAuth token refreshed, expires at {expires_at}")

        return new_access_token, new_refresh_token, expires_at
