"""
Tests for Canvas content scan/review/approve/writeback API routes.

Tests cover:
- POST /canvas/content/scan — scan all content in a course
- POST /canvas/content/scan/{content_type} — scan one content type
- GET /canvas/content/courses/{course_id}/status — course content compliance
- GET /canvas/content/{cloud_file_id}/diff — original vs remediated diff
- POST /canvas/content/{cloud_file_id}/approve — approve a remediation
- POST /canvas/content/{cloud_file_id}/reject — reject a remediation
- POST /canvas/content/batch-approve — approve multiple items
- POST /canvas/content/{cloud_file_id}/writeback — execute write-back
- POST /canvas/content/batch-writeback — write back all approved items
- POST /canvas/content/{cloud_file_id}/rollback — rollback a write-back
- GET /canvas/content/{cloud_file_id}/audit — audit log
- Auth: all endpoints require API key and lms_integration feature
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import uuid

from src.api.main import app
from src.auth.dependencies import get_required_api_key
from src.db.database import get_db_dependency

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_api_key():
    """Mock API key for authentication."""
    api_key = MagicMock()
    api_key.user_id = "test-user-123"
    api_key.department_id = "test-dept-456"
    return api_key


@pytest.fixture
def auth_headers():
    """Headers with mock authentication."""
    return {"Authorization": "Bearer test-api-key-12345"}


@pytest.fixture
def mock_session():
    """Mock database session."""
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
        None
    )
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
        []
    )
    return session


@pytest.fixture(autouse=False)
def patch_require_feature():
    """Patch require_feature to be a no-op for content route tests."""
    with patch("src.api.canvas_content_routes.require_feature", new_callable=AsyncMock):
        yield


@pytest.fixture
def override_deps(mock_api_key, mock_session, patch_require_feature):
    """Override FastAPI dependencies for auth and DB."""
    app.dependency_overrides[get_required_api_key] = lambda: (
        mock_api_key,
        "test-user-123",
        "test-dept-456",
    )
    app.dependency_overrides[get_db_dependency] = lambda: mock_session
    yield
    app.dependency_overrides.pop(get_required_api_key, None)
    app.dependency_overrides.pop(get_db_dependency, None)


def _make_cloud_file(
    *,
    cloud_file_id=None,
    content_source="page",
    file_name="Test Page",
    content_body="<p>Hello</p>",
    remediated_body=None,
    writeback_status=None,
    department_id="test-dept-456",
    compliance_score=None,
    last_scan_id=None,
    has_remediated_version=None,
):
    """Create a mock CloudFile object.

    has_remediated_version defaults to mirroring remediated_body's
    presence (the historical HTML-only behavior) but can be overridden —
    file-type rows carry has_remediated_version=True with
    remediated_body=None (remediated as a file, not HTML; see
    POST /education/remediate/{scan_id}).
    """
    cf = MagicMock()
    cf.id = cloud_file_id or str(uuid.uuid4())
    cf.department_id = department_id
    cf.content_source = content_source
    cf.file_name = file_name
    cf.content_body = content_body
    cf.remediated_body = remediated_body
    cf.writeback_status = writeback_status
    cf.has_remediated_version = (
        (remediated_body is not None)
        if has_remediated_version is None
        else has_remediated_version
    )
    cf.last_compliance_score = compliance_score
    cf.last_scan_id = last_scan_id
    cf.last_scanned_at = datetime.now(timezone.utc) if last_scan_id else None
    cf.provider_parent_id = "course-101"
    cf.provider_file_id = "42"
    cf.content_updated_at = datetime.now(timezone.utc)
    cf.needs_rescan = False
    cf.provider = "CANVAS"
    cf.credential_id = "cred-1"
    return cf


def _make_writeback_log(cloud_file_id, *, rolled_back=False):
    """Create a mock ContentWritebackLog entry."""
    log = MagicMock()
    log.id = str(uuid.uuid4())
    log.cloud_file_id = cloud_file_id
    log.original_body = "<p>Original</p>"
    log.remediated_body = "<p>Fixed</p>"
    log.approved_by = "test-user-123"
    log.approved_at = datetime.now(timezone.utc)
    log.written_back_at = datetime.now(timezone.utc)
    log.canvas_revision = None
    log.rollback_status = "rolled_back" if rolled_back else None
    log.rolled_back_at = datetime.now(timezone.utc) if rolled_back else None
    log.created_at = datetime.now(timezone.utc)
    return log


# ---------------------------------------------------------------------------
# Auth tests — endpoints must require API key
# ---------------------------------------------------------------------------


class TestAuthRequired:
    """All endpoints require API key authentication."""

    def test_scan_requires_auth(self, client):
        """POST /canvas/content/scan must require auth."""
        response = client.post(
            "/canvas/content/scan",
            json={"course_id": "101"},
        )
        assert response.status_code in (401, 403)

    def test_course_status_requires_auth(self, client):
        """GET /canvas/content/courses/{id}/status must require auth."""
        response = client.get("/canvas/content/courses/101/status")
        assert response.status_code in (401, 403)

    def test_approve_requires_auth(self, client):
        """POST /canvas/content/{id}/approve must require auth."""
        response = client.post(f"/canvas/content/{uuid.uuid4()}/approve")
        assert response.status_code in (401, 403)

    def test_reject_requires_auth(self, client):
        """POST /canvas/content/{id}/reject must require auth."""
        response = client.post(f"/canvas/content/{uuid.uuid4()}/reject")
        assert response.status_code in (401, 403)

    def test_diff_requires_auth(self, client):
        """GET /canvas/content/{id}/diff must require auth."""
        response = client.get(f"/canvas/content/{uuid.uuid4()}/diff")
        assert response.status_code in (401, 403)

    def test_writeback_requires_auth(self, client):
        """POST /canvas/content/{id}/writeback must require auth."""
        response = client.post(f"/canvas/content/{uuid.uuid4()}/writeback")
        assert response.status_code in (401, 403)

    def test_rollback_requires_auth(self, client):
        """POST /canvas/content/{id}/rollback must require auth."""
        response = client.post(f"/canvas/content/{uuid.uuid4()}/rollback")
        assert response.status_code in (401, 403)

    def test_audit_requires_auth(self, client):
        """GET /canvas/content/{id}/audit must require auth."""
        response = client.get(f"/canvas/content/{uuid.uuid4()}/audit")
        assert response.status_code in (401, 403)

    def test_scan_by_type_requires_auth(self, client):
        """POST /canvas/content/scan/{content_type} must require auth."""
        response = client.post(
            "/canvas/content/scan/page",
            json={"course_id": "101"},
        )
        assert response.status_code in (401, 403)

    def test_batch_approve_requires_auth(self, client):
        """POST /canvas/content/batch-approve must require auth."""
        response = client.post(
            "/canvas/content/batch-approve",
            json={"cloud_file_ids": [str(uuid.uuid4())]},
        )
        assert response.status_code in (401, 403)

    def test_batch_writeback_requires_auth(self, client):
        """POST /canvas/content/batch-writeback must require auth."""
        response = client.post(
            "/canvas/content/batch-writeback",
            json={"course_id": "101"},
        )
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /canvas/content/scan — scan all content types
# ---------------------------------------------------------------------------


class TestScanCourseContent:
    """Tests for POST /canvas/content/scan."""

    @patch("src.api.canvas_content_routes._content_scan_task", new_callable=AsyncMock)
    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_scan_returns_summary(
        self, mock_get_client, mock_scan_task, client, mock_session, override_deps
    ):
        """Successful scan returns total_items, jobs_queued, skipped, by_type."""
        mock_credential = MagicMock()
        mock_credential.id = "cred-1"
        mock_canvas = AsyncMock()
        mock_get_client.return_value = (mock_credential, mock_canvas)

        scan_result = {
            "course_id": "101",
            "cloud_file_ids": ["cf-1", "cf-2", "cf-3"],
            "counts": {
                "page": 2,
                "assignment": 1,
                "announcement": 0,
                "quiz": 0,
                "discussion": 0,
                "skipped_empty": 3,
            },
        }

        with patch("src.api.canvas_content_routes.CanvasContentScanner") as MockScanner:
            scanner_instance = AsyncMock()
            scanner_instance.scan_course_content.return_value = scan_result
            MockScanner.return_value = scanner_instance

            response = client.post(
                "/canvas/content/scan",
                json={"course_id": "101"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_items"] == 3
        assert data["jobs_queued"] == 3
        assert data["skipped"] == 3
        assert data["by_type"]["page"] == 2
        assert data["by_type"]["assignment"] == 1

    @patch(
        "src.api.canvas_content_routes._canvas_scan_file_task", new_callable=AsyncMock
    )
    @patch("src.api.canvas_content_routes._content_scan_task", new_callable=AsyncMock)
    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_scan_fires_background_task_per_file_job(
        self,
        mock_get_client,
        mock_content_task,
        mock_file_task,
        client,
        mock_session,
        override_deps,
    ):
        """Each file_scan_job the scanner returns must get its own
        _canvas_scan_file_task background task fired. A CloudJobQueue row
        the scanner created but nobody fires a task for sits PENDING
        forever — nothing in this app polls the queue (JobProcessor is
        never started)."""
        mock_credential = MagicMock()
        mock_credential.id = "cred-1"
        mock_canvas = AsyncMock()
        mock_get_client.return_value = (mock_credential, mock_canvas)

        scan_result = {
            "course_id": "101",
            "cloud_file_ids": ["cf-1"],
            "file_scan_jobs": [
                {"job_id": "job-1", "cloud_file_id": "file-cf-1"},
                {"job_id": "job-2", "cloud_file_id": "file-cf-2"},
            ],
            "counts": {
                "page": 1,
                "assignment": 0,
                "announcement": 0,
                "quiz": 0,
                "discussion": 0,
                "file": 2,
                "skipped_empty": 0,
            },
        }

        with patch("src.api.canvas_content_routes.CanvasContentScanner") as MockScanner:
            scanner_instance = AsyncMock()
            scanner_instance.scan_course_content.return_value = scan_result
            MockScanner.return_value = scanner_instance

            response = client.post(
                "/canvas/content/scan",
                json={"course_id": "101"},
            )

        assert response.status_code == 200
        data = response.json()
        # 1 HTML item + 2 files — files must be counted, not just queued.
        assert data["total_items"] == 3
        assert data["jobs_queued"] == 3
        assert data["by_type"]["file"] == 2

        assert mock_file_task.await_count == 2
        called_kwargs = [call.kwargs for call in mock_file_task.await_args_list]
        assert {
            "job_id": "job-1",
            "cloud_file_id": "file-cf-1",
            "credential_id": "cred-1",
        } in called_kwargs
        assert {
            "job_id": "job-2",
            "cloud_file_id": "file-cf-2",
            "credential_id": "cred-1",
        } in called_kwargs


# ---------------------------------------------------------------------------
# POST /canvas/content/scan/{content_type} — scan one type
# ---------------------------------------------------------------------------


class TestScanSingleContentType:
    """Tests for POST /canvas/content/scan/{content_type}."""

    @patch("src.api.canvas_content_routes._content_scan_task", new_callable=AsyncMock)
    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_scan_single_type(
        self, mock_get_client, mock_scan_task, client, mock_session, override_deps
    ):
        """Scanning a single content type returns filtered results."""
        mock_credential = MagicMock()
        mock_credential.id = "cred-1"
        mock_canvas = AsyncMock()
        mock_get_client.return_value = (mock_credential, mock_canvas)

        scan_result = {
            "course_id": "101",
            "cloud_file_ids": ["cf-1"],
            "counts": {
                "page": 1,
                "assignment": 0,
                "announcement": 0,
                "quiz": 0,
                "discussion": 0,
                "skipped_empty": 0,
            },
        }

        with patch("src.api.canvas_content_routes.CanvasContentScanner") as MockScanner:
            scanner_instance = AsyncMock()
            scanner_instance.scan_course_content.return_value = scan_result
            MockScanner.return_value = scanner_instance

            response = client.post(
                "/canvas/content/scan/page",
                json={"course_id": "101"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["by_type"]["page"] == 1

    def test_scan_invalid_content_type(self, client, override_deps):
        """Invalid content type returns 422."""
        response = client.post(
            "/canvas/content/scan/invalid_type",
            json={"course_id": "101"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /canvas/content/courses/{course_id}/status
# ---------------------------------------------------------------------------


class TestCourseContentStatus:
    """Tests for GET /canvas/content/courses/{course_id}/status."""

    def test_status_returns_compliance_data(self, client, mock_session, override_deps):
        """Course status returns compliance summary from DB."""
        cf1 = _make_cloud_file(
            content_source="page",
            file_name="Page 1",
            compliance_score=95.0,
            last_scan_id="scan-1",
        )
        cf2 = _make_cloud_file(
            content_source="assignment",
            file_name="Assignment 1",
            compliance_score=80.0,
            last_scan_id="scan-2",
        )

        # Make the query chain return our cloud files
        mock_session.query.return_value.filter.return_value.all.return_value = [
            cf1,
            cf2,
        ]

        response = client.get("/canvas/content/courses/101/status")

        assert response.status_code == 200
        data = response.json()
        assert data["course_id"] == "101"
        assert isinstance(data["items"], list)

    def test_status_empty_course(self, client, mock_session, override_deps):
        """Course with no content items returns empty items list."""
        mock_session.query.return_value.filter.return_value.all.return_value = []

        response = client.get("/canvas/content/courses/999/status")

        assert response.status_code == 200
        data = response.json()
        assert data["course_id"] == "999"
        assert data["items"] == []


# ---------------------------------------------------------------------------
# GET /canvas/content/{cloud_file_id}/diff
# ---------------------------------------------------------------------------


class TestContentDiff:
    """Tests for GET /canvas/content/{cloud_file_id}/diff."""

    def test_diff_returns_html(self, client, mock_session, override_deps):
        """Diff endpoint returns original and remediated HTML."""
        cf = _make_cloud_file(
            content_body="<p>Original</p>",
            remediated_body="<p>Fixed</p>",
            last_scan_id="scan-1",
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        # Mock ScanResult for issues counts
        scan_result = MagicMock()
        scan_result.issues = [{"id": "issue-1"}]
        scan_result.compliance_score = 95.0
        # Make second filter().first() call return scan_result
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            cf,
            scan_result,
        ]

        response = client.get(f"/canvas/content/{cf.id}/diff")

        assert response.status_code == 200
        data = response.json()
        assert data["original_html"] == "<p>Original</p>"
        assert data["remediated_html"] == "<p>Fixed</p>"

    def test_diff_not_found(self, client, mock_session, override_deps):
        """Diff for missing cloud_file_id returns 404."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/canvas/content/{uuid.uuid4()}/diff")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /canvas/content/{cloud_file_id}/approve
