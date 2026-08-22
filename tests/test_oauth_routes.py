from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
import src.api.oauth_routes as oauth_routes
from src.services.account_deletion_service import AccountDeletionService


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def oauth_settings(monkeypatch):
    settings = SimpleNamespace(
        google_oauth_client_id="google-client",
        google_oauth_client_secret="google-secret",
        google_oauth_redirect_uri="https://api.example.test/auth/google/callback",
        microsoft_oauth_client_id="microsoft-client",
        microsoft_oauth_client_secret="microsoft-secret",
        microsoft_oauth_redirect_uri="https://api.example.test/auth/microsoft/callback",
        microsoft_oauth_tenant_id="common",
        magic_link_base_url="https://dashboard.example.test",
        session_cookie_secure=True,
        session_cookie_samesite="lax",
        session_cookie_domain=None,
        jwt_access_token_expire_minutes=15,
        jwt_refresh_token_expire_days=30,
        env="test",
    )
    monkeypatch.setattr(oauth_routes, "get_settings", lambda: settings)
    return settings


@pytest.mark.parametrize("provider", ["google", "microsoft"])
@pytest.mark.parametrize(
    ("requested_next", "expected_next"),
    [
        (
            "/canvas/courses/42/content?tab=files",
            "/canvas/courses/42/content?tab=files",
        ),
        ("//evil.example", "/dashboard"),
        ("https://evil.example/steal", "/dashboard"),
        ("/\\evil.example", "/dashboard"),
    ],
)
def test_oauth_login_binds_safe_next_in_httponly_cookie_not_state(
    client, oauth_settings, provider, requested_next, expected_next
):
    response = client.get(
        f"/auth/{provider}/login",
        params={"next": requested_next},
        follow_redirects=False,
    )

    assert response.status_code in {302, 307}
    location = response.headers["location"]
    state = parse_qs(urlparse(location).query)["state"][0]
    assert requested_next not in state
    assert expected_next not in state
    assert response.cookies["oauth_next"].strip('"') == expected_next
    continuation_cookie = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith("oauth_next=")
    )
    assert "HttpOnly" in continuation_cookie
    assert "Max-Age=600" in continuation_cookie


class _FakeOAuthClient:
    def __init__(self, userinfo):
        self.userinfo = userinfo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, *args, **kwargs):
        response = MagicMock()
        response.json.return_value = {"access_token": "provider-token"}
        return response

    async def get(self, *args, **kwargs):
        response = MagicMock()
        response.json.return_value = self.userinfo
        return response


@pytest.mark.parametrize(
    ("provider", "userinfo"),
    [
        (
            "google",
            {
                "email": "prof@stanford.edu",
                "id": "google-id",
                "name": "Professor",
                "picture": None,
            },
        ),
        (
            "microsoft",
            {
                "mail": "prof@stanford.edu",
                "id": "microsoft-id",
                "displayName": "Professor",
            },
        ),
    ],
)
@pytest.mark.parametrize(
    ("stored_next", "expected_path"),
    [
        (
            "/canvas/courses/42/content?tab=files",
            "/canvas/courses/42/content?tab=files",
        ),
        ("//evil.example", "/dashboard"),
        ("https://evil.example/steal", "/dashboard"),
        ("/\\evil.example", "/dashboard"),
    ],
)
def test_oauth_callback_revalidates_cookie_redirects_and_clears_it(
    client, oauth_settings, monkeypatch, provider, userinfo, stored_next, expected_path
):
    from src.db.models import AuthProvider, User

    user = MagicMock(
        email="prof@stanford.edu",
        department_id="dept-1",
        auth_provider=AuthProvider.MAGIC_LINK,
        email_verified_at=None,
        role=None,
    )
    user.name = "Professor"
    department = MagicMock(id="dept-1")
    db = MagicMock()

    def query(model):
        result = MagicMock()
        result.filter.return_value = result
        result.first.return_value = user if model is User else department
        return result

    db.query.side_effect = query
    app.dependency_overrides[oauth_routes.get_db_dependency] = lambda: db
    session_service = MagicMock()
    session_service.create_session.return_value = (
        "access-token",
        "refresh-token",
        None,
        None,
    )
    monkeypatch.setattr(oauth_routes, "get_session_service", lambda: session_service)
    monkeypatch.setattr(
        oauth_routes.httpx,
        "AsyncClient",
        lambda: _FakeOAuthClient(userinfo),
    )

    try:
        response = client.get(
            f"/auth/{provider}/callback",
            params={"code": "code", "state": "csrf-state"},
            headers={"cookie": f"oauth_state=csrf-state; oauth_next={stored_next}"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_routes.get_db_dependency, None)

    assert response.status_code == 302
    assert response.headers["location"] == (
        f"{oauth_settings.magic_link_base_url}{expected_path}"
    )
    cleared_cookie = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith("oauth_next=")
    )
    assert "Max-Age=0" in cleared_cookie


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_oauth_error_redirect_is_safe_and_clears_continuation(
    client, oauth_settings, provider
):
    response = client.get(
        f"/auth/{provider}/callback",
        params={"error": "access_denied"},
        headers={"cookie": "oauth_next=//evil.example; oauth_state=csrf-state"},
        follow_redirects=False,
    )

    assert response.headers["location"] == (
        f"{oauth_settings.magic_link_base_url}/login?error=oauth_denied"
    )
    assert "evil.example" not in response.headers["location"]
    cleared = response.headers.get_list("set-cookie")
    assert any(
        value.startswith("oauth_next=") and "Max-Age=0" in value for value in cleared
    )
    assert any(
        value.startswith("oauth_state=") and "Max-Age=0" in value for value in cleared
    )


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_missing_callback_state_redirects_safely_and_clears_one_time_cookies(
    client, oauth_settings, provider
):
    response = client.get(
        f"/auth/{provider}/callback",
        params={"code": "code"},
        headers={"cookie": "oauth_state=csrf-state; oauth_next=/private/destination"},
        follow_redirects=False,
    )

    assert response.status_code in {302, 307}
    assert response.headers["location"] == (
        f"{oauth_settings.magic_link_base_url}/login?error=invalid_state"
    )
    cleared = response.headers.get_list("set-cookie")
    assert any(
        value.startswith("oauth_next=") and "Max-Age=0" in value for value in cleared
    )
    assert any(
        value.startswith("oauth_state=") and "Max-Age=0" in value for value in cleared
    )


