"""Bearer-token auth must honour token type and session revocation.

Regression for a fail-open in get_required_api_key's Bearer branch: it
decoded any signed JWT and authenticated on sub+department_id alone,
checking neither the token "type" nor whether the backing session was
revoked. Consequences fixed here:

  1. A refresh token (multi-day lifetime) worked as an access credential.
  2. A logged-out / revoked access token kept working until its own expiry.

LTI-launch tokens legitimately have no UserSession row; they are accepted
by the positive lti_launch=True claim, not by absence of a session.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.auth.dependencies import get_required_api_key
from src.auth.jwt_service import JWTService


def _bearer(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _request_without_cookie():
    req = MagicMock()
    req.cookies = {}
    return req


@pytest.fixture
def jwt_service():
    return JWTService()


@pytest.fixture
def db_no_api_key(monkeypatch):
    """DB whose API-key lookup always misses, forcing the JWT branch."""
    from src.auth import auth_service

    monkeypatch.setattr(
        auth_service.AuthService, "validate_api_key", staticmethod(lambda db, t: None)
    )
    return MagicMock()


def test_refresh_token_rejected_as_bearer(jwt_service, db_no_api_key):
    # A refresh token must never authenticate an API request.
    refresh, _raw, _exp = jwt_service.create_refresh_token(user_id="u1")
    with pytest.raises(HTTPException) as exc:
        get_required_api_key(
            request=_request_without_cookie(),
            credentials=_bearer(refresh),
            db=db_no_api_key,
        )
    assert exc.value.status_code == 401


def test_revoked_access_token_rejected_as_bearer(
    jwt_service, db_no_api_key, monkeypatch
):
    # A well-formed access token whose session is revoked/absent → 401,
    # even though the JWT itself is still validly signed and unexpired.
    access, _jti, _exp = jwt_service.create_access_token(
        user_id="u1", department_id="d1", email="u@x.edu", role="faculty"
    )

    import src.auth.session_service as ss

    fake_session = MagicMock()
    fake_session.validate_session.return_value = None  # revoked / not found
    monkeypatch.setattr(ss, "get_session_service", lambda: fake_session)

    with pytest.raises(HTTPException) as exc:
        get_required_api_key(
            request=_request_without_cookie(),
            credentials=_bearer(access),
            db=db_no_api_key,
        )
    assert exc.value.status_code == 401
    assert fake_session.validate_session.called


def test_live_access_token_accepted_as_bearer(jwt_service, db_no_api_key, monkeypatch):
    access, _jti, _exp = jwt_service.create_access_token(
        user_id="u1", department_id="d1", email="u@x.edu", role="faculty"
    )

    import src.auth.session_service as ss

    user = MagicMock(id="u1", department_id="d1")
    fake_session = MagicMock()
    fake_session.validate_session.return_value = (user, {})
    monkeypatch.setattr(ss, "get_session_service", lambda: fake_session)

    api_key, user_id, dept_id = get_required_api_key(
        request=_request_without_cookie(),
        credentials=_bearer(access),
        db=db_no_api_key,
    )
    assert api_key is None
    assert user_id == "u1"
    assert dept_id == "d1"


def test_lti_launch_token_accepted_without_session(
    jwt_service, db_no_api_key, monkeypatch
):
    # LTI-launch tokens have no UserSession row; the lti_launch=True claim
    # is what admits them, and validate_session must NOT be consulted.
    access, _jti, _exp = jwt_service.create_access_token(
        user_id="lti-u",
        department_id="d1",
        email="u@x.edu",
        role="faculty",
        additional_claims={"lti_launch": True, "course_id": "c1"},
    )

    import src.auth.session_service as ss

    fake_session = MagicMock()
    monkeypatch.setattr(ss, "get_session_service", lambda: fake_session)

    api_key, user_id, dept_id = get_required_api_key(
        request=_request_without_cookie(),
        credentials=_bearer(access),
        db=db_no_api_key,
    )
    assert user_id == "lti-u"
    fake_session.validate_session.assert_not_called()
