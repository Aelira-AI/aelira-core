"""User-facing scores require comparable saved-file scanner measurements."""

import pytest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.education.remediation.score_reporting import measured_score, score_fields


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["original_file_missing", "output_scan_failed", "incomplete_comparison"]
)
async def test_failure_reason_survives_queue_terminal_persistence_and_api(reason):
    from src.api.education.remediation_routes import _public_job_shape
    from src.db.models import Scan, ScanResult
    from src.jobs.job_processor import ClaimedJob, JobProcessor
    from src.jobs.registry import adapt_legacy_handler
    from src.jobs.remediation_job import _commit_terminal_failure

    scan = Scan(id="scan-failure", scan_type="PDF")
    baseline = ScanResult(scan_id=scan.id, compliance_score=95.7)
    job = SimpleNamespace(
        id="job-failure",
        status="processing",
        progress=0,
        created_at=None,
        updated_at=None,
        started_at=None,
        completed_at=None,
        last_error_code=None,
    )
    db = MagicMock()
    db.get.return_value = job

    async def handler(job, session, _tokens):
        await _commit_terminal_failure(
            job,
            session,
            "manual_required",
            scan=scan,
            result={
                "original_compliance_score": 95.7,
                "remediated_compliance_score": 100,
                "score_verified": True,
                "score_verification_reason": reason,
                "error_detail": "untrusted diagnostic detail",
            },
        )

    result = await adapt_legacy_handler(handler)(
        SimpleNamespace(job_id=job.id), db, None
    )
    claim = ClaimedJob(job.id, "remediate", {}, "claim", "worker", 1, 1)
    values = JobProcessor()._finish_values(claim, result, external_effect_state=None)
    assert values["status"] == "failed"
    assert values["result_data"]["score_verification_reason"] == reason
    assert values["result_data"]["score_verified"] is False
    assert "untrusted diagnostic detail" not in str(values)
    for key, value in values.items():
        setattr(job, key, value)
    records = {Scan: scan, ScanResult: baseline}
    db.query.side_effect = lambda model: SimpleNamespace(
        filter=lambda *_: SimpleNamespace(first=lambda: records[model])
    )
    response = _public_job_shape(db, job, scan.id)
    assert response["score_verification_reason"] == reason
    assert response["original_score"] == 95.7
    assert response["remediated_score"] is None
    assert response["score_verified"] is False
    assert response["download_available"] is False


def receipt(source=95.7, output=98):
    return {
        "method_version": "pdf-strict-v1",
        "source_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "source_score": source,
        "output_score": output,
    }


def measured_result(source=95.7, output=98):
    return {
        "score_verified": True,
        "score_provenance": "scanner_rescan",
        "original_compliance_score": source,
        "remediated_compliance_score": output,
        "score_measurement": receipt(source, output),
    }


@pytest.mark.parametrize(
    "value", [None, True, "95", float("nan"), float("inf"), -1, 101]
)
def test_invalid_score_is_unavailable(value):
    assert measured_score(value) is None


def test_rescan_regression_is_reported_without_clamping():
    fields = score_fields(
        {
            "score_provenance": "scanner_rescan",
            "original_compliance_score": 95.7,
            "remediated_compliance_score": 80.3,
            "score_measurement": receipt(95.7, 80.3),
        },
        original_score=95.7,
    )
    assert fields["original_compliance_score"] == 95.7
    assert fields["remediated_compliance_score"] == 80.3
    assert fields["compliance_improvement"] == pytest.approx(-15.4)
    assert fields["score_verified"] is True


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"remediated_compliance_score": 100, "fixed_count": 3},
        {"remediated_compliance_score": 95, "score_verified": True},
        {
            "score_provenance": "scanner_rescan",
            "original_compliance_score": 75,
            "remediated_compliance_score": 95,
        },
        {
            "score_provenance": "scanner_rescan",
            "original_compliance_score": 95.7,
            "remediated_compliance_score": float("nan"),
        },
    ],
)
def test_estimates_legacy_and_incomparable_measurements_are_unavailable(result):
    fields = score_fields(result, original_score=95.7)
    assert fields["original_compliance_score"] == 95.7
    assert fields["remediated_compliance_score"] is None
    assert fields["compliance_improvement"] is None
    assert fields["score_verified"] is False


def test_zero_is_a_real_measurement():
    fields = score_fields(
        {
            "score_provenance": "scanner_rescan",
            "original_compliance_score": 49,
            "remediated_compliance_score": 0,
            "score_measurement": receipt(49, 0),
        },
        original_score=49,
    )
    assert fields["remediated_compliance_score"] == 0
    assert fields["score_verified"] is True


