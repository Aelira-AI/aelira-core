"""
Canvas Content Scan / Review / Approve / Write-back Routes

Endpoints for scanning Canvas HTML content (pages, assignments,
announcements, quizzes, discussions) and managing the remediation
review + write-back workflow.

Prefix: /canvas/content

Endpoints:
 1. POST /canvas/content/scan                      — scan all 5 content types
 2. POST /canvas/content/scan/{content_type}        — scan one content type
 3. GET  /canvas/content/courses/{course_id}/status — DB-only compliance summary
3b. GET  /canvas/content/overview                   — institution-wide compliance overview
 4. GET  /canvas/content/{cloud_file_id}/diff       — original vs remediated diff
 5. POST /canvas/content/{cloud_file_id}/approve    — approve a remediation
 6. POST /canvas/content/{cloud_file_id}/reject     — reject a remediation
 7. POST /canvas/content/batch-approve              — approve multiple items
 8. POST /canvas/content/{cloud_file_id}/writeback  — execute write-back to Canvas
 9. POST /canvas/content/batch-writeback            — write back all approved items
10. POST /canvas/content/{cloud_file_id}/rollback   — rollback a written-back item
11. GET  /canvas/content/{cloud_file_id}/audit      — writeback audit log

SECURITY:
- All endpoints require API key authentication
- All endpoints require lms_integration feature gate
- Users can only access their own department's data
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import verify_department_access
from ..auth.dependencies import get_required_api_key
from ..db.database import get_db_dependency
from ..db.models import (
    APIKey,
    CloudFile,
    CloudProvider,
    ContentWritebackLog,
    ScanResult,
)
from ..education.canvas_content_scanner import CanvasContentScanner
from ..integrations.canvas.content_models import CanvasContentType
from ..middleware.quota import require_feature
from .canvas_routes import _get_canvas_client
from .canvas_scan_routes import _canvas_scan_file_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/canvas/content", tags=["canvas-content"])


# =============================================================================
# Request / Response Models
# =============================================================================


class CanvasContentScanRequest(BaseModel):
    """Request to scan Canvas course content."""

    course_id: str = Field(..., description="Canvas course ID")
    department_id: Optional[str] = None
    # Scan options
    generate_alt_text: bool = Field(
        default=True, description="Generate AI alt text for images"
    )
    auto_remediate: bool = Field(
        default=True, description="Automatically fix issues after scan"
    )
    detect_decorative: bool = Field(
        default=True, description="Detect decorative images"
    )

    def to_scan_options(self) -> Dict[str, Any]:
        """Convert scan options to dict for CanvasContentScanner."""
        return {
            "generate_alt_text": self.generate_alt_text,
            "auto_remediate": self.auto_remediate,
            "detect_decorative": self.detect_decorative,
        }


class CanvasContentScanResponse(BaseModel):
    """Summary after queuing content scans."""

    total_items: int
    jobs_queued: int
    skipped: int
    by_type: Dict[str, int]


class ContentItemStatus(BaseModel):
    """Status of a single content item."""

    cloud_file_id: str
    provider_file_id: Optional[str] = None
    content_type: Optional[str] = None
    title: str
    compliance_score: Optional[float] = None
    issue_count: int = 0
    writeback_status: Optional[str] = None
    has_remediated_version: bool = False
    # The scan whose results are current for this item — the client needs
    # this to call POST /education/remediate/{scan_id} for a per-item
    # remediate action (the same endpoint the LTI Files tab already uses).
    scan_id: Optional[str] = None
    last_scanned_at: Optional[str] = None


class ContentTypeStatus(BaseModel):
    """Compliance summary for a single content type."""

    content_type: str
    total: int
    scanned: int
    average_compliance: Optional[float] = None
    issues: int = 0


class CourseContentStatusResponse(BaseModel):
    """Course-level content compliance summary."""

    course_id: str
    overall_compliance: Optional[float] = None
    by_type: List[ContentTypeStatus]
    items: List[ContentItemStatus]


class CourseOverviewItem(BaseModel):
    """Compliance summary for a single course."""

    course_id: str
    course_name: str
    course_code: Optional[str] = None
    total_items: int = 0
    scanned_items: int = 0
    avg_compliance: Optional[float] = None
    total_issues: int = 0
    written_back: int = 0
    status: str  # "not_started", "critical", "at_risk", "on_track", "compliant"


class CourseOverviewResponse(BaseModel):
    """Institution-wide compliance overview across all courses."""

    total_courses: int
    total_items: int
    total_scanned: int
    avg_compliance: Optional[float] = None
    total_issues: int
    courses: List[CourseOverviewItem]


class ContentIssueDetail(BaseModel):
    """A single real accessibility finding from the last scan's stored
    axe-core violation — every field here is read straight off
    ScanResult.issues, never generated. No per-issue fixed/remaining
    status: that attribution isn't tracked anywhere for Canvas content
    (see get_content_diff's comment) — issues_fixed/issues_remaining on
    ContentDiffResponse are aggregate-only."""

    id: str
    impact: Optional[str] = None
    description: Optional[str] = None
    help: Optional[str] = None
    wcag_tags: List[str] = []
    nodes_affected: int = 0


class ContentDiffResponse(BaseModel):
    """Original vs remediated HTML for review."""

    cloud_file_id: str
    content_type: Optional[str] = None
    title: str
    original_html: Optional[str] = None
    remediated_html: Optional[str] = None
    issues_fixed: int = 0
    issues_remaining: int = 0
    # Real findings from the last scan (axe-core violations). NOT split
    # into fixed/remaining — that attribution doesn't exist in the data;
    # this is the full pre-remediation issue set. Empty for older scans
    # that predate this field, or if the scan stored no issues.
    issues: List[ContentIssueDetail] = []


class BatchApproveRequest(BaseModel):
    """Request to batch-approve multiple content items."""

    cloud_file_ids: List[str] = Field(
        ..., description="List of cloud file IDs to approve"
    )


class BatchApproveResponse(BaseModel):
    """Response from batch approve."""

    approved_count: int
    skipped_count: int = 0
    errors: List[str] = []


class BatchWritebackRequest(BaseModel):
    """Request to batch write-back all approved items for a course."""

    course_id: str = Field(..., description="Canvas course ID")


class BatchWritebackResponse(BaseModel):
    """Response from batch write-back."""

    written_count: int
    failed_count: int = 0
    stale_count: int = 0
    # Approved file-type rows (has_remediated_version, no remediated_body —
    # there's no HTML to write back) that couldn't even be attempted: file
    # write-back to Canvas isn't wired yet. Counted honestly here rather
    # than silently dropped from the response.
    skipped_count: int = 0
    errors: List[str] = []


class WritebackResponse(BaseModel):
    """Response from a single write-back."""

    success: bool
    stale: bool = False
    error: Optional[str] = None


class RollbackResponse(BaseModel):
    """Response from a rollback."""

    success: bool
    error: Optional[str] = None


class ApproveRejectResponse(BaseModel):
    """Response from approve or reject."""

    cloud_file_id: str
    writeback_status: str


class AuditEntry(BaseModel):
    """A single write-back audit log entry."""

    id: str
    original_body: Optional[str] = None
    remediated_body: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    written_back_at: Optional[str] = None
    rollback_status: Optional[str] = None
    rolled_back_at: Optional[str] = None
    created_at: Optional[str] = None


class AuditResponse(BaseModel):
    """Response from the audit log endpoint."""

    cloud_file_id: str
    entries: List[AuditEntry]


# =============================================================================
# Helpers
# =============================================================================


async def _fetch_course_meta(api_client, course_id: str) -> tuple:
    """Fetch course name/code for CloudFile metadata. Returns ("", "") on failure."""
    try:
        info = await api_client.get_course(course_id)
        return info.name or "", info.course_code or ""
    except Exception as e:
        logger.warning(
            "Failed to fetch course info for metadata: %s",
            e,
            extra={"course_id": course_id},
        )
        return "", ""


def _get_cloud_file_or_404(
    db: Session, cloud_file_id: str, department_id: str
) -> CloudFile:
    """Fetch a CloudFile by ID, verifying department ownership."""
    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.id == cloud_file_id,
            CloudFile.department_id == department_id,
        )
        .first()
    )
    if not cloud_file:
        raise HTTPException(status_code=404, detail="Content item not found")
    return cloud_file


def _format_scan_issue(raw: Dict[str, Any]) -> ContentIssueDetail:
    """Convert one raw axe-core violation dict (ScanResult.issues element)
    into a ContentIssueDetail. Every field is read directly off the raw
    violation — nothing here is generated or guessed. `id` falls back to
    an empty string rather than being fabricated if axe-core's own shape
    is ever missing it (defensive only, not expected in practice)."""
    return ContentIssueDetail(
        id=raw.get("id", ""),
        impact=raw.get("impact"),
        description=raw.get("description"),
        help=raw.get("help"),
        wcag_tags=[t for t in raw.get("tags", []) if isinstance(t, str)],
        nodes_affected=len(raw.get("nodes", []) or []),
    )


# =============================================================================
# 1. POST /canvas/content/scan — scan all content types
# =============================================================================


@router.post("/scan", response_model=CanvasContentScanResponse)
async def scan_course_content(
    request: CanvasContentScanRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> CanvasContentScanResponse:
    """
    Scan all 5 content types in a Canvas course.

    Fetches pages, assignments, announcements, quizzes, and discussions,
    upserts CloudFile records, and queues scan jobs for each.
    """
    _, user_id, auth_department_id = api_key_info
    dept_id = request.department_id or auth_department_id
    verify_department_access(dept_id, auth_department_id)

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    try:
        credential, api_client = await _get_canvas_client(dept_id, db)
        try:
            course_name, course_code = await _fetch_course_meta(
                api_client, request.course_id
            )

            scan_options = request.to_scan_options()

            scanner = CanvasContentScanner(
                canvas_client=api_client,
                db=db,
                department_id=dept_id,
                credential_id=credential.id,
                course_name=course_name,
                course_code=course_code,
                scan_options=scan_options,
            )
            result = await scanner.scan_course_content(request.course_id)

            counts = result.get("counts", {})
            cloud_file_ids = result.get("cloud_file_ids", [])
            # The scanner already created a CloudJobQueue row for each file
            # (it needs the file-download pipeline, not the axe-core
            # background task below) — but a CloudJobQueue row is a record
            # only. Nothing in this app polls the queue (JobProcessor is
            # never started), so the row sits PENDING forever unless a
            # background task is actually fired for it here, exactly like
            # canvas_scan_routes.py's single-file scan endpoint does.
            file_scan_jobs = result.get("file_scan_jobs", [])
            skipped = counts.get("skipped_empty", 0)

            # Queue background scan jobs for each discovered HTML content item
            for cf_id in cloud_file_ids:
                background_tasks.add_task(
                    _content_scan_task,
                    cf_id,
                    dept_id,
                    credential.id,
                    scan_options=scan_options,
                )

            # Fire the actual background task for each file's CloudJobQueue
            # row — mirrors canvas_scan_routes.py's single-file scan
            # endpoint's call signature exactly.
            for job in file_scan_jobs:
                background_tasks.add_task(
                    _canvas_scan_file_task,
                    job_id=job["job_id"],
                    cloud_file_id=job["cloud_file_id"],
                    credential_id=credential.id,
                )

            by_type = {k: v for k, v in counts.items() if k != "skipped_empty"}
            total_items = len(cloud_file_ids) + len(file_scan_jobs)

            return CanvasContentScanResponse(
                total_items=total_items,
                jobs_queued=total_items,
                skipped=skipped,
                by_type=by_type,
            )
        finally:
            await api_client.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to scan course content: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to scan course content: {str(e)}",
        )


# =============================================================================
# 2. POST /canvas/content/scan/{content_type} — scan one type
# =============================================================================


class ContentTypeParam(str, Enum):
    """Valid content types for single-type scanning."""

    page = "page"
    assignment = "assignment"
    announcement = "announcement"
    quiz = "quiz"
    discussion = "discussion"


@router.post("/scan/{content_type}", response_model=CanvasContentScanResponse)
async def scan_course_content_by_type(
    content_type: ContentTypeParam,
    request: CanvasContentScanRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> CanvasContentScanResponse:
    """
    Scan a single content type in a Canvas course.

    Fetches only the specified content type, upserts CloudFile records,
    and queues scan jobs.
    """
    _, user_id, auth_department_id = api_key_info
    dept_id = request.department_id or auth_department_id
    verify_department_access(dept_id, auth_department_id)

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    try:
        credential, api_client = await _get_canvas_client(dept_id, db)
        try:
            course_name, course_code = await _fetch_course_meta(
                api_client, request.course_id
            )

            scan_options = request.to_scan_options()

            scanner = CanvasContentScanner(
                canvas_client=api_client,
                db=db,
                department_id=dept_id,
                credential_id=credential.id,
                course_name=course_name,
                course_code=course_code,
                scan_options=scan_options,
            )
            result = await scanner.scan_course_content(
                request.course_id,
                content_types=[CanvasContentType(content_type.value)],
            )

            counts = result.get("counts", {})
            cloud_file_ids = result.get("cloud_file_ids", [])
            skipped = counts.get("skipped_empty", 0)

            for cf_id in cloud_file_ids:
                background_tasks.add_task(
                    _content_scan_task,
                    cf_id,
                    dept_id,
                    credential.id,
                    scan_options=scan_options,
                )

            by_type = {k: v for k, v in counts.items() if k != "skipped_empty"}

            return CanvasContentScanResponse(
                total_items=len(cloud_file_ids),
                jobs_queued=len(cloud_file_ids),
                skipped=skipped,
                by_type=by_type,
            )
        finally:
            await api_client.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to scan course content type {content_type}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to scan content: {str(e)}",
        )


# =============================================================================
# 3. GET /canvas/content/courses/{course_id}/status — DB-only compliance
# =============================================================================


@router.get(
    "/courses/{course_id}/status",
    response_model=CourseContentStatusResponse,
)
async def get_course_content_status(
    course_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> CourseContentStatusResponse:
    """
    Get course content compliance summary from the database.

    No Canvas API calls — safe for frequent polling.
    """
    _, user_id, auth_department_id = api_key_info

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    cloud_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.provider == CloudProvider.CANVAS.value,
            CloudFile.provider_parent_id == course_id,
            CloudFile.department_id == auth_department_id,
            CloudFile.content_source.isnot(None),
        )
        .all()
    )

    # Build per-type stats
    type_map: Dict[str, List[CloudFile]] = {}
    for cf in cloud_files:
        ct = cf.content_source or "unknown"
        type_map.setdefault(ct, []).append(cf)

    # Count issues per cloud file from scan results
    issue_counts: Dict[str, int] = {}
    scan_ids = [cf.last_scan_id for cf in cloud_files if cf.last_scan_id]
    if scan_ids:
        from ..db.models import ScanResult

        scan_results = (
            db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
        )
        for sr in scan_results:
            total = (
                (sr.critical_issues or 0)
                + (sr.high_issues or 0)
                + (sr.medium_issues or 0)
                + (sr.low_issues or 0)
            )
            issue_counts[sr.scan_id] = total

    by_type: List[ContentTypeStatus] = []
    all_scores: List[float] = []

    for ct, files in type_map.items():
        scanned = [f for f in files if f.last_scan_id is not None]
        scores = [
            f.last_compliance_score
            for f in scanned
            if f.last_compliance_score is not None
        ]
        type_issues = sum(issue_counts.get(f.last_scan_id, 0) for f in scanned)
        avg = round(sum(scores) / len(scores), 1) if scores else None
        all_scores.extend(scores)
        by_type.append(
            ContentTypeStatus(
                content_type=ct,
                total=len(files),
                scanned=len(scanned),
                average_compliance=avg,
                issues=type_issues,
            )
        )

    overall = round(sum(all_scores) / len(all_scores), 1) if all_scores else None

    items = [
        ContentItemStatus(
            cloud_file_id=cf.id,
            provider_file_id=cf.provider_file_id,
            content_type=cf.content_source,
            title=cf.file_name,
            compliance_score=cf.last_compliance_score,
            issue_count=issue_counts.get(cf.last_scan_id, 0) if cf.last_scan_id else 0,
            writeback_status=cf.writeback_status,
            has_remediated_version=cf.has_remediated_version or False,
            last_scanned_at=(
                cf.last_scanned_at.isoformat() if cf.last_scanned_at else None
            ),
            scan_id=cf.last_scan_id,
        )
        for cf in cloud_files
    ]

    return CourseContentStatusResponse(
        course_id=course_id,
        overall_compliance=overall,
        by_type=by_type,
        items=items,
    )


# =============================================================================
# 3b. GET /canvas/content/overview — institution-wide compliance overview
# =============================================================================


@router.get("/overview", response_model=CourseOverviewResponse)
async def get_course_overview(
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> CourseOverviewResponse:
    """
    Get compliance overview across all courses for this department.

    Aggregates CloudFile records grouped by course (provider_parent_id).
    Falls back to Canvas API for course names when provider_metadata is missing,
    and backfills the metadata so subsequent requests are DB-only.
    """
    _, user_id, dept_id = api_key_info

    await require_feature(db, dept_id, "lms_integration", "Canvas LMS Integration")

    # Query all Canvas content CloudFiles for this department
    cloud_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.department_id == dept_id,
            CloudFile.provider == CloudProvider.CANVAS.value,
            CloudFile.content_source.isnot(
                None
            ),  # Only content items, not uploaded files
        )
        .all()
    )

    # Compute issue counts from scan results (same pattern as get_course_content_status)
    issue_counts: Dict[str, int] = {}
    scan_ids = [cf.last_scan_id for cf in cloud_files if cf.last_scan_id]
    if scan_ids:
        scan_results = (
            db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
        )
        for sr in scan_results:
            total = (
                (sr.critical_issues or 0)
                + (sr.high_issues or 0)
                + (sr.medium_issues or 0)
                + (sr.low_issues or 0)
            )
            issue_counts[sr.scan_id] = total

    # Group by course (provider_parent_id = Canvas course ID)
    courses_map: Dict[str, List[CloudFile]] = {}
    for cf in cloud_files:
        cid = cf.provider_parent_id or "unknown"
        courses_map.setdefault(cid, []).append(cf)

    # Identify courses missing names in provider_metadata and fetch from Canvas
    course_names: Dict[str, Dict[str, str]] = {}  # course_id -> {name, code}
    missing_ids = []
    for course_id, files in courses_map.items():
        found = False
        for f in files:
            if f.provider_metadata and isinstance(f.provider_metadata, dict):
                name = f.provider_metadata.get("course_name", "")
                if name:
                    course_names[course_id] = {
                        "name": name,
                        "code": f.provider_metadata.get("course_code", ""),
                    }
                    found = True
                    break
        if not found and course_id != "unknown":
            missing_ids.append(course_id)

    if missing_ids:
        try:
            _, api_client = await _get_canvas_client(dept_id, db)
            for cid in missing_ids:
                try:
                    course_info = await api_client.get_course(cid)
                    course_names[cid] = {
                        "name": course_info.name,
                        "code": course_info.course_code,
                    }
                    # Backfill provider_metadata on one file per course
                    for f in courses_map[cid]:
                        metadata = f.provider_metadata or {}
                        if not isinstance(metadata, dict):
                            metadata = {}
                        metadata["course_name"] = course_info.name
                        metadata["course_code"] = course_info.course_code
                        f.provider_metadata = metadata
                        break
                except Exception:
                    logger.warning(f"Could not fetch course name for {cid}")
            db.commit()
        except Exception:
            logger.warning("Could not get Canvas client for course name lookup")

    course_items = []
    total_items = 0
    total_scanned = 0
    total_issues = 0
    all_scores = []

    for course_id, files in courses_map.items():
        items = len(files)
        scanned = sum(1 for f in files if f.last_scan_id is not None)
        issues = sum(
            issue_counts.get(f.last_scan_id, 0) for f in files if f.last_scan_id
        )
        written = sum(1 for f in files if f.writeback_status == "written_back")
        scores = [
            f.last_compliance_score
            for f in files
            if f.last_compliance_score is not None
        ]
        avg = sum(scores) / len(scores) if scores else None

        # Determine status
        if scanned == 0:
            status = "not_started"
        elif avg is not None and avg >= 95:
            status = "compliant"
        elif avg is not None and avg >= 70:
            status = "on_track"
        elif avg is not None and avg >= 50:
            status = "at_risk"
        else:
            status = "critical"

        info = course_names.get(course_id, {})
        course_name = info.get("name", "") or f"Course {course_id}"
        course_code = info.get("code", "")

        course_items.append(
            CourseOverviewItem(
                course_id=course_id,
                course_name=course_name,
                course_code=course_code,
                total_items=items,
                scanned_items=scanned,
                avg_compliance=round(avg, 1) if avg is not None else None,
                total_issues=issues,
                written_back=written,
                status=status,
            )
        )

        total_items += items
        total_scanned += scanned
        total_issues += issues
        if avg is not None:
            all_scores.append(avg)

    overall_avg = round(sum(all_scores) / len(all_scores), 1) if all_scores else None

    # Sort: critical first, then at_risk, on_track, compliant, not_started
    status_order = {
        "critical": 0,
        "at_risk": 1,
        "on_track": 2,
        "compliant": 3,
        "not_started": 4,
    }
    course_items.sort(
        key=lambda c: (status_order.get(c.status, 9), -(c.avg_compliance or 0))
    )

    return CourseOverviewResponse(
        total_courses=len(course_items),
        total_items=total_items,
        total_scanned=total_scanned,
        avg_compliance=overall_avg,
        total_issues=total_issues,
        courses=course_items,
    )


# =============================================================================
# 4. GET /canvas/content/{cloud_file_id}/diff
# =============================================================================


@router.get("/{cloud_file_id}/diff", response_model=ContentDiffResponse)
async def get_content_diff(
    cloud_file_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> ContentDiffResponse:
    """
    Get the original vs remediated HTML for review.
    """
    _, user_id, auth_department_id = api_key_info

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    cf = _get_cloud_file_or_404(db, cloud_file_id, auth_department_id)

    # Get issue counts + the real issue list from the latest scan.
    # issues_fixed/issues_remaining stay aggregate-only (an existing
    # optimistic heuristic — a remediated item counts as "all fixed"; no
    # per-issue fixed/remaining attribution is tracked anywhere for
    # Canvas content). `issues` is the real, unmodified pre-remediation
    # violation set from the scan — every field a client renders from it
    # must trace back to this list, never to a generated description.
    issues_fixed = 0
    issues_remaining = 0
    issues: List[ContentIssueDetail] = []
    if cf.last_scan_id:
        scan_result = (
            db.query(ScanResult).filter(ScanResult.scan_id == cf.last_scan_id).first()
        )
        if scan_result and scan_result.issues:
            issues_remaining = len(scan_result.issues)
            issues = [_format_scan_issue(raw) for raw in scan_result.issues]
            # If we have a remediated version, some issues may be fixed
            if cf.remediated_body:
                issues_fixed = issues_remaining  # Optimistic: all issues addressed
                issues_remaining = 0

    return ContentDiffResponse(
        cloud_file_id=cf.id,
        content_type=cf.content_source,
        title=cf.file_name,
        original_html=cf.content_body,
        remediated_html=cf.remediated_body,
        issues_fixed=issues_fixed,
        issues_remaining=issues_remaining,
        issues=issues,
    )


# =============================================================================
# 5. POST /canvas/content/{cloud_file_id}/approve
# =============================================================================


@router.post("/{cloud_file_id}/approve", response_model=ApproveRejectResponse)
async def approve_content(
    cloud_file_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> ApproveRejectResponse:
    """
    Approve a remediated content item for write-back.
    """
    _, user_id, auth_department_id = api_key_info

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    cf = _get_cloud_file_or_404(db, cloud_file_id, auth_department_id)

    # File-type rows are remediated as FILES (has_remediated_version set by
    # POST /education/remediate/{scan_id}) — remediated_body stays NULL for
    # them permanently, since they're real documents, not HTML fragments.
    # Checking remediated_body alone made every file unapprovable.
    if not cf.remediated_body and not cf.has_remediated_version:
        raise HTTPException(status_code=400, detail="No remediated content to approve")

    cf.writeback_status = "approved"
    db.commit()

    logger.info(
        "Content approved for write-back",
        extra={
            "cloud_file_id": cf.id,
            "user_id": user_id,
            "department_id": auth_department_id,
        },
    )

    return ApproveRejectResponse(
        cloud_file_id=cf.id,
        writeback_status="approved",
    )


# =============================================================================
# 6. POST /canvas/content/{cloud_file_id}/reject
# =============================================================================


@router.post("/{cloud_file_id}/reject", response_model=ApproveRejectResponse)
async def reject_content(
    cloud_file_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> ApproveRejectResponse:
    """
    Reject a remediated content item — it will not be written back.
    """
    _, user_id, auth_department_id = api_key_info

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    cf = _get_cloud_file_or_404(db, cloud_file_id, auth_department_id)

    cf.writeback_status = "rejected"
    db.commit()

    logger.info(
        "Content rejected",
        extra={
            "cloud_file_id": cf.id,
            "user_id": user_id,
            "department_id": auth_department_id,
        },
    )

    return ApproveRejectResponse(
        cloud_file_id=cf.id,
        writeback_status="rejected",
    )


# =============================================================================
# 7. POST /canvas/content/batch-approve
# =============================================================================


@router.post("/batch-approve", response_model=BatchApproveResponse)
async def batch_approve_content(
    request: BatchApproveRequest,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> BatchApproveResponse:
    """
    Approve multiple content items at once.
    """
    _, user_id, auth_department_id = api_key_info

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    cloud_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.id.in_(request.cloud_file_ids),
            CloudFile.department_id == auth_department_id,
        )
        .all()
    )

    approved = 0
    skipped = 0
    errors: List[str] = []

    for cf in cloud_files:
        # See approve_content's comment above — files carry
        # has_remediated_version instead of remediated_body.
        if not cf.remediated_body and not cf.has_remediated_version:
            skipped += 1
            errors.append(f"{cf.id}: no remediated content")
            continue
        cf.writeback_status = "approved"
        approved += 1

    db.commit()

    logger.info(
        "Batch approve complete",
        extra={
            "approved_count": approved,
            "skipped_count": skipped,
            "user_id": user_id,
            "department_id": auth_department_id,
        },
    )

    return BatchApproveResponse(
        approved_count=approved,
        skipped_count=skipped,
        errors=errors,
    )


# =============================================================================
# 8. POST /canvas/content/{cloud_file_id}/writeback
# =============================================================================


@router.post("/{cloud_file_id}/writeback", response_model=WritebackResponse)
async def writeback_content(
    cloud_file_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> WritebackResponse:
    """
    Execute write-back of remediated content to Canvas.

    Content must be in 'approved' status.
    """
    _, user_id, auth_department_id = api_key_info

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    cf = _get_cloud_file_or_404(db, cloud_file_id, auth_department_id)

    # File-type rows have no working write-back-to-Canvas path yet (the
    # only candidate mechanism, CloudJobType.UPLOAD, lives in the dormant
    # JobProcessor queue — see c67cb9f). scanner.write_back_content()
    # would return a technically-true but confusing "No remediated body"
    # for a file that WAS remediated (just not as HTML) — give an honest,
    # specific reason instead of letting that ambiguous error surface.
    if cf.content_source == "file":
        return WritebackResponse(
            success=False,
            stale=False,
            error="File write-back to Canvas isn't wired up yet — coming soon.",
        )

    try:
        credential, api_client = await _get_canvas_client(auth_department_id, db)
        try:
            scanner = CanvasContentScanner(
                canvas_client=api_client,
                db=db,
                department_id=auth_department_id,
                credential_id=credential.id,
            )
            result = await scanner.write_back_content(cf, approved_by=user_id)

            return WritebackResponse(
                success=result.get("success", False),
                stale=result.get("stale", False),
                error=result.get("error"),
            )
        finally:
            await api_client.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Write-back failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Write-back failed: {str(e)}",
        )


# =============================================================================
# 9. POST /canvas/content/batch-writeback
# =============================================================================


@router.post("/batch-writeback", response_model=BatchWritebackResponse)
async def batch_writeback_content(
    request: BatchWritebackRequest,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> BatchWritebackResponse:
    """
    Write back all approved content items for a course.
    """
    _, user_id, auth_department_id = api_key_info

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    # Find all approved items for this course
    approved_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.provider == CloudProvider.CANVAS.value,
            CloudFile.provider_parent_id == request.course_id,
            CloudFile.department_id == auth_department_id,
            CloudFile.writeback_status == "approved",
            CloudFile.remediated_body.isnot(None),
        )
        .all()
    )

    # Approved file-type rows (has_remediated_version, remediated_body
    # NULL) are excluded from the query above by construction — they'd
    # otherwise vanish from this response with no acknowledgment at all,
    # the same silent-skip the client eligibility bug produced for approve.
    # There's no working write-back-to-Canvas path for files yet (see
    # writeback_content's comment above), so report them honestly as
    # skipped rather than silently dropping them.
    approved_file_rows = (
        db.query(CloudFile)
        .filter(
            CloudFile.provider == CloudProvider.CANVAS.value,
            CloudFile.provider_parent_id == request.course_id,
            CloudFile.department_id == auth_department_id,
            CloudFile.writeback_status == "approved",
            CloudFile.content_source == "file",
        )
        .all()
    )
    skip_errors = [
        f"{cf.id}: file write-back to Canvas isn't wired up yet — coming soon"
        for cf in approved_file_rows
    ]

    if not approved_files and not approved_file_rows:
        return BatchWritebackResponse(
            written_count=0,
            failed_count=0,
            stale_count=0,
            skipped_count=0,
            errors=["No approved items found for this course"],
        )

    if not approved_files:
        # Only file rows were approved — none of them can be written back
        # yet, so there's nothing to hand to the Canvas client at all.
        return BatchWritebackResponse(
            written_count=0,
            failed_count=0,
            stale_count=0,
            skipped_count=len(approved_file_rows),
            errors=skip_errors,
        )

    try:
        credential, api_client = await _get_canvas_client(auth_department_id, db)
        try:
            scanner = CanvasContentScanner(
                canvas_client=api_client,
                db=db,
                department_id=auth_department_id,
                credential_id=credential.id,
            )

            written = 0
            failed = 0
            stale = 0
            errors: List[str] = list(skip_errors)

            for cf in approved_files:
                result = await scanner.write_back_content(cf, approved_by=user_id)
                if result.get("success"):
                    written += 1
                elif result.get("stale"):
                    stale += 1
                    errors.append(f"{cf.id}: content is stale")
                else:
                    failed += 1
                    errors.append(f"{cf.id}: {result.get('error', 'unknown error')}")

            return BatchWritebackResponse(
                written_count=written,
                failed_count=failed,
                stale_count=stale,
                skipped_count=len(approved_file_rows),
                errors=errors,
            )
        finally:
            await api_client.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch write-back failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Batch write-back failed: {str(e)}",
        )


# =============================================================================
# 10. POST /canvas/content/{cloud_file_id}/rollback
# =============================================================================


@router.post("/{cloud_file_id}/rollback", response_model=RollbackResponse)
async def rollback_content(
    cloud_file_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> RollbackResponse:
    """
    Rollback a written-back content item to its original content.
    """
    _, user_id, auth_department_id = api_key_info

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    cf = _get_cloud_file_or_404(db, cloud_file_id, auth_department_id)

    try:
        credential, api_client = await _get_canvas_client(auth_department_id, db)
        try:
            scanner = CanvasContentScanner(
                canvas_client=api_client,
                db=db,
                department_id=auth_department_id,
                credential_id=credential.id,
            )
            result = await scanner.rollback_content(cf)

            return RollbackResponse(
                success=result.get("success", False),
                error=result.get("error"),
            )
        finally:
            await api_client.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Rollback failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Rollback failed: {str(e)}",
        )


# =============================================================================
# 11. GET /canvas/content/{cloud_file_id}/audit
# =============================================================================


@router.get("/{cloud_file_id}/audit", response_model=AuditResponse)
async def get_audit_log(
    cloud_file_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> AuditResponse:
    """
    Get the write-back audit log for a content item.
    """
    _, user_id, auth_department_id = api_key_info

    await require_feature(
        db, auth_department_id, "lms_integration", "Canvas LMS Integration"
    )

    cf = _get_cloud_file_or_404(db, cloud_file_id, auth_department_id)

    logs = (
        db.query(ContentWritebackLog)
        .filter(ContentWritebackLog.cloud_file_id == cf.id)
        .order_by(ContentWritebackLog.created_at.desc())
        .all()
    )

    entries = [
        AuditEntry(
            id=log.id,
            original_body=log.original_body,
            remediated_body=log.remediated_body,
            approved_by=log.approved_by,
            approved_at=(log.approved_at.isoformat() if log.approved_at else None),
            written_back_at=(
                log.written_back_at.isoformat() if log.written_back_at else None
            ),
            rollback_status=log.rollback_status,
            rolled_back_at=(
                log.rolled_back_at.isoformat() if log.rolled_back_at else None
            ),
            created_at=(log.created_at.isoformat() if log.created_at else None),
        )
        for log in logs
    ]

    return AuditResponse(
        cloud_file_id=cf.id,
        entries=entries,
    )


# =============================================================================
# Background task
# =============================================================================


async def _content_scan_task(
    cloud_file_id: str,
    department_id: str,
    credential_id: str,
    scan_options: Optional[Dict[str, Any]] = None,
):
    """
    Background task to scan a single content item (axe-core + remediate).
    """
    from ..db.database import get_db as _get_db_ctx

    logger.info(
        f"Starting content scan: cloud_file={cloud_file_id}, dept={department_id}"
    )

    with _get_db_ctx() as db:
        cloud_file = db.query(CloudFile).filter(CloudFile.id == cloud_file_id).first()
        if not cloud_file:
            logger.error(f"CloudFile not found: {cloud_file_id}")
            return

        try:
            from ..integrations.canvas import CanvasAPIClient
            from ..integrations.oauth_token_manager import OAuthTokenManager
            from ..db.models import CloudOAuthCredentials

            credential = (
                db.query(CloudOAuthCredentials)
                .filter(CloudOAuthCredentials.id == credential_id)
                .first()
            )
            if not credential:
                logger.error(f"Credential not found: {credential_id}")
                return

            token_manager = OAuthTokenManager()
            access_token = token_manager.decrypt_token(credential.access_token)
            canvas_url = credential.provider_metadata.get("canvas_instance_url", "")

            api_client = CanvasAPIClient(
                canvas_instance_url=canvas_url,
                access_token=access_token,
                credential_id=credential_id,
            )

            try:
                scanner = CanvasContentScanner(
                    canvas_client=api_client,
                    db=db,
                    department_id=department_id,
                    credential_id=credential_id,
                    scan_options=scan_options,
                )

                # 1. Scan
                scan_result = await scanner.scan_content_item(cloud_file)
                logger.info(
                    f"Content scan complete: {cloud_file_id}, "
                    f"issues={scan_result.get('issues', 0)}"
                )

                # 2. Remediate if issues found and auto_remediate is enabled
                if scan_result.get("issues", 0) > 0 and (scan_options or {}).get(
                    "auto_remediate", True
                ):
                    remediation_result = await scanner.remediate_content_item(
                        cloud_file
                    )
                    logger.info(
                        f"Content remediation complete: {cloud_file_id}, "
                        f"fixed={remediation_result.get('fixed_count', 0)}"
                    )

            finally:
                await api_client.close()

        except Exception as e:
            logger.error(
                f"Content scan task failed: {cloud_file_id}, error={e}",
                exc_info=True,
            )
