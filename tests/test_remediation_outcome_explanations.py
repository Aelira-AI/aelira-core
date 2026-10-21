"""Durable, source-bound explanations must survive without private diagnostics."""

from types import SimpleNamespace

from src.education.remediation.outcome_accounting import outcome_accounting
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
    # Other verification failures keep their own reason; the size explanation
    # is never borrowed for them.
    assert failed["reason_code"] == "equation_verification_failed"
    assert "larger than" not in failed["reason"]
    assert "did not pass or could not run" in failed["reason"]


def test_withheld_alt_text_client_has_an_accurate_public_explanation():
    issues = [{"id": "equation"}, {"id": "embedded-image"}]
    result = SimpleNamespace(
        manual_issues=[
            {"issue_id": "equation", "reason": "alt_text_client_unavailable"},
            {"issue_id": "embedded-image", "reason": "alt_text_client_unavailable"},
        ],
        fixed_issues=[],
    )
    counts = outcome_accounting(issues, result, published=False)
    public = public_job_result(_safe_failure_result("manual_required", counts, None))

    for outcome in public["issue_outcomes"]:
        assert outcome["reason_code"] == "alt_text_client_unavailable"
        assert outcome["attempt"] == "not_attempted"
        assert "authorized for image descriptions" in outcome["reason"]
        assert "equation recognition" in outcome["reason"]
        assert "AI policy" in outcome["next_step"]
        # The generic missing-provider guidance must not stand in for it.
        assert "image AI configuration" not in outcome["next_step"]


EQUATION_REASONS = {
    "equation_image_source_rejected": "not_attempted",
    "equation_region_source_rejected": "not_attempted",
    "equation_verifier_unavailable": "not_attempted",
    "equation_recognition_rejected": "not_applied",
    "handwritten_equation_recognition_rejected": "not_applied",
    "not_printed_equation": "not_applied",
    "not_handwritten_equation": "not_applied",
    "handwritten_math_not_eligible": "not_applied",
    "handwritten_math_suitability_rejected": "not_applied",
    "handwritten_notation_unsupported": "not_applied",
    "hmer_unavailable": "not_applied",
    "equation_verification_failed": "not_applied",
    "equation_verification_mismatch": "not_applied",
    "handwritten_equation_verification_failed": "not_applied",
    "handwritten_equation_verification_mismatch": "not_applied",
    "image_equation_conversion_failed": "not_applied",
    "math_fix_failed": "not_applied",
}


def test_every_math_fixer_refusal_has_its_own_public_explanation():
    import re
    from pathlib import Path

    from src.education.remediation.outcome_explanations import manual_explanation

    source = Path(__file__).resolve().parents[1] / (
        "src/education/remediation/math_fixer.py"
    )
    emitted = set(re.findall(r'error="([a-z0-9_]+)"', source.read_text()))
    # Every code MathFixer reports as a literal is covered here.
    assert emitted - {"alt_text_client_unavailable"} == set(EQUATION_REASONS)
    reasons = set()
    for code, attempt in EQUATION_REASONS.items():
        explanation = manual_explanation({"reason": code})
        assert explanation["reason_code"] == code
        assert explanation["attempt"] == attempt
        assert "accessible math" in explanation["next_step"]
        reasons.add(explanation["reason"])
    assert len(reasons) == len(EQUATION_REASONS)


def test_unverified_equation_association_has_a_public_explanation():
    from src.education.remediation.outcome_explanations import manual_explanation

    explanation = manual_explanation(
        {"reason": "Verified image equation awaits exact content association"}
    )
    assert explanation["reason_code"] == "equation_association_unverified"
    assert explanation["attempt"] == "not_applied"
    assert "saved PDF" in explanation["reason"]
