"""Public PDF fixtures retain truthful outcomes through backend boundaries."""

import hashlib
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pymupdf as fitz
import pytest

from src.education.pdf_processor import PDFProcessor
from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.scan_completeness import IncompleteScanError

FIXTURES = Path(__file__).parent / "fixtures" / "pdfs"
INCOMPLETE_MESSAGE = (
    "Required accessibility checks could not be completed. No score is available. "
    "Review the document manually before relying on its accessibility."
)


def test_forms_fixture_remediation_preserves_short_direct_text(tmp_path):
    source = FIXTURES / "test_forms_links.pdf"
    original = source.read_bytes()
    scan = PDFProcessor(
        generate_alt_text=False, enhance_descriptions=False
    ).process_pdf(str(source))
    assert len(scan.issues) == 6
    result = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            use_ai=False,
            verify_fixes=True,
            create_backup=False,
            output_directory=str(tmp_path),
        ),
    ).remediate()
    try:
        assert result.success, result.error_message
        assert result.output_file
        assert result.verification_passed
        assert result.remediated_compliance_score == 100
        assert result.fixed_count == 6
        with fitz.open(source) as before, fitz.open(result.output_file) as after:
            assert before[0].get_text().strip() == after[0].get_text().strip()
        rescanned = PDFProcessor(
            generate_alt_text=False, enhance_descriptions=False
        ).process_pdf(result.output_file)
        assert rescanned.compliance_score == result.remediated_compliance_score
        assert rescanned.issues == []
        assert source.read_bytes() == original
    finally:
        result.close_output_claim()


@pytest.mark.parametrize(
    "replacement", ["", "Other text content", "Unrelated words " * 5]
)
def test_short_source_text_cannot_be_lost_or_replaced(
    tmp_path, monkeypatch, replacement
):
    source = FIXTURES / "test_forms_links.pdf"
    original = source.read_bytes()

    def replace_text(self, document, output_path):
        with fitz.open() as output:
            page = output.new_page()
            if replacement:
                page.insert_text((72, 72), replacement)
            output.save(output_path)

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", replace_text)
    result = PdfRemediator(
        str(source),
        [{"type": "language", "severity": "medium", "message": "Missing language"}],
        RemediationConfig(
            use_ai=False,
            verify_fixes=False,
            create_backup=False,
            output_directory=str(tmp_path),
        ),
    ).remediate()
    try:
        assert not result.success
        assert result.output_file is None
        assert "text layer" in result.error_message
        assert source.read_bytes() == original
        assert not list(tmp_path.glob("*_remediated.pdf"))
    finally:
        result.close_output_claim()


@pytest.mark.parametrize("fixture", ["academic_paper.pdf", "test_forms_links.pdf"])
def test_real_pdf_background_scan_has_truthful_terminal_outcome(
    tmp_path, monkeypatch, fixture
):
    from src.api.education.scan_routes import process_pdf_background
    from src.db.models import ScanStatus

    path = tmp_path / fixture
    shutil.copy2(FIXTURES / fixture, path)
    content = path.read_bytes()
    scan = SimpleNamespace(
        id="scan-a", department_id="dept-a", status=ScanStatus.PROCESSING
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = scan
    monkeypatch.setattr("src.db.database.SessionLocal", lambda: db)
    process_pdf_background(str(path), content, fixture, scan.id, False, False)

    assert not path.exists()
    if fixture == "academic_paper.pdf":
        assert scan.status == ScanStatus.FAILED
        assert scan.error_message == INCOMPLETE_MESSAGE
        assert scan.progress_message == INCOMPLETE_MESSAGE
        db.add.assert_not_called()
    else:
        assert scan.status == ScanStatus.COMPLETED
        assert scan.file_hash == hashlib.sha256(content).hexdigest()
        assert len(db.add.call_args.args[0].issues) == 6
    db.close.assert_called()


@pytest.mark.parametrize("error", [RuntimeError, IncompleteScanError])
def test_pdf_failure_never_publishes_exception_text(tmp_path, monkeypatch, error):
    from src.api.education.scan_routes import process_pdf_background

    scan = SimpleNamespace(id="scan-a", department_id="dept-a")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = scan
    monkeypatch.setattr("src.db.database.SessionLocal", lambda: db)
    monkeypatch.setattr(
        PDFProcessor, "process_pdf", MagicMock(side_effect=error("private-path-secret"))
    )
    process_pdf_background(
        str(tmp_path / "input.pdf"), b"content", "input.pdf", scan.id, False, False
    )
    assert "private-path-secret" not in scan.error_message
    assert scan.error_message == scan.progress_message
    if error is IncompleteScanError:
        assert scan.error_message == INCOMPLETE_MESSAGE
    else:
        assert (
            scan.error_message == "Processing encountered an error. Please try again."
        )
    db.add.assert_not_called()
