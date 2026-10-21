"""Durable, source-bound explanations must survive without private diagnostics."""

from types import SimpleNamespace

import pytest

from src.education.remediation.outcome_accounting import outcome_accounting
from src.education.remediation.outcome_explanations import EXPLANATIONS
from src.jobs.contracts import JobSuccess, public_job_result
from src.jobs.remediation_job import _safe_failure_result


def test_known_manual_reason_survives_failed_job_public_projection():
    issues = [{"id": "heading"}, {"id": "root"}]
    result = SimpleNamespace(
        manual_issues=[
            {
                "issue_id": "heading",
                "reason": "Heading cannot be bound to a distinct complete source text run.",
            }
        ],
        fixed_issues=[{"issue_id": "root"}],
    )
    counts = outcome_accounting(
        issues, result, published=False, withheld_reason="output_verification_failed"
    )
    public = public_job_result(_safe_failure_result("manual_required", counts, None))
    manual, withheld = public["issue_outcomes"]
    assert manual["reason_code"] == "source_text_run_ambiguous"
    assert manual["attempt"] == "not_applied"
    assert "source document" in manual["next_step"]
    assert withheld["reason_code"] == "output_verification_failed"
    assert withheld["attempt"] == "candidate_change"
    assert public["fixed_count"] == 0 and public["withheld_count"] == 1


def test_withheld_for_introduced_pdfua_failures_says_so_and_is_not_a_retry_hint():
    result = SimpleNamespace(fixed_issues=[{"issue_id": "equation"}])
    counts = outcome_accounting(
        [{"id": "equation"}],
        result,
        published=False,
        withheld_reason="output_adds_pdfua_failures",
    )
    public = public_job_result(
        _safe_failure_result("remediation_artifact_unavailable", counts, None)
    )
    [withheld] = public["issue_outcomes"]
    assert withheld["status"] == "withheld"
    assert withheld["reason_code"] == "output_adds_pdfua_failures"
    assert withheld["attempt"] == "candidate_change"
    assert "PDF/UA problems the source document did not have" in withheld["reason"]
    assert "original is unchanged" in withheld["reason"]
    assert "is unlikely to change the result" in withheld["next_step"]
    assert "retrying" not in withheld["next_step"]
    assert public["fixed_count"] == 0 and public["withheld_count"] == 1


def test_public_reasons_are_canonical_and_never_include_raw_diagnostics():
    private = "SECRET /app/uploads/private.pdf token=abc"
    records = [
        {
            "source_index": 0,
            "status": "manual",
            "reason_code": "saved_finding_unresolved",
            "reason": private,
            "next_step": private,
            "attempt": private,
        }
    ]
    projected = JobSuccess({"issue_outcomes": records}).result["issue_outcomes"]
    assert private not in str(projected)
    assert projected[0]["attempt"] == "candidate_change"
    assert "rescan" in projected[0]["reason"]
    records[0]["reason_code"] = private
    assert public_job_result({"issue_outcomes": records})["issue_outcomes"] == [
        {"source_index": 0, "status": "manual"}
    ]


def test_unknown_exception_and_ambiguous_identity_do_not_invent_reasons():
    result = SimpleNamespace(
        manual_issues=[
            {"issue_id": "a", "reason": "provider failed token=SECRET /private/key"}
        ]
    )
    outcomes = outcome_accounting([{"id": "a"}], result, published=False)[
        "issue_outcomes"
    ]
    assert outcomes[0]["reason_code"] == "manual_reason_unrecorded"
    assert "SECRET" not in str(outcomes)
    ambiguous = outcome_accounting([{"id": "a"}, {"id": "a"}], result, published=False)
    assert all(
        item["status"] == "unreported" and "reason" not in item
        for item in ambiguous["issue_outcomes"]
    )


def test_reason_cannot_promote_or_relabel_an_outcome():
    for status, code in [
        ("fixed", "output_verification_failed"),
        ("fixed", "output_adds_pdfua_failures"),
        ("manual", "output_adds_pdfua_failures"),
        ("withheld", "category_disabled"),
        ("unreported", "source_text_run_ambiguous"),
    ]:
        outcome = public_job_result(
            {
                "issue_outcomes": [
                    {"source_index": 0, "status": status, "reason_code": code}
                ]
            }
        )["issue_outcomes"][0]
        assert outcome == {"source_index": 0, "status": status}


