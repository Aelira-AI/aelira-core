"""Truthfulness contracts for bounded accessibility evidence PDFs."""

from io import BytesIO

import pytest
from pypdf import PdfReader

from src.education.compliance_certificate import ComplianceCertificate


def _text(pdf: bytes) -> str:
    return "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages
    )


@pytest.mark.parametrize("score", [0, 69, 70, 95, 100])
def test_legacy_score_input_never_changes_bounded_claim_posture(score):
    pdf = ComplianceCertificate.generate_certificate(
        department_name="Accessibility Office",
        institution="Example University",
        compliance_score=score,
        total_scans=4,
        files_analyzed=3,
    )
    text = _text(pdf)

    assert "Accessibility Evidence Report" in text
    assert "Coverage" in text
    assert "Methodology" in text
    assert "Automated Scan Score" in text
    assert "Unresolved Findings" in text
    assert "Verification" in text
    assert "Applicable Standard Metadata" in text
    assert "Limitations" in text
    assert "does not determine whether" in text
    assert "CERTIFICATE OF COMPLIANCE" not in text
    assert "BRONZE" not in text
    assert "SILVER" not in text
    assert "GOLD" not in text
    assert "PLATINUM" not in text
    assert "meets WCAG" not in text
    assert "verified against WCAG" not in text
    assert "support@example.com" not in text
    assert ComplianceCertificate.get_certificate_level(score) is None
