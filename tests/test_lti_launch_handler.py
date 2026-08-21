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

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.api import lti_launch_handler
from src.api.lti_launch_handler import LTIStaffAccessDenied, handle_lti_launch
from src.db.models import AuthProvider, User, UserRole
from src.integrations.canvas_lti import CanvasLaunchData


def test_local_launch_code_fallback_enforces_ttl(monkeypatch):
    lti_launch_handler._code_store.clear()
    monkeypatch.setattr(lti_launch_handler, "get_redis_client", lambda: None)
    monkeypatch.setattr(
        lti_launch_handler, "get_settings", lambda: SimpleNamespace(env="test")
    )
    now = iter([100.0, 106.0])
    monkeypatch.setattr(lti_launch_handler.time, "monotonic", lambda: next(now))

    lti_launch_handler._store_code("code", "payload", ttl=5)

    assert lti_launch_handler._pop_code("code") is None


def test_production_launch_code_storage_fails_closed_without_redis(monkeypatch):
    lti_launch_handler._code_store.clear()
    monkeypatch.setattr(lti_launch_handler, "get_redis_client", lambda: None)
    monkeypatch.setattr(
        lti_launch_handler, "get_settings", lambda: SimpleNamespace(env="production")
    )

    with pytest.raises(
        RuntimeError, match="Durable LTI launch-code storage unavailable"
    ):
        lti_launch_handler._store_code("code", "payload", ttl=120)

    assert lti_launch_handler._code_store == {}


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
    user.auth_provider = AuthProvider.LTI
    user.lti_source = "https://canvas.instructure.com:canvas-user-1"
    user.is_active = True
    user.lti_reauthorization_required = False
    user.deactivated_at = None
    user.deletion_requested_at = None
    user.deletion_scheduled_for = None
    user.deletion_confirmation_code_hash = None
    user.deletion_confirmation_expires_at = None
    db.query.return_value.filter.return_value.first.return_value = user
    return db


def _make_db_without_existing_user() -> MagicMock:
    """A mock session whose same- and cross-department lookups both miss."""

    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [None, None, None]
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


def test_migration_marked_staff_relaunch_reactivates_existing_lti_user(monkeypatch):
    launch_data = _make_launch_data()
    db = _make_db_with_existing_user()
    user = db.query.return_value.filter.return_value.first.return_value
    user.is_active = False
    user.lti_reauthorization_required = True
    monkeypatch.setattr(lti_launch_handler, "_store_code", lambda *args, **kwargs: None)

    handle_lti_launch(launch_data, _make_registration(), db, platform="canvas")

    assert user.is_active is True
    assert user.lti_reauthorization_required is False


def test_deliberately_inactive_lti_user_is_not_reactivated(monkeypatch):
    db = _make_db_with_existing_user()
    user = db.query.return_value.filter.return_value.first.return_value
    user.is_active = False
    user.lti_reauthorization_required = False
    monkeypatch.setattr(lti_launch_handler, "_store_code", lambda *args, **kwargs: None)

    with pytest.raises(LTIStaffAccessDenied):
        handle_lti_launch(_make_launch_data(), _make_registration(), db)

    assert user.is_active is False


def test_deletion_pending_lti_user_is_not_reactivated(monkeypatch):
    db = _make_db_with_existing_user()
    user = db.query.return_value.filter.return_value.first.return_value
    user.is_active = False
    user.lti_reauthorization_required = True
    user.deletion_requested_at = datetime.now(timezone.utc)
    monkeypatch.setattr(lti_launch_handler, "_store_code", lambda *args, **kwargs: None)

    with pytest.raises(LTIStaffAccessDenied):
        handle_lti_launch(_make_launch_data(), _make_registration(), db)

    assert user.is_active is False
    assert user.lti_reauthorization_required is True


def test_same_email_non_lti_user_is_not_repurposed(monkeypatch):
    db = MagicMock()
    non_lti_user = MagicMock(spec=User)
    non_lti_user.email = "instructor@example.edu"
    non_lti_user.auth_provider = AuthProvider.GOOGLE
    non_lti_user.is_active = True
    query = MagicMock()
    query.filter.return_value = query
    query.first.side_effect = [None, None, non_lti_user]
    db.query.return_value = query
    monkeypatch.setattr(lti_launch_handler, "_store_code", lambda *args, **kwargs: None)

    handle_lti_launch(_make_launch_data(), _make_registration(), db)

    created_user = next(
        call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], User)
    )
    assert created_user is not non_lti_user
    assert created_user.auth_provider is AuthProvider.LTI
    assert created_user.email != non_lti_user.email
    assert non_lti_user.auth_provider is AuthProvider.GOOGLE


def test_lti_source_lookup_does_not_repurpose_non_lti_user(monkeypatch):
    db = MagicMock()
    non_lti_user = MagicMock(spec=User)
    non_lti_user.email = "instructor@example.edu"
    non_lti_user.auth_provider = AuthProvider.GOOGLE
    non_lti_user.is_active = True
    query = MagicMock()
    query.filter.return_value = query
    query.first.side_effect = [non_lti_user, None, non_lti_user]
    db.query.return_value = query
    monkeypatch.setattr(lti_launch_handler, "_store_code", lambda *args, **kwargs: None)

    handle_lti_launch(_make_launch_data(), _make_registration(), db)

    created_user = next(
        call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], User)
    )
    assert created_user.auth_provider is AuthProvider.LTI
    assert non_lti_user.auth_provider is AuthProvider.GOOGLE


def test_legacy_email_fallback_does_not_rebind_another_lti_source(monkeypatch):
    db = MagicMock()
    different_lti_user = MagicMock(spec=User)
    different_lti_user.email = "instructor@example.edu"
    different_lti_user.auth_provider = AuthProvider.LTI
    different_lti_user.lti_source = "https://other.example.edu:other-user"
    different_lti_user.is_active = True
    query = MagicMock()
    query.filter.return_value = query
    query.first.side_effect = [None, different_lti_user, different_lti_user]
    db.query.return_value = query
    monkeypatch.setattr(lti_launch_handler, "_store_code", lambda *args, **kwargs: None)

    handle_lti_launch(_make_launch_data(), _make_registration(), db)

    created_user = next(
        call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], User)
    )
    assert created_user.auth_provider is AuthProvider.LTI
    assert created_user.lti_source != different_lti_user.lti_source


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
    assert claims["lti_platform"] == "canvas"
    assert claims["lti_authz_version"] == 2
    assert claims["course_id"] == "course-42"
