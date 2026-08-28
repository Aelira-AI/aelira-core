"""Analytics preserve an unknown score when no document is verified."""

import asyncio
from datetime import datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from openpyxl import load_workbook

from src.api import analytics
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import Scan, ScanResult, ScanStatus, ScanType, UserRole
from src.education.compliance_dashboard import ComplianceDashboard
from src.education.compliance_predictor import predict_compliance
from src.education.snapshot_service import SnapshotService, TrendPoint

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
        estimated_hours_remaining=0,
        on_track=False,
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

    trend_db = MagicMock()
    trend_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        snapshot
    ]
    trend = SnapshotService.get_historical_trend(trend_db, DEPARTMENT_ID)
    assert trend[0].avg_compliance_score is None


def test_trend_analysis_and_projection_require_verified_scores(monkeypatch):
    snapshots = [
        SimpleNamespace(avg_compliance_score=None, total_issues=0) for _ in range(14)
    ]
    db = MagicMock()
    db.query.return_value.filter.return_value.all.side_effect = [
        snapshots[:7],
        snapshots[7:],
    ]

    analysis = SnapshotService.analyze_trend(db, DEPARTMENT_ID)

    assert analysis.current_avg_score is None
    assert analysis.previous_avg_score is None
    assert analysis.score_change is None
    assert analysis.score_change_pct is None
    assert analysis.trend_direction == "insufficient_data"
    assert analysis.on_track_for_deadline is False

    unverified_trend = [_unverified_trend_point(day) for day in range(7)]
    monkeypatch.setattr(
        SnapshotService,
        "get_historical_trend",
        lambda _db, _department_id, days: unverified_trend,
    )
    projection = SnapshotService.get_deadline_projection(MagicMock(), DEPARTMENT_ID)

    assert projection == {
        "projection_available": False,
        "message": "Need at least 7 days of verified compliance data for projection",
    }


def test_predictor_returns_insufficient_data_without_inventing_zero(monkeypatch):
    unverified_trend = [_unverified_trend_point(day) for day in range(7)]
    monkeypatch.setattr(
        SnapshotService,
        "get_historical_trend",
        lambda _db, _department_id, days: unverified_trend,
    )

    result = predict_compliance(MagicMock(), DEPARTMENT_ID)

    assert result["prediction"]["current_score"] is None
    assert result["prediction"]["projected_score"] is None
    assert result["risk_assessment"]["level"] == "unknown"
    assert result["model_info"]["model"] == "insufficient_data"
