"""Cloud scan identity is bound by the public Scan document fields."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.education.remediation_routes import _resolve_bound_scan_cloud_file
from src.api import review_routes
from src.api import canvas_scan_routes, canvas_content_routes
from src.api.education._scope import authorize_review_scan_access, authorize_scan_access
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import CloudFile, Scan, ScanType, UserRole


def _principal():
    return SimpleNamespace(
        department_id="department-1", auth_method="api_key", lti_account_wide=False
    )


def _cloud_file(file_id: str):
    return SimpleNamespace(
        id=file_id,
        department_id="department-1",
        last_scan_id="scan-1",
        provider="google_drive",
    )


def _db(*, scan, files):
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.limit.return_value = query
    query.all.return_value = files
    query.first.return_value = scan
    return db


@pytest.mark.parametrize(
    "document_source,document_id,file_ids",
    [
        ("cloud_file", "old-file", ["new-file"]),
        ("cloud_file", "file-1", ["file-1", "file-2"]),
        ("local", None, ["file-1"]),
    ],
)
def test_managed_artifact_rejects_stale_or_ambiguous_public_cloud_binding(
    document_source, document_id, file_ids
):
    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        document_source=document_source,
        document_id=document_id,
    )
    db = _db(scan=scan, files=[_cloud_file(file_id) for file_id in file_ids])

    with pytest.raises(HTTPException) as error:
        _resolve_bound_scan_cloud_file(db, scan, _principal(), None)

    assert error.value.status_code == 404


@pytest.mark.parametrize(
    "document_source,document_id,file_ids",
    [
        ("cloud_file", "old-file", ["new-file"]),
        ("cloud_file", "file-1", ["file-1", "file-2"]),
        ("local", None, ["file-1"]),
    ],
)
def test_review_edit_rejects_stale_or_ambiguous_public_cloud_binding(
    monkeypatch, document_source, document_id, file_ids
):
    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        scan_type="PDF",
        document_source=document_source,
        document_id=document_id,
    )
    db = _db(scan=scan, files=[_cloud_file(file_id) for file_id in file_ids])
    monkeypatch.setattr(review_routes, "authorize_pdf_scan_access", lambda *_: None)

    with pytest.raises(HTTPException) as error:
        review_routes._pdf_edit_authority(
            db, "scan-1", _principal(), file_ids[0], require_pdf=True
        )

    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document_id,expected_uploads", [("old-file", 0), ("file-1", 1)]
)
async def test_canvas_single_file_upload_checks_public_scan_identity_before_writeback(
    monkeypatch, document_id, expected_uploads
):
    cloud = _cloud_file("file-1")
    cloud.provider = "canvas"
    cloud.provider_parent_id = "course-1"
    cloud.content_source = "file"
    cloud.current_remediation_artifact_id = "artifact-1"
    cloud.writeback_status = "approved"
    cloud.needs_rescan = False
    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        document_source="cloud_file",
        document_id=document_id,
    )
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = cloud if model is CloudFile else scan
        return chain

    db.query.side_effect = query
    principal = _principal()
    principal.user_id = "reviewer-1"
    client = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(canvas_scan_routes, "require_feature", AsyncMock())
    monkeypatch.setattr(
        canvas_scan_routes,
        "_get_canvas_client",
        AsyncMock(return_value=(SimpleNamespace(id="credential-1"), client)),
    )
    monkeypatch.setattr(
        canvas_scan_routes, "_rewrite_localhost_for_docker", lambda *_: None
    )
    scanner = MagicMock()
    scanner.write_back_file = AsyncMock(
        return_value={"success": True, "url": "https://canvas.example/file/2"}
    )
    monkeypatch.setattr(canvas_scan_routes, "CanvasContentScanner", lambda **_: scanner)

    request = canvas_scan_routes.CanvasUploadRemediatedRequest(
        scan_id="scan-1", course_id="course-1"
    )
    if expected_uploads:
        response = await canvas_scan_routes.upload_remediated_to_canvas(
            request, db, principal
        )
        assert response.success is True
    else:
        with pytest.raises(HTTPException) as error:
            await canvas_scan_routes.upload_remediated_to_canvas(request, db, principal)
        assert error.value.status_code == 404
    assert scanner.write_back_file.await_count == expected_uploads


@pytest.mark.asyncio
async def test_canvas_batch_keeps_approved_file_path(monkeypatch):
    cloud = _cloud_file("file-1")
    cloud.provider = "canvas"
    cloud.content_source = "file"
    cloud.provider_parent_id = "course-1"
    cloud.writeback_status = "approved"
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.all.side_effect = [[], [cloud]]
    principal = _principal()
    principal.user_id = "reviewer-1"
    client = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(canvas_content_routes, "require_feature", AsyncMock())
    monkeypatch.setattr(
        canvas_content_routes,
        "_get_canvas_client",
        AsyncMock(return_value=(SimpleNamespace(id="credential-1"), client)),
    )
    scanner = MagicMock()
    scanner.write_back_file = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(
        canvas_content_routes, "CanvasContentScanner", lambda **_: scanner
    )

    response = await canvas_content_routes.batch_writeback_content(
        canvas_content_routes.BatchWritebackRequest(course_id="course-1"), db, principal
    )

    assert response.written_count == 1
    scanner.write_back_file.assert_awaited_once_with(cloud, approved_by="reviewer-1")


@pytest.mark.parametrize("platform", ["canvas", "brightspace"])
def test_real_public_scan_allows_exact_course_cloud_binding(platform):
    scan = Scan(
        id="scan-1",
        department_id="department-1",
        scan_type=ScanType.PDF,
        document_source="cloud_file",
        document_id="file-1",
    )
    cloud = CloudFile(
        id="file-1",
        department_id="department-1",
        last_scan_id="scan-1",
        provider=platform,
        provider_parent_id="course-1",
    )
    principal = AuthenticatedPrincipal(
        api_key=None,
        user_id="reviewer-1",
        department_id="department-1",
        user_role=UserRole.FACULTY,
        auth_method="lti",
        lti_platform=platform,
        lti_course_id="course-1",
        lti_staff_role="Instructor",
    )
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.first.return_value = cloud

    assert authorize_review_scan_access(db, scan, principal) is cloud
    if platform == "canvas":
        assert authorize_scan_access(db, scan, principal) is cloud

    cloud.provider_parent_id = "other-course"
    with pytest.raises(HTTPException) as error:
        authorize_review_scan_access(db, scan, principal)
    assert error.value.status_code == 404
