"""Partial deterministic PDF checks cannot prove remediation improvement."""

import shutil

import fitz
import pytest

from src.education.pdf_processor import PDFProcessor
from src.education.pdf_checks.completeness import (
    IncompletePDFScanError,
    complete_scan_requested,
    require_complete_pdf_scan,
)


@pytest.fixture
def document(tmp_path):
    path = tmp_path / "source.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), "COURSE OVERVIEW")
        for index in range(5):
            page.insert_text(
                (50, 80 + 20 * index),
                "Course materials cover accessible instruction and assessment.",
            )
        pdf.save(path)
    return str(path)


def test_real_pdf_successful_checks_provide_repeatable_score(document):
    scanner = PDFProcessor(require_complete_scan=True)
    first = scanner.process_pdf(document)
    second = scanner.process_pdf(document)
    assert first.compliance_score == second.compliance_score
    assert first.issues == second.issues
    assert first.pages == 1
    assert not complete_scan_requested()


@pytest.mark.parametrize(
    "checker,method",
    [
        ("TableAccessibilityChecker", "check"),
        ("ReadingOrderVerifier", "check"),
        ("FormFieldChecker", "check"),
        ("FormFieldChecker", "check_links"),
        ("ColorContrastChecker", "check"),
    ],
)
def test_failed_output_checker_cannot_claim_improvement(
    document, tmp_path, monkeypatch, checker, method
):
    import src.education.pdf_processor as module

    scanner = PDFProcessor(require_complete_scan=True)
    source = scanner.process_pdf(document)
    assert source.issues
    output = str(tmp_path / "output.pdf")
    shutil.copyfile(document, output)
    checker_type = getattr(module, checker)
    original = getattr(checker_type, method)

    def failed_output(self, path, *args, **kwargs):
        if path == output:
            raise RuntimeError("Injected checker failure")
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(checker_type, method, failed_output)
    # A real source measurement remains possible, so this probes the output failure.
    assert scanner.process_pdf(document).compliance_score == source.compliance_score
    with pytest.raises(IncompletePDFScanError):
        scanner.process_pdf(output)
    assert not complete_scan_requested()


def test_nested_contrast_parse_failure_is_not_empty_success(document, monkeypatch):
    from src.education.pdf_checks import contrast_checker

    original = contrast_checker.pikepdf.parse_content_stream

    def failed_stream(*args, **kwargs):
        raise RuntimeError("Injected content-stream failure")

    checker = contrast_checker.ColorContrastChecker()
    with require_complete_pdf_scan(True):
        assert isinstance(checker.check(document), list)
    monkeypatch.setattr(contrast_checker.pikepdf, "parse_content_stream", failed_stream)
    with pytest.raises(IncompletePDFScanError, match="contrast_checker"):
        with require_complete_pdf_scan(True):
            checker.check(document)
    assert checker.check(document) == []  # Legacy partial scans remain supported.
    monkeypatch.setattr(contrast_checker.pikepdf, "parse_content_stream", original)


def test_missing_required_dependency_cannot_pass(document, monkeypatch):
    from src.education.pdf_checks import contrast_checker

    monkeypatch.setattr(contrast_checker, "HAS_PIKEPDF", False)
    with pytest.raises(IncompletePDFScanError, match="dependency"):
        PDFProcessor(require_complete_scan=True).process_pdf(document)


def test_tagged_pdf_can_complete_strict_checks(document, tmp_path):
    import pikepdf
    from src.education.remediation.pdf_structure import PDFStructureTree

    output = str(tmp_path / "tagged.pdf")
    with pikepdf.open(document) as pdf:
        structure = PDFStructureTree(pdf)
        structure.set_document_language("en-US")
        structure.set_document_title("Course overview")
        structure.add_heading(page_num=1, level=1, text="COURSE OVERVIEW")
        structure.add_paragraph(page_num=1, text="Course materials")
        pdf.save(output)
    result = PDFProcessor(require_complete_scan=True).process_pdf(output)
    assert result.pages == 1
    assert 0 <= result.compliance_score <= 100


def test_contrast_after_page_ten_is_checked_in_strict_mode(tmp_path):
    from src.education.pdf_checks.contrast_checker import ColorContrastChecker

    output = str(tmp_path / "eleven_pages.pdf")
    with fitz.open() as pdf:
        for page_number in range(11):
            page = pdf.new_page()
            page.insert_text(
                (50, 50),
                "Course overview",
                color=(0.5, 0.5, 0.5) if page_number == 10 else (0, 0, 0),
            )
        pdf.save(output)
    checker = ColorContrastChecker()
    assert checker.check(output) == []
    with require_complete_pdf_scan(True):
        issues = checker.check(output)
    assert issues and issues[0]["page_number"] == 11
