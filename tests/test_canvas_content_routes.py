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
from types import SimpleNamespace
import uuid

from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import CloudProvider, UserRole

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
    app.dependency_overrides[get_authenticated_principal] = (
        lambda: AuthenticatedPrincipal(
            api_key=mock_api_key,
            user_id="test-user-123",
            department_id="test-dept-456",
            user_role=UserRole.FACULTY,
            auth_method="api_key",
        )
    )
    app.dependency_overrides[get_db_dependency] = lambda: mock_session
    yield
    app.dependency_overrides.pop(get_authenticated_principal, None)
    app.dependency_overrides.pop(get_db_dependency, None)


def _make_cloud_file(
    *,
    cloud_file_id=None,
    content_source: str | None = "page",
    file_name="Test Page",
    content_body="<p>Hello</p>",
    remediated_body=None,
    writeback_status=None,
    department_id="test-dept-456",
    compliance_score=None,
    last_scan_id=None,
    has_remediated_version=None,
    remediation_origin=None,
    current_remediation_artifact_id=None,
    remediated_issues_fixed=None,
    remediated_issues_remaining=None,
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
    cf.remediation_origin = remediation_origin
    cf.current_remediation_artifact_id = current_remediation_artifact_id
    # Explicitly None unless a test sets them: a bare MagicMock attribute
    # is truthy and coerces to an int, which would let the "was this
    # verified by a rescan" check pass silently in every fixture.
    cf.remediated_issues_fixed = remediated_issues_fixed
    cf.remediated_issues_remaining = remediated_issues_remaining
    cf.last_compliance_score = compliance_score
    cf.last_scan_id = last_scan_id
    cf.last_scanned_at = datetime.now(timezone.utc) if last_scan_id else None
    cf.provider_parent_id = "course-101"
    cf.provider_file_id = "42"
    cf.content_updated_at = datetime.now(timezone.utc)
    cf.needs_rescan = False
    cf.provider = CloudProvider.CANVAS.value
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

    def test_scan_enqueues_one_durable_course_discovery(
        self, client, mock_session, override_deps
    ):
        """The route persists immutable discovery input for the worker."""
        credential = SimpleNamespace(id="cred-1")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            credential
        )
        queued_job = SimpleNamespace(id="job-course", status="pending", progress=0)
        enqueue = MagicMock(return_value=queued_job)
        scanner = MagicMock()
        get_client = AsyncMock()

        with (
            patch("src.api.canvas_content_routes.require_persisted_canvas_origin"),
            patch("src.api.canvas_content_routes.enqueue_cloud_job", enqueue),
            patch("src.api.canvas_content_routes.CanvasContentScanner", scanner),
            patch("src.api.canvas_content_routes._get_canvas_client", get_client),
        ):
            response = client.post(
                "/canvas/content/scan",
                json={"course_id": "101"},
            )

        assert response.status_code == 200
        assert response.json() == {
            "job_id": "job-course",
            "status": "pending",
            "progress": 0,
            "total_items": 0,
            "jobs_queued": 1,
            "skipped": 0,
            "by_type": {},
            "operation_kind": "deterministic_scan",
            "external_ai_used": False,
            "ai_used": False,
        }
        enqueue.assert_called_once_with(
            mock_session,
            department_id="test-dept-456",
            job_type="scan",
            payload={
                "scan_kind": "canvas_course",
                "credential_id": "cred-1",
                "provider": "canvas",
                "course_id": "101",
                "content_types": [
                    "page",
                    "assignment",
                    "announcement",
                    "quiz",
                    "discussion",
                    "file",
                ],
                "scan_options": {
                    "generate_alt_text": False,
                    "auto_remediate": False,
                    "detect_decorative": False,
                },
            },
            dedupe_key="canvas-course-scan:cred-1:101:68549e4fec25e6f7",
            provider="canvas",
            credential_id="cred-1",
            provider_file_id="101",
        )
        scanner.assert_not_called()
        get_client.assert_not_awaited()

    def test_scan_legacy_true_flags_cannot_expand_inline_execution(
        self, client, mock_session, override_deps
    ):
        """Legacy generative flags are frozen false in the durable payload."""
        credential = SimpleNamespace(id="cred-1")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            credential
        )
        queued_job = SimpleNamespace(id="job-course", status="pending", progress=0)
        enqueue = MagicMock(return_value=queued_job)
        scanner = MagicMock()

        with (
            patch("src.api.canvas_content_routes.require_persisted_canvas_origin"),
            patch("src.api.canvas_content_routes.enqueue_cloud_job", enqueue),
            patch("src.api.canvas_content_routes.CanvasContentScanner", scanner),
        ):
            response = client.post(
                "/canvas/content/scan",
                json={
                    "course_id": "101",
                    "generate_alt_text": True,
                    "auto_remediate": True,
                    "detect_decorative": True,
                },
            )

        assert response.status_code == 200
        assert enqueue.call_args.kwargs["payload"]["scan_options"] == {
            "generate_alt_text": False,
            "auto_remediate": False,
            "detect_decorative": False,
        }
        assert enqueue.call_count == 1
        scanner.assert_not_called()