def test_distinct_displayed_baselines_are_not_comparable():
    fields = score_fields(
        {
            "original_compliance_score": 95.6,
            "remediated_compliance_score": 98,
            "score_provenance": "scanner_rescan",
            "score_measurement": receipt(95.6, 98),
        },
        original_score=95.7,
    )
    assert fields["score_verified"] is False


def test_generic_job_projection_does_not_publish_legacy_estimates():
    from src.jobs.contracts import public_job_result

    projected = public_job_result(
        {"original_compliance_score": 75, "remediated_compliance_score": 95}
    )
    assert "original_compliance_score" not in projected
    assert "remediated_compliance_score" not in projected
    assert projected["score_verified"] is False


@pytest.mark.parametrize("scan_type", ["WEBSITE", "CANVAS_CONTENT"])
def test_static_html_measurement_cannot_certify_browser_scan(scan_type):
    fields = score_fields(
        {
            "original_compliance_score": 80,
            "remediated_compliance_score": 95,
            "score_provenance": "scanner_rescan",
            "score_measurement": receipt(80, 95),
        },
        original_score=80,
        source_scan_type=scan_type,
    )
    assert fields["original_compliance_score"] == 80
    assert fields["remediated_compliance_score"] is None
    assert fields["score_verified"] is False


def test_generic_job_projection_preserves_verified_regression():
    from src.jobs.contracts import public_job_result

    projected = public_job_result(
        {
            "score_verified": True,
            "original_compliance_score": 95.7,
            "remediated_compliance_score": 95,
            "score_provenance": "scanner_rescan",
            "score_measurement": receipt(95.7, 95),
        }
    )
    assert projected["score_verified"] is True
    assert projected["compliance_improvement"] == -0.7


def test_generic_projection_cannot_certify_an_unapproved_receipt():
    from src.jobs.contracts import public_job_result

    result = measured_result()
    result.pop("score_verified")
    assert public_job_result(result)["score_verified"] is False


@pytest.mark.parametrize(
    "method", ["office-word-strict-v1", "code-static-v1", "canvas-axe-v1"]
)
def test_measurement_method_must_match_source_scanner(method):
    result = measured_result()
    result["score_measurement"]["method_version"] = method
    fields = score_fields(result, original_score=95.7, source_scan_type="PDF")
    assert fields["score_verified"] is False
    assert fields["score_verification_reason"] == "unsupported_scan_type"


def test_huge_json_integer_is_not_a_score():
    from src.education.remediation.score_measurement import valid_measurement

    assert measured_score(10**400) is None
    assert valid_measurement(receipt(10**400, 90)) is None


def test_public_worker_keeps_automatic_upload_disabled():
    import ast

    source = Path(__file__).parents[1] / "src/jobs/remediation_job.py"
    tree = ast.parse(source.read_text())
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "upload_job_id"
            for target in node.targets
        )
    ]
    assert assignments
    assert all(
        isinstance(node.value, ast.Constant) and node.value.value is None
        for node in assignments
    )
    upload_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and any(
            keyword.arg == "job_type"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "upload"
            for keyword in node.keywords
        )
    ]
    assert upload_calls == []


@pytest.mark.parametrize(
    "measurement",
    [
        None,
        {},
        {"method_version": "unknown"},
        {**receipt(), "source_sha256": "invalid"},
        {**receipt(), "output_score": float("inf")},
    ],
)
def test_invalid_or_missing_receipt_cannot_verify_a_pair(measurement):
    result = {**measured_result(), "score_measurement": measurement}
    fields = score_fields(result, original_score=95.7)
    assert fields["score_verified"] is False
    assert fields["remediated_compliance_score"] is None
    assert fields["score_measurement"] is None
    assert fields["score_verification_reason"] == "incomplete_comparison"


def test_public_projection_roundtrip_preserves_only_valid_receipt_fields():
    from src.jobs.contracts import public_job_result

    result = measured_result()
    result["score_measurement"]["diagnostic"] = "unrecognized diagnostic detail"
    original = deepcopy(result)
    projected = public_job_result(result)
    assert projected["score_verified"] is True
    assert projected["score_measurement"] == receipt()
    assert public_job_result(projected) == projected
    assert result == original


@pytest.mark.parametrize(
    "reason",
    ["unknown diagnostic", "", ["output_scan_failed"], {"code": "output_scan_failed"}],
)
def test_public_projection_sanitizes_unknown_diagnostic_strings(reason):
    from src.jobs.contracts import public_job_result

    result = {"score_verification_reason": reason, "remediated_compliance_score": 100}
    projected = public_job_result(result)
    assert projected["score_verification_reason"] == "legacy_unverified"
    assert projected["score_verified"] is False
    assert projected["score_measurement"] is None


