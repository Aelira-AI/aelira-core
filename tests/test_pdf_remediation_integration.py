# backend/tests/test_pdf_remediation_integration.py
"""End-to-end integration test: scan -> remediate -> re-scan."""

import pytest
import tempfile
import os
from hashlib import sha256
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "pdfs"


@pytest.mark.parametrize(
    "fixture,expected_regressions",
    [("academic_paper.pdf", ["Matterhorn 13-004"]), ("simple_syllabus.pdf", [])],
)
@pytest.mark.parametrize("_attempt", range(3))
def test_remediation_pipeline_preserves_complete_scan_boundary(
    fixture, expected_regressions, _attempt
):
    """Valid scans can verify fixes; partial findings cannot certify an output."""
    from src.education.pdf_processor import PDFProcessor
    from src.education.pdf_checks.completeness import IncompletePDFScanError
    from src.education.remediation.pdf_remediator import PdfRemediator
    from src.education.remediation.base import RemediationConfig

    input_pdf = str(FIXTURES / fixture)
    assert Path(input_pdf).is_file(), f"Missing required fixture: {fixture}"
    source_digest = sha256(Path(input_pdf).read_bytes()).hexdigest()
    unsupported_table = fixture == "academic_paper.pdf"

    # Step 1: Initial scan
    processor = PDFProcessor(generate_alt_text=False, validate_alt_text=False)
    if unsupported_table:
        with pytest.raises(IncompletePDFScanError, match="reading_order.table"):
            processor.process_pdf(input_pdf)
        # Explicit diagnostics permit testing provisional output preservation;
        # PdfRemediator must still require its own complete paired rescan.
        partial_processor = PDFProcessor(
            generate_alt_text=False,
            validate_alt_text=False,
            require_complete_scan=False,
        )
        scan_result = partial_processor.process_pdf(input_pdf)
        assert any(
            issue.get("issue_type") == "reading_order_mismatch"
            for issue in scan_result.issues
        )
    else:
        scan_result = processor.process_pdf(input_pdf)

    initial_issues = scan_result.issues
    assert len(initial_issues) > 0, "Test PDF should have accessibility issues"

    # Step 2: Remediate (no AI — rule-based and template fixes only)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = RemediationConfig(
            use_ai=False,
            verify_fixes=True,
            create_backup=False,
            output_directory=tmpdir,
            allow_legacy_nested_ai=False,
        )
        remediator = PdfRemediator(input_pdf, initial_issues, config)
        result = remediator.remediate()

        assert result.success, f"Remediation failed: {result.error_message}"
        assert result.output_file is not None
        assert os.path.exists(result.output_file)
        if unsupported_table:
            assert result.verification_passed is False
            assert result.fixed_count == 0
            assert result.manual_count > 0
            assert result.remediated_compliance_score is None
            assert result.score_provenance is None
            assert result.score_measurement is None
            assert result.score_verification_reason == "original_scan_failed"
        else:
            assert result.fixed_count > 0, (
                f"Should fix at least some issues. "
                f"Total: {result.total_issues}, manual: {result.manual_count}, "
                f"failed: {result.failed_count}"
            )

        # Step 3: Re-scan the remediated PDF
        if unsupported_table:
            with pytest.raises(IncompletePDFScanError, match="reading_order.table"):
                processor.process_pdf(result.output_file)
            re_scan = partial_processor.process_pdf(result.output_file)
        else:
            re_scan = processor.process_pdf(result.output_file)
        remaining_issues = re_scan.issues
        assert sha256(Path(input_pdf).read_bytes()).hexdigest() == source_digest

        import fitz

        with fitz.open(input_pdf) as source, fitz.open(result.output_file) as saved:
            assert [page.get_text() for page in saved] == [
                page.get_text() for page in source
            ]

        # Retain diagnostic issue-count coverage for provisional output, while
        # only the supported fixture can obtain verified fixes and a score.
        assert len(remaining_issues) < len(initial_issues), (
            f"Re-scan should show fewer issues: "
            f"{len(initial_issues)} before -> {len(remaining_issues)} after"
        )

        # Every newly introduced failure blocks; existing inaccessible fixture
        # failures remain visible without being mislabeled as regressions.
        assert result.verification_result is not None
        assert result.verification_result.unavailable_checks == []
        if unsupported_table:
            assert result.verification_result.passed is False
            assert result.verification_result.issues_fixed == []
            assert any(
                "reading_order.table" in reason
                for reason in result.verification_result.regressions
            )
        else:
            assert result.verification_result.regressions == expected_regressions
            assert result.verification_passed is True
            assert result.score_provenance == "scanner_rescan"
            assert result.score_measurement is not None

        from collections import Counter
        from src.education.validation.matterhorn import MatterhornValidator

        mh = MatterhornValidator()
        before_mh = mh.validate(input_pdf)
        after_mh = mh.validate(result.output_file)
        assert before_mh and after_mh
        assert before_mh.total > 0 and after_mh.total > 0
        before_ids = {cp.id for cp in before_mh.checkpoints}
        after_ids = {cp.id for cp in after_mh.checkpoints}
        assert before_ids.issubset(after_ids)
        assert all(
            cp.status.value == "pass"
            for cp in after_mh.checkpoints
            if cp.id not in before_ids
        )
        before_failures = Counter(
            (cp.id, cp.page_number)
            for cp in before_mh.checkpoints
            if cp.status.value == "fail"
        )
        after_failures = Counter(
            (cp.id, cp.page_number)
            for cp in after_mh.checkpoints
            if cp.status.value == "fail"
        )
        assert [
            f"Matterhorn {identifier}"
            for (identifier, _page), count in (after_failures - before_failures).items()
            for _ in range(count)
        ] == expected_regressions

        result.close_output_claim()