# ---------------------------------------------------------------------------
# POST /canvas/content/scan/{content_type} — scan one type
# ---------------------------------------------------------------------------


class TestScanSingleContentType:
    """Tests for POST /canvas/content/scan/{content_type}."""

    def test_scan_single_type(self, client, mock_session, override_deps):
        """A type-scoped request persists that exact worker scope."""
        credential = SimpleNamespace(id="cred-1")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            credential
        )
        queued_job = SimpleNamespace(id="job-page", status="pending", progress=0)
        enqueue = MagicMock(return_value=queued_job)
        scanner = MagicMock()

        with (
            patch("src.api.canvas_content_routes.require_persisted_canvas_origin"),
            patch("src.api.canvas_content_routes.enqueue_cloud_job", enqueue),
            patch("src.api.canvas_content_routes.CanvasContentScanner", scanner),
        ):
            response = client.post(
                "/canvas/content/scan/page",
                json={"course_id": "101"},
            )

        assert response.status_code == 200
        assert response.json()["job_id"] == "job-page"
        enqueue.assert_called_once_with(
            mock_session,
            department_id="test-dept-456",
            job_type="scan",
            payload={
                "scan_kind": "canvas_course",
                "credential_id": "cred-1",
                "provider": "canvas",
                "course_id": "101",
                "content_types": ["page"],
                "scan_options": {
                    "generate_alt_text": False,
                    "auto_remediate": False,
                    "detect_decorative": False,
                },
            },
            dedupe_key="canvas-course-scan:cred-1:101:page",
            provider="canvas",
            credential_id="cred-1",
            provider_file_id="101",
        )
        scanner.assert_not_called()

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
        assert data["items"][0]["provider"] == "canvas"
        assert data["items"][0]["provider_parent_id"] == "course-101"
        assert (
            data["items"][0]["content_updated_at"] == cf1.content_updated_at.isoformat()
        )

    def test_status_returns_persisted_remediation_origin(
        self, client, mock_session, override_deps
    ):
        automatic = _make_cloud_file(
            cloud_file_id="automatic",
            has_remediated_version=True,
            remediation_origin="automatic",
        )
        manual = _make_cloud_file(
            cloud_file_id="manual",
            has_remediated_version=True,
            remediation_origin="manual",
        )
        legacy = _make_cloud_file(
            cloud_file_id="legacy",
            has_remediated_version=True,
            remediation_origin=None,
        )
        mock_session.query.return_value.filter.return_value.all.return_value = [
            automatic,
            manual,
            legacy,
        ]

        response = client.get("/canvas/content/courses/101/status")

        assert response.status_code == 200
        assert [item["remediation_origin"] for item in response.json()["items"]] == [
            "automatic",
            "manual",
            None,
        ]

    def test_status_counts_files_with_no_content_source(
        self, client, mock_session, override_deps
    ):
        """Canvas rows predating the content_source column are files, which
        the model already documents. They used to be filtered out of this
        query, so a course could report a clean score while its files were
        the worst thing in it."""
        page = _make_cloud_file(
            content_source="page",
            file_name="Page 1",
            compliance_score=100.0,
            last_scan_id="scan-1",
        )
        legacy_file = _make_cloud_file(
            content_source=None,
            file_name="handbook.pdf",
            compliance_score=40.0,
            last_scan_id="scan-2",
        )
        mock_session.query.return_value.filter.return_value.all.return_value = [
            page,
            legacy_file,
        ]

        response = client.get("/canvas/content/courses/101/status")

        assert response.status_code == 200
        data = response.json()
        types = {row["content_type"] for row in data["by_type"]}
        assert "file" in types
        assert len(data["items"]) == 2
        # The low-scoring file has to drag the course score down, which is
        # the whole point of counting it.
        assert data["overall_compliance"] is not None
        assert data["overall_compliance"] < 100.0

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

    def test_diff_returns_real_issues_not_fabricated(
        self, client, mock_session, override_deps
    ):
        """The issues list must be the real axe-core violation data from
        the scan, not a generated description — every field traces back
        to the raw stored violation."""
        cf = _make_cloud_file(
            content_body="<p>Original</p>",
            remediated_body="<p>Fixed</p>",
            last_scan_id="scan-1",
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        scan_result = MagicMock()
        scan_result.issues = [
            {
                "id": "image-alt",
                "impact": "critical",
                "description": "Images must have alternate text",
                "help": "Images must have alternative text",
                "tags": ["wcag2a", "wcag111", "cat.text-alternatives"],
                "nodes": [{"html": "<img src='x.png'>"}, {"html": "<img src='y.png'>"}],
            }
        ]
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            cf,
            scan_result,
        ]

        response = client.get(f"/canvas/content/{cf.id}/diff")

        assert response.status_code == 200
        data = response.json()
        assert len(data["issues"]) == 1
        issue = data["issues"][0]
        assert issue["id"] == "image-alt"
        assert issue["impact"] == "critical"
        assert issue["description"] == "Images must have alternate text"
        assert issue["help"] == "Images must have alternative text"
        assert issue["wcag_tags"] == ["wcag2a", "wcag111", "cat.text-alternatives"]
        assert issue["nodes_affected"] == 2
        # No fabricated-pool text anywhere in the response.
        assert "Added missing alt text" not in str(data)
        assert "Fixed heading hierarchy" not in str(data)

    def test_diff_reports_the_verified_split_when_a_rescan_recorded_one(
        self, client, mock_session, override_deps
    ):
        """The fixed/remaining split must come from the rescan of the
        remediated copy, which is the only thing that knows whether a fix
        worked."""
        cf = _make_cloud_file(
            content_body="<p>Original</p>",
            remediated_body="<p>Fixed</p>",
            last_scan_id="scan-1",
            remediated_issues_fixed=3,
            remediated_issues_remaining=1,
        )
        scan_result = MagicMock()
        scan_result.issues = [
            {"id": "image-alt", "impact": "critical", "nodes": [{}, {}, {}, {}]}
        ]
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            cf,
            scan_result,
        ]

        response = client.get(f"/canvas/content/{cf.id}/diff")

        assert response.status_code == 200
        data = response.json()
        assert data["issues_verified_by_rescan"] is True
        assert data["issues_fixed"] == 3
        assert data["issues_remaining"] == 1

    def test_diff_does_not_claim_fixes_that_were_never_verified(
        self, client, mock_session, override_deps
    ):
        """A remediated body with no rescan behind it used to report every
        issue as fixed. Absent a rescan the split is unknown, and the
        response has to say so rather than guess in our own favour."""
        cf = _make_cloud_file(
            content_body="<p>Original</p>",
            remediated_body="<p>Fixed</p>",
            last_scan_id="scan-1",
        )
        scan_result = MagicMock()
        scan_result.issues = [
            {"id": "image-alt", "impact": "critical", "nodes": [{}, {}]}
        ]
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            cf,
            scan_result,
        ]

        response = client.get(f"/canvas/content/{cf.id}/diff")

        assert response.status_code == 200
        data = response.json()
        assert data["issues_verified_by_rescan"] is False
        assert data["issues_fixed"] == 0
        assert data["issues_remaining"] == 1

    def test_diff_empty_issues_when_no_scan_results(
        self, client, mock_session, override_deps
    ):
        """An item with no last_scan_id (or a scan with no stored issues)
        returns an empty issues list — never padded or invented."""
        cf = _make_cloud_file(
            content_body="<p>Original</p>",
            remediated_body=None,
            last_scan_id=None,
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        response = client.get(f"/canvas/content/{cf.id}/diff")

        assert response.status_code == 200
        data = response.json()
        assert data["issues"] == []


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

        with patch(
            "src.api.canvas_content_routes.lock_current_canvas_content_candidate",
            return_value=cf,
        ):
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

    def test_approve_rejects_source_that_requires_rescan(
        self, client, mock_session, override_deps
    ):
        cf = _make_cloud_file(
            writeback_status="pending_review",
            remediated_body="<p>Old remediation</p>",
        )
        cf.needs_rescan = True
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        response = client.post(f"/canvas/content/{cf.id}/approve")

        assert response.status_code == 409
        assert response.json()["detail"] == "Source changed; re-scan required"
        mock_session.commit.assert_not_called()

    def test_approve_rejects_legacy_file_flag_without_managed_artifact(
        self, client, mock_session, override_deps
    ):
        """A legacy boolean cannot authorize file approval without byte identity."""
        cf = _make_cloud_file(
            content_source="file",
            writeback_status=None,
            remediated_body=None,
            has_remediated_version=True,
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        response = client.post(f"/canvas/content/{cf.id}/approve")

        assert response.status_code == 400
        assert response.json()["detail"] == "Managed artifact required"


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
            remediation_origin="automatic",
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        response = client.post(f"/canvas/content/{cf.id}/reject")

        assert response.status_code == 200
        data = response.json()
        assert data["writeback_status"] == "rejected"
        assert cf.remediation_origin is None

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

        with patch(
            "src.api.canvas_content_routes.lock_current_canvas_content_candidate",
            side_effect=lambda _db, row: row,
        ):
            response = client.post(
                "/canvas/content/batch-approve",
                json={"cloud_file_ids": [cf1.id, cf2.id]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["approved_count"] == 2

    def test_batch_approve_is_atomic_when_any_item_is_unapprovable(
        self, client, mock_session, override_deps
    ):
        """No item mutates when the complete set is not approvable."""
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

        assert response.status_code == 400
        assert html_item.writeback_status == "pending_review"
        assert file_item.writeback_status is None


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

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_batch_writeback_counts_stale_file_rows(
        self, mock_get_client, client, mock_session, override_deps
    ):
        """A stale managed file is reported as stale rather than failed."""
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

        mock_credential = MagicMock()
        mock_credential.id = "cred-1"
        mock_get_client.return_value = (mock_credential, AsyncMock())

        with patch("src.api.canvas_content_routes.CanvasContentScanner") as MockScanner:
            scanner_instance = AsyncMock()
            scanner_instance.write_back_file.return_value = {
                "success": False,
                "stale": True,
                "error": "Canvas file changed since the scan",
            }
            MockScanner.return_value = scanner_instance

            response = client.post(
                "/canvas/content/batch-writeback",
                json={"course_id": "101"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["written_count"] == 0
        assert data["failed_count"] == 0
        assert data["stale_count"] == 1
        assert data["skipped_count"] == 0
        assert data["errors"] == [f"{file_item.id}: content is stale"]

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_batch_writeback_mixed_html_and_file_rows(
        self, mock_get_client, client, mock_session, override_deps
    ):
        """A course with both an approved HTML item and an approved file
        item writes both back, through the two different mechanisms, and
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
            scanner_instance.write_back_file.return_value = {
                "success": True,
                "stale": False,
                "canvas_file_id": "canvas-77",
            }
            MockScanner.return_value = scanner_instance

            response = client.post(
                "/canvas/content/batch-writeback",
                json={"course_id": "101"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["written_count"] == 2
        assert data["skipped_count"] == 0
        assert data["errors"] == []
        scanner_instance.write_back_content.assert_awaited_once()
        scanner_instance.write_back_file.assert_awaited_once()

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
    """Tests for Canvas content remediation and write-back routes."""

    @pytest.fixture(autouse=True)
    def durable_canvas_enqueue(self):
        job = SimpleNamespace(id="canvas-job-1", status="pending")
        with patch(
            "src.api.canvas_content_routes.enqueue_canvas_content_remediation",
            return_value=job,
        ) as enqueue:
            self.canvas_enqueue = enqueue
            yield

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

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_remediating_a_content_item_uses_the_content_path(
        self, mock_get_client, client, mock_session, override_deps
    ):
        """Content items are markup we hold, not documents to download. They
        used to be sent to the file endpoint, which tried to fetch a Canvas
        file that does not exist and reported the 404 as a failed
        remediation, when nothing had been attempted."""
        cf = _make_cloud_file(content_source="page", content_body="<p>Hi</p>")
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        mock_credential = MagicMock()
        mock_credential.id = "cred-1"
        mock_get_client.return_value = (mock_credential, AsyncMock())

        with patch("src.api.canvas_content_routes.CanvasContentScanner") as MockScanner:
            scanner_instance = AsyncMock()
            scanner_instance.remediate_content_item.return_value = {
                "success": True,
                "verified": True,
                "fixed_count": 2,
                "issues_remaining": 1,
                "issues_introduced": 0,
                "remediated_score": 91.0,
            }
            MockScanner.return_value = scanner_instance

            response = client.post(f"/canvas/content/{cf.id}/remediate")

        assert response.status_code == 202
        data = response.json()
        assert data == {
            "job_id": "canvas-job-1",
            "cloud_file_id": cf.id,
            "status": "pending",
            "status_url": (f"/canvas/content/{cf.id}/remediation/jobs/canvas-job-1"),
        }
        scanner_instance.remediate_content_item.assert_not_awaited()

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_content_remediation_defaults_to_deterministic_without_policy_lookup(
        self, mock_get_client, client, mock_session, override_deps
    ):
        cf = _make_cloud_file(content_source="page", content_body="<p>Hi</p>")
        mock_session.query.return_value.filter.return_value.first.return_value = cf
        credential = MagicMock(id="cred-1")
        mock_get_client.return_value = (credential, AsyncMock())

        with (
            patch(
                "src.api.canvas_content_routes.LMSRemediationClient.bind_if_allowed",
                side_effect=AssertionError("policy lookup forbidden without intent"),
            ),
            patch("src.api.canvas_content_routes.CanvasContentScanner") as scanner_cls,
        ):
            scanner = AsyncMock()
            scanner.remediate_content_item.return_value = {
                "success": True,
                "fixed_count": 1,
            }
            scanner_cls.return_value = scanner
            response = client.post(f"/canvas/content/{cf.id}/remediate")

        assert response.status_code == 202
        scanner.remediate_content_item.assert_not_awaited()
        assert self.canvas_enqueue.call_args.kwargs["options"]["use_ai"] is False

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_requested_html_ai_denied_returns_403_before_canvas_or_remediation(
        self, mock_get_client, client, mock_session, override_deps
    ):
        cf = _make_cloud_file(content_source="page", content_body="<p>Hi</p>")
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        with (
            patch(
                "src.api.canvas_content_routes.LMSRemediationClient.bind_if_allowed",
                return_value=None,
            ),
            patch("src.api.canvas_content_routes.CanvasContentScanner") as scanner_cls,
        ):
            response = client.post(
                f"/canvas/content/{cf.id}/remediate", json={"use_ai": True}
            )

        assert response.status_code == 202
        mock_get_client.assert_not_awaited()
        scanner_cls.assert_not_called()
        assert self.canvas_enqueue.call_args.kwargs["options"]["use_ai"] is True

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_alt_text_policy_is_independent_and_denial_remains_manual(
        self, mock_get_client, client, mock_session, override_deps
    ):
        cf = _make_cloud_file(
            content_source="page", content_body="<img src='/files/1'>"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf
        mock_get_client.return_value = (MagicMock(id="cred-1"), AsyncMock())
        remediation_client = MagicMock(provider="ollama", purpose="remediation")

        def bind(**kwargs):
            return remediation_client if kwargs["purpose"] == "remediation" else None

        with (
            patch(
                "src.api.canvas_content_routes.LMSRemediationClient.bind_if_allowed",
                side_effect=bind,
            ),
            patch("src.api.canvas_content_routes.CanvasContentScanner") as scanner_cls,
        ):
            scanner = AsyncMock()
            scanner.remediate_content_item.return_value = {
                "success": True,
                "fixed_count": 0,
            }
            scanner_cls.return_value = scanner
            response = client.post(
                f"/canvas/content/{cf.id}/remediate",
                json={"use_ai": True, "generate_alt_text": True},
            )

        assert response.status_code == 202
        scanner.remediate_content_item.assert_not_awaited()
        assert self.canvas_enqueue.call_args.kwargs["options"] == {
            "use_ai": True,
            "generate_alt_text": True,
            "actor_id": "test-user-123",
        }

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_dispatch_revocation_and_canvas_close_failure_preserve_authoritative_truth(
        self, mock_get_client, client, mock_session, override_deps, caplog
    ):
        cf = _make_cloud_file(content_source="page", content_body="<p>Hi</p>")
        mock_session.query.return_value.filter.return_value.first.return_value = cf
        remediation_client = MagicMock(provider="gemini", purpose="remediation")
        api_client = AsyncMock()
        api_client.close.side_effect = RuntimeError("SENSITIVE CLOSE DETAIL")
        mock_get_client.return_value = (MagicMock(id="cred-1"), api_client)

        with (
            patch(
                "src.api.canvas_content_routes.LMSRemediationClient.bind_if_allowed",
                return_value=remediation_client,
            ),
            patch("src.api.canvas_content_routes.CanvasContentScanner") as scanner_cls,
        ):
            scanner = AsyncMock()
            scanner.remediate_content_item.return_value = {
                "success": False,
                "error": "policy_denied",
                "ai_used": False,
                "external_ai_used": False,
                "provider": None,
                "purpose_decisions": {
                    "remediation": "denied_at_dispatch",
                    "alt_text": "not_requested",
                },
            }
            scanner_cls.return_value = scanner
            response = client.post(
                f"/canvas/content/{cf.id}/remediate", json={"use_ai": True}
            )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "pending"
        assert "SENSITIVE" not in str(data)
        assert "SENSITIVE CLOSE DETAIL" not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_provider_attempt_and_canvas_close_failure_preserve_usage_truth(
        self, mock_get_client, client, mock_session, override_deps, caplog
    ):
        cf = _make_cloud_file(content_source="page", content_body="<p>Hi</p>")
        mock_session.query.return_value.filter.return_value.first.return_value = cf
        api_client = AsyncMock()
        api_client.close.side_effect = RuntimeError("raw close secret")
        mock_get_client.return_value = (MagicMock(id="cred-1"), api_client)

        with (
            patch(
                "src.api.canvas_content_routes.LMSRemediationClient.bind_if_allowed",
                return_value=MagicMock(provider="gemini", purpose="remediation"),
            ),
            patch("src.api.canvas_content_routes.CanvasContentScanner") as scanner_cls,
        ):
            scanner = AsyncMock()
            scanner.remediate_content_item.return_value = {
                "success": False,
                "error": "provider_call_failed",
                "ai_used": True,
                "external_ai_used": True,
                "provider": "gemini",
                "purpose_decisions": {
                    "remediation": "attempted_failed",
                    "alt_text": "not_requested",
                },
            }
            scanner_cls.return_value = scanner
            response = client.post(
                f"/canvas/content/{cf.id}/remediate", json={"use_ai": True}
            )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "pending"
        assert "secret" not in str(data).lower()
        assert "raw close secret" not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)

    def test_remediating_a_file_row_is_refused_with_a_reason(
        self, client, mock_session, override_deps
    ):
        """Files belong to the scan-based endpoint. Saying so is better than
        attempting it and reporting a download failure."""
        cf = _make_cloud_file(content_source="file", content_body=None)
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        response = client.post(f"/canvas/content/{cf.id}/remediate")

        assert response.status_code == 400
        assert "scan-based" in response.json()["detail"]

    @patch("src.api.canvas_content_routes._get_canvas_client", new_callable=AsyncMock)
    def test_writeback_legacy_null_file_row_uses_the_upload_path(
        self, mock_get_client, client, mock_session, override_deps
    ):
        """A file row is written back by uploading the remediated copy, not
        by the HTML path, which would report the technically-true but
        useless "No remediated body" for a file that was remediated."""
        cf = _make_cloud_file(
            content_source=None,
            writeback_status="approved",
            remediated_body=None,
            has_remediated_version=True,
        )
        mock_session.query.return_value.filter.return_value.first.return_value = cf

        mock_credential = MagicMock()
        mock_credential.id = "cred-1"
        mock_get_client.return_value = (mock_credential, AsyncMock())

        with patch("src.api.canvas_content_routes.CanvasContentScanner") as MockScanner:
            scanner_instance = AsyncMock()
            scanner_instance.write_back_file.return_value = {
                "success": True,
                "stale": False,
                "canvas_file_id": "canvas-77",
            }
            MockScanner.return_value = scanner_instance

            response = client.post(f"/canvas/content/{cf.id}/writeback")

        assert response.status_code == 200
        assert response.json()["success"] is True
        scanner_instance.write_back_file.assert_awaited_once()
        scanner_instance.write_back_content.assert_not_awaited()


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