@pytest.mark.parametrize("key", ["source_sha256", "output_sha256"])
def test_persisted_artifact_hash_mismatch_rejects_score(key):
    fields = score_fields(measured_result(), original_score=95.7, **{key: "c" * 64})
    assert fields["score_verification_reason"] == "artifact_mismatch"
    assert fields["score_verified"] is False
    assert fields["remediated_compliance_score"] is None


def test_legacy_job_status_preserves_stored_baseline_without_mutation():
    from src.api.education.remediation_routes import _public_job_shape
    from src.db.models import Scan, ScanResult

    baseline = ScanResult(scan_id="scan-legacy", compliance_score=95.7)
    scan = Scan(id="scan-legacy", scan_type="PDF")
    result = {
        "original_compliance_score": 75,
        "remediated_compliance_score": 100,
        "fixed_count": 3,
        "manual_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
    }
    original = deepcopy(result)
    job = SimpleNamespace(
        id="job-legacy",
        status="completed",
        result_data=result,
        progress=100,
        created_at=None,
        updated_at=None,
        started_at=None,
        completed_at=None,
        last_error_code=None,
    )
    records = {ScanResult: baseline, Scan: scan}
    db = MagicMock()
    db.query.side_effect = lambda model: SimpleNamespace(
        filter=lambda *_args: SimpleNamespace(first=lambda: records[model])
    )

    response = _public_job_shape(db, job, "scan-legacy")

    assert response["original_score"] == 95.7
    assert response["remediated_score"] is None
    assert response["improvement"] is None
    assert response["score_verified"] is False
    assert response["score_verification_reason"] == "legacy_unverified"
    assert response["score_measurement"] is None
    assert response["human_review_required"] is True
    assert response["download_available"] is False
    assert baseline.compliance_score == 95.7
    assert job.result_data == original
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "ownership, reason",
    [
        ("owned", None),
        ("missing", "output_file_missing"),
        ("foreign", "artifact_mismatch"),
        ("local", None),
        ("local_foreign_provider", "artifact_mismatch"),
        ("cloud_missing_job", "artifact_mismatch"),
        ("local_foreign_job_provider", "artifact_mismatch"),
        ("cloud_foreign_provider", "artifact_mismatch"),
    ],
)
def test_job_score_requires_owned_artifact_receipt(monkeypatch, ownership, reason):
    from src.api.education import remediation_routes as routes
    from src.db.models import Scan, ScanResult

    baseline = ScanResult(scan_id="scan-1", compliance_score=95.7)
    scan = Scan(id="scan-1", scan_type="PDF", file_hash="a" * 64)
    raw = {**measured_result(), "artifact_id": "artifact-1"}
    original = deepcopy(raw)
    job = SimpleNamespace(
        id="job-1",
        department_id="dept-1",
        cloud_file_id=None if ownership.startswith("local") else "cloud-1",
        provider=(
            "local"
            if ownership.startswith("local")
            and ownership != "local_foreign_job_provider"
            else "canvas"
        ),
        status="completed",
        result_data=raw,
        progress=100,
        created_at=None,
        updated_at=None,
        started_at=None,
        completed_at=None,
        last_error_code=None,
    )
    artifact = (
        None
        if ownership == "missing"
        else SimpleNamespace(
            id="artifact-1",
            sha256="b" * 64,
            scan_id="other-scan" if ownership == "foreign" else "scan-1",
            department_id="dept-1",
            cloud_file_id=job.cloud_file_id,
            provider=(
                "local"
                if ownership
                in {"local", "local_foreign_job_provider", "cloud_foreign_provider"}
                else "canvas"
            ),
            remediation_job_id=(
                None
                if ownership.startswith("local") or ownership == "cloud_missing_job"
                else "job-1"
            ),
        )
    )
    db = MagicMock()
    records = {ScanResult: baseline, Scan: scan}
    db.query.side_effect = lambda model: SimpleNamespace(
        filter=lambda *_: SimpleNamespace(first=lambda: records[model])
    )
    db.get.return_value = artifact
    monkeypatch.setattr(routes, "_artifact_is_downloadable", lambda *_: (False, None))
    response = routes._public_job_shape(db, job, "scan-1")
    assert response["score_verified"] is (reason is None)
    assert response["score_verification_reason"] == reason
    assert response["remediated_score"] == (98 if reason is None else None)
    assert response["original_score"] == 95.7
    assert raw == original
    db.add.assert_not_called()
