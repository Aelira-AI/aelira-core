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


@pytest.mark.asyncio
async def test_a_clean_rescan_reports_a_measured_score_and_no_remainder():
    scanner = _scanner()
    before = [_violation("image-alt", 3), _violation("label", 1)]
    after = {"violations": [], "passes": [{}] * 9}

    with patch.object(scanner, "_run_axe_scan", new=AsyncMock(return_value=after)):
        result = await scanner._verify_remediation(_cloud_file(), "<p>ok</p>", before)

    assert result["score"] == 100.0
    assert result["fixed"] == 4
    assert result["remaining"] == 0
    assert result["introduced"] == 0


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
    assert result["fixed"] == 1
    assert result["remaining"] == 1
    # A rule that did not fail before is a regression, never a remainder.
    assert result["introduced"] == 5
    assert result["score"] == 80.0


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
