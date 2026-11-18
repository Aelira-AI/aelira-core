"""Issue #719: filename and tool-placeholder alt text counts as missing alt text.

Mirrors the shipped PPTX treatment in the DOCX, XLSX, web-code and PDF
scanners: an image whose only alt text is a file name (``IMG_0042.jpg``) or
an authoring-tool label (``Picture 3``) is reported exactly as missing alt
text, with placeholder wording. Empty alt text keeps its existing meaning
(decorative), so it is never re-labelled here.
"""

import io
from types import SimpleNamespace

import pytest
import pymupdf as fitz
from docx import Document
from docx.oxml import parse_xml
from PIL import Image

from src.education.docx_processor import DocxProcessor
from src.education.pdf_checks.image_checker import ImageAccessibilityChecker
from src.education.pdf_processor import PDFProcessor
from src.education.xlsx_processor import (
    ImageIssue as XlsxImageIssue,
    SheetAnalysis,
    XlsxProcessingResult,
    XlsxProcessor,
)

pytestmark = pytest.mark.unit

PLACEHOLDER_PHRASE = "the existing text appears to be a filename or placeholder"


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), "green").save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def _docx_with_descr(tmp_path, descr: str):
    path = tmp_path / "lesson.docx"
    document = Document()
    document.add_heading("Course overview", level=2)
    document.add_paragraph("A lecture on cell biology and the mitochondrion.")
    picture = document.add_picture(io.BytesIO(_png()))
    picture._inline.docPr.set("descr", descr)
    document.save(path)
    return path


def test_docx_filename_alt_is_reported_as_missing(tmp_path):
    path = _docx_with_descr(tmp_path, "nature1.png")
    result = DocxProcessor().process_docx(str(path))

    missing = [issue for issue in result.image_issues if not issue.has_alt_text]
    assert len(missing) == 1
    assert missing[0].alt_text_is_placeholder is True
    assert missing[0].existing_alt_text == "nature1.png"

    alt_issue = next(i for i in result.issues if i["title"] == "Missing Alt Text")
    assert PLACEHOLDER_PHRASE in alt_issue["description"]
    assert alt_issue["severity"] == "critical"


def test_docx_real_description_is_not_flagged(tmp_path):
    path = _docx_with_descr(tmp_path, "Red barn under snow")
    result = DocxProcessor().process_docx(str(path))

    assert result.image_issues == []
    assert not [i for i in result.issues if i["title"] == "Missing Alt Text"]


_SMARTART_XML = (
    '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:drawing>"
    '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
    'wordprocessingDrawing">'
    '<wp:docPr id="1" name="Graphic 1" descr="{descr}"/>'
    '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    '<dgm:graphicData xmlns:dgm="http://schemas.openxmlformats.org/drawingml/'
    '2006/diagram" uri="http://schemas.openxmlformats.org/drawingml/2006/'
    'diagram"/>'
    "</a:graphic>"
    "</wp:inline>"
    "</w:drawing>"
    "</w:r>"
)


@pytest.mark.parametrize(
    ("descr", "expected"),
    [
        ("Image 1", (False, None)),
        (
            "Organization chart of the department",
            (True, "Organization chart of the department"),
        ),
    ],
)
def test_docx_smartart_placeholder_alt_is_not_accepted(descr, expected):
    document = Document()
    paragraph = document.add_paragraph()
    paragraph._element.append(parse_xml(_SMARTART_XML.format(descr=descr)))

    assert DocxProcessor()._get_smartart_alt_text(document, 0) == expected


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------
#
# openpyxl drops a loaded image's alt text (``desc`` does not survive the
# round-trip through the saved workbook), so the image check is exercised at
# its real per-image seam with description-bearing objects, and the issue
# mapping is exercised by feeding those findings through the result model.


def _xlsx_image_issues(desc):
    image = SimpleNamespace(desc=desc)
    return XlsxProcessor()._check_images(None, "Sheet1", [image], {})


def test_xlsx_placeholder_desc_is_reported_as_missing():
    issues = _xlsx_image_issues("Picture 3")

    assert len(issues) == 1
    assert issues[0].has_alt_text is False
    assert issues[0].alt_text_is_placeholder is True
    assert issues[0].existing_alt_text == "Picture 3"


def test_xlsx_real_desc_is_not_flagged():
    assert _xlsx_image_issues("Bar chart of enrolment by year") == []


def test_xlsx_missing_desc_stays_missing_without_placeholder_flag():
    issues = _xlsx_image_issues(None)

    assert len(issues) == 1
    assert issues[0].has_alt_text is False
    assert issues[0].alt_text_is_placeholder is False


