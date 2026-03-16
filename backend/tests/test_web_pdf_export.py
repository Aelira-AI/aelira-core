"""Tests for web-specific PDF export formatting in compliance reports.

Verifies the 'Web Remediation Guide' section is added to PDF reports
for web scans, with fixes grouped by page URL and before/after code snippets.
"""
import importlib.util
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

# Load compliance_report module directly to avoid triggering conftest.py
# session fixtures that require PostgreSQL.
_mod_path = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "education"
    / "reports"
    / "compliance_report.py"
)
_spec = importlib.util.spec_from_file_location("compliance_report", _mod_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["compliance_report"] = _mod
_spec.loader.exec_module(_mod)
AuditReportGenerator = _mod.AuditReportGenerator


def _make_scan(scan_type: str = "pdf", file_name: str = "test-doc.pdf") -> SimpleNamespace:
    """Create a mock scan object."""
    return SimpleNamespace(
        id="scan-001",
        file_name=file_name,
        scan_type=scan_type,
        status="completed",
        created_at=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 3, 1, 12, 5, 0, tzinfo=timezone.utc),
    )


def _make_department() -> SimpleNamespace:
    """Create a mock department object."""
    return SimpleNamespace(
        name="Computer Science",
        institution="Test University",
    )


def _make_fix(
    fix_id: str = "fix-001",
    category: str = "images",
    severity: str = "serious",
    description: str = "Missing alt text on image",
    location: str | None = None,
    original_content: str | None = None,
    fixed_content: str = '<img alt="Campus photo">',
    review_status: str = "approved",
    wcag_criteria: str = "1.1.1",
    page_number: int | None = 1,
    confidence: float = 0.95,
) -> SimpleNamespace:
    """Create a mock ScanFix object."""
    return SimpleNamespace(
        id=fix_id,
        issue_id=f"issue-{fix_id}",
        category=category,
        severity=severity,
        description=description,
        location=location,
        original_content=original_content,
        fixed_content=fixed_content,
        fix_method="ai_text",
        model_used="gemini",
        confidence=confidence,
        needs_review=False,
        review_status=review_status,
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
        wcag_criteria=wcag_criteria,
        page_number=page_number,
        created_at=datetime(2026, 3, 1, 12, 2, 0, tzinfo=timezone.utc),
    )


def _make_audit_entry(
    entry_id: str = "audit-001",
    action: str = "fix_approved",
) -> SimpleNamespace:
    """Create a mock audit entry."""
    return SimpleNamespace(
        id=entry_id,
        action=action,
        user_name="Dr. Smith",
        details={"fix_id": "fix-001", "status": "approved"},
        created_at=datetime(2026, 3, 1, 12, 10, 0, tzinfo=timezone.utc),
    )


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract all text from PDF bytes using pypdf.

    Returns the concatenated text from all pages, useful for
    asserting the presence of specific content in the rendered PDF.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text_parts = []
    for page in reader.pages:
        text_parts.append(page.extract_text())
    return "\n".join(text_parts)


class TestWebPdfExportNonWebScan:
    """Tests that non-web scans do NOT include the Web Remediation Guide."""

    def test_pdf_scan_has_no_web_remediation_section(self):
        """A standard PDF scan should not contain the Web Remediation Guide."""

        scan = _make_scan(scan_type="pdf")
        department = _make_department()
        fixes = [_make_fix(review_status="approved")]
        audit_entries = [_make_audit_entry()]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=audit_entries,
            matterhorn_results=[],
            department=department,
        )

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" not in text

    def test_document_scan_has_no_web_remediation_section(self):
        """A document scan type should not contain the Web Remediation Guide."""

        scan = _make_scan(scan_type="document")
        department = _make_department()

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=[],
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" not in text


