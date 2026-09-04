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

import hashlib
import hmac
import logging
import mimetypes
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from collections.abc import Iterable
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
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
    RemediationArtifact,
    ReviewAuditLog,
    User,
)
from ..education.equation_region_contract import PageRasterRegionLocator
from ..education.visual_semantic_contract import VisualSemanticContract
from ..education.reports.compliance_report import (
    ACCEPTED_REVIEW_STATUSES,
    AuditReportGenerator,
    artifact_evidence,
    bounded_audit_details,
)
from ..education.reports.evidence_package import (
    EvidenceFile,
    EvidencePackageError,
    build_evidence_package,
)
from ..services.remediation_artifact_service import (
    ArtifactError,
    RemediationArtifactService,
)
from ..services.scan_fix_service import (
    apply_authenticated_batch_review,
    bind_fix_review_decision,
    invalidate_current_artifact_approvals,
    lock_scan_review_graph,
    valid_sha256,
    validate_fix_review_action,
    validated_visual_semantic_contract,
    visual_semantic_disposition,
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

_MAX_INCLUDED_SOURCE_BYTES = 500 * 1024 * 1024

_AUTO_APPROVED_STATUS = "auto_approved"
_HUMAN_REVIEWED_STATUSES = frozenset({"approved", "edited", "rejected"})
_ACCEPTED_STATUSES = ACCEPTED_REVIEW_STATUSES
_TERMINAL_STATUSES = _ACCEPTED_STATUSES | {"rejected"}


def _audit_export_headers(scan_id: str, format: str) -> dict[str, str]:
    """Build stable attachment metadata without reflecting unsafe path text."""
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", scan_id)[:64] or "scan"
    prefix = "accessibility-review-evidence" if format == "pdf" else "audit"
    return {
        "Content-Disposition": f'attachment; filename="{prefix}-{safe_id}.{format}"',
        "Cache-Control": "no-store",
    }


def _evidence_package_headers(scan_id: str) -> dict[str, str]:
    """Build bounded attachment headers for a portable evidence package."""
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", scan_id)[:64] or "scan"
    return {
        "Content-Disposition": f'attachment; filename="aelira-evidence-{safe_id}.zip"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


def _read_verified_source(scan: Scan) -> EvidenceFile:
    """Read explicitly requested source bytes through a descriptor-bound check."""
    storage_path = getattr(scan, "storage_path", None)
    expected_size = getattr(scan, "file_size_bytes", None)
    expected_sha256 = getattr(scan, "file_hash", None)
    if (
        not isinstance(storage_path, str)
        or not storage_path
        or not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or expected_size < 0
        or expected_size > _MAX_INCLUDED_SOURCE_BYTES
        or not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        raise EvidencePackageError("source evidence metadata is unavailable")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        fd = os.open(storage_path, flags)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size != expected_size:
            raise EvidencePackageError("source size mismatch")
        content = bytearray()
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, min(1024 * 1024, expected_size - len(content) + 1))
            if not chunk:
                break
            content.extend(chunk)
            digest.update(chunk)
            if len(content) > expected_size:
                raise EvidencePackageError("source size mismatch")
        if len(content) != expected_size:
            raise EvidencePackageError("source size mismatch")
        if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
            raise EvidencePackageError("source checksum mismatch")
    except EvidencePackageError:
        raise
    except OSError as exc:
        raise EvidencePackageError("source bytes are missing or unsafe") from exc
    finally:
        if fd >= 0:
            os.close(fd)

    filename = getattr(scan, "file_name", None)
    if not isinstance(filename, str) or not filename:
        filename = "source.bin"
    return EvidenceFile(
        filename=filename,
        media_type=mimetypes.guess_type(filename)[0],
        content=bytes(content),
    )


# -- Response Models --


class DeferralSummary(BaseModel):
    lifecycle: Literal["active", "expired", "revoked", "resolved"]
    owner: str
    reason: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None


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
    source_kind: Optional[str] = None
    source_locator: Optional[PageRasterRegionLocator] = None
    visual_semantic_contract: Optional[VisualSemanticContract] = None
    visual_semantic_disposition: Literal[
        "complete", "legacy_incomplete", "invalid", "not_applicable"
    ] = "not_applicable"
    review_digest: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    approved_review_digest: Optional[str] = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    deferral: Optional[DeferralSummary] = None


class QueueItem(BaseModel):
    scan_id: str
    file_name: str
    department_id: Optional[str] = None
    scan_type: Optional[str] = None
    total_fixes: int
    needs_review_count: int
    lowest_confidence: float
    status: Literal["pending", "approved", "rejected"]
    created_at: datetime


class QueueResponse(BaseModel):
    items: list[QueueItem]
    total: int
    has_more: bool


class QueueStats(BaseModel):
    pending: int
    approved: int
    rejected: int
    total: int
    by_type: Optional[dict[str, int]] = None


class DocumentReview(BaseModel):
    scan_id: str
    file_name: str
    status: Literal["pending", "approved", "rejected"]
    fixes: list[FixSummary]
    total_fixes: int
    needs_review_count: int
    auto_approved_count: int
    reviewed_count: int
    matterhorn_total: int
    matterhorn_passed: int
    matterhorn_failed: int
    validator_result: str


class FixAction(BaseModel):
    action: Literal["approve", "reject", "edit"]
    notes: Optional[str] = None
    edited_content: Optional[str] = None


class DeferralAction(BaseModel):
    owner: str = Field(max_length=255)
    reason: str = Field(max_length=4000)
    expires_at: datetime

    @field_validator("owner", "reason")
    @classmethod
    def validate_non_blank(cls, value: str, info) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must not be blank")
        return normalized

    @field_validator("expires_at")
    @classmethod
    def validate_future_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        if value <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return value.astimezone(timezone.utc)


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


class DeferralResponse(BaseModel):
    status: str
    fix_id: str
    deferral: DeferralSummary


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


def deferral_lifecycle(
    fix: object, *, now: Optional[datetime] = None
) -> Optional[Literal["active", "expired", "revoked", "resolved"]]:
    """Return the reportable lifecycle without mutating persisted state."""
    stored_status = getattr(fix, "deferral_status", None)
    if stored_status is None:
        return None
    if stored_status in {"revoked", "resolved"}:
        return stored_status
    if stored_status != "active":
        return None
    expires_at = getattr(fix, "deferral_expires_at", None)
    if expires_at is None:
        return None
    comparison_time = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return "expired" if expires_at <= comparison_time else "active"


def _deferral_snapshot(
    fix: object, *, now: Optional[datetime] = None
) -> Optional[dict[str, object]]:
    lifecycle = deferral_lifecycle(fix, now=now)
    if lifecycle is None:
        return None

    def iso(field: str) -> Optional[str]:
        value = getattr(fix, field, None)
        return value.isoformat() if isinstance(value, datetime) else None

    return {
        "lifecycle": lifecycle,
        "owner": getattr(fix, "deferral_owner", None),
        "reason": getattr(fix, "deferral_reason", None),
        "expires_at": iso("deferral_expires_at"),
        "created_at": iso("deferral_created_at"),
        "updated_at": iso("deferral_updated_at"),
        "closed_at": iso("deferral_closed_at"),
    }


def _deferral_summary(fix: object) -> Optional[DeferralSummary]:
    snapshot = _deferral_snapshot(fix)
    return DeferralSummary.model_validate(snapshot) if snapshot else None


def _resolve_fix_deferral(
    db: Session,
    *,
    fix: ScanFix,
    user_id: str,
    resolved_at: datetime,
) -> bool:
    """Resolve an open or expired deferral and append its history event."""
    if getattr(fix, "deferral_status", None) != "active":
        return False
    previous = _deferral_snapshot(fix, now=resolved_at)
    fix.deferral_status = "resolved"
    fix.deferral_updated_at = resolved_at
    fix.deferral_closed_at = resolved_at
    db.add(
        ReviewAuditLog(
            id=str(uuid.uuid4()),
            scan_id=fix.scan_id,
            fix_id=fix.id,
            user_id=user_id,
            action="fix_deferral_resolved",
            details={
                "actor_id": user_id,
                "previous": previous,
                "current": _deferral_snapshot(fix, now=resolved_at),
            },
        )
    )
    return True


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


def compute_validator_result(total: int, passed: int, failed: int) -> str:
    """Summarize recorded validator checkpoints without asserting conformance."""
    if total == 0:
        return "not_run"
    if failed > 0:
        return "recorded_checkpoint_failures"
    if passed == total:
        return "all_recorded_checkpoints_passed"
    return "recorded_checkpoint_results_available"


def summarize_review_statuses(statuses: Iterable[Optional[str]]) -> dict[str, int]:
    """Summarize persisted fix states for the review UI.

    Unknown and legacy non-terminal states remain pending instead of being
    silently presented as reviewed. ``edited`` is accepted for legacy rows,
    although new edits are persisted as ``approved`` and recorded in audit logs.
    """
    status_list = list(statuses)
    return {
        "total_fixes": len(status_list),
        "needs_review_count": sum(
            status not in _TERMINAL_STATUSES for status in status_list
        ),
        "auto_approved_count": sum(
            status == _AUTO_APPROVED_STATUS for status in status_list
        ),
        "reviewed_count": sum(
            status in _HUMAN_REVIEWED_STATUSES for status in status_list
        ),
    }


def compute_doc_status(
    statuses: Iterable[Optional[str]],
) -> Literal["pending", "approved", "rejected"]:
    """Determine document status from the persisted states of all its fixes."""
    status_list = list(statuses)
    if any(status not in _TERMINAL_STATUSES for status in status_list):
        return "pending"
    if "rejected" in status_list:
        return "rejected"
    return "approved"


def _fix_summary(fix: ScanFix) -> FixSummary:
    """Build a response from validated visual evidence only."""
    disposition = visual_semantic_disposition(fix)
    contract = (
        validated_visual_semantic_contract(fix.visual_semantic_contract)
        if disposition == "complete"
        else None
    )
    source_locator = None
    if fix.source_locator is not None:
        try:
            source_locator = PageRasterRegionLocator.model_validate(fix.source_locator)
        except (TypeError, ValueError):
            source_locator = None
    return FixSummary(
        id=fix.id,
        category=fix.category,
        severity=fix.severity,
        description=fix.description,
        confidence=fix.confidence,
        fix_method=fix.fix_method,
        needs_review=fix.needs_review,
        review_status=fix.review_status,
        page_number=fix.page_number,
        original_content=fix.original_content,
        fixed_content=fix.fixed_content,
        wcag_criteria=fix.wcag_criteria,
        location=fix.location,
        review_notes=fix.review_notes,
        source_kind=fix.source_kind,
        source_locator=source_locator,
        visual_semantic_contract=contract,
        visual_semantic_disposition=disposition,
        review_digest=fix.review_digest if valid_sha256(fix.review_digest) else None,
        approved_review_digest=(
            fix.approved_review_digest
            if valid_sha256(fix.approved_review_digest)
            else None
        ),
        deferral=_deferral_summary(fix),
    )


# -- Endpoints --


@router.get("/queue", response_model=QueueResponse)
def get_review_queue(
    department_id: Optional[str] = Query(None),
    status: Optional[Literal["pending", "approved", "rejected"]] = Query(None),
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
                case((ScanFix.review_status.in_(_TERMINAL_STATUSES), 0), else_=1)
            ).label("needs_review_count"),
            func.sum(case((ScanFix.review_status == "rejected", 1), else_=0)).label(
                "rejected_count"
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
            func.sum(case((ScanFix.review_status.in_(_TERMINAL_STATUSES), 0), else_=1))
            > 0
        )
    elif status == "approved":
        query = query.having(
            func.sum(case((ScanFix.review_status.in_(_TERMINAL_STATUSES), 0), else_=1))
            == 0
        ).having(func.sum(case((ScanFix.review_status == "rejected", 1), else_=0)) == 0)
    elif status == "rejected":
        query = query.having(
            func.sum(case((ScanFix.review_status.in_(_TERMINAL_STATUSES), 0), else_=1))
            == 0
        ).having(func.sum(case((ScanFix.review_status == "rejected", 1), else_=0)) > 0)

    total = query.count()
    rows = (
        query.order_by(func.min(ScanFix.confidence).asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    results = []
    for row in rows:
        pending_count = row.needs_review_count or 0
        doc_status = (
            "pending"
            if pending_count > 0
            else "rejected" if (row.rejected_count or 0) > 0 else "approved"
        )
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

    return QueueResponse(
        items=results,
        total=total,
        has_more=offset + len(results) < total,
    )


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
    total = sum(counts.values())
    approved = sum(counts.get(status, 0) for status in _ACCEPTED_STATUSES)
    rejected = counts.get("rejected", 0)
    pending = total - approved - rejected

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
        pending=pending,
        approved=approved,
        rejected=rejected,
        total=total,
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

    approved_count = sum(status_counts.get(status, 0) for status in _ACCEPTED_STATUSES)
    rejected_count = status_counts.get("rejected", 0)
    total_fixes = sum(status_counts.values())
    pending_count = total_fixes - approved_count - rejected_count

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

    validator_result = compute_validator_result(total, passed, failed)
    statuses = [fix.review_status for fix in fixes]
    summary = summarize_review_statuses(statuses)

    return DocumentReview(
        scan_id=scan_id,
        file_name=scan.file_name,
        status=compute_doc_status(statuses),
        fixes=[_fix_summary(fix) for fix in fixes],
        **summary,
        matterhorn_total=total,
        matterhorn_passed=passed,
        matterhorn_failed=failed,
        validator_result=validator_result,
    )


@router.put("/{scan_id}/fixes/{fix_id}/deferral", response_model=DeferralResponse)
def defer_fix(
    scan_id: str,
    fix_id: str,
    body: DeferralAction,
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Create or change a controlled, time-bounded deferral."""
    _, user_id, department_id = auth_result
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan or scan.department_id != department_id:
        raise HTTPException(status_code=404, detail="Scan not found")

    graph = lock_scan_review_graph(db, scan_id)
    fix = next((row for row in graph.fixes if row.id == fix_id), None)
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")
    if fix.review_status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Only unresolved findings can be deferred",
        )

    now = datetime.now(timezone.utc)
    previous = _deferral_snapshot(fix, now=now)
    changing = getattr(fix, "deferral_status", None) == "active"
    fix.deferral_status = "active"
    fix.deferral_owner = body.owner
    fix.deferral_reason = body.reason
    fix.deferral_expires_at = body.expires_at
    if not changing:
        fix.deferral_created_at = now
    fix.deferral_updated_at = now
    fix.deferral_closed_at = None

    action = "fix_deferral_updated" if changing else "fix_deferral_created"
    db.add(
        ReviewAuditLog(
            id=str(uuid.uuid4()),
            scan_id=scan_id,
            fix_id=fix_id,
            user_id=user_id,
            action=action,
            details={
                "actor_id": user_id,
                "previous": previous,
                "current": _deferral_snapshot(fix, now=now),
            },
        )
    )
    db.commit()
    summary = _deferral_summary(fix)
    if summary is None:  # Defensive: persistence fields were assigned above.
        raise HTTPException(status_code=500, detail="Deferral state is invalid")
    return DeferralResponse(status="ok", fix_id=fix_id, deferral=summary)


@router.post(
    "/{scan_id}/fixes/{fix_id}/deferral/revoke",
    response_model=DeferralResponse,
)
def revoke_fix_deferral(
    scan_id: str,
    fix_id: str,
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Revoke an active or expired controlled deferral."""
    _, user_id, department_id = auth_result
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan or scan.department_id != department_id:
        raise HTTPException(status_code=404, detail="Scan not found")

    graph = lock_scan_review_graph(db, scan_id)
    fix = next((row for row in graph.fixes if row.id == fix_id), None)
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")
    if getattr(fix, "deferral_status", None) != "active":
        raise HTTPException(
            status_code=409,
            detail="Only an active or expired deferral can be revoked",
        )

    now = datetime.now(timezone.utc)
    previous = _deferral_snapshot(fix, now=now)
    fix.deferral_status = "revoked"
    fix.deferral_updated_at = now
    fix.deferral_closed_at = now
    db.add(
        ReviewAuditLog(
            id=str(uuid.uuid4()),
            scan_id=scan_id,
            fix_id=fix_id,
            user_id=user_id,
            action="fix_deferral_revoked",
            details={
                "actor_id": user_id,
                "previous": previous,
                "current": _deferral_snapshot(fix, now=now),
            },
        )
    )
    db.commit()
    summary = _deferral_summary(fix)
    if summary is None:
        raise HTTPException(status_code=500, detail="Deferral state is invalid")
    return DeferralResponse(status="ok", fix_id=fix_id, deferral=summary)


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

    try:
        bind_fix_review_decision(fix, body.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    fix.review_status = "rejected" if body.action == "reject" else "approved"
    fix.reviewed_by = user_id
    fix.reviewed_at = now
    fix.review_notes = body.notes
    _resolve_fix_deferral(
        db,
        fix=fix,
        user_id=user_id,
        resolved_at=now,
    )

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

    try:
        apply_authenticated_batch_review(
            db,
            scan_id=scan_id,
            fixes=fixes,
            action=body.action,
            user_id=user_id,
            reviewed_at=now,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    for fix in fixes:
        _resolve_fix_deferral(
            db,
            fix=fix,
            user_id=user_id,
            resolved_at=now,
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


def _audit_export_inputs(db: Session, scan: Scan) -> tuple[list, list, list, object]:
    """Load the bounded evidence graph shared by exports and packages."""
    fixes = (
        db.query(ScanFix)
        .filter(ScanFix.scan_id == scan.id)
        .order_by(ScanFix.confidence.asc())
        .all()
    )

    matterhorn_results = (
        db.query(MatterhornResult).filter(MatterhornResult.scan_id == scan.id).all()
    )

    raw_entries = (
        db.query(ReviewAuditLog)
        .filter(ReviewAuditLog.scan_id == scan.id)
        .order_by(ReviewAuditLog.created_at.asc())
        .all()
    )

    user_ids = {e.user_id for e in raw_entries if e.user_id}
    user_ids.update(fix.reviewed_by for fix in fixes if fix.reviewed_by)
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

    for fix in fixes:
        fix._export_reviewer_name = (
            user_map.get(fix.reviewed_by) if fix.reviewed_by else None
        )

    dept = db.query(Department).filter(Department.id == scan.department_id).first()
    if not dept:
        dept = type(
            "DeptFallback",
            (),
            {
                "name": "Unknown Department",
                "institution": "Unknown Institution",
            },
        )()
    return fixes, audit_entries, matterhorn_results, dept


@router.get("/{scan_id}/audit/export")
def export_audit_trail(
    scan_id: str,
    format: Literal["json", "csv", "pdf"] = Query("json"),
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Export bounded scan, validator, remediation, and review evidence.

    The export records issues, fixes, review history, and Matterhorn results;
    it does not make an accessibility-standard or legal determination.
    """
    _, _user_id, department_id = auth_result

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan or scan.department_id != department_id:
        raise HTTPException(status_code=404, detail="Scan not found")

    fixes, audit_entries, matterhorn_results, dept = _audit_export_inputs(db, scan)

    if format == "json":
        data = AuditReportGenerator.generate_json(
            scan=scan,
            fixes=fixes,
            audit_entries=audit_entries,
            matterhorn_results=matterhorn_results,
            department=dept,
        )
        return JSONResponse(
            content=data,
            headers=_audit_export_headers(scan_id, format),
        )

    elif format == "csv":
        csv_content = AuditReportGenerator.generate_csv(
            scan=scan,
            fixes=fixes,
            audit_entries=audit_entries,
            matterhorn_results=matterhorn_results,
            department=dept,
        )
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers=_audit_export_headers(scan_id, format),
        )

    else:  # pdf
        pdf_bytes = AuditReportGenerator.generate_pdf(
            scan=scan,
            fixes=fixes,
            audit_entries=audit_entries,
            matterhorn_results=matterhorn_results,
            department=dept,
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers=_audit_export_headers(scan_id, format),
        )


@router.get("/{scan_id}/audit/package")
def export_evidence_package(
    scan_id: str,
    include_source: bool = Query(False),
    include_output: bool = Query(False),
    db: Session = Depends(get_db_dependency),
    auth_result=Depends(get_auth),
):
    """Download a versioned evidence package without document bytes by default."""
    _, _user_id, department_id = auth_result
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan or scan.department_id != department_id:
        raise HTTPException(status_code=404, detail="Scan not found")

    source_file = None
    if include_source:
        try:
            source_file = _read_verified_source(scan)
        except EvidencePackageError:
            raise HTTPException(
                status_code=409, detail="Source evidence unavailable"
            ) from None

    artifact = None
    output_file = None
    artifact_id = getattr(scan, "current_remediation_artifact_id", None)
    if artifact_id is not None:
        artifact = (
            db.query(RemediationArtifact)
            .filter(
                RemediationArtifact.id == artifact_id,
                RemediationArtifact.scan_id == scan.id,
                RemediationArtifact.department_id == department_id,
            )
            .one_or_none()
        )
        if artifact is None:
            raise HTTPException(status_code=409, detail="Evidence artifact unavailable")
        service = RemediationArtifactService.from_settings()
        try:
            if artifact.cloud_file_id is not None:
                service.lock_current(
                    db,
                    artifact_id=artifact.id,
                    department_id=department_id,
                    cloud_file_id=artifact.cloud_file_id,
                    provider=artifact.provider,
                )
            if include_output:
                with service.open_verified(
                    db,
                    artifact,
                    department_id=department_id,
                    scan_id=scan.id,
                    cloud_file_id=artifact.cloud_file_id,
                ) as stream:
                    output_file = EvidenceFile(
                        filename=artifact.filename,
                        media_type=artifact.mime_type,
                        content=stream.read(),
                    )
            else:
                service.resolve_record(
                    db,
                    artifact,
                    department_id=department_id,
                    scan_id=scan.id,
                    cloud_file_id=artifact.cloud_file_id,
                )
        except ArtifactError:
            raise HTTPException(
                status_code=409, detail="Evidence artifact unavailable"
            ) from None

    fixes, audit_entries, matterhorn_results, dept = _audit_export_inputs(db, scan)
    evidence = AuditReportGenerator.generate_json(
        scan=scan,
        fixes=fixes,
        audit_entries=audit_entries,
        matterhorn_results=matterhorn_results,
        department=dept,
    )
    evidence["artifact"] = artifact_evidence(artifact)
    try:
        package = build_evidence_package(
            evidence,
            source_file=source_file,
            output_file=output_file,
        )
    except EvidencePackageError:
        raise HTTPException(
            status_code=409, detail="Evidence package unavailable"
        ) from None
    return Response(
        content=package,
        media_type="application/zip",
        headers=_evidence_package_headers(scan_id),
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
                details=bounded_audit_details(e.details),
                created_at=e.created_at,
            )
        )

    return results
