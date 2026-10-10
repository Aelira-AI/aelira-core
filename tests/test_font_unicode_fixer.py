"""The retired partial fixer cannot mutate or claim a successful no-op."""

import pytest
from pikepdf import Array, Dictionary, Name

from pdf_font_fixtures import snapshot
from test_pdf_font_text import simple_pdf
from src.education.remediation.base import (
    IssueCategory,
    IssueSeverity,
    RemediationIssue,
)
from src.education.remediation.font_unicode_fixer import FontUnicodeFixer

pytestmark = pytest.mark.unit


def issue(kind="missing_tounicode"):
    return RemediationIssue(
        category=IssueCategory.STRUCTURE,
        severity=IssueSeverity.MEDIUM,
        description="Font finding",
        metadata={"issue_type": kind},
    )


@pytest.mark.parametrize(
    "encoding",
    [
        None,
        Name.WinAnsiEncoding,
        Dictionary(
            BaseEncoding=Name.WinAnsiEncoding, Differences=Array([127, Name.bullet])
        ),
        Dictionary(
            BaseEncoding=Name.WinAnsiEncoding, Differences=Array([65, Name.unknown])
        ),
    ],
)
def test_findings_are_unresolved_and_source_unchanged(encoding):
    with simple_pdf(b"BT /F1 12 Tf (AB) Tj ET", encoding=encoding) as pdf:
        before = snapshot(pdf)
        results = FontUnicodeFixer(pdf).fix([issue()])
        assert results and all(
            not result.success and result.needs_review for result in results
        )
        assert all(
            result.mappings_added == 0 and result.confidence == 0 for result in results
        )
        assert snapshot(pdf) == before


def test_no_relevant_issue_or_noop_never_becomes_fixed():
    with simple_pdf(b"") as pdf:
        assert FontUnicodeFixer(pdf).fix([]) == []
        assert FontUnicodeFixer(pdf).fix([issue("other_structure")]) == []