# ---------------------------------------------------------------------------


class TestApproveContent:
    """Tests for POST /canvas/content/{cloud_file_id}/approve."""

    def test_approve_updates_status(self, client, mock_session, override_deps):
        """Approving an item sets writeback_status to 'approved'."""
        cf = _make_cloud_file(
            writeback_status="pending_review",
            remediated_body="<p>Fixed</p>",
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        response = client.post(f"/canvas/content/{cf.id}/approve")

        assert response.status_code == 200
        data = response.json()
        assert data["writeback_status"] == "approved"

    def test_approve_not_found(self, client, mock_session, override_deps):
        """Approve for missing cloud_file_id returns 404."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.post(f"/canvas/content/{uuid.uuid4()}/approve")
        assert response.status_code == 404

    def test_approve_without_remediation_returns_400(
        self, client, mock_session, override_deps
    ):
        """Approving a CloudFile with no remediated_body returns HTTP 400."""
        cf = _make_cloud_file(
            writeback_status="pending_review",
            remediated_body=None,  # no remediated content yet
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        response = client.post(f"/canvas/content/{cf.id}/approve")

        assert response.status_code == 400
        assert "remediated" in response.json()["detail"].lower()

    def test_approve_accepts_file_row_with_no_remediated_body(
        self, client, mock_session, override_deps
    ):
        """A file-type row remediated as a file (has_remediated_version=True,
        remediated_body=None — files never get an HTML body) must still be
        approvable. Checking remediated_body alone made every file
        unapprovable even after a successful remediation."""
        cf = _make_cloud_file(
            content_source="file",
            writeback_status=None,
            remediated_body=None,
            has_remediated_version=True,
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        response = client.post(f"/canvas/content/{cf.id}/approve")

        assert response.status_code == 200
        data = response.json()
        assert data["writeback_status"] == "approved"


# ---------------------------------------------------------------------------
# POST /canvas/content/{cloud_file_id}/reject
# ---------------------------------------------------------------------------


class TestRejectContent:
    """Tests for POST /canvas/content/{cloud_file_id}/reject."""

    def test_reject_updates_status(self, client, mock_session, override_deps):
        """Rejecting an item sets writeback_status to 'rejected'."""
        cf = _make_cloud_file(
            writeback_status="pending_review",
            remediated_body="<p>Fixed</p>",
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        response = client.post(f"/canvas/content/{cf.id}/reject")

        assert response.status_code == 200
        data = response.json()
        assert data["writeback_status"] == "rejected"

    def test_reject_not_found(self, client, mock_session, override_deps):
        """Reject for missing cloud_file_id returns 404."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.post(f"/canvas/content/{uuid.uuid4()}/reject")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /canvas/content/batch-approve
# ---------------------------------------------------------------------------


class TestBatchApprove:
    """Tests for POST /canvas/content/batch-approve."""

    def test_batch_approve(self, client, mock_session, override_deps):
        """Batch approve updates multiple items."""
        cf1 = _make_cloud_file(
            writeback_status="pending_review",
            remediated_body="<p>Fix 1</p>",
        )
        cf2 = _make_cloud_file(
            writeback_status="pending_review",
            remediated_body="<p>Fix 2</p>",
        )

        mock_session.query.return_value.filter.return_value.all.return_value = [
            cf1,
            cf2,
        ]

        response = client.post(
            "/canvas/content/batch-approve",
            json={"cloud_file_ids": [cf1.id, cf2.id]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved_count"] == 2

    def test_batch_approve_accepts_files_and_skips_unremediated(
        self, client, mock_session, override_deps
    ):
        """A body-only item and a has_remediated_version-only (file) item
        both approve; an item with neither is still skipped with the
        existing 'no remediated content' error."""
        html_item = _make_cloud_file(
            writeback_status="pending_review",
            remediated_body="<p>Fixed</p>",
        )
        file_item = _make_cloud_file(
            content_source="file",
            writeback_status=None,
            remediated_body=None,
            has_remediated_version=True,
        )
        unremediated_item = _make_cloud_file(
            writeback_status=None,
            remediated_body=None,
            has_remediated_version=False,
        )

        mock_session.query.return_value.filter.return_value.all.return_value = [
            html_item,
            file_item,
            unremediated_item,
        ]

        response = client.post(
            "/canvas/content/batch-approve",
            json={"cloud_file_ids": [html_item.id, file_item.id, unremediated_item.id]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["approved_count"] == 2
        assert data["skipped_count"] == 1
        assert any("no remediated content" in e for e in data["errors"])
        assert html_item.writeback_status == "approved"
        assert file_item.writeback_status == "approved"


# ---------------------------------------------------------------------------
# POST /canvas/content/batch-writeback
# ---------------------------------------------------------------------------


class TestBatchWriteback:
    """Tests for POST /canvas/content/batch-writeback."""

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_batch_writeback_calls_scanner_for_each_approved_file(
        self, mock_get_client, client, mock_session, override_deps
    ):
        """Batch writeback invokes scanner.write_back_content() for each approved item."""
        cf1 = _make_cloud_file(
            writeback_status="approved",
            remediated_body="<p>Fixed 1</p>",
        )
        cf2 = _make_cloud_file(
            writeback_status="approved",
            remediated_body="<p>Fixed 2</p>",
        )

        # Two sequential .query().filter().all() calls: approved HTML rows,
        # then approved file rows. No file rows in this scenario.
        mock_session.query.return_value.filter.return_value.all.side_effect = [
            [cf1, cf2],
            [],
        ]

        mock_credential = MagicMock()
        mock_credential.id = "cred-1"
        mock_canvas = AsyncMock()
        mock_get_client.return_value = (mock_credential, mock_canvas)

        with patch("src.api.canvas_content_routes.CanvasContentScanner") as MockScanner:
            scanner_instance = AsyncMock()
            scanner_instance.write_back_content.return_value = {
                "success": True,
                "stale": False,
            }
            MockScanner.return_value = scanner_instance

            response = client.post(
                "/canvas/content/batch-writeback",
                json={"course_id": "101"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["written_count"] == 2
        assert data["failed_count"] == 0
        assert data["skipped_count"] == 0
        assert scanner_instance.write_back_content.call_count == 2

    def test_batch_writeback_skips_approved_file_rows_honestly(
        self, client, mock_session, override_deps
    ):
        """Approved file-type rows have no working write-back-to-Canvas
        path yet — they must be reported as skipped with an explanatory
        error, not silently dropped from the response the way approve
        used to drop them."""
        file_item = _make_cloud_file(
            content_source="file",
            writeback_status="approved",
            remediated_body=None,
            has_remediated_version=True,
        )

        # No approved HTML rows; one approved file row.
        mock_session.query.return_value.filter.return_value.all.side_effect = [
            [],
            [file_item],
        ]

        response = client.post(
            "/canvas/content/batch-writeback",
            json={"course_id": "101"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["written_count"] == 0
        assert data["failed_count"] == 0
        assert data["skipped_count"] == 1
        assert any("wired up" in e for e in data["errors"])

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_batch_writeback_mixed_html_and_file_rows(
        self, mock_get_client, client, mock_session, override_deps
    ):
        """A course with both an approved HTML item and an approved file
        item writes back the HTML item and honestly skips the file item —
        neither is silently dropped."""
        html_item = _make_cloud_file(
            writeback_status="approved",
            remediated_body="<p>Fixed</p>",
        )
        file_item = _make_cloud_file(
            content_source="file",
            writeback_status="approved",
            remediated_body=None,
            has_remediated_version=True,
        )

        mock_session.query.return_value.filter.return_value.all.side_effect = [
            [html_item],
            [file_item],
        ]

        mock_credential = MagicMock()
        mock_credential.id = "cred-1"
        mock_canvas = AsyncMock()
        mock_get_client.return_value = (mock_credential, mock_canvas)

        with patch("src.api.canvas_content_routes.CanvasContentScanner") as MockScanner:
            scanner_instance = AsyncMock()
            scanner_instance.write_back_content.return_value = {
                "success": True,
                "stale": False,
            }
            MockScanner.return_value = scanner_instance

            response = client.post(
                "/canvas/content/batch-writeback",
                json={"course_id": "101"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["written_count"] == 1
        assert data["skipped_count"] == 1
        assert any("wired up" in e for e in data["errors"])

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_batch_writeback_no_approved_items_returns_zero_written(
        self, mock_get_client, client, mock_session, override_deps
    ):
        """Batch writeback with no approved items returns written_count=0."""
        mock_session.query.return_value.filter.return_value.all.return_value = []

        response = client.post(
            "/canvas/content/batch-writeback",
            json={"course_id": "999"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["written_count"] == 0
        mock_get_client.assert_not_called()


# ---------------------------------------------------------------------------
# POST /canvas/content/{cloud_file_id}/writeback
# ---------------------------------------------------------------------------


class TestWriteback:
    """Tests for POST /canvas/content/{cloud_file_id}/writeback."""

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_writeback_calls_scanner(
        self, mock_get_client, client, mock_session, override_deps
    ):
        """Writeback invokes scanner.write_back_content()."""
        cf = _make_cloud_file(
            writeback_status="approved",
            remediated_body="<p>Fixed</p>",
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        mock_credential = MagicMock()
        mock_credential.id = "cred-1"
        mock_canvas = AsyncMock()
        mock_get_client.return_value = (mock_credential, mock_canvas)

        with patch("src.api.canvas_content_routes.CanvasContentScanner") as MockScanner:
            scanner_instance = AsyncMock()
            scanner_instance.write_back_content.return_value = {
                "success": True,
                "stale": False,
            }
            MockScanner.return_value = scanner_instance

            response = client.post(f"/canvas/content/{cf.id}/writeback")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_writeback_not_found(self, client, mock_session, override_deps):
        """Writeback for missing cloud_file_id returns 404."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.post(f"/canvas/content/{uuid.uuid4()}/writeback")
        assert response.status_code == 404

    def test_writeback_file_row_returns_honest_skip_not_confusing_error(
        self, client, mock_session, override_deps
    ):
        """A file-type row has no working write-back-to-Canvas path yet —
        must return an honest, specific reason rather than the ambiguous
        'No remediated body' scanner.write_back_content() would give (the
        file WAS remediated, just not as HTML)."""
        cf = _make_cloud_file(
            content_source="file",
            writeback_status="approved",
            remediated_body=None,
            has_remediated_version=True,
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        response = client.post(f"/canvas/content/{cf.id}/writeback")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "wired up" in data["error"]


# ---------------------------------------------------------------------------
# POST /canvas/content/{cloud_file_id}/rollback
# ---------------------------------------------------------------------------


class TestRollback:
    """Tests for POST /canvas/content/{cloud_file_id}/rollback."""

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_rollback_calls_scanner(
        self, mock_get_client, client, mock_session, override_deps
    ):
        """Rollback invokes scanner.rollback_content()."""
        cf = _make_cloud_file(writeback_status="written_back")
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        mock_credential = MagicMock()
        mock_credential.id = "cred-1"
        mock_canvas = AsyncMock()
        mock_get_client.return_value = (mock_credential, mock_canvas)

        with patch("src.api.canvas_content_routes.CanvasContentScanner") as MockScanner:
            scanner_instance = AsyncMock()
            scanner_instance.rollback_content.return_value = {"success": True}
            MockScanner.return_value = scanner_instance

            response = client.post(f"/canvas/content/{cf.id}/rollback")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


# ---------------------------------------------------------------------------
# GET /canvas/content/{cloud_file_id}/audit
# ---------------------------------------------------------------------------


class TestAuditLog:
    """Tests for GET /canvas/content/{cloud_file_id}/audit."""

    def test_audit_returns_log_entries(self, client, mock_session, override_deps):
        """Audit endpoint returns writeback log entries."""
        cf = _make_cloud_file()
        log1 = _make_writeback_log(cf.id)
        log2 = _make_writeback_log(cf.id, rolled_back=True)

        # First call returns the cloud file, subsequent returns logs
        mock_session.query.return_value.filter.return_value.first.return_value = cf
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            log1,
            log2,
        ]

        response = client.get(f"/canvas/content/{cf.id}/audit")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["entries"], list)

    def test_audit_not_found(self, client, mock_session, override_deps):
        """Audit for missing cloud_file_id returns 404."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/canvas/content/{uuid.uuid4()}/audit")
        assert response.status_code == 404
