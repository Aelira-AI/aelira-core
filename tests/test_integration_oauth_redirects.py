"""Integration OAuth callbacks keep redirects on the configured dashboard."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.main import app
from src.api import google_routes, microsoft_routes
from src.api.oauth_routes import integration_callback_redirect
from src.auth.redis_rate_limiter import OAuthStateManager
from src.config.settings import Settings


@pytest.fixture
def callback_settings(monkeypatch):
    settings = SimpleNamespace(
        dashboard_url="https://dashboard.example.test",
        public_api_url="https://api.example.test",
        microsoft_oauth_redirect_uri="https://api.example.test/auth/microsoft/callback",
    )
    monkeypatch.setattr(google_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(microsoft_routes, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def callback_db():
    db = MagicMock()
    app.dependency_overrides[google_routes.get_db_dependency] = lambda: db
    try:
        yield db
    finally:
        app.dependency_overrides.pop(google_routes.get_db_dependency, None)


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_provider_error_is_bounded_even_without_code_or_state(
    callback_settings, callback_db, provider
):
    with TestClient(app) as client:
        response = client.get(
            f"/{provider}/callback",
            params={"error": "denied&success=attacker@example.test#fragment"},
            follow_redirects=False,
        )
    assert response.status_code in {302, 307}
    assert response.headers["location"] == (
        "https://dashboard.example.test/integrations?error=oauth_failed"
    )


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_success_encodes_email_and_uses_integration_exchange_uri(
    callback_settings, callback_db, monkeypatch, provider
):
    manager = MagicMock()
    exchange = AsyncMock(
        return_value={
            "access_token": "provider-access",
            "refresh_token": "provider-refresh",
            "expires_at": datetime.now(timezone.utc),
            "user_id": "provider-user",
            "email": "faculty+tag&role=admin@example.edu",
            "name": "Faculty",
            "scopes": ["User.Read"],
        }
    )
    setattr(manager, f"exchange_{provider}_code", exchange)
    manager.encrypt_token.side_effect = lambda value: f"encrypted-{value}"
    module = google_routes if provider == "google" else microsoft_routes
    monkeypatch.setattr(module, "get_token_manager", lambda: manager)
    verify = MagicMock(
        side_effect=[
            (True, {"department_id": "department-1", "provider": provider}),
            (False, None),
        ]
    )
    monkeypatch.setattr(OAuthStateManager, "verify_and_consume_state", verify)

    with TestClient(app) as client:
        response = client.get(
            f"/{provider}/callback",
            params={"code": "auth-code", "state": "one-time-state"},
            follow_redirects=False,
        )
        replay = client.get(
            f"/{provider}/callback",
            params={"code": "auth-code", "state": "one-time-state"},
            follow_redirects=False,
        )

    assert response.status_code in {302, 307}
    target = urlsplit(response.headers["location"])
    assert target.scheme == "https"
    assert target.netloc == "dashboard.example.test"
    assert target.path == "/integrations"
    assert parse_qs(target.query) == {
        "success": [f"{provider}_connected"],
        "email": ["faculty+tag&role=admin@example.edu"],
    }
    assert "%26role%3Dadmin" in response.headers["location"]
    assert replay.headers["location"] == (
        "https://dashboard.example.test/integrations?error=invalid_state"
    )
    assert exchange.await_count == 1
    if provider == "microsoft":
        assert exchange.await_args.kwargs["redirect_uri"] == (
            "https://api.example.test/microsoft/callback"
        )


def test_microsoft_integration_uses_public_api_default_for_both_oauth_legs(
    monkeypatch, callback_db
):
    settings = Settings(
        _env_file=None,
        jwt_secret="unit-test-only-secret",
        database_url="postgresql://test:test@127.0.0.1:1/test",
    )
    assert settings.public_api_url == "http://localhost:8000"
    assert settings.microsoft_oauth_redirect_uri.endswith("/auth/microsoft/callback")
    assert settings.microsoft_oauth_redirect_uri != (
        "http://localhost:8000/microsoft/callback"
    )
    monkeypatch.setattr(microsoft_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(microsoft_routes, "require_feature", AsyncMock())
    monkeypatch.setattr(
        OAuthStateManager, "create_state", MagicMock(return_value="one-time-state")
    )
    monkeypatch.setattr(
        OAuthStateManager,
        "verify_and_consume_state",
        MagicMock(
            return_value=(
                True,
                {"department_id": "department-1", "provider": "microsoft"},
            )
        ),
    )
    callback_db.query.return_value.filter.return_value.first.return_value = None
    manager = MagicMock()
    manager.get_microsoft_auth_url.return_value = (
        "https://login.microsoftonline.com/authorize"
    )
    manager.exchange_microsoft_code = AsyncMock(
        return_value={
            "access_token": "provider-access",
            "refresh_token": "provider-refresh",
            "expires_at": datetime.now(timezone.utc),
            "email": "faculty@example.edu",
        }
    )
    manager.encrypt_token.side_effect = lambda value: f"encrypted-{value}"
    monkeypatch.setattr(microsoft_routes, "get_token_manager", lambda: manager)
    app.dependency_overrides[microsoft_routes.get_current_api_key] = lambda: (
        SimpleNamespace(department_id="department-1")
    )
    try:
        with TestClient(app) as client:
            connect = client.post(
                "/microsoft/connect",
                json={"redirect_uri": "http://localhost:8000/microsoft/callback"},
            )
            callback = client.get(
                "/microsoft/callback",
                params={"code": "auth-code", "state": "one-time-state"},
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.pop(microsoft_routes.get_current_api_key, None)

    assert connect.status_code == 200
    assert manager.get_microsoft_auth_url.call_args.kwargs["redirect_uri"] == (
        "http://localhost:8000/microsoft/callback"
    )
    assert manager.exchange_microsoft_code.await_args.kwargs["redirect_uri"] == (
        "http://localhost:8000/microsoft/callback"
    )
    assert callback.headers["location"].startswith(
        "http://localhost:5173/integrations?success=microsoft_connected"
    )


@pytest.mark.parametrize(
    "requested_uri",
    [
        "https://api.example.test/auth/microsoft/callback",
        "https://api.example.test/other/callback",
        "https://attacker.example.test/microsoft/callback",
    ],
)
def test_microsoft_connect_rejects_redirect_mismatch_before_state_creation(
    callback_settings, callback_db, monkeypatch, requested_uri
):
    monkeypatch.setattr(microsoft_routes, "require_feature", AsyncMock())
    create_state = MagicMock()
    monkeypatch.setattr(OAuthStateManager, "create_state", create_state)
    manager = MagicMock()
    monkeypatch.setattr(microsoft_routes, "get_token_manager", lambda: manager)
    callback_db.query.return_value.filter.return_value.first.return_value = None
    app.dependency_overrides[microsoft_routes.get_current_api_key] = lambda: (
        SimpleNamespace(department_id="department-1")
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/microsoft/connect", json={"redirect_uri": requested_uri}
            )
    finally:
        app.dependency_overrides.pop(microsoft_routes.get_current_api_key, None)
    assert response.status_code == 400
    create_state.assert_not_called()
    manager.get_microsoft_auth_url.assert_not_called()


def test_microsoft_connect_rejects_malformed_public_api_origin(
    callback_settings, callback_db, monkeypatch
):
    callback_settings.public_api_url = "https://trusted.example@evil.example"
    monkeypatch.setattr(microsoft_routes, "require_feature", AsyncMock())
    create_state = MagicMock()
    monkeypatch.setattr(OAuthStateManager, "create_state", create_state)
    callback_db.query.return_value.filter.return_value.first.return_value = None
    app.dependency_overrides[microsoft_routes.get_current_api_key] = lambda: (
        SimpleNamespace(department_id="department-1")
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/microsoft/connect",
                json={"redirect_uri": "https://evil.example/microsoft/callback"},
            )
    finally:
        app.dependency_overrides.pop(microsoft_routes.get_current_api_key, None)
    assert response.status_code == 503
    create_state.assert_not_called()


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_state_for_other_provider_cannot_complete_callback(
    callback_settings, callback_db, monkeypatch, provider
):
    other = "microsoft" if provider == "google" else "google"
    monkeypatch.setattr(
        OAuthStateManager,
        "verify_and_consume_state",
        MagicMock(
            return_value=(True, {"department_id": "department-1", "provider": other})
        ),
    )
    with TestClient(app) as client:
        response = client.get(
            f"/{provider}/callback",
            params={"code": "auth-code", "state": "other-provider-state"},
            follow_redirects=False,
        )
    assert response.headers["location"] == (
        "https://dashboard.example.test/integrations?error=invalid_state"
    )


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(1)",
        "https://trusted.example@evil.example",
        "https://good.example/path",
        "https://good.example?next=evil",
        "https://good.example?",
        "https://good.example#",
        "https://good.example%2f.evil.example",
        "https://good.example:",
        "https://good.example\\@evil.example",
        "https://good.example\r\nX-Injected: yes",
    ],
)
def test_malformed_dashboard_origin_fails_closed(bad_url):
    with pytest.raises(ValueError, match="OAuth origin"):
        integration_callback_redirect(bad_url, error="oauth_failed")


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_callback_does_not_redirect_to_malformed_configured_origin(
    callback_settings, callback_db, provider
):
    callback_settings.dashboard_url = "https://trusted.example@evil.example"
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/{provider}/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
    assert response.status_code == 500
    assert "location" not in response.headers


def test_callback_event_codes_are_bounded():
    with pytest.raises(ValueError, match="callback event"):
        integration_callback_redirect(
            "https://dashboard.example.test", error="raw-provider-error"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_programmatic_callback_does_not_reflect_exchange_exception(
    monkeypatch, provider
):
    module = google_routes if provider == "google" else microsoft_routes
    manager = MagicMock()
    setattr(
        manager,
        f"exchange_{provider}_code",
        AsyncMock(side_effect=RuntimeError("secret-provider-token-and-internal-path")),
    )
    monkeypatch.setattr(module, "get_token_manager", lambda: manager)
    callback = getattr(module, f"{provider}_callback")
    request_type = getattr(module, f"{provider.title()}CallbackRequest")
    request = request_type(
        code="auth-code",
        state="department-1:state",
        redirect_uri="https://api.example.test/callback",
    )
    with pytest.raises(HTTPException) as caught:
        await callback(
            request, SimpleNamespace(department_id="department-1"), MagicMock()
        )
    assert caught.value.status_code == 400
    assert "secret-provider-token" not in caught.value.detail


@pytest.mark.asyncio
async def test_microsoft_refresh_error_is_not_persisted(monkeypatch):
    credential = SimpleNamespace(
        token_expires_at=datetime.now(timezone.utc),
        refresh_token="encrypted-refresh",
        scopes=["User.Read"],
        is_active=True,
        last_error=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = credential
    manager = MagicMock()
    manager.is_token_expired.return_value = True
    manager.decrypt_token.return_value = "refresh-token"
    manager.refresh_microsoft_token = AsyncMock(
        side_effect=RuntimeError("secret-provider-token-and-internal-path")
    )
    monkeypatch.setattr(microsoft_routes, "get_token_manager", lambda: manager)
    with pytest.raises(HTTPException) as caught:
        await microsoft_routes.get_microsoft_credential(
            SimpleNamespace(department_id="department-1"), db
        )
    assert caught.value.status_code == 401
    assert credential.last_error == "Token refresh failed"
    assert "secret-provider-token" not in caught.value.detail
