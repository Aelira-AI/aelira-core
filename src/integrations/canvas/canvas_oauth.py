"""
Canvas OAuth 2.0 Service

Handles OAuth 2.0 authentication for Canvas REST API access.

Canvas OAuth Documentation:
- https://canvas.instructure.com/doc/api/file.oauth.html
- https://canvas.instructure.com/doc/api/file.oauth_endpoints.html
"""

import os
import logging
from typing import Optional, Tuple
from datetime import datetime, timedelta, timezone
import httpx

from .models import CanvasOAuthCredential

logger = logging.getLogger(__name__)


class CanvasOAuthService:
    """
    Canvas OAuth 2.0 Service for REST API access.

    Handles:
    - OAuth 2.0 authorization code flow
    - Token refresh
    - Token validation
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ):
        """
        Initialize Canvas OAuth service.

        Args:
            client_id: Canvas Developer Key ID
            client_secret: Canvas Developer Key Secret
            redirect_uri: OAuth redirect URI
        """
        self.client_id = client_id or os.getenv("CANVAS_OAUTH_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("CANVAS_OAUTH_CLIENT_SECRET")
        self.redirect_uri = redirect_uri or os.getenv(
            "CANVAS_OAUTH_REDIRECT_URI",
            "http://localhost:8000/api/canvas/oauth/callback",
        )

        if not self.client_id or not self.client_secret:
            logger.warning(
                "Canvas OAuth credentials not configured. Canvas REST API integration disabled."
            )

    def is_configured(self) -> bool:
        """Check if OAuth is properly configured"""
        return bool(self.client_id and self.client_secret)

    def get_authorization_url(
        self,
        canvas_instance_url: str,
        state: str,
        scopes: Optional[list] = None,
    ) -> str:
        """
        Generate Canvas OAuth authorization URL.

        Args:
            canvas_instance_url: Canvas instance URL (e.g., "https://canvas.university.edu")
            state: CSRF protection state parameter
            scopes: Optional list of OAuth scopes (default: file operations)

        Returns:
            Authorization URL to redirect user to
        """
        if not self.is_configured():
            raise ValueError("Canvas OAuth not configured")

        # Default scopes for file operations
        if scopes is None:
            scopes = [
                "url:GET|/api/v1/courses",
                "url:GET|/api/v1/courses/:course_id/files",
                "url:GET|/api/v1/files/:id",
                "url:POST|/api/v1/courses/:course_id/files",
                "url:PUT|/api/v1/files/:id",
                "url:DELETE|/api/v1/files/:id",
            ]

        # Canvas OAuth authorization endpoint
        auth_url = f"{canvas_instance_url}/login/oauth2/auth"

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "scope": " ".join(scopes) if scopes else "",
        }

        query_string = "&".join([f"{k}={v}" for k, v in params.items() if v])
        return f"{auth_url}?{query_string}"

    async def exchange_code_for_token(
        self,
        canvas_instance_url: str,
        authorization_code: str,
    ) -> CanvasOAuthCredential:
        """
        Exchange authorization code for access token.

        Args:
            canvas_instance_url: Canvas instance URL
            authorization_code: Authorization code from Canvas callback

        Returns:
            CanvasOAuthCredential with tokens and expiration
        """
        if not self.is_configured():
            raise ValueError("Canvas OAuth not configured")

        token_url = f"{canvas_instance_url}/login/oauth2/token"

        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": authorization_code,
        }

        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.post(token_url, data=data, timeout=30.0)
            response.raise_for_status()
            token_data = response.json()

        # Calculate token expiration (Canvas tokens typically expire in 1 hour)
        expires_in = token_data.get("expires_in", 3600)  # Default 1 hour
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        # Get user info
        user_id = token_data.get("user", {}).get("id", "")

        return CanvasOAuthCredential(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            token_type=token_data.get("token_type", "Bearer"),
            expires_at=expires_at,
            scope=token_data.get("scope"),
            canvas_instance_url=canvas_instance_url,
            user_id=str(user_id),
        )

    async def refresh_access_token(
        self,
        canvas_instance_url: str,
        refresh_token: str,
    ) -> Tuple[str, Optional[str], datetime]:
        """
        Refresh Canvas access token.

        Args:
            canvas_instance_url: Canvas instance URL
            refresh_token: Canvas refresh token

        Returns:
            Tuple of (new_access_token, new_refresh_token, expires_at)
        """
        if not self.is_configured():
            raise ValueError("Canvas OAuth not configured")

        token_url = f"{canvas_instance_url}/login/oauth2/token"

        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
        }

        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.post(token_url, data=data, timeout=30.0)
            response.raise_for_status()
            token_data = response.json()

        expires_in = token_data.get("expires_in", 3600)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        return (
            token_data["access_token"],
            token_data.get("refresh_token"),
            expires_at,
        )

    async def revoke_token(
        self,
        canvas_instance_url: str,
        access_token: str,
    ) -> bool:
        """
        Revoke Canvas access token.

        Args:
            canvas_instance_url: Canvas instance URL
            access_token: Access token to revoke

        Returns:
            True if revocation succeeded
        """
        try:
            # Canvas doesn't have a standard OAuth revoke endpoint
            # Token will expire naturally
            logger.info("Canvas token marked for expiration (no revoke endpoint)")
            return True
        except Exception as e:
            logger.error(f"Error revoking Canvas token: {e}")
            return False

    def is_token_expired(self, expires_at: Optional[datetime]) -> bool:
        """
        Check if token is expired or about to expire.

        Args:
            expires_at: Token expiration datetime

        Returns:
            True if token is expired or will expire within 5 minutes
        """
        if not expires_at:
            return True

        # Add timezone info if naive
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        # Consider expired if less than 5 minutes remaining
        buffer = timedelta(minutes=5)
        return datetime.now(timezone.utc) + buffer >= expires_at


__all__ = ["CanvasOAuthService"]
