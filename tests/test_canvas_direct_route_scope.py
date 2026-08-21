"""Launch-scope enforcement for direct Canvas REST routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudOAuthCredentials,
    CloudProvider,
    Scan,
    UserRole,
)
from src.integrations.canvas.canvas_api import CanvasAPIClient

DEPT = "dept-1"
COURSE = "course-1"
OTHER_COURSE = "course-2"


def _paginated_canvas_client(*pages):
    canvas = CanvasAPIClient("http://localhost", "token")
    responses = []
    for index, page in enumerate(pages, start=1):
        response = MagicMock()
        response.json.return_value = page
        response.headers = (
            {
                "Link": (
                    f"<http://localhost/api/v1/courses/{COURSE}/files?"
                    f'page={index + 1}&per_page=100>; rel="next"'
                )
            }
            if index < len(pages)
            else {}
        )
        responses.append(response)
    request_mock = AsyncMock(side_effect=responses)
    close_mock = AsyncMock()
    canvas._request_with_retry = request_mock
    canvas.close = close_mock
    return canvas, request_mock, close_mock


def _canvas_file(file_id: int):
    return {
        "id": file_id,
        "display_name": f"File {file_id}",
        "filename": f"file-{file_id}.pdf",
        "content-type": "application/pdf",
        "size": 10,
        "url": f"https://canvas.test/files/{file_id}",
        "created_at": "2026-03-01T10:00:00Z",
        "updated_at": "2026-03-01T10:00:00Z",
    }


def _principal(staff_role: str | None = None, *, auth_method: str = "lti"):
    if auth_method != "lti":
        return AuthenticatedPrincipal(
            api_key=None,
            user_id="user-1",
            department_id=DEPT,
            user_role=UserRole.FACULTY,
            auth_method=auth_method,
        )
    if staff_role == "Administrator":
        return AuthenticatedPrincipal(
            api_key=None,
            user_id="user-1",
            department_id=DEPT,
            user_role=UserRole.ADMIN,
            auth_method="lti",
            lti_staff_role=staff_role,
            lti_account_wide=True,
        )
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id=DEPT,
        user_role=UserRole.FACULTY,
        auth_method="lti",
        lti_course_id=COURSE,
        lti_staff_role=staff_role,
        lti_account_wide=False,
    )


@pytest.fixture
def db():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    return session


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db_dependency] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db_dependency, None)
    app.dependency_overrides.pop(get_authenticated_principal, None)


def _authenticate(principal):
    app.dependency_overrides[get_authenticated_principal] = lambda: principal


@pytest.mark.parametrize(
    "staff_role", ["Instructor", "TeachingAssistant", "ContentDeveloper"]
)
def test_course_scoped_lti_staff_cannot_manage_account_connection(client, staff_role):
    _authenticate(_principal(staff_role))

    response = client.get(f"/canvas/status?department_id={DEPT}")

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.parametrize(
    "principal",
    [_principal("Administrator"), _principal(auth_method="session")],
)
def test_account_wide_and_non_lti_principals_preserve_connection_access(
    client, principal
):
    _authenticate(principal)

    response = client.get(f"/canvas/status?department_id={DEPT}")

    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_account_route_rejects_department_override(client):
    _authenticate(_principal("Administrator"))

    response = client.get("/canvas/status?department_id=other-dept")

    assert response.status_code == 403


@pytest.mark.parametrize(
    "staff_role", ["Instructor", "TeachingAssistant", "ContentDeveloper"]
)
def test_course_listing_returns_only_the_launch_course(client, staff_role):
    _authenticate(_principal(staff_role))
    canvas = AsyncMock()
    canvas.list_courses.return_value = [
        SimpleNamespace(
            id=COURSE,
            name="Launch",
            course_code="ONE",
            workflow_state="available",
            start_at=None,
            end_at=None,
        ),
        SimpleNamespace(
            id=OTHER_COURSE,
            name="Other",
            course_code="TWO",
            workflow_state="available",
            start_at=None,
            end_at=None,
        ),
    ]

    with patch(
        "src.api.canvas_routes._get_canvas_client",
        new=AsyncMock(return_value=(MagicMock(), canvas)),
    ):
        response = client.get(f"/canvas/courses?department_id={DEPT}")

    assert response.status_code == 200
    assert [course["id"] for course in response.json()] == [COURSE]


@pytest.mark.parametrize(
    "principal",
    [_principal("Administrator"), _principal(auth_method="api_key")],
)
def test_account_wide_and_non_lti_course_listing_remains_unfiltered(client, principal):
    _authenticate(principal)
    canvas = AsyncMock()
    canvas.list_courses.return_value = [
        SimpleNamespace(
            id=COURSE,
            name="Launch",
            course_code="ONE",
            workflow_state="available",
            start_at=None,
            end_at=None,
        ),
        SimpleNamespace(
            id=OTHER_COURSE,
            name="Other",
            course_code="TWO",
            workflow_state="available",
            start_at=None,
            end_at=None,
        ),
    ]

    with patch(
        "src.api.canvas_routes._get_canvas_client",
        new=AsyncMock(return_value=(MagicMock(), canvas)),
    ):
        response = client.get(f"/canvas/courses?department_id={DEPT}")

    assert response.status_code == 200
    assert [course["id"] for course in response.json()] == [COURSE, OTHER_COURSE]


def test_course_listing_rejects_department_override_before_canvas_call(client):
    _authenticate(_principal("Instructor"))
    get_client = AsyncMock()

    with patch("src.api.canvas_routes._get_canvas_client", new=get_client):
        response = client.get("/canvas/courses?department_id=other-dept")

    assert response.status_code == 403
    get_client.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("post", "/canvas/connect", {"canvas_instance_url": "https://canvas.test"}),
        ("delete", f"/canvas/disconnect?department_id={DEPT}", None),
        ("get", f"/canvas/status?department_id={DEPT}", None),
    ],
)
def test_all_account_connection_routes_deny_course_scoped_lti(
    client, method, path, json
):
    _authenticate(_principal("Instructor"))

    response = client.request(method, path, json=json)

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.parametrize(
    "staff_role", ["Instructor", "TeachingAssistant", "ContentDeveloper"]
)
@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("get", f"/canvas/courses/{OTHER_COURSE}/files?department_id={DEPT}", None),
        ("get", f"/canvas/courses/{OTHER_COURSE}/folders?department_id={DEPT}", None),
        (
            "post",
            "/canvas/remediate",
            {"file_id": "file-1", "course_id": OTHER_COURSE, "department_id": DEPT},
        ),
    ],
)
def test_course_routes_deny_courses_other_than_exact_launch(
    client, staff_role, method, path, json
):
    _authenticate(_principal(staff_role))

    response = client.request(method, path, json=json)

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_remediate_rejects_body_department_override(client):
    _authenticate(_principal(auth_method="session"))

    response = client.post(
        "/canvas/remediate",
        json={
            "file_id": "file-1",
            "course_id": COURSE,
            "department_id": "other-dept",
        },
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "staff_role", ["Instructor", "TeachingAssistant", "ContentDeveloper"]
)
@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/canvas/scan",
            {"department_id": DEPT, "course_id": OTHER_COURSE, "file_id": "file-1"},
        ),
        (
            "/canvas/scan/bulk",
            {"department_id": DEPT, "course_id": OTHER_COURSE},
        ),
    ],
)
def test_scan_routes_deny_courses_other_than_exact_launch(
    client, staff_role, path, body
):
    _authenticate(_principal(staff_role))

    response = client.post(path, json=body)

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_scan_rejects_body_department_override(client):
    _authenticate(_principal(auth_method="session"))

    response = client.post(
        "/canvas/scan",
        json={
            "department_id": "other-dept",
            "course_id": COURSE,
            "file_id": "file-1",
        },
    )

    assert response.status_code == 403


def test_scan_hides_cached_file_from_another_course_before_queueing(client, db):
    _authenticate(_principal("Instructor"))
    other_course_file = SimpleNamespace(
        id="cf-other",
        needs_rescan=False,
        provider_parent_id=OTHER_COURSE,
    )
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.first.return_value = other_course_file
    db.query.side_effect = lambda model: (
        cloud_file_query if model is CloudFile else MagicMock()
    )
    canvas = AsyncMock()
    canvas.list_course_files.return_value = []
    enqueue = MagicMock()

    with (
        patch("src.api.canvas_scan_routes.require_feature", new=AsyncMock()),
        patch(
            "src.api.canvas_scan_routes._get_canvas_client",
            new=AsyncMock(return_value=(SimpleNamespace(id="cred-1"), canvas)),
        ),
        patch(
            "src.api.canvas_scan_routes.enqueue_cloud_job",
            enqueue,
        ),
    ):
        response = client.post(
            "/canvas/scan",
            json={"department_id": DEPT, "course_id": COURSE, "file_id": "file-1"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Canvas file not found"}
    canvas.list_course_files.assert_awaited_once_with(COURSE)
    canvas.close.assert_awaited_once()
    db.add.assert_not_called()
    enqueue.assert_not_called()
    assert other_course_file.provider_parent_id == OTHER_COURSE


def test_scan_cached_lookup_is_constrained_to_requested_course(client, db):
    _authenticate(_principal("Instructor"))
    cached_file = SimpleNamespace(
        id="cf-current",
        needs_rescan=False,
        provider_parent_id=COURSE,
    )
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.first.return_value = cached_file
    db.query.side_effect = lambda model: (
        cloud_file_query if model is CloudFile else MagicMock()
    )
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [SimpleNamespace(id="file-1")]

    with (
        patch("src.api.canvas_scan_routes.require_feature", new=AsyncMock()),
        patch(
            "src.api.canvas_scan_routes._get_canvas_client",
            new=AsyncMock(return_value=(SimpleNamespace(id="cred-1"), canvas)),
        ),
    ):
        response = client.post(
            "/canvas/scan",
            json={"department_id": DEPT, "course_id": COURSE, "file_id": "file-1"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "already_scanned"
    predicates = " ".join(str(arg) for arg in cloud_file_query.filter.call_args.args)
    assert "cloud_files.provider_parent_id" in predicates
    db.add.assert_not_called()
    canvas.close.assert_awaited_once()


def test_scan_finds_target_file_after_first_canvas_page(client, db):
    _authenticate(_principal("Instructor"))
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.first.return_value = None
    db.query.side_effect = lambda model: cloud_file_query
    first_page = [_canvas_file(file_id) for file_id in range(1, 101)]
    canvas, request_mock, close_mock = _paginated_canvas_client(
        first_page, [_canvas_file(101)]
    )
    enqueue = MagicMock(return_value=SimpleNamespace(id="job-101"))
    execute_scan = AsyncMock()

    with (
        patch("src.api.canvas_scan_routes.require_feature", new=AsyncMock()),
        patch(
            "src.api.canvas_scan_routes._get_canvas_client",
            new=AsyncMock(return_value=(SimpleNamespace(id="cred-1"), canvas)),
        ),
        patch(
            "src.api.canvas_scan_routes.enqueue_cloud_job",
            enqueue,
        ),
        patch("src.jobs.cloud_scan_job.CloudScanJob.run", execute_scan),
    ):
        response = client.post(
            "/canvas/scan",
            json={"department_id": DEPT, "course_id": COURSE, "file_id": "101"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert request_mock.await_count == 2
    close_mock.assert_awaited_once()
    created = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], CloudFile)
    )
    enqueue.assert_called_once_with(
        db,
        department_id=DEPT,
        job_type="scan",
        payload={
            "cloud_file_id": created.id,
            "credential_id": "cred-1",
            "provider": "canvas",
            "provider_file_id": "101",
            "course_id": COURSE,
        },
        dedupe_key="scan:canvas:course-1:101:2026-03-01 10:00:00+00:00",
        cloud_file_id=created.id,
        credential_id="cred-1",
        provider="canvas",
        provider_file_id="101",
        priority=5,
    )
    execute_scan.assert_not_awaited()


def test_bulk_scan_includes_all_files_across_canvas_pages(client, db):
    _authenticate(_principal("Instructor"))
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.first.return_value = None
    db.query.side_effect = lambda model: cloud_file_query
    first_page = [_canvas_file(file_id) for file_id in range(1, 101)]
    canvas, request_mock, close_mock = _paginated_canvas_client(
        first_page, [_canvas_file(101)]
    )
    enqueue = MagicMock(
        side_effect=lambda *args, **kwargs: SimpleNamespace(
            id=f"job-{kwargs['provider_file_id']}"
        )
    )
    execute_scan = AsyncMock()

    with (
        patch("src.api.canvas_scan_routes.require_feature", new=AsyncMock()),
        patch(
            "src.api.canvas_scan_routes._get_canvas_client",
            new=AsyncMock(return_value=(SimpleNamespace(id="cred-1"), canvas)),
        ),
        patch(
            "src.api.canvas_scan_routes.enqueue_cloud_job",
            enqueue,
        ),
        patch("src.jobs.cloud_scan_job.CloudScanJob.run", execute_scan),
    ):
        response = client.post(
            "/canvas/scan/bulk",
            json={"department_id": DEPT, "course_id": COURSE},
        )

    assert response.status_code == 200
    assert response.json()["total"] == 101
    assert len(response.json()["jobs"]) == 101
    assert response.json()["jobs"][-1]["file_id"] == "101"
    created_cloud_files = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], CloudFile)
    ]
    assert len(created_cloud_files) == 101
    assert enqueue.call_count == 101
    first_enqueue = enqueue.call_args_list[0].kwargs
    last_enqueue = enqueue.call_args_list[-1].kwargs
    assert first_enqueue["department_id"] == DEPT
    assert first_enqueue["credential_id"] == "cred-1"
    assert first_enqueue["payload"] == {
        "cloud_file_id": created_cloud_files[0].id,
        "credential_id": "cred-1",
        "provider": "canvas",
        "provider_file_id": "1",
        "course_id": COURSE,
    }
    assert first_enqueue["dedupe_key"] == (
        "scan:canvas:course-1:1:2026-03-01 10:00:00+00:00"
    )
    assert last_enqueue["payload"]["provider_file_id"] == "101"
    assert last_enqueue["dedupe_key"] == (
        "scan:canvas:course-1:101:2026-03-01 10:00:00+00:00"
    )
    assert request_mock.await_count == 2
    close_mock.assert_awaited_once()
    execute_scan.assert_not_awaited()


def test_bulk_scan_does_not_reassign_cached_file_from_another_course(client, db):
    _authenticate(_principal("Instructor"))
    other_course_file = SimpleNamespace(
        id="cf-other",
        needs_rescan=False,
        provider_parent_id=OTHER_COURSE,
    )
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.first.return_value = other_course_file
    db.query.side_effect = lambda model: (
        cloud_file_query if model is CloudFile else MagicMock()
    )
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [
        SimpleNamespace(
            id="file-1",
            filename="syllabus.pdf",
            display_name="Syllabus",
            content_type="application/pdf",
            size=100,
            url="https://canvas.test/files/file-1",
        )
    ]
    enqueue = MagicMock(return_value=SimpleNamespace(id="job-file-1"))

    with (
        patch("src.api.canvas_scan_routes.require_feature", new=AsyncMock()),
        patch(
            "src.api.canvas_scan_routes._get_canvas_client",
            new=AsyncMock(return_value=(SimpleNamespace(id="cred-1"), canvas)),
        ),
        patch(
            "src.api.canvas_scan_routes.enqueue_cloud_job",
            enqueue,
        ),
    ):
        response = client.post(
            "/canvas/scan/bulk",
            json={"department_id": DEPT, "course_id": COURSE},
        )

    assert response.status_code == 200
    assert other_course_file.provider_parent_id == OTHER_COURSE
    predicates = " ".join(str(arg) for arg in cloud_file_query.filter.call_args.args)
    assert "cloud_files.provider_parent_id" in predicates
    created_files = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], CloudFile)
    ]
    assert len(created_files) == 1
    assert created_files[0].provider_parent_id == COURSE
    created = created_files[0]
    enqueue.assert_called_once_with(
        db,
        department_id=DEPT,
        job_type="scan",
        payload={
            "cloud_file_id": created.id,
            "credential_id": "cred-1",
            "provider": "canvas",
            "provider_file_id": "file-1",
            "course_id": COURSE,
        },
        dedupe_key="scan:canvas:course-1:file-1:current",
        cloud_file_id=created.id,
        credential_id="cred-1",
        provider="canvas",
        provider_file_id="file-1",
        priority=5,
    )
    canvas.close.assert_awaited_once()


def test_remediate_hides_file_from_another_course_before_queueing(client, db):
    _authenticate(_principal("Instructor"))
    credential = SimpleNamespace(id="cred-1")
    other_course_file = SimpleNamespace(
        id="cf-other",
        provider_parent_id=OTHER_COURSE,
    )
    credential_query = MagicMock()
    credential_query.filter.return_value = credential_query
    credential_query.first.return_value = credential
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.first.return_value = other_course_file
    db.query.side_effect = lambda model: (
        credential_query if model is CloudOAuthCredentials else cloud_file_query
    )
    canvas = AsyncMock()
    canvas.list_course_files.return_value = []
    enqueue = MagicMock()

    with (
        patch("src.api.canvas_routes.require_feature", new=AsyncMock()),
        patch(
            "src.api.canvas_routes._get_canvas_client",
            new=AsyncMock(return_value=(credential, canvas)),
        ),
        patch(
            "src.api.canvas_routes.enqueue_cloud_job",
            enqueue,
        ),
    ):
        response = client.post(
            "/canvas/remediate",
            json={"department_id": DEPT, "course_id": COURSE, "file_id": "file-1"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Canvas file not found"}
    canvas.list_course_files.assert_awaited_once_with(COURSE)
    canvas.close.assert_awaited_once()
    db.add.assert_not_called()
    enqueue.assert_not_called()
    assert other_course_file.provider_parent_id == OTHER_COURSE


def test_scan_status_denies_course_other_than_exact_launch(client):
    _authenticate(_principal("Instructor"))

    response = client.get(f"/canvas/courses/{OTHER_COURSE}/scan-status?file_ids=file-1")

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_scan_status_query_is_constrained_to_canvas_course_and_department(client, db):
    _authenticate(_principal("Instructor"))
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.all.return_value = []
    db.query.side_effect = lambda model: (
        cloud_file_query if model is CloudFile else MagicMock()
    )

    response = client.get(f"/canvas/courses/{COURSE}/scan-status?file_ids=file-1")

    assert response.status_code == 200
    predicates = " ".join(str(arg) for arg in cloud_file_query.filter.call_args.args)
    assert "cloud_files.provider" in predicates
    assert "cloud_files.provider_parent_id" in predicates
    assert "cloud_files.department_id" in predicates


def test_upload_course_mismatch_is_indistinguishable_from_unknown_scan_before_mutation(
    client, db
):
    _authenticate(_principal("Instructor"))
    cloud_file = SimpleNamespace(
        id="cf-1",
        department_id=DEPT,
        provider=CloudProvider.CANVAS.value,
        provider_parent_id=OTHER_COURSE,
    )
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.first.return_value = cloud_file
    scan_query = MagicMock()
    scan_query.filter.return_value = scan_query
    scan_query.first.return_value = SimpleNamespace(id="scan-1", department_id=DEPT)
    db.query.side_effect = lambda model: (
        cloud_file_query if model is CloudFile else scan_query
    )
    get_client = AsyncMock()

    with patch("src.api.canvas_scan_routes._get_canvas_client", new=get_client):
        response = client.post(
            "/canvas/upload-remediated",
            json={"scan_id": "scan-1", "course_id": COURSE},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Scan not found"}
    get_client.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_upload_hides_cross_department_or_non_canvas_scan_link(client, db):
    _authenticate(_principal("Administrator"))
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.first.return_value = None
    db.query.side_effect = lambda model: (
        cloud_file_query if model is CloudFile else MagicMock()
    )

    response = client.post(
        "/canvas/upload-remediated",
        json={"scan_id": "scan-1", "course_id": COURSE},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Scan not found"}
    predicates = " ".join(str(arg) for arg in cloud_file_query.filter.call_args.args)
    assert "cloud_files.last_scan_id" in predicates
    assert "cloud_files.department_id" in predicates
    assert "cloud_files.provider" in predicates


@pytest.mark.parametrize(
    "principal",
    [_principal("Administrator"), _principal(auth_method="session")],
)
def test_account_wide_and_non_lti_scan_status_can_access_other_course(
    client, db, principal
):
    _authenticate(principal)
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.all.return_value = []
    db.query.side_effect = lambda model: cloud_file_query

    response = client.get(f"/canvas/courses/{OTHER_COURSE}/scan-status?file_ids=file-1")

    assert response.status_code == 200


def test_upload_hides_scan_row_not_linked_to_same_department_canvas_file(client, db):
    _authenticate(_principal("Administrator"))
    cloud_file = SimpleNamespace(
        id="cf-1",
        provider_parent_id=COURSE,
    )
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.first.return_value = cloud_file
    scan_query = MagicMock()
    scan_query.filter.return_value = scan_query
    scan_query.first.return_value = None
    db.query.side_effect = lambda model: (
        cloud_file_query if model is CloudFile else scan_query
    )

    response = client.post(
        "/canvas/upload-remediated",
        json={"scan_id": "scan-mismatch", "course_id": COURSE},
    )

    assert response.status_code == 404
    predicates = " ".join(str(arg) for arg in scan_query.filter.call_args.args)
    assert "scans.id" in predicates
    assert "scans.department_id" in predicates


def test_upload_never_uses_same_filename_remediation_from_another_course(
    client, db, tmp_path
):
    _authenticate(_principal("Instructor"))
    other_course_path = tmp_path / "other-course" / "syllabus_remediated.pdf"
    other_course_path.parent.mkdir()
    other_course_path.write_bytes(b"other course remediation")

    cloud_file = SimpleNamespace(
        id="cf-current",
        department_id=DEPT,
        provider=CloudProvider.CANVAS.value,
        provider_parent_id=COURSE,
    )
    authorized_scan = SimpleNamespace(
        id="scan-current",
        department_id=DEPT,
        file_name="syllabus.pdf",
        storage_path=str(tmp_path / "current-course" / "syllabus.pdf"),
    )
    other_course_scan = SimpleNamespace(
        id="scan-other",
        department_id=DEPT,
        file_name="syllabus.pdf",
        storage_path=str(other_course_path),
    )
    cloud_file_query = MagicMock()
    cloud_file_query.filter.return_value = cloud_file_query
    cloud_file_query.first.return_value = cloud_file
    scan_query = MagicMock()
    scan_query.filter.return_value = scan_query
    scan_query.order_by.return_value = scan_query
    scan_query.limit.return_value = scan_query
    scan_query.first.return_value = authorized_scan
    scan_query.all.return_value = [other_course_scan]
    remediation_job_query = MagicMock()
    remediation_job_query.filter.return_value = remediation_job_query
    remediation_job_query.order_by.return_value = remediation_job_query
    remediation_job_query.first.return_value = None
    db.query.side_effect = lambda model: {
        CloudFile: cloud_file_query,
        Scan: scan_query,
        CloudJobQueue: remediation_job_query,
    }[model]
    canvas = AsyncMock()
    canvas.upload_file.return_value = SimpleNamespace(
        success=True,
        file_id="uploaded-wrong-file",
        web_view_link="https://canvas.test/files/uploaded-wrong-file",
        error=None,
    )

    with (
        patch("src.api.canvas_scan_routes.require_feature", new=AsyncMock()),
        patch(
            "src.api.canvas_scan_routes._get_canvas_client",
            new=AsyncMock(return_value=(SimpleNamespace(id="cred-1"), canvas)),
        ),
    ):
        response = client.post(
            "/canvas/upload-remediated",
            json={"scan_id": "scan-current", "course_id": COURSE},
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "No remediated file found for this scan. Please remediate the file first."
    }
    canvas.upload_file.assert_not_awaited()
    canvas.close.assert_not_awaited()
    db.commit.assert_not_called()