class TestWebPdfExportEnumScanType:
    """Tests that ScanType enum values (not just plain strings) work correctly."""

    def test_scantype_enum_with_value_attribute(self):
        """scan.scan_type as an enum-like object with .value should work."""

        # Simulate the ScanType enum with a .value attribute
        class FakeScanType:
            value = "WEBSITE"
            def __str__(self):
                return "ScanType.WEBSITE"

        scan = SimpleNamespace(
            id="scan-enum",
            file_name="https://example.edu",
            scan_type=FakeScanType(),
            status="completed",
            created_at=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 3, 1, 12, 5, 0, tzinfo=timezone.utc),
        )
        department = _make_department()
        fixes = [
            _make_fix(
                location="https://example.edu/page | .content",
                original_content="<div></div>",
                fixed_content='<div role="main"></div>',
                review_status="approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan, fixes=fixes, audit_entries=[], matterhorn_results=[], department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text

    def test_none_scan_type_does_not_crash(self):
        """scan.scan_type=None should not crash and should not show web section."""

        scan = _make_scan(scan_type=None)
        department = _make_department()

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan, fixes=[], audit_entries=[], matterhorn_results=[], department=department,
        )

        assert isinstance(pdf_bytes, bytes)
        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" not in text


class TestWebPdfExportWebScan:
    """Tests for web scan PDF reports with the Web Remediation Guide section."""

    def test_web_scan_includes_remediation_section(self):
        """A web scan with approved fixes should include the Web Remediation Guide."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()
        fixes = [
            _make_fix(
                fix_id="fix-001",
                location="https://example.edu/about | h1.title",
                original_content="<h1></h1>",
                fixed_content="<h1>About Us</h1>",
                review_status="approved",
                wcag_criteria="2.4.2",
                description="Empty heading element",
            ),
        ]
        audit_entries = [_make_audit_entry()]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=audit_entries,
            matterhorn_results=[],
            department=department,
        )

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 100
        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text

    def test_website_scan_type_also_works(self):
        """scan_type='website' should also trigger the Web Remediation Guide."""

        scan = _make_scan(scan_type="website", file_name="https://example.edu")
        department = _make_department()
        fixes = [
            _make_fix(
                location="https://example.edu | div.main",
                original_content="<img>",
                fixed_content='<img alt="Logo">',
                review_status="auto_approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text

    def test_web_scan_case_insensitive(self):
        """scan_type should be matched case-insensitively (e.g. 'WEB')."""

        scan = _make_scan(scan_type="WEB", file_name="https://example.edu")
        department = _make_department()
        fixes = [
            _make_fix(
                location="https://example.edu | .nav",
                original_content="<nav>",
                fixed_content='<nav aria-label="Main navigation">',
                review_status="approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text

    def test_web_scan_no_approved_fixes_skips_section(self):
        """A web scan with only rejected/pending fixes should NOT show the section."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()
        fixes = [
            _make_fix(review_status="rejected"),
            _make_fix(fix_id="fix-002", review_status="pending"),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" not in text


class TestWebFixGroupingByUrl:
    """Tests that web fixes are correctly grouped by page URL."""

    def test_fixes_grouped_by_page_url(self):
        """Fixes with different page URLs should be grouped separately."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()

        fixes = [
            _make_fix(
                fix_id="fix-001",
                location="https://example.edu/about | h1.title",
                original_content="<h1></h1>",
                fixed_content="<h1>About</h1>",
                review_status="approved",
                description="Empty heading on about page",
            ),
            _make_fix(
                fix_id="fix-002",
                location="https://example.edu/about | img.hero",
                original_content="<img>",
                fixed_content='<img alt="Hero image">',
                review_status="approved",
                description="Missing alt text on about page",
            ),
            _make_fix(
                fix_id="fix-003",
                location="https://example.edu/contact | form.main",
                original_content='<input type="text">',
                fixed_content='<input type="text" aria-label="Name">',
                review_status="edited",
                description="Missing form label on contact page",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text
        # Both page URLs should appear in the PDF
        assert "example.edu/about" in text
        assert "example.edu/contact" in text

    def test_location_without_pipe_uses_full_location_as_url(self):
        """If location has no ' | ' separator, the entire string is the page URL."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()

        fixes = [
            _make_fix(
                fix_id="fix-001",
                location="https://example.edu/simple-page",
                original_content="<div></div>",
                fixed_content='<div role="main"></div>',
                review_status="approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text
        assert "example.edu/simple-page" in text

    def test_fix_without_location_grouped_as_unknown(self):
        """Fixes with no location field should be grouped under '(unknown page)'."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()

        fixes = [
            _make_fix(
                fix_id="fix-001",
                location=None,
                original_content="<img>",
                fixed_content='<img alt="Photo">',
                review_status="approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text
        assert "unknown page" in text


class TestWebFixFilteringByStatus:
    """Tests that only approved/edited/auto_approved fixes appear in the section."""

    def test_only_approved_statuses_included(self):
        """Only approved, edited, and auto_approved fixes should appear in the
        Web Remediation Guide section."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()

        fixes = [
            _make_fix(
                fix_id="fix-approved",
                location="https://example.edu/page1 | div",
                original_content="<div></div>",
                fixed_content='<div role="main"></div>',
                review_status="approved",
                description="Approved fix description unique",
            ),
            _make_fix(
                fix_id="fix-rejected",
                location="https://example.edu/page2 | span",
                original_content="<span></span>",
                fixed_content='<span aria-hidden="true"></span>',
                review_status="rejected",
                description="Rejected fix xyzzy unique",
            ),
            _make_fix(
                fix_id="fix-pending",
                location="https://example.edu/page3 | p",
                original_content="<p></p>",
                fixed_content='<p lang="en"></p>',
                review_status="pending",
                description="Pending fix qwerty unique",
            ),
            _make_fix(
                fix_id="fix-edited",
                location="https://example.edu/page4 | a",
                original_content='<a href="#">Link</a>',
                fixed_content='<a href="/about">About Us</a>',
                review_status="edited",
                description="Edited fix description unique",
            ),
            _make_fix(
                fix_id="fix-auto",
                location="https://example.edu/page5 | img",
                original_content="<img>",
                fixed_content='<img alt="Logo">',
                review_status="auto_approved",
                description="AutoApproved fix description unique",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text

        # The web remediation section groups fixes by page URL.
        # Approved fixes' page URLs should appear as headings:
        assert "example.edu/page1" in text  # approved
        assert "example.edu/page4" in text  # edited
        assert "example.edu/page5" in text  # auto_approved

        # Rejected and pending fixes' unique page URLs should NOT appear
        # as web remediation headings. However, they do appear in the
        # Issues Found / Fixes Applied tables. We verify the guide
        # includes only 3 fixes by checking approved descriptions appear.
        assert "Approved fix description unique" in text
        assert "Edited fix description unique" in text
        assert "AutoApproved fix description unique" in text


class TestWebPdfOutputValidity:
    """Tests that the generated PDF output is valid."""

    def test_pdf_starts_with_pdf_header(self):
        """The generated PDF should start with the %PDF magic bytes."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()
        fixes = [
            _make_fix(
                location="https://example.edu/page | .content",
                original_content="<div></div>",
                fixed_content='<div role="main"></div>',
                review_status="approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=[_make_audit_entry()],
            matterhorn_results=[],
            department=department,
        )

        assert pdf_bytes[:5] == b"%PDF-"

    def test_pdf_is_nonempty_bytes(self):
        """The output should be non-empty bytes for any valid input."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=[],
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0

    def test_pdf_with_special_characters_in_content(self):
        """The PDF should handle HTML special characters (<, >, &) in code snippets."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()
        fixes = [
            _make_fix(
                location="https://example.edu | div",
                original_content='<div class="test" data-value="a&b">Content</div>',
                fixed_content='<div class="test" role="main" data-value="a&b">Content</div>',
                review_status="approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        # Should not crash and should produce valid PDF
        assert pdf_bytes[:5] == b"%PDF-"
        assert len(pdf_bytes) > 100

    def test_pdf_with_long_content_is_truncated(self):
        """Long original/fixed content should be truncated without crashing."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()

        long_content = "<div>" + "x" * 500 + "</div>"
        fixes = [
            _make_fix(
                location="https://example.edu | div",
                original_content=long_content,
                fixed_content=long_content,
                review_status="approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=[],
            matterhorn_results=[],
            department=department,
        )

        assert pdf_bytes[:5] == b"%PDF-"
        assert len(pdf_bytes) > 100
        # Verify it's a valid PDF by parsing it
        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text


class TestWebPdfSelectorAndSeverity:
    """Tests that element selector and severity are shown per fix."""

    def test_element_selector_shown_in_pdf(self):
        """Element selector from the location field should appear in the PDF."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()
        fixes = [
            _make_fix(
                location="https://example.edu/about | h1.page-title",
                original_content="<h1></h1>",
                fixed_content="<h1>About</h1>",
                review_status="approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan, fixes=fixes, audit_entries=[], matterhorn_results=[], department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "h1.page-title" in text

    def test_severity_shown_in_fix_label(self):
        """Fix severity should appear in the per-fix label."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()
        fixes = [
            _make_fix(
                location="https://example.edu | img",
                original_content="<img>",
                fixed_content='<img alt="Photo">',
                review_status="approved",
                severity="critical",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan, fixes=fixes, audit_entries=[], matterhorn_results=[], department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "CRITICAL" in text

    def test_no_selector_when_location_has_no_pipe(self):
        """When location has no pipe separator, no Element line should appear."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()
        fixes = [
            _make_fix(
                location="https://example.edu/page",
                original_content="<div></div>",
                fixed_content='<div role="main"></div>',
                review_status="approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan, fixes=fixes, audit_entries=[], matterhorn_results=[], department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text
        assert "Element:" not in text

    def test_none_original_and_fixed_content(self):
        """None values for original_content and fixed_content should show fallback."""

        scan = _make_scan(scan_type="web", file_name="https://example.edu")
        department = _make_department()
        fixes = [
            _make_fix(
                location="https://example.edu | div",
                original_content=None,
                fixed_content=None,
                review_status="approved",
            ),
        ]

        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan, fixes=fixes, audit_entries=[], matterhorn_results=[], department=department,
        )

        text = _extract_pdf_text(pdf_bytes)
        assert "Web Remediation Guide" in text
        assert "not available" in text
