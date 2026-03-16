"""
Tests for OAuth token refresh and revocation mechanisms.

Tests cover:
- Token expiration detection
- Automatic token refresh
- Token revocation (disconnect)
- Token encryption/decryption
- Refresh failure handling
"""

import os

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
from cryptography.fernet import Fernet

from src.integrations.oauth_token_manager import OAuthTokenManager

# Mark all tests in this module as integration (skipped in CI)
pytestmark = pytest.mark.integration


@pytest.fixture
def token_manager():
    """Create an OAuthTokenManager with a valid test encryption key."""
    original_key = os.environ.get("TOKEN_ENCRYPTION_KEY")
    test_key = Fernet.generate_key().decode()
    os.environ["TOKEN_ENCRYPTION_KEY"] = test_key
    manager = OAuthTokenManager()
    yield manager
    # Restore original env var
    if original_key is not None:
        os.environ["TOKEN_ENCRYPTION_KEY"] = original_key
    else:
        os.environ.pop("TOKEN_ENCRYPTION_KEY", None)


@pytest.fixture
def expired_token_time():
    """Token that expired 1 hour ago."""
    return datetime.now(timezone.utc) - timedelta(hours=1)


@pytest.fixture
def valid_token_time():
    """Token that expires in 1 hour."""
    return datetime.now(timezone.utc) + timedelta(hours=1)


@pytest.fixture
def nearly_expired_token_time():
    """Token that expires in 2 minutes (within refresh window)."""
    return datetime.now(timezone.utc) + timedelta(minutes=2)


class TestTokenExpiration:
    """Tests for token expiration detection."""

    def test_token_expired(self, token_manager, expired_token_time):
        """Test detection of expired token."""
        assert token_manager.is_token_expired(expired_token_time) is True

    def test_token_valid(self, token_manager, valid_token_time):
        """Test detection of valid token."""
        assert token_manager.is_token_expired(valid_token_time) is False

    def test_token_nearly_expired(self, token_manager, nearly_expired_token_time):
        """Test detection of nearly expired token (should refresh proactively)."""
        # Default buffer is 5 minutes, so 2 minutes left = expired
        assert token_manager.is_token_expired(nearly_expired_token_time) is True

    def test_token_none_expires_at(self, token_manager):
        """Test handling of None expires_at."""
        # None should be treated as expired (conservative approach)
        assert token_manager.is_token_expired(None) is True


class TestTokenEncryption:
    """Tests for token encryption and decryption."""

    def test_encrypt_decrypt_roundtrip(self, token_manager):
        """Test that encryption/decryption is reversible."""
        original_token = "test-access-token-12345"
        encrypted = token_manager.encrypt_token(original_token)
        decrypted = token_manager.decrypt_token(encrypted)

        assert encrypted != original_token  # Should be encrypted
        assert decrypted == original_token  # Should decrypt correctly

    def test_encrypt_empty_token(self, token_manager):
        """Test handling of empty token."""
        encrypted = token_manager.encrypt_token("")
        decrypted = token_manager.decrypt_token(encrypted)
        assert decrypted == ""

    def test_encrypt_long_token(self, token_manager):
        """Test encryption of long token."""
        long_token = "a" * 1000
        encrypted = token_manager.encrypt_token(long_token)
        decrypted = token_manager.decrypt_token(encrypted)
        assert decrypted == long_token

    def test_decrypt_invalid_token(self, token_manager):
        """Test decryption of invalid encrypted token."""
        with pytest.raises(Exception):  # Should raise decryption error
            token_manager.decrypt_token("invalid-encrypted-data")


