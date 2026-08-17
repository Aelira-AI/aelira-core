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
    """DB whose single lookup chain serves both the user and the API key."""
    db = MagicMock()
    row = MagicMock(id="k1", user_id="lti-u", is_active=True, rate_limit_per_hour=1000)
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.first.return_value = row
    db.query.return_value = chain
    return db


def test_lti_launch_cookie_resolves_an_api_key(jwt_service, no_session):
    access, _jti, _exp = jwt_service.create_access_token(
        user_id="lti-u",
        department_id="d1",
        email="u@x.edu",
        role="faculty",
        additional_claims={"lti_launch": True, "course_id": "c1"},
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
