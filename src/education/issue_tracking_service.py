"""
Issue Tracking Service - Team Collaboration & Issue Management

Provides persistent issue tracking across scans, enabling:
- Issue status management (open, in-progress, resolved)
- Team assignment and collaboration
- Resolution tracking and notes
- Auto-remediation status tracking

Author: Aelira Team
Created: November 30, 2025
"""

from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional
from datetime import datetime
from dataclasses import dataclass
import logging
import uuid
import hashlib

from ..db.models import (
    AuditLogAction,
    IssueTracking,
    IssueStatus,
    IssuePriority,
    Scan,
    ScanResult,
    User,
)
from ..security.audit_service import AuditService

logger = logging.getLogger(__name__)


@dataclass
class IssueStats:
    """Statistics for issue tracking"""

    total_issues: int
    open_issues: int
    in_progress_issues: int
    resolved_issues: int
    wont_fix_issues: int
    false_positive_issues: int
    auto_fixable_issues: int
    auto_fixed_issues: int
    resolution_rate: float  # Percentage resolved


@dataclass
class IssueSummary:
    """Summary of a tracked issue"""

    id: str
    scan_id: str
    file_name: str
    issue_type: str
    severity: str
    wcag_criterion: Optional[str]
    description: str
    status: str
    assigned_to_name: Optional[str]
    created_at: str
    updated_at: Optional[str]
    auto_fix_available: bool
    auto_fix_applied: bool


