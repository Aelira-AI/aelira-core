"""Analytics preserve an unknown score when no document is verified."""

import asyncio
from datetime import date, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from openpyxl import load_workbook

from src.api import analytics
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import (
    ComplianceSnapshot,
    Scan,
    ScanResult,
    ScanStatus,
    ScanType,
    UserRole,
)
from src.education.compliance_dashboard import ComplianceDashboard
from src.education.current_compliance import project_current_documents
from src.education.compliance_predictor import predict_compliance
from src.education.deadline_config import DeadlineService
from src.education.snapshot_service import SnapshotService, TrendPoint
from src.education.snapshot_service import CURRENT_STOCK_PROVENANCE

DEPARTMENT_ID = "department-without-verified-documents"


def _principal():
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="admin-user",
        department_id=DEPARTMENT_ID,
        user_role=UserRole.ADMIN,
        auth_method="session",
    )


def _stats_without_verified_documents():
    return SimpleNamespace(
        avg_compliance_score=None,
        min_compliance_score=None,
        max_compliance_score=None,
        total_scans=1,
        total_files_scanned=0,
        scans_last_7_days=1,
        total_critical=0,
        total_high=0,
        total_medium=0,
        total_low=0,
        total_issues=0,
        files_compliant=0,
        files_needs_work=0,
        files_critical=0,
        active_faculty=0,
        total_faculty=0,
        days_until_deadline=None,
        estimated_hours_remaining=0,
        on_track=None,
    )


def _unverified_trend_point(day: int) -> TrendPoint:
    return TrendPoint(
        date=(datetime.utcnow() - timedelta(days=day)).date().isoformat(),
        avg_compliance_score=None,
        scan_count=1,
        total_issues=0,
        files_compliant=0,
        files_needs_work=0,
        files_critical=0,
    )


def test_excel_export_marks_nullable_current_score_not_assessed(monkeypatch):
    db = MagicMock()
    pending_scan = SimpleNamespace(
        id="pending-scan",
        created_at=datetime.utcnow(),
        file_name="pending.pdf",
        scan_type=ScanType.PDF,
        status=ScanStatus.PENDING,
        remediated=False,
    )

    def query(model):
        result = MagicMock()
        if model is Scan:
            result.filter.return_value.order_by.return_value.all.return_value = [
                pending_scan
            ]
        elif model is ScanResult:
            result.filter.return_value.first.return_value = None
        return result

    db.query.side_effect = query
    monkeypatch.setattr(
        ComplianceDashboard,
        "get_department_compliance",
        lambda _db, _department_id: _stats_without_verified_documents(),
    )
    monkeypatch.setattr(
        analytics.IssueTrackingService,
        "get_issue_stats",
        lambda _db, _department_id: None,
    )

    response = asyncio.run(
        analytics.export_scans_excel(
            department_id=DEPARTMENT_ID,
            date_from=None,
            date_to=None,
            db=db,
            principal=_principal(),
        )
    )

    workbook = load_workbook(BytesIO(response.body))
    assert workbook["Summary"]["B9"].value == "Not assessed"
    assert workbook["All Scans"]["F2"].value == "Not assessed"


def test_snapshot_and_trend_keep_unverified_score_null(monkeypatch):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(
        "src.education.snapshot_service.ComplianceDashboard.get_department_compliance",
        lambda _db, _department_id: _stats_without_verified_documents(),
    )

    snapshot = SnapshotService.capture_daily_snapshot(db, DEPARTMENT_ID)

    assert snapshot.avg_compliance_score is None
    assert snapshot.metric_provenance == CURRENT_STOCK_PROVENANCE

    trend_db = MagicMock()
    legacy = SimpleNamespace(
        snapshot_date=datetime.utcnow(),
        avg_compliance_score=100,
        metric_provenance=None,
        total_scans=1,
        total_issues=0,
        files_compliant=1,
        files_needs_work=0,
        files_critical=0,
    )

    def query(model):
        if model is not ComplianceSnapshot:
            raise AssertionError("trend must not substitute scan-attempt cohorts")
        result = MagicMock()
        result.filter.return_value.order_by.return_value.all.return_value = [
            legacy,
            snapshot,
        ]
        return result

    trend_db.query.side_effect = query
    trend = SnapshotService.get_historical_trend(trend_db, DEPARTMENT_ID)
    assert len(trend) == 1
    assert trend[0].avg_compliance_score is None
    assert trend[0].scan_count == snapshot.total_scans


