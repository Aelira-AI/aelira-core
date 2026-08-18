"""
Tests for the LTI launch handler's redirect-URL routing.

A non-account_navigation launch hops through /lti/go, which exchanges the
code (setting the aelira_access cookie) and then hard-navigates into the
main dashboard's course content page — the requested destination for
"open in aelira" launches, since that route is behind ProtectedRoute and
doesn't itself know how to exchange a launch code. When course_id can't be
resolved at all, /lti/go?code=... is built without a &course= param (its
own fallback lands on /lti/overview) rather than the old bare
"/lti/course/?code=..." — that URL's empty :courseId segment never matched
the client's React Router route and stranded the user on the dashboard
home. account_navigation launches are unaffected and still go straight to
/lti/overview.
"""

from unittest.mock import MagicMock

import pytest

from src.api import lti_launch_handler
from src.api.lti_launch_handler import LTIStaffAccessDenied, handle_lti_launch
from src.db.models import User, UserRole
from src.integrations.canvas_lti import CanvasLaunchData


def _make_launch_data(**overrides) -> CanvasLaunchData:
    defaults = dict(
        user_id="canvas-user-1",
        user_name="Test Instructor",
        user_email="instructor@example.edu",
        course_id="",
        course_name="",
        roles=["http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"],
        is_instructor=True,
        is_student=False,
        resource_link_id="link-1",
        deployment_id="deploy-1",
        platform_id="https://canvas.instructure.com",
        client_id="client-1",
        nonce="nonce-1",
        placement="course_navigation",
        custom_params={},
    )
    defaults.update(overrides)
    return CanvasLaunchData(**defaults)


def _make_db_with_existing_user() -> MagicMock:
    """A mock db session whose User lookup returns an existing, active user
    — this keeps the test on the "returning user" branch and out of the
    User-creation code path, which isn't what this test is about."""
    db = MagicMock()
    user = MagicMock(spec=User)
    user.id = "user-1"
    user.email = "instructor@example.edu"
    user.role = "student"
    db.query.return_value.filter.return_value.first.return_value = user
    return db


def _make_db_without_existing_user() -> MagicMock:
    """A mock session whose same- and cross-department lookups both miss."""

    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [None, None]
    return db


def _make_registration() -> MagicMock:
    registration = MagicMock()
    registration.department_id = "dept-1"
    return registration


class TestLtiGoRouting:
    def test_empty_course_id_redirects_to_lti_go_without_course_param(self):
        launch_data = _make_launch_data(course_id="", custom_params={})
        db = _make_db_with_existing_user()
        registration = _make_registration()

        redirect_url = handle_lti_launch(
            launch_data, registration, db, platform="canvas"
        )

        assert "/lti/go?code=" in redirect_url
        assert "course=" not in redirect_url
        assert "/lti/course/" not in redirect_url

    def test_course_id_from_custom_params_fallback_is_used(self):
        # extract_launch_data() didn't resolve course_id (e.g. context claim
        # type wasn't recognized as a course), but a raw custom param still
        # carries it — the handler should recover it before falling back to
        # a course-less /lti/go.
        launch_data = _make_launch_data(
            course_id="", custom_params={"custom_course_id": "778"}
        )
        db = _make_db_with_existing_user()
        registration = _make_registration()

        redirect_url = handle_lti_launch(
            launch_data, registration, db, platform="canvas"
        )

        assert "/lti/go?code=" in redirect_url
        assert "&course=778" in redirect_url

    def test_unsubstituted_canvas_variable_is_ignored(self):
        # An unsubstituted Canvas variable ("$Canvas.course.id") must not be
        # treated as a real course id.
        launch_data = _make_launch_data(
            course_id="", custom_params={"canvas_course_id": "$Canvas.course.id"}
        )
        db = _make_db_with_existing_user()
        registration = _make_registration()

        redirect_url = handle_lti_launch(
            launch_data, registration, db, platform="canvas"
        )

        assert "/lti/go?code=" in redirect_url
        assert "course=" not in redirect_url
        assert "/lti/course/" not in redirect_url

    def test_present_course_id_routes_via_lti_go_with_course_param(self):
        # Regression guard: a normal launch with a resolved course id must
        # keep hopping through /lti/go, carrying the course id along.
        launch_data = _make_launch_data(course_id="123")
        db = _make_db_with_existing_user()
        registration = _make_registration()

        redirect_url = handle_lti_launch(
            launch_data, registration, db, platform="canvas"
        )

        assert "/lti/go?code=" in redirect_url
        assert "&course=123" in redirect_url
        assert "/lti/course/" not in redirect_url

    def test_account_navigation_still_routes_to_overview(self):
        # Regression guard: account_navigation placements already went to
        # /lti/overview regardless of course context — must still, and must
        # NOT be routed through /lti/go.
        launch_data = _make_launch_data(course_id="123", placement="account_navigation")
        db = _make_db_with_existing_user()
        registration = _make_registration()

        redirect_url = handle_lti_launch(
            launch_data, registration, db, platform="canvas"
        )

        assert "/lti/overview?code=" in redirect_url
        assert "/lti/go" not in redirect_url


