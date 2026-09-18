"""Successful auth route contexts fail visibly when their collaborator fails.

PostgreSQL-backed profile fixture supplies a trusted identity. API key and
session services are controlled here; this is HTTP failure mapping evidence.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import test_profile_session_routes as profile_fixtures
from src.api import auth_routes as routes
from src.auth.dependencies import AuthenticatedPrincipal

profile_route = profile_fixtures.profile_route


def test_key_validation_failure_after_success(profile_route, monkeypatch):
    case = profile_route
    key = SimpleNamespace(
        id="fixture-key",
        name="Fixture key",
        key_prefix="fixture",
        user_id=case.user.id,
        department_id=case.department.id,
        rate_limit_per_hour=100,
    )
    validator = MagicMock(return_value=key)
    monkeypatch.setattr(routes.AuthService, "validate_api_key", validator)
    monkeypatch.setattr(
        routes.RateLimiter, "check_rate_limit", lambda *args, **kwargs: (True, {})
    )
    headers = {"Authorization": "Bearer synthetic-fixture-key"}
    assert case.client.get("/auth/keys/validate", headers=headers).status_code == 200
    validator.side_effect = RuntimeError("synthetic key store unavailable")
    response = case.client.get("/auth/keys/validate", headers=headers)
    assert response.status_code == 500
    assert "synthetic" not in response.text


@pytest.mark.parametrize("path", ["/auth/profile", "/auth/profile/email-preferences"])
def test_profile_update_requires_identity(profile_route, path):
    case = profile_route
    case.client.app.dependency_overrides.pop(routes.get_required_api_key)
    case.client.cookies.clear()
    assert case.client.patch(path, json={}).status_code == 401


@pytest.mark.parametrize(
    "method,path,service,result,body",
    [
        ("post", "/auth/keys", "create_api_key", "created", {"name": "Example key"}),
        ("get", "/auth/keys", "list_api_keys", [], None),
        ("delete", "/auth/keys/example-key", "revoke_api_key", True, None),
    ],
)
def test_key_service_failure_after_success(
    profile_route, monkeypatch, method, path, service, result, body
):
    case = profile_route
    case.client.app.dependency_overrides[routes.get_key_management_principal] = (
        lambda: AuthenticatedPrincipal(
            None, case.user.id, case.department.id, case.user.role, "session"
        )
    )
    monkeypatch.setattr(routes, "get_audit_service", lambda db: MagicMock())
    if result == "created":
        key = SimpleNamespace(
            id="example-key",
            name="Example key",
            key_prefix="test_prefix",
            rate_limit_per_hour=100,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            expires_at=None,
            is_active=True,
        )
        result = (key, "synthetic-test-key")
    collaborator = MagicMock(return_value=result)
    monkeypatch.setattr(routes.AuthService, service, collaborator)

    def request():
        return case.client.request(method, path, json=body)

    assert request().status_code == 200
    collaborator.side_effect = RuntimeError("synthetic collaborator unavailable")
    response = request()
    assert response.status_code == 500
    assert "synthetic" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/auth/departments/{department}",
        "/auth/validate",
        "/auth/profile/email-preferences",
        "/auth/sessions",
    ],
)
def test_read_query_failure_after_success(profile_route, monkeypatch, path):
    case = profile_route
    case.client.app.dependency_overrides[routes.get_current_api_key] = (
        lambda: SimpleNamespace(user_id=case.user.id, department_id=case.department.id)
    )
    path = path.format(department=case.department.id)
    assert case.client.get(path).status_code == 200
    monkeypatch.setattr(
        case.db,
        "query",
        MagicMock(side_effect=RuntimeError("synthetic query unavailable")),
    )
    response = case.client.get(path)
    assert response.status_code == 500
    assert "synthetic" not in response.text


def test_quota_failure_after_success(profile_route, monkeypatch):
    case = profile_route
    case.client.app.dependency_overrides[routes.get_current_api_key] = (
        lambda: SimpleNamespace(department_id=case.department.id)
    )
    assert case.client.get("/auth/quota").status_code == 200
    monkeypatch.setattr(
        routes,
        "get_quota_status",
        MagicMock(side_effect=RuntimeError("synthetic quota unavailable")),
    )
    response = case.client.get("/auth/quota")
    assert response.status_code == 500
    assert "synthetic" not in response.text


def test_refresh_service_failure_after_success_sets_no_new_cookies(profile_route):
    case = profile_route
    case.client.cookies.set("aelira_refresh", "synthetic-refresh")
    case.identity.refresh_session.return_value = ("access", "refresh", 123, 456)
    assert case.client.post("/auth/session/refresh").status_code == 200
    case.identity.refresh_session.side_effect = RuntimeError(
        "synthetic refresh unavailable"
    )
    response = case.client.post("/auth/session/refresh")
    assert response.status_code == 500
    assert "set-cookie" not in response.headers


def test_session_resolution_failure_after_success(profile_route, monkeypatch):
    case = profile_route
    resolver = MagicMock(
        return_value=SimpleNamespace(
            user=case.user,
            payload={"exp": 123},
            principal=SimpleNamespace(auth_method="session"),
        )
    )
    monkeypatch.setattr(routes, "resolve_access_token", resolver)
    assert case.client.get("/auth/session/validate").status_code == 200
    resolver.side_effect = RuntimeError("synthetic session unavailable")
    response = case.client.get("/auth/session/validate")
    assert response.status_code == 500
    assert "synthetic" not in response.text


@pytest.mark.parametrize("operation", ["request", "check", "verify"])
def test_magic_link_dependency_failure_after_success(
    profile_route, monkeypatch, operation
):
    case = profile_route
    email = case.user.email
    case.identity.create_magic_link.return_value = "synthetic-link"
    case.identity.check_magic_link.return_value = True
    case.identity.verify_magic_link.return_value = SimpleNamespace(
        signup_name=None, signup_institution=None
    )
    case.identity.get_or_create_user_for_magic_link.return_value = (case.user, False)
    case.identity.create_session.return_value = ("access", "refresh", 123, 456)
    monkeypatch.setattr(
        routes.RateLimiter, "check_rate_limit", lambda *args, **kwargs: (True, {})
    )
    monkeypatch.setattr(
        routes,
        "get_email_service",
        lambda: SimpleNamespace(is_configured=lambda: False),
    )
    if operation == "check":

        def request():
            return case.client.get(
                "/auth/magic-link/check",
                params={"email": email, "token": "synthetic-link"},
            )

        collaborator = case.identity.check_magic_link
    else:
        body = {"email": email}
        if operation == "verify":
            body["token"] = "synthetic-link"

        def request():
            return case.client.post(f"/auth/magic-link/{operation}", json=body)

        collaborator = (
            case.identity.create_magic_link
            if operation == "request"
            else case.identity.verify_magic_link
        )
    assert request().status_code == 200
    collaborator.side_effect = RuntimeError("synthetic link service unavailable")
    response = request()
    assert response.status_code == 500
    assert "synthetic" not in response.text
    assert "set-cookie" not in response.headers


def test_logout_dependency_failure_after_success(profile_route, monkeypatch):
    case = profile_route
    jwt = MagicMock()
    jwt.decode_token.return_value = {
        "type": "access",
        "sub": case.user.id,
        "sid": "fixture-session",
    }
    monkeypatch.setattr(routes, "get_jwt_service", lambda: jwt)
    assert case.client.post("/auth/session/logout").status_code == 200
    case.client.cookies.set("aelira_access", "synthetic-session")
    case.identity.revoke_session.side_effect = RuntimeError(
        "synthetic session unavailable"
    )
    response = case.client.post("/auth/session/logout")
    assert response.status_code == 500
    assert "synthetic" not in response.text


def test_email_preferences_write_failure_after_success(profile_route, monkeypatch):
    case = profile_route
    body = {"email_scan_complete": False}
    assert (
        case.client.patch("/auth/profile/email-preferences", json=body).status_code
        == 200
    )
    monkeypatch.setattr(
        case.db,
        "commit",
        MagicMock(side_effect=RuntimeError("synthetic commit unavailable")),
    )
    response = case.client.patch(
        "/auth/profile/email-preferences", json={"email_scan_complete": True}
    )
    assert response.status_code == 500
    assert "synthetic" not in response.text
