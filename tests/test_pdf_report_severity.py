"""Severity summaries in the PDF report must count every kind of finding."""

import pytest

from src.education.pdf_report_generator import issue_severity


@pytest.mark.parametrize(
    "issue, expected",
    [
        ({"impact": "serious"}, "serious"),
        ({"severity": "critical"}, "critical"),
        ({"impact": "Moderate", "severity": "low"}, "moderate"),
        (
            {
                "severity": "high",
                "impact": "Blind and visually impaired users cannot access visual information",
            },
            "high",
        ),
        ({"impact": "prose about who is affected"}, ""),
        ({}, ""),
    ],
)
def test_severity_comes_from_the_field_that_holds_a_level(issue, expected):
    assert issue_severity(issue) == expected


def test_media_findings_are_counted_in_the_summary():
    from src.education.pdf_report_generator import AccessibilityPDFReportGenerator

    issues = [
        {
            "severity": "critical",
            "rule": "WCAG 1.2.2",
            "message": "Missing captions/transcription for multimedia content",
            "impact": "Deaf and hard-of-hearing users cannot access audio content",
        },
        {
            "severity": "high",
            "rule": "WCAG 1.2.3",
            "message": "Audio description not provided for visual content",
            "impact": "Blind and visually impaired users cannot access visual information",
        },
    ]
    pdf = AccessibilityPDFReportGenerator.generate_website_report(
        {
            "url": "teaching-clip.mp4",
            "created_at": "2026-01-01T00:00:00Z",
            "compliance_score": 70,
            "issues": issues,
            "total_issues": 2,
            "pages_scanned": 1,
        }
    )
    from io import BytesIO

    from pypdf import PdfReader

    text = PdfReader(BytesIO(pdf)).pages[0].extract_text()
    summary = text.split("Summary", 1)[1].split("Accessibility Issues", 1)[0]
    counts = [int(token) for token in summary.split() if token.isdigit()]
    # Critical, serious, moderate, minor: one critical and one high finding.
    assert counts[-4:] == [1, 1, 0, 0]
