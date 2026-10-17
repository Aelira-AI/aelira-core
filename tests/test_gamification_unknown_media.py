"""Unassessed media must not become a gamified compliance score."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.db.models import Scan, ScanResult, ScanType, User
from src.education.gamification_service import BadgeType, GamificationService


def _scan(scan_id, scan_type, created_at):
    return SimpleNamespace(
        id=scan_id,
        scan_type=scan_type,
        created_at=created_at,
        file_name=f"{scan_id}.mp4",
        status="COMPLETED",
    )


def _result(scan_id, score, media_type=None):
    return SimpleNamespace(
        scan_id=scan_id,
        compliance_score=score,
        remediation_applied=False,
        issues=[],
        structure={"media_type": media_type} if media_type else None,
    )


def test_all_legacy_video_scores_remain_unknown_in_faculty_stats():
    scan = _scan("legacy-video", ScanType.VIDEO, datetime.utcnow())
    result = _result("legacy-video", 100, "video")
    user = SimpleNamespace(id="user-1", name="Faculty", email="faculty@example.test")
    db = MagicMock()

    def query(model):
        response = MagicMock()
        if model is User:
            response.filter.return_value.first.return_value = user
        elif model is Scan:
            response.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [
                scan
            ]
        elif model is ScanResult:
            response.filter.return_value.first.return_value = result
        return response

    db.query.side_effect = query
    service = GamificationService(db)
    service._get_rank = MagicMock(return_value=None)
    stats = service.get_faculty_stats("user-1", "dept-1")

    assert stats.total_scans == 1
    assert stats.avg_compliance_score is None
    assert stats.score_change_7d is None
    assert stats.score_change_30d is None
    assert stats.all_time_rank is None
    assert stats.total_points >= 5  # Scan-count points remain available.
    assert not {
        BadgeType.COMPLIANCE_CHAMPION,
        BadgeType.PERFECT_SCORE,
        BadgeType.COMPLIANT_FILE,
        BadgeType.IMPROVEMENT_5,
    }.intersection(badge.badge_type for badge in stats.badges_earned)


def test_improvement_requires_comparable_real_scores_and_preserves_measured_zero():
    now = datetime.utcnow()
    scans = [
        _scan("legacy-video", ScanType.VIDEO, now - timedelta(days=40)),
        _scan("old-pdf", ScanType.PDF, now - timedelta(days=40)),
        _scan("new-pdf", ScanType.PDF, now),
    ]
    results = {
        "legacy-video": _result("legacy-video", 100, "video"),
        "old-pdf": _result("old-pdf", 0),
        "new-pdf": _result("new-pdf", 80),
    }
    db = MagicMock()
    response = MagicMock()
    response.filter.return_value.first.side_effect = [results[s.id] for s in scans]
    db.query.return_value = response
    service = GamificationService(db)
    assert service._calculate_improvement(scans, 30) == 80
    response.filter.return_value.first.side_effect = [results[s.id] for s in scans[:2]]
    assert service._calculate_improvement(scans[:2], 30) is None


def test_leaderboard_places_unassessed_faculty_after_assessed_zero():
    faculty = [
        SimpleNamespace(id="unknown", name="Unknown", email="unknown@example.test"),
        SimpleNamespace(id="measured", name="Measured", email="measured@example.test"),
    ]
    db = MagicMock()
    db.query.return_value.filter.return_value.filter.return_value.all.return_value = (
        faculty
    )
    service = GamificationService(db)
    stats = {
        "unknown": SimpleNamespace(
            total_scans=1,
            avg_compliance_score=None,
            total_points=5,
            level=1,
            level_name="Newcomer",
            badges_earned=[],
            current_streak=0,
            score_change_7d=None,
        ),
        "measured": SimpleNamespace(
            total_scans=1,
            avg_compliance_score=0,
            total_points=5,
            level=1,
            level_name="Newcomer",
            badges_earned=[],
            current_streak=0,
            score_change_7d=0,
        ),
    }
    service.get_faculty_stats = lambda user_id, _department: stats[user_id]
    for kind in ("all_time", "monthly", "improvement"):
        leaderboard = service.get_department_leaderboard("dept-1", kind)
        assert [entry["user_id"] for entry in leaderboard] == ["measured", "unknown"]
        assert leaderboard[0]["rank"] == 1
        assert leaderboard[1]["rank"] is None
