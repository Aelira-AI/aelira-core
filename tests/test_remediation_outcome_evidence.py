"""Saved-file finding checks and human review are independent durable evidence."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.education.remediation.outcome_accounting import outcome_accounting
from src.jobs.contracts import JobSuccess, public_job_result
from src.jobs.remediation_subprocess import SubprocessRemediationResult, _json_record

SOURCE = "a" * 64
OUTPUT = "b" * 64


def _receipt(identifier="image"):
    return {
        "method_version": "pdf-finding-presence-v1",
        "issue_id": identifier,
        "source_sha256": SOURCE,
        "output_sha256": OUTPUT,
    }


def _public_receipt(index=0, scope="original_scan"):
    value = _receipt()
    value.pop("issue_id")
    return {**value, "source_index": index, "source_index_scope": scope}


def test_false_and_unknown_evidence_remain_distinct_through_public_boundary():
    result = SimpleNamespace(
        fixed_issues=[
            {
                "issue_id": "failed-check",
                "verification_passed": False,
                "needs_review": True,
            },
            {"issue_id": "legacy"},
        ]
    )
    outcomes = outcome_accounting(
        [{"id": "failed-check"}, {"id": "legacy"}], result, published=True
    )
    public = public_job_result(JobSuccess(outcomes).result)["issue_outcomes"]
    assert public[0]["verification_passed"] is False
    assert public[0]["needs_review"] is True
    assert "verification_passed" not in public[1]
    assert "needs_review" not in public[1]


@pytest.mark.parametrize(
    "reason,code",
    [
        (
            "The image occurrence could not be uniquely bound to an existing Figure.",
            "image_occurrence_unbound",
        ),
        (
            "The existing Figure has ambiguous, shared or unsupported image ownership.",
            "image_ownership_unsupported",
        ),
        (
            "Decorative image artifact conversion requires source-level review.",
            "decorative_image_requires_review",
        ),
        (
            "reading_order_candidate_still_unverified",
            "reading_order_candidate_still_unverified",
        ),
    ],
)
def test_known_manual_guards_publish_authored_guidance(reason, code):
    result = SimpleNamespace(manual_issues=[{"issue_id": "a", "reason": reason}])
    outcomes = outcome_accounting([{"id": "a"}], result, published=True)
    row = public_job_result(JobSuccess(outcomes).result)["issue_outcomes"][0]
    assert row["reason_code"] == code
    assert row["next_step"]
    assert "verification_passed" not in row


def test_bound_saved_verification_survives_child_parent_durable_public_roundtrip():
    record = SimpleNamespace(
        issue_id="image",
        verification_passed=True,
        needs_review=True,
        saved_file_verification=_receipt(),
    )
    child = SubprocessRemediationResult(values={"fixed_issues": [_json_record(record)]})
    outcomes = outcome_accounting(
        [{"id": "image"}],
        child,
        published=True,
        source_sha256=SOURCE,
        output_sha256=OUTPUT,
        original_issues=[{"id": "original-image"}],
    )
    row = public_job_result(JobSuccess(outcomes).result)["issue_outcomes"][0]
    assert row["verification_passed"] is True
    assert row["verification_scope"] == "saved_file_finding"
    assert row["needs_review"] is True
    assert row["saved_file_verification"] == _public_receipt()
    assert "semantic" not in str(row)


def test_reviewed_subset_preserves_proven_original_index_without_inventing_outcomes():
    from src.education.remediation.reviewed_pdf import recover_reviewed_pdf_issues
    from src.education.remediation.outcome_evidence import bind_outcome_evidence

    source = [
        {"category": "alt_text", "message": "Missing first", "location": "Page 1"},
        {"category": "alt_text", "message": "Missing second", "location": "Page 2"},
        {"category": "alt_text", "message": "Missing third", "location": "Page 3"},
    ]
    selected = [
        {
            "id": "source-2",
            "category": "alt_text",
            "description": "Missing third",
            "location": "Page 3",
            "fixed_content": "Reviewed third image",
            "original_source_index": 0,
        }
    ]
    issues = recover_reviewed_pdf_issues(selected, source)
    assert issues[0]["original_source_index"] == 2
    child = SubprocessRemediationResult(
        values={
            "fixed_issues": [
                {
                    "issue_id": "source-2",
                    "verification_passed": True,
                    "needs_review": True,
                    "saved_file_verification": _receipt("source-2"),
                    "original_source_index": 0,
                }
            ]
        }
    )
    accounting = outcome_accounting(
        issues,
        child,
        published=True,
        source_sha256=SOURCE,
        output_sha256=OUTPUT,
        source_index_scope="approved_subset",
        source_original_indices=[issue["original_source_index"] for issue in issues],
    )
    public = public_job_result(JobSuccess(accounting).result)
    assert public["total_issues"] == 1
    assert len(public["issue_outcomes"]) == 1
    row = public["issue_outcomes"][0]
    assert row["source_index"] == 0
    assert row["original_source_index"] == 2
    assert row["saved_file_verification"]["original_source_index"] == 2
    assert (
        bind_outcome_evidence(
            row, status="fixed", source_sha256=SOURCE, output_sha256=OUTPUT
        )["original_source_index"]
        == 2
    )
    assert "original_source_index" not in bind_outcome_evidence(
        row, status="fixed", source_sha256="c" * 64, output_sha256=OUTPUT
    )


@pytest.mark.parametrize("index", [True, -1, 10_000, "2", 1])
def test_forged_original_index_cannot_relabel_saved_finding(index):
    row = {
        "source_index": 0,
        "source_index_scope": "approved_subset",
        "status": "fixed",
        "verification_passed": True,
        "original_source_index": index,
        "saved_file_verification": {
            **_public_receipt(scope="approved_subset"),
            "original_source_index": 2,
        },
    }
    public = public_job_result({"issue_outcomes": [row]})["issue_outcomes"][0]
    assert "original_source_index" not in public
    assert "verification_passed" not in public


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "c" * 64},
        {"output_sha256": "c" * 64},
        {"issue_id": "other"},
        {"method_version": "ai-says-fixed"},
        {"output_sha256": "/private/source.pdf"},
        {"source_sha256": True},
    ],
)
def test_mismatched_or_malformed_receipt_never_claims_verification(change):
    receipt = {**_receipt(), **change}
    result = SimpleNamespace(
        fixed_issues=[
            {
                "issue_id": "image",
                "verification_passed": True,
                "needs_review": True,
                "saved_file_verification": receipt,
            }
        ]
    )
    row = outcome_accounting(
        [{"id": "image"}],
        result,
        published=True,
        source_sha256=SOURCE,
        output_sha256=OUTPUT,
    )["issue_outcomes"][0]
    assert "verification_passed" not in row
    assert "saved_file_verification" not in row
    assert row["needs_review"] is True


@pytest.mark.parametrize("value", [True, 1, "true", None, {}, []])
def test_bare_positive_or_malformed_flag_is_not_saved_file_proof(value):
    row = public_job_result(
        {
            "issue_outcomes": [
                {
                    "source_index": 0,
                    "status": "fixed",
                    "issue_id": "image",
                    "verification_passed": value,
                    "needs_review": value,
                    "verification_scope": "semantic_conformance",
                }
            ]
        }
    )["issue_outcomes"][0]
    assert "verification_passed" not in row
    assert "verification_scope" not in row
    assert ("needs_review" in row) is (type(value) is bool)


@pytest.mark.parametrize("status", ["manual", "failed", "unreported", "withheld"])
def test_receipt_cannot_promote_nonfixed_outcome(status):
    row = public_job_result(
        {
            "issue_outcomes": [
                {
                    "source_index": 0,
                    "status": status,
                    "issue_id": "image",
                    "verification_passed": True,
                    "saved_file_verification": _public_receipt(),
                }
            ]
        }
    )["issue_outcomes"][0]
    assert row["status"] == status
    assert "verification_passed" not in row
    assert "saved_file_verification" not in row


def test_duplicate_claims_lose_evidence_and_unpublished_changes_cannot_verify():
    record = {
        "issue_id": "image",
        "verification_passed": True,
        "saved_file_verification": _receipt(),
        "needs_review": True,
    }
    for records, published, expected in [
        ([record, record], True, "unreported"),
        ([record], False, "withheld"),
    ]:
        row = outcome_accounting(
            [{"id": "image"}],
            SimpleNamespace(fixed_issues=records),
            published=published,
            source_sha256=SOURCE,
            output_sha256=OUTPUT,
        )["issue_outcomes"][0]
        assert row["status"] == expected
        assert "verification_passed" not in row
        assert "saved_file_verification" not in row
        if expected == "unreported":
            assert "needs_review" not in row


def test_source_index_binding_survives_historical_row_without_id():
    result = SimpleNamespace(
        fixed_issues=[
            {
                "issue_id": "source-0",
                "verification_passed": True,
                "saved_file_verification": _receipt("source-0"),
                "needs_review": False,
            }
        ]
    )
    outcomes = outcome_accounting(
        [{"id": "source-0"}],
        result,
        published=True,
        original_issues=[{}],
        source_sha256=SOURCE,
        output_sha256=OUTPUT,
    )
    row = public_job_result(JobSuccess(outcomes).result)["issue_outcomes"][0]
    assert row["verification_passed"] is True
    assert row["saved_file_verification"] == _public_receipt()
    assert row["needs_review"] is False
    assert "issue_id" not in row


@pytest.mark.parametrize(
    "change",
    [
        {"source_index": True},
        {"source_index": 1},
        {"source_index_scope": "approved_subset"},
        {"method_version": "semantic-conformance-v1"},
        {"output_sha256": "wrong"},
        {"reason": "/private/file.pdf"},
    ],
)
def test_forged_public_receipt_cannot_change_identity_or_scope(change):
    row = public_job_result(
        {
            "issue_outcomes": [
                {
                    "source_index": 0,
                    "source_index_scope": "original_scan",
                    "status": "fixed",
                    "verification_passed": True,
                    "needs_review": True,
                    "saved_file_verification": {**_public_receipt(), **change},
                }
            ]
        }
    )["issue_outcomes"][0]
    assert row["needs_review"] is True
    assert "verification_passed" not in row
    assert "saved_file_verification" not in row


def test_malformed_public_fields_are_ignored_without_dropping_other_rows():
    result = public_job_result(
        {
            "issue_outcomes": [
                {"source_index": 0, "status": []},
                {
                    "source_index": 1,
                    "status": "manual",
                    "reason_code": {},
                    "source_index_scope": [],
                },
            ]
        }
    )
    assert result["issue_outcomes"] == [{"source_index": 1, "status": "manual"}]


def test_duplicate_public_source_identity_cannot_claim_evidence():
    record = {
        "source_index": 0,
        "source_index_scope": "original_scan",
        "status": "fixed",
        "verification_passed": True,
        "needs_review": True,
        "saved_file_verification": _public_receipt(),
    }
    rows = public_job_result({"issue_outcomes": [record, record]})["issue_outcomes"]
    assert len(rows) == 2
    assert all(
        "verification_passed" not in row and "needs_review" not in row for row in rows
    )


def test_decorative_empty_alt_refusal_is_specific_and_does_not_change_pdf(tmp_path):
    import hashlib
    import pymupdf as fitz
    from src.education.remediation.base import (
        IssueCategory,
        IssueSeverity,
        RemediationConfig,
        RemediationIssue,
    )
    from src.education.remediation.pdf_remediator import PdfRemediator

    source = tmp_path / "source.pdf"
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.save(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    issue = RemediationIssue(
        id="decorative",
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.MEDIUM,
        description="Decorative image detected",
        metadata={"is_decorative": True},
    )
    remediator = PdfRemediator(str(source), [issue], RemediationConfig(use_ai=False))
    remediator._struct_tree = SimpleNamespace()
    remediator._generated_structure = False
    assert remediator._get_rule_based_fix(issue, object()) == ""
    assert remediator._apply_alt_text_fix(issue, object(), "") is False
    assert remediator._source_binding_refusals[issue.id] == (
        "Decorative image artifact conversion requires source-level review."
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("receipt_output", [OUTPUT, "c" * 64])
def test_public_job_requires_current_downloadable_artifact_for_positive_finding(
    monkeypatch, receipt_output
):
    from src.api.education import remediation_routes as routes
    from src.db.models import Scan, ScanResult

    scan = Scan(id="scan-1", scan_type="PDF", file_hash=SOURCE)
    baseline = ScanResult(scan_id=scan.id, compliance_score=80)
    artifact = SimpleNamespace(
        id="artifact-1",
        scan_id=scan.id,
        department_id="dept-1",
        cloud_file_id=None,
        remediation_job_id=None,
        provider="local",
        sha256=OUTPUT,
    )
    outcome = {
        "source_index": 0,
        "source_index_scope": "original_scan",
        "status": "fixed",
        "issue_id": "image",
        "verification_passed": True,
        "saved_file_verification": {
            **_public_receipt(),
            "output_sha256": receipt_output,
        },
    }
    job = SimpleNamespace(
        id="job-1",
        department_id="dept-1",
        cloud_file_id=None,
        provider="local",
        status="completed",
        result_data={
            "artifact_id": artifact.id,
            "fresh_score_comparison": {
                "method_version": "pdf-strict-v1",
                "source_sha256": SOURCE,
                "output_sha256": OUTPUT,
                "source_score": 80,
                "output_score": 90,
            },
            "issue_outcomes": [outcome],
        },
        progress=100,
        created_at=None,
        updated_at=None,
        started_at=None,
        completed_at=None,
        last_error_code=None,
    )
    records = {Scan: scan, ScanResult: baseline}
    db = MagicMock()
    db.query.side_effect = lambda model: SimpleNamespace(
        filter=lambda *_: SimpleNamespace(first=lambda: records[model])
    )
    db.get.return_value = artifact
    monkeypatch.setattr(
        routes, "_artifact_is_downloadable", lambda *_: (True, artifact)
    )

    response = routes._public_job_shape(db, job, scan.id)

    assert response["fresh_score_comparison"]["output_sha256"] == OUTPUT
    saved = response["issue_outcomes"][0]
    assert (saved.get("verification_passed") is True) is (receipt_output == OUTPUT)
    assert job.result_data["issue_outcomes"][0] == outcome


def test_terminal_failure_clears_positive_saved_file_finding_claim():
    from src.jobs.remediation_job import _safe_failure_result

    failed = _safe_failure_result(
        "remediation_failed",
        {
            "fixed_count": 1,
            "total_issues": 1,
            "issue_outcomes": [
                {
                    "source_index": 0,
                    "source_index_scope": "original_scan",
                    "status": "fixed",
                    "issue_id": "image",
                    "verification_passed": True,
                    "verification_scope": "saved_file_finding",
                    "saved_file_verification": _public_receipt(),
                }
            ],
        },
        None,
    )
    outcome = failed["issue_outcomes"][0]
    assert outcome["status"] == "withheld"
    assert "verification_passed" not in outcome
    assert "saved_file_verification" not in outcome
