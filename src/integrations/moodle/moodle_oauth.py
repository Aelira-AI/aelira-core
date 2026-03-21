"""
Moodle OAuth 2.0 Service

Handles OAuth 2.0 authentication for Moodle Web Services access.

Moodle OAuth Documentation:
- https://docs.moodle.org/dev/OAuth_2.0
- https://docs.moodle.org/dev/OAuth_2_API

Note: Moodle's OAuth is primarily for user authentication. After OAuth login,
we need to exchange for a Web Services token to use the REST API.
"""

import os
import logging
import httpx
from typing import Optional, Tuple
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


def get_moodle_authorization_url(
    moodle_instance_url: str,
    redirect_uri: str,
    state: str,
    client_id: Optional[str] = None,
) -> str:
    """
    Generate Moodle OAuth 2.0 authorization URL.

    Args:
        moodle_instance_url: Moodle instance URL (e.g., "https://moodle.university.edu")
        redirect_uri: OAuth callback URL
        state: CSRF protection state parameter
        client_id: OAuth client ID (defaults to MOODLE_CLIENT_ID env var)

    Returns:
        Authorization URL to redirect user to
    """
    client_id = client_id or os.getenv("MOODLE_CLIENT_ID")
    if not client_id:
        raise ValueError("Moodle OAuth client ID not configured")

    # Moodle OAuth authorization endpoint
    auth_url = f"{moodle_instance_url.rstrip('/')}/admin/oauth2callback.php"

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": "openid profile email",  # Standard OpenID Connect scopes
    }

    return f"{auth_url}?{urlencode(params)}"


async def exchange_moodle_code_for_token(
    moodle_instance_url: str,
    authorization_code: str,
    redirect_uri: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Tuple[str, str, datetime]:
    """
    Exchange Moodle authorization code for access token.

    Args:
        moodle_instance_url: Moodle instance URL
        authorization_code: Authorization code from Moodle callback
        redirect_uri: OAuth callback URL (must match authorization request)
        client_id: OAuth client ID
        client_secret: OAuth client secret

    Returns:
        Tuple of (access_token, refresh_token, expires_at)

    Raises:
        httpx.HTTPError: If token exchange fails
    """
    client_id = client_id or os.getenv("MOODLE_CLIENT_ID")
    client_secret = client_secret or os.getenv("MOODLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError("Moodle OAuth credentials not configured")

    # Moodle OAuth token endpoint
    token_url = f"{moodle_instance_url.rstrip('/')}/admin/oauth2callback.php"

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

        logger.info(f"Moodle OAuth token obtained, expires at {expires_at}")

        return access_token, refresh_token, expires_at


async def refresh_moodle_token(
    moodle_instance_url: str,
    refresh_token: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Tuple[str, Optional[str], datetime]:
    """
    Refresh Moodle access token using refresh token.

    Args:
        moodle_instance_url: Moodle instance URL
        refresh_token: Refresh token
        client_id: OAuth client ID
        client_secret: OAuth client secret

    Returns:
        Tuple of (new_access_token, new_refresh_token, expires_at)
    """
    client_id = client_id or os.getenv("MOODLE_CLIENT_ID")
    client_secret = client_secret or os.getenv("MOODLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError("Moodle OAuth credentials not configured")

    token_url = f"{moodle_instance_url.rstrip('/')}/admin/oauth2callback.php"

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
            },
        )

        response.raise_for_status()
        data = response.json()

        new_access_token = data["access_token"]
        new_refresh_token = data.get("refresh_token")  # May be None if not rotated

        # Calculate expiration
        expires_in = data.get("expires_in", 3600)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        logger.info(f"Moodle OAuth token refreshed, expires at {expires_at}")

        return new_access_token, new_refresh_token, expires_at


async def get_moodle_webservice_token(
    moodle_instance_url: str,
    oauth_access_token: str,
) -> str:
    """
    Exchange OAuth access token for Moodle Web Services token.

    This is a Moodle-specific step: OAuth gets you authenticated,
    but you need a separate Web Services token to use the REST API.

    Args:
        moodle_instance_url: Moodle instance URL
        oauth_access_token: OAuth access token from authorization flow

    Returns:
        Web Services token (wstoken) for API calls

    Note: This may require custom Moodle plugin or configuration.
    Standard Moodle doesn't automatically provide this bridge.
    Alternative: Admin can manually create a web service token for the user.
    """
    # This is a simplified implementation
    # In practice, this may require:
    # 1. Custom Moodle plugin to bridge OAuth → Web Services
    # 2. OR: Admin pre-creates web service tokens
    # 3. OR: Use OAuth token directly if Moodle is configured to accept it

    # For now, we'll assume the OAuth access token can be used directly
    # as the wstoken (this works if Moodle is configured appropriately)
    logger.warning(
        "Using OAuth access token as Web Services token. "
        "This may require Moodle configuration or custom plugin."
    )
    return oauth_access_token
