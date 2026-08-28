"""Canvas LTI launch-scope tests for generic education routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.education._shared import get_api_key_or_mock
from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import (
    CloudFile,
    CloudOAuthCredentials,
    CloudProvider,
    Scan,
    ScanType,
    UserRole,
)

DEPT = "dept-1"
OTHER_DEPT = "dept-2"
COURSE = "course-1"
OTHER_COURSE = "course-2"


def _principal(role="Instructor", *, auth_method="lti"):
    if auth_method != "lti":
        return AuthenticatedPrincipal(
            api_key=None,
            user_id="user-1",
            department_id=DEPT,
            user_role=UserRole.FACULTY,
            auth_method=auth_method,
        )
    if role == "Administrator":
        return AuthenticatedPrincipal(
            api_key=None,
            user_id="user-1",
            department_id=DEPT,
            user_role=UserRole.ADMIN,
            auth_method="lti",
            lti_staff_role=role,
            lti_account_wide=True,
        )
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id=DEPT,
        user_role=UserRole.FACULTY,
        auth_method="lti",
        lti_course_id=COURSE,
        lti_staff_role=role,
    )


def _scan(scan_id="scan-1", *, department_id=DEPT, result=None):
    return SimpleNamespace(
        id=scan_id,
        department_id=department_id,
        file_name="document.pdf",
        scan_type=ScanType.PDF,
        status=SimpleNamespace(value="completed"),
        pages=1,
        file_size_bytes=10,
        processing_time_ms=1,
        progress=100,
        progress_message="Done",
        error_message=None,
        created_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00"),
        completed_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:01"),
        result=result,
        storage_path=None,
    )


def _cloud_file(
    scan_id="scan-1",
    *,
    department_id=DEPT,
    provider=CloudProvider.CANVAS.value,
    course_id=COURSE,
):
    return SimpleNamespace(
        id="cf-1",
        last_scan_id=scan_id,
        department_id=department_id,
        provider=provider,
        provider_parent_id=course_id,
        credential_id="credential-1",
        provider_file_id="provider-file-1",
        file_name="document.pdf",
        provider_metadata={},
        has_remediated_version=False,
    )


def _db(*, scan=None, cloud_file=None, list_scans=None):
    db = MagicMock()
    scan_query = MagicMock()
    scan_query.filter.return_value = scan_query
    scan_query.first.return_value = scan

    cloud_query = MagicMock()
    cloud_query.filter.return_value = cloud_query
    cloud_query.limit.return_value = cloud_query
    cloud_query.first.return_value = cloud_file
    cloud_query.all.return_value = [cloud_file] if cloud_file is not None else []

    list_query = MagicMock()
    for method in ("join", "filter", "order_by", "limit", "offset"):
        getattr(list_query, method).return_value = list_query
    list_query.all.return_value = list_scans or []

    def query(model):
        if model is CloudFile:
            return cloud_query
        if model is Scan:
            return list_query if list_scans is not None else scan_query
        return MagicMock()

    db.query.side_effect = query
    db._cloud_query = cloud_query
    db._list_query = list_query
    return db


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.pop(get_db_dependency, None)
    app.dependency_overrides.pop(get_authenticated_principal, None)
    app.dependency_overrides.pop(get_api_key_or_mock, None)


def _authenticate(client, principal, db):
    app.dependency_overrides[get_db_dependency] = lambda: db
    app.dependency_overrides[get_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_api_key_or_mock] = lambda: principal.as_legacy_tuple()
    return client


@pytest.mark.parametrize(
    "role", ["Instructor", "TeachingAssistant", "ContentDeveloper"]
)
def test_course_staff_can_read_scan_linked_to_exact_launch_course(client, role):
    scan = _scan()
    db = _db(scan=scan, cloud_file=_cloud_file())
    _authenticate(client, _principal(role), db)

    with patch(
        "src.api.education.scan_history_routes.ScanService.get_scan_with_result",
        return_value=scan,
    ):
        response = client.get("/education/scans/scan-1")

    assert response.status_code == 200
    assert response.json()["scan"]["scan_id"] == "scan-1"


@pytest.mark.parametrize(
    "role", ["Instructor", "TeachingAssistant", "ContentDeveloper"]
)
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/education/scans/scan-1"),
        ("get", "/education/scans/scan-1/progress"),
        ("get", "/education/scans/scan-1/report"),
        ("get", "/education/scans/scan-1/html"),
        ("delete", "/education/scans/scan-1"),
        ("post", "/education/remediate/scan-1"),
        ("post", "/education/code/remediate/scan-1"),
        ("get", "/education/scans/scan-1/remediated"),
        ("get", "/education/scans/scan-1/remediated/formats"),
    ],
)
def test_course_staff_object_routes_hide_generic_scans(client, role, method, path):
    result = SimpleNamespace(html_output="<html></html>", issues=[])
    scan = _scan(result=result)
    db = _db(scan=scan, cloud_file=None)
    _authenticate(client, _principal(role), db)

    with (
        patch(
            "src.api.education.scan_history_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
    ):
        response = client.request(method, path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Scan not found"}
    db.delete.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("cloud_file", "expected"),
    [
        (_cloud_file(provider=CloudProvider.GOOGLE.value), 404),
        (_cloud_file(course_id=OTHER_COURSE), 404),
        (_cloud_file(department_id=OTHER_DEPT), 404),
    ],
)
def test_course_staff_reject_non_canvas_cross_course_and_cross_department_scans(
    client, cloud_file, expected
):
    scan = _scan()
    db = _db(scan=scan, cloud_file=cloud_file)
    _authenticate(client, _principal(), db)

    with patch(
        "src.api.education.scan_history_routes.ScanService.get_scan_with_result",
        return_value=scan,
    ):
        response = client.get("/education/scans/scan-1")

    assert response.status_code == expected


@pytest.mark.parametrize(
    "principal", [_principal("Administrator"), _principal(auth_method="session")]
)
def test_account_admin_and_non_lti_keep_department_scan_access(client, principal):
    scan = _scan()
    db = _db(scan=scan)
    _authenticate(client, principal, db)

    with patch(
        "src.api.education.scan_history_routes.ScanService.get_scan_with_result",
        return_value=scan,
    ):
        response = client.get("/education/scans/scan-1")

    assert response.status_code == 200


def test_non_lti_cross_department_behavior_remains_forbidden(client):
    scan = _scan(department_id=OTHER_DEPT)
    db = _db(scan=scan)
    _authenticate(client, _principal(auth_method="api_key"), db)

    with patch(
        "src.api.education.scan_history_routes.ScanService.get_scan_with_result",
        return_value=scan,
    ):
        response = client.get("/education/scans/scan-1")

    assert response.status_code == 403


def test_course_staff_scan_list_is_query_bound_to_canvas_launch_course(client):
    scan = _scan()
    db = _db(list_scans=[scan])
    _authenticate(client, _principal(), db)

    with patch(
        "src.api.education.scan_history_routes.ScanService.get_scan_history"
    ) as legacy_history:
        response = client.get("/education/scans")

    assert response.status_code == 200
    assert response.json()["total_returned"] == 1
    assert response.json()["scans"][0]["compliance_score"] is None
    assert response.json()["scans"][0]["total_issues"] is None
    legacy_history.assert_not_called()
    db._list_query.join.assert_called_once()
    predicates = " ".join(
        str(arg) for call in db._list_query.filter.call_args_list for arg in call.args
    )
    assert "cloud_files.department_id" in predicates
    assert "cloud_files.provider" in predicates
    assert "cloud_files.provider_parent_id" in predicates
    join_predicate = " ".join(str(arg) for arg in db._list_query.join.call_args.args)
    assert "cloud_files.last_scan_id" in join_predicate


@pytest.mark.parametrize(
    "role", ["Instructor", "TeachingAssistant", "ContentDeveloper"]
)
def test_course_staff_cannot_read_department_stats(client, role):
    db = _db()
    _authenticate(client, _principal(role), db)

    with patch(
        "src.api.education.scan_history_routes.ScanService.get_department_stats"
    ) as stats:
        response = client.get("/education/stats")

    assert response.status_code == 403
    stats.assert_not_called()


@pytest.mark.parametrize(
    "principal", [_principal("Administrator"), _principal(auth_method="session")]
)
def test_account_admin_and_non_lti_keep_stats_access(client, principal):
    db = _db()
    _authenticate(client, principal, db)

    with patch(
        "src.api.education.scan_history_routes.ScanService.get_department_stats",
        return_value={"total_scans": 0},
    ):
        response = client.get("/education/stats")

    assert response.status_code == 200


def test_brightspace_account_admin_can_read_provider_neutral_stats(client):
    principal = AuthenticatedPrincipal(
        api_key=None,
        user_id="brightspace-admin",
        department_id=DEPT,
        user_role=UserRole.ADMIN,
        auth_method="lti",
        lti_staff_role="Administrator",
        lti_account_wide=True,
        lti_platform="brightspace",
    )
    db = _db()
    _authenticate(client, principal, db)

    with patch(
        "src.api.education.scan_history_routes.ScanService.get_department_stats",
        return_value={"total_scans": 0, "deadline": None},
    ):
        response = client.get("/education/stats")

    assert response.status_code == 200


def test_course_staff_batch_is_atomic_for_mixed_launch_scope(client):
    result = SimpleNamespace(issues=[])
    scans = {
        "scan-1": _scan("scan-1", result=result),
        "scan-2": _scan("scan-2", result=result),
    }
    cloud_files = {
        "scan-1": _cloud_file("scan-1"),
        "scan-2": _cloud_file("scan-2", course_id=OTHER_COURSE),
    }
    db = _db()
    db.query.side_effect = lambda model: MagicMock()
    _authenticate(client, _principal(), db)

    def scan_lookup(*, db, scan_id):
        return scans.get(scan_id)

    # Scope helper performs one constrained lookup per scan; return the linked rows.
    cloud_query = MagicMock()
    cloud_query.filter.return_value = cloud_query
    cloud_query.first.side_effect = [cloud_files["scan-1"], cloud_files["scan-2"]]
    db.query.side_effect = lambda model: (
        cloud_query if model is CloudFile else MagicMock()
    )

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            side_effect=scan_lookup,
        ),
        patch(
            "src.api.education.remediation_routes.require_feature", new=AsyncMock()
        ) as require_feature,
    ):
        response = client.post("/education/remediate/batch", json=["scan-1", "scan-2"])

    assert response.status_code == 404
    require_feature.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_non_lti_remediation_rejects_cross_tenant_cloud_file_before_side_effects(
    client,
):
    result = SimpleNamespace(issues=[{"description": "Missing document title"}])
    scan = _scan(result=result)
    cloud_file = _cloud_file(department_id=OTHER_DEPT)
    # The tenant-bound CloudFile query excludes this foreign row.
    db = _db(scan=scan, cloud_file=None)
    _authenticate(client, _principal(auth_method="session"), db)

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.integrations.oauth_token_manager.OAuthTokenManager.refresh_if_expired",
            new=AsyncMock(),
        ) as refresh_token,
        patch("src.integrations.canvas.canvas_api.CanvasAPIClient") as canvas_client,
    ):
        response = client.post("/education/remediate/scan-1")

    assert response.status_code == 400
    refresh_token.assert_not_awaited()
    canvas_client.assert_not_called()
    assert cloud_file.has_remediated_version is False
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("credential_department", "credential_provider"),
    [
        (OTHER_DEPT, CloudProvider.CANVAS.value),
        (DEPT, CloudProvider.GOOGLE.value),
    ],
)
def test_non_lti_remediation_rejects_unbound_credential_before_side_effects(
    client, credential_department, credential_provider
):
    result = SimpleNamespace(issues=[{"description": "Missing document title"}])
    scan = _scan(result=result)
    cloud_file = _cloud_file()
    credential = SimpleNamespace(
        id=cloud_file.credential_id,
        department_id=credential_department,
        provider=credential_provider,
    )
    db = _db(scan=scan, cloud_file=cloud_file)
    credential_query = MagicMock()
    credential_query.filter.return_value = credential_query
    credential_query.first.return_value = credential
    db.query.side_effect = lambda model: (
        db._cloud_query
        if model is CloudFile
        else credential_query if model is CloudOAuthCredentials else MagicMock()
    )
    _authenticate(client, _principal(auth_method="session"), db)

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.integrations.oauth_token_manager.OAuthTokenManager.refresh_if_expired",
            new=AsyncMock(),
        ) as refresh_token,
        patch("src.integrations.canvas.canvas_api.CanvasAPIClient") as canvas_client,
        patch(
            "src.integrations.google_workspace.google_drive.GoogleDriveIntegration"
        ) as google_drive,
    ):
        response = client.post("/education/remediate/scan-1")

    assert response.status_code == 400
    refresh_token.assert_not_awaited()
    canvas_client.assert_not_called()
    google_drive.assert_not_called()
    assert cloud_file.has_remediated_version is False
    db.commit.assert_not_called()


def test_non_lti_fallback_helpers_return_only_same_tenant_provider_objects():
    from src.api.education.remediation_routes import (
        _get_bound_cloud_credential,
        _get_bound_fallback_cloud_file,
    )

    cloud_file = _cloud_file()
    credential = SimpleNamespace(
        id=cloud_file.credential_id,
        department_id=DEPT,
        provider=cloud_file.provider,
    )
    db = _db(cloud_file=cloud_file)
    credential_query = MagicMock()
    credential_query.filter.return_value = credential_query
    credential_query.first.return_value = credential
    db.query.side_effect = lambda model: (
        db._cloud_query
        if model is CloudFile
        else credential_query if model is CloudOAuthCredentials else MagicMock()
    )

    bound_cloud_file = _get_bound_fallback_cloud_file(db, "scan-1", DEPT)
    bound_credential = _get_bound_cloud_credential(db, bound_cloud_file, DEPT)

    assert bound_cloud_file is cloud_file
    assert bound_credential is credential
    cloud_predicates = " ".join(
        str(arg) for arg in db._cloud_query.filter.call_args.args
    )
    credential_predicates = " ".join(
        str(arg) for arg in credential_query.filter.call_args.args
    )
    assert "cloud_files.last_scan_id" in cloud_predicates
    assert "cloud_files.department_id" in cloud_predicates
    assert "cloud_oauth_credentials.id" in credential_predicates
    assert "cloud_oauth_credentials.department_id" in credential_predicates
    assert "cloud_oauth_credentials.provider" in credential_predicates


def test_html_route_unknown_scan_uses_scan_not_found_detail(client):
    db = _db(scan=None)
    _authenticate(client, _principal(), db)

    with patch(
        "src.api.education.scan_history_routes.ScanService.get_scan_with_result",
        return_value=None,
    ):
        response = client.get("/education/scans/unknown/html")

    assert response.status_code == 404
    assert response.json() == {"detail": "Scan not found"}


def test_html_route_in_scope_scan_without_html_keeps_html_specific_detail(client):
    scan = _scan(result=SimpleNamespace(html_output=None))
    db = _db(scan=scan, cloud_file=_cloud_file())
    _authenticate(client, _principal(), db)

    with patch(
        "src.api.education.scan_history_routes.ScanService.get_scan_with_result",
        return_value=scan,
    ):
        response = client.get("/education/scans/scan-1/html")

    assert response.status_code == 404
    assert response.json() == {"detail": "HTML output not found"}