def test_incident_fixture_raw_scanner_output_reconciles_every_finding():
    """The production-shaped syllabus flow keeps every raw finding accounted for."""
    from src.education.pdf_processor import PDFProcessor
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.pdf_remediator import PdfRemediator

    input_pdf = str(FIXTURES / "simple_syllabus.pdf")
    processor = PDFProcessor(generate_alt_text=False, validate_alt_text=False)
    scan_result = processor.process_pdf(input_pdf)

    assert len(scan_result.issues) == 8
    structure_finding = next(
        issue
        for issue in scan_result.issues
        if issue.get("issue_type") == "missing_structure_tree"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        remediator = PdfRemediator(
            input_pdf,
            scan_result.issues,
            RemediationConfig(
                use_ai=False,
                verify_fixes=True,
                create_backup=False,
                output_directory=tmpdir,
                allow_legacy_nested_ai=False,
            ),
        )
        result = remediator.remediate()

        assert result.success, result.error_message
        assert result.total_issues == 8
        assert result.fixed_count == 5
        assert result.manual_count == 3
        assert result.failed_count == 0
        assert result.skipped_count == 0
        assert (
            result.fixed_count
            + result.manual_count
            + result.failed_count
            + result.skipped_count
            == result.total_issues
        )
        assert any(
            fixed.description == structure_finding["message"]
            for fixed in result.fixed_issues
        )
        result.close_output_claim()


def test_reported_three_finding_payload_stays_unverified_when_source_differs():
    """Historical findings cannot certify fixes a paired source scan cannot reproduce."""
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.pdf_remediator import PdfRemediator

    issues = [
        {
            "rule": "WCAG 1.3.1",
            "message": "Document should start with H1 heading",
            "severity": "medium",
            "location": "Beginning of document",
            "page_number": 1,
        },
        {
            "issue_type": "missing_title",
            "rule": "WCAG 2.4.2",
            "message": "PDF document title not set in metadata",
            "severity": "medium",
            "location": "Document metadata",
            "page_number": 1,
        },
        {
            "issue_type": "missing_pdfua_identifier",
            "rule": "PDF/UA 6.6.4",
            "message": "PDF/UA identifier not set in XMP metadata",
            "severity": "medium",
            "location": "XMP metadata",
            "page_number": 1,
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        result = PdfRemediator(
            str(FIXTURES / "simple_syllabus.pdf"),
            issues,
            RemediationConfig(
                use_ai=False,
                verify_fixes=True,
                create_backup=False,
                output_directory=tmpdir,
                allow_legacy_nested_ai=False,
            ),
        ).remediate()

        assert result.success, result.error_message
        assert result.total_issues == 3
        assert result.fixed_count == 0
        assert result.manual_count == 3
        assert result.failed_count == 0
        assert result.skipped_count == 0
        assert {manual.description for manual in result.manual_issues} == {
            issue["message"] for issue in issues
        }
        assert result.verification_passed is False
        assert result.remediated_compliance_score is None
        assert result.score_measurement is None
        assert result.score_verification_reason == "incomplete_comparison"
        result.close_output_claim()