def _xlsx_result(image_issue=None, chart_issue=None):
    sheet = SheetAnalysis(
        sheet_name="Sheet1",
        row_count=10,
        column_count=3,
        has_data=True,
        has_tables=False,
        has_charts=chart_issue is not None,
        has_images=image_issue is not None,
        has_merged_cells=False,
        has_frozen_panes=False,
        table_header_issues=[],
        chart_issues=[] if chart_issue is None else [chart_issue],
        image_issues=[] if image_issue is None else [image_issue],
        merge_issues=[],
        color_issues=[],
        navigation_issues=[],
    )
    return XlsxProcessingResult(
        file_path="book.xlsx",
        file_name="book.xlsx",
        total_sheets=1,
        total_rows=10,
        total_charts=1 if chart_issue else 0,
        total_images=1 if image_issue else 0,
        sheet_name_issues=[],
        sheets=[sheet],
        summary={},
        compliance_score=50.0,
        remediation_suggestions=[],
    )


def test_xlsx_image_issue_mapping_uses_placeholder_wording():
    result = _xlsx_result(
        image_issue=XlsxImageIssue(
            sheet_name="Sheet1",
            image_index=0,
            has_alt_text=False,
            existing_alt_text="Picture 3",
            alt_text_is_placeholder=True,
        )
    )

    alt_issue = next(i for i in result.issues if i["title"] == "Missing Alt Text")
    assert PLACEHOLDER_PHRASE in alt_issue["description"]
    assert alt_issue["severity"] == "critical"


def test_xlsx_chart_title_string_placeholder_is_reported_as_missing():
    issues = XlsxProcessor()._check_charts(
        None, "Sheet1", [SimpleNamespace(title="Image 1")], {}
    )

    assert len(issues) == 1
    assert issues[0].has_alt_text is False
    assert issues[0].alt_text_is_placeholder is True
    assert issues[0].existing_alt_text == "Image 1"

    result = _xlsx_result(chart_issue=issues[0])
    chart_issue = next(i for i in result.issues if "Chart" in i["title"])
    assert PLACEHOLDER_PHRASE in chart_issue["description"]


def test_xlsx_chart_title_object_is_untouched():
    class _Title:
        """Stand-in for openpyxl's Title object, which is never a str."""

        def __bool__(self):
            return True

    assert (
        XlsxProcessor()._check_charts(
            None, "Sheet1", [SimpleNamespace(title=_Title())], {}
        )
        == []
    )


# ---------------------------------------------------------------------------
# PDF image checker
# ---------------------------------------------------------------------------


def _pdf_with_alt(tmp_path, alt):
    path = tmp_path / "lesson.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 200, 500, 700), "Readable course material. " * 40)
    xref = page.insert_image(fitz.Rect(40, 40, 90, 90), stream=_png())
    if alt is not None:
        doc.xref_set_key(xref, "Alt", f"({alt})")
    doc.save(str(path))
    doc.close()
    return path


def _scan_pdf(tmp_path, alt):
    checker = ImageAccessibilityChecker(
        generate_alt_text=False, validate_alt_text=False
    )
    return checker.check(str(_pdf_with_alt(tmp_path, alt)), {})


def test_pdf_placeholder_alt_is_reported_as_missing(tmp_path):
    issues = _scan_pdf(tmp_path, "IMG_0042.jpg")

    assert len(issues) == 1
    assert issues[0].has_alt_text is False
    assert issues[0].alt_text_is_placeholder is True
    assert issues[0].existing_alt_text == "IMG_0042.jpg"


def test_pdf_real_alt_stays_described(tmp_path):
    assert _scan_pdf(tmp_path, "Photo of a red barn under snow") == []


def test_pdf_absent_alt_stays_missing_without_placeholder_flag(tmp_path):
    issues = _scan_pdf(tmp_path, None)

    assert len(issues) == 1
    assert issues[0].has_alt_text is False
    assert issues[0].alt_text_is_placeholder is False


def test_pdf_report_message_names_the_placeholder(tmp_path):
    path = _pdf_with_alt(tmp_path, "Picture 3")
    result = PDFProcessor(require_complete_scan=False).process_pdf(str(path))

    alt_issues = [
        issue
        for issue in result.issues
        if isinstance(issue, dict) and issue.get("issue_type") == "missing_alt_text"
    ]
    assert len(alt_issues) == 1
    assert PLACEHOLDER_PHRASE in alt_issues[0]["message"]
    assert alt_issues[0]["has_alt_text"] is False
