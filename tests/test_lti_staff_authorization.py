"""Tests for the canonical, side-effect-free LTI staff authorization policy."""

import pytest

from src.auth.lti_authorization import LTIStaffAuthorization, authorize_lti_roles
from src.db.models import UserRole


@pytest.mark.parametrize(
    (
        "roles",
        "expected_role",
        "expected_staff_role",
        "expected_account_wide",
    ),
    [
        (
            ["http://purl.imsglobal.org/vocab/lis/v2/system/person#Administrator"],
            UserRole.ADMIN,
            "Administrator",
            True,
        ),
        (
            ["http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"],
            UserRole.FACULTY,
            "Instructor",
            False,
        ),
        (
            [
                "http://purl.imsglobal.org/vocab/lis/v2/membership/Instructor#TeachingAssistant"
            ],
            UserRole.FACULTY,
            "TeachingAssistant",
            False,
        ),
        (
            ["http://purl.imsglobal.org/vocab/lis/v2/membership#ContentDeveloper"],
            UserRole.FACULTY,
            "ContentDeveloper",
            False,
        ),
        (
            ["http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"],
            None,
            None,
            False,
        ),
        (
            ["http://purl.imsglobal.org/vocab/lis/v2/membership#Student"],
            None,
            None,
            False,
        ),
        (
            ["http://purl.imsglobal.org/vocab/lis/v2/membership#Mentor"],
            None,
            None,
            False,
        ),
        (
            ["http://purl.imsglobal.org/vocab/lis/v2/membership#Observer"],
            None,
            None,
            False,
        ),
        ([], None, None, False),
        (None, None, None, False),
        (["http://example.edu/roles#Unknown"], None, None, False),
        (["http://example.edu/roles#NotAnInstructor"], None, None, False),
        (["http://example.edu/roles#AdministratorAssistant"], None, None, False),
    ],
)
def test_authorize_lti_roles_applies_staff_only_policy(
    roles,
    expected_role,
    expected_staff_role,
    expected_account_wide,
):
    decision = authorize_lti_roles(roles)

    assert isinstance(decision, LTIStaffAuthorization)
    assert decision.allowed is (expected_role is not None)
    assert decision.aelira_role is expected_role
    assert decision.staff_role == expected_staff_role
    assert decision.account_wide is expected_account_wide


def test_authorize_lti_roles_allows_mixed_learner_and_instructor_as_staff():
    decision = authorize_lti_roles(
        [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner",
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
        ]
    )

    assert decision.allowed is True
    assert decision.aelira_role is UserRole.FACULTY
    assert decision.staff_role == "Instructor"
    assert decision.account_wide is False


def test_authorize_lti_roles_gives_administrator_precedence_over_course_role():
    decision = authorize_lti_roles(
        [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
            "http://purl.imsglobal.org/vocab/lis/v2/system/person#Administrator",
        ]
    )

    assert decision.allowed is True
    assert decision.aelira_role is UserRole.ADMIN
    assert decision.staff_role == "Administrator"
    assert decision.account_wide is True
