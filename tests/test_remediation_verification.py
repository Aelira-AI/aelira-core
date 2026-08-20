"""Remediation must be verified by a rescan, not estimated from fixer counts.

The remediated score used to be inferred from how many fixers ran, which
cannot see a fix that did not work or a fix that broke something else. Our
central claim is that every fix is verified by a rescan and the verified
number is the one that counts, so the rescan has to be real and its result
has to win.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.education.canvas_content_scanner import CanvasContentScanner
from src.education.remediation.base import RemediationConfig
from src.education.remediation.html_remediator import HtmlRemediator


def _scanner():
    return CanvasContentScanner(
        canvas_client=MagicMock(),
        db=MagicMock(),
        department_id="d1",
        credential_id="cred-1",
    )


def _violation(rule_id, nodes, impact="serious"):
    return {"id": rule_id, "impact": impact, "nodes": [{}] * nodes}


def _cloud_file():
    return MagicMock(id="cf-1", file_name="Welcome Page")


def test_html_remediator_marks_a_deterministic_fix_verified(tmp_path):
    source = tmp_path / "page.html"
    source.write_text("<html><body><p>Course content</p></body></html>")
    remediator = HtmlRemediator(
        str(source),
        [
            {
                "id": "language",
                "category": "language",
                "severity": "high",
                "description": "Document language is missing",
            }
        ],
        RemediationConfig(create_backup=False, use_ai=False, verify_fixes=True),
    )

    result = remediator.remediate()

    assert result.fixed_count == 1
    assert result.verification_passed is True
    assert result.verification_result is not None
    assert result.verification_result.passed is True


def test_html_remediator_keeps_verification_false_when_issues_remain(tmp_path):
    source = tmp_path / "page.html"
    source.write_text(
        '<html><body><img src="unresolved.png"><p>Course content</p></body></html>'
    )
    remediator = HtmlRemediator(
        str(source),
        [
            {
                "id": "language",
                "category": "language",
                "severity": "high",
                "description": "Document language is missing",
            }
        ],
        RemediationConfig(create_backup=False, use_ai=False, verify_fixes=True),
    )

    result = remediator.remediate()

    assert result.fixed_count == 1
    assert result.verification_passed is False
    assert result.verification_result is not None
    assert result.verification_result.passed is False
    assert result.verification_result.issues_after == 1


@pytest.mark.parametrize(
    "category",
    ["aria", "heading", "form", "navigation", "link", "contrast"],
)
def test_html_remediator_never_verifies_categories_without_implemented_verifier(
    tmp_path, category
):
    source = tmp_path / "page.html"
    source.write_text(
        '<html lang="en"><body><main id="main">Content</main></body></html>'
    )
    remediator = HtmlRemediator(
        str(source),
        [
            {
                "id": f"unsupported-{category}",
                "category": category,
                "severity": "high",
                "description": f"Unsupported {category} issue",
            }
        ],
        RemediationConfig(create_backup=False, use_ai=False, verify_fixes=True),
    )

    result = remediator.remediate()

    assert result.verification_passed is False
    assert result.verification_result is not None
    assert result.verification_result.passed is False
    assert any(
        "no implemented verifier" in issue.lower()
        for issue in result.verification_result.issues_remaining
    )


def test_navigation_fix_without_a_real_target_stays_unverified(tmp_path):
    source = tmp_path / "page.html"
    source.write_text('<html lang="en"><body><p>Content only</p></body></html>')
    remediator = HtmlRemediator(
        str(source),
        [
            {
                "id": "bypass",
                "category": "navigation",
                "severity": "high",
                "description": "Page needs a skip navigation link",
            }
        ],
        RemediationConfig(create_backup=False, use_ai=False, verify_fixes=True),
    )

    result = remediator.remediate()

    assert result.fixed_count == 0
    assert result.verification_passed is False


def test_url_only_image_is_never_sent_to_text_ai_or_marked_inspected(tmp_path):
    source = tmp_path / "page.html"
    source.write_text(
        '<html lang="en"><body><img src="https://cdn.example/chart.png"></body></html>'
    )
    client = MagicMock()
    client.generate_text_sync.return_value = {
        "success": True,
        "content": "A fabricated chart description",
    }
    remediator = HtmlRemediator(
        str(source),
        [
            {
                "id": "image-alt",
                "category": "alt_text",
                "severity": "high",
                "description": "Image is missing alt text",
                "location": "https://cdn.example/chart.png",
            }
        ],
        RemediationConfig(create_backup=False, use_ai=True, verify_fixes=True),
        ai_client=client,
    )

    result = remediator.remediate()

    client.generate_text_sync.assert_not_called()
    assert result.fixed_count == 0
    assert result.manual_count == 1
    assert result.verification_passed is False


@pytest.mark.asyncio
async def test_a_clean_rescan_reports_a_measured_score_and_no_remainder():
    scanner = _scanner()
    before = [_violation("image-alt", 3), _violation("label", 1)]
    after = {"violations": [], "passes": [{}] * 9}

    with patch.object(scanner, "_run_axe_scan", new=AsyncMock(return_value=after)):
        result = await scanner._verify_remediation(_cloud_file(), "<p>ok</p>", before)

    assert result.score == 100.0
    assert result.fixed == 4
    assert result.remaining == 0
    assert result.introduced == 0


@pytest.mark.asyncio
async def test_issues_the_remediation_introduced_are_counted_separately():
    scanner = _scanner()
    before = [_violation("image-alt", 2)]
    after = {
        "violations": [_violation("image-alt", 1), _violation("color-contrast", 5)],
        "passes": [{}] * 8,
    }

    with patch.object(scanner, "_run_axe_scan", new=AsyncMock(return_value=after)):
        result = await scanner._verify_remediation(_cloud_file(), "<p>x</p>", before)

    # One of the two original nodes still fails, so one was fixed.
    assert result.fixed == 1
    assert result.remaining == 1
    # A rule that did not fail before is a regression, never a remainder.
    assert result.introduced == 5
    assert result.score == 80.0


@pytest.mark.asyncio
async def test_a_failed_rescan_returns_none_so_the_caller_can_say_unverified():
    scanner = _scanner()

    with patch.object(
        scanner, "_run_axe_scan", new=AsyncMock(side_effect=RuntimeError("no browser"))
    ):
        result = await scanner._verify_remediation(
            _cloud_file(), "<p>x</p>", [_violation("image-alt", 1)]
        )

    assert result is None


@pytest.mark.asyncio
async def test_a_rule_that_fails_harder_afterwards_counts_as_introduced():
    """A rule that failed before and fails more afterwards has had failures
    introduced on top of the ones that remain. Counting the whole after-total
    as remaining would hide a regression inside a rule we already knew about."""
    scanner = _scanner()
    before = [_violation("image-alt", 1)]
    after = {"violations": [_violation("image-alt", 3)], "passes": [{}] * 9}

    with patch.object(scanner, "_run_axe_scan", new=AsyncMock(return_value=after)):
        result = await scanner._verify_remediation(_cloud_file(), "<p>x</p>", before)

    assert result.remaining == 1
    assert result.introduced == 2
    assert result.fixed == 0
