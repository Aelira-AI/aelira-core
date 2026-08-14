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
from typing import Dict, List
from datetime import datetime, timedelta, date
from dataclasses import dataclass
import logging
import uuid

from ..db.models import (
    Department,
    Scan,
    ScanResult,
    ScanStatus,
    ComplianceSnapshot,
)
from .compliance_dashboard import ComplianceDashboard
from .deadline_config import US_ADA_TITLE_II_DEADLINE

logger = logging.getLogger(__name__)


@dataclass
class TrendPoint:
    """Single data point in a trend chart"""

    date: str
    avg_compliance_score: float
    scan_count: int
    total_issues: int
    files_compliant: int
    files_needs_work: int
    files_critical: int


@dataclass
class TrendAnalysis:
    """Analysis comparing two time periods"""

    current_avg_score: float
    previous_avg_score: float
    score_change: float
    score_change_pct: float
    current_total_issues: int
    previous_total_issues: int
    issues_change: int
    issues_change_pct: float
    trend_direction: str  # 'improving', 'declining', 'stable'
    on_track_for_deadline: bool


class SnapshotService:
    """
    Service for managing compliance snapshots and historical trending
    """

    # DOJ ADA Title II deadline, sourced from deadline_config (single source of truth).
    # Currently April 26, 2027 for large public entities (pop >= 50,000); extended
    # from April 24, 2026 via DOJ Interim Final Rule RIN 1190-AA82 (effective 2026-04-20).
    DEADLINE_DATE = datetime.combine(
        US_ADA_TITLE_II_DEADLINE, datetime.max.time()
    ).replace(microsecond=0)

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
        logger.info(f"Capturing daily snapshot for department: {department_id}")

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
            logger.warning(f"Department not found: {department_id}")
            raise

        # Calculate days until deadline
        now = datetime.utcnow()
        days_until_deadline = (SnapshotService.DEADLINE_DATE - now).days

        # Create or update snapshot
        if existing:
            logger.info(f"Updating existing snapshot for {today}")
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
        snapshot.days_until_deadline = days_until_deadline
        snapshot.estimated_hours_remaining = stats.estimated_hours_remaining
        snapshot.on_track = stats.on_track

        db.commit()
        db.refresh(snapshot)

        logger.info(
            f"Snapshot captured: score={stats.avg_compliance_score}, issues={stats.total_issues}"
        )
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
                logger.error(f"Failed to capture snapshot for {dept.id}: {e}")
                continue

        logger.info(f"Captured {len(snapshots)} snapshots")
        return snapshots

    @staticmethod
    def get_historical_trend(
        db: Session, department_id: str, days: int = 30
    ) -> List[TrendPoint]:
        """
        Get historical trend data from snapshots.

        Falls back to computing from scans if no snapshots exist.

        Args:
            db: Database session
            department_id: Department to query
            days: Number of days to look back

        Returns:
            List of TrendPoint objects for charting
        """
        logger.info(f"Getting {days}-day historical trend for {department_id}")

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Try to get from snapshots first
        snapshots = (
            db.query(ComplianceSnapshot)
            .filter(
                and_(
                    ComplianceSnapshot.department_id == department_id,
                    ComplianceSnapshot.snapshot_date >= cutoff_date,
                )
            )
            .order_by(ComplianceSnapshot.snapshot_date.asc())
            .all()
        )

        if snapshots:
            logger.info(f"Found {len(snapshots)} snapshots")
            return [
                TrendPoint(
                    date=s.snapshot_date.date().isoformat(),
                    avg_compliance_score=s.avg_compliance_score,
                    scan_count=s.total_scans,
                    total_issues=s.total_issues,
                    files_compliant=s.files_compliant,
                    files_needs_work=s.files_needs_work,
                    files_critical=s.files_critical,
                )
                for s in snapshots
            ]

        # Fallback: Compute from scans (slower but works without snapshots)
        logger.info("No snapshots found, computing from scans")
        return SnapshotService._compute_trend_from_scans(db, department_id, days)

    @staticmethod
    def _compute_trend_from_scans(
        db: Session, department_id: str, days: int
    ) -> List[TrendPoint]:
        """
        Compute trend data directly from scans table.

        Used as fallback when no snapshots exist.
        """
        from collections import defaultdict

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Get all scans in period
        scans = (
            db.query(Scan)
            .filter(
                and_(
                    Scan.department_id == department_id,
                    Scan.created_at >= cutoff_date,
                    Scan.status == ScanStatus.COMPLETED,
                )
            )
            .all()
        )

        if not scans:
            return []

        # Get results
        scan_ids = [s.id for s in scans]
        results = db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
        results_by_scan = {r.scan_id: r for r in results}

        # Group by date
        data_by_date = defaultdict(
            lambda: {
                "scores": [],
                "issues": 0,
                "compliant": 0,
                "needs_work": 0,
                "critical": 0,
            }
        )

        for scan in scans:
            date_key = scan.created_at.date().isoformat()
            result = results_by_scan.get(scan.id)

            if result:
                data_by_date[date_key]["scores"].append(result.compliance_score)
                data_by_date[date_key]["issues"] += (
                    result.critical_issues
                    + result.high_issues
                    + result.medium_issues
                    + result.low_issues
                )

                if result.compliance_score >= 90:
                    data_by_date[date_key]["compliant"] += 1
                elif result.compliance_score >= 70:
                    data_by_date[date_key]["needs_work"] += 1
                else:
                    data_by_date[date_key]["critical"] += 1

        # Convert to TrendPoints
        trend_points = []
        for date_str in sorted(data_by_date.keys()):
            data = data_by_date[date_str]
            avg_score = (
                sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
            )

            trend_points.append(
                TrendPoint(
                    date=date_str,
                    avg_compliance_score=round(avg_score, 2),
                    scan_count=len(data["scores"]),
                    total_issues=data["issues"],
                    files_compliant=data["compliant"],
                    files_needs_work=data["needs_work"],
                    files_critical=data["critical"],
                )
            )

        return trend_points

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

        # Get current period data
        current_start = now - timedelta(days=current_period_days)
        current_snapshots = (
            db.query(ComplianceSnapshot)
            .filter(
                and_(
                    ComplianceSnapshot.department_id == department_id,
                    ComplianceSnapshot.snapshot_date >= current_start,
                )
            )
            .all()
        )

        # Get previous period data
        previous_start = current_start - timedelta(days=comparison_period_days)
        previous_snapshots = (
            db.query(ComplianceSnapshot)
            .filter(
                and_(
                    ComplianceSnapshot.department_id == department_id,
                    ComplianceSnapshot.snapshot_date >= previous_start,
                    ComplianceSnapshot.snapshot_date < current_start,
                )
            )
            .all()
        )

        # Calculate averages
        current_scores = [s.avg_compliance_score for s in current_snapshots]
        previous_scores = [s.avg_compliance_score for s in previous_snapshots]

        current_avg = sum(current_scores) / len(current_scores) if current_scores else 0
        previous_avg = (
            sum(previous_scores) / len(previous_scores) if previous_scores else 0
        )

        current_issues = sum(s.total_issues for s in current_snapshots)
        previous_issues = sum(s.total_issues for s in previous_snapshots)

        # Calculate changes
        score_change = current_avg - previous_avg
        score_change_pct = (
            (score_change / previous_avg * 100) if previous_avg > 0 else 0
        )

        issues_change = current_issues - previous_issues
        issues_change_pct = (
            (issues_change / previous_issues * 100) if previous_issues > 0 else 0
        )

        # Determine trend direction
        if score_change > 2:
            trend_direction = "improving"
        elif score_change < -2:
            trend_direction = "declining"
        else:
            trend_direction = "stable"

        # Check if on track for deadline
        (SnapshotService.DEADLINE_DATE - now).days
        on_track = current_avg >= 80 or (
            current_avg >= 70 and trend_direction == "improving"
        )

        return TrendAnalysis(
            current_avg_score=round(current_avg, 2),
            previous_avg_score=round(previous_avg, 2),
            score_change=round(score_change, 2),
            score_change_pct=round(score_change_pct, 2),
            current_total_issues=current_issues,
            previous_total_issues=previous_issues,
            issues_change=issues_change,
            issues_change_pct=round(issues_change_pct, 2),
            trend_direction=trend_direction,
            on_track_for_deadline=on_track,
        )

    @staticmethod
    def get_deadline_projection(db: Session, department_id: str) -> Dict:
        """
        Project whether department will meet the DOJ ADA Title II deadline.

        The deadline is sourced from ``deadline_config`` (currently April 26,
        2027 for large public entities; extended from April 24, 2026 via the
        April 2026 DOJ IFR). Projection is based on historical trend and
        current pace.

        Returns:
            Dictionary with projection data
        """
        # Get 30-day trend
        trend = SnapshotService.get_historical_trend(db, department_id, days=30)

        if len(trend) < 7:
            return {
                "projection_available": False,
                "message": "Need at least 7 days of data for projection",
            }

        # Calculate improvement rate (points per day)
        first_week_avg = sum(p.avg_compliance_score for p in trend[:7]) / 7
        last_week_avg = sum(p.avg_compliance_score for p in trend[-7:]) / 7
        improvement_rate = (last_week_avg - first_week_avg) / 21  # Over ~3 weeks

        # Days until deadline
        days_remaining = (SnapshotService.DEADLINE_DATE - datetime.utcnow()).days

        # Project final score
        projected_score = last_week_avg + (improvement_rate * days_remaining)
        projected_score = min(100, max(0, projected_score))  # Clamp to 0-100

        # Determine if on track
        will_meet_deadline = projected_score >= 90
        score_needed = 90 - last_week_avg
        improvement_needed_per_day = (
            score_needed / days_remaining if days_remaining > 0 else 0
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
            "recommendation": (
                "On track to meet deadline!"
                if will_meet_deadline
                else f"Need to improve {improvement_needed_per_day:.2f} points/day to meet deadline"
            ),
        }
