from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.auth.dependencies import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
    get_key_management_principal,
    get_required_api_key,
)
from src.db.models import APIKey, User, UserRole


def _request(*, cookie: str | None = None):
    request = MagicMock()
    request.cookies = {"aelira_access": cookie} if cookie else {}
    return request


def _bearer(token: str):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _user(**overrides):
    values = {
        "id": "user-1",
        "department_id": "dept-1",
        "role": UserRole.FACULTY,
        "is_active": True,
    }
    values.update(overrides)
    return MagicMock(spec=User, **values)


def test_authenticated_principal_is_immutable_and_preserves_legacy_tuple():
    principal = AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method="session",
        lti_course_id=None,
        lti_staff_role=None,
        lti_account_wide=False,
    )

    assert principal.as_legacy_tuple() == (None, "user-1", "dept-1")
    with pytest.raises(FrozenInstanceError):
        principal.user_id = "other-user"


def test_api_key_principal_uses_active_database_owner_role_and_tenant(monkeypatch):
    from src.auth.auth_service import AuthService, RateLimiter

    api_key = MagicMock(
        spec=APIKey,
        user_id="user-1",
        department_id="dept-1",
    )
    owner = _user(role=UserRole.ADMIN)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = owner
    validate = MagicMock(return_value=api_key)
    limiter = MagicMock(return_value=(True, {}))
    monkeypatch.setattr(AuthService, "validate_api_key", validate)
    monkeypatch.setattr(RateLimiter, "check_rate_limit", limiter)

    principal = get_authenticated_principal(
        _request(), credentials=_bearer("api-token"), db=db
    )

    assert principal == AuthenticatedPrincipal(
        api_key=api_key,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.ADMIN,
        auth_method="api_key",
    )
    validate.assert_called_once_with(db, "api-token")
    limiter.assert_called_once_with(api_key.id, api_key.rate_limit_per_hour)


def test_api_key_principal_rejects_rate_limited_bearer(monkeypatch):
    from src.auth.auth_service import AuthService, RateLimiter

    api_key = MagicMock(
        spec=APIKey,
        id="key-1",
        user_id="user-1",
        department_id="dept-1",
        rate_limit_per_hour=20,
    )
    owner = _user(role=UserRole.ADMIN)
    api_key.user = owner
    db = MagicMock()
    monkeypatch.setattr(
        AuthService, "validate_api_key", MagicMock(return_value=api_key)
    )
    monkeypatch.setattr(
        RateLimiter,
        "check_rate_limit",
        MagicMock(return_value=(False, {"Retry-After": "30"})),
    )

    with pytest.raises(HTTPException) as exc:
        get_authenticated_principal(_request(), credentials=_bearer("api-token"), db=db)

    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "30"}


def test_api_key_principal_rejects_inactive_owner(monkeypatch):
    from src.auth.auth_service import AuthService

    api_key = MagicMock(
        spec=APIKey,
        user_id="user-1",
        department_id="dept-1",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _user(
        is_active=False
    )
    monkeypatch.setattr(
        AuthService, "validate_api_key", MagicMock(return_value=api_key)
    )

    with pytest.raises(HTTPException) as exc:
        get_authenticated_principal(_request(), credentials=_bearer("api-token"), db=db)

    assert exc.value.status_code == 401


def test_session_principal_uses_validated_user_and_validates_once(monkeypatch):
    from src.auth import jwt_service, session_service

    user = _user(role=UserRole.SUPER_ADMIN)
    service = MagicMock()
    service.validate_session.return_value = (user, {"untrusted": "ignored"})
    monkeypatch.setattr(session_service, "get_session_service", lambda: service)
    monkeypatch.setattr(
        jwt_service.JWTService,
        "verify_access_token",
        lambda self, token: {
            "sub": "user-1",
            "jti": "live-jti",
            "type": "access",
        },
    )

    principal = get_authenticated_principal(
        _request(cookie="session-token"), credentials=None, db=MagicMock()
    )

    assert principal == AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.SUPER_ADMIN,
        auth_method="session",
    )
    service.validate_session.assert_called_once_with(ANY, "session-token")


def test_lti_principal_retains_only_canonical_v2_authorization_claims(monkeypatch):
    from src.auth import auth_service, dependencies, jwt_service, session_service

    payload = {
        "sub": "user-1",
        "department_id": "dept-1",
        "role": "faculty",
        "lti_launch": True,
        "lti_staff": True,
        "lti_staff_role": "TeachingAssistant",
        "lti_roles": ["TeachingAssistant"],
        "lti_account_wide": False,
        "lti_platform": "canvas",
        "lti_authz_version": 2,
        "course_id": "course-1",
        "untrusted_extra": "ignored",
    }
    user = _user()
    canonical_validation = MagicMock(return_value=user)
    monkeypatch.setattr(
        auth_service.AuthService, "validate_api_key", staticmethod(lambda *_: None)
    )
    monkeypatch.setattr(
        jwt_service.JWTService, "verify_access_token", lambda self, token: payload
    )
    monkeypatch.setattr(session_service, "get_session_service", lambda: MagicMock())
    monkeypatch.setattr(
        dependencies, "validate_lti_staff_token_payload", canonical_validation
    )
    db = MagicMock()

    principal = get_authenticated_principal(
        _request(), credentials=_bearer("lti-token"), db=db
    )

    assert principal == AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method="lti",
        lti_course_id="course-1",
        lti_staff_role="TeachingAssistant",
        lti_account_wide=False,
    )
    canonical_validation.assert_called_once_with(payload, db)