@pytest.mark.parametrize(
    ("provider", "userinfo"),
    [
        (
            "google",
            {
                "email": "prof@stanford.edu",
                "id": "google-id",
                "name": "Professor",
                "picture": None,
            },
        ),
        (
            "microsoft",
            {
                "mail": "prof@stanford.edu",
                "id": "microsoft-id",
                "displayName": "Professor",
            },
        ),
    ],
)
def test_unexpected_oauth_callback_failure_redirects_safely_and_clears_cookies(
    client, oauth_settings, monkeypatch, provider, userinfo
):
    db = MagicMock()
    app.dependency_overrides[oauth_routes.get_db_dependency] = lambda: db
    monkeypatch.setattr(
        oauth_routes.httpx,
        "AsyncClient",
        lambda: _FakeOAuthClient(userinfo),
    )

    def fail_tier_check(_db, _email):
        raise RuntimeError("sensitive backend failure")

    monkeypatch.setattr(oauth_routes, "_check_oauth_tier", fail_tier_check)

    try:
        response = client.get(
            f"/auth/{provider}/callback",
            params={"code": "code", "state": "csrf-state"},
            headers={
                "cookie": "oauth_state=csrf-state; oauth_next=/private/destination"
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_routes.get_db_dependency, None)

    assert response.status_code in {302, 307}
    assert response.headers["location"] == (
        f"{oauth_settings.magic_link_base_url}/login?error=oauth_error"
    )
    assert "sensitive backend failure" not in response.headers["location"]
    cleared = response.headers.get_list("set-cookie")
    assert any(
        value.startswith("oauth_next=") and "Max-Age=0" in value for value in cleared
    )
    assert any(
        value.startswith("oauth_state=") and "Max-Age=0" in value for value in cleared
    )


@pytest.mark.parametrize(
    ("provider", "userinfo"),
    [
        (
            "google",
            {
                "email": "blocked@stanford.edu",
                "id": "google-id",
                "name": "Blocked User",
                "picture": None,
            },
        ),
        (
            "microsoft",
            {
                "mail": "blocked@stanford.edu",
                "id": "microsoft-id",
                "displayName": "Blocked User",
            },
        ),
    ],
)
def test_blocked_oauth_callback_redirects_safely_and_clears_one_time_cookies(
    client, oauth_settings, monkeypatch, provider, userinfo
):
    from src.db.models import User

    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = None
    db.query.return_value = query
    app.dependency_overrides[oauth_routes.get_db_dependency] = lambda: db
    monkeypatch.setattr(
        oauth_routes,
        "_check_oauth_tier",
        lambda _db, _email: (True, "OK", MagicMock(id="dept-1")),
    )
    monkeypatch.setattr(
        oauth_routes.httpx,
        "AsyncClient",
        lambda: _FakeOAuthClient(userinfo),
    )
    monkeypatch.setattr(
        AccountDeletionService,
        "is_email_blocked",
        MagicMock(return_value=(True, "sensitive internal block reason")),
    )

    try:
        response = client.get(
            f"/auth/{provider}/callback",
            params={"code": "code", "state": "csrf-state"},
            headers={
                "cookie": "oauth_state=csrf-state; oauth_next=/private/destination"
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_routes.get_db_dependency, None)

    assert db.query.call_args_list[-1].args[0] is User
    assert response.status_code in {302, 307}
    assert response.headers["location"] == (
        f"{oauth_settings.magic_link_base_url}/login?error=oauth_error"
    )
    assert "sensitive internal block reason" not in response.headers["location"]
    cleared = response.headers.get_list("set-cookie")
    assert any(
        value.startswith("oauth_next=") and "Max-Age=0" in value for value in cleared
    )
    assert any(
        value.startswith("oauth_state=") and "Max-Age=0" in value for value in cleared
    )


@pytest.mark.parametrize(
    ("provider", "userinfo"),
    [
        (
            "google",
            {
                "email": "inactive@stanford.edu",
                "id": "google-id",
                "name": "Inactive User",
                "picture": None,
            },
        ),
        (
            "microsoft",
            {
                "mail": "inactive@stanford.edu",
                "id": "microsoft-id",
                "displayName": "Inactive User",
            },
        ),
    ],
)
def test_inactive_existing_oauth_user_is_denied_without_session_or_mutation(
    client, oauth_settings, monkeypatch, provider, userinfo
):
    inactive_user = MagicMock(is_active=False)
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = inactive_user
    db.query.return_value = query
    app.dependency_overrides[oauth_routes.get_db_dependency] = lambda: db
    monkeypatch.setattr(
        oauth_routes.httpx,
        "AsyncClient",
        lambda: _FakeOAuthClient(userinfo),
    )
    session_service = MagicMock()
    monkeypatch.setattr(oauth_routes, "get_session_service", lambda: session_service)

    try:
        response = client.get(
            f"/auth/{provider}/callback",
            params={"code": "code", "state": "csrf-state"},
            headers={
                "cookie": "oauth_state=csrf-state; oauth_next=/private/destination"
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_routes.get_db_dependency, None)

    assert response.status_code in {302, 307}
    assert response.headers["location"] == (
        f"{oauth_settings.magic_link_base_url}/login?error=oauth_error"
    )
    db.commit.assert_not_called()
    session_service.create_session.assert_not_called()
    cleared = response.headers.get_list("set-cookie")
    assert any(
        value.startswith("oauth_next=") and "Max-Age=0" in value for value in cleared
    )
    assert any(
        value.startswith("oauth_state=") and "Max-Age=0" in value for value in cleared
    )


def test_oauth_status_does_not_reveal_active_inactive_or_unknown_account_state(
    client, oauth_settings, monkeypatch
):
    monkeypatch.setattr(
        oauth_routes.RateLimiter,
        "check_rate_limit",
        staticmethod(lambda _key, _limit: (True, 0)),
    )
    inactive_user = MagicMock(is_active=False)
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = inactive_user
    query.all.return_value = []
    db.query.return_value = query
    app.dependency_overrides[oauth_routes.get_db_dependency] = lambda: db

    try:
        inactive = client.get(
            "/auth/oauth/status", params={"email": "inactive@stanford.edu"}
        )
        query.first.return_value = None
        unknown = client.get(
            "/auth/oauth/status", params={"email": "unknown@stanford.edu"}
        )
        query.first.return_value = MagicMock(is_active=True)
        active = client.get(
            "/auth/oauth/status", params={"email": "active@stanford.edu"}
        )
    finally:
        app.dependency_overrides.pop(oauth_routes.get_db_dependency, None)

    assert inactive.status_code == 200
    assert unknown.status_code == 200
    assert active.status_code == 200
    assert inactive.json() == unknown.json() == active.json()
    assert active.json()["oauth_allowed"] is True
    assert "account_unavailable" not in str(inactive.json())


@pytest.mark.parametrize(
    ("provider", "userinfo"),
    [
        (
            "google",
            {
                "email": "active@stanford.edu",
                "id": "google-id",
                "name": "Active User",
                "picture": None,
            },
        ),
        (
            "microsoft",
            {
                "mail": "active@stanford.edu",
                "id": "microsoft-id",
                "displayName": "Active User",
            },
        ),
    ],
)
def test_session_creation_failure_rolls_back_oauth_user_mutation(
    client, oauth_settings, monkeypatch, provider, userinfo
):
    from src.db.models import AuthProvider

    active_user = MagicMock(
        is_active=True,
        id="user-1",
        department_id="dept-1",
        auth_provider=AuthProvider.MAGIC_LINK,
        email_verified_at=None,
    )
    department = MagicMock(id="dept-1")
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.side_effect = [active_user, department, active_user]
    db.query.return_value = query
    app.dependency_overrides[oauth_routes.get_db_dependency] = lambda: db
    monkeypatch.setattr(
        oauth_routes.httpx,
        "AsyncClient",
        lambda: _FakeOAuthClient(userinfo),
    )
    session_service = MagicMock()
    session_service.create_session.side_effect = RuntimeError(
        "session persistence failed"
    )
    monkeypatch.setattr(oauth_routes, "get_session_service", lambda: session_service)

    try:
        response = client.get(
            f"/auth/{provider}/callback",
            params={"code": "code", "state": "csrf-state"},
            headers={
                "cookie": "oauth_state=csrf-state; oauth_next=/private/destination"
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_routes.get_db_dependency, None)

    assert response.status_code in {302, 307}
    assert response.headers["location"] == (
        f"{oauth_settings.magic_link_base_url}/login?error=oauth_error"
    )
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
