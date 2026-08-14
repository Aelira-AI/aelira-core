"""
Tests for _resolve_remediated_score.

The fix-ratio estimate reports 100 whenever every detected issue is marked
fixed, because it assumes the fixes landed and that nothing new was found.
Post-remediation verification exists precisely to check that assumption, so
its score must win when it ran.
"""

import pytest

from src.api.demo_routes import _resolve_remediated_score


def test_verified_score_wins_over_fix_ratio_estimate():
    """Regression: a fully-fixed document must not report a perfect score.

    Numbers taken from a real demo scan of a screenshot-to-PDF upload. All 5
    detected issues were fixed, so the estimate produced 100, while the
    verified re-scan of the output file scored 49 — two critical PDF/UA
    issues (missing content marking, empty parent tree) only became
    detectable once remediation had built the structure tree.
    """
    score = _resolve_remediated_score(
        original_score=49.0,
        total_issues=5,
        fixed_count=5,
        remediation_result={
            "remediated_compliance_score": 49.0,
            "score_verified": True,
        },
    )
    assert score == 49.0


def test_falls_back_to_estimate_when_verification_did_not_run():
    """Without verification the estimate is all we have; behaviour unchanged."""
    score = _resolve_remediated_score(
        original_score=49.0,
        total_issues=5,
        fixed_count=5,
        remediation_result={"score_verified": False},
    )
    assert score == pytest.approx(100.0)


def test_verified_score_is_used_even_when_it_is_lower_than_the_original():
    """Remediation can make a document worse; reporting must not hide that."""
    score = _resolve_remediated_score(
        original_score=60.0,
        total_issues=4,
        fixed_count=4,
        remediation_result={
            "remediated_compliance_score": 35.0,
            "score_verified": True,
        },
    )
    assert score == 35.0


def test_verified_flag_without_a_score_falls_back():
    """Verification can be flagged but produce no score; don't crash on None."""
    score = _resolve_remediated_score(
        original_score=40.0,
        total_issues=2,
        fixed_count=1,
        remediation_result={
            "remediated_compliance_score": None,
            "score_verified": True,
        },
    )
    assert score == pytest.approx(70.0)


@pytest.mark.parametrize("remediation_result", [None, {}])
def test_missing_remediation_result_uses_estimate(remediation_result):
    score = _resolve_remediated_score(
        original_score=50.0,
        total_issues=2,
        fixed_count=1,
        remediation_result=remediation_result,
    )
    assert score == pytest.approx(75.0)


def test_no_fixes_returns_original_score():
    score = _resolve_remediated_score(
        original_score=42.0,
        total_issues=3,
        fixed_count=0,
        remediation_result=None,
    )
    assert score == 42.0


def test_estimate_never_exceeds_one_hundred():
    score = _resolve_remediated_score(
        original_score=95.0,
        total_issues=1,
        fixed_count=5,
        remediation_result=None,
    )
    assert score <= 100.0
