"""A partial heading inventory must not invent a first-heading finding."""

import pytest
from pikepdf import Name

from src.education.pdf_checks import bound_headings
from src.education.pdf_checks.completeness import (
    IncompletePDFScanError,
    require_complete_pdf_scan,
)
from src.education.pdf_checks.structure_checker import StructureTreeChecker
from src.education.pdf_processor import PDFProcessor
from src.education.remediation import source_order
from test_pdf_bound_heading_inventory import title_pdf

pytestmark = pytest.mark.unit


def _first_h1_with_empty_h2(pdf, group, owners):
    owners[0].S = Name.H1
    owners[2].S = Name.H2
    del owners[2].K


def _first_heading_findings(result):
    return [
        issue
        for issue in result.issues
        if issue.get("message", "").startswith("Document should start with H1")
    ]


@pytest.fixture
def ocr_text(monkeypatch):
    # The fixture's text layer is short enough to send the processor to OCR.
    # Return the page text so the text-heuristic heading check runs the same
    # way with or without Tesseract installed.
    monkeypatch.setattr(
        PDFProcessor,
        "_ocr_pdf",
        lambda self, path: (
            "R\xe9sum\xe9 of methods\nfor invented courses\n\n"
            "Ordinary source paragraph\n"
        ),
    )


def test_empty_later_heading_does_not_create_first_heading_finding(tmp_path, ocr_text):
    path = title_pdf(tmp_path, change=_first_h1_with_empty_h2)
    result = PDFProcessor(
        generate_alt_text=False,
        enhance_descriptions=False,
        require_complete_scan=False,
    ).process_pdf(str(path))
    assert _first_heading_findings(result) == []


def test_refused_first_heading_keeps_first_heading_finding(tmp_path, ocr_text):
    def change(pdf, group, owners):
        owners[0].S = Name.H1
        del owners[0].K

    path = title_pdf(tmp_path, change=change)
    result = PDFProcessor(
        generate_alt_text=False,
        enhance_descriptions=False,
        require_complete_scan=False,
    ).process_pdf(str(path))
    assert len(_first_heading_findings(result)) == 1


def test_complete_inventory_still_reports_first_heading_not_h1(tmp_path, ocr_text):
    def change(pdf, group, owners):
        owners[0].S = Name.H2
        owners[2].S = Name.H1

    path = title_pdf(tmp_path, change=change)
    result = PDFProcessor(
        generate_alt_text=False,
        enhance_descriptions=False,
        require_complete_scan=False,
    ).process_pdf(str(path))
    assert len(_first_heading_findings(result)) == 1


def _inventory_in_strict_scan(path):
    with require_complete_pdf_scan(True):
        return StructureTreeChecker().heading_inventory(str(path))


def test_work_limit_in_strict_mode_fails_closed(tmp_path, monkeypatch):
    path = title_pdf(tmp_path, change=_first_h1_with_empty_h2)
    monkeypatch.setattr(source_order, "MAX_NODES", 1)
    with pytest.raises(IncompletePDFScanError):
        _inventory_in_strict_scan(path)


def test_unexpected_error_in_strict_mode_fails_closed(tmp_path, monkeypatch):
    path = title_pdf(tmp_path, change=_first_h1_with_empty_h2)

    def boom(*args, **kwargs):
        raise RuntimeError("resolver blew up")

    monkeypatch.setattr(bound_headings.MarkedContentResolver, "text", boom)
    with pytest.raises(IncompletePDFScanError):
        _inventory_in_strict_scan(path)


def test_empty_heading_is_a_structure_refusal_not_an_incomplete_check(tmp_path):
    path = title_pdf(tmp_path, change=_first_h1_with_empty_h2)
    found = _inventory_in_strict_scan(path)
    assert not found.complete
    assert found.reason == "heading_unbound_or_empty"
