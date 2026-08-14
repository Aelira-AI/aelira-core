"""Small-document scores must be governed by defects, not document size.

Issue #90: four fuzz-corpus files with identical severity counts (1 high +
1 medium, weighted 3.0) scored 100.0 / 75.0 / 70.0 / 0.0, driven only by
total_elements. Two defects:

1. Processor wrappers returned a perfect 100.0 for total_elements == 0 while
   discarding the issue list.
2. Ratio scoring saturates: fail_ratio caps at 1.0, so any document with
   weighted_issues >= total_elements is mathematically pinned to 0.0 (a
   single-slide PPTX with 3 elements and weight 3.0 can never score above 0).

Contract under test: below MIN_ELEMENTS_FOR_RATIO the penalty path (diminishing
returns, size-independent) is used, so identical defects score identically for
any small document; at or above it, ratio scoring behaves as before.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.education.compliance_scoring import (
    MIN_ELEMENTS_FOR_RATIO,
    calculate_compliance_score,
)

ONE_HIGH_ONE_MEDIUM = [{"severity": "high"}, {"severity": "medium"}]


@pytest.mark.unit
def test_zero_elements_with_issues_is_not_perfect():
    result = calculate_compliance_score(ONE_HIGH_ONE_MEDIUM, total_elements=0)
    assert result.score < 100.0


@pytest.mark.unit
def test_small_docs_with_identical_defects_score_identically():
    # The issue #90 evidence table: total_elements 3, 10, 12 spanned 0.0-75.0.
    scores = {
        n: calculate_compliance_score(ONE_HIGH_ONE_MEDIUM, total_elements=n).score
        for n in (0, 3, 10, 12)
    }
    assert len(set(scores.values())) == 1, f"size-dependent scores: {scores}"


@pytest.mark.unit
def test_small_doc_is_not_pinned_to_zero():
    # weighted 3.0 >= 3 elements used to force fail_ratio 1.0 -> score 0.0
    result = calculate_compliance_score(ONE_HIGH_ONE_MEDIUM, total_elements=3)
    assert result.score > 0.0


@pytest.mark.unit
def test_large_doc_keeps_ratio_scoring():
    # 1 high + 1 medium = weighted 3.0 over 100 elements -> 97.0
    result = calculate_compliance_score(ONE_HIGH_ONE_MEDIUM, total_elements=100)
    assert result.score == 97.0


@pytest.mark.unit
def test_threshold_boundary_is_pinned():
    """Pin the exact MIN_ELEMENTS_FOR_RATIO edge so a refactor can't move it.

    One medium issue: penalty path scores 97.0 (base 3), ratio path scores
    (1 - 1/20) * 100 = 95.0 — distinct values, so the branch taken is provable
    from the score alone.
    """
    one_medium = [{"severity": "medium"}]
    below = calculate_compliance_score(
        one_medium, total_elements=MIN_ELEMENTS_FOR_RATIO - 1
    )
    at = calculate_compliance_score(one_medium, total_elements=MIN_ELEMENTS_FOR_RATIO)
    assert below.score == 97.0  # penalty path
    assert at.score == 95.0  # ratio path


@pytest.mark.unit
def test_clean_doc_still_scores_100_at_any_size():
    for n in (0, 3, MIN_ELEMENTS_FOR_RATIO, 500):
        assert calculate_compliance_score([], total_elements=n).score == 100.0


@pytest.mark.unit
def test_critical_cap_survives_on_penalty_path():
    result = calculate_compliance_score([{"severity": "critical"}], total_elements=2)
    assert result.score <= 49.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "make_call",
    [
        pytest.param(
            lambda: __import__(
                "src.education.docx_processor", fromlist=["DocxProcessor"]
            )
            .DocxProcessor()
            ._calculate_compliance_score({"image_issues": 1, "heading_issues": 1}, 0),
            id="docx",
        ),
        pytest.param(
            lambda: __import__(
                "src.education.xlsx_processor", fromlist=["XlsxProcessor"]
            )
            .XlsxProcessor()
            ._calculate_compliance_score(
                {"chart_issues": 1, "color_only_issues": 1}, 0
            ),
            id="xlsx",
        ),
        pytest.param(
            lambda: __import__(
                "src.education.pptx_processor", fromlist=["PowerPointProcessor"]
            )
            .PowerPointProcessor()
            ._calculate_compliance_score(
                {"alt_text_issues": 1, "contrast_issues": 1}, 0, 0
            ),
            id="pptx",
        ),
    ],
)
def test_processor_wrappers_no_longer_return_100_for_empty_docs(make_call):
    assert make_call() < 100.0
