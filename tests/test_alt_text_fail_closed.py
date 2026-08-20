"""Alt-text generation must fail CLOSED when AI is unavailable.

For an accessibility-compliance product, emitting placeholder alt text such as
"Visual content on page 3" and counting it as remediated is itself a WCAG 1.1.1
failure reported as a fix. When the AI call fails, the generators must return
None so the issue is routed to the human review queue (matching DOCX behaviour),
not a meaningless placeholder string.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.education.remediation.base import (
    IssueCategory,
    IssueSeverity,
    RemediationConfig,
    RemediationIssue,
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    yield


class _FailingAIClient:
    """AI client whose every call reports failure."""

    def generate_text_sync(self, *args, **kwargs):
        return {"success": False, "content": "", "error": "provider unavailable"}

    def analyze_image_sync(self, *args, **kwargs):
        return {"success": False, "content": "", "error": "provider unavailable"}


def _alt_issue(**metadata):
    return RemediationIssue(
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="Image missing alt text",
        metadata=metadata,
    )


def test_pdf_alt_text_returns_none_when_ai_fails():
    fitz = pytest.importorskip("fitz")  # PyMuPDF
    from src.education.remediation.pdf_remediator import PdfRemediator

    # A minimal real PDF so the remediator constructs and document context works.
    doc = fitz.open()
    doc.new_page()
    tmp = Path(__file__).parent / "_tmp_alt.pdf"
    doc.save(str(tmp))
    doc_for_ctx = fitz.open(str(tmp))
    try:
        rem = PdfRemediator(
            str(tmp),
            issues=[],
            config=RemediationConfig(),
            ai_client=_FailingAIClient(),
        )
        issue = _alt_issue(page_number=1)  # no image_xref → text path
        result = rem._generate_alt_text_with_ai(
            issue, doc_for_ctx, client=rem.alt_text_client
        )
        assert result is None
    finally:
        doc_for_ctx.close()
        tmp.unlink(missing_ok=True)


def test_pptx_alt_text_returns_none_when_ai_fails():
    pptx = pytest.importorskip("pptx")
    from src.education.remediation.pptx_remediator import PptxRemediator

    tmp = Path(__file__).parent / "_tmp_alt.pptx"
    pptx.Presentation().save(str(tmp))
    try:
        rem = PptxRemediator(
            str(tmp),
            issues=[],
            config=RemediationConfig(),
            ai_client=_FailingAIClient(),
        )
        issue = _alt_issue(slide_index=0, shape_name="Picture 1")
        result = rem._generate_alt_text_with_ai(
            issue, document=None, client=rem.alt_text_client
        )
        assert result is None
    finally:
        tmp.unlink(missing_ok=True)


def test_xlsx_chart_description_returns_none_when_ai_fails():
    openpyxl = pytest.importorskip("openpyxl")
    from src.education.remediation.xlsx_remediator import XlsxRemediator

    tmp = Path(__file__).parent / "_tmp_alt.xlsx"
    openpyxl.Workbook().save(str(tmp))

    class _StubDoc:
        class active:
            title = "Sheet1"

        def __getitem__(self, key):
            raise KeyError(key)

    try:
        rem = XlsxRemediator(
            str(tmp),
            issues=[],
            config=RemediationConfig(),
            ai_client=_FailingAIClient(),
        )
        issue = _alt_issue(sheet_name="Sheet1")
        result = rem._generate_chart_description_with_ai(
            issue, _StubDoc(), client=rem.alt_text_client
        )
        assert result is None
    finally:
        tmp.unlink(missing_ok=True)
