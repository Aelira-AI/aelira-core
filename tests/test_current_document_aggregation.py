"""Current compliance is a projection of documents, not scan attempts."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

from src.db.models import Department, Scan, ScanStatus, ScanType, User
from src.db.scan_service import ScanService
from src.api.user_management import get_department_stats as get_admin_department_stats
from src.education.compliance_dashboard import ComplianceDashboard
from src.education.current_compliance import (
    _provider_scan_ids,
    project_current_documents,
)

NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def _scan(
    scan_id: str,
    *,
    department_id: str = "dept-a",
    file_hash: str | None = None,
    file_name: str = "document.pdf",
    status: ScanStatus = ScanStatus.COMPLETED,
    created_offset: int = 0,
    remediation_outcome: str | None = None,
):
    created_at = NOW + timedelta(minutes=created_offset)
    return SimpleNamespace(
        id=scan_id,
        department_id=department_id,
        document_id=None,
        file_hash=file_hash,
        file_name=file_name,
        scan_type=ScanType.PDF,
        status=status,
        remediation_outcome=remediation_outcome,
        created_at=created_at,
        completed_at=created_at if status is not ScanStatus.PENDING else None,
        pages=2,
        user_id="user-a",
    )


def _result(scan_id: str, score: float, issues: int):
    return SimpleNamespace(
        scan_id=scan_id,
        compliance_score=score,
        critical_issues=issues,
        high_issues=0,
        medium_issues=0,
        low_issues=0,
        issues=[{"severity": "critical"}] * issues,
    )


def _cloud_file(file_id: str, last_scan_id: str | None):
    return SimpleNamespace(
        id=file_id,
        department_id="dept-a",
        last_scan_id=last_scan_id,
    )


def test_duplicate_uploads_and_rescans_use_one_latest_verified_state():
    scans = [
        _scan("old", file_hash="a" * 64, created_offset=0),
        _scan("new", file_hash="a" * 64, created_offset=10),
        _scan(
            "failed",
            file_hash="a" * 64,
            status=ScanStatus.FAILED,
            created_offset=20,
        ),
    ]

    projection = project_current_documents(
        scans,
        [_result("old", 50, 4), _result("new", 90, 1)],
        [],
    )

    assert projection.historical_scan_count == 3
    assert projection.enrolled_document_count == 1
    assert projection.verified_document_count == 1
    assert projection.unverified_document_count == 0
    assert projection.current_documents[0].scan.id == "new"
    assert projection.average_compliance_score == 90
    assert projection.total_issues == 1


def test_document_without_verified_result_is_coverage_not_zero_score():
    projection = project_current_documents(
        [_scan("pending", file_hash="b" * 64, status=ScanStatus.PENDING)],
        [],
        [],
    )

    assert projection.enrolled_document_count == 1
    assert projection.verified_document_count == 0
    assert projection.unverified_document_count == 1
    assert projection.average_compliance_score is None
    assert projection.total_issues == 0


def test_cloud_file_identity_uses_only_its_authoritative_current_scan():
    scans = [
        _scan("cloud-old", file_hash="c" * 64, created_offset=0),
        _scan("cloud-current", file_hash="d" * 64, created_offset=10),
    ]

    projection = project_current_documents(
        scans,
        [_result("cloud-old", 40, 5), _result("cloud-current", 95, 1)],
        [_cloud_file("cloud-file-1", "cloud-current")],
        provider_scan_ids={"cloud-old", "cloud-current"},
    )

    assert projection.historical_scan_count == 2
    assert projection.enrolled_document_count == 1
    assert projection.current_documents[0].identity == "dept-a:cloud:cloud-file-1"
    assert projection.current_documents[0].scan.id == "cloud-current"
    assert projection.average_compliance_score == 95
    assert projection.total_issues == 1


def test_verified_remediation_changes_current_state_only_after_scan_promotion():
    source = _scan("source", file_hash="8" * 64, created_offset=0)
    verified_candidate = _scan(
        "verified-candidate", file_hash="9" * 64, created_offset=10
    )
    results = [_result("source", 60, 4), _result("verified-candidate", 95, 1)]
    cloud_file = _cloud_file("cloud-file-remediated", "source")

    before_promotion = project_current_documents(
        [source, verified_candidate],
        results,
        [cloud_file],
        provider_scan_ids={source.id, verified_candidate.id},
    )
    assert before_promotion.average_compliance_score == 60
    assert before_promotion.total_issues == 4

    cloud_file.last_scan_id = verified_candidate.id
    after_promotion = project_current_documents(
        [source, verified_candidate],
        results,
        [cloud_file],
        provider_scan_ids={source.id, verified_candidate.id},
    )
    assert after_promotion.enrolled_document_count == 1
    assert after_promotion.average_compliance_score == 95
    assert after_promotion.total_issues == 1


def test_failed_remediation_does_not_erase_the_original_verified_measurement():
    scan = _scan(
        "remediation-failed",
        file_hash="e" * 64,
        status=ScanStatus.FAILED,
        remediation_outcome="manual_required",
    )

    projection = project_current_documents(
        [scan],
        [_result(scan.id, 72, 3)],
        [],
    )

    assert projection.verified_document_count == 1
    assert projection.average_compliance_score == 72
    assert projection.total_issues == 3


def test_remediation_timestamp_does_not_make_an_older_duplicate_current():
    old = _scan("old", file_hash="7" * 64, created_offset=0)
    newer = _scan("newer", file_hash="7" * 64, created_offset=10)
    old.status = ScanStatus.FAILED
    old.remediation_outcome = "manual_required"
    old.completed_at = NOW + timedelta(days=1)

    projection = project_current_documents(
        [old, newer],
        [_result(old.id, 50, 5), _result(newer.id, 90, 1)],
        [],
    )

    assert projection.current_documents[0].scan.id == newer.id
    assert projection.average_compliance_score == 90


def test_failed_provider_job_scan_stays_history_not_a_phantom_document():
    failed = _scan("cloud-failed", status=ScanStatus.FAILED, created_offset=10)
    current = _scan("cloud-current", created_offset=0)
    current.document_id = "cloud-file-1"
    job = SimpleNamespace(
        cloud_file_id="cloud-file-1",
        job_type="scan",
        result_data=None,
    )

    projection = project_current_documents(
        [current, failed],
        [_result(current.id, 95, 1)],
        [_cloud_file("cloud-file-1", current.id)],
        provider_scan_ids=_provider_scan_ids([job], []),
    )

    assert projection.historical_scan_count == 2
    assert projection.enrolled_document_count == 1
    assert projection.verified_document_count == 1
    assert projection.unverified_document_count == 0


def test_new_failed_standalone_scan_remains_current_without_provider_evidence():
    failed = _scan("standalone-failed", status=ScanStatus.FAILED)
    failed.document_id = "standalone-document-1"
    failed.document_source = "standalone"
    unrelated_job = SimpleNamespace(
        cloud_file_id="cloud-file-1",
        job_type="upload",
        result_data={"scan_id": failed.id},
    )

    projection = project_current_documents(
        [failed],
        [],
        [],
        provider_scan_ids=_provider_scan_ids([unrelated_job], []),
    )

    assert projection.historical_scan_count == 1
    assert projection.enrolled_document_count == 1
    assert projection.verified_document_count == 0
    assert projection.unverified_document_count == 1
    assert projection.current_documents[0].identity == (
        "dept-a:document:standalone-document-1"
    )
    assert projection.current_documents[0].source_kind == "standalone_upload"


def test_legacy_hashless_unlinked_attempt_is_history_only():
    legacy_attempt = _scan("legacy-unlinked", status=ScanStatus.FAILED)

    projection = project_current_documents([legacy_attempt], [], [])

    assert projection.historical_scan_count == 1
    assert projection.enrolled_document_count == 0


def test_legacy_failed_standalone_with_content_identity_remains_current():
    failed = _scan(
        "legacy-standalone-failed",
        status=ScanStatus.FAILED,
        file_hash="f" * 64,
    )

    projection = project_current_documents([failed], [], [])

    assert projection.historical_scan_count == 1
    assert projection.enrolled_document_count == 1
    assert projection.unverified_document_count == 1


def test_new_standalone_scans_receive_an_opaque_document_identity():
    default = Scan.__table__.c.document_id.default

    assert default is not None
    value = default.arg(None)
    assert str(UUID(value)) == value

    source_default = Scan.__table__.c.document_source.default
    assert source_default is not None
    assert source_default.arg == "standalone"


def test_disconnected_provider_scan_remains_history_only():
    provider_scan = _scan("provider-history", file_hash="a" * 64)
    provider_scan.document_id = "deleted-cloud-file"
    provider_scan.document_source = "cloud_file"

    projection = project_current_documents(
        [provider_scan],
        [_result(provider_scan.id, 92, 2)],
        [],
    )

    assert projection.historical_scan_count == 1
    assert projection.enrolled_document_count == 0
    assert projection.verified_document_count == 0
    assert projection.total_issues == 0


def test_cloud_scan_handler_returns_failed_scan_identity(monkeypatch):
    from src.jobs.cloud_scan_job import CloudScanJob, handle_scan_job

    credential = SimpleNamespace(
        id="credential-1",
        department_id="dept-a",
        provider="google",
        is_active=True,
    )
    cloud_file = SimpleNamespace(
        id="cloud-file-1",
        department_id="dept-a",
        credential_id=credential.id,
        provider=credential.provider,
    )
    job = SimpleNamespace(
        id="job-1",
        department_id="dept-a",
        credential_id=credential.id,
        provider=credential.provider,
        provider_file_id="provider-file-1",
        cloud_file_id=cloud_file.id,
        payload={"scan_kind": "cloud_file"},
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [
        credential,
        cloud_file,
    ]
    failure = {
        "success": False,
        "scan_id": "failed-scan-1",
        "error": "Accessibility scan failed",
        "error_code": "SCAN_PROCESSING_FAILED",
    }

    async def fail_with_persisted_scan(_self, _db):
        return failure

    monkeypatch.setattr(CloudScanJob, "run", fail_with_persisted_scan)

    result = asyncio.run(handle_scan_job(job, db, MagicMock()))

    assert result == {**failure, "failure_kind": "indeterminate"}


def test_comprehensive_stats_keep_unverified_scores_unknown(monkeypatch):
    projection = project_current_documents(
        [_scan("pending", file_hash="6" * 64, status=ScanStatus.PENDING)],
        [],
        [],
    )
    monkeypatch.setattr(
        "src.education.compliance_dashboard.get_department_current_compliance",
        lambda _db, _department_id: projection,
    )

    db = MagicMock()

    def query(model):
        result = MagicMock()
        if model is Department:
            result.filter.return_value.first.return_value = SimpleNamespace(
                id="dept-a",
                name="Department A",
                institution="Institution A",
                country_code="AU",
                regulatory_framework=None,
                custom_deadline=None,
            )
        elif model is User:
            result.filter.return_value.count.return_value = 1
        else:  # pragma: no cover - catches an accidental historical query
            raise AssertionError(f"unexpected query for {model}")
        return result

    db.query.side_effect = query
    stats = ComplianceDashboard.get_department_compliance(db, "dept-a")

    assert stats.avg_compliance_score is None
    assert stats.min_compliance_score is None
    assert stats.max_compliance_score is None
    assert stats.days_until_deadline is None
    assert stats.on_track is None
    assert stats.deadline["applicability"] == "ongoing_no_date"
    report = stats.to_report_dict()
    assert report["april_2026_deadline"]["deprecated"] is True
    assert report["april_2026_deadline"]["framework"] == "AU_DDA"


def test_hashless_scans_are_not_grouped_by_mutable_filename():
    scans = [
        _scan("one", file_name="same-name.pdf", created_offset=0),
        _scan("two", file_name="same-name.pdf", created_offset=10),
    ]
    scans[0].document_id = "standalone-document-one"
    scans[1].document_id = "standalone-document-two"

    projection = project_current_documents(
        scans,
        [_result("one", 60, 4), _result("two", 80, 2)],
        [],
    )

    assert projection.enrolled_document_count == 2
    assert projection.verified_document_count == 2
    assert projection.average_compliance_score == 70
    assert projection.total_issues == 6


def test_website_identity_normalizes_host_default_port_and_fragment():
    first = _scan(
        "web-one",
        file_name="HTTPS://Example.EDU:443/path?view=1#first",
        created_offset=0,
    )
    second = _scan(
        "web-two",
        file_name="https://example.edu/path?view=1#second",
        created_offset=10,
    )
    first.scan_type = second.scan_type = ScanType.WEBSITE

    projection = project_current_documents(
        [first, second],
        [_result("web-one", 60, 4), _result("web-two", 80, 2)],
        [],
    )

    assert projection.enrolled_document_count == 1
    assert projection.current_documents[0].identity == (
        "dept-a:url:https://example.edu/path?view=1"
    )
    assert projection.current_documents[0].scan.id == "web-two"


def test_identical_hashes_remain_tenant_scoped():
    dept_a = _scan("a", department_id="dept-a", file_hash="f" * 64)
    dept_b = _scan("b", department_id="dept-b", file_hash="f" * 64)

    projection = project_current_documents(
        [dept_a, dept_b],
        [_result("a", 100, 0), _result("b", 0, 8)],
        [],
    )

    assert projection.enrolled_document_count == 2
    assert {doc.identity for doc in projection.current_documents} == {
        f"dept-a:sha256:{'f' * 64}",
        f"dept-b:sha256:{'f' * 64}",
    }


def test_general_stats_expose_current_coverage_and_historical_volume(monkeypatch):
    projection = project_current_documents(
        [
            _scan("old", file_hash="1" * 64, created_offset=0),
            _scan("new", file_hash="1" * 64, created_offset=10),
            _scan("unverified", file_hash="2" * 64, status=ScanStatus.PENDING),
        ],
        [_result("old", 50, 4), _result("new", 90, 1)],
        [],
    )
    monkeypatch.setattr(
        "src.education.current_compliance.get_department_current_compliance",
        lambda _db, _department_id: projection,
    )

    stats = ScanService.get_department_stats(MagicMock(), "dept-a")

    assert stats["total_scans"] == 3
    assert stats["historical_scan_count"] == 3
    assert stats["enrolled_document_count"] == 2
    assert stats["verified_document_count"] == 1
    assert stats["unverified_document_count"] == 1
    assert stats["avg_compliance_score"] == 90
    assert stats["total_issues"] == 1
    assert stats["total_pages"] == 2


def test_comprehensive_stats_use_the_same_current_projection(monkeypatch):
    projection = project_current_documents(
        [
            _scan("old", file_hash="3" * 64, created_offset=0),
            _scan("new", file_hash="3" * 64, created_offset=10),
            _scan("pending", file_hash="4" * 64, status=ScanStatus.PENDING),
        ],
        [_result("old", 50, 4), _result("new", 90, 1)],
        [],
    )
    monkeypatch.setattr(
        "src.education.compliance_dashboard.get_department_current_compliance",
        lambda _db, _department_id: projection,
    )

    db = MagicMock()

    def query(model):
        result = MagicMock()
        if model is Department:
            result.filter.return_value.first.return_value = SimpleNamespace(
                id="dept-a",
                name="Department A",
                institution="Institution A",
                country_code="AU",
                regulatory_framework=None,
                custom_deadline=None,
            )
        elif model is User:
            result.filter.return_value.count.return_value = 1
        else:  # pragma: no cover - catches an accidental historical query
            raise AssertionError(f"unexpected query for {model}")
        return result

    db.query.side_effect = query
    stats = ComplianceDashboard.get_department_compliance(db, "dept-a")

    assert stats.total_scans == 3
    assert stats.enrolled_documents == 2
    assert stats.verified_documents == 1
    assert stats.documents_without_verified_state == 1
    assert stats.total_files_scanned == 1
    assert stats.avg_compliance_score == 90
    assert stats.total_critical == 1
    assert stats.total_issues == 1
    assert stats.compliance_rate == 100


def test_admin_stats_reuse_the_general_current_compliance_contract(monkeypatch):
    shared_stats = {
        "total_scans": 5,
        "historical_scan_count": 5,
        "enrolled_document_count": 3,
        "verified_document_count": 2,
        "unverified_document_count": 1,
        "scans_this_month": 4,
        "avg_compliance_score": 85.0,
        "total_issues": 3,
    }
    monkeypatch.setattr(
        ScanService,
        "get_department_stats",
        lambda _db, _department_id: shared_stats,
    )

    db = MagicMock()
    filtered = db.query.return_value.filter.return_value
    filtered.scalar.side_effect = [6, 4, 1]
    filtered.first.return_value = SimpleNamespace(
        name="Department A",
        institution="Institution A",
        tier="core",
        max_users=100,
    )

    response = asyncio.run(
        get_admin_department_stats(
            db=db,
            admin_info=(None, "admin-a", "dept-a", SimpleNamespace(value="admin")),
        )
    )

    assert response["stats"] == {
        "total_users": 6,
        "active_users": 4,
        **shared_stats,
        "pending_invitations": 1,
    }


def test_priority_issues_only_include_each_documents_current_result(monkeypatch):
    projection = project_current_documents(
        [
            _scan("old", file_hash="5" * 64, created_offset=0),
            _scan("new", file_hash="5" * 64, created_offset=10),
        ],
        [_result("old", 40, 5), _result("new", 90, 1)],
        [],
    )
    monkeypatch.setattr(
        "src.education.compliance_dashboard.get_department_current_compliance",
        lambda _db, _department_id: projection,
    )

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(id="user-a", name="Faculty A")
    ]

    issues = ComplianceDashboard.get_priority_issues(db, "dept-a")

    assert len(issues) == 1
    assert issues[0].scan_id == "new"
