import pytest
from fastapi import HTTPException

from src.auth.canvas_permissions import (
    require_canvas_staff,
    require_lti_account_access,
    require_lti_course_access,
)
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import UserRole


def _principal(auth_method="session", **overrides):
    values = {
        "api_key": None,
        "user_id": "user-1",
        "department_id": "dept-1",
        "user_role": UserRole.FACULTY,
        "auth_method": auth_method,
    }
    values.update(overrides)
    return AuthenticatedPrincipal(**values)


def _assert_forbidden(call, *args):
    with pytest.raises(HTTPException) as exc:
        call(*args)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Forbidden"


@pytest.mark.parametrize(
    ("auth_method", "role"),
    [
        ("api_key", UserRole.FACULTY),
        ("session", UserRole.FACULTY),
        ("mock", UserRole.ADMIN),
    ],
)
def test_non_lti_principals_preserve_existing_canvas_access(auth_method, role):
    principal = _principal(auth_method, user_role=role)

    assert require_canvas_staff(principal) is principal
    assert require_lti_course_access(principal, "same-course") is principal
    assert require_lti_course_access(principal, "other-course") is principal
    assert require_lti_account_access(principal) is principal


@pytest.mark.parametrize(
    "staff_role", ["Instructor", "TeachingAssistant", "ContentDeveloper"]
)
def test_course_staff_are_limited_to_their_exact_nonempty_course(staff_role):
    principal = _principal(
        "lti",
        lti_course_id="course-1",
        lti_staff_role=staff_role,
        lti_account_wide=False,
    )

    assert require_canvas_staff(principal) is principal
    assert require_lti_course_access(principal, "course-1") is principal
    _assert_forbidden(require_lti_course_access, principal, "course-2")
    _assert_forbidden(require_lti_course_access, principal, "")
    _assert_forbidden(require_lti_account_access, principal)


def test_lti_administrator_has_account_and_any_named_course_access():
    principal = _principal(
        "lti",
        user_role=UserRole.ADMIN,
        lti_course_id=None,
        lti_staff_role="Administrator",
        lti_account_wide=True,
    )

    assert require_canvas_staff(principal) is principal
    assert require_lti_course_access(principal, "course-1") is principal
    assert require_lti_course_access(principal, "course-2") is principal
    _assert_forbidden(require_lti_course_access, principal, "")
    assert require_lti_account_access(principal) is principal


@pytest.mark.parametrize(
    "changes",
    [
        {"lti_staff_role": None},
        {"lti_staff_role": "Learner"},
        {"lti_staff_role": "Instructor", "lti_course_id": None},
        {"lti_staff_role": "Instructor", "lti_course_id": ""},
        {"lti_staff_role": "Instructor", "lti_account_wide": True},
        {
            "lti_staff_role": "Administrator",
            "lti_account_wide": False,
            "user_role": UserRole.ADMIN,
        },
        {
            "lti_staff_role": "Administrator",
            "lti_account_wide": True,
            "user_role": UserRole.FACULTY,
        },
    ],
)
def test_malformed_lti_principals_cannot_be_constructed(changes):
    values = {
        "lti_course_id": "course-1",
        "lti_staff_role": "Instructor",
        "lti_account_wide": False,
    }
    values.update(changes)

    with pytest.raises(ValueError):
        _principal("lti", **values)
