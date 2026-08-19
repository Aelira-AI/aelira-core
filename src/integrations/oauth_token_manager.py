"""
OAuth Token Manager for Cloud Integrations

Handles:
- Token encryption/decryption at rest
- Token refresh for Google and Microsoft OAuth
- Token validation and expiration checking

Security:
- Uses Fernet symmetric encryption (AES-128-CBC)
- Encryption key stored in environment variable
- Tokens are encrypted before database storage
"""

from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
import httpx
import logging
import os
import time

logger = logging.getLogger(__name__)


class TokenEncryptionError(Exception):
    """Error during token encryption/decryption"""

    pass


class TokenRefreshError(Exception):
    """Error during OAuth token refresh"""

    pass


class OAuthTokenManager:
    """
    Manages OAuth tokens for cloud integrations.

    Provides encryption/decryption and automatic refresh for
    Google Workspace and Microsoft 365 OAuth tokens.
    """

    # Google OAuth endpoints
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

    # Microsoft OAuth endpoints (common tenant)
    MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    MICROSOFT_REVOKE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/logout"

    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize the token manager.

        Args:
            encryption_key: Base64-encoded Fernet key. If not provided,
                          reads from TOKEN_ENCRYPTION_KEY env var.
        """
        key = encryption_key or os.getenv("TOKEN_ENCRYPTION_KEY")

        if not key:
            env = os.getenv("ENV", "development").lower()
            if env in ("production", "staging"):
                raise TokenEncryptionError(
                    "TOKEN_ENCRYPTION_KEY must be set in production/staging. "
                    'Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
                )
            # Development only: generate temporary key with warning
            logger.warning(
                "TOKEN_ENCRYPTION_KEY not set. Generating temporary key. "
                "Tokens will be lost on restart. Set TOKEN_ENCRYPTION_KEY for persistence."
            )
            key = Fernet.generate_key().decode()

        try:
            # Ensure key is bytes
            if isinstance(key, str):
                key = key.encode()
            self._fernet = Fernet(key)
        except Exception as e:
            raise TokenEncryptionError(f"Invalid encryption key: {e}")

        # Load OAuth client credentials
        self._google_client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        self._google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
        self._microsoft_client_id = os.getenv("MICROSOFT_CLIENT_ID", "")
        self._microsoft_client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "")

    @staticmethod
    def generate_encryption_key() -> str:
        """
        Generate a new Fernet encryption key.

        Returns:
            Base64-encoded encryption key suitable for TOKEN_ENCRYPTION_KEY env var.
        """
        return Fernet.generate_key().decode()

    def encrypt_token(self, token: str) -> str:
        """
        Encrypt an OAuth token for storage.

        Args:
            token: Plain text token

        Returns:
            Encrypted token (base64 encoded)
        """
        try:
            encrypted = self._fernet.encrypt(token.encode())
            return encrypted.decode()
        except Exception as e:
            raise TokenEncryptionError(f"Failed to encrypt token: {e}")

    def decrypt_token(self, encrypted_token: str) -> str:
        """
        Decrypt an OAuth token from storage.

        Args:
            encrypted_token: Encrypted token (base64 encoded)

        Returns:
            Decrypted plain text token
        """
        try:
            decrypted = self._fernet.decrypt(encrypted_token.encode())
            return decrypted.decode()
        except Exception as e:
            raise TokenEncryptionError(f"Failed to decrypt token: {e}")

    def is_token_expired(
        self, expires_at: Optional[datetime], buffer_minutes: int = 5
    ) -> bool:
        """
        Check if a token is expired or about to expire.

        Args:
            expires_at: Token expiration timestamp (None treated as expired)
            buffer_minutes: Minutes before expiration to consider expired

        Returns:
            True if token is expired or will expire within buffer
        """
        if expires_at is None:
            return True

        # Ensure timezone-aware comparison
        now = datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        return now >= expires_at - timedelta(minutes=buffer_minutes)

    async def refresh_google_token(
        self, refresh_token: str
    ) -> Tuple[str, str, datetime]:
        """
        Refresh a Google OAuth access token.

        Args:
            refresh_token: The refresh token (decrypted)

        Returns:
            Tuple of (new_access_token, new_refresh_token, new_expiration)

        Raises:
            TokenRefreshError: If refresh fails
        """
        if not self._google_client_id or not self._google_client_secret:
            raise TokenRefreshError("Google OAuth credentials not configured")

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self._google_client_id,
                        "client_secret": self._google_client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                response.raise_for_status()
                data = response.json()

                access_token = data["access_token"]
                # Google doesn't always return a new refresh token
                new_refresh_token = data.get("refresh_token", refresh_token)
                expires_in = data.get("expires_in", 3600)
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

                logger.info("Successfully refreshed Google OAuth token")
                return access_token, new_refresh_token, expires_at

            except httpx.HTTPStatusError as e:
                logger.error(f"Google token refresh failed: {e.response.text}")
                raise TokenRefreshError(f"Google token refresh failed: {e}")
            except Exception as e:
                logger.error(f"Google token refresh error: {e}")
                raise TokenRefreshError(f"Google token refresh error: {e}")

    async def refresh_microsoft_token(
        self, refresh_token: str, scopes: Optional[list] = None
    ) -> Tuple[str, str, datetime]:
        """
        Refresh a Microsoft OAuth access token.

        Args:
            refresh_token: The refresh token (decrypted)
            scopes: Optional list of scopes to request

        Returns:
            Tuple of (new_access_token, new_refresh_token, new_expiration)

        Raises:
            TokenRefreshError: If refresh fails
        """
        if not self._microsoft_client_id or not self._microsoft_client_secret:
            raise TokenRefreshError("Microsoft OAuth credentials not configured")

        # Default scopes if not provided
        if scopes is None:
            scopes = [
                "Files.Read.All",
                "Files.ReadWrite.All",
                "Sites.Read.All",
                "User.Read",
                "offline_access",
            ]

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.MICROSOFT_TOKEN_URL,
                    data={
                        "client_id": self._microsoft_client_id,
                        "client_secret": self._microsoft_client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                        "scope": " ".join(scopes),
                    },
                )
                response.raise_for_status()
                data = response.json()

                access_token = data["access_token"]
                new_refresh_token = data.get("refresh_token", refresh_token)
                expires_in = data.get("expires_in", 3600)
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

                logger.info("Successfully refreshed Microsoft OAuth token")
                return access_token, new_refresh_token, expires_at

            except httpx.HTTPStatusError as e:
                logger.error(f"Microsoft token refresh failed: {e.response.text}")
                raise TokenRefreshError(f"Microsoft token refresh failed: {e}")
            except Exception as e:
                logger.error(f"Microsoft token refresh error: {e}")
                raise TokenRefreshError(f"Microsoft token refresh error: {e}")

    async def refresh_token(
        self,
        provider: str,
        refresh_token: str,
        scopes: Optional[list] = None,
    ) -> Tuple[str, str, datetime]:
        """
        Refresh an OAuth token for any supported provider.

        Args:
            provider: 'google' or 'microsoft'
            refresh_token: The refresh token (decrypted)
            scopes: Optional scopes (Microsoft only)

        Returns:
            Tuple of (new_access_token, new_refresh_token, new_expiration)
        """
        if provider == "google":
            return await self.refresh_google_token(refresh_token)
        elif provider == "microsoft":
            return await self.refresh_microsoft_token(refresh_token, scopes)
        else:
            raise TokenRefreshError(f"Unsupported provider: {provider}")

    async def refresh_if_expired(
        self,
        credential,  # CloudOAuthCredentials
        db,  # SQLAlchemy Session
        lock_timeout: int = 30,
    ) -> str:
        """
        Refresh the credential's access token if expired, with distributed locking.

        Uses Redis SET NX EX to prevent concurrent refresh attempts for the same
        credential. If Redis is unavailable, falls back to unlocked refresh.

        Args:
            credential: CloudOAuthCredentials ORM object
            db: SQLAlchemy Session (will be committed on refresh)
            lock_timeout: Seconds to hold the Redis lock (default 30)

        Returns:
            Decrypted access token (refreshed if needed)

        Raises:
            TokenRefreshError: If refresh fails after acquiring lock
        """
        from ..db.models import CloudProvider

        canvas_instance_url = None
        if credential.provider == CloudProvider.CANVAS.value:
            from ..utils.security import (
                require_persisted_canvas_origin,
                resolve_canvas_network_origin,
            )

            canvas_instance_url = resolve_canvas_network_origin(
                require_persisted_canvas_origin(credential)
            )

        if not self.is_token_expired(credential.token_expires_at):
            return self.decrypt_token(credential.access_token)

        # Try to acquire distributed lock via Redis
        lock_key = f"token_refresh:{credential.id}"
        redis_client = None
        lock_acquired = False

        try:
            from ..auth.redis_rate_limiter import get_redis_client

            redis_client = get_redis_client()
        except Exception:
            pass

        if redis_client is not None:
            # Spin-wait up to lock_timeout seconds for the lock
            for _ in range(lock_timeout * 2):
                lock_acquired = redis_client.set(
                    lock_key, "1", nx=True, ex=lock_timeout
                )
                if lock_acquired:
                    break
                # Another worker is refreshing — wait and re-check expiry
                time.sleep(0.5)
                db.refresh(credential)
                if not self.is_token_expired(credential.token_expires_at):
                    return self.decrypt_token(credential.access_token)

            if not lock_acquired:
                # Timed out waiting; try to return current token anyway
                db.refresh(credential)
                return self.decrypt_token(credential.access_token)

        try:
            # Re-check after acquiring lock (another worker may have refreshed)
            db.refresh(credential)
            if not self.is_token_expired(credential.token_expires_at):
                return self.decrypt_token(credential.access_token)

            refresh_token = self.decrypt_token(credential.refresh_token)

            # Dispatch based on provider
            if credential.provider == CloudProvider.GOOGLE.value:
                new_access, new_refresh, new_expires = await self.refresh_google_token(
                    refresh_token
                )
            elif credential.provider == CloudProvider.MICROSOFT.value:
                new_access, new_refresh, new_expires = (
                    await self.refresh_microsoft_token(
                        refresh_token, scopes=credential.scopes
                    )
                )
            elif credential.provider == CloudProvider.CANVAS.value:
                from ..integrations.canvas import CanvasOAuthService

                assert canvas_instance_url is not None
                canvas_oauth = CanvasOAuthService()
                new_access, new_refresh, new_expires = (
                    await canvas_oauth.refresh_access_token(
                        canvas_instance_url=canvas_instance_url,
                        refresh_token=refresh_token,
                    )
                )
            elif credential.provider == CloudProvider.BLACKBOARD.value:
                from ..integrations.blackboard import BlackboardOAuthService

                blackboard_oauth = BlackboardOAuthService()
                blackboard_instance_url = credential.provider_metadata.get(
                    "blackboard_instance_url"
                )
                new_access, new_refresh, new_expires = (
                    await blackboard_oauth.refresh_access_token(
                        blackboard_instance_url=blackboard_instance_url,
                        refresh_token=refresh_token,
                    )
                )
            else:
                raise TokenRefreshError(f"Unsupported provider: {credential.provider}")

            # Persist encrypted tokens
            credential.access_token = self.encrypt_token(new_access)
            if new_refresh:
                credential.refresh_token = self.encrypt_token(new_refresh)
            credential.token_expires_at = new_expires
            db.commit()

            logger.info(
                f"Refreshed OAuth token for credential {credential.id} "
                f"(provider={credential.provider})"
            )
            return new_access

        finally:
            # Release lock
            if redis_client is not None and lock_acquired:
                try:
                    redis_client.delete(lock_key)
                except Exception:
                    pass  # Lock will expire via TTL

    async def revoke_google_token(self, token: str) -> bool:
        """
        Revoke a Google OAuth token.

        Args:
            token: Access token or refresh token to revoke

        Returns:
            True if revocation succeeded
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.GOOGLE_REVOKE_URL,
                    params={"token": token},
                )
                # Google returns 200 on success
                return response.status_code == 200
            except Exception as e:
                logger.error(f"Failed to revoke Google token: {e}")
                return False

    async def revoke_microsoft_token(self, refresh_token: str) -> bool:
        """
        Revoke a Microsoft OAuth token.

        Note: Microsoft doesn't have a direct revoke endpoint for refresh tokens.
        The best approach is to redirect the user to the logout URL.

        Args:
            refresh_token: The refresh token to invalidate

        Returns:
            True (Microsoft handles this through session logout)
        """
        # Microsoft doesn't support programmatic token revocation
        # Tokens can only be revoked through user-initiated logout
        # or by changing app permissions in Azure portal
        logger.info("Microsoft token revocation requested (user logout required)")
        return True

    def get_google_auth_url(
        self,
        redirect_uri: str,
        scopes: Optional[list] = None,
        state: Optional[str] = None,
    ) -> str:
        """
        Generate Google OAuth authorization URL.

        Args:
            redirect_uri: OAuth callback URL
            scopes: List of OAuth scopes to request
            state: Optional state parameter for CSRF protection

        Returns:
            Authorization URL to redirect user to
        """
        if scopes is None:
            scopes = [
                "https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/drive.file",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ]

        params = {
            "client_id": self._google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",  # Request refresh token
            "prompt": "consent",  # Force consent to get refresh token
        }

        if state:
            params["state"] = state

        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

    def get_microsoft_auth_url(
        self,
        redirect_uri: str,
        scopes: Optional[list] = None,
        state: Optional[str] = None,
        tenant: str = "common",
    ) -> str:
        """
        Generate Microsoft OAuth authorization URL.

        Args:
            redirect_uri: OAuth callback URL
            scopes: List of OAuth scopes to request
            state: Optional state parameter for CSRF protection
            tenant: Azure AD tenant (default 'common' for any account)

        Returns:
            Authorization URL to redirect user to
        """
        if scopes is None:
            scopes = [
                "Files.Read.All",
                "Files.ReadWrite.All",
                "Sites.Read.All",
                "User.Read",
                "offline_access",
            ]

        params = {
            "client_id": self._microsoft_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "response_mode": "query",
        }

        if state:
            params["state"] = state

        query = "&".join(f"{k}={v}" for k, v in params.items())
        return (
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{query}"
        )

    async def exchange_google_code(
        self, code: str, redirect_uri: str
    ) -> Dict[str, Any]:
        """
        Exchange Google authorization code for tokens.

        Args:
            code: Authorization code from OAuth callback
            redirect_uri: Same redirect URI used in auth request

        Returns:
            Dict with access_token, refresh_token, expires_at, user_info
        """
        async with httpx.AsyncClient() as client:
            # Exchange code for tokens
            response = await client.post(
                self.GOOGLE_TOKEN_URL,
                data={
                    "client_id": self._google_client_id,
                    "client_secret": self._google_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            response.raise_for_status()
            token_data = response.json()

            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 3600)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            # Get user info
            user_response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_response.raise_for_status()
            user_info = user_response.json()

            return {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "scopes": token_data.get("scope", "").split(),
                "user_id": user_info.get("id"),
                "email": user_info.get("email"),
                "name": user_info.get("name"),
            }

    async def exchange_microsoft_code(
        self, code: str, redirect_uri: str, scopes: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Exchange Microsoft authorization code for tokens.

        Args:
            code: Authorization code from OAuth callback
            redirect_uri: Same redirect URI used in auth request
            scopes: Scopes to request (same as auth request)

        Returns:
            Dict with access_token, refresh_token, expires_at, user_info
        """
        if scopes is None:
            scopes = [
                "Files.Read.All",
                "Files.ReadWrite.All",
                "Sites.Read.All",
                "User.Read",
                "offline_access",
            ]

        async with httpx.AsyncClient() as client:
            # Exchange code for tokens
            response = await client.post(
                self.MICROSOFT_TOKEN_URL,
                data={
                    "client_id": self._microsoft_client_id,
                    "client_secret": self._microsoft_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                    "scope": " ".join(scopes),
                },
            )
            response.raise_for_status()
            token_data = response.json()

            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 3600)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            # Get user info from Graph API
            user_response = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_response.raise_for_status()
            user_info = user_response.json()

            return {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "scopes": token_data.get("scope", "").split(),
                "user_id": user_info.get("id"),
                "email": user_info.get("mail") or user_info.get("userPrincipalName"),
                "name": user_info.get("displayName"),
            }


# Singleton instance
_token_manager: Optional[OAuthTokenManager] = None


def get_token_manager() -> OAuthTokenManager:
    """Get the singleton token manager instance."""
    global _token_manager
    if _token_manager is None:
        _token_manager = OAuthTokenManager()
    return _token_manager
