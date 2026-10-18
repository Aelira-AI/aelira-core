"""Current compliance is a projection of documents, not scan attempts."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from src.db.models import (
    CloudFile,
    Department,
    Scan,
    ScanResult,
    ScanStatus,
    ScanType,
    User,
)
from src.db.scan_service import ScanService
from src.api.user_management import get_department_stats as get_admin_department_stats
from src.education.compliance_dashboard import ComplianceDashboard
from src.education.current_compliance import (
    _provider_scan_ids,
    public_cloud_file_score,
    public_cloud_file_scores,
    public_cloud_file_issue_totals,
    public_issue_total,
    retired_media_flash_scan_ids,
    public_scan_job_results,
    public_compliance_score,
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


def test_legacy_and_partial_media_never_become_verified_credit():
    legacy = _scan("legacy", file_hash="b" * 64)
    legacy.scan_type = ScanType.MULTIMEDIA
    stale_safe = _result("legacy", 100, 0)
    stale_safe.structure = {
        "media_type": "video",
        "flashing_analysis": {"severity": "safe"},
    }
    partial = _scan("partial", file_hash="b" * 64, created_offset=1)
    partial.scan_type = ScanType.MULTIMEDIA
    partial_result = _result("partial", None, 1)
    partial_result.structure = {
        "media_type": "video",
        "flashing_assessment": {
            "state": "unassessed",
            "reason": "detector_unavailable",
            "version": "unassessed-v1",
        },
    }
    projection = project_current_documents(
        [legacy, partial], [stale_safe, partial_result], []
    )
    assert public_compliance_score(legacy, stale_safe) is None
    assert projection.verified_document_count == 0
    assert projection.average_compliance_score is None
    assert projection.historical_scan_count == 2

    audio = _scan("audio", file_hash="c" * 64)
    audio.scan_type = ScanType.MULTIMEDIA
    audio_result = _result("audio", 80, 1)
    audio_result.structure = {
        "media_type": "audio",
        "flashing_assessment": {"state": "not_applicable", "version": "unassessed-v1"},
    }
    assert public_compliance_score(audio, audio_result) == 80


def test_completed_pdf_without_assessed_score_is_coverage_with_findings_not_credit():
    unknown = _scan("unknown", file_hash="d" * 64)
    unknown_result = _result("unknown", None, 1)
    measured_zero = _scan("measured-zero", file_hash="e" * 64)
    zero_result = _result("measured-zero", 0, 2)

    projection = project_current_documents(
        [unknown, measured_zero], [unknown_result, zero_result], []
    )

    assert public_compliance_score(unknown, unknown_result) is None
    assert public_issue_total(unknown, unknown_result) == 1
    assert projection.historical_scan_count == 2
    assert projection.scanned_document_count == 2
    assert projection.verified_document_count == 1
    assert projection.unverified_document_count == 1
    assert projection.average_compliance_score == 0
    assert projection.minimum_compliance_score == 0
    assert projection.maximum_compliance_score == 0
    assert projection.compliance_band_counts(compliant_at=95, needs_work_at=70) == (
        0,
        0,
        1,
    )
    assert projection.total_issues == 2


def test_invalid_completed_document_scores_never_enter_current_aggregates():
    invalid_scores = [None, float("nan"), float("inf"), -1, 101, True, "75"]
    scans = [
        _scan(f"invalid-{index}", file_hash=f"{index + 1:064x}")
        for index in range(len(invalid_scores))
    ]
    results = [_result(scan.id, score, 1) for scan, score in zip(scans, invalid_scores)]
    projection = project_current_documents(scans, results, [])

    assert all(
        public_compliance_score(scan, result) is None
        for scan, result in zip(scans, results)
    )
    assert projection.scanned_document_count == len(scans)
    assert projection.verified_document_count == 0
    assert projection.unverified_document_count == len(scans)
    assert projection.average_compliance_score is None
    assert projection.minimum_compliance_score is None
    assert projection.maximum_compliance_score is None
    assert projection.compliance_band_counts(compliant_at=95, needs_work_at=70) == (
        0,
        0,
        0,
    )


def test_cached_provider_score_cannot_restore_legacy_video_credit():
    legacy = _scan("legacy")
    legacy.scan_type = ScanType.MULTIMEDIA
    result = _result("legacy", 100, 0)
    result.structure = {
        "media_type": "video",
        "flashing_analysis": {"severity": "safe"},
    }

    db = MagicMock()

    def query(model):
        matches = MagicMock()
        matches.filter.return_value.all.return_value = [
            legacy if model is Scan else result
        ]
        return matches

    db.query.side_effect = query
    cloud_file = SimpleNamespace(
        id="file-1",
        last_scan_id="legacy",
        last_compliance_score=100,
        file_type="file",
        mime_type="video/mp4",
    )
    assert public_cloud_file_score(db, cloud_file) is None

    cloud_file.last_scan_id = None
    assert public_cloud_file_score(db, cloud_file) is None

    cloud_file.file_type = "pdf"
    cloud_file.mime_type = "application/pdf"
    assert public_cloud_file_score(db, cloud_file) == 100

    legacy.scan_type = ScanType.MULTIMEDIA
    result.compliance_score = 80
    result.structure = {
        "media_type": "audio",
        "flashing_assessment": {"state": "not_applicable", "version": "unassessed-v1"},
    }
    cloud_file.last_scan_id = "legacy"
    cloud_file.file_type = "mp3"
    cloud_file.mime_type = "audio/mpeg"
    assert public_cloud_file_score(db, cloud_file) == 80


def test_provider_scores_use_bounded_provenance_queries():
    db = MagicMock()
    legacy = _scan("legacy")
    legacy.scan_type = ScanType.VIDEO
    stale = _result("legacy", 100, 0)
    stale.structure = {"media_type": "video", "flashing_analysis": {"severity": "safe"}}

    def query(model):
        matches = MagicMock()
        matches.filter.return_value.all.return_value = (
            [legacy] if model is Scan else [stale]
        )
        return matches

    db.query.side_effect = query
    files = [
        SimpleNamespace(
            id=f"file-{index}",
            last_scan_id="legacy" if index == 0 else None,
            last_compliance_score=100,
            file_type="mp4" if index == 0 else "pdf",
            mime_type="video/mp4" if index == 0 else "application/pdf",
        )
        for index in range(100)
    ]
    scores = public_cloud_file_scores(db, files)
    assert scores["file-0"] is None
    assert scores["file-99"] == 100
    assert db.query.call_count == 2


def test_canvas_course_status_keeps_partial_scans_without_score_credit():
    from src.api.canvas_scan_routes import get_course_scan_status

    video = _scan("video")
    video.scan_type = ScanType.VIDEO
    pdf = _scan("pdf")
    legacy_result = _result("video", 100, 1)
    legacy_result.structure = {
        "media_type": "video",
        "flashing_analysis": {"severity": "safe"},
    }
    legacy_result.suggestions = {}
    pdf_result = _result("pdf", 80, 2)
    pdf_result.suggestions = {}
    files = [
        SimpleNamespace(
            provider_file_id=str(index),
            file_name=f"file-{index}",
            file_type=file_type,
            last_scan_id=scan_id,
            has_remediated_version=False,
            remediation_origin=None,
        )
        for index, file_type, scan_id in [(1, "mp4", "video"), (2, "pdf", "pdf")]
    ]
    queries = {}
    for model, values in (
        (CloudFile, files),
        (Scan, [video, pdf]),
        (ScanResult, [legacy_result, pdf_result]),
    ):
        query = MagicMock()
        query.filter.return_value.all.return_value = values
        query.filter.return_value.first.side_effect = values
        queries[model] = query
    db = MagicMock()
    db.query.side_effect = lambda model: queries[model]
    with patch("src.api.canvas_scan_routes.require_lti_course_access"):
        response = asyncio.run(
            get_course_scan_status(
                "course-1",
                file_ids="1,2",
                db=db,
                principal=SimpleNamespace(department_id="dept-a"),
            )
        )
    assert response.scanned_files == 2
    assert response.average_compliance is None
    assert response.files[0].compliance_score is None
    assert response.files[0].issues_count == 1


def test_faculty_leaderboard_excludes_legacy_video_and_preserves_measured_zero():
    users = [
        SimpleNamespace(id=user_id, name=user_id, email=f"{user_id}@example.test")
        for user_id in ("video", "zero", "perfect")
    ]
    scans = [
        _scan("video-scan", file_hash="a" * 64),
        _scan("zero-scan", file_hash="b" * 64),
        _scan("perfect-scan", file_hash="c" * 64),
    ]
    scans[0].scan_type = ScanType.VIDEO
    results = [
        _result("video-scan", 100, 1),
        _result("zero-scan", 0, 1),
        _result("perfect-scan", 100, 0),
    ]
    results[0].structure = {
        "media_type": "video",
        "flashing_analysis": {"severity": "safe"},
    }
    caption_issue = {
        "rule": "WCAG 1.2.2",
        "severity": "critical",
        "message": "Missing captions",
    }
    retired_flash = {
        "rule": "WCAG 2.3.1",
        "severity": "critical",
        "message": "Dangerous flashing content detected (4.0 Hz)",
        "timestamps": [1.0],
    }
    results[0].issues = [caption_issue, retired_flash]
    results[0].critical_issues = 2
    scan_iter = iter(scans)
    result_iter = iter(results)
    db = MagicMock()

    def query(model):
        response = MagicMock()
        if model is User:
            response.filter.return_value.filter.return_value.all.return_value = users
        elif model is Scan:
            response.filter.return_value.filter.return_value.all.return_value = [
                next(scan_iter)
            ]
        else:
            response.filter.return_value.all.return_value = [next(result_iter)]
        return response

    db.query.side_effect = query
    leaderboard = ComplianceDashboard.get_faculty_leaderboard(db, "dept-a")
    assert [entry.user_id for entry in leaderboard] == ["perfect", "zero", "video"]
    assert [entry.avg_compliance_score for entry in leaderboard] == [100, 0, None]
    assert [entry.rank for entry in leaderboard] == [1, 2, None]
    assert leaderboard[0].badge == "🏆 Accessibility Champion"
    assert leaderboard[2].badge == "🚀 Getting Started"
    assert all(entry.total_issues_fixed is None for entry in leaderboard)


def test_cached_scan_jobs_mask_legacy_media_without_mutating_stored_blobs():
    scans = [
        _scan("legacy-video"),
        _scan("pdf-zero"),
        _scan("pdf-perfect"),
        _scan("new-video"),
        _scan("foreign"),
    ]
    for scan in scans:
        scan.document_id = f"cf-{scan.id}"
    scans[0].scan_type = ScanType.VIDEO
    scans[3].scan_type = ScanType.VIDEO
    scans[4].department_id = "foreign-dept"
    results = [
        _result("legacy-video", 100, 0),
        _result("pdf-zero", 0, 1),
        _result("pdf-perfect", 100, 0),
        _result("new-video", None, 1),
        _result("foreign", 88, 1),
    ]
    results[0].structure = {
        "media_type": "video",
        "flashing_analysis": {"severity": "safe"},
    }
    results[0].issues = [
        {"rule": "WCAG 1.2.2", "severity": "critical", "message": "Missing captions"},
        {
            "rule": "WCAG 2.3.1",
            "severity": "critical",
            "message": "Dangerous flashing content detected (4.0 Hz)",
            "timestamps": [1.0],
        },
    ]
    results[0].critical_issues = 2
    results[3].structure = {
        "media_type": "video",
        "flashing_assessment": {
            "state": "unassessed",
            "reason": "detector_unavailable",
            "version": "unassessed-v1",
        },
    }
    jobs = [
        SimpleNamespace(
            id=scan_id,
            job_type="scan",
            department_id="dept-a",
            cloud_file_id=f"cf-{scan_id}",
            result_data={
                "scan_id": scan_id,
                "compliance_score": cached,
                "issues_found": 1,
                "success": True,
            },
        )
        for scan_id, cached in (
            ("legacy-video", 100),
            ("pdf-zero", 100),
            ("pdf-perfect", 0),
            ("new-video", None),
            ("foreign", 88),
            ("missing", 90),
        )
    ]
    jobs.append(
        SimpleNamespace(
            id="remediation",
            job_type="remediate",
            department_id="dept-a",
            cloud_file_id=None,
            result_data={"compliance_score": 75, "success": True},
        )
    )
    db = MagicMock()

    def query(model):
        response = MagicMock()
        response.filter.return_value.all.return_value = (
            scans if model is Scan else results
        )
        return response

    db.query.side_effect = query
    public = public_scan_job_results(db, jobs)
    assert [public[job.id]["compliance_score"] for job in jobs] == [
        None,
        0,
        100,
        None,
        None,
        None,
        75,
    ]
    assert [public[job.id].get("issues_found") for job in jobs] == [
        1,
        1,
        0,
        1,
        None,
        None,
        None,
    ]
    assert jobs[0].result_data["compliance_score"] == 100
    assert jobs[0].result_data["issues_found"] == 1
    assert len(results[0].issues) == 2
    assert db.query.call_count == 2


def test_retired_media_flash_filter_keeps_other_source_findings():
    from src.education.current_compliance import (
        public_issue_counts,
        public_issue_total,
        public_scan_issues,
    )

    caption = {
        "rule": "WCAG 1.2.2",
        "severity": "critical",
        "message": "Missing captions",
    }
    retired = {
        "rule": "WCAG 2.3.1",
        "severity": "critical",
        "message": "Dangerous flashing content detected (4.0 Hz)",
        "timestamps": [1.0],
    }
    website = _scan("website")
    website.scan_type = ScanType.WEBSITE
    video = _scan("video")
    video.scan_type = ScanType.VIDEO
    result = _result("video", 100, 2)
    result.issues = [caption, retired]
    assert public_scan_issues(video, result) == [caption]
    assert public_issue_counts(video, result)["critical"] == 1
    assert public_issue_total(video, result) == 1
    assert result.issues == [caption, retired]
    assert public_scan_issues(website, result) == [caption, retired]
    assert public_issue_total(website, result) == 2
    result.issues = [retired]
    result.critical_issues = 1
    assert public_scan_issues(video, result) == []
    assert public_issue_total(video, result) == 0
    assert public_compliance_score(video, result) is None
    result.issues = [
        {
            "rule": "WCAG 2.3.1",
            "severity": "high",
            "message": "Potentially problematic flashing detected (11 flashes)",
        }
    ]
    result.critical_issues = 0
    result.high_issues = 1
    assert public_issue_total(video, result) == 0


def test_provider_issue_totals_are_batched_and_preserve_caption_counts():
    scans = [_scan("video"), _scan("website")]
    scans[0].scan_type = ScanType.VIDEO
    scans[0].document_id = "video-file"
    scans[1].scan_type = ScanType.WEBSITE
    scans[1].document_id = "website-file"
    caption = {
        "rule": "WCAG 1.2.2",
        "severity": "critical",
        "message": "Missing captions",
    }
    retired = {
        "rule": "WCAG 2.3.1",
        "severity": "critical",
        "message": "Dangerous flashing content detected (4.0 Hz)",
    }
    results = [_result("video", 100, 2), _result("website", 80, 1)]
    results[0].issues = [caption, retired]
    results[1].issues = [retired]
    files = [
        _cloud_file("video-file", "video"),
        _cloud_file("website-file", "website"),
        _cloud_file("unscanned", None),
    ]
    db = MagicMock()

    def query(model):
        response = MagicMock()
        response.filter.return_value.all.return_value = (
            scans if model is Scan else results
        )
        return response

    db.query.side_effect = query
    assert public_cloud_file_issue_totals(db, files) == {
        "video-file": 1,
        "website-file": 1,
        "unscanned": None,
    }
    assert db.query.call_count == 2


def test_retired_tracker_source_detection_accepts_legacy_issue_shapes_only_for_media():
    retired = {
        "rule": "WCAG 2.3.1",
        "message": "Dangerous flashing content detected (4.0 Hz)",
    }
    scans = [
        _scan("media-list"),
        _scan("media-json"),
        _scan("media-details"),
        _scan("website"),
    ]
    for scan in scans[:3]:
        scan.scan_type = ScanType.VIDEO
    scans[3].scan_type = ScanType.WEBSITE
    results = [_result(scan.id, 100, 1) for scan in scans]
    results[0].issues = [retired]
    results[1].issues = json.dumps([retired])
    results[2].issues = {"details": [retired]}
    results[3].issues = [retired]

    assert retired_media_flash_scan_ids(scans, results) == {
        "media-list",
        "media-json",
        "media-details",
    }


def test_new_issue_tracking_import_excludes_retired_media_verdict():
    from src.education.issue_tracking_service import IssueTrackingService

    scan = _scan("legacy-video")
    scan.scan_type = ScanType.VIDEO
    result = _result("legacy-video", 100, 2)
    result.issues = [
        {"type": "missing_captions", "severity": "critical"},
        {
            "rule": "WCAG 2.3.1",
            "severity": "critical",
            "message": "Dangerous flashing content detected (4.0 Hz)",
        },
    ]
    with patch.object(
        IssueTrackingService, "create_tracked_issue", return_value="caption"
    ) as create:
        assert IssueTrackingService.create_issues_from_scan_result(
            MagicMock(), scan, result
        ) == ["caption"]
    create.assert_called_once()
    assert result.issues[1]["rule"] == "WCAG 2.3.1"


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
