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
