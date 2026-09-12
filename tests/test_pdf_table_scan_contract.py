"""Table page coverage reaches aggregate scans and saved-score verification."""

from contextlib import nullcontext
from hashlib import sha256

import pytest

from test_reading_order_tables import build_table_pdf
from src.education.pdf_checks.completeness import IncompletePDFScanError
from src.education.pdf_processor import PDFProcessor
from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator

pytestmark = pytest.mark.unit


def save_fixture(directory, **kwargs):
    directory.mkdir()
    pdf, path = build_table_pdf(directory, **kwargs)
    with pdf:
        pdf.save(path)
    return path


@pytest.mark.parametrize(
    "order",
    [
        ("paragraph", "table", "heading"),
        ("table", "heading", "paragraph"),
        ("heading", "table"),
        ("heading", "table", "paragraph", "paragraph"),
    ],
)
def test_table_surrounding_corruption_reaches_aggregate(tmp_path, order):
    source = save_fixture(tmp_path / "source", pages=2)
    changed = save_fixture(tmp_path / "changed", pages=2, order=order)
    hashes = [sha256(p.read_bytes()).hexdigest() for p in (source, changed)]
    before = PDFProcessor(require_complete_scan=True).process_pdf(str(source))
    after = PDFProcessor(require_complete_scan=False).process_pdf(str(changed))
    assert not any(
        i.get("issue_type") == "reading_order_mismatch" for i in before.issues
    )
    findings = [
        i for i in after.issues if i.get("issue_type") == "reading_order_mismatch"
    ]
    assert {i["page_number"] for i in findings} == {1, 2}
    assert hashes == [sha256(p.read_bytes()).hexdigest() for p in (source, changed)]


@pytest.mark.parametrize(
    "defect", ["ambiguous_text", "empty_table", "replacement", "side_text"]
)
def test_unsupported_tables_are_visible_and_strict_scan_is_incomplete(tmp_path, defect):
    path = save_fixture(tmp_path / "source", defect=defect)
    partial = PDFProcessor(require_complete_scan=False).process_pdf(str(path))
    assert any(i.get("issue_type") == "reading_order_mismatch" for i in partial.issues)
    with pytest.raises(IncompletePDFScanError, match="reading_order.table"):
        PDFProcessor(require_complete_scan=True).process_pdf(str(path))


@pytest.mark.parametrize("outcome", ["unchanged", "reordered", "unsupported"])
def test_table_source_saved_verification_never_certifies_missing_coverage(
    tmp_path, monkeypatch, outcome
):
    source = save_fixture(tmp_path / "source")
    options = {
        "reordered": {"order": ("table", "heading", "paragraph")},
        "unsupported": {"defect": "replacement"},
    }
    saved = save_fixture(tmp_path / "saved", **options.get(outcome, {}))
    source_scan = PDFProcessor(require_complete_scan=True).process_pdf(str(source))
    remediator = PdfRemediator(
        str(source), source_scan.issues, config=RemediationConfig(use_ai=False)
    )
    monkeypatch.setattr(
        remediator,
        "_materialize_output_claim_for_verification",
        lambda: nullcontext(str(saved)),
    )
    verification = remediator._verify_fixes(str(saved))
    if outcome == "unsupported":
        assert not verification.passed
        assert remediator.result.remediated_compliance_score is None
        assert remediator.result.score_provenance is None
        assert remediator.result.score_verification_reason == "output_scan_failed"
    else:
        assert remediator.result.score_provenance == "scanner_rescan"
        assert remediator.result.remediated_compliance_score is not None
        if outcome == "reordered":
            assert not verification.passed
            assert verification.regressions
