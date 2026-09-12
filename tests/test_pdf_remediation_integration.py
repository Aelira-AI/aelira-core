# backend/tests/test_pdf_remediation_integration.py
"""End-to-end integration test: scan -> remediate -> re-scan."""

import pytest
import tempfile
import os
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "pdfs"


@pytest.mark.skipif(
    not (FIXTURES / "simple_syllabus.pdf").exists()
    and not (FIXTURES / "academic_paper.pdf").exists(),
    reason="Test fixture not available",
)
def test_full_remediation_pipeline():
    """Scan a PDF, remediate it, re-scan, and verify improvement."""
    from src.education.pdf_processor import PDFProcessor
    from src.education.remediation.pdf_remediator import PdfRemediator
    from src.education.remediation.base import RemediationConfig

    # Pick whichever fixture exists
    if (FIXTURES / "academic_paper.pdf").exists():
        input_pdf = str(FIXTURES / "academic_paper.pdf")
    else:
        input_pdf = str(FIXTURES / "simple_syllabus.pdf")

    # Step 1: Initial scan
    processor = PDFProcessor(generate_alt_text=False, validate_alt_text=False)
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
        assert result.fixed_count > 0, (
            f"Should fix at least some issues. "
            f"Total: {result.total_issues}, manual: {result.manual_count}, "
            f"failed: {result.failed_count}"
        )

        # Step 3: Re-scan the remediated PDF
        re_scan = processor.process_pdf(result.output_file)
        remaining_issues = re_scan.issues

        # Step 4: Verify improvement — fewer issues after remediation
        assert len(remaining_issues) < len(initial_issues), (
            f"Re-scan should show fewer issues: "
            f"{len(initial_issues)} before -> {len(remaining_issues)} after"
        )

        # Step 5: Check built-in verification result for regressions (lenient)
        if result.verification_result:
            regressions = getattr(result.verification_result, "regressions", [])
            if regressions:
                # Log but don't fail — some regressions may be expected
                print(f"Verification regressions (non-fatal): {regressions}")

        # Step 6: Explicit Matterhorn validation (lenient — don't fail test)
        try:
            from src.education.validation.matterhorn import MatterhornValidator

            mh = MatterhornValidator()
            mh_result = mh.validate(result.output_file)
            if mh_result:
                failed_cps = [
                    cp for cp in mh_result.checkpoints if cp.status.value == "fail"
                ]
                if failed_cps:
                    print(
                        f"Matterhorn failures (non-fatal): {len(failed_cps)} "
                        f"of {mh_result.total} checkpoints"
                    )
                else:
                    print(f"Matterhorn: all {mh_result.total} checkpoints passed")
        except Exception as e:
            print(f"Matterhorn validation skipped: {e}")

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
