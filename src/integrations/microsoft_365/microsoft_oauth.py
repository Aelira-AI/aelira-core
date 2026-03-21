"""
Microsoft OAuth Service

Provides OAuth 2.0 authentication for Microsoft 365 using MSAL.
This is a facade over the main OAuthTokenManager for Microsoft-specific operations.
"""

import logging
from typing import List, Optional, Dict, Any

from ..oauth_token_manager import OAuthTokenManager, get_token_manager

logger = logging.getLogger(__name__)


class MicrosoftOAuthService:
    """
    Microsoft OAuth 2.0 service using MSAL.

    Provides OAuth flow management for Microsoft 365 integration.
    This is a wrapper around the main OAuthTokenManager for Microsoft-specific operations.
    """

    def __init__(self, token_manager: OAuthTokenManager = None):
        """
        Initialize Microsoft OAuth service.

        Args:
            token_manager: OAuth token manager instance (optional)
        """
        self.token_manager = token_manager or get_token_manager()

    def get_authorization_url(
        self,
        redirect_uri: str,
        scopes: Optional[List[str]] = None,
        state: str = None,
    ) -> str:
        """
        Get Microsoft OAuth authorization URL.

        Args:
            redirect_uri: URI to redirect after OAuth
            scopes: OAuth scopes to request
            state: State parameter for verification

        Returns:
            Authorization URL
        """
        return self.token_manager.get_microsoft_auth_url(
            redirect_uri=redirect_uri,
            scopes=scopes,
            state=state,
        )

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        """
        Exchange authorization code for tokens.

        Args:
            code: Authorization code from Microsoft
            redirect_uri: Same redirect_uri used in get_authorization_url

        Returns:
            Dict with token data and user info
        """
        return await self.token_manager.exchange_microsoft_code(
            code=code,
            redirect_uri=redirect_uri,
        )

    def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Refresh Microsoft OAuth tokens.

        Args:
            refresh_token: Refresh token

        Returns:
            Dict with new access token and expiry
        """
        return self.token_manager.refresh_microsoft_token(refresh_token)

    def revoke_token(self, token: str) -> bool:
        """
        Revoke Microsoft OAuth token.

        Args:
            token: Access or refresh token to revoke

        Returns:
            True if revoked successfully
        """
        return self.token_manager.revoke_microsoft_token(token)

    def is_connected(self, department_id: str, db) -> bool:
        """
        Check if Microsoft 365 is connected for a department.

        Args:
            department_id: Department ID
            db: Database session

        Returns:
            True if connected
        """
        from ...db.models import CloudOAuthCredentials, CloudProvider

        credential = (
            db.query(CloudOAuthCredentials)
            .filter(
                CloudOAuthCredentials.department_id == department_id,
                CloudOAuthCredentials.provider == CloudProvider.MICROSOFT.value,
                CloudOAuthCredentials.is_active,
            )
            .first()
        )

        return credential is not None

    def get_account_info(self, department_id: str, db) -> Optional[Dict[str, Any]]:
        """
        Get connected Microsoft account information.

        Args:
            department_id: Department ID
            db: Database session

        Returns:
            Dict with account info or None if not connected
        """
        from ...db.models import CloudOAuthCredentials, CloudProvider

        credential = (
            db.query(CloudOAuthCredentials)
            .filter(
                CloudOAuthCredentials.department_id == department_id,
                CloudOAuthCredentials.provider == CloudProvider.MICROSOFT.value,
                CloudOAuthCredentials.is_active,
            )
            .first()
        )

        if not credential:
            return None

        return {
            "email": credential.provider_email,
            "name": credential.provider_name,
            "connected_at": (
                credential.created_at.isoformat() if credential.created_at else None
            ),
            "last_sync_at": (
                credential.last_sync_at.isoformat() if credential.last_sync_at else None
            ),
        }


__all__ = ["MicrosoftOAuthService"]