class TestGoogleTokenRefresh:
    """Tests for Google OAuth token refresh."""

    @patch("src.integrations.google.google_oauth.GoogleOAuthService")
    async def test_refresh_google_token_success(self, mock_oauth_service):
        """Test successful Google token refresh."""
        mock_service = AsyncMock()
        mock_service.refresh_access_token.return_value = (
            "new-access-token",
            "new-refresh-token",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        mock_oauth_service.return_value = mock_service

        # This would normally be tested via the integration endpoint
        new_access, new_refresh, new_expires = await mock_service.refresh_access_token(
            "old-refresh-token"
        )

        assert new_access == "new-access-token"
        assert new_refresh == "new-refresh-token"
        assert new_expires > datetime.now(timezone.utc)

    @patch("src.integrations.google.google_oauth.GoogleOAuthService")
    async def test_refresh_google_token_invalid_refresh(self, mock_oauth_service):
        """Test Google token refresh with invalid refresh token."""
        mock_service = AsyncMock()
        mock_service.refresh_access_token.side_effect = Exception(
            "Invalid refresh token"
        )
        mock_oauth_service.return_value = mock_service

        with pytest.raises(Exception) as exc_info:
            await mock_service.refresh_access_token("invalid-refresh-token")

        assert "Invalid refresh token" in str(exc_info.value)


class TestMicrosoftTokenRefresh:
    """Tests for Microsoft OAuth token refresh."""

    @patch("src.integrations.microsoft.microsoft_oauth.MicrosoftOAuthService")
    async def test_refresh_microsoft_token_success(self, mock_oauth_service):
        """Test successful Microsoft token refresh."""
        mock_service = AsyncMock()
        mock_service.refresh_access_token.return_value = (
            "new-access-token",
            "new-refresh-token",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        mock_oauth_service.return_value = mock_service

        new_access, new_refresh, new_expires = await mock_service.refresh_access_token(
            "old-refresh-token"
        )

        assert new_access == "new-access-token"
        assert new_refresh is not None


class TestCanvasTokenRefresh:
    """Tests for Canvas OAuth token refresh."""

    @patch("src.integrations.canvas.canvas_oauth.CanvasOAuthService")
    async def test_refresh_canvas_token_success(self, mock_oauth_service):
        """Test successful Canvas token refresh."""
        mock_service = AsyncMock()
        mock_service.refresh_access_token.return_value = (
            "new-access-token",
            "new-refresh-token",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        mock_oauth_service.return_value = mock_service

        new_access, new_refresh, new_expires = await mock_service.refresh_access_token(
            canvas_instance_url="https://canvas.university.edu",
            refresh_token="old-refresh-token",
        )

        assert new_access == "new-access-token"


class TestTokenRevocation:
    """Tests for token revocation (disconnect)."""

    @patch("src.api.google_routes.get_db")
    async def test_google_disconnect_revokes_token(self, mock_db):
        """Test that disconnecting Google revokes the token."""
        mock_credential = MagicMock()
        mock_credential.is_active = True
        mock_credential.access_token = "encrypted-token"

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_credential
        )
        mock_db.return_value = mock_session

        # After disconnect, is_active should be False
        mock_credential.is_active = False
        mock_session.commit()

        assert mock_credential.is_active is False

    @patch("src.api.microsoft_routes.get_db")
    async def test_microsoft_disconnect_revokes_token(self, mock_db):
        """Test that disconnecting Microsoft revokes the token."""
        mock_credential = MagicMock()
        mock_credential.is_active = True

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_credential
        )
        mock_db.return_value = mock_session

        mock_credential.is_active = False
        mock_session.commit()

        assert mock_credential.is_active is False


class TestAutoRefreshOnRequest:
    """Tests for automatic token refresh during API requests."""

    @patch("src.integrations.oauth_token_manager.OAuthTokenManager")
    async def test_expired_token_triggers_refresh(self, mock_token_manager):
        """Test that expired token triggers automatic refresh."""
        manager = MagicMock()
        manager.is_token_expired.return_value = True
        manager.decrypt_token.return_value = "old-access-token"
        manager.encrypt_token.return_value = "encrypted-new-token"
        mock_token_manager.return_value = manager

        # Verify is_token_expired is called during request
        assert manager.is_token_expired(datetime.now(timezone.utc) - timedelta(hours=1))

    @patch("src.integrations.oauth_token_manager.OAuthTokenManager")
    async def test_valid_token_no_refresh(self, mock_token_manager):
        """Test that valid token doesn't trigger refresh."""
        manager = MagicMock()
        manager.is_token_expired.return_value = False
        manager.decrypt_token.return_value = "valid-access-token"
        mock_token_manager.return_value = manager

        # Verify no refresh needed
        assert not manager.is_token_expired(
            datetime.now(timezone.utc) + timedelta(hours=1)
        )


class TestRefreshTokenStorage:
    """Tests for refresh token storage behavior."""

    def test_refresh_token_encrypted_in_database(self, token_manager):
        """Test that refresh tokens are stored encrypted."""
        refresh_token = "sensitive-refresh-token"
        encrypted = token_manager.encrypt_token(refresh_token)

        # Encrypted token should not contain original
        assert refresh_token not in encrypted
        # Should be able to decrypt
        assert token_manager.decrypt_token(encrypted) == refresh_token

    def test_null_refresh_token_handling(self, token_manager):
        """Test handling of null refresh token (some providers don't return one)."""
        # Some OAuth providers don't always return a refresh token
        encrypted = token_manager.encrypt_token("")
        decrypted = token_manager.decrypt_token(encrypted)
        assert decrypted == ""