def test_new_lti_user_role_comes_from_canonical_staff_policy(monkeypatch):
    launch_data = _make_launch_data(
        roles=["http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"],
        user_email="instructor@example.edu",
    )
    db = _make_db_without_existing_user()

    monkeypatch.setattr(lti_launch_handler, "_store_code", lambda *args, **kwargs: None)

    handle_lti_launch(launch_data, _make_registration(), db, platform="canvas")

    created_user = next(
        call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], User)
    )
    assert created_user.role is UserRole.FACULTY


@pytest.mark.parametrize(
    "roles",
    [
        ["http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"],
        ["http://purl.imsglobal.org/vocab/lis/v2/membership#Student"],
        ["http://purl.imsglobal.org/vocab/lis/v2/membership#Mentor"],
        ["http://purl.imsglobal.org/vocab/lis/v2/membership#Observer"],
        [],
        None,
        ["http://example.edu/lti/role#Unknown"],
    ],
)
def test_denied_launch_has_no_side_effects_before_rejection(monkeypatch, roles):
    launch_data = _make_launch_data(roles=roles or [], user_email=None)
    if roles is None:
        object.__setattr__(launch_data, "roles", None)
    db = MagicMock()

    monkeypatch.setattr(
        lti_launch_handler,
        "urlparse",
        MagicMock(side_effect=AssertionError("email resolution must not run")),
    )
    jwt_service = MagicMock(side_effect=AssertionError("JWT creation must not run"))
    monkeypatch.setattr(lti_launch_handler, "JWTService", jwt_service)
    store_code = MagicMock(side_effect=AssertionError("code storage must not run"))
    monkeypatch.setattr(lti_launch_handler, "_store_code", store_code)

    with pytest.raises(LTIStaffAccessDenied):
        handle_lti_launch(launch_data, _make_registration(), db, platform="canvas")

    assert db.mock_calls == []
    jwt_service.assert_not_called()
    store_code.assert_not_called()


def test_returning_users_role_is_recomputed_from_each_staff_launch(monkeypatch):
    launch_data = _make_launch_data(
        roles=["http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"]
    )
    db = _make_db_with_existing_user()
    user = db.query.return_value.filter.return_value.first.return_value
    user.role = UserRole.ADMIN
    monkeypatch.setattr(lti_launch_handler, "_store_code", lambda *args, **kwargs: None)

    handle_lti_launch(launch_data, _make_registration(), db, platform="canvas")

    assert user.role is UserRole.FACULTY


@pytest.mark.parametrize(
    ("role_uri", "staff_role", "account_wide", "aelira_role"),
    [
        (
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
            "Instructor",
            False,
            UserRole.FACULTY,
        ),
        (
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Administrator",
            "Administrator",
            True,
            UserRole.ADMIN,
        ),
    ],
)
def test_staff_launch_mints_v2_authorization_claims(
    monkeypatch, role_uri, staff_role, account_wide, aelira_role
):
    launch_data = _make_launch_data(roles=[role_uri], course_id="course-42")
    db = _make_db_with_existing_user()
    user = db.query.return_value.filter.return_value.first.return_value
    token_service = MagicMock()
    token_service.create_access_token.return_value = ("token", "jti", "exp")
    monkeypatch.setattr(lti_launch_handler, "JWTService", lambda: token_service)
    monkeypatch.setattr(lti_launch_handler, "_store_code", lambda *args, **kwargs: None)

    handle_lti_launch(launch_data, _make_registration(), db, platform="canvas")

    assert user.role is aelira_role
    claims = token_service.create_access_token.call_args.kwargs["additional_claims"]
    assert claims["lti_staff"] is True
    assert claims["lti_staff_role"] == staff_role
    assert claims["lti_account_wide"] is account_wide
    assert claims["lti_authz_version"] == 2
    assert claims["course_id"] == "course-42"
