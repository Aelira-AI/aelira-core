"""
Compliance Dashboard API - Department-Wide Accessibility Tracking

Provides comprehensive compliance reporting and analytics for department administrators
to track WCAG 2.2 compliance across all scanned files.

Features:
- Department-wide compliance aggregation
- Priority-ranked issue lists (Critical → Low)
- Progress tracking over time
- Legal-ready compliance reports (PDF)
- Faculty compliance leaderboards
- Deadline tracking (April 2026)

Author: Aelira Team
Created: November 1, 2025
"""

from sqlalchemy.orm import Session
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import logging

from ..db.models import (
    Department,
    User,
    Scan,
    ScanResult,
    ScanType,
    ScanStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class ComplianceStats:
    """Department-wide compliance statistics"""

    department_id: str
    department_name: str
    institution: str

    # Overall stats
    total_scans: int
    total_files_scanned: int  # Unique files (deduped by hash)
    total_pages_slides: int

    # Compliance scoring
    avg_compliance_score: float
    min_compliance_score: float
    max_compliance_score: float

    # Issue counts
    total_critical: int
    total_high: int
    total_medium: int
    total_low: int
    total_issues: int

    # Scan type breakdown
    pdf_scans: int
    powerpoint_scans: int
    latex_scans: int
    image_scans: int
    video_scans: int
    website_scans: int
    code_scans: int
    multimedia_scans: int

    # Compliance targets
    files_compliant: int  # Score >= 90
    files_needs_work: int  # Score 70-89
    files_critical: int  # Score < 70
    compliance_rate: float  # % of files >= 90

    # Time-based metrics
    scans_last_7_days: int
    scans_last_30_days: int
    scans_this_month: int

    # Deadline tracking (region-aware, Phase 4.1)
    days_until_deadline: Optional[int]  # None if no deadline applies
    estimated_hours_remaining: float  # Based on avg remediation time
    on_track: bool  # Whether department will meet deadline
    has_deadline: bool = True  # Whether a regulatory deadline applies
    deadline_framework: str = "US_ADA_TITLE_II"  # Which regulatory framework
    deadline_standard: str = "WCAG 2.2 Level AA"  # Which accessibility standard

    # User stats
    active_faculty: int = 0
    total_faculty: int = 0
    faculty_participation_rate: float = 0.0

    # Color Vision Deficiency (CVD) accessibility metrics
    cvd_issues_total: int = 0  # Total CVD accessibility issues
    cvd_affected_files: int = 0  # Files with CVD issues
    cvd_accessibility_rate: float = 100.0  # % of files with no CVD issues

    def to_report_dict(self) -> dict:
        """Convert flat dataclass to nested dict expected by ComplianceReportGenerator."""
        return {
            "department_name": self.department_name,
            "institution": self.institution,
            "overview": {
                "total_scans": self.total_scans,
                "total_files_scanned": self.total_files_scanned,
                "compliance_rate": self.compliance_rate,
            },
            "compliance_scores": {
                "average": self.avg_compliance_score,
                "min": self.min_compliance_score,
                "max": self.max_compliance_score,
            },
            "issues": {
                "total": self.total_issues,
                "critical": self.total_critical,
                "high": self.total_high,
                "medium": self.total_medium,
                "low": self.total_low,
            },
            "scan_types": {
                "pdf": self.pdf_scans,
                "powerpoint": self.powerpoint_scans,
                "latex": self.latex_scans,
                "image": self.image_scans,
                "video": self.video_scans,
                "website": self.website_scans,
                "code": self.code_scans,
            },
            "compliance_breakdown": {
                "compliant": self.files_compliant,
                "needs_work": self.files_needs_work,
                "critical": self.files_critical,
            },
            "faculty": {
                "active": self.active_faculty,
                "total": self.total_faculty,
                "participation_rate": self.faculty_participation_rate,
            },
            "april_2026_deadline": {
                "days_remaining": self.days_until_deadline or 0,
                "estimated_hours_remaining": self.estimated_hours_remaining,
                "on_track": self.on_track,
                "has_deadline": self.has_deadline,
                "framework": self.deadline_framework,
                "standard": self.deadline_standard,
            },
        }


@dataclass
class PriorityIssue:
    """High-priority issue that needs remediation"""

    scan_id: str
    file_name: str
    scan_type: str
    severity: str  # critical, high, medium, low
    issue_type: str
    description: str
    page_slide_number: Optional[int]
    created_at: datetime
    user_name: str
    compliance_score: float
    estimated_fix_time_minutes: int


@dataclass
class FacultyLeaderboard:
    """Faculty member compliance metrics"""

    user_id: str
    user_name: str
    user_email: str

    total_scans: int
    avg_compliance_score: float
    total_files: int
    total_issues_fixed: int

    rank: int  # 1 = highest compliance
    badge: str  # "Accessibility Champion", "Making Progress", etc.


class ComplianceDashboard:
    """
    Department-wide compliance dashboard service

    Provides aggregation and reporting for department administrators
    to track accessibility compliance across all faculty and files.
    """

    # April 24, 2026 WCAG 2.2 deadline
    DEADLINE_DATE = datetime(2026, 4, 24, 23, 59, 59)

    # Compliance thresholds
    COMPLIANCE_THRESHOLD = 90.0  # Score >= 90 is "compliant"
    NEEDS_WORK_THRESHOLD = 70.0  # Score 70-89 is "needs work"
    # Score < 70 is "critical"

    # Average remediation time estimates (minutes per issue)
    REMEDIATION_TIME = {
        "critical": 30,  # 30 min per critical issue
        "high": 15,  # 15 min per high issue
        "medium": 10,  # 10 min per medium issue
        "low": 5,  # 5 min per low issue
    }

    @staticmethod
    def get_department_compliance(db: Session, department_id: str) -> ComplianceStats:
        """
        Get comprehensive compliance statistics for a department

        Args:
            db: Database session
            department_id: Department to analyze

        Returns:
            ComplianceStats object with all metrics
        """
        logger.info(f"Generating compliance stats for department: {department_id}")

        # Get department info
        dept = db.query(Department).filter(Department.id == department_id).first()
        if not dept:
            raise ValueError(f"Department not found: {department_id}")

        # Get all scans for department
        scans = db.query(Scan).filter(Scan.department_id == department_id).all()

        if not scans:
            return ComplianceDashboard._empty_stats(
                department_id, dept.name, dept.institution, dept
            )

        # Get all scan results
        scan_ids = [s.id for s in scans]
        results = db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
        results_by_scan = {r.scan_id: r for r in results}

        # Calculate time-based metrics (use timezone-aware datetimes)
        now = datetime.now(timezone.utc)
        seven_days_ago = now - timedelta(days=7)
        thirty_days_ago = now - timedelta(days=30)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Count scans by type
        scan_type_counts = {
            ScanType.PDF: 0,
            ScanType.POWERPOINT: 0,
            ScanType.LATEX: 0,
            ScanType.IMAGE: 0,
            ScanType.VIDEO: 0,
            ScanType.WEBSITE: 0,
            ScanType.CODE: 0,
            ScanType.MULTIMEDIA: 0,
        }

        # Aggregate metrics
        total_pages = 0
        compliance_scores = []
        total_critical = total_high = total_medium = total_low = 0
        files_compliant = files_needs_work = files_critical = 0
        scans_7d = scans_30d = scans_month = 0
        unique_files = set()

        for scan in scans:
            # Count by type
            if scan.scan_type in scan_type_counts:
                scan_type_counts[scan.scan_type] += 1

            # Pages/slides
            total_pages += scan.pages or 0

            # Unique files (by hash)
            if scan.file_hash:
                unique_files.add(scan.file_hash)

            # Time-based counts
            if scan.created_at >= seven_days_ago:
                scans_7d += 1
            if scan.created_at >= thirty_days_ago:
                scans_30d += 1
            if scan.created_at >= month_start:
                scans_month += 1

            # Get result
            result = results_by_scan.get(scan.id)
            if result:
                compliance_scores.append(result.compliance_score)
                total_critical += result.critical_issues
                total_high += result.high_issues
                total_medium += result.medium_issues
                total_low += result.low_issues

                # Categorize compliance
                if result.compliance_score >= ComplianceDashboard.COMPLIANCE_THRESHOLD:
                    files_compliant += 1
                elif (
                    result.compliance_score >= ComplianceDashboard.NEEDS_WORK_THRESHOLD
                ):
                    files_needs_work += 1
                else:
                    files_critical += 1

        # Calculate averages
        avg_score = (
            sum(compliance_scores) / len(compliance_scores) if compliance_scores else 0
        )
        min_score = min(compliance_scores) if compliance_scores else 0
        max_score = max(compliance_scores) if compliance_scores else 0

        # Compliance rate
        compliance_rate = (files_compliant / len(scans) * 100) if scans else 0

        # Faculty stats
        total_faculty = (
            db.query(User).filter(User.department_id == department_id).count()
        )
        active_faculty_ids = set(scan.user_id for scan in scans)
        active_faculty = len(active_faculty_ids)
        participation_rate = (
            (active_faculty / total_faculty * 100) if total_faculty > 0 else 0
        )

        # Deadline tracking (region-aware, Phase 4.1)
        from .deadline_config import DeadlineService

        deadline_info = DeadlineService.get_deadline_info(
            country_code=getattr(dept, "country_code", "US"),
            regulatory_framework=getattr(dept, "regulatory_framework", None),
            custom_deadline=getattr(dept, "custom_deadline", None),
        )

        # Estimate remaining work
        total_issues = total_critical + total_high + total_medium + total_low
        estimated_hours = (
            total_critical * ComplianceDashboard.REMEDIATION_TIME["critical"]
            + total_high * ComplianceDashboard.REMEDIATION_TIME["high"]
            + total_medium * ComplianceDashboard.REMEDIATION_TIME["medium"]
            + total_low * ComplianceDashboard.REMEDIATION_TIME["low"]
        ) / 60.0  # Convert to hours

        # On track calculation (only if deadline applies)
        if deadline_info.has_deadline and deadline_info.days_remaining is not None:
            days_until_deadline = deadline_info.days_remaining
            # Assume 4 hours/day of productive work
            hours_available = deadline_info.days_remaining * 4
            on_track = hours_available >= estimated_hours or compliance_rate >= 80.0
        else:
            days_until_deadline = None
            on_track = (
                compliance_rate >= 80.0
            )  # No deadline, just track compliance rate

        return ComplianceStats(
            department_id=department_id,
            department_name=dept.name,
            institution=dept.institution,
            total_scans=len(scans),
            total_files_scanned=len(unique_files),
            total_pages_slides=total_pages,
            avg_compliance_score=round(avg_score, 2),
            min_compliance_score=round(min_score, 2),
            max_compliance_score=round(max_score, 2),
            total_critical=total_critical,
            total_high=total_high,
            total_medium=total_medium,
            total_low=total_low,
            total_issues=total_issues,
            pdf_scans=scan_type_counts[ScanType.PDF],
            powerpoint_scans=scan_type_counts[ScanType.POWERPOINT],
            latex_scans=scan_type_counts[ScanType.LATEX],
            image_scans=scan_type_counts[ScanType.IMAGE],
            video_scans=scan_type_counts[ScanType.VIDEO],
            website_scans=scan_type_counts[ScanType.WEBSITE],
            code_scans=scan_type_counts[ScanType.CODE],
            multimedia_scans=scan_type_counts[ScanType.MULTIMEDIA],
            files_compliant=files_compliant,
            files_needs_work=files_needs_work,
            files_critical=files_critical,
            compliance_rate=round(compliance_rate, 2),
            scans_last_7_days=scans_7d,
            scans_last_30_days=scans_30d,
            scans_this_month=scans_month,
            days_until_deadline=days_until_deadline,
            estimated_hours_remaining=round(estimated_hours, 2),
            on_track=on_track,
            has_deadline=deadline_info.has_deadline,
            deadline_framework=deadline_info.framework_code,
            deadline_standard=deadline_info.standard,
            active_faculty=active_faculty,
            total_faculty=total_faculty,
            faculty_participation_rate=round(participation_rate, 2),
            # CVD metrics (TODO: populate from scan results when cvd_analysis is stored)
            cvd_issues_total=0,
            cvd_affected_files=0,
            cvd_accessibility_rate=100.0,
        )

    @staticmethod
    def _empty_stats(
        dept_id: str, dept_name: str, institution: str, dept=None
    ) -> ComplianceStats:
        """Return empty stats for departments with no scans"""
        from .deadline_config import DeadlineService

        # Get deadline info based on department region (if available)
        deadline_info = DeadlineService.get_deadline_info(
            country_code=getattr(dept, "country_code", "US") if dept else "US",
            regulatory_framework=(
                getattr(dept, "regulatory_framework", None) if dept else None
            ),
            custom_deadline=getattr(dept, "custom_deadline", None) if dept else None,
        )

        return ComplianceStats(
            department_id=dept_id,
            department_name=dept_name,
            institution=institution,
            total_scans=0,
            total_files_scanned=0,
            total_pages_slides=0,
            avg_compliance_score=0.0,
            min_compliance_score=0.0,
            max_compliance_score=0.0,
            total_critical=0,
            total_high=0,
            total_medium=0,
            total_low=0,
            total_issues=0,
            pdf_scans=0,
            powerpoint_scans=0,
            latex_scans=0,
            image_scans=0,
            video_scans=0,
            website_scans=0,
            code_scans=0,
            multimedia_scans=0,
            files_compliant=0,
            files_needs_work=0,
            files_critical=0,
            compliance_rate=0.0,
            scans_last_7_days=0,
            scans_last_30_days=0,
            scans_this_month=0,
            days_until_deadline=deadline_info.days_remaining,
            estimated_hours_remaining=0.0,
            on_track=True,
            has_deadline=deadline_info.has_deadline,
            deadline_framework=deadline_info.framework_code,
            deadline_standard=deadline_info.standard,
            active_faculty=0,
            total_faculty=0,
            faculty_participation_rate=0.0,
            # CVD metrics
            cvd_issues_total=0,
            cvd_affected_files=0,
            cvd_accessibility_rate=100.0,
        )

    @staticmethod
    def get_priority_issues(
        db: Session, department_id: str, severity: Optional[str] = None, limit: int = 50
    ) -> List[PriorityIssue]:
        """
        Get prioritized list of issues that need remediation

        Args:
            db: Database session
            department_id: Department to query
            severity: Optional severity filter ('critical', 'high', 'medium', 'low')
            limit: Maximum number of issues to return

        Returns:
            List of PriorityIssue objects, sorted by severity and date
        """
        logger.info(f"Getting priority issues for department: {department_id}")

        # Get all scans with results
        scans = (
            db.query(Scan)
            .filter(Scan.department_id == department_id)
            .filter(Scan.status == ScanStatus.COMPLETED)
            .order_by(Scan.created_at.desc())
            .all()
        )

        if not scans:
            return []

        # Get scan results
        scan_ids = [s.id for s in scans]
        results = db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
        results_by_scan = {r.scan_id: r for r in results}

        # Get users for names
        user_ids = list(set(s.user_id for s in scans))
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        users_by_id = {u.id: u for u in users}

        # Flatten all issues
        priority_issues = []

        for scan in scans:
            result = results_by_scan.get(scan.id)
            if not result or not result.issues:
                continue

            user = users_by_id.get(scan.user_id)
            user_name = user.name if user else "Unknown"

            # Extract issues from JSON (handle various formats)
            issues_data = result.issues
            if isinstance(issues_data, str):
                # If issues is a JSON string, try to parse it
                try:
                    import json

                    issues_data = json.loads(issues_data)
                except (json.JSONDecodeError, TypeError):
                    issues_data = []

            if not isinstance(issues_data, list):
                issues_data = []

            for issue in issues_data:
                # Skip non-dict entries
                if not isinstance(issue, dict):
                    continue

                issue_severity = issue.get("severity", "low")

                # Apply severity filter
                if severity and issue_severity != severity:
                    continue

                # Estimate fix time
                fix_time = ComplianceDashboard.REMEDIATION_TIME.get(issue_severity, 5)

                priority_issue = PriorityIssue(
                    scan_id=scan.id,
                    file_name=scan.file_name,
                    scan_type=scan.scan_type.value,
                    severity=issue_severity,
                    issue_type=issue.get("type", "unknown"),
                    description=issue.get(
                        "description", issue.get("text", "No description")
                    ),
                    page_slide_number=issue.get("page") or issue.get("slide"),
                    created_at=scan.created_at,
                    user_name=user_name,
                    compliance_score=result.compliance_score,
                    estimated_fix_time_minutes=fix_time,
                )
                priority_issues.append(priority_issue)

        # Sort by severity (critical first) then by date (newest first)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        priority_issues.sort(
            key=lambda x: (
                severity_order.get(x.severity, 999),
                -x.created_at.timestamp(),
            )
        )

        return priority_issues[:limit]

    @staticmethod
    def get_faculty_leaderboard(
        db: Session, department_id: str, limit: int = 20
    ) -> List[FacultyLeaderboard]:
        """
        Get faculty compliance leaderboard

        Args:
            db: Database session
            department_id: Department to query
            limit: Number of faculty to include

        Returns:
            List of FacultyLeaderboard objects, sorted by compliance score
        """
        logger.info(f"Generating faculty leaderboard for department: {department_id}")

        # Get all faculty in department
        faculty = (
            db.query(User)
            .filter(User.department_id == department_id)
            .filter(User.is_active)
            .all()
        )

        if not faculty:
            return []

        # Calculate metrics for each faculty member
        leaderboard = []

        for user in faculty:
            # Get user's scans
            scans = (
                db.query(Scan)
                .filter(Scan.user_id == user.id)
                .filter(Scan.department_id == department_id)
                .all()
            )

            if not scans:
                # Include faculty with 0 scans
                leaderboard.append(
                    FacultyLeaderboard(
                        user_id=user.id,
                        user_name=user.name or "Unknown",
                        user_email=user.email,
                        total_scans=0,
                        avg_compliance_score=0.0,
                        total_files=0,
                        total_issues_fixed=0,
                        rank=0,
                        badge="Not Started",
                    )
                )
                continue

            # Get results
            scan_ids = [s.id for s in scans]
            results = (
                db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
            )

            # Calculate avg compliance
            scores = [r.compliance_score for r in results]
            avg_score = sum(scores) / len(scores) if scores else 0

            # Count unique files
            unique_files = len(set(s.file_hash for s in scans if s.file_hash))

            # Count issues (rough estimate of "fixed" issues)
            # We assume high compliance = many issues fixed
            total_issues_fixed = sum(
                r.critical_issues + r.high_issues + r.medium_issues + r.low_issues
                for r in results
            )

            # Assign badge
            if avg_score >= 95:
                badge = "🏆 Accessibility Champion"
            elif avg_score >= 90:
                badge = "⭐ Highly Compliant"
            elif avg_score >= 80:
                badge = "✅ Making Great Progress"
            elif avg_score >= 70:
                badge = "📈 Improving"
            elif len(scans) > 0:
                badge = "🚀 Getting Started"
            else:
                badge = "Not Started"

            leaderboard.append(
                FacultyLeaderboard(
                    user_id=user.id,
                    user_name=user.name or "Unknown",
                    user_email=user.email,
                    total_scans=len(scans),
                    avg_compliance_score=round(avg_score, 2),
                    total_files=unique_files,
                    total_issues_fixed=total_issues_fixed,
                    rank=0,  # Will be assigned after sorting
                    badge=badge,
                )
            )

        # Sort by avg compliance score (highest first)
        leaderboard.sort(key=lambda x: (-x.avg_compliance_score, -x.total_scans))

        # Assign ranks
        for i, entry in enumerate(leaderboard[:limit], start=1):
            entry.rank = i

        return leaderboard[:limit]

    @staticmethod
    def get_compliance_trend(
        db: Session, department_id: str, days: int = 30
    ) -> Dict[str, List]:
        """
        Get compliance score trends over time

        Args:
            db: Database session
            department_id: Department to analyze
            days: Number of days to look back

        Returns:
            Dictionary with date labels and compliance scores
        """
        from collections import defaultdict

        logger.info(
            f"Generating {days}-day compliance trend for department: {department_id}"
        )

        # Get scans from last N days (use timezone-aware datetime)
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        scans = (
            db.query(Scan)
            .filter(Scan.department_id == department_id)
            .filter(Scan.created_at >= cutoff_date)
            .order_by(Scan.created_at.asc())
            .all()
        )

        if not scans:
            return {"dates": [], "scores": [], "scans_per_day": []}

        # Get results
        scan_ids = [s.id for s in scans]
        results = db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
        results_by_scan = {r.scan_id: r for r in results}

        # Group by date
        scores_by_date = defaultdict(list)
        scans_by_date = defaultdict(int)

        for scan in scans:
            date_key = scan.created_at.date().isoformat()
            scans_by_date[date_key] += 1

            result = results_by_scan.get(scan.id)
            if result:
                scores_by_date[date_key].append(result.compliance_score)

        # Calculate daily averages
        dates = sorted(scores_by_date.keys())
        avg_scores = [
            round(sum(scores_by_date[date]) / len(scores_by_date[date]), 2)
            for date in dates
        ]
        scans_counts = [scans_by_date[date] for date in dates]

        return {"dates": dates, "scores": avg_scores, "scans_per_day": scans_counts}
