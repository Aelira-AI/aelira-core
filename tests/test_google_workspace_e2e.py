"""Authenticated Google HTTP contracts, replacing obsolete gated journey mocks.

These are PostgreSQL route integration tests with controlled Google adapters,
not browser or live-provider end-to-end evidence.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.api import google_routes as routes
from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudOAuthCredentials,
    Scan,
    ScanType,
    ScanStatus,
)
from src.integrations.cloud_base import (
    CloudExportResult,
    CloudIntegrationError,
    CloudNotFoundError,
    CloudRateLimitError,
)
from tests import google_route_fixtures as fixtures

pytestmark = pytest.mark.integration
google_route = fixtures.google_route

OPERATIONS = [
    ("GET", "/google/drive/files", None),
    ("GET", "/google/drive/folders", None),
    ("GET", "/google/drive/files/provider-0", None),
    ("GET", "/google/drive/files/provider-0/download", None),
    ("POST", "/google/scan/file", {"file_id": "missing"}),
    ("POST", "/google/scan/folder", {"folder_id": "folder-1"}),
    ("POST", "/google/remediate", {"file_id": "missing"}),
    ("GET", "/google/jobs/missing", None),
    ("GET", "/google/jobs", None),
    ("POST", "/google/docs/export", {"file_id": "provider-0"}),
    ("POST", "/google/slides/export", {"file_id": "provider-0"}),
    ("POST", "/google/sheets/export", {"file_id": "provider-0"}),
    ("POST", "/google/upload", {"file_path": "synthetic-document.pdf"}),
]


def request(fixture, operation):
    method, path, payload = operation
    return fixture.client.request(method, path, json=payload)


def enqueue(fixture):
    return fixture.client.post("/google/scan/file", json={"file_id": fixture.file.id})


@pytest.mark.parametrize("operation", OPERATIONS)
def test_file_routes_require_authentication(google_route, operation):
    google_route.app.dependency_overrides.pop(routes.get_current_api_key)
    response = request(google_route, operation)
    assert response.status_code == 401
    assert isinstance(response.json()["detail"], str)
    google_route.factory.assert_not_called()


def test_browse_persists_actual_dto_and_pagination(google_route):
    fixtures.allow_listing(google_route, token="next-page")
    response = google_route.client.get(
        "/google/drive/files",
        params={"folder_id": "folder-1", "page_token": "first-page", "page_size": 7},
    )
    assert response.status_code == 200
    data = response.json()
    row = (
        google_route.db.query(CloudFile)
        .filter_by(
            department_id=google_route.key.department_id, provider_file_id="listed-doc"
        )
        .one()
    )
    assert data == {
        "files": [
            {
                "id": row.id,
                "provider_file_id": "listed-doc",
                "file_name": "Lecture notes",
                "file_type": "docx",
                "mime_type": "application/vnd.google-apps.document",
                "file_size_bytes": 321,
                "web_view_link": None,
                "last_scanned_at": None,
                "last_compliance_score": None,
                "needs_rescan": True,
            }
        ],
        "next_page_token": "next-page",
        "total_count": 1,
    }
    google_route.adapter.list_files.assert_awaited_once_with(
        folder_id="folder-1", page_token="first-page", page_size=7
    )
    google_route.factory.assert_called_once_with(
        access_token="fixture-access-0", credential_id=google_route.credential.id
    )
    google_route.db.expire_all()
    assert row.credential_id == google_route.credential.id
    assert row.provider_version == "v2" and row.provider_parent_id == "folder-1"
    assert google_route.credential.last_sync_at is not None
    assert google_route.other_credential.last_sync_at is None
    google_route.adapter.close.assert_awaited_once()


def test_browse_updates_version_without_duplicate_or_foreign_mutation(google_route):
    fixtures.allow_listing(google_route, [fixtures.file_info("provider-0")])
    response = google_route.client.get("/google/drive/files")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["files"]] == [google_route.file.id]
    google_route.db.expire_all()
    assert (
        google_route.file.provider_version == "v2"
        and google_route.file.file_name == "Lecture notes"
    )
    assert google_route.other_file.provider_version == "v1"
    assert (
        google_route.db.query(CloudFile)
        .filter_by(department_id=google_route.key.department_id)
        .count()
        == 1
    )


def test_folder_listing_exact_contract(google_route):
    fixtures.allow_folders(google_route)
    response = google_route.client.get(
        "/google/drive/folders", params={"parent_id": "root"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "folders": [
            {
                "id": "folder-1",
                "name": "Teaching",
                "parent_id": "root",
                "web_view_link": "https://drive.example/folder-1",
            }
        ]
    }
    google_route.adapter.list_folders.assert_awaited_once_with(parent_id="root")
    google_route.adapter.close.assert_awaited_once()


def test_metadata_uses_async_adapter_contract(google_route):
    google_route.adapter.get_file_info.side_effect = None
    google_route.adapter.get_file_info.return_value = fixtures.file_info("provider-0")
    response = google_route.client.get("/google/drive/files/provider-0")
    assert response.status_code == 200
    assert response.json() == {
        "id": "provider-0",
        "name": "Lecture notes",
        "mime_type": "application/vnd.google-apps.document",
        "size": 321,
        "created_time": None,
        "modified_time": None,
        "web_view_link": None,
    }
    google_route.adapter.get_file_info.assert_awaited_once_with("provider-0")
    google_route.adapter.close.assert_awaited_once()


def test_metadata_missing_provider_file_is_404(google_route):
    google_route.adapter.get_file_info.side_effect = None
    google_route.adapter.get_file_info.return_value = None
    response = google_route.client.get("/google/drive/files/provider-0")
    assert response.status_code == 404
    assert response.json() == {"detail": "File not found"}


def test_download_returns_bytes_and_cleans_its_owned_path(google_route):
    fixtures.allow_download(google_route)
    response = google_route.client.get("/google/drive/files/provider-0/download")
    assert response.status_code == 200
    assert response.content == b"PK\x00\xffdownload"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="download"'
    supplied_path = google_route.adapter.download_file.call_args.kwargs["local_path"]
    assert not Path(supplied_path).exists() and not Path(supplied_path).parent.exists()
    google_route.adapter.close.assert_awaited_once()


@pytest.mark.parametrize("failure", ["provider", "storage", "unexpected_path"])
def test_download_failure_is_bounded_and_cleans_only_owned_path(
    google_route, tmp_path, failure
):
    other = tmp_path / "unrelated.txt"
    other.write_text("untouched")

    async def download(file_id, local_path=None):
        assert local_path
        if failure == "provider":
            Path(local_path).write_bytes(b"partial")
            return CloudExportResult(success=False, error="private provider failure")
        if failure == "unexpected_path":
            return CloudExportResult(success=True, local_path=str(other))
        return CloudExportResult(success=True, local_path=local_path)

    google_route.adapter.download_file.side_effect = download
    response = google_route.client.get("/google/drive/files/provider-0/download")
    assert response.status_code == (502 if failure == "provider" else 500)
    assert response.json() == {
        "detail": (
            "Google Drive request failed."
            if failure == "provider"
            else "Unable to complete Google file operation. Please try again."
        )
    }
    assert other.read_text() == "untouched"
    assert not Path(
        google_route.adapter.download_file.call_args.kwargs["local_path"]
    ).parent.exists()


@pytest.mark.parametrize(
    "kind,method,extension",
    [
        ("docs", "export_to_docx", "docx"),
        ("slides", "export_to_pptx", "pptx"),
        ("sheets", "export_to_xlsx", "xlsx"),
    ],
)
def test_exports_return_office_bytes_with_real_constructor_contract(
    google_route, kind, method, extension
):
    response = google_route.client.post(
        f"/google/{kind}/export", json={"file_id": "provider-0"}
    )
    assert response.status_code == 200
    assert response.content == b"PK\x00\xffsynthetic-office-bytes"
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="export.{extension}"'
    )
    assert "openxmlformats-officedocument" in response.headers["content-type"]
    getattr(google_route.exports[kind], method).assert_called_once_with(
        file_id="provider-0"
    )
    credentials = google_route.export_factories[kind].call_args.kwargs["credentials"]
    assert credentials.token == "fixture-access-0"


@pytest.mark.parametrize("kind", ["docs", "slides", "sheets"])
def test_exports_refuse_server_output_paths(google_route, kind):
    response = google_route.client.post(
        f"/google/{kind}/export",
        json={"file_id": "provider-0", "output_path": "synthetic-output.docx"},
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": "output_path is not supported; download the response content instead."
    }
    google_route.export_factories[kind].assert_not_called()


@pytest.mark.parametrize(
    "kind,method",
    [
        ("docs", "export_to_docx"),
        ("slides", "export_to_pptx"),
        ("sheets", "export_to_xlsx"),
    ],
)
def test_export_provider_failures_do_not_claim_success(google_route, kind, method):
    getattr(google_route.exports[kind], method).side_effect = RuntimeError(
        "private export failure"
    )
    response = google_route.client.post(
        f"/google/{kind}/export", json={"file_id": "provider-0"}
    )
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to complete Google file operation. Please try again."
    }


def test_unsupported_upload_does_not_read_path_or_invent_success(
    google_route, tmp_path, monkeypatch
):
    document = tmp_path / "input.pdf"
    document.write_bytes(b"synthetic")
    original_exists = routes.os.path.exists
    probe = Mock()

    def checked_exists(path):
        if path == str(document):
            probe(path)
        return original_exists(path)

    monkeypatch.setattr(routes.os.path, "exists", checked_exists)
    response = google_route.client.post(
        "/google/upload", json={"file_path": str(document)}
    )
    assert response.status_code == 501
    assert response.json() == {
        "detail": "Local-path upload is not supported. Use the approved remediation artifact workflow."
    }
    google_route.adapter.upload_file.assert_not_called()
    probe.assert_not_called()
    assert google_route.db.query(CloudJobQueue).count() == 0


def test_file_scan_returns_stored_job_and_status_with_deduplication(google_route):
    first = enqueue(google_route)
    assert first.status_code == 200
    data = first.json()
    job = (
        google_route.db.query(CloudJobQueue)
        .filter_by(cloud_file_id=google_route.file.id)
        .one()
    )
    assert data == {
        "file_id": google_route.file.id,
        "job_id": job.id,
        "scan_id": None,
        "compliance_score": None,
        "issues_found": 0,
        "status": "queued",
        "message": f"Scan job {job.id} queued for processing",
    }
    assert job.payload == {
        "cloud_file_id": google_route.file.id,
        "credential_id": google_route.credential.id,
        "provider": "google",
        "provider_file_id": "provider-0",
    }
    assert (
        job.credential_id == google_route.credential.id
        and job.department_id == google_route.key.department_id
    )
    second = enqueue(google_route)
    assert second.status_code == 200 and second.json()["job_id"] == job.id
    status = google_route.client.get(f"/google/jobs/{job.id}")
    assert status.status_code == 200
    assert status.json() == {
        "job_id": job.id,
        "status": "pending",
        "progress": 0,
        "progress_message": None,
        "result_data": None,
        "error_message": None,
        "created_at": job.created_at.isoformat().replace("+00:00", "Z"),
        "completed_at": None,
    }
    listing = google_route.client.get(
        "/google/jobs", params={"status": "pending", "limit": 1}
    )
    assert listing.status_code == 200 and listing.json() == [status.json()]


@pytest.mark.parametrize("recursive", [True, False])
def test_folder_scan_honors_recursion_and_returns_stored_jobs(google_route, recursive):
    fixtures.allow_folder(
        google_route, [fixtures.file_info("doc-1"), fixtures.file_info("doc-2")]
    )
    response = google_route.client.post(
        "/google/scan/folder", json={"folder_id": "folder-1", "recursive": recursive}
    )
    assert response.status_code == 200
    data = response.json()
    assert (
        data["success"] is True
        and data["files_found"] == 2
        and data["jobs_created"] == 2
    )
    assert (
        data["folder_id"] == "folder-1"
        and data["message"] == "Created 2 scan jobs for folder"
    )
    jobs = (
        google_route.db.query(CloudJobQueue)
        .order_by(CloudJobQueue.provider_file_id)
        .all()
    )
    assert data["job_ids"] == [job.id for job in jobs]
    assert [job.provider_file_id for job in jobs] == ["doc-1", "doc-2"]
    assert all(
        job.department_id == google_route.key.department_id
        and job.credential_id == google_route.credential.id
        for job in jobs
    )
    google_route.adapter.list_all_files.assert_called_once_with(
        folder_id="folder-1", recursive=recursive
    )
    assert all(
        google_route.db.get(CloudFile, job.cloud_file_id).provider_version == "v2"
        for job in jobs
    )


@pytest.mark.parametrize(
    "kind", ["foreign", "missing", "other_provider", "other_credential"]
)
@pytest.mark.parametrize("path", ["/google/scan/file", "/google/remediate"])
def test_file_scope_refuses_before_enqueue(google_route, path, kind):
    identifier = google_route.file.id
    if kind == "foreign":
        identifier = google_route.other_file.id
    elif kind == "missing":
        identifier = str(uuid4())
    elif kind == "other_provider":
        google_route.file.provider = "microsoft"
    else:
        alternate = CloudOAuthCredentials(
            id=str(uuid4()),
            department_id=google_route.key.department_id,
            provider="google",
            is_active=False,
            access_token="synthetic",
            refresh_token="synthetic",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        google_route.db.add(alternate)
        google_route.db.flush()
        google_route.file.credential_id = alternate.id
    google_route.db.commit()
    response = google_route.client.post(path, json={"file_id": identifier})
    assert response.status_code == 404
    assert response.json() == {"detail": "File not found"}
    assert google_route.db.query(CloudJobQueue).count() == 0


def test_remediation_enqueues_matching_scan_identity(google_route):
    scan = Scan(
        id=str(uuid4()),
        department_id=google_route.key.department_id,
        user_id=google_route.key.user_id,
        scan_type=ScanType.PDF,
        status=ScanStatus.COMPLETED,
        file_name="example.pdf",
    )
    google_route.db.add(scan)
    google_route.db.flush()
    google_route.file.last_scan_id = scan.id
    google_route.db.commit()
    response = google_route.client.post(
        "/google/remediate",
        json={"file_id": google_route.file.id, "upload_as_new": True},
    )
    assert response.status_code == 200
    job = google_route.db.query(CloudJobQueue).one()
    assert response.json() == {
        "success": True,
        "job_id": job.id,
        "file_id": google_route.file.id,
        "status": "queued",
        "message": f"Remediation job {job.id} queued for processing",
    }
    assert job.payload["scan_id"] == scan.id and job.payload["upload_as_new"] is True
    assert job.job_type == "remediate" and job.priority == 3


def test_remediation_without_scan_is_harmless(google_route):
    response = google_route.client.post(
        "/google/remediate", json={"file_id": google_route.file.id}
    )
    assert response.status_code == 400
    assert response.json() == {
        "detail": "File has not been scanned yet. Scan first before remediation."
    }
    assert google_route.db.query(CloudJobQueue).count() == 0


@pytest.mark.parametrize("kind", ["foreign", "other_provider", "missing"])
def test_job_status_scope_is_exact(google_route, kind):
    job = CloudJobQueue(
        id=str(uuid4()),
        department_id=(
            google_route.other_credential.department_id
            if kind == "foreign"
            else google_route.key.department_id
        ),
        provider="microsoft" if kind == "other_provider" else "google",
        job_type="scan",
        payload={},
    )
    google_route.db.add(job)
    google_route.db.commit()
    response = google_route.client.get(
        f"/google/jobs/{str(uuid4()) if kind == 'missing' else job.id}"
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}
    listing = google_route.client.get("/google/jobs")
    assert listing.status_code == 200
    if kind != "missing":
        assert listing.json() == []


@pytest.mark.parametrize(
    "path,params",
    [
        ("/google/drive/files", {"page_size": 0}),
        ("/google/drive/files", {"page_size": 101}),
        ("/google/jobs", {"limit": 0}),
        ("/google/jobs", {"limit": 101}),
        ("/google/jobs", {"status": "unknown"}),
    ],
)
def test_invalid_query_inputs_are_rejected(google_route, path, params):
    response = google_route.client.get(path, params=params)
    assert response.status_code == 422
    google_route.factory.assert_not_called()


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/google/scan/file", {}),
        ("/google/scan/file", {"file_id": " "}),
        ("/google/scan/folder", {"folder_id": ""}),
        ("/google/remediate", {}),
        ("/google/docs/export", {"file_id": ""}),
        ("/google/slides/export", {}),
        ("/google/sheets/export", {}),
        ("/google/upload", {}),
    ],
)
def test_invalid_payload_is_rejected(google_route, path, payload):
    response = google_route.client.post(path, json=payload)
    assert response.status_code == 422
    assert google_route.db.query(CloudJobQueue).count() == 0


@pytest.mark.parametrize(
    "operation", OPERATIONS[:4] + [OPERATIONS[5]] + OPERATIONS[9:12]
)
def test_missing_or_inactive_connection_cannot_use_other_tenant(
    google_route, operation
):
    google_route.credential.is_active = False
    google_route.db.commit()
    response = request(google_route, operation)
    assert response.status_code == 404
    assert response.json() == {
        "detail": "Google Workspace not connected. Please connect first via /google/connect"
    }
    google_route.factory.assert_not_called()


@pytest.mark.parametrize(
    "failure,status,detail",
    [
        (CloudIntegrationError, 502, "Google Drive request failed."),
        (CloudNotFoundError, 404, "File not found"),
        (
            CloudRateLimitError,
            429,
            "Google Drive rate limit exceeded. Please try again.",
        ),
    ],
)
def test_provider_errors_are_bounded_after_success(
    google_route, failure, status, detail
):
    fixtures.allow_listing(google_route)
    assert google_route.client.get("/google/drive/files").status_code == 200
    google_route.adapter.list_files.side_effect = failure("private upstream detail")
    response = google_route.client.get("/google/drive/files")
    assert response.status_code == status
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize("operation", OPERATIONS[:9] + OPERATIONS[9:12])
def test_database_errors_are_bounded(google_route, monkeypatch, operation):
    monkeypatch.setattr(
        google_route.db,
        "query",
        Mock(side_effect=RuntimeError("private database detail")),
    )
    response = request(google_route, operation)
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to complete Google file operation. Please try again."
    }


@pytest.mark.parametrize(
    "path", ["/google/drive/files", "/google/scan/folder", "/google/scan/file"]
)
def test_commit_failure_rolls_back_files_jobs_and_sync(google_route, monkeypatch, path):
    fixtures.allow_listing(google_route)
    fixtures.allow_folder(google_route)
    monkeypatch.setattr(
        google_route.db,
        "commit",
        Mock(side_effect=RuntimeError("private commit detail")),
    )
    response = (
        google_route.client.get(path)
        if path.endswith("files")
        else google_route.client.post(
            path, json={"folder_id": "folder-1", "file_id": google_route.file.id}
        )
    )
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to complete Google file operation. Please try again."
    }
    google_route.db.expire_all()
    assert google_route.db.query(CloudJobQueue).count() == 0
    assert google_route.db.query(CloudFile).count() == 2
    assert google_route.credential.last_sync_at is None


def test_folder_second_enqueue_failure_rolls_back_first(google_route, monkeypatch):
    fixtures.allow_folder(
        google_route, [fixtures.file_info("doc-1"), fixtures.file_info("doc-2")]
    )
    original = routes.enqueue_cloud_job
    count = 0

    def enqueue_once(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("private enqueue detail")
        return original(*args, **kwargs)

    monkeypatch.setattr(routes, "enqueue_cloud_job", enqueue_once)
    response = google_route.client.post(
        "/google/scan/folder", json={"folder_id": "folder-1"}
    )
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to complete Google file operation. Please try again."
    }
    assert count == 2
    google_route.db.expire_all()
    assert google_route.db.query(CloudJobQueue).count() == 0
    assert google_route.db.query(CloudFile).count() == 2


@pytest.mark.parametrize(
    "kind,method",
    [
        ("docs", "export_to_docx"),
        ("slides", "export_to_pptx"),
        ("sheets", "export_to_xlsx"),
    ],
)
def test_real_export_service_reaches_controlled_transport(
    google_route, monkeypatch, kind, method
):
    from importlib import import_module

    module = import_module(f"src.integrations.google_workspace.google_{kind}")
    cls = getattr(module, f"Google{kind.title()}Service")
    calls = []

    class ExportRequest:
        def execute(self):
            return b"PK\x00\xffreal-service-transport"

    class Files:
        def export_media(self, *, fileId, mimeType):
            calls.append((fileId, mimeType))
            return ExportRequest()

    class Drive:
        def files(self):
            return Files()

    def build(service_name, version, *, credentials):
        assert (service_name, version) == ("drive", "v3")
        assert credentials.token == "fixture-access-0"
        return Drive()

    monkeypatch.setattr(module, "build", build)
    monkeypatch.setattr(routes, cls.__name__, cls)
    response = google_route.client.post(
        f"/google/{kind}/export", json={"file_id": "provider-0"}
    )
    assert response.status_code == 200
    assert response.content == b"PK\x00\xffreal-service-transport"
    assert calls == [("provider-0", response.headers["content-type"])]


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_expired_credential_refresh_is_tenant_bound(google_route, monkeypatch, outcome):
    from unittest.mock import AsyncMock

    google_route.credential.token_expires_at = datetime.now(timezone.utc) - timedelta(
        hours=1
    )
    google_route.db.commit()
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    refresh = AsyncMock(return_value=("refreshed-access", "refreshed-refresh", expiry))
    if outcome == "failure":
        refresh.side_effect = RuntimeError("private refresh detail")
    monkeypatch.setattr(google_route.manager, "refresh_google_token", refresh)
    monkeypatch.setattr(routes, "get_token_manager", lambda: google_route.manager)
    fixtures.allow_listing(google_route, [])
    response = google_route.client.get("/google/drive/files")
    refresh.assert_awaited_once_with("fixture-refresh-0")
    google_route.db.expire_all()
    assert (
        google_route.manager.decrypt_token(google_route.other_credential.access_token)
        == "fixture-access-1"
    )
    assert google_route.other_credential.is_active
    if outcome == "success":
        assert response.status_code == 200
        assert response.json() == {
            "files": [],
            "next_page_token": None,
            "total_count": 0,
        }
        assert (
            google_route.manager.decrypt_token(google_route.credential.access_token)
            == "refreshed-access"
        )
        assert (
            google_route.manager.decrypt_token(google_route.credential.refresh_token)
            == "refreshed-refresh"
        )
        assert google_route.credential.token_expires_at == expiry
    else:
        assert response.status_code == 401
        assert response.json() == {
            "detail": "Google connection expired. Please reconnect."
        }
        assert not google_route.credential.is_active
        assert google_route.credential.last_error == "Token refresh failed"
        google_route.factory.assert_not_called()


@pytest.mark.parametrize("route", ["/google/status", "/google/account"])
def test_connection_metadata_is_scoped_and_omits_tokens(google_route, route):
    response = google_route.client.get(route)
    assert response.status_code == 200
    data = response.json()
    if route.endswith("status"):
        assert data == {
            "id": google_route.credential.id,
            "provider": "google",
            "provider_email": "faculty0@example.edu",
            "provider_name": "Example Faculty",
            "is_active": True,
            "last_sync_at": None,
            "created_at": google_route.credential.created_at.isoformat().replace(
                "+00:00", "Z"
            ),
        }
    else:
        assert data == {
            "email": "faculty0@example.edu",
            "name": "Example Faculty",
            "connected_at": google_route.credential.created_at.isoformat(),
            "last_sync_at": None,
        }
    assert google_route.other_credential.id not in response.text
    assert google_route.credential.access_token not in response.text
    assert google_route.credential.refresh_token not in response.text


def test_folder_empty_result_creates_no_jobs(google_route):
    fixtures.allow_folder(google_route, [])
    response = google_route.client.post(
        "/google/scan/folder", json={"folder_id": "folder-1", "recursive": False}
    )
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "folder_id": "folder-1",
        "files_found": 0,
        "jobs_created": 0,
        "job_ids": [],
        "message": "Created 0 scan jobs for folder",
    }
    assert google_route.db.query(CloudJobQueue).count() == 0


@pytest.mark.parametrize("outcome", ["success", "provider_failure", "close_failure"])
def test_real_adapter_cleans_owned_directory_on_every_exit(
    google_route, monkeypatch, outcome
):
    import asyncio
    import httpx
    from src.integrations.google_workspace.google_drive import GoogleDriveIntegration

    calls = []

    def transport(request):
        assert request.method == "GET"
        assert request.url.path == "/drive/v3/files"
        calls.append(request)
        if outcome == "provider_failure":
            return httpx.Response(500, json={"error": "controlled provider failure"})
        return httpx.Response(200, json={"files": []})

    adapter = GoogleDriveIntegration(
        access_token="fixture-access-0", credential_id=google_route.credential.id
    )
    owned_directory = Path(adapter._temp_dir)
    marker = owned_directory / "owned-test-file"
    marker.write_bytes(b"owned")
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    adapter._client = client
    original_close = adapter.close
    if outcome == "close_failure":

        async def close_failure():
            await original_close()
            raise RuntimeError("controlled client close failure")

        monkeypatch.setattr(adapter, "close", close_failure)

    def factory(*, access_token, credential_id):
        assert access_token == "fixture-access-0"
        assert credential_id == google_route.credential.id
        return adapter

    monkeypatch.setattr(routes, "GoogleDriveIntegration", factory)
    try:
        response = google_route.client.get("/google/drive/files")
        assert (
            response.status_code
            == {"success": 200, "provider_failure": 502, "close_failure": 500}[outcome]
        )
        if outcome == "success":
            assert response.json() == {
                "files": [],
                "next_page_token": None,
                "total_count": 0,
            }
        assert len(calls) == 1
        assert client.is_closed
        assert not owned_directory.exists()
    finally:
        # This adapter instance and directory were allocated by this test only.
        asyncio.run(original_close())
        adapter.cleanup()
