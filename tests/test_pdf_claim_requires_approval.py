"""Direct and Brightspace PDF artifacts record the equation approval gate.

The queued job already stores ``requires_approval`` in the artifact's provider
result, computed from the surviving fixes. The direct route and the Brightspace
route published the same kind of artifact without it, leaving the durable
review-gate row as the only record that the PDF needs human approval.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.api.education.remediation_routes import _artifact_requires_approval
from src.education.remediation.base import (
    FixedIssue,
    IssueCategory,
    IssueSeverity,
)
from tests.test_brightspace_pdf_output_claim import (
    CLAIMED_PDF,
    _image_equation_result,
    _PdfResult,
    _run_pdf_outer,
)
from tests.test_direct_pdf_claim_publication import (
    CLAIMED_BYTES,
    _DirectPdfResult,
    _run_route,
)

pytestmark = pytest.mark.unit


def _plain_fix() -> FixedIssue:
    return FixedIssue(
        issue_id="alt-1",
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="Image missing alternative text",
        fixed_content="A bar chart of enrolment by year",
        fix_method="ai_vision",
        confidence=0.7,
        needs_review=True,
        wcag_criteria="1.1.1",
        page_number=1,
    )


def _db_without_gate_rows() -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


def _artifact(provider_result: dict | None) -> SimpleNamespace:
    return SimpleNamespace(provider_result=provider_result)


@pytest.mark.asyncio
async def test_direct_pdf_with_an_equation_fix_records_requires_approval(tmp_path):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_BYTES)
    result = _DirectPdfResult(output)
    assert result.fixed_issues[0].source_kind == "image_equation"

    run = await _run_route(tmp_path, result)

    assert run.response["success"] is True
    provider_result = run.publication["kwargs"]["provider_result"]
    assert provider_result["requires_approval"] is True
    assert provider_result["verification_passed"] is True


@pytest.mark.asyncio
async def test_direct_pdf_without_an_equation_fix_does_not_require_approval(tmp_path):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_BYTES)
    result = _DirectPdfResult(output)
    result.fixed_issues = [_plain_fix()]

    run = await _run_route(tmp_path, result)

    assert run.response["success"] is True
    assert run.publication["kwargs"]["provider_result"]["requires_approval"] is False


@pytest.mark.asyncio
async def test_brightspace_pdf_with_an_equation_fix_records_requires_approval(
    tmp_path,
):
    result = _image_equation_result(tmp_path / "fixed.pdf")

    run = await _run_pdf_outer(tmp_path, result)

    assert run.outcome.status == "completed"
    provider_result = run.artifact.provider_result
    assert provider_result["requires_approval"] is True
    assert provider_result["verification_passed"] is True


@pytest.mark.asyncio
async def test_brightspace_pdf_without_an_equation_fix_does_not_require_approval(
    tmp_path,
):
    output = tmp_path / "partial.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output)
    result.fixed_issues = [_plain_fix()]

    run = await _run_pdf_outer(tmp_path, result)

    assert run.outcome.has_remediated_version is True
    assert run.artifact.provider_result["requires_approval"] is False


@pytest.mark.asyncio
async def test_brightspace_result_without_fix_records_is_not_gated(tmp_path):
    """Results that expose no fix list (older adapters) stay ungated."""
    output = tmp_path / "partial.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output)
    assert not hasattr(result, "fixed_issues")

    run = await _run_pdf_outer(tmp_path, result)

    assert run.artifact.provider_result["requires_approval"] is False


# The download and approval gates read the same flag.


def test_download_gate_honours_the_recorded_flag_without_review_rows():
    gated = _artifact({"requires_approval": True})
    assert _artifact_requires_approval(
        _db_without_gate_rows(), scan_id="scan-1", artifact=gated
    )


@pytest.mark.parametrize(
    "provider_result",
    [None, {}, {"requires_approval": False}, {"requires_approval": "yes"}],
)
def test_download_gate_stays_open_without_a_flag_or_review_rows(provider_result):
    assert not _artifact_requires_approval(
        _db_without_gate_rows(),
        scan_id="scan-1",
        artifact=_artifact(provider_result),
    )


def test_download_gate_still_closes_on_a_durable_review_row():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ("row",)
    assert _artifact_requires_approval(
        db, scan_id="scan-1", artifact=_artifact({"requires_approval": False})
    )
