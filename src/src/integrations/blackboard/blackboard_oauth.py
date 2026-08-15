"""
Blackboard Learn OAuth 2.0 Service

Handles OAuth 2.0 authentication flow for Blackboard Learn REST API.

Blackboard OAuth Documentation:
- https://developer.blackboard.com/portal/displayApi/Learn
- Uses three-legged OAuth 2.0
"""

import os
import logging
from typing import Optional, Tuple
from datetime import datetime, timezone, timedelta
import httpx

from .models import BlackboardOAuthCredential

logger = logging.getLogger(__name__)


class BlackboardOAuthService:
    """
    Blackboard Learn OAuth 2.0 service.

    Provides methods for:
    - Generating authorization URLs
    - Exchanging authorization codes for tokens
    - Refreshing access tokens
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        """
        Initialize Blackboard OAuth service.

        Args:
            client_id: Blackboard application ID (from environment if not provided)
            client_secret: Blackboard application secret (from environment if not provided)
        """
        self.client_id = client_id or os.getenv("BLACKBOARD_OAUTH_CLIENT_ID")
        self.client_secret = client_secret or os.getenv(
            "BLACKBOARD_OAUTH_CLIENT_SECRET"
        )

        # Blackboard OAuth endpoints are instance-specific
        # Format: https://{blackboard_instance}/learn/api/public/v1/oauth2/...

    def is_configured(self) -> bool:
        """Check if OAuth credentials are configured."""
        return bool(self.client_id and self.client_secret)

    def get_authorization_url(
        self,
        blackboard_instance_url: str,
        redirect_uri: str,
        state: str,
        scopes: Optional[list] = None,
    ) -> str:
        """
        Generate Blackboard OAuth authorization URL.

        Args:
            blackboard_instance_url: Blackboard instance URL (e.g., "https://blackboard.university.edu")
            redirect_uri: OAuth callback URL
            state: CSRF protection token
            scopes: List of requested scopes (default: read, write)

        Returns:
            Authorization URL for user to visit
        """
        if scopes is None:
            scopes = ["read", "write", "delete"]  # Standard Blackboard scopes

        blackboard_url = blackboard_instance_url.rstrip("/")
        auth_endpoint = f"{blackboard_url}/learn/api/public/v1/oauth2/authorizationcode"

        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
        }

        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        auth_url = f"{auth_endpoint}?{query_string}"

        logger.info(
            f"Generated Blackboard authorization URL for {blackboard_instance_url}"
        )
        return auth_url

    async def exchange_code_for_token(
        self,
        blackboard_instance_url: str,
        authorization_code: str,
        redirect_uri: str,
    ) -> BlackboardOAuthCredential:
        """
        Exchange authorization code for access token.

        Args:
            blackboard_instance_url: Blackboard instance URL
            authorization_code: Authorization code from OAuth callback
            redirect_uri: OAuth callback URL (must match authorization request)

        Returns:
            BlackboardOAuthCredential with access token and metadata
        """
        blackboard_url = blackboard_instance_url.rstrip("/")
        token_endpoint = f"{blackboard_url}/learn/api/public/v1/oauth2/token"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": authorization_code,
                    "redirect_uri": redirect_uri,
                },
                auth=(self.client_id, self.client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            response.raise_for_status()
            token_data = response.json()

            # Blackboard tokens typically expire in 1 hour
            expires_in = token_data.get("expires_in", 3600)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            logger.info(
                f"Successfully exchanged authorization code for Blackboard token at {blackboard_instance_url}"
            )

            return BlackboardOAuthCredential(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token"),
                token_type=token_data.get("token_type", "Bearer"),
                expires_at=expires_at,
                blackboard_instance_url=blackboard_instance_url,
                user_id=token_data.get(
                    "user_id", "unknown"
                ),  # Blackboard may include user_id
                scope=token_data.get("scope"),
            )

    async def refresh_access_token(
        self,
        blackboard_instance_url: str,
        refresh_token: str,
    ) -> Tuple[str, Optional[str], datetime]:
        """
        Refresh access token using refresh token.

        Args:
            blackboard_instance_url: Blackboard instance URL
            refresh_token: Refresh token from initial authorization

        Returns:
            Tuple of (new_access_token, new_refresh_token, expires_at)
        """
        blackboard_url = blackboard_instance_url.rstrip("/")
        token_endpoint = f"{blackboard_url}/learn/api/public/v1/oauth2/token"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                auth=(self.client_id, self.client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            response.raise_for_status()
            token_data = response.json()

            expires_in = token_data.get("expires_in", 3600)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            new_access_token = token_data["access_token"]
            new_refresh_token = token_data.get("refresh_token", refresh_token)

            logger.info(
                f"Successfully refreshed Blackboard access token for {blackboard_instance_url}"
            )

            return new_access_token, new_refresh_token, expires_at


__all__ = ["BlackboardOAuthService"]
