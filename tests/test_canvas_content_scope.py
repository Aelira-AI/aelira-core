"""Launch-scope authorization tests for Canvas content routes."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import CloudProvider, UserRole

pytestmark = pytest.mark.integration

DEPARTMENT_ID = "dept-1"
COURSE_ID = "course-1"
OTHER_COURSE_ID = "course-2"


def _principal(
    *,
    auth_method: str = "lti",
    staff_role: str = "Instructor",
    course_id: str | None = COURSE_ID,
) -> AuthenticatedPrincipal:
    if auth_method != "lti":
        return AuthenticatedPrincipal(
            api_key=MagicMock() if auth_method == "api_key" else None,
            user_id="user-1",
            department_id=DEPARTMENT_ID,
            user_role=UserRole.FACULTY,
            auth_method=auth_method,
        )
    is_admin = staff_role == "Administrator"
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id=DEPARTMENT_ID,
        user_role=UserRole.ADMIN if is_admin else UserRole.FACULTY,
        auth_method="lti",
        lti_course_id=None if is_admin else course_id,
        lti_staff_role=staff_role,
        lti_account_wide=is_admin,
    )


def _cloud_file(
    cloud_file_id: str,
    *,
    course_id: str = COURSE_ID,
    provider: str = CloudProvider.CANVAS.value,
) -> MagicMock:
    cloud_file = MagicMock()
    cloud_file.id = cloud_file_id
    cloud_file.department_id = DEPARTMENT_ID
    cloud_file.provider = provider
    cloud_file.provider_parent_id = course_id
    cloud_file.content_source = "page"
    cloud_file.file_name = "Page"
    cloud_file.content_body = "<p>Original</p>"
    cloud_file.remediated_body = "<p>Fixed</p>"
    cloud_file.has_remediated_version = True
    cloud_file.writeback_status = "pending_review"
    cloud_file.needs_rescan = False
    cloud_file.last_scan_id = None
    cloud_file.remediated_issues_fixed = None
    cloud_file.remediated_issues_remaining = None
    return cloud_file


@contextmanager
def _client(principal: AuthenticatedPrincipal, db: MagicMock):
    app.dependency_overrides[get_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_db_dependency] = lambda: db
    try:
        with patch(
            "src.api.canvas_content_routes.require_feature", new_callable=AsyncMock
        ):
            yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_authenticated_principal, None)
        app.dependency_overrides.pop(get_db_dependency, None)


@pytest.mark.parametrize(
    "staff_role", ["Instructor", "TeachingAssistant", "ContentDeveloper"]
)
def test_course_staff_can_read_their_exact_launch_course(staff_role):
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []

    with _client(_principal(staff_role=staff_role), db) as client:
        response = client.get(f"/canvas/content/courses/{COURSE_ID}/status")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "staff_role", ["Instructor", "TeachingAssistant", "ContentDeveloper"]
)
def test_course_staff_cannot_read_another_course(staff_role):
    db = MagicMock()

    with _client(_principal(staff_role=staff_role), db) as client:
        response = client.get(f"/canvas/content/courses/{OTHER_COURSE_ID}/status")

    assert response.status_code == 403
    db.query.assert_not_called()


def test_non_lti_principal_preserves_course_access():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []

    with _client(_principal(auth_method="api_key"), db) as client:
        response = client.get(f"/canvas/content/courses/{OTHER_COURSE_ID}/status")

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/canvas/content/scan", "/canvas/content/scan/page"])
def test_scan_requires_exact_body_course_before_canvas_client(path):
    db = MagicMock()

    with patch(
        "src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock
    ) as get_client:
        with _client(_principal(), db) as client:
            response = client.post(
                path,
                json={"course_id": OTHER_COURSE_ID, "department_id": DEPARTMENT_ID},
            )

    assert response.status_code == 403
    get_client.assert_not_awaited()


@pytest.mark.parametrize("path", ["/canvas/content/scan", "/canvas/content/scan/page"])
def test_scan_requires_exact_body_department_before_canvas_client(path):
    db = MagicMock()

    with patch(
        "src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock
    ) as get_client:
        with _client(_principal(), db) as client:
            response = client.post(
                path,
                json={"course_id": COURSE_ID, "department_id": "other-dept"},
            )

    assert response.status_code == 403
    get_client.assert_not_awaited()


def test_overview_denies_course_scoped_lti_staff_before_query():
    db = MagicMock()

    with _client(_principal(), db) as client:
        response = client.get("/canvas/content/overview")

    assert response.status_code == 403
    db.query.assert_not_called()


def test_overview_allows_account_wide_lti_administrator():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []

    with _client(_principal(staff_role="Administrator"), db) as client:
        response = client.get("/canvas/content/overview")

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("get", "diff"),
        ("post", "remediate"),
        ("post", "approve"),
        ("post", "reject"),
        ("post", "writeback"),
        ("post", "rollback"),
        ("get", "audit"),
    ],
)
def test_object_routes_deny_other_course_before_mutation_or_client(method, suffix):
    db = MagicMock()
    cloud_file = _cloud_file("cf-other", course_id=OTHER_COURSE_ID)
    db.query.return_value.filter.return_value.first.return_value = cloud_file

    with patch(
        "src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock
    ) as get_client:
        with _client(_principal(), db) as client:
            response = getattr(client, method)(
                f"/canvas/content/{cloud_file.id}/{suffix}"
            )

    assert response.status_code == 403
    assert cloud_file.writeback_status == "pending_review"
    db.commit.assert_not_called()
    get_client.assert_not_awaited()


def test_object_lookup_hides_non_canvas_rows():
    db = MagicMock()
    foreign = _cloud_file("non-canvas-id", provider="google")
    # Even a misbehaving repository/test double must not let a foreign
    # provider row escape the resolver's Canvas-only boundary.
    db.query.return_value.filter.return_value.first.return_value = foreign

    with _client(_principal(), db) as client:
        response = client.get(f"/canvas/content/{foreign.id}/diff")

    assert response.status_code == 404


def test_batch_approve_rejects_missing_id_without_partial_writes():
    db = MagicMock()
    present = _cloud_file("present")
    db.query.return_value.filter.return_value.all.return_value = [present]

    with _client(_principal(), db) as client:
        response = client.post(
            "/canvas/content/batch-approve",
            json={"cloud_file_ids": [present.id, "missing"]},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Content items not found"
    assert present.writeback_status == "pending_review"
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "rows",
    [
        [_cloud_file("canvas"), _cloud_file("foreign", provider="google")],
        [_cloud_file("own"), _cloud_file("other", course_id=OTHER_COURSE_ID)],
    ],
)
def test_batch_approve_rejects_non_canvas_or_mixed_course_without_partial_writes(rows):
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows

    with _client(_principal(), db) as client:
        response = client.post(
            "/canvas/content/batch-approve",
            json={"cloud_file_ids": [row.id for row in rows]},
        )

    assert response.status_code == 404
    assert all(row.writeback_status == "pending_review" for row in rows)
    db.commit.assert_not_called()


def test_batch_approve_hides_an_other_course_only_batch():
    db = MagicMock()
    other = _cloud_file("other", course_id=OTHER_COURSE_ID)
    db.query.return_value.filter.return_value.all.return_value = [other]

    with _client(_principal(), db) as client:
        response = client.post(
            "/canvas/content/batch-approve",
            json={"cloud_file_ids": [other.id]},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Content items not found"
    assert other.writeback_status == "pending_review"
    db.commit.assert_not_called()


@pytest.mark.parametrize("auth_method", ["api_key", "session", "mock"])
def test_non_lti_batch_approve_preserves_multi_course_behavior(auth_method):
    db = MagicMock()
    first = _cloud_file("first", course_id=COURSE_ID)
    second = _cloud_file("second", course_id=OTHER_COURSE_ID)
    db.query.return_value.filter.return_value.all.return_value = [first, second]

    with _client(_principal(auth_method=auth_method), db) as client:
        response = client.post(
            "/canvas/content/batch-approve",
            json={"cloud_file_ids": [first.id, second.id]},
        )

    assert response.status_code == 200
    assert response.json()["approved_count"] == 2
    assert first.writeback_status == "approved"
    assert second.writeback_status == "approved"
    db.commit.assert_called_once()


def test_batch_writeback_requires_exact_body_course_before_query_or_client():
    db = MagicMock()

    with patch(
        "src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock
    ) as get_client:
        with _client(_principal(), db) as client:
            response = client.post(
                "/canvas/content/batch-writeback",
                json={"course_id": OTHER_COURSE_ID},
            )

    assert response.status_code == 403
    db.query.assert_not_called()
    get_client.assert_not_awaited()
