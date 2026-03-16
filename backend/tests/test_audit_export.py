"""Tests for audit trail export functionality.

Tests cover:
- AuditReportGenerator: JSON, CSV, and PDF generation
- Export endpoint: format routing, auth, error handling
- Edge cases: empty data, missing fields, large datasets

TDD: These tests were written first, then the implementation.
"""
import csv
import io
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test fixtures — mock database objects
# ---------------------------------------------------------------------------


def _make_department(name: str = "Computer Science", institution: str = "MIT"):
    dept = MagicMock()
    dept.id = "dept-001"
    dept.name = name
    dept.institution = institution
    dept.contact_email = "cs@mit.edu"
    return dept


def _make_scan(
    scan_id: str = "scan-001",
    file_name: str = "syllabus.pdf",
    scan_type: str = "PDF",
    department_id: str = "dept-001",
):
    scan = MagicMock()
    scan.id = scan_id
    scan.file_name = file_name
    scan.scan_type = scan_type
    scan.department_id = department_id
    scan.status = "COMPLETED"
    scan.file_size_bytes = 102400
    scan.created_at = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    scan.completed_at = datetime(2026, 1, 15, 10, 5, 0, tzinfo=timezone.utc)
    return scan


def _make_fix(
    fix_id: str = "fix-001",
    category: str = "images",
    severity: str = "critical",
    description: str = "Missing alt text on figure 1",
    fix_method: str = "ai_vision",
    confidence: float = 0.85,
    review_status: str = "approved",
    wcag_criteria: str = "1.1.1",
    page_number: int = 3,
    review_notes: str = "Looks accurate",
):
    fix = MagicMock()
    fix.id = fix_id
    fix.category = category
    fix.severity = severity
    fix.description = description
    fix.original_content = ""
    fix.fixed_content = "A bar chart showing enrollment trends"
    fix.fix_method = fix_method
    fix.confidence = confidence
    fix.needs_review = confidence < 0.9
    fix.review_status = review_status
    fix.reviewed_by = "user-001"
    fix.reviewed_at = datetime(2026, 1, 16, 14, 0, 0, tzinfo=timezone.utc)
    fix.review_notes = review_notes
    fix.wcag_criteria = wcag_criteria
    fix.page_number = page_number
    fix.created_at = datetime(2026, 1, 15, 10, 2, 0, tzinfo=timezone.utc)
    return fix


def _make_audit_entry(
    entry_id: str = "audit-001",
    action: str = "fix_approve",
    user_name: str = "Jane Doe",
    details: dict = None,
):
    entry = MagicMock()
    entry.id = entry_id
    entry.action = action
    entry.user_name = user_name
    entry.details = details or {"notes": "LGTM"}
    entry.created_at = datetime(2026, 1, 16, 14, 0, 0, tzinfo=timezone.utc)
    return entry


def _make_matterhorn(
    result_id: str = "mh-001",
    checkpoint_id: str = "01-001",
    checkpoint_name: str = "Document is tagged",
    status: str = "pass",
    severity: str = None,
    page_number: int = None,
):
    mh = MagicMock()
    mh.id = result_id
    mh.checkpoint_id = checkpoint_id
    mh.checkpoint_name = checkpoint_name
    mh.status = status
    mh.severity = severity
    mh.details = None
    mh.page_number = page_number
    return mh


# ---------------------------------------------------------------------------
# Sample data sets
# ---------------------------------------------------------------------------


def _sample_fixes():
    return [
        _make_fix(
            fix_id="fix-001",
            category="images",
            severity="critical",
            description="Missing alt text on figure 1",
            fix_method="ai_vision",
            confidence=0.85,
            review_status="approved",
            wcag_criteria="1.1.1",
            page_number=3,
        ),
        _make_fix(
            fix_id="fix-002",
            category="structure",
            severity="minor",
            description="Heading hierarchy skip",
            fix_method="rule",
            confidence=0.98,
            review_status="auto_approved",
            wcag_criteria="1.3.1",
            page_number=1,
            review_notes=None,
        ),
        _make_fix(
            fix_id="fix-003",
            category="color",
            severity="serious",
            description="Insufficient color contrast",
            fix_method="heuristic",
            confidence=0.72,
            review_status="rejected",
            wcag_criteria="1.4.3",
            page_number=5,
            review_notes="False positive",
        ),
    ]


