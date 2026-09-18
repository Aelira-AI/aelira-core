"""
Google OAuth transport contracts.

Authenticated file workflows live in test_google_workspace_e2e.py; those backend
route tests replace the former permissive HTTP assertions in this module.
"""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.integrations import oauth_token_manager
from src.integrations.google_workspace.google_oauth import GoogleOAuthService
from src.integrations.oauth_token_manager import OAuthTokenManager

# OAuth transport contracts remain independent of the route fixtures.
pytestmark = pytest.mark.integration


@pytest.fixture
def google_token_manager():
    """Create the production token manager with deterministic credentials."""
    manager = OAuthTokenManager(OAuthTokenManager.generate_encryption_key())
    manager._google_client_id = "google-client-id"
    manager._google_client_secret = "google-client-secret"
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


class TestGoogleOAuthFlow:
    """Tests for Google OAuth 2.0 connection flow."""

    def test_google_connect_returns_auth_url(self, google_token_manager):
        """Build the authorization request through the production service."""
        service = GoogleOAuthService(google_token_manager)

        auth_url = service.get_authorization_url(
            redirect_uri="https://dashboard.example/callback",
            scopes=["drive.readonly", "userinfo.email"],
            state="csrf-state",
        )

        parsed = urlparse(auth_url)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == "accounts.google.com"
        assert parsed.path == "/o/oauth2/v2/auth"
        assert query == {
            "client_id": ["google-client-id"],
            "redirect_uri": ["https://dashboard.example/callback"],
            "response_type": ["code"],
            "scope": ["drive.readonly userinfo.email"],
            "access_type": ["offline"],
            "prompt": ["consent"],
            "state": ["csrf-state"],
        }

    async def test_google_callback_exchanges_code(
        self, google_token_manager, monkeypatch
    ):
        """Exchange code and load identity through the production HTTP seam."""
        requests = []

        def handler(request):
            requests.append(request)
            if request.url == httpx.URL(google_token_manager.GOOGLE_TOKEN_URL):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-token",
                        "refresh_token": "refresh-token",
                        "expires_in": 3600,
                        "scope": "drive.readonly userinfo.email",
                    },
                )
            if request.url == httpx.URL(
                "https://www.googleapis.com/oauth2/v2/userinfo"
            ):
                return httpx.Response(
                    200,
                    json={
                        "id": "google-user-id",
                        "email": "faculty@example.edu",
                        "name": "Test Faculty",
                    },
                )
            raise AssertionError(f"Unexpected Google OAuth request: {request.url}")

        install_httpx_transport(monkeypatch, handler)
        service = GoogleOAuthService(google_token_manager)

        token_data = await service.exchange_code(
            code="authorization-code",
            redirect_uri="https://dashboard.example/callback",
        )

        assert token_data["access_token"] == "access-token"
        assert token_data["refresh_token"] == "refresh-token"
        assert token_data["scopes"] == ["drive.readonly", "userinfo.email"]
        assert token_data["user_id"] == "google-user-id"
        assert token_data["email"] == "faculty@example.edu"
        assert token_data["name"] == "Test Faculty"
        assert [request.method for request in requests] == ["POST", "GET"]
        assert parse_qs(requests[0].content.decode()) == {
            "client_id": ["google-client-id"],
            "client_secret": ["google-client-secret"],
            "code": ["authorization-code"],
            "grant_type": ["authorization_code"],
            "redirect_uri": ["https://dashboard.example/callback"],
        }
        assert requests[1].headers["Authorization"] == "Bearer access-token"

    async def test_google_disconnect_revokes_access(
        self, google_token_manager, monkeypatch
    ):
        """Revoke access through the production HTTP seam."""
        requests = []

        def handler(request):
            requests.append(request)
            if request.url != httpx.URL(
                f"{google_token_manager.GOOGLE_REVOKE_URL}?token=refresh-token"
            ):
                raise AssertionError(f"Unexpected Google revoke request: {request.url}")
            return httpx.Response(200)

        install_httpx_transport(monkeypatch, handler)

        revoked = await google_token_manager.revoke_google_token("refresh-token")

        assert revoked is True
        assert len(requests) == 1
        assert requests[0].method == "POST"
