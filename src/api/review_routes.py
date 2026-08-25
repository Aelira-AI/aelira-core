"""API routes for remediation review workflow.

Provides endpoints for:
- Review queue: paginated list of documents needing review
- Queue statistics: aggregate counts by review status
- Document review: detailed view of all fixes and Matterhorn results
- Fix actions: approve, reject, or edit individual fixes
- Batch actions: approve/reject multiple fixes by threshold or category
- Audit trail: chronological log of all review actions
- Audit export: export audit trail in JSON, CSV, or PDF format
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from ..auth.dependencies import get_required_api_key
from ..db.database import get_db_dependency
from ..db.models import (
    Department,
    Scan,
    ScanFix,
    ScanType,
    MatterhornResult,
    ReviewAuditLog,
    User,
)
from ..education.reports.compliance_report import AuditReportGenerator
from ..services.scan_fix_service import (
    apply_authenticated_batch_review,
    invalidate_current_artifact_approvals,
    lock_scan_review_graph,
    validate_fix_review_action,
)

logger = logging.getLogger(__name__)

# Map frontend-friendly scan type names to ScanType enum values.
# The frontend sends lowercase names; the DB stores uppercase enum values.
_SCAN_TYPE_MAP: dict[str, ScanType] = {
    "pdf": ScanType.PDF,
    "word": ScanType.WORD,
    "excel": ScanType.EXCEL,
    "powerpoint": ScanType.POWERPOINT,
    "latex": ScanType.LATEX,
    "web": ScanType.WEBSITE,
    "website": ScanType.WEBSITE,
    "code": ScanType.CODE,
    "multimedia": ScanType.MULTIMEDIA,
    "image": ScanType.IMAGE,
    "video": ScanType.VIDEO,
    "batch": ScanType.BATCH,
}


def _scan_type_display(scan_type: object) -> str:
    """Convert a ScanType enum (or string) to a lowercase display string."""
    val = scan_type.value if hasattr(scan_type, "value") else str(scan_type)
    return val.lower()


router = APIRouter(prefix="/reviews", tags=["reviews"])
get_auth = get_required_api_key


# -- Response Models --


class FixSummary(BaseModel):
    id: str
    category: str
    severity: str
    description: str
    confidence: float
    fix_method: str
    needs_review: bool
    review_status: str
    page_number: Optional[int] = None
    original_content: Optional[str] = None
    fixed_content: Optional[str] = None
    wcag_criteria: Optional[str] = None
    location: Optional[str] = None
    review_notes: Optional[str] = None


class QueueItem(BaseModel):
    scan_id: str
    file_name: str
    department_id: Optional[str] = None
    scan_type: Optional[str] = None
    total_fixes: int
    needs_review_count: int
    lowest_confidence: float
    status: str  # pending / approved
    created_at: datetime


class QueueStats(BaseModel):
    pending: int
    in_review: int
    approved: int
    rejected: int
    total: int
    by_type: Optional[dict[str, int]] = None


class DocumentReview(BaseModel):
    scan_id: str
    file_name: str
    fixes: list[FixSummary]
    matterhorn_total: int
    matterhorn_passed: int
    matterhorn_failed: int
    compliance_level: str


class FixAction(BaseModel):
    action: Literal["approve", "reject", "edit"]
    notes: Optional[str] = None
    edited_content: Optional[str] = None


class BatchAction(BaseModel):
    action: Literal["approve", "reject"]
    min_confidence: Optional[float] = None
    category: Optional[str] = None
    fix_ids: Optional[list[str]] = None
    notes: Optional[str] = None


class ReviewResponse(BaseModel):
    status: str
    fix_id: str
    review_status: str


class BatchResponse(BaseModel):
    status: str
    affected: int


class AuditEntry(BaseModel):
    id: str
    action: str
    user_name: Optional[str] = None
    details: Optional[dict] = None
    created_at: datetime


class DepartmentSummary(BaseModel):
    total_documents: int
    reviewed_percent: float
    approved_count: int
    pending_count: int
    rejected_count: int
    avg_confidence: float
    by_type: Optional[dict[str, int]] = None


# -- Helper functions --


def compute_compliance_level(total: int, failed: int) -> str:
    """Determine compliance level from Matterhorn results.

    Returns:
        "not_validated" if no checkpoints were run,
        "compliant" if zero failures,
        "partial" if failures <= 20% of total,
        "non_compliant" otherwise.
    """
    if total == 0:
        return "not_validated"
    if failed == 0:
        return "compliant"
    if failed <= total * 0.2:
        return "partial"
    return "non_compliant"


def compute_doc_status(needs_review_count: int) -> str:
    """Determine document-level review status.

    A document is 'approved' when no fixes remain pending review.
    """
    return "approved" if needs_review_count == 0 else "pending"


# -- Endpoints --


@router.get("/queue", response_model=list[QueueItem])
def get_review_queue(
    department_id: Optional[str] = Query(None),
    status: Optional[Literal["pending", "approved"]] = Query(None),
    scan_type: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Get paginated review queue sorted by lowest confidence."""
    _, _user_id, auth_department_id = auth_result
    effective_department_id = department_id if department_id else auth_department_id

    query = (
        db.query(
            Scan.id.label("scan_id"),
            Scan.file_name,
            Scan.department_id,
            Scan.scan_type,
            func.count(ScanFix.id).label("total_fixes"),
            func.sum(
                case((ScanFix.needs_review == True, 1), else_=0)
            ).label(  # noqa: E712
                "needs_review_count"
            ),
            func.min(ScanFix.confidence).label("lowest_confidence"),
            Scan.created_at,
        )
        .join(ScanFix, ScanFix.scan_id == Scan.id)
        .group_by(Scan.id)
    )

    if effective_department_id:
        query = query.filter(Scan.department_id == effective_department_id)

    if scan_type:
        enum_val = _SCAN_TYPE_MAP.get(scan_type.lower())
        if enum_val:
            query = query.filter(Scan.scan_type == enum_val)

    # Filter by review status
    if status == "pending":
        query = query.having(
            func.sum(case((ScanFix.review_status == "pending", 1), else_=0)) > 0
        )
    elif status == "approved":
        query = query.having(
            func.sum(case((ScanFix.review_status == "pending", 1), else_=0)) == 0
        )

    rows = (
        query.order_by(func.min(ScanFix.confidence).asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    results = []
    for row in rows:
        pending_count = row.needs_review_count or 0
        doc_status = compute_doc_status(pending_count)
        results.append(
            QueueItem(
                scan_id=row.scan_id,
                file_name=row.file_name,
                department_id=row.department_id,
                scan_type=_scan_type_display(row.scan_type) if row.scan_type else None,
                total_fixes=row.total_fixes,
                needs_review_count=pending_count,
                lowest_confidence=row.lowest_confidence or 1.0,
                status=doc_status,
                created_at=row.created_at,
            )
        )

    return results


@router.get("/queue/stats", response_model=QueueStats)
def get_review_stats(
    department_id: Optional[str] = Query(None),
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Get aggregate review queue statistics."""
    _, _user_id, auth_department_id = auth_result
    effective_department_id = department_id if department_id else auth_department_id

    query = db.query(ScanFix.review_status, func.count(ScanFix.id))
    if effective_department_id:
        query = query.join(Scan, Scan.id == ScanFix.scan_id).filter(
            Scan.department_id == effective_department_id
        )
    rows = query.group_by(ScanFix.review_status).all()

    counts = {status: count for status, count in rows}

    # Per-type breakdown
    type_query = db.query(Scan.scan_type, func.count(func.distinct(Scan.id))).join(
        ScanFix, ScanFix.scan_id == Scan.id
    )
    if effective_department_id:
        type_query = type_query.filter(Scan.department_id == effective_department_id)
    type_counts = type_query.group_by(Scan.scan_type).all()
    by_type = {
        _scan_type_display(scan_type) if scan_type else "unknown": count
        for scan_type, count in type_counts
    }

    return QueueStats(
        pending=counts.get("pending", 0),
        in_review=counts.get("in_review", 0),
        approved=counts.get("approved", 0) + counts.get("auto_approved", 0),
        rejected=counts.get("rejected", 0),
        total=sum(counts.values()),
        by_type=by_type if by_type else None,
    )


@router.get("/department-summary", response_model=DepartmentSummary)
def get_department_summary(
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Get aggregate review summary for the authenticated user's department.

    Returns total documents scanned, percentage reviewed, counts by status,
    and average fix confidence. Used by the dashboard widget.
    """
    _, _user_id, department_id = auth_result

    # 1. Count distinct scans with fixes for this department
    total_documents = (
        db.query(func.count(func.distinct(Scan.id)))
        .join(ScanFix, ScanFix.scan_id == Scan.id)
        .filter(Scan.department_id == department_id)
        .scalar()
    ) or 0

    # 2. Count fixes grouped by review_status
    status_rows = (
        db.query(ScanFix.review_status, func.count(ScanFix.id))
        .join(Scan, Scan.id == ScanFix.scan_id)
        .filter(Scan.department_id == department_id)
        .group_by(ScanFix.review_status)
        .all()
    )
    status_counts = {status: count for status, count in status_rows}

    approved_count = status_counts.get("approved", 0) + status_counts.get(
        "auto_approved", 0
    )
    rejected_count = status_counts.get("rejected", 0)
    # in_review is still pending human action, so count it as pending
    pending_count = status_counts.get("pending", 0) + status_counts.get("in_review", 0)
    total_fixes = sum(status_counts.values())

    # 3. Calculate reviewed percentage
    reviewed = approved_count + rejected_count
    if total_fixes > 0:
        reviewed_percent = round(reviewed / total_fixes * 100, 2)
    else:
        reviewed_percent = 0.0

    # 4. Average confidence across all fixes for this department
    avg_conf = (
        db.query(func.avg(ScanFix.confidence))
        .join(Scan, Scan.id == ScanFix.scan_id)
        .filter(Scan.department_id == department_id)
        .scalar()
    )

    # 5. Per-type document breakdown
    type_counts = (
        db.query(Scan.scan_type, func.count(func.distinct(Scan.id)))
        .join(ScanFix, ScanFix.scan_id == Scan.id)
        .filter(Scan.department_id == department_id)
        .group_by(Scan.scan_type)
        .all()
    )
    by_type = {
        _scan_type_display(scan_type) if scan_type else "unknown": count
        for scan_type, count in type_counts
    }

    return DepartmentSummary(
        total_documents=total_documents,
        reviewed_percent=reviewed_percent,
        approved_count=approved_count,
        pending_count=pending_count,
        rejected_count=rejected_count,
        avg_confidence=round(avg_conf, 4) if avg_conf is not None else 0.0,
        by_type=by_type if by_type else None,
    )


@router.get("/{scan_id}", response_model=DocumentReview)
def get_document_review(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Get document review data including all fixes and Matterhorn results."""
    _, _user_id, department_id = auth_result

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan or scan.department_id != department_id:
        raise HTTPException(status_code=404, detail="Scan not found")

    fixes = (
        db.query(ScanFix)
        .filter(ScanFix.scan_id == scan_id)
        .order_by(ScanFix.confidence.asc())
        .all()
    )

    matterhorn = (
        db.query(MatterhornResult).filter(MatterhornResult.scan_id == scan_id).all()
    )

    passed = sum(1 for m in matterhorn if m.status == "pass")
    failed = sum(1 for m in matterhorn if m.status == "fail")
    total = len(matterhorn)

    compliance = compute_compliance_level(total, failed)

    return DocumentReview(
        scan_id=scan_id,
        file_name=scan.file_name,
        fixes=[
            FixSummary(
                id=f.id,
                category=f.category,
                severity=f.severity,
                description=f.description,
                confidence=f.confidence,
                fix_method=f.fix_method,
                needs_review=f.needs_review,
                review_status=f.review_status,
                page_number=f.page_number,
                original_content=f.original_content,
                fixed_content=f.fixed_content,
                wcag_criteria=f.wcag_criteria,
                location=f.location,
                review_notes=f.review_notes,
            )
            for f in fixes
        ],
        matterhorn_total=total,
        matterhorn_passed=passed,
        matterhorn_failed=failed,
        compliance_level=compliance,
    )


@router.post("/{scan_id}/fixes/{fix_id}", response_model=ReviewResponse)
def review_fix(
    scan_id: str,
    fix_id: str,
    body: FixAction,
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Approve, reject, or edit a single fix."""
    _, user_id, department_id = auth_result

    # Verify scan belongs to the authenticated user's department
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan or scan.department_id != department_id:
        raise HTTPException(status_code=404, detail="Scan not found")

    graph = lock_scan_review_graph(db, scan_id)
    fix = next((row for row in graph.fixes if row.id == fix_id), None)
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")

    try:
        validate_fix_review_action(fix, body.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    now = datetime.now(timezone.utc)

    if body.action == "edit":
        if not body.edited_content:
            raise HTTPException(
                status_code=400,
                detail="edited_content is required for edit action",
            )
        fix.fixed_content = body.edited_content

    fix.review_status = "rejected" if body.action == "reject" else "approved"
    fix.reviewed_by = user_id
    fix.reviewed_at = now
    fix.review_notes = body.notes

    db.add(
        ReviewAuditLog(
            id=str(uuid.uuid4()),
            scan_id=scan_id,
            fix_id=fix_id,
            user_id=user_id,
            action=f"fix_{body.action}",
            details={"notes": body.notes, "edited": body.action == "edit"},
        )
    )
    invalidate_current_artifact_approvals(db, graph)

    db.commit()
    return ReviewResponse(status="ok", fix_id=fix_id, review_status=fix.review_status)


@router.post("/{scan_id}/batch", response_model=BatchResponse)
def batch_review(
    scan_id: str,
    body: BatchAction,
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Batch approve or reject fixes by threshold, category, or explicit IDs."""
    _, user_id, department_id = auth_result

    # Verify scan belongs to the authenticated user's department
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan or scan.department_id != department_id:
        raise HTTPException(status_code=404, detail="Scan not found")

    graph = lock_scan_review_graph(db, scan_id)
    fixes = [fix for fix in graph.fixes if fix.review_status == "pending"]
    if body.fix_ids:
        selected_ids = set(body.fix_ids)
        fixes = [fix for fix in fixes if fix.id in selected_ids]
    if body.min_confidence is not None:
        fixes = [fix for fix in fixes if fix.confidence >= body.min_confidence]
    if body.category:
        fixes = [fix for fix in fixes if fix.category == body.category]
    now = datetime.now(timezone.utc)

    apply_authenticated_batch_review(
        db,
        scan_id=scan_id,
        fixes=fixes,
        action=body.action,
        user_id=user_id,
        reviewed_at=now,
        notes=body.notes,
    )
    invalidate_current_artifact_approvals(db, graph)

    db.add(
        ReviewAuditLog(
            id=str(uuid.uuid4()),
            scan_id=scan_id,
            user_id=user_id,
            action=f"batch_{body.action}",
            details={
                "count": len(fixes),
                "fix_ids": [f.id for f in fixes],
                "min_confidence": body.min_confidence,
                "category": body.category,
            },
        )
    )

    db.commit()
    return BatchResponse(status="ok", affected=len(fixes))


@router.get("/{scan_id}/audit/export")
def export_audit_trail(
    scan_id: str,
    format: Literal["json", "csv", "pdf"] = Query("json"),
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Export the audit trail and compliance data in JSON, CSV, or PDF format.

    Generates a comprehensive report including issues found, fixes applied,
    review history, Matterhorn results, and WCAG conformance statement.
    """
    _, _user_id, department_id = auth_result

    # Verify scan belongs to the authenticated user's department
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan or scan.department_id != department_id:
        raise HTTPException(status_code=404, detail="Scan not found")

    # Fetch related data
    fixes = (
        db.query(ScanFix)
        .filter(ScanFix.scan_id == scan_id)
        .order_by(ScanFix.confidence.asc())
        .all()
    )

    matterhorn_results = (
        db.query(MatterhornResult).filter(MatterhornResult.scan_id == scan_id).all()
    )

    # Build audit entries with user names
    raw_entries = (
        db.query(ReviewAuditLog)
        .filter(ReviewAuditLog.scan_id == scan_id)
        .order_by(ReviewAuditLog.created_at.asc())
        .all()
    )

    # Batch-load user names to avoid N+1 queries
    user_ids = {e.user_id for e in raw_entries if e.user_id}
    user_map: dict[str, str] = {}
    if user_ids:
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        user_map = {u.id: u.name for u in users}

    audit_entries = []
    for e in raw_entries:
        user_name = user_map.get(e.user_id, "System") if e.user_id else "System"
        entry = type(
            "AuditEntryObj",
            (),
            {
                "id": e.id,
                "action": e.action,
                "user_name": user_name,
                "details": e.details,
                "created_at": e.created_at,
            },
        )()
        audit_entries.append(entry)

    # Fetch department for PDF/branding
    dept = db.query(Department).filter(Department.id == scan.department_id).first()
    if not dept:
        # Fallback: create a minimal department-like object
        dept = type(
            "DeptFallback",
            (),
            {
                "name": "Unknown Department",
                "institution": "Unknown Institution",
            },
        )()

    if format == "json":
        data = AuditReportGenerator.generate_json(
            scan=scan,
            fixes=fixes,
            audit_entries=audit_entries,
            matterhorn_results=matterhorn_results,
            department=dept,
        )
        return JSONResponse(content=data)

    elif format == "csv":
        csv_content = AuditReportGenerator.generate_csv(
            scan=scan,
            fixes=fixes,
            audit_entries=audit_entries,
            matterhorn_results=matterhorn_results,
            department=dept,
        )
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", scan_id)[:64]
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="audit-{safe_id}.csv"',
            },
        )

    else:  # pdf
        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=audit_entries,
            matterhorn_results=matterhorn_results,
            department=dept,
        )
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", scan_id)[:64]
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="compliance-report-{safe_id}.pdf"',
            },
        )


@router.get("/{scan_id}/audit", response_model=list[AuditEntry])
def get_audit_trail(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Get chronological audit trail for a document."""
    _, _user_id, department_id = auth_result

    # Verify scan belongs to the authenticated user's department
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan or scan.department_id != department_id:
        raise HTTPException(status_code=404, detail="Scan not found")

    entries = (
        db.query(ReviewAuditLog)
        .filter(ReviewAuditLog.scan_id == scan_id)
        .order_by(ReviewAuditLog.created_at.asc())
        .all()
    )

    # Batch-load user names to avoid N+1 queries
    user_ids = {e.user_id for e in entries if e.user_id}
    user_map: dict[str, str] = {}
    if user_ids:
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        user_map = {u.id: u.name for u in users}

    results = []
    for e in entries:
        user_name = user_map.get(e.user_id) if e.user_id else None

        results.append(
            AuditEntry(
                id=e.id,
                action=e.action,
                user_name=user_name or "System",
                details=e.details,
                created_at=e.created_at,
            )
        )

    return results
