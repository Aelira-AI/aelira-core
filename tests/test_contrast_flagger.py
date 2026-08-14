"""Tests for ContrastFlagger specialist module."""


def test_contrast_flagger_produces_guidance():
    """ContrastFlagger should produce structured guidance, not fixes."""
    from src.education.remediation.contrast_flagger import ContrastFlagger
    from src.education.remediation.base import (
        RemediationIssue,
        IssueCategory,
        IssueSeverity,
    )

    issue = RemediationIssue(
        category=IssueCategory.CONTRAST,
        severity=IssueSeverity.MEDIUM,
        description="Low color contrast: ratio 2.5:1",
        metadata={
            "issue_type": "low_color_contrast",
            "contrast_ratio": 2.5,
            "foreground_color": "#777777",
            "background_color": "#FFFFFF",
            "page_number": 1,
        },
    )

    flagger = ContrastFlagger()
    results = flagger.flag([issue])

    assert len(results) == 1
    result = results[0]
    assert result.issue_id == issue.id
    assert "4.5:1" in result.guidance or "3:1" in result.guidance
    assert result.manual_review_required is True