def test_legacy_cached_video_score_cannot_drive_trend_analysis_or_projection():
    now = datetime.utcnow()
    snapshots = [
        SimpleNamespace(
            snapshot_date=now - timedelta(days=8),
            avg_compliance_score=100,
            metric_provenance=None,
            total_scans=1,
            total_issues=0,
            files_compliant=1,
            files_needs_work=0,
            files_critical=0,
        ),
        SimpleNamespace(
            snapshot_date=now,
            avg_compliance_score=80,
            metric_provenance=CURRENT_STOCK_PROVENANCE,
            total_scans=2,
            total_issues=1,
            files_compliant=0,
            files_needs_work=1,
            files_critical=0,
        ),
    ]
    department = SimpleNamespace(
        country_code="US",
        regulatory_framework="US_ADA_TITLE_II",
        title_ii_entity_class="large",
        custom_deadline=None,
    )
    db = MagicMock()

    def query(model):
        if model is Scan or model is ScanResult:
            raise AssertionError("daily scan cohorts cannot replace stock snapshots")
        response = MagicMock()
        response.filter.return_value.order_by.return_value.all.return_value = snapshots
        response.filter.return_value.first.return_value = department
        return response

    db.query.side_effect = query
    trend = SnapshotService.get_historical_trend(db, DEPARTMENT_ID, days=14)
    assert [point.avg_compliance_score for point in trend] == [80]
    assert [point.files_compliant for point in trend] == [0]
    analysis = SnapshotService.analyze_trend(
        db, DEPARTMENT_ID, current_period_days=7, comparison_period_days=7
    )
    assert analysis.current_avg_score == 80
    assert analysis.previous_avg_score is None
    assert analysis.score_change is None
    assert analysis.trend_direction == "insufficient_data"
    projection = SnapshotService.get_deadline_projection(db, DEPARTMENT_ID)
    assert projection["projection_available"] is False


def test_fresh_snapshot_tracks_current_stock_not_today_upload_cohort(monkeypatch):
    now = datetime.utcnow()
    scans = [
        SimpleNamespace(
            id=scan_id,
            department_id=DEPARTMENT_ID,
            file_hash=hash_char * 64,
            file_name=f"{scan_id}.pdf",
            scan_type=scan_type,
            status=ScanStatus.COMPLETED,
            created_at=created_at,
            document_source="standalone_upload",
            document_id=None,
        )
        for scan_id, hash_char, scan_type, created_at in (
            ("old-pdf", "a", ScanType.PDF, now - timedelta(days=1)),
            ("new-pdf", "b", ScanType.PDF, now),
            ("video", "c", ScanType.VIDEO, now),
        )
    ]
    results = [
        SimpleNamespace(
            scan_id=scan_id,
            compliance_score=score,
            structure=structure,
            critical_issues=0,
            high_issues=0,
            medium_issues=0,
            low_issues=0,
        )
        for scan_id, score, structure in (
            ("old-pdf", 0, None),
            ("new-pdf", 100, None),
            (
                "video",
                None,
                {
                    "media_type": "video",
                    "flashing_assessment": {
                        "state": "unassessed",
                        "reason": "detector_unavailable",
                        "version": "unassessed-v1",
                    },
                },
            ),
        )
    ]
    stock = project_current_documents(scans, results, [])
    assert stock.average_compliance_score == 50
    stats = _stats_without_verified_documents()
    stats.avg_compliance_score = stock.average_compliance_score
    stats.min_compliance_score = 0
    stats.max_compliance_score = 100
    stats.total_scans = 3
    stats.total_files_scanned = 2
    stats.files_compliant = 1
    stats.files_critical = 1
    monkeypatch.setattr(
        ComplianceDashboard,
        "get_department_compliance",
        lambda _db, _department_id: stats,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    snapshot = SnapshotService.capture_daily_snapshot(db, DEPARTMENT_ID)
    assert snapshot.avg_compliance_score == 50
    assert snapshot.metric_provenance == CURRENT_STOCK_PROVENANCE
    assert snapshot.files_compliant == 1
    assert snapshot.files_critical == 1

    trend_db = MagicMock()
    trend_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        snapshot
    ]
    trend = SnapshotService.get_historical_trend(trend_db, DEPARTMENT_ID)
    assert [point.avg_compliance_score for point in trend] == [50]