def _sample_audit_entries():
    return [
        _make_audit_entry(
            entry_id="audit-001",
            action="fix_approve",
            user_name="Jane Doe",
            details={"notes": "Alt text is accurate", "edited": False},
        ),
        _make_audit_entry(
            entry_id="audit-002",
            action="batch_approve",
            user_name="Admin",
            details={"count": 5, "min_confidence": 0.9},
        ),
        _make_audit_entry(
            entry_id="audit-003",
            action="fix_reject",
            user_name="Jane Doe",
            details={"notes": "False positive"},
        ),
    ]


def _sample_matterhorn():
    return [
        _make_matterhorn("mh-001", "01-001", "Document is tagged", "pass"),
        _make_matterhorn("mh-002", "01-002", "Content is tagged", "pass"),
        _make_matterhorn("mh-003", "06-001", "Image has alt text", "fail", "critical", 3),
        _make_matterhorn("mh-004", "07-001", "Table has headers", "pass"),
        _make_matterhorn("mh-005", "14-001", "Natural language specified", "warning", "minor"),
    ]


# ===========================================================================
# AuditReportGenerator — JSON
# ===========================================================================


class TestGenerateJSON:
    """Tests for AuditReportGenerator.generate_json()."""

    def test_json_contains_scan_metadata(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_json(
            scan=_make_scan(),
            fixes=_sample_fixes(),
            audit_entries=_sample_audit_entries(),
            matterhorn_results=_sample_matterhorn(),
            department=_make_department(),
        )
        assert result["scan"]["id"] == "scan-001"
        assert result["scan"]["file_name"] == "syllabus.pdf"
        assert result["scan"]["scan_type"] == "PDF"

    def test_json_contains_department_info(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_json(
            scan=_make_scan(),
            fixes=[],
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department("Physics", "Stanford"),
        )
        assert result["department"]["name"] == "Physics"
        assert result["department"]["institution"] == "Stanford"

    def test_json_contains_all_fixes(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        fixes = _sample_fixes()
        result = AuditReportGenerator.generate_json(
            scan=_make_scan(),
            fixes=fixes,
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department(),
        )
        assert len(result["fixes"]) == 3
        assert result["fixes"][0]["id"] == "fix-001"
        assert result["fixes"][0]["category"] == "images"
        assert result["fixes"][0]["severity"] == "critical"
        assert result["fixes"][0]["wcag_criteria"] == "1.1.1"

    def test_json_contains_audit_entries(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        entries = _sample_audit_entries()
        result = AuditReportGenerator.generate_json(
            scan=_make_scan(),
            fixes=[],
            audit_entries=entries,
            matterhorn_results=[],
            department=_make_department(),
        )
        assert len(result["audit_trail"]) == 3
        assert result["audit_trail"][0]["action"] == "fix_approve"
        assert result["audit_trail"][0]["user_name"] == "Jane Doe"

    def test_json_contains_matterhorn_results(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        mh = _sample_matterhorn()
        result = AuditReportGenerator.generate_json(
            scan=_make_scan(),
            fixes=[],
            audit_entries=[],
            matterhorn_results=mh,
            department=_make_department(),
        )
        assert len(result["matterhorn_results"]) == 5
        assert result["matterhorn_results"][0]["checkpoint_id"] == "01-001"
        assert result["matterhorn_results"][0]["status"] == "pass"

    def test_json_contains_summary_statistics(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_json(
            scan=_make_scan(),
            fixes=_sample_fixes(),
            audit_entries=_sample_audit_entries(),
            matterhorn_results=_sample_matterhorn(),
            department=_make_department(),
        )
        summary = result["summary"]
        assert summary["total_issues"] == 3
        assert summary["total_fixes"] == 3
        # 2 approved or auto_approved out of 3
        assert summary["approved_count"] == 2
        assert summary["rejected_count"] == 1
        assert summary["matterhorn_total"] == 5
        assert summary["matterhorn_passed"] == 3
        assert summary["matterhorn_failed"] == 1

    def test_json_compliance_level_compliant(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        all_pass = [
            _make_matterhorn("mh-1", "01-001", "Test", "pass"),
            _make_matterhorn("mh-2", "01-002", "Test", "pass"),
        ]
        result = AuditReportGenerator.generate_json(
            scan=_make_scan(),
            fixes=[],
            audit_entries=[],
            matterhorn_results=all_pass,
            department=_make_department(),
        )
        assert result["summary"]["compliance_level"] == "compliant"

    def test_json_compliance_level_not_validated(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_json(
            scan=_make_scan(),
            fixes=[],
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department(),
        )
        assert result["summary"]["compliance_level"] == "not_validated"

    def test_json_empty_data(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_json(
            scan=_make_scan(),
            fixes=[],
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department(),
        )
        assert result["fixes"] == []
        assert result["audit_trail"] == []
        assert result["matterhorn_results"] == []
        assert result["summary"]["total_issues"] == 0

    def test_json_is_serializable(self):
        """Ensure the result can be serialized to JSON string."""
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_json(
            scan=_make_scan(),
            fixes=_sample_fixes(),
            audit_entries=_sample_audit_entries(),
            matterhorn_results=_sample_matterhorn(),
            department=_make_department(),
        )
        # Should not raise — datetimes must be ISO formatted strings
        serialized = json.dumps(result)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["scan"]["id"] == "scan-001"


# ===========================================================================
# AuditReportGenerator — CSV
# ===========================================================================


class TestGenerateCSV:
    """Tests for AuditReportGenerator.generate_csv()."""

    def test_csv_returns_string(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_csv(
            scan=_make_scan(),
            fixes=_sample_fixes(),
            audit_entries=_sample_audit_entries(),
            matterhorn_results=_sample_matterhorn(),
            department=_make_department(),
        )
        assert isinstance(result, str)

    def test_csv_has_header_row(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_csv(
            scan=_make_scan(),
            fixes=_sample_fixes(),
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department(),
        )
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # First non-empty row should contain headers
        assert len(rows) > 0
        # Find the issues section header
        header_found = False
        for row in rows:
            if row and "Category" in row and "Severity" in row:
                header_found = True
                break
        assert header_found, "CSV should contain issue column headers"

    def test_csv_contains_fix_data(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_csv(
            scan=_make_scan(),
            fixes=_sample_fixes(),
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department(),
        )
        # Should contain fix data
        assert "images" in result
        assert "critical" in result
        assert "Missing alt text on figure 1" in result
        assert "1.1.1" in result

    def test_csv_contains_audit_entries(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_csv(
            scan=_make_scan(),
            fixes=[],
            audit_entries=_sample_audit_entries(),
            matterhorn_results=[],
            department=_make_department(),
        )
        assert "fix_approve" in result
        assert "Jane Doe" in result

    def test_csv_contains_matterhorn_data(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_csv(
            scan=_make_scan(),
            fixes=[],
            audit_entries=[],
            matterhorn_results=_sample_matterhorn(),
            department=_make_department(),
        )
        assert "01-001" in result
        assert "Document is tagged" in result

    def test_csv_parseable(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_csv(
            scan=_make_scan(),
            fixes=_sample_fixes(),
            audit_entries=_sample_audit_entries(),
            matterhorn_results=_sample_matterhorn(),
            department=_make_department(),
        )
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # Should have multiple sections: metadata, fixes, audit trail, matterhorn
        assert len(rows) > 10

    def test_csv_empty_data(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_csv(
            scan=_make_scan(),
            fixes=[],
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department(),
        )
        # Should still have headers and section markers
        assert isinstance(result, str)
        assert len(result) > 0


# ===========================================================================
# AuditReportGenerator — PDF
# ===========================================================================


class TestGeneratePDF:
    """Tests for AuditReportGenerator.generate_pdf()."""

    def test_pdf_returns_bytes(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_pdf(
            scan=_make_scan(),
            fixes=_sample_fixes(),
            audit_entries=_sample_audit_entries(),
            matterhorn_results=_sample_matterhorn(),
            department=_make_department(),
        )
        assert isinstance(result, bytes)

    def test_pdf_has_valid_header(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_pdf(
            scan=_make_scan(),
            fixes=_sample_fixes(),
            audit_entries=_sample_audit_entries(),
            matterhorn_results=_sample_matterhorn(),
            department=_make_department(),
        )
        # PDF files start with %PDF
        assert result[:4] == b"%PDF"

    def test_pdf_generates_with_custom_department(self):
        """PDF should generate successfully with custom department/institution."""
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_pdf(
            scan=_make_scan(),
            fixes=[],
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department("Chemistry", "Harvard"),
        )
        # PDF content is compressed, so we verify it generates successfully
        assert isinstance(result, bytes)
        assert result[:4] == b"%PDF"
        # Should be a non-trivial size (has content)
        assert len(result) > 500

    def test_pdf_generates_with_custom_filename(self):
        """PDF should generate successfully with a custom scan filename."""
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_pdf(
            scan=_make_scan(file_name="lecture-notes.pdf"),
            fixes=[],
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department(),
        )
        assert isinstance(result, bytes)
        assert result[:4] == b"%PDF"

    def test_pdf_with_empty_data(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_pdf(
            scan=_make_scan(),
            fixes=[],
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department(),
        )
        assert isinstance(result, bytes)
        assert result[:4] == b"%PDF"

    def test_pdf_nonzero_size(self):
        from src.education.reports.compliance_report import AuditReportGenerator

        result = AuditReportGenerator.generate_pdf(
            scan=_make_scan(),
            fixes=_sample_fixes(),
            audit_entries=_sample_audit_entries(),
            matterhorn_results=_sample_matterhorn(),
            department=_make_department(),
        )
        # A real PDF with content should be at least a few KB
        assert len(result) > 1000

    def test_pdf_graceful_with_missing_logo(self):
        """PDF should generate even if the logo file is missing."""
        from src.education.reports.compliance_report import AuditReportGenerator

        with patch("os.path.exists", return_value=False):
            result = AuditReportGenerator.generate_pdf(
                scan=_make_scan(),
                fixes=_sample_fixes(),
                audit_entries=_sample_audit_entries(),
                matterhorn_results=_sample_matterhorn(),
                department=_make_department(),
            )
        assert isinstance(result, bytes)
        assert result[:4] == b"%PDF"

    def test_pdf_with_none_review_notes(self):
        """Fixes with None review_notes should not cause errors."""
        from src.education.reports.compliance_report import AuditReportGenerator

        fix = _make_fix(review_notes=None)
        result = AuditReportGenerator.generate_pdf(
            scan=_make_scan(),
            fixes=[fix],
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department(),
        )
        assert result[:4] == b"%PDF"

    def test_pdf_with_none_page_number(self):
        """Fixes with None page_number should not cause errors."""
        from src.education.reports.compliance_report import AuditReportGenerator

        fix = _make_fix(page_number=None)
        result = AuditReportGenerator.generate_pdf(
            scan=_make_scan(),
            fixes=[fix],
            audit_entries=[],
            matterhorn_results=[],
            department=_make_department(),
        )
        assert result[:4] == b"%PDF"


# ===========================================================================
# Endpoint tests via FastAPI TestClient
# ===========================================================================


class TestExportEndpoint:
    """Tests for the GET /{scan_id}/audit/export endpoint.

    These tests verify routing, auth, content-type headers, and format
    validation. Generator methods are patched to isolate endpoint logic
    from the report generation (which is tested separately above).
    """

    def _setup_app(self):
        """Create a minimal FastAPI app with mocked dependencies."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.review_routes import router
        from src.db.database import get_db_dependency
        from src.auth.dependencies import get_required_api_key

        app = FastAPI()
        app.include_router(router, prefix="/api")

        # Mock auth
        def mock_auth():
            return (None, "user-001", "dept-001")

        # Mock DB session
        mock_db = MagicMock()

        def mock_get_db():
            return mock_db

        app.dependency_overrides[get_required_api_key] = mock_auth
        app.dependency_overrides[get_db_dependency] = mock_get_db

        client = TestClient(app)
        return client, mock_db

    def _setup_db_for_export(self, mock_db, scan=None, dept=None):
        """Configure mock DB to return expected objects for the export endpoint."""
        if scan is None:
            scan = _make_scan()
        if dept is None:
            dept = _make_department()

        # The endpoint calls db.query(Model).filter(...).first() multiple times.
        # We use side_effect to return different values for sequential .first() calls.
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            scan,  # Scan lookup
            dept,  # Department lookup
        ]
        # ScanFix query: .filter().order_by().all()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        # MatterhornResult + ReviewAuditLog queries: .filter().all()
        mock_db.query.return_value.filter.return_value.all.return_value = []

    @patch("src.api.review_routes.AuditReportGenerator")
    def test_json_export_returns_200(self, mock_gen_cls):
        client, mock_db = self._setup_app()
        self._setup_db_for_export(mock_db)

        mock_gen_cls.generate_json.return_value = {"scan": {"id": "scan-001"}}

        response = client.get("/api/reviews/scan-001/audit/export?format=json")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert response.json()["scan"]["id"] == "scan-001"

    @patch("src.api.review_routes.AuditReportGenerator")
    def test_csv_export_returns_200(self, mock_gen_cls):
        client, mock_db = self._setup_app()
        self._setup_db_for_export(mock_db)

        mock_gen_cls.generate_csv.return_value = "Department,Name\nCS,MIT\n"

        response = client.get("/api/reviews/scan-001/audit/export?format=csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers.get("content-disposition", "")

    @patch("src.api.review_routes.AuditReportGenerator")
    def test_pdf_export_returns_200(self, mock_gen_cls):
        client, mock_db = self._setup_app()
        self._setup_db_for_export(mock_db)

        mock_gen_cls.generate_pdf.return_value = b"%PDF-1.4 fake content"

        response = client.get("/api/reviews/scan-001/audit/export?format=pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment" in response.headers.get("content-disposition", "")

    def test_scan_not_found_returns_404(self):
        client, mock_db = self._setup_app()

        mock_db.query.return_value.filter.return_value.first.return_value = None

        response = client.get("/api/reviews/nonexistent/audit/export?format=json")
        assert response.status_code == 404

    def test_wrong_department_returns_404(self):
        client, mock_db = self._setup_app()

        scan = _make_scan(department_id="dept-other")
        mock_db.query.return_value.filter.return_value.first.return_value = scan

        response = client.get("/api/reviews/scan-001/audit/export?format=json")
        assert response.status_code == 404

    def test_invalid_format_returns_422(self):
        client, mock_db = self._setup_app()

        response = client.get("/api/reviews/scan-001/audit/export?format=xml")
        assert response.status_code == 422

    @patch("src.api.review_routes.AuditReportGenerator")
    def test_default_format_is_json(self, mock_gen_cls):
        client, mock_db = self._setup_app()
        self._setup_db_for_export(mock_db)

        mock_gen_cls.generate_json.return_value = {"ok": True}

        response = client.get("/api/reviews/scan-001/audit/export")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

    @patch("src.api.review_routes.AuditReportGenerator")
    def test_csv_filename_contains_scan_id(self, mock_gen_cls):
        client, mock_db = self._setup_app()
        self._setup_db_for_export(mock_db)

        mock_gen_cls.generate_csv.return_value = "data"

        response = client.get("/api/reviews/scan-001/audit/export?format=csv")
        disposition = response.headers.get("content-disposition", "")
        assert "scan-001" in disposition

    @patch("src.api.review_routes.AuditReportGenerator")
    def test_pdf_filename_contains_scan_id(self, mock_gen_cls):
        client, mock_db = self._setup_app()
        self._setup_db_for_export(mock_db)

        mock_gen_cls.generate_pdf.return_value = b"%PDF-1.4 fake"

        response = client.get("/api/reviews/scan-001/audit/export?format=pdf")
        disposition = response.headers.get("content-disposition", "")
        assert "scan-001" in disposition