def test_mock_principal_is_admin(monkeypatch):
    from src.config import settings as settings_module
    from src.db import database as database_module

    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(env="development", allow_mock_auth=True),
    )

    @contextmanager
    def mock_db():
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = object()
        yield db

    monkeypatch.setattr(database_module, "get_db", mock_db)

    principal = get_authenticated_principal(
        _request(), credentials=None, db=MagicMock()
    )

    assert principal == AuthenticatedPrincipal(
        api_key=None,
        user_id="dev-user-local",
        department_id="dev-dept-local",
        user_role=UserRole.ADMIN,
        auth_method="mock",
    )


def test_legacy_dependency_is_a_thin_principal_adapter(monkeypatch):
    expected = AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method="session",
    )
    authenticate = MagicMock(return_value=expected)
    monkeypatch.setattr(
        "src.auth.dependencies.get_authenticated_principal", authenticate
    )
    request = _request()
    credentials = _bearer("token")
    db = MagicMock()

    assert get_required_api_key(request, credentials, db) == expected.as_legacy_tuple()
    authenticate.assert_called_once_with(request, credentials, db)


def test_key_management_prefers_valid_normal_session_over_stale_bearer(monkeypatch):
    from src.auth.auth_service import AuthService
    from src.auth import dependencies

    session_principal = AuthenticatedPrincipal(
        api_key=None,
        user_id="session-user",
        department_id="session-dept",
        user_role=UserRole.FACULTY,
        auth_method="session",
    )
    resolve = MagicMock(return_value=SimpleNamespace(principal=session_principal))
    validate_key = MagicMock(return_value=None)
    monkeypatch.setattr(dependencies, "resolve_access_token", resolve)
    monkeypatch.setattr(AuthService, "validate_api_key", validate_key)

    principal = get_key_management_principal(
        _request(cookie="valid-session"), _bearer("retired-key"), MagicMock()
    )

    assert principal is session_principal
    resolve.assert_called_once()
    validate_key.assert_not_called()


def test_key_management_falls_back_from_invalid_cookie_to_valid_api_key(monkeypatch):
    from src.auth.auth_service import AuthService, RateLimiter
    from src.auth import dependencies

    owner = _user(role=UserRole.ADMIN)
    key = MagicMock(spec=APIKey, user_id=owner.id, department_id=owner.department_id)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = owner
    monkeypatch.setattr(
        dependencies, "resolve_access_token", MagicMock(return_value=None)
    )
    monkeypatch.setattr(AuthService, "validate_api_key", MagicMock(return_value=key))
    limiter = MagicMock(return_value=(True, {}))
    monkeypatch.setattr(RateLimiter, "check_rate_limit", limiter)

    principal = get_key_management_principal(
        _request(cookie="expired-session"), _bearer("valid-api-key"), db
    )

    assert principal.auth_method == "api_key"
    assert principal.api_key is key
    assert principal.user_id == owner.id
    limiter.assert_called_once_with(key.id, key.rate_limit_per_hour)


def test_key_management_rejects_rate_limited_api_key_bearer(monkeypatch):
    from src.auth.auth_service import AuthService, RateLimiter

    owner = _user(role=UserRole.ADMIN)
    key = MagicMock(
        spec=APIKey,
        id="key-1",
        user_id=owner.id,
        department_id=owner.department_id,
        rate_limit_per_hour=20,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = owner
    monkeypatch.setattr(AuthService, "validate_api_key", MagicMock(return_value=key))
    monkeypatch.setattr(
        RateLimiter,
        "check_rate_limit",
        MagicMock(return_value=(False, {"Retry-After": "30"})),
    )

    with pytest.raises(HTTPException) as exc:
        get_key_management_principal(_request(), _bearer("valid-api-key"), db)

    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "30"}


def test_key_management_session_is_not_subject_to_api_key_bearer_limit(monkeypatch):
    from src.auth.auth_service import RateLimiter
    from src.auth import dependencies

    session_principal = AuthenticatedPrincipal(
        api_key=None,
        user_id="session-user",
        department_id="session-dept",
        user_role=UserRole.FACULTY,
        auth_method="session",
    )
    monkeypatch.setattr(
        dependencies,
        "resolve_access_token",
        MagicMock(return_value=SimpleNamespace(principal=session_principal)),
    )
    limiter = MagicMock(return_value=(False, {}))
    monkeypatch.setattr(RateLimiter, "check_rate_limit", limiter)

    assert (
        get_key_management_principal(
            _request(cookie="valid-session"), _bearer("stale-key"), MagicMock()
        )
        is session_principal
    )
    limiter.assert_not_called()


def test_key_management_rejects_lti_without_normal_api_key(monkeypatch):
    from src.auth.auth_service import AuthService
    from src.auth import dependencies

    lti_principal = AuthenticatedPrincipal(
        api_key=None,
        user_id="lti-user",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method="lti",
        lti_course_id="course-1",
        lti_staff_role="Instructor",
    )
    monkeypatch.setattr(
        dependencies,
        "resolve_access_token",
        MagicMock(return_value=SimpleNamespace(principal=lti_principal)),
    )
    monkeypatch.setattr(AuthService, "validate_api_key", MagicMock(return_value=None))

    with pytest.raises(HTTPException) as exc:
        get_key_management_principal(_request(cookie="lti-token"), None, MagicMock())

    assert exc.value.status_code == 403
