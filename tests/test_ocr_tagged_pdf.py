"""
Regression tests for OCR short-circuit paths in PDFProcessor._ocr_pdf_enhanced.

A PDF that already carries a text layer (PriorOcrFoundError) or a structure
tree (TaggedPDFError) does not need OCR. Both are ordinary control flow, not
failures, so neither may be logged at ERROR level — doing so promotes a normal
branch into a Sentry issue.

TaggedPDFError is reachable from the demo route: remediation adds a structure
tree, then verification re-scans the remediated file, which is now tagged.
"""

import logging

import ocrmypdf
import pytest

from src.education.pdf_processor import PDFProcessor


@pytest.fixture
def processor():
    return PDFProcessor()


@pytest.mark.parametrize(
    "exc",
    [
        ocrmypdf.exceptions.PriorOcrFoundError,
        ocrmypdf.exceptions.TaggedPDFError,
    ],
)
def test_no_ocr_needed_extracts_directly_without_error_log(
    processor, monkeypatch, caplog, tmp_path, exc
):
    """Both 'OCR unnecessary' signals extract text directly and log no ERROR."""
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    def raise_exc(*args, **kwargs):
        raise exc("no OCR needed")

    # Long enough to look like a real text layer; a short result would (rightly)
    # trigger the pytesseract fallback instead.
    extracted = "extracted text " * 8

    monkeypatch.setattr(ocrmypdf, "ocr", raise_exc)
    monkeypatch.setattr(PDFProcessor, "_extract_text", lambda self, path: extracted)

    with caplog.at_level(logging.DEBUG):
        text, out_path = processor._ocr_pdf_enhanced(str(pdf_path))

    assert text == extracted
    # Returns the original path — no OCR output file was produced.
    assert out_path == str(pdf_path)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not error_records, (
        f"expected no ERROR logs for {exc.__name__}, got: "
        f"{[r.getMessage() for r in error_records]}"
    )


def test_tagged_image_only_pdf_falls_back_to_pytesseract(
    processor, monkeypatch, tmp_path
):
    """A tagged PDF whose pages are still images must be OCR'd, not read as empty.

    Remediation writes a structure tree onto the original scan, so verification
    re-scans a file that is tagged but has no text layer. Trusting the direct
    extraction there reports "No content extracted from PDF" — a critical issue
    that caps the compliance score at 49 no matter how good the remediation was.
    """
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    def raise_exc(*args, **kwargs):
        raise ocrmypdf.exceptions.TaggedPDFError("already tagged")

    monkeypatch.setattr(ocrmypdf, "ocr", raise_exc)
    monkeypatch.setattr(PDFProcessor, "_extract_text", lambda self, path: "")
    monkeypatch.setattr(
        PDFProcessor, "_ocr_pdf_fallback", lambda self, path: "text read off the image"
    )

    text, out_path = processor._ocr_pdf_enhanced(str(pdf_path))

    assert text == "text read off the image"
    assert out_path == str(pdf_path)


def test_tagged_pdf_with_a_real_text_layer_skips_the_ocr_fallback(
    processor, monkeypatch, tmp_path
):
    """Don't pay for OCR when direct extraction already produced the text."""
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    def raise_exc(*args, **kwargs):
        raise ocrmypdf.exceptions.PriorOcrFoundError("has text layer")

    def fail(self, path):
        raise AssertionError("pytesseract fallback should not run")

    monkeypatch.setattr(ocrmypdf, "ocr", raise_exc)
    monkeypatch.setattr(PDFProcessor, "_extract_text", lambda self, path: "x" * 80)
    monkeypatch.setattr(PDFProcessor, "_ocr_pdf_fallback", fail)

    text, _ = processor._ocr_pdf_enhanced(str(pdf_path))

    assert text == "x" * 80


def test_genuine_ocr_failure_still_logs_error_and_reraises(
    processor, monkeypatch, caplog, tmp_path
):
    """A real OCRmyPDF failure must still log ERROR and propagate to the caller.

    Guards against over-broad suppression: the caller falls back to pytesseract,
    which yields lower-quality output, so genuine breakage must stay visible.
    """
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    def raise_exc(*args, **kwargs):
        raise ocrmypdf.exceptions.MissingDependencyError("tesseract not installed")

    monkeypatch.setattr(ocrmypdf, "ocr", raise_exc)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ocrmypdf.exceptions.MissingDependencyError):
            processor._ocr_pdf_enhanced(str(pdf_path))

    assert any(r.levelno >= logging.ERROR for r in caplog.records)
