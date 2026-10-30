"""The review evidence PDF keeps its hashes inside the metadata table, names its
counts for what they measure, and lists Matterhorn results for PDFs only."""

import hashlib
import io
import shutil

import pymupdf as fitz
import pytesseract
import pytest
from PIL import Image

from src.education.reports.compliance_report import AuditReportGenerator
from tests.test_audit_export import (
    _make_department,
    _make_fix,
    _make_matterhorn,
    _make_scan,
)

pytestmark = pytest.mark.unit

# The metadata table is 6 inches wide and centred on a US Letter page.
TABLE_RIGHT_EDGE = (612 + 6 * 72) / 2


def _render(scan, matterhorn=()):
    return AuditReportGenerator.generate_pdf(
        scan=scan,
        fixes=[_make_fix(fix_id="fix-1"), _make_fix(fix_id="fix-2")],
        audit_entries=[],
        matterhorn_results=list(matterhorn),
        department=_make_department(),
    )


def _text(pdf_bytes):
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return " ".join(" ".join(page.get_text().split()) for page in doc)


def test_hashes_stay_inside_the_table():
    scan = _make_scan()
    digest = hashlib.sha256(b"course outline").hexdigest()
    scan.file_hash = digest
    with fitz.open(stream=_render(scan), filetype="pdf") as doc:
        words = doc[0].get_text("words")
    # A wrapped digest comes back as several words; together they are the digest.
    chunks = sorted(
        (w for w in words if len(w[4]) >= 4 and w[4] in digest),
        key=lambda w: (round(w[1]), w[0]),
    )
    assert "".join(w[4] for w in chunks) == digest
    assert max(w[2] for w in chunks) <= TABLE_RIGHT_EDGE


def test_review_item_count_is_named_for_what_it_counts():
    text = _text(_render(_make_scan()))
    assert "Review Items Recorded: 2" in text
    assert "Total Issues Found" not in text


def test_word_document_has_no_matterhorn_lines():
    text = _text(_render(_make_scan(file_name="course.docx", scan_type="WORD")))
    assert "Matterhorn" not in text
    assert "Recorded Validator Result" not in text


def test_pdf_keeps_its_matterhorn_lines():
    text = _text(_render(_make_scan(), matterhorn=[_make_matterhorn(status="pass")]))
    assert "Matterhorn Checkpoints: 1/1 passed" in text
    assert "Recorded Validator Result" in text


def test_markup_in_names_is_shown_as_text():
    scan = _make_scan(file_name="notes <b>&amp; draft.pdf")
    text = _text(_render(scan))
    assert "notes <b>&amp; draft.pdf" in text


def _header_logo(pdf_bytes):
    """The first page's logo: its placed rectangle and a 4x rendering."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        xref = page.get_images(full=True)[0][0]
        rect = page.get_image_rects(xref)[0]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=rect)
        return rect, pixmap.tobytes("png")


def test_header_logo_is_the_wordmark_without_a_tagline():
    rect, _ = _header_logo(_render(_make_scan()))
    # The wordmark alone is about 3.4 times as wide as it is tall; the logo
    # with the tagline under it was about 2.5.
    assert rect.width / rect.height > 3
    assert rect.height <= 0.42 * 72 + 0.5


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")
def test_header_logo_carries_no_compliance_claim():
    _, png = _header_logo(_render(_make_scan()))
    text = pytesseract.image_to_string(Image.open(io.BytesIO(png))).upper()
    # The stylised a is not read reliably; "LIRA" shows the OCR saw the wordmark.
    assert "LIRA" in text
    assert "COMPLIANCE" not in text
