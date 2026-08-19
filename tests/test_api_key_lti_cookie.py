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


def _db_returning_key():
    """DB whose model-specific lookup chains return the LTI user and API key."""
    db = MagicMock()
    user = MagicMock(
        spec=User,
        id="lti-u",
        department_id="d1",
        is_active=True,
        auth_provider=AuthProvider.LTI,
        role=UserRole.FACULTY,
    )
    api_key = MagicMock(
        spec=APIKey,
        id="k1",
        user_id="lti-u",
        is_active=True,
        rate_limit_per_hour=1000,
    )
    user_chain = MagicMock()
    user_chain.filter.return_value = user_chain
    user_chain.first.return_value = user
    key_chain = MagicMock()
    key_chain.filter.return_value = key_chain
    key_chain.order_by.return_value = key_chain
    key_chain.first.return_value = api_key
    db.query.side_effect = lambda model: user_chain if model is User else key_chain
    return db


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

    api_key = get_current_api_key(
        request=_request_with_cookie(access),
        credentials=None,
        db=_db_returning_key(),
    )

    assert api_key is not None
    assert no_session.validate_session.called


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

    assert api_key.user_id == "lti-u"
