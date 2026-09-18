"""
Tests for Microsoft 365 integration.

Tests cover:
- OAuth 2.0 connection flow with Microsoft Identity Platform
- Microsoft Graph API operations
- OneDrive file operations
- SharePoint site and library access
- Document scanning and remediation
"""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.integrations import oauth_token_manager
from src.integrations.microsoft_365.microsoft_oauth import MicrosoftOAuthService
from src.integrations.oauth_token_manager import OAuthTokenManager

# OAuth service transport tests; no real Microsoft account is used.
pytestmark = pytest.mark.integration


@pytest.fixture
def microsoft_token_manager():
    """Create the production token manager with deterministic credentials."""
    manager = OAuthTokenManager(OAuthTokenManager.generate_encryption_key())
    manager._microsoft_client_id = "microsoft-client-id"
    manager._microsoft_client_secret = "microsoft-client-secret"
    return manager


def install_httpx_transport(monkeypatch, handler):
    """Route the production AsyncClient through a fail-closed mock transport."""
    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        oauth_token_manager.httpx,
        "AsyncClient",
        lambda *args, **kwargs: async_client(*args, transport=transport, **kwargs),
    )


class TestMicrosoftOAuthFlow:
    """Tests for Microsoft OAuth 2.0 connection flow."""

    def test_microsoft_connect_returns_auth_url(self, microsoft_token_manager):
        """Build the authorization request through the production service."""
        service = MicrosoftOAuthService(microsoft_token_manager)

        auth_url = service.get_authorization_url(
            redirect_uri="https://dashboard.example/callback",
            scopes=["User.Read", "offline_access"],
            state="csrf-state",
        )

        parsed = urlparse(auth_url)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == "login.microsoftonline.com"
        assert parsed.path == "/common/oauth2/v2.0/authorize"
        assert query == {
            "client_id": ["microsoft-client-id"],
            "redirect_uri": ["https://dashboard.example/callback"],
            "response_type": ["code"],
            "scope": ["User.Read offline_access"],
            "response_mode": ["query"],
            "state": ["csrf-state"],
        }

    async def test_microsoft_callback_exchanges_code(
        self, microsoft_token_manager, monkeypatch
    ):
        """Exchange code and load identity through the production HTTP seam."""
        requests = []

        def handler(request):
            requests.append(request)
            if request.url == httpx.URL(microsoft_token_manager.MICROSOFT_TOKEN_URL):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-token",
                        "refresh_token": "refresh-token",
                        "expires_in": 3600,
                        "scope": "User.Read offline_access",
                    },
                )
            if request.url == httpx.URL("https://graph.microsoft.com/v1.0/me"):
                return httpx.Response(
                    200,
                    json={
                        "id": "microsoft-user-id",
                        "mail": "faculty@example.edu",
                        "displayName": "Test Faculty",
                    },
                )
            raise AssertionError(f"Unexpected Microsoft OAuth request: {request.url}")

        install_httpx_transport(monkeypatch, handler)
        service = MicrosoftOAuthService(microsoft_token_manager)

        token_data = await service.exchange_code(
            code="authorization-code",
            redirect_uri="https://dashboard.example/callback",
        )

        assert token_data["access_token"] == "access-token"
        assert token_data["refresh_token"] == "refresh-token"
        assert token_data["scopes"] == ["User.Read", "offline_access"]
        assert token_data["user_id"] == "microsoft-user-id"
        assert token_data["email"] == "faculty@example.edu"
        assert token_data["name"] == "Test Faculty"
        assert [request.method for request in requests] == ["POST", "GET"]
        assert parse_qs(requests[0].content.decode()) == {
            "client_id": ["microsoft-client-id"],
            "client_secret": ["microsoft-client-secret"],
            "code": ["authorization-code"],
            "grant_type": ["authorization_code"],
            "redirect_uri": ["https://dashboard.example/callback"],
            "scope": [
                "Files.Read.All Files.ReadWrite.All Sites.Read.All User.Read offline_access"
            ],
        }
        assert requests[1].headers["Authorization"] == "Bearer access-token"

    async def test_microsoft_token_refresh(self, microsoft_token_manager, monkeypatch):
        """Refresh an expired token through the production HTTP seam."""
        requests = []

        def handler(request):
            requests.append(request)
            if request.url != httpx.URL(microsoft_token_manager.MICROSOFT_TOKEN_URL):
                raise AssertionError(
                    f"Unexpected Microsoft refresh request: {request.url}"
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access-token",
                    "refresh_token": "new-refresh-token",
                    "expires_in": 1800,
                },
            )

        install_httpx_transport(monkeypatch, handler)

        access_token, refresh_token, expires_at = (
            await microsoft_token_manager.refresh_microsoft_token("old-refresh-token")
        )

        assert access_token == "new-access-token"
        assert refresh_token == "new-refresh-token"
        assert expires_at.tzinfo is not None
        assert len(requests) == 1
        assert parse_qs(requests[0].content.decode()) == {
            "client_id": ["microsoft-client-id"],
            "client_secret": ["microsoft-client-secret"],
            "refresh_token": ["old-refresh-token"],
            "grant_type": ["refresh_token"],
            "scope": [
                "Files.Read.All Files.ReadWrite.All Sites.Read.All User.Read offline_access"
            ],
        }

    @pytest.mark.parametrize(
        ("failure", "error_type"),
        [
            ("provider", httpx.HTTPStatusError),
            ("transport", httpx.ConnectError),
        ],
    )
    async def test_microsoft_exchange_failures_use_real_http_path(
        self, microsoft_token_manager, monkeypatch, caplog, failure, error_type
    ):
        """Keep provider and transport failures bounded at the real seam."""

        def handler(request):
            assert request.method == "POST"
            assert request.url == httpx.URL(microsoft_token_manager.MICROSOFT_TOKEN_URL)
            if failure == "provider":
                return httpx.Response(
                    400,
                    json={"error": "invalid_grant", "error_description": "rejected"},
                )
            raise httpx.ConnectError("test transport unavailable", request=request)

        install_httpx_transport(monkeypatch, handler)
        service = MicrosoftOAuthService(microsoft_token_manager)

        with pytest.raises(error_type):
            await service.exchange_code(
                code="sensitive-authorization-code",
                redirect_uri="https://dashboard.example/callback",
            )

        assert "sensitive-authorization-code" not in caplog.text
        assert "microsoft-client-secret" not in caplog.text
