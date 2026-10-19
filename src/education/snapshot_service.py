"""
Compliance Snapshot Service - Historical Trending & Analytics

Captures daily compliance snapshots for departments and provides
historical trend data for the dashboard graphs.

Features:
- Daily snapshot capture (via cron job or API trigger)
- Historical trend queries (30/60/90 days)
- Comparison with previous periods
- Deadline progress tracking

Author: Aelira Team
Created: November 30, 2025
"""

from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, date
from dataclasses import dataclass
import logging
import uuid

from ..db.models import (
    Department,
    ComplianceSnapshot,
)
from .compliance_dashboard import ComplianceDashboard
from .deadline_config import DeadlineService

logger = logging.getLogger(__name__)

CURRENT_STOCK_PROVENANCE = "current_document_stock_v1"


@dataclass
class TrendPoint:
    """Single data point in a trend chart"""

    date: str
    avg_compliance_score: Optional[float]
    scan_count: int
    total_issues: int
    files_compliant: int
    files_needs_work: int
    files_critical: int


@dataclass
class TrendAnalysis:
    """Analysis comparing two time periods"""

    current_avg_score: Optional[float]
    previous_avg_score: Optional[float]
    score_change: Optional[float]
    score_change_pct: Optional[float]
    current_total_issues: int
    previous_total_issues: int
    issues_change: int
    issues_change_pct: float
    trend_direction: str  # 'improving', 'declining', 'stable'
    on_track_for_deadline: Optional[bool]
    deadline: Dict[str, Any]