EQUATION_REASONS = {
    "image_equation_existing_owner_unsupported": (
        "already inside other marked content, such as a tagged element or an artifact",
        "not_attempted",
    ),
    "image_equation_draw_unresolved": (
        "could not be matched to exactly one drawing operation",
        "not_attempted",
    ),
    "image_equation_alt_text_deferred": (
        "left to the equation fix, and that fix did not complete",
        "not_applied",
    ),
    "image_equation_alt_text_withheld": (
        "recognised as an equation that could not be verified, so no description was written and it needs review",
        "not_applied",
    ),
    "printed_equation_verifier_unavailable": (
        "no second, independent reading was available to confirm it",
        "not_applied",
    ),
    "printed_equation_verifier_failed": (
        "a second, independent reading could not be completed",
        "not_applied",
    ),
    "printed_equation_verifier_declined": (
        "a second, independent reading did not find a printed equation in it",
        "not_applied",
    ),
    "printed_equation_semantic_disagreement": (
        "Two independent readings of the equation did not agree",
        "not_applied",
    ),
    "image_equation_alt_text_ambiguous": (
        "its notation cannot be turned into reliable plain-language alternative text",
        "not_applied",
    ),
    "image_description_math_not_speakable": (
        "contained mathematical notation, such as LaTeX, that a screen reader would announce symbol by symbol",
        "not_applied",
    ),
}


@pytest.mark.parametrize("code", sorted(EQUATION_REASONS))
def test_image_equation_manual_reasons_reach_the_public_projection(code):
    """The reason strings the remediator records become canonical public text."""
    result = SimpleNamespace(
        manual_issues=[{"issue_id": "equation", "reason": code}], fixed_issues=[]
    )
    counts = outcome_accounting([{"id": "equation"}], result, published=False)
    public = public_job_result(_safe_failure_result("manual_required", counts, None))
    (outcome,) = public["issue_outcomes"]

    needle, attempt = EQUATION_REASONS[code]
    assert outcome["status"] == "manual"
    assert outcome["reason_code"] == code
    assert outcome["attempt"] == attempt
    assert needle in outcome["reason"]
    assert (outcome["reason"], outcome["next_step"], outcome["attempt"]) == (
        EXPLANATIONS[code]
    )


def test_equation_not_read_reasons_say_the_equation_was_not_read_or_changed():
    for code in (
        "image_equation_existing_owner_unsupported",
        "image_equation_draw_unresolved",
    ):
        assert "so the equation was not read or changed" in EXPLANATIONS[code][0]
    # The deferred and withheld reasons make no such claim: the equation was read.
    for code in (
        "image_equation_alt_text_deferred",
        "image_equation_alt_text_withheld",
    ):
        assert "not read" not in EXPLANATIONS[code][0]


def test_second_reading_reasons_say_what_was_and_was_not_established():
    for code in (
        "printed_equation_verifier_unavailable",
        "printed_equation_verifier_failed",
        "printed_equation_verifier_declined",
    ):
        reason, next_step, attempt = EXPLANATIONS[code]
        # The first reading matched the image; nothing was claimed beyond that.
        assert "was read once and matched its image" in reason
        assert reason.endswith("so it was not applied.")
        assert "manually" in next_step
        assert attempt == "not_applied"
    reason, next_step, _ = EXPLANATIONS["printed_equation_semantic_disagreement"]
    assert "neither was applied" in reason
    assert "Formula tag and alt text manually" in next_step
    reason, next_step, _ = EXPLANATIONS["image_equation_alt_text_ambiguous"]
    assert "was read, but its notation" in reason
    assert "alternative text yourself" in next_step


@pytest.mark.parametrize(
    "reason",
    [
        "image_equation_something_else",
        "equation_verification_failed",
        "Image_equation_draw_unresolved",
        " image_equation_draw_unresolved",
        "image_equation_draw_unresolved: provider failed token=SECRET",
        "image_equation_alt_text_withheld ",
    ],
)
def test_other_equation_reasons_still_map_to_unrecorded(reason):
    result = SimpleNamespace(manual_issues=[{"issue_id": "a", "reason": reason}])
    (outcome,) = outcome_accounting([{"id": "a"}], result, published=False)[
        "issue_outcomes"
    ]
    assert outcome["reason_code"] == "manual_reason_unrecorded"
    assert "SECRET" not in str(outcome)


def test_oversized_equation_reason_has_its_own_honest_public_explanation():
    issues = [{"id": "oversized"}, {"id": "failed"}]
    result = SimpleNamespace(
        manual_issues=[
            {"issue_id": "oversized", "reason": "equation_too_large_for_verification"},
            {"issue_id": "failed", "reason": "equation_verification_failed"},
        ],
        fixed_issues=[],
    )
    counts = outcome_accounting(issues, result, published=False)
    public = public_job_result(_safe_failure_result("manual_required", counts, None))
    oversized, failed = public["issue_outcomes"]

    assert oversized["reason_code"] == "equation_too_large_for_verification"
    assert oversized["attempt"] == "not_applied"
    assert "larger than" in oversized["reason"]
    assert "could not be compared" in oversized["reason"]
    assert "alt text manually" in oversized["next_step"]
    # Other equation verification failures still do not invent a public reason.
    assert failed["reason_code"] == "manual_reason_unrecorded"
