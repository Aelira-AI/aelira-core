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

from src.api.lti_launch_handler import handle_lti_launch
from src.db.models import User
from src.integrations.canvas_lti import CanvasLaunchData


def _make_launch_data(**overrides) -> CanvasLaunchData:
    defaults = dict(
        user_id="canvas-user-1",
        user_name="Test Student",
        user_email="student@example.edu",
        course_id="",
        course_name="",
        roles=["http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"],
        is_instructor=False,
        is_student=True,
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
    user.email = "student@example.edu"
    user.role = "student"
    db.query.return_value.filter.return_value.first.return_value = user
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
