"""Fail-closed validation for signed LTI staff access-token payloads."""

from unittest.mock import MagicMock

import pytest

from src.auth.lti_authorization import validate_lti_staff_token_payload
from src.db.models import AuthProvider, User, UserRole


def _user(**overrides):
    values = {
        "id": "lti-u",
        "department_id": "db-dept",
        "auth_provider": AuthProvider.LTI,
        "role": UserRole.FACULTY,
        "is_active": True,
    }
    values.update(overrides)
    return MagicMock(spec=User, **values)


def _payload(**overrides):
    values = {
        "sub": "lti-u",
        "department_id": "db-dept",
        "role": "faculty",
        "lti_launch": True,
        "lti_staff": True,
        "lti_staff_role": "Instructor",
        "lti_roles": ["Instructor"],
        "lti_account_wide": False,
        "lti_authz_version": 2,
    }
    values.update(overrides)
    return values


def _db_returning(user):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    return db


def test_valid_v2_staff_payload_returns_database_identity():
    user = _user()

    validated = validate_lti_staff_token_payload(_payload(), _db_returning(user))

    assert validated is user
    assert validated.id == "lti-u"
    assert validated.department_id == "db-dept"


@pytest.mark.parametrize(
    "payload_changes",
    [
        {"lti_staff": None},
        {"lti_authz_version": None},
        {"lti_authz_version": 1},
        {"lti_staff_role": "Learner"},
        {"lti_staff_role": "Unknown"},
        {"lti_staff_role": None},
        {"lti_roles": ["Learner"]},
        {"lti_roles": "Instructor"},
        {"department_id": "claimed-dept"},
        {"role": "admin"},
        {"lti_account_wide": True},
        {"user_id": "other-user"},
    ],
)
def test_invalid_or_inconsistent_lti_claims_are_rejected(payload_changes):
    assert (
        validate_lti_staff_token_payload(
            _payload(**payload_changes), _db_returning(_user())
        )
        is None
    )


@pytest.mark.parametrize(
    "user_changes",
    [
        {"is_active": False},
        {"auth_provider": AuthProvider.MAGIC_LINK},
        {"department_id": "other-dept"},
        {"role": UserRole.ADMIN},
    ],
)
def test_invalid_database_user_state_is_rejected(user_changes):
    assert (
        validate_lti_staff_token_payload(
            _payload(), _db_returning(_user(**user_changes))
        )
        is None
    )


def test_nonexistent_database_user_is_rejected():
    assert validate_lti_staff_token_payload(_payload(), _db_returning(None)) is None
