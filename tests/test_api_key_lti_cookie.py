"""The API-key dependency must admit LTI-launch tokens on the cookie path.

get_current_api_key is the second of the two dependencies that read the
aelira_access cookie. An LTI launch delivers its token as that cookie and
has no UserSession row, so the session lookup misses and the request was
rejected. Admission is by the positive lti_launch claim, never by the
absence of a session, which the second test pins down.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.auth_routes import get_current_api_key
from src.auth.auth_service import AuthService
from src.auth.jwt_service import JWTService
from src.db.models import APIKey, AuthProvider, User, UserRole


@pytest.fixture
def jwt_service():
    return JWTService()


def _request_with_cookie(token):
    req = MagicMock()
    req.cookies = {"aelira_access": token}
    return req


@pytest.fixture
def no_session(monkeypatch):
    """Session lookup always misses, as it does for every LTI launch."""
    import src.api.auth_routes as auth_routes

    fake_session = MagicMock()
    fake_session.validate_session.return_value = None
    monkeypatch.setattr(auth_routes, "get_session_service", lambda: fake_session)
    return fake_session


def _db_returning_key(
    *,
    user_department_id: str | None = "d1",
    key_department_id: str | None = "d1",
    unscoped_first_key_department_id: str | None = None,
):
    """DB whose model-specific lookup chains return the LTI user and API key."""
    db = MagicMock()
    user = MagicMock(
        spec=User,
        id="lti-u",
        department_id=user_department_id,
        is_active=True,
        auth_provider=AuthProvider.LTI,
        role=UserRole.FACULTY,
    )
    api_key = MagicMock(
        spec=APIKey,
        id="k1",
        user_id="lti-u",
        department_id=key_department_id,
        is_active=True,
        rate_limit_per_hour=1000,
    )
    user_chain = MagicMock()
    user_chain.filter.return_value = user_chain
    user_chain.first.return_value = user
    key_chain = MagicMock()
    key_chain.order_by.return_value = key_chain
    key_chain.first.return_value = api_key

    if unscoped_first_key_department_id is not None:
        old_key = MagicMock(
            spec=APIKey,
            id="old-k",
            user_id="lti-u",
            department_id=unscoped_first_key_department_id,
            is_active=True,
            rate_limit_per_hour=1000,
        )

        def filter_keys(*conditions):
            filters_current_department = any(
                getattr(getattr(condition, "left", None), "name", None)
                == "department_id"
                and getattr(getattr(condition, "right", None), "value", None)
                == user_department_id
                for condition in conditions
            )
            key_chain.first.return_value = (
                api_key if filters_current_department else old_key
            )
            return key_chain

        key_chain.filter.side_effect = filter_keys
    else:
        key_chain.filter.return_value = key_chain

    db.query.side_effect = lambda model: user_chain if model is User else key_chain
    db.key_query = key_chain
    return db


def _v2_lti_staff_token(jwt_service, *, department_id="d1"):
    access, _jti, _exp = jwt_service.create_access_token(
        user_id="lti-u",
        department_id=department_id,
        email="u@x.edu",
        role="faculty",
        additional_claims={
            "lti_launch": True,
            "course_id": "c1",
            "lti_staff": True,
            "lti_staff_role": "Instructor",
            "lti_roles": ["Instructor"],
            "lti_account_wide": False,
            "lti_authz_version": 2,
        },
    )
    return access


def _api_key_filter_values(db):
    values = set()
    for condition in db.key_query.filter.call_args.args:
        right = getattr(condition, "right", None)
        value = getattr(right, "value", None)
        if str(right).lower() == "true":
            value = True
        values.add((getattr(getattr(condition, "left", None), "name", None), value))
    return values


def test_legacy_lti_launch_cookie_is_rejected(jwt_service, no_session):
    access, _jti, _exp = jwt_service.create_access_token(
        user_id="lti-u",
        department_id="d1",
        email="u@x.edu",
        role="faculty",
        additional_claims={"lti_launch": True, "course_id": "c1"},
    )

    with pytest.raises(HTTPException) as exc:
        get_current_api_key(
            request=_request_with_cookie(access),
            credentials=None,
            db=_db_returning_key(),
        )

    assert exc.value.status_code == 401


def test_v2_lti_staff_cookie_resolves_an_api_key(jwt_service, no_session):
    access, _jti, _exp = jwt_service.create_access_token(
        user_id="lti-u",
        department_id="d1",
        email="u@x.edu",
        role="faculty",
        additional_claims={
            "lti_launch": True,
            "course_id": "c1",
            "lti_staff": True,
            "lti_staff_role": "Instructor",
            "lti_roles": ["Instructor"],
            "lti_account_wide": False,
            "lti_authz_version": 2,
        },
    )

    db = _db_returning_key()
    api_key = get_current_api_key(
        request=_request_with_cookie(access),
        credentials=None,
        db=db,
    )

    assert api_key is not None
    assert getattr(api_key, "department_id") == "d1"
    assert {("user_id", "lti-u"), ("department_id", "d1"), ("is_active", True)} <= (
        _api_key_filter_values(db)
    )
    assert no_session.validate_session.called


def test_v2_lti_staff_cookie_rejects_old_department_api_key(
    jwt_service, no_session, monkeypatch
):
    access = _v2_lti_staff_token(jwt_service)
    create = MagicMock()
    monkeypatch.setattr(AuthService, "create_api_key", create)

    with pytest.raises(HTTPException) as exc:
        get_current_api_key(
            request=_request_with_cookie(access),
            credentials=None,
            db=_db_returning_key(key_department_id="old-dept"),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail.startswith("No active API key")
    create.assert_not_called()


def test_v2_lti_staff_cookie_mixed_keys_selects_current_department(
    jwt_service, no_session
):
    db = _db_returning_key(
        key_department_id="d1",
        unscoped_first_key_department_id="old-dept",
    )

    api_key = get_current_api_key(
        request=_request_with_cookie(_v2_lti_staff_token(jwt_service)),
        credentials=None,
        db=db,
    )

    assert getattr(api_key, "id") == "k1"
    assert getattr(api_key, "department_id") == "d1"
    assert ("department_id", "d1") in _api_key_filter_values(db)


def test_v2_lti_staff_cookie_rejects_key_without_department(
    jwt_service, no_session, monkeypatch
):
    db = _db_returning_key(key_department_id=None)
    create = MagicMock()
    monkeypatch.setattr(AuthService, "create_api_key", create)

    with pytest.raises(HTTPException) as exc:
        get_current_api_key(
            request=_request_with_cookie(_v2_lti_staff_token(jwt_service)),
            credentials=None,
            db=db,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail.startswith("No active API key")
    create.assert_not_called()


def test_representative_legacy_route_never_receives_old_department_key(
    jwt_service, no_session
):
    representative_route = MagicMock()

    with pytest.raises(HTTPException):
        api_key = get_current_api_key(
            request=_request_with_cookie(_v2_lti_staff_token(jwt_service)),
            credentials=None,
            db=_db_returning_key(key_department_id="old-dept"),
        )
        representative_route(api_key)

    representative_route.assert_not_called()


def test_cookie_without_lti_claim_is_still_rejected(jwt_service, no_session):
    access, _jti, _exp = jwt_service.create_access_token(
        user_id="u1", department_id="d1", email="u@x.edu", role="faculty"
    )

    with pytest.raises(HTTPException) as exc:
        get_current_api_key(
            request=_request_with_cookie(access),
            credentials=None,
            db=MagicMock(),
        )

    assert exc.value.status_code == 401


def test_normal_session_cookie_behavior_is_unchanged(monkeypatch):
    import src.api.auth_routes as auth_routes

    db = _db_returning_key()
    user = db.query(User).filter().first()
    session_service = MagicMock()
    session_service.validate_session.return_value = (user, {"type": "access"})
    monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)

    api_key = get_current_api_key(
        request=_request_with_cookie("normal-session-token"),
        credentials=None,
        db=db,
    )

    assert getattr(api_key, "user_id") == "lti-u"
