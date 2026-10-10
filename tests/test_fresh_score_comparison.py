"""Fresh paired scores stay separate from the historical scan baseline."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.api.education import remediation_routes as routes
from src.db.models import Scan, ScanResult
from src.education.remediation.score_reporting import (
    fresh_score_comparison,
    score_fields,
)
from src.jobs.contracts import public_job_result
from src.jobs import remediation_subprocess
from src.services.remediation_artifact_service import ArtifactError


def receipt(source=57.9, output=0):
    return {
        "method_version": "pdf-strict-v1",
        "source_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "source_score": source,
        "output_score": output,
    }


def raw_child():
    return {
        "score_verified": True,
        "score_provenance": "scanner_rescan",
        "original_compliance_score": 57.9,
        "remediated_compliance_score": 0,
        "score_measurement": receipt(),
    }


def ordinary_child_envelope(
    tmp_path, monkeypatch, *, receipt_change=None, explicit_verified=None
):
    """Exercise the normal worker serializer rather than a hand-built child dict."""
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    source.write_bytes(b"synthetic source")
    output.write_bytes(b"synthetic output")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    measurement = {
        **receipt(),
        "source_sha256": source_hash,
        "output_sha256": output_hash,
        **(receipt_change or {}),
    }
    result = SimpleNamespace(
        success=True,
        output_file=str(output),
        total_issues=1,
        fixed_count=1,
        manual_count=0,
        failed_count=0,
        skipped_count=0,
        original_compliance_score=57.9,
        remediated_compliance_score=0,
        score_provenance="scanner_rescan",
        score_measurement=measurement,
        score_verification_reason=None,
        improvement=-57.9,
        duration_seconds=1.0,
        verification_passed=True,
        fixed_issues=[],
        manual_issues=[],
        failed_issues=[],
    )
    if explicit_verified is not None:
        result.score_verified = explicit_verified

    def build_remediator(*_args, **_kwargs):
        return SimpleNamespace(remediate=lambda: result)

    monkeypatch.setattr(remediation_subprocess, "_build_remediator", build_remediator)
    envelope = remediation_subprocess._run_child(
        {
            "source_path": str(source),
            "work_dir": str(tmp_path),
            "scan_type": "PDF",
            "issues": [],
            "options": {},
        }
    )
    return json.loads(json.dumps(envelope)), source_hash, output_hash


def test_ordinary_child_certifies_fresh_zero_before_historical_baseline(
    monkeypatch, tmp_path
):
    child, source_hash, output_hash = ordinary_child_envelope(tmp_path, monkeypatch)
    assert child["score_verified"] is True
    fresh = fresh_score_comparison(
        child,
        source_scan_type="PDF",
        source_sha256=source_hash,
        output_sha256=output_hash,
    )
    assert fresh["source_score"] == 57.9
    assert fresh["output_score"] == 0
    child.update(
        score_fields(
            child,
            original_score=59.7,
            source_scan_type="PDF",
            source_sha256=source_hash,
            output_sha256=output_hash,
        )
    )
    assert child["original_compliance_score"] == 59.7
    assert child["remediated_compliance_score"] is None
    assert child["score_verified"] is False
    assert child["score_verification_reason"] == "baseline_mismatch"
    assert (
        fresh_score_comparison(
            {"fresh_score_comparison": fresh},
            source_scan_type="PDF",
            source_sha256=source_hash,
            output_sha256=output_hash,
        )
        == fresh
    )


@pytest.mark.parametrize(
    "receipt_change,explicit_verified,child_verified",
    [
        ({"source_sha256": "c" * 64}, None, True),
        ({"output_sha256": "c" * 64}, None, True),
        ({"source_sha256": "malformed"}, None, False),
        ({"output_sha256": "malformed"}, None, False),
        ({"method_version": "canvas-axe-v1"}, None, False),
        ({"output_score": 1}, None, False),
        (None, False, False),
    ],
)
def test_ordinary_child_refuses_invalid_or_explicitly_unverified_receipt(
    monkeypatch, tmp_path, receipt_change, explicit_verified, child_verified
):
    child, source_hash, output_hash = ordinary_child_envelope(
        tmp_path,
        monkeypatch,
        receipt_change=receipt_change,
        explicit_verified=explicit_verified,
    )
    assert child["score_verified"] is child_verified
    assert (
        fresh_score_comparison(
            child,
            source_scan_type="PDF",
            source_sha256=source_hash,
            output_sha256=output_hash,
        )
        is None
    )


def test_fresh_pair_survives_legacy_baseline_mismatch_and_reprojection():
    child = raw_child()
    fresh = fresh_score_comparison(
        child,
        source_scan_type="PDF",
        source_sha256="a" * 64,
        output_sha256="b" * 64,
    )
    assert fresh == receipt()
    child.update(score_fields(child, original_score=59.7, source_scan_type="PDF"))
    assert child["original_compliance_score"] == 59.7
    assert child["remediated_compliance_score"] is None
    assert child["score_verified"] is False
    assert child["score_verification_reason"] == "baseline_mismatch"
    child.update(score_fields(child, original_score=59.7, source_scan_type="PDF"))
    assert child["score_verification_reason"] == "baseline_mismatch"
    assert child["remediated_compliance_score"] is None
    assert (
        fresh_score_comparison(
            {"fresh_score_comparison": fresh},
            source_scan_type="PDF",
            source_sha256="a" * 64,
            output_sha256="b" * 64,
        )
        == fresh
    )
    assert public_job_result({"fresh_score_comparison": fresh}) is None
    projected = public_job_result({**child, "fresh_score_comparison": fresh})
    assert "fresh_score_comparison" not in projected
    assert public_job_result(projected) == projected


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "c" * 64},
        {"output_sha256": "c" * 64},
        {"source_sha256": ""},
        {"output_sha256": ""},
        {"method_version": "canvas-axe-v1"},
        {"source_score": 0},
        {"output_score": 1},
    ],
)
def test_raw_pair_rejects_wrong_hash_method_or_value(change):
    child = raw_child()
    child["score_measurement"] = {**receipt(), **change}
    assert (
        fresh_score_comparison(
            child,
            source_scan_type="PDF",
            source_sha256="a" * 64,
            output_sha256="b" * 64,
        )
        is None
    )


@pytest.mark.parametrize("scan_type", ["WEBSITE", "CANVAS_CONTENT", "IMAGE", None])
def test_fresh_pair_rejects_unsupported_scan_type(scan_type):
    assert (
        fresh_score_comparison(
            {"fresh_score_comparison": receipt()},
            source_scan_type=scan_type,
            source_sha256="a" * 64,
            output_sha256="b" * 64,
        )
        is None
    )


def _api_case(
    monkeypatch,
    *,
    artifact_changes=None,
    job_changes=None,
    scan_changes=None,
    receipt_changes=None,
    available=True,
    outcome_rows=None,
):
    raw = {
        **raw_child(),
        "artifact_id": "artifact-1",
        "fresh_score_comparison": {**receipt(), **(receipt_changes or {})},
        **({"issue_outcomes": outcome_rows} if outcome_rows is not None else {}),
    }
    original = deepcopy(raw)
    job = SimpleNamespace(
        id="job-1",
        department_id="dept-1",
        cloud_file_id="cloud-1",
        provider="canvas",
        status="completed",
        result_data=raw,
        progress=100,
        created_at=None,
        updated_at=None,
        started_at=None,
        completed_at=None,
        last_error_code=None,
    )
    for key, value in (job_changes or {}).items():
        setattr(job, key, value)
    scan = SimpleNamespace(scan_type="PDF", file_hash="a" * 64)
    for key, value in (scan_changes or {}).items():
        setattr(scan, key, value)
    artifact = SimpleNamespace(
        id="artifact-1",
        scan_id="scan-1",
        department_id="dept-1",
        cloud_file_id="cloud-1",
        remediation_job_id="job-1",
        provider="canvas",
        sha256="b" * 64,
    )
    for key, value in (artifact_changes or {}).items():
        setattr(artifact, key, value)
    db = MagicMock()
    records = {ScanResult: SimpleNamespace(compliance_score=59.7), Scan: scan}
    db.query.side_effect = lambda model: SimpleNamespace(
        filter=lambda *_: SimpleNamespace(first=lambda: records[model])
    )
    db.get.return_value = artifact
    monkeypatch.setattr(
        routes,
        "_artifact_is_downloadable",
        lambda *_: (available, artifact if available else None),
    )
    response = routes._public_job_shape(db, job, "scan-1")
    assert raw == original
    return response


def test_api_exposes_only_fresh_pair_while_historical_score_is_unchanged(monkeypatch):
    response = _api_case(monkeypatch)
    assert response["original_score"] == 59.7
    assert response["remediated_score"] is None
    assert response["improvement"] is None
    assert response["score_verified"] is False
    assert response["score_verification_reason"] == "baseline_mismatch"
    assert response["fresh_score_comparison"] == receipt()
    assert response["human_review_required"] is True


def _verified_outcome():
    return {
        "source_index": 0,
        "source_index_scope": "original_scan",
        "status": "fixed",
        "verification_passed": True,
        "needs_review": True,
        "saved_file_verification": {
            "method_version": "pdf-finding-presence-v1",
            "source_index": 0,
            "source_index_scope": "original_scan",
            "source_sha256": "a" * 64,
            "output_sha256": "b" * 64,
        },
    }


def test_api_preserves_bound_saved_finding_proof_independent_of_historical_score(
    monkeypatch,
):
    response = _api_case(monkeypatch, outcome_rows=[_verified_outcome()])
    row = response["issue_outcomes"][0]
    assert response["score_verified"] is False
    assert row["verification_passed"] is True
    assert row["verification_scope"] == "saved_file_finding"
    assert row["needs_review"] is True


@pytest.mark.parametrize("available", [True, False])
def test_api_binds_approved_subset_original_index_to_current_saved_evidence(
    monkeypatch, available
):
    outcome = _verified_outcome()
    outcome.update(source_index_scope="approved_subset", original_source_index=9)
    outcome["saved_file_verification"].update(
        source_index_scope="approved_subset", original_source_index=9
    )
    row = _api_case(monkeypatch, outcome_rows=[outcome], available=available)[
        "issue_outcomes"
    ][0]
    if available:
        assert row["source_index"] == 0
        assert row["original_source_index"] == 9
    else:
        assert "original_source_index" not in row


@pytest.mark.parametrize(
    "case",
    [
        {"scan_changes": {"file_hash": "c" * 64}},
        {"artifact_changes": {"sha256": "c" * 64}},
        {"artifact_changes": {"remediation_job_id": "other"}},
        {"artifact_changes": {"department_id": "other"}},
        {"receipt_changes": {"method_version": "canvas-axe-v1"}},
        {"job_changes": {"status": "failed"}},
        {"available": False},
    ],
)
def test_api_removes_stale_or_unbound_per_finding_verification(monkeypatch, case):
    row = _api_case(monkeypatch, outcome_rows=[_verified_outcome()], **case)[
        "issue_outcomes"
    ][0]
    assert "verification_passed" not in row
    assert "saved_file_verification" not in row
    assert row["needs_review"] is True


def test_api_does_not_recover_legacy_finding_proof_from_a_valid_aggregate_score(
    monkeypatch,
):
    row = _api_case(
        monkeypatch,
        outcome_rows=[
            {
                "source_index": 0,
                "source_index_scope": "original_scan",
                "status": "fixed",
            }
        ],
    )["issue_outcomes"][0]
    assert "verification_passed" not in row
    assert "needs_review" not in row


@pytest.mark.parametrize(
    "case",
    [
        {"scan_changes": {"file_hash": "c" * 64}},
        {"artifact_changes": {"sha256": "c" * 64}},
        {"artifact_changes": {"scan_id": "other"}},
        {"artifact_changes": {"department_id": "other"}},
        {"artifact_changes": {"cloud_file_id": "other"}},
        {"artifact_changes": {"remediation_job_id": "other"}},
        {"artifact_changes": {"provider": "other"}},
        {"artifact_changes": {"id": "other"}},
        {"job_changes": {"provider": "other"}},
        {"job_changes": {"status": "failed"}},
        {"job_changes": {"status": "cancelled"}},
        {"receipt_changes": {"method_version": "canvas-axe-v1"}},
        {"available": False},
    ],
)
def test_api_withholds_fresh_pair_for_wrong_authority_or_unavailable_output(
    monkeypatch, case
):
    assert _api_case(monkeypatch, **case)["fresh_score_comparison"] is None


@pytest.mark.parametrize("failure", ["missing", "expired", "unavailable"])
def test_artifact_resolution_blocks_fresh_pair(monkeypatch, failure):
    artifact = SimpleNamespace(id="artifact-1", sha256="b" * 64)
    job = SimpleNamespace(
        id="job-1",
        status="completed",
        department_id="dept-1",
        cloud_file_id="cloud-1",
        result_data={"artifact_id": "artifact-1"},
    )
    db = MagicMock()
    db.get.return_value = None if failure == "missing" else artifact
    monkeypatch.setattr(
        routes, "_artifact_requires_approval", lambda *_args, **_kw: False
    )
    service = SimpleNamespace(resolve_record=MagicMock())
    if failure != "missing":
        service.resolve_record.side_effect = ArtifactError(failure)
    monkeypatch.setattr(
        routes.RemediationArtifactService, "from_settings", lambda: service
    )
    assert routes._artifact_is_downloadable(db, job, "scan-1") == (False, None)