class IssueTrackingService:
    """
    Service for managing tracked issues across scans
    """

    @staticmethod
    def _get_issue_for_department(
        db: Session, issue_id: str, department_id: str
    ) -> IssueTracking:
        """Return one tenant-owned issue without revealing foreign IDs."""

        issue = (
            db.query(IssueTracking)
            .filter(
                IssueTracking.id == issue_id,
                IssueTracking.department_id == department_id,
            )
            .first()
        )
        if not issue:
            raise ValueError("Issue not found")
        return issue

    @staticmethod
    def _get_active_user_for_department(
        db: Session, user_id: str, department_id: str
    ) -> User:
        """Return an active tenant member without revealing foreign IDs."""

        user = (
            db.query(User)
            .filter(
                User.id == user_id,
                User.department_id == department_id,
                User.is_active.is_(True),
            )
            .first()
        )
        if not user:
            raise ValueError("User not found")
        return user

    @staticmethod
    def _stage_audit(
        db: Session,
        *,
        action: AuditLogAction,
        user_id: str,
        department_id: str,
        resource_id: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        """Stage an attributable audit entry in the mutation transaction."""

        AuditService(db).log_action(
            action=action,
            user_id=user_id,
            department_id=department_id,
            resource_type="issue_tracking",
            resource_id=resource_id,
            details=details,
            commit=False,
        )

    @staticmethod
    def generate_issue_hash(
        scan_id: str,
        issue_type: str,
        description: str,
        page_number: Optional[int] = None,
        slide_number: Optional[int] = None,
        element_selector: Optional[str] = None,
    ) -> str:
        """
        Generate a unique hash for an issue to detect duplicates.

        The hash is based on the issue's identifying characteristics,
        not the scan ID, so the same issue in different scans can be linked.
        """
        # Create a stable string representation
        hash_input = f"{issue_type}|{description}|{page_number}|{slide_number}|{element_selector}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:32]

    @staticmethod
    def create_tracked_issue(
        db: Session,
        scan_id: str,
        department_id: str,
        issue_type: str,
        severity: str,
        description: str,
        wcag_criterion: Optional[str] = None,
        element_selector: Optional[str] = None,
        page_number: Optional[int] = None,
        slide_number: Optional[int] = None,
        auto_fix_available: bool = False,
    ) -> IssueTracking:
        """
        Create a new tracked issue from a scan result.

        Args:
            db: Database session
            scan_id: ID of the scan that found this issue
            department_id: Department ID for multi-tenancy
            issue_type: Type of accessibility issue
            severity: Issue severity (CRITICAL, HIGH, MEDIUM, LOW)
            description: Human-readable description
            wcag_criterion: WCAG criterion (e.g., "1.1.1")
            element_selector: CSS selector or location identifier
            page_number: Page number (for PDFs)
            slide_number: Slide number (for PowerPoint)
            auto_fix_available: Whether AI can auto-fix this issue

        Returns:
            Created IssueTracking object
        """
        # Map severity string to enum
        severity_map = {
            "critical": IssuePriority.CRITICAL,
            "high": IssuePriority.HIGH,
            "medium": IssuePriority.MEDIUM,
            "low": IssuePriority.LOW,
        }
        priority = severity_map.get(severity.lower(), IssuePriority.MEDIUM)

        scan = (
            db.query(Scan)
            .filter(Scan.id == scan_id, Scan.department_id == department_id)
            .first()
        )
        if not scan:
            raise ValueError("Scan not found")

        # Generate issue hash
        issue_hash = IssueTrackingService.generate_issue_hash(
            scan_id,
            issue_type,
            description,
            page_number,
            slide_number,
            element_selector,
        )

        # Check if this exact issue already exists for this scan
        existing = (
            db.query(IssueTracking)
            .filter(
                and_(
                    IssueTracking.scan_id == scan_id,
                    IssueTracking.department_id == department_id,
                    IssueTracking.issue_hash == issue_hash,
                )
            )
            .first()
        )

        if existing:
            logger.debug("Issue already tracked for scan")
            return existing

        # Create new tracked issue
        issue = IssueTracking(
            id=str(uuid.uuid4()),
            scan_id=scan_id,
            department_id=department_id,
            issue_hash=issue_hash,
            issue_type=issue_type,
            severity=priority,
            wcag_criterion=wcag_criterion,
            description=description,
            element_selector=element_selector,
            page_number=page_number,
            slide_number=slide_number,
            status=IssueStatus.OPEN,
            auto_fix_available=auto_fix_available,
        )

        db.add(issue)
        db.commit()
        db.refresh(issue)

        logger.info("Created tracked issue")
        return issue

    @staticmethod
    def create_issues_from_scan_result(
        db: Session, scan: Scan, result: ScanResult
    ) -> List[IssueTracking]:
        """
        Create tracked issues from a scan result's issues array.

        Automatically extracts issues from the scan result and creates
        tracked issues for each one.

        Args:
            db: Database session
            scan: Scan object
            result: ScanResult object with issues array

        Returns:
            List of created IssueTracking objects
        """
        if not result.issues:
            return []

        tracked_issues = []

        for issue_data in result.issues:
            try:
                tracked = IssueTrackingService.create_tracked_issue(
                    db=db,
                    scan_id=scan.id,
                    department_id=scan.department_id,
                    issue_type=issue_data.get(
                        "type", issue_data.get("code", "unknown")
                    ),
                    severity=issue_data.get("severity", "medium"),
                    description=issue_data.get(
                        "description", issue_data.get("text", "No description")
                    ),
                    wcag_criterion=issue_data.get("wcag", issue_data.get("criterion")),
                    element_selector=issue_data.get(
                        "selector", issue_data.get("element")
                    ),
                    page_number=issue_data.get("page"),
                    slide_number=issue_data.get("slide"),
                    auto_fix_available=issue_data.get("auto_fixable", False),
                )
                tracked_issues.append(tracked)
            except Exception as e:
                logger.error("Tracked issue creation failed (%s)", type(e).__name__)
                continue

        logger.info("Created tracked issues from scan")
        return tracked_issues

    @staticmethod
    def update_issue_status(
        db: Session,
        issue_id: str,
        department_id: str,
        new_status: str,
        user_id: Optional[str] = None,
        resolution_notes: Optional[str] = None,
        resolution_method: Optional[str] = None,
    ) -> IssueTracking:
        """
        Update the status of a tracked issue.

        Args:
            db: Database session
            issue_id: ID of the issue to update
            department_id: Authenticated department that owns the issue
            new_status: New status (OPEN, IN_PROGRESS, RESOLVED, WONT_FIX, FALSE_POSITIVE)
            user_id: ID of user making the change
            resolution_notes: Notes about the resolution
            resolution_method: How the issue was resolved (auto, manual, wont_fix)

        Returns:
            Updated IssueTracking object
        """
        issue = IssueTrackingService._get_issue_for_department(
            db, issue_id, department_id
        )

        # Map status string to enum
        status_map = {
            "open": IssueStatus.OPEN,
            "in_progress": IssueStatus.IN_PROGRESS,
            "resolved": IssueStatus.RESOLVED,
            "wont_fix": IssueStatus.WONT_FIX,
            "false_positive": IssueStatus.FALSE_POSITIVE,
        }
        status = status_map.get(new_status.lower())
        if not status:
            raise ValueError(f"Invalid status: {new_status}")

        if user_id is not None:
            IssueTrackingService._get_active_user_for_department(
                db, user_id, department_id
            )

        # Update status
        issue.status = status
        issue.updated_at = datetime.utcnow()

        # Handle resolution
        if status in [
            IssueStatus.RESOLVED,
            IssueStatus.WONT_FIX,
            IssueStatus.FALSE_POSITIVE,
        ]:
            issue.resolved_by = user_id
            issue.resolved_at = datetime.utcnow()
            if resolution_notes:
                issue.resolution_notes = resolution_notes
            if resolution_method:
                issue.resolution_method = resolution_method

        if user_id is not None:
            IssueTrackingService._stage_audit(
                db,
                action=AuditLogAction.ISSUE_STATUS_UPDATE,
                user_id=user_id,
                department_id=department_id,
                resource_id=issue_id,
                details={"status": status.value},
            )

        db.commit()
        db.refresh(issue)

        logger.info("Updated tracked issue status")
        return issue

    @staticmethod
    def assign_issue(
        db: Session,
        issue_id: str,
        department_id: str,
        assigned_to: str,
        assigned_by: str,
    ) -> IssueTracking:
        """
        Assign an issue to a team member.

        Args:
            db: Database session
            issue_id: ID of the issue to assign
            department_id: Authenticated department that owns the issue and assignee
            assigned_to: User ID to assign to
            assigned_by: User ID making the assignment

        Returns:
            Updated IssueTracking object
        """
        issue = IssueTrackingService._get_issue_for_department(
            db, issue_id, department_id
        )
        try:
            IssueTrackingService._get_active_user_for_department(
                db, assigned_to, department_id
            )
            IssueTrackingService._get_active_user_for_department(
                db, assigned_by, department_id
            )
        except ValueError:
            raise ValueError("Assignee not found")

        issue.assigned_to = assigned_to
        issue.assigned_by = assigned_by
        issue.assigned_at = datetime.utcnow()
        issue.updated_at = datetime.utcnow()

        # Automatically set to IN_PROGRESS if currently OPEN
        if issue.status == IssueStatus.OPEN:
            issue.status = IssueStatus.IN_PROGRESS

        IssueTrackingService._stage_audit(
            db,
            action=AuditLogAction.ISSUE_ASSIGN,
            user_id=assigned_by,
            department_id=department_id,
            resource_id=issue_id,
        )

        db.commit()
        db.refresh(issue)

        logger.info("Assigned tracked issue to department member")
        return issue

    @staticmethod
    def add_issue_note(
        db: Session,
        issue_id: str,
        department_id: str,
        note: str,
        user_id: str,
    ) -> IssueTracking:
        """
        Add a note to an issue for team collaboration.

        Notes are appended with timestamp and user info.

        Args:
            db: Database session
            issue_id: ID of the issue
            department_id: Authenticated department that owns the issue and actor
            note: Note text to add
            user_id: ID of user adding the note

        Returns:
            Updated IssueTracking object
        """
        issue = IssueTrackingService._get_issue_for_department(
            db, issue_id, department_id
        )

        # Get user name
        user = IssueTrackingService._get_active_user_for_department(
            db, user_id, department_id
        )
        user_name = user.name or "Member"

        # Append note with timestamp
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        new_note = f"[{timestamp}] {user_name}: {note}"

        if issue.notes:
            issue.notes = f"{issue.notes}\n\n{new_note}"
        else:
            issue.notes = new_note

        issue.updated_at = datetime.utcnow()

        IssueTrackingService._stage_audit(
            db,
            action=AuditLogAction.ISSUE_NOTE_ADD,
            user_id=user_id,
            department_id=department_id,
            resource_id=issue_id,
        )

        db.commit()
        db.refresh(issue)

        logger.info("Added note to tracked issue")
        return issue

    @staticmethod
    def mark_auto_fixed(
        db: Session, issue_id: str, department_id: str, fix_result: str
    ) -> IssueTracking:
        """
        Mark an issue as auto-fixed by AI remediation.

        Args:
            db: Database session
            issue_id: ID of the issue
            department_id: Authenticated department that owns the issue
            fix_result: JSON string describing the fix applied

        Returns:
            Updated IssueTracking object
        """
        issue = IssueTrackingService._get_issue_for_department(
            db, issue_id, department_id
        )

        issue.auto_fix_applied = True
        issue.auto_fix_result = fix_result
        issue.status = IssueStatus.RESOLVED
        issue.resolution_method = "auto"
        issue.resolved_at = datetime.utcnow()
        issue.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(issue)

        logger.info("Marked tracked issue as auto-fixed")
        return issue

    @staticmethod
    def get_department_issues(
        db: Session,
        department_id: str,
        status_filter: Optional[str] = None,
        severity_filter: Optional[str] = None,
        assigned_to: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[IssueSummary]:
        """
        Get all tracked issues for a department with filters.

        Args:
            db: Database session
            department_id: Department to query
            status_filter: Optional status filter
            severity_filter: Optional severity filter
            assigned_to: Optional assignee filter
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of IssueSummary objects
        """
        query = (
            db.query(IssueTracking, Scan, User)
            .join(Scan, IssueTracking.scan_id == Scan.id)
            .outerjoin(
                User,
                and_(
                    IssueTracking.assigned_to == User.id,
                    User.department_id == department_id,
                ),
            )
            .filter(
                IssueTracking.department_id == department_id,
                Scan.department_id == department_id,
            )
        )

        # Apply filters
        if status_filter:
            status_map = {
                "open": IssueStatus.OPEN,
                "in_progress": IssueStatus.IN_PROGRESS,
                "resolved": IssueStatus.RESOLVED,
                "wont_fix": IssueStatus.WONT_FIX,
                "false_positive": IssueStatus.FALSE_POSITIVE,
            }
            status = status_map.get(status_filter.lower())
            if status:
                query = query.filter(IssueTracking.status == status)

        if severity_filter:
            severity_map = {
                "critical": IssuePriority.CRITICAL,
                "high": IssuePriority.HIGH,
                "medium": IssuePriority.MEDIUM,
                "low": IssuePriority.LOW,
            }
            severity = severity_map.get(severity_filter.lower())
            if severity:
                query = query.filter(IssueTracking.severity == severity)

        if assigned_to:
            query = query.filter(IssueTracking.assigned_to == assigned_to)

        # Order by severity (critical first) then created_at
        query = query.order_by(
            IssueTracking.severity.asc(),  # CRITICAL=0, LOW=3
            IssueTracking.created_at.desc(),
        )

        # Paginate
        results = query.offset(offset).limit(limit).all()

        return [
            IssueSummary(
                id=issue.id,
                scan_id=issue.scan_id,
                file_name=scan.file_name,
                issue_type=issue.issue_type,
                severity=issue.severity.value,
                wcag_criterion=issue.wcag_criterion,
                description=issue.description,
                status=issue.status.value,
                assigned_to_name=user.name if user else None,
                created_at=issue.created_at.isoformat() if issue.created_at else None,
                updated_at=issue.updated_at.isoformat() if issue.updated_at else None,
                auto_fix_available=issue.auto_fix_available or False,
                auto_fix_applied=issue.auto_fix_applied or False,
            )
            for issue, scan, user in results
        ]

    @staticmethod
    def get_issue_stats(db: Session, department_id: str) -> IssueStats:
        """
        Get issue statistics for a department.

        Args:
            db: Database session
            department_id: Department to query

        Returns:
            IssueStats object with counts
        """
        # Count by status
        status_counts = (
            db.query(IssueTracking.status, func.count(IssueTracking.id))
            .filter(IssueTracking.department_id == department_id)
            .group_by(IssueTracking.status)
            .all()
        )

        counts = {status.value: count for status, count in status_counts}

        total = sum(counts.values())
        open_count = counts.get("OPEN", 0)
        in_progress = counts.get("IN_PROGRESS", 0)
        resolved = counts.get("RESOLVED", 0)
        wont_fix = counts.get("WONT_FIX", 0)
        false_positive = counts.get("FALSE_POSITIVE", 0)

        # Count auto-fixable and auto-fixed
        auto_fixable = (
            db.query(func.count(IssueTracking.id))
            .filter(
                and_(
                    IssueTracking.department_id == department_id,
                    IssueTracking.auto_fix_available,
                )
            )
            .scalar()
            or 0
        )

        auto_fixed = (
            db.query(func.count(IssueTracking.id))
            .filter(
                and_(
                    IssueTracking.department_id == department_id,
                    IssueTracking.auto_fix_applied,
                )
            )
            .scalar()
            or 0
        )

        # Calculate resolution rate
        resolution_rate = (resolved / total * 100) if total > 0 else 0

        return IssueStats(
            total_issues=total,
            open_issues=open_count,
            in_progress_issues=in_progress,
            resolved_issues=resolved,
            wont_fix_issues=wont_fix,
            false_positive_issues=false_positive,
            auto_fixable_issues=auto_fixable,
            auto_fixed_issues=auto_fixed,
            resolution_rate=round(resolution_rate, 2),
        )

    @staticmethod
    def bulk_update_status(
        db: Session,
        issue_ids: List[str],
        department_id: str,
        new_status: str,
        user_id: Optional[str] = None,
    ) -> int:
        """
        Bulk update status for multiple issues.

        Args:
            db: Database session
            issue_ids: List of issue IDs to update
            department_id: Authenticated department that owns every requested issue
            new_status: New status to set
            user_id: ID of user making the change

        Returns:
            Number of issues updated
        """
        status_map = {
            "open": IssueStatus.OPEN,
            "in_progress": IssueStatus.IN_PROGRESS,
            "resolved": IssueStatus.RESOLVED,
            "wont_fix": IssueStatus.WONT_FIX,
            "false_positive": IssueStatus.FALSE_POSITIVE,
        }
        status = status_map.get(new_status.lower())
        if not status:
            raise ValueError(f"Invalid status: {new_status}")

        if user_id is not None:
            IssueTrackingService._get_active_user_for_department(
                db, user_id, department_id
            )

        unique_issue_ids = list(dict.fromkeys(issue_ids))
        if not unique_issue_ids:
            raise ValueError("At least one issue is required")

        owned_count = (
            db.query(func.count(IssueTracking.id))
            .filter(
                IssueTracking.department_id == department_id,
                IssueTracking.id.in_(unique_issue_ids),
            )
            .scalar()
            or 0
        )
        if owned_count != len(unique_issue_ids):
            raise ValueError("One or more issues not found")

        now = datetime.utcnow()

        # Build update dict
        update_dict = {"status": status, "updated_at": now}

        if status in [
            IssueStatus.RESOLVED,
            IssueStatus.WONT_FIX,
            IssueStatus.FALSE_POSITIVE,
        ]:
            update_dict["resolved_by"] = user_id
            update_dict["resolved_at"] = now

        count = (
            db.query(IssueTracking)
            .filter(
                IssueTracking.department_id == department_id,
                IssueTracking.id.in_(unique_issue_ids),
            )
            .update(update_dict, synchronize_session=False)
        )

        if count != len(unique_issue_ids):
            db.rollback()
            raise ValueError("One or more issues not found")

        if user_id is not None:
            IssueTrackingService._stage_audit(
                db,
                action=AuditLogAction.ISSUE_BULK_UPDATE,
                user_id=user_id,
                department_id=department_id,
                details={"status": status.value, "issue_count": count},
            )

        db.commit()

        logger.info("Bulk updated %s tracked issues", count)
        return count