def test_trend_analysis_and_projection_require_verified_scores(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        SnapshotService,
        "get_historical_trend",
        lambda _db, _department_id, days: [
            _unverified_trend_point(day) for day in range(14)
        ],
    )

    analysis = SnapshotService.analyze_trend(db, DEPARTMENT_ID)

    assert analysis.current_avg_score is None
    assert analysis.previous_avg_score is None
    assert analysis.score_change is None
    assert analysis.score_change_pct is None
    assert analysis.trend_direction == "insufficient_data"
    assert analysis.on_track_for_deadline is None

    unverified_trend = [_unverified_trend_point(day) for day in range(7)]
    monkeypatch.setattr(
        SnapshotService,
        "get_historical_trend",
        lambda _db, _department_id, days: unverified_trend,
    )
    deadline = DeadlineService.get_deadline_info(
        country_code="US",
        regulatory_framework="US_ADA_TITLE_II",
        title_ii_entity_class="large",
    )
    monkeypatch.setattr(
        "src.education.snapshot_service.DeadlineService.for_department",
        lambda _department: deadline,
    )
    projection = SnapshotService.get_deadline_projection(MagicMock(), DEPARTMENT_ID)

    assert projection == {
        "projection_available": False,
        "message": "Need at least 7 days of verified compliance data for projection",
        "deadline": deadline.to_dict(),
    }


def test_future_dated_profile_stays_unknown_without_verified_scores(monkeypatch):
    department = SimpleNamespace(
        country_code="US",
        regulatory_framework="US_ADA_TITLE_II",
        title_ii_entity_class="large",
        custom_deadline=None,
    )
    deadline = DeadlineService.for_department(department, as_of=date(2026, 8, 28))
    monkeypatch.setattr(DeadlineService, "for_department", lambda _department: deadline)

    stats = ComplianceDashboard._empty_stats(
        DEPARTMENT_ID, "Accessibility", "Example University", department
    )

    db = MagicMock()
    monkeypatch.setattr(
        SnapshotService,
        "get_historical_trend",
        lambda _db, _department_id, days: [
            _unverified_trend_point(day) for day in range(14)
        ],
    )
    db.query.return_value.filter.return_value.first.return_value = department
    analysis = SnapshotService.analyze_trend(db, DEPARTMENT_ID)

    monkeypatch.setattr(
        SnapshotService,
        "get_historical_trend",
        lambda _db, _department_id, days: [
            _unverified_trend_point(day) for day in range(7)
        ],
    )
    projection = SnapshotService.get_deadline_projection(db, DEPARTMENT_ID)

    assert deadline.has_deadline is True
    assert deadline.is_past_deadline is False
    assert stats.on_track is None
    assert analysis.on_track_for_deadline is None
    assert projection["projection_available"] is False
    assert "will_meet_deadline" not in projection
    assert "projected_score_at_deadline" not in projection


def test_predictor_returns_insufficient_data_without_inventing_zero(monkeypatch):
    unverified_trend = [_unverified_trend_point(day) for day in range(7)]
    monkeypatch.setattr(
        SnapshotService,
        "get_historical_trend",
        lambda _db, _department_id, days: unverified_trend,
    )
    deadline = DeadlineService.get_deadline_info(
        country_code="US",
        regulatory_framework="US_ADA_TITLE_II",
        title_ii_entity_class="large",
    )
    monkeypatch.setattr(
        "src.education.compliance_predictor.DeadlineService.for_department",
        lambda _department: deadline,
    )

    result = predict_compliance(MagicMock(), DEPARTMENT_ID)

    assert result["prediction"]["current_score"] is None
    assert result["prediction"]["projected_score"] is None
    assert result["risk_assessment"]["level"] == "unknown"
    assert result["model_info"]["model"] == "insufficient_data"


def test_past_deadline_disables_projection_and_prediction():
    department = SimpleNamespace(
        country_code="DE",
        regulatory_framework="EU_EAA",
        title_ii_entity_class=None,
        custom_deadline=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = department

    projection = SnapshotService.get_deadline_projection(db, DEPARTMENT_ID)
    prediction = predict_compliance(db, DEPARTMENT_ID)

    assert projection["projection_available"] is False
    assert projection["unavailable_reason"] == "deadline_passed"
    assert projection["deadline"]["is_past_deadline"] is True
    assert prediction["prediction"]["will_meet_deadline"] is None
    assert prediction["prediction"]["days_remaining"] is None
    assert prediction["model_info"]["model"] == "deadline_unavailable"
    assert prediction["deadline"]["is_past_deadline"] is True