class SnapshotService:
    """
    Service for managing compliance snapshots and historical trending
    """

    @staticmethod
    def capture_daily_snapshot(db: Session, department_id: str) -> ComplianceSnapshot:
        """
        Capture a daily compliance snapshot for a department.

        Should be run once per day (via cron job or scheduled task).
        If a snapshot already exists for today, it will be updated.

        Args:
            db: Database session
            department_id: Department to snapshot

        Returns:
            ComplianceSnapshot object
        """
        logger.info("Capturing daily department snapshot")

        today = date.today()

        # Check if snapshot already exists for today
        existing = (
            db.query(ComplianceSnapshot)
            .filter(
                and_(
                    ComplianceSnapshot.department_id == department_id,
                    func.date(ComplianceSnapshot.snapshot_date) == today,
                )
            )
            .first()
        )

        # Get current compliance stats
        try:
            stats = ComplianceDashboard.get_department_compliance(db, department_id)
        except ValueError:
            logger.warning("Department snapshot target not found")
            raise

        # Create or update snapshot
        if existing:
            logger.info("Updating existing daily snapshot")
            snapshot = existing
        else:
            snapshot = ComplianceSnapshot(
                id=str(uuid.uuid4()),
                department_id=department_id,
                snapshot_date=datetime.combine(today, datetime.min.time()),
            )
            db.add(snapshot)

        # Update snapshot values
        snapshot.avg_compliance_score = stats.avg_compliance_score
        snapshot.metric_provenance = CURRENT_STOCK_PROVENANCE
        snapshot.min_compliance_score = stats.min_compliance_score
        snapshot.max_compliance_score = stats.max_compliance_score
        snapshot.total_scans = stats.total_scans
        snapshot.scans_today = stats.scans_last_7_days  # Approximate
        snapshot.critical_issues = stats.total_critical
        snapshot.high_issues = stats.total_high
        snapshot.medium_issues = stats.total_medium
        snapshot.low_issues = stats.total_low
        snapshot.total_issues = stats.total_issues
        snapshot.files_compliant = stats.files_compliant
        snapshot.files_needs_work = stats.files_needs_work
        snapshot.files_critical = stats.files_critical
        snapshot.active_faculty = stats.active_faculty
        snapshot.total_faculty = stats.total_faculty
        snapshot.days_until_deadline = stats.days_until_deadline
        snapshot.estimated_hours_remaining = stats.estimated_hours_remaining
        snapshot.on_track = stats.on_track

        db.commit()
        db.refresh(snapshot)

        logger.info("Daily department snapshot captured")
        return snapshot

    @staticmethod
    def capture_all_departments(db: Session) -> List[ComplianceSnapshot]:
        """
        Capture daily snapshots for all active departments.

        Should be run once per day as a scheduled task.

        Returns:
            List of captured snapshots
        """
        logger.info("Capturing daily snapshots for all departments")

        departments = db.query(Department).filter(Department.is_active).all()
        snapshots = []

        for dept in departments:
            try:
                snapshot = SnapshotService.capture_daily_snapshot(db, dept.id)
                snapshots.append(snapshot)
            except Exception as e:
                logger.error("Department snapshot failed (%s)", type(e).__name__)
                continue

        logger.info(f"Captured {len(snapshots)} snapshots")
        return snapshots

    @staticmethod
    def get_historical_trend(
        db: Session, department_id: str, days: int = 30
    ) -> List[TrendPoint]:
        """
        Read only snapshots captured from verified current document stock.

        Historical rows without explicit provenance remain stored, but their
        aggregates may include legacy video scores and cannot be trusted.

        Args:
            db: Database session
            department_id: Department to query
            days: Number of days to look back

        Returns:
            List of TrendPoint objects for charting
        """
        logger.info("Getting department historical trend")

        cutoff_date = datetime.utcnow() - timedelta(days=days)
        snapshots = (
            db.query(ComplianceSnapshot)
            .filter(
                and_(
                    ComplianceSnapshot.department_id == department_id,
                    ComplianceSnapshot.snapshot_date >= cutoff_date,
                    ComplianceSnapshot.metric_provenance == CURRENT_STOCK_PROVENANCE,
                )
            )
            .order_by(ComplianceSnapshot.snapshot_date.asc())
            .all()
        )
        return [
            TrendPoint(
                date=snapshot.snapshot_date.date().isoformat(),
                avg_compliance_score=snapshot.avg_compliance_score,
                scan_count=snapshot.total_scans,
                total_issues=snapshot.total_issues,
                files_compliant=snapshot.files_compliant,
                files_needs_work=snapshot.files_needs_work,
                files_critical=snapshot.files_critical,
            )
            for snapshot in snapshots
            if snapshot.metric_provenance == CURRENT_STOCK_PROVENANCE
        ]

    @staticmethod
    def analyze_trend(
        db: Session,
        department_id: str,
        current_period_days: int = 7,
        comparison_period_days: int = 7,
    ) -> TrendAnalysis:
        """
        Analyze trend comparing current period to previous period.

        Args:
            db: Database session
            department_id: Department to analyze
            current_period_days: Number of days in current period
            comparison_period_days: Number of days in comparison period

        Returns:
            TrendAnalysis with comparison metrics
        """
        now = datetime.utcnow()

        current_start = now - timedelta(days=current_period_days)
        previous_start = current_start - timedelta(days=comparison_period_days)
        points = SnapshotService.get_historical_trend(
            db, department_id, current_period_days + comparison_period_days
        )
        current_points = [
            point
            for point in points
            if date.fromisoformat(point.date) >= current_start.date()
        ]
        previous_points = [
            point
            for point in points
            if previous_start.date()
            <= date.fromisoformat(point.date)
            < current_start.date()
        ]

        # Calculate averages
        current_scores = [
            point.avg_compliance_score
            for point in current_points
            if point.avg_compliance_score is not None
        ]
        previous_scores = [
            point.avg_compliance_score
            for point in previous_points
            if point.avg_compliance_score is not None
        ]

        current_avg = (
            sum(current_scores) / len(current_scores) if current_scores else None
        )
        previous_avg = (
            sum(previous_scores) / len(previous_scores) if previous_scores else None
        )

        current_issues = sum(point.total_issues for point in current_points)
        previous_issues = sum(point.total_issues for point in previous_points)

        # Calculate changes
        score_change = (
            current_avg - previous_avg
            if current_avg is not None and previous_avg is not None
            else None
        )
        score_change_pct = (
            score_change / previous_avg * 100
            if score_change is not None and previous_avg > 0
            else (0 if score_change is not None else None)
        )

        issues_change = current_issues - previous_issues
        issues_change_pct = (
            (issues_change / previous_issues * 100) if previous_issues > 0 else 0
        )

        # Determine trend direction
        if score_change is None:
            trend_direction = "insufficient_data"
        elif score_change > 2:
            trend_direction = "improving"
        elif score_change < -2:
            trend_direction = "declining"
        else:
            trend_direction = "stable"

        department = db.query(Department).filter(Department.id == department_id).first()
        deadline_info = DeadlineService.for_department(department)
        on_track = None
        if (
            deadline_info.has_deadline
            and not deadline_info.is_past_deadline
            and current_avg is not None
        ):
            on_track = current_avg >= 80 or (
                current_avg >= 70 and trend_direction == "improving"
            )

        return TrendAnalysis(
            current_avg_score=(
                round(current_avg, 2) if current_avg is not None else None
            ),
            previous_avg_score=(
                round(previous_avg, 2) if previous_avg is not None else None
            ),
            score_change=(round(score_change, 2) if score_change is not None else None),
            score_change_pct=(
                round(score_change_pct, 2) if score_change_pct is not None else None
            ),
            current_total_issues=current_issues,
            previous_total_issues=previous_issues,
            issues_change=issues_change,
            issues_change_pct=round(issues_change_pct, 2),
            trend_direction=trend_direction,
            on_track_for_deadline=on_track,
            deadline=deadline_info.to_dict(),
        )

    @staticmethod
    def get_deadline_projection(db: Session, department_id: str) -> Dict:
        """
        Project the department's automated scan score at its configured target date.

        Returns:
            Dictionary with projection data
        """
        department = db.query(Department).filter(Department.id == department_id).first()
        if department is None:
            raise ValueError("Department not found")
        deadline_info = DeadlineService.for_department(department)
        deadline = deadline_info.to_dict()

        if not deadline_info.has_deadline or deadline_info.is_past_deadline:
            unavailable_reason = (
                "deadline_passed"
                if deadline_info.is_past_deadline
                else deadline_info.applicability
            )
            message = (
                "The configured target date has passed; projections are unavailable."
                if deadline_info.is_past_deadline
                else deadline_info.message
            )
            return {
                "projection_available": False,
                "unavailable_reason": unavailable_reason,
                "message": message,
                "deadline": deadline,
            }

        # Get 30-day trend
        trend = SnapshotService.get_historical_trend(db, department_id, days=30)

        verified_scores = [
            point.avg_compliance_score
            for point in trend
            if point.avg_compliance_score is not None
        ]

        if len(verified_scores) < 7:
            return {
                "projection_available": False,
                "message": (
                    "Need at least 7 days of verified compliance data for projection"
                ),
                "deadline": deadline,
            }

        # Calculate improvement rate (points per day)
        first_week_avg = sum(verified_scores[:7]) / 7
        last_week_avg = sum(verified_scores[-7:]) / 7
        improvement_rate = (last_week_avg - first_week_avg) / 21  # Over ~3 weeks

        days_remaining = deadline_info.days_remaining
        if days_remaining is None:
            raise ValueError("Dated deadline is missing remaining-day metadata")

        # Project final score
        projected_score = last_week_avg + (improvement_rate * days_remaining)
        projected_score = min(100, max(0, projected_score))  # Clamp to 0-100

        # Determine if on track
        will_meet_deadline = projected_score >= 90
        score_needed = 90 - last_week_avg
        improvement_needed_per_day = (
            score_needed / days_remaining if days_remaining > 0 else 0
        )

        if deadline_info.is_past_deadline:
            recommendation = (
                "The configured target date has passed; prioritize current "
                "accessibility work and review the institution profile."
            )
        elif will_meet_deadline:
            recommendation = "Projected automated scan score is on target."
        else:
            recommendation = (
                f"Improve the automated scan score by "
                f"{improvement_needed_per_day:.2f} points/day to reach the target."
            )

        return {
            "projection_available": True,
            "current_avg_score": round(last_week_avg, 2),
            "projected_score_at_deadline": round(projected_score, 2),
            "days_until_deadline": days_remaining,
            "improvement_rate_per_day": round(improvement_rate, 4),
            "will_meet_deadline": will_meet_deadline,
            "score_needed_to_comply": round(max(0, score_needed), 2),
            "required_improvement_per_day": round(
                max(0, improvement_needed_per_day), 4
            ),
            "recommendation": recommendation,
            "deadline": deadline,
        }
