"""
Canvas Content Scanner Service

Orchestrates the full scan -> remediate -> review -> write-back workflow
for Canvas LMS HTML content (pages, assignments, announcements, quizzes,
discussions).

Workflow:
1. scan_course_content()  — fetch all 5 content types, upsert CloudFile records
2. scan_content_item()    — wrap HTML, run axe-core via Playwright, store results
3. remediate_content_item() — bridge to HtmlRemediator, sanitize output
4. write_back_content()   — stale check, audit log, push to Canvas
5. rollback_content()     — restore original from ContentWritebackLog
"""

import logging
import asyncio
import json
import uuid
import tempfile
from dataclasses import dataclass
from html import escape
import os
from pathlib import Path
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from ..db.models import (
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudJobType,
    CloudOAuthCredentials,
    ContentWritebackLog,
    RemediationArtifact,
    Scan,
    ScanResult,
    ScanType,
    ScanStatus,
)
from ..integrations.canvas.canvas_api import CanvasAPIClient
from ..integrations.canvas.models import CanvasFileInfo
from ..config.settings import get_settings
from ..integrations.canvas.content_models import CanvasContentType
from ..utils.sanitization import sanitize_for_postgres
from ..services.remediation_artifact_service import (
    ArtifactError,
    RemediationArtifactService,
)
from .deterministic_axe import DeterministicScanUnavailable, run_deterministic_axe

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML helpers — used by the scanner and also importable for tests
# ---------------------------------------------------------------------------


AELIRA_CONTENT_ID = "aelira-content"


def _wrap_html_fragment(fragment: str, title: str = "Scan") -> str:
    """
    Wrap a body-only HTML fragment in the document context the LMS renders.

    axe-core needs a complete document to run, and a bare skeleton is not a
    neutral one: with no landmark and no first-level heading, axe reports
    region, landmark-one-main and page-has-heading-one against content whose
    author never had the chance to provide them. The LMS supplies the page
    chrome, so those findings describe our wrapper rather than the page, and
    an author cannot act on them.

    Wrapping in a main landmark with the item's title as the heading matches
    what the LMS actually renders, so axe judges the author's content. The
    fragment sits in a marked container, which is what the unwrapper reads
    back, so nothing we add here can leak into the stored content.

    Args:
        fragment: HTML body content from the LMS
        title: The item's title, rendered as the page heading

    Returns:
        Full HTML document string
    """
    safe_title = escape(title or "Untitled")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        f"<head><title>{safe_title}</title></head>\n"
        "<body>\n"
        "<main>\n"
        f"<h1>{safe_title}</h1>\n"
        f'<div id="{AELIRA_CONTENT_ID}">{fragment}</div>\n'
        "</main>\n"
        "</body>\n"
        "</html>"
    )


def _unwrap_html_fragment(document: str) -> str:
    """
    Extract the inner content of <body> from a full HTML document.

    The inverse of _wrap_html_fragment.  Uses BeautifulSoup so we handle
    edge cases (extra whitespace, varied casing, etc.) gracefully.

    Args:
        document: Full HTML document string

    Returns:
        Inner HTML of the <body> element
    """
    soup = BeautifulSoup(document, "html.parser")
    # The wrapper marks the author's content, so read that back rather than
    # the whole body: the landmark and heading we add for scanning context
    # belong to the LMS, not to the item, and must never be written back.
    marked = soup.find(id=AELIRA_CONTENT_ID)
    if marked is not None:
        return marked.decode_contents()
    body = soup.find("body")
    if body is None:
        return document
    # decode_contents() gives us the inner HTML without the <body> tags
    return body.decode_contents()


def _sanitize_html(html: str) -> str:
    """
    Strip dangerous HTML constructs from remediated content before writing
    back to Canvas.

    Removes:
    - <script>, <iframe>, <object>, <embed>, <base>, <meta>, <style>,
      <link>, <form> tags (and their content)
    - on* event handler attributes (onclick, onload, etc.)
    - javascript:, vbscript:, and data: URLs in href/src/action/xlink:href

    Preserves:
    - Semantic HTML (headings, paragraphs, lists, tables, images, links)
    - Safe attributes (class, id, style, alt, src with http(s), href with http(s))

    Args:
        html: HTML string to sanitize

    Returns:
        Sanitized HTML string
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove dangerous tags entirely
    for tag in soup.find_all(
        ["script", "iframe", "object", "embed", "base", "meta", "style", "link", "form"]
    ):
        tag.decompose()

    # Remove on* event handlers and dangerous URL schemes
    for tag in soup.find_all(True):
        # Collect attribute names to remove (can't modify dict during iteration)
        attrs_to_remove = []
        for attr_name in list(tag.attrs.keys()):
            # Remove on* event handlers
            if attr_name.lower().startswith("on"):
                attrs_to_remove.append(attr_name)
            # Remove dangerous URL schemes (javascript:, vbscript:, data:)
            elif attr_name.lower() in ("href", "src", "action", "xlink:href"):
                value = tag.attrs[attr_name]
                if isinstance(value, str) and re.match(
                    r"\s*(javascript|vbscript|data)\s*:", value, re.IGNORECASE
                ):
                    attrs_to_remove.append(attr_name)

        for attr_name in attrs_to_remove:
            del tag.attrs[attr_name]

    return str(soup)


# ---------------------------------------------------------------------------
# CanvasContentScanner
# ---------------------------------------------------------------------------


def _canonical_issue_identifier(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


# Keep this explicit list aligned with the ALT_TEXT aliases accepted by
# remediation_routes._map_category_string/BaseRemediator, plus axe-core image
# rules and the scanner-specific IDs emitted elsewhere in this repository.
# Exact canonical matching is intentional: substring matching would misroute
# unrelated work such as image_contrast to the alt-text purpose.
_ALT_TEXT_ISSUE_IDENTIFIERS = {
    "alt_text",
    "alternative_text",
    "area_alt",
    "figure_alt",
    "image",
    "image_alt",
    "image_alt_text",
    "image_description",
    "image_of_text",
    "input_image_alt",
    "missing_alt_text",
    "missing_figure_caption",
    "missing_image_description",
    "object_alt",
    "role_img_alt",
    "svg_img_alt",
}


_ALT_TEXT_CANDIDATE_FIELDS = (
    "category",
    "type",
    "issue_type",
    "id",  # axe-core's rule identifier
    "rule",
    "rule_id",
    "axe_id",
    "axe_rule_id",
)


def _is_alt_text_issue(
    raw_issue: Dict[str, Any], normalized_issue: Dict[str, Any]
) -> bool:
    """Recognize image-description work across raw and normalized scanner shapes."""

    candidates = [
        issue.get(field)
        for issue in (normalized_issue, raw_issue)
        for field in _ALT_TEXT_CANDIDATE_FIELDS
    ]
    return any(
        _canonical_issue_identifier(candidate) in _ALT_TEXT_ISSUE_IDENTIFIERS
        for candidate in candidates
    )


def _issue_node_count(issue: Dict[str, Any]) -> int:
    nodes = issue.get("nodes")
    return max(1, len(nodes)) if isinstance(nodes, list) else 1


class _AIUsageTracker:
    """Transparent compatibility wrapper with an aggregate purpose outcome.

    Outcomes describe the whole operation, not merely its final provider call.
    Precedence is ``used`` (any successful AI contribution), then
    ``attempted_failed`` (any failed call attempt), ``denied_at_dispatch``
    (any dispatch denial), and finally ``allowed_not_used``. ``not_requested``
    is reserved for operations where no tracker is constructed.
    """

    _OUTCOME_PRECEDENCE = {
        "allowed_not_used": 0,
        "denied_at_dispatch": 1,
        "attempted_failed": 2,
        "used": 3,
    }

    def __init__(self, wrapped_client: Any, *, requested: bool):
        self.wrapped_client = wrapped_client
        self.ai_used = False
        self.external_ai_used = False
        self.provider_used: Optional[str] = None
        self.outcome = (
            "allowed_not_used"
            if wrapped_client is not None
            else ("denied_at_dispatch" if requested else "not_requested")
        )

    @property
    def provider(self) -> Any:
        return getattr(self.wrapped_client, "provider", None)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.wrapped_client, name)
        if name not in {
            "generate_text_sync",
            "generate_code_sync",
            "analyze_image_sync",
        }:
            return target

        def tracked(*args: Any, **kwargs: Any) -> Any:
            result = target(*args, **kwargs)
            if isinstance(result, dict):
                used = result.get("ai_used") is True
                self.ai_used = self.ai_used or used
                self.external_ai_used = self.external_ai_used or (
                    result.get("external_ai_used") is True
                )
                provider = result.get("provider")
                if isinstance(provider, str):
                    self.provider_used = provider
                reported_outcome = result.get("purpose_outcome")
                successful_contribution = result.get("success") is True and (
                    used
                    or result.get("call_made") is True
                    or reported_outcome == "used"
                )
                attempted_failure = result.get("success") is False and (
                    used
                    or result.get("call_made") is True
                    or reported_outcome in {"used", "attempted_failed"}
                )
                if successful_contribution:
                    call_outcome = "used"
                elif attempted_failure:
                    call_outcome = "attempted_failed"
                elif reported_outcome == "denied_at_dispatch" or (
                    result.get("success") is False
                ):
                    call_outcome = "denied_at_dispatch"
                else:
                    call_outcome = "allowed_not_used"
                if (
                    self._OUTCOME_PRECEDENCE[call_outcome]
                    > self._OUTCOME_PRECEDENCE[self.outcome]
                ):
                    self.outcome = call_outcome
            return result

        return tracked


@dataclass(frozen=True)
class _PendingVerification:
    """Immutable verification data held until artifact cleanup succeeds."""

    scan_id: str
    score: float
    fixed: int
    remaining: int
    introduced: int
    axe_results_json: str
    issues_json: str
    critical_issues: int
    high_issues: int
    medium_issues: int
    low_issues: int


@dataclass(frozen=True)
class _PendingRemediation:
    """Immutable remediation result held outside persistent ORM state."""

    body: str
    fixed_count: int
    manual_count: int
    failed_count: int
    remediated_score: Optional[float]
    verification: Optional[_PendingVerification]


class CanvasContentScanner:
    """
    Orchestrates scanning, remediation, and write-back of Canvas HTML content.

    Designed to be instantiated per-request or per-job with:
    - A CanvasAPIClient already authenticated for the target institution
    - A SQLAlchemy Session for DB operations
    - The department_id that owns the Canvas credential

    The class is intentionally *not* tied to a single course — call
    scan_course_content() for each course.
    """

    def __init__(
        self,
        canvas_client: CanvasAPIClient,
        db: Session,
        department_id: str,
        credential_id: str,
        course_name: str = "",
        course_code: str = "",
        scan_options: Optional[Dict[str, Any]] = None,
        artifact_service: Optional[RemediationArtifactService] = None,
    ):
        self.canvas_client = canvas_client
        self.db = db
        self.department_id = department_id
        self.credential_id = credential_id
        self.artifact_service = (
            artifact_service or RemediationArtifactService.from_settings()
        )
        self.course_name = course_name
        self.course_code = course_code
        # Safe fallbacks are deterministic. Explicit options are retained for
        # separate remediation operations; scan methods never consult them.
        self.scan_options = scan_options or {
            "generate_alt_text": False,
            "auto_remediate": False,
            "detect_decorative": False,
        }

    # ------------------------------------------------------------------
    # 1. scan_course_content — discover and upsert all content items
    # ------------------------------------------------------------------

    async def scan_course_content(
        self,
        course_id: str,
        content_types: Optional[List[CanvasContentType]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch content types from a Canvas course in parallel,
        upsert CloudFile records for items that have HTML body content,
        and queue scan jobs for each.

        Args:
            course_id: Canvas course ID
            content_types: Optional list of content types to scan.
                If None, scans all 6 types (5 HTML types + files).

        Returns:
            Dict with counts per content type; cloud_file_ids (the 5 HTML
            types — the caller fires the axe-core background task for each);
            and file_scan_jobs ([{job_id, cloud_file_id}, ...] — CloudJobQueue
            rows already created for each file, still needing the caller to
            fire _canvas_scan_file_task per job; the scanner has no
            BackgroundTasks handle to do that itself).
        """
        # Default to all types if none specified
        types_to_scan = set(content_types or list(CanvasContentType))

        logger.info(
            "Scanning course content",
            extra={
                "course_id": course_id,
                "department_id": self.department_id,
                "content_types": [t.value for t in types_to_scan],
            },
        )

        # Build coroutines only for requested types
        coros = []
        type_order = []

        if CanvasContentType.PAGE in types_to_scan:
            coros.append(self.canvas_client.list_course_pages(course_id))
            type_order.append("page")
        if CanvasContentType.ASSIGNMENT in types_to_scan:
            coros.append(self.canvas_client.list_course_assignments(course_id))
            type_order.append("assignment")
        if CanvasContentType.ANNOUNCEMENT in types_to_scan:
            coros.append(self.canvas_client.list_course_announcements(course_id))
            type_order.append("announcement")
        if CanvasContentType.QUIZ in types_to_scan:
            coros.append(self.canvas_client.list_course_quizzes(course_id))
            type_order.append("quiz")
        if CanvasContentType.DISCUSSION in types_to_scan:
            coros.append(self.canvas_client.list_course_discussions(course_id))
            type_order.append("discussion")
        if CanvasContentType.FILE in types_to_scan:
            coros.append(self.canvas_client.list_course_files(course_id))
            type_order.append("file")

        # Parallel fetch requested content types
        results = await asyncio.gather(*coros, return_exceptions=True)

        # Map results back to named lists
        fetched: Dict[str, list] = {}
        for i, type_name in enumerate(type_order):
            result = results[i]
            if isinstance(result, BaseException):
                logger.error("Failed to fetch %s: %s", type_name, result)
                fetched[type_name] = []
            else:
                fetched[type_name] = result

        pages = fetched.get("page", [])
        assignments = fetched.get("assignment", [])
        announcements = fetched.get("announcement", [])
        quizzes = fetched.get("quiz", [])
        discussions = fetched.get("discussion", [])
        files = fetched.get("file", [])

        cloud_file_ids: List[str] = []
        # Files get their own CloudJobQueue SCAN row (created below) instead
        # of flowing through cloud_file_ids — that list drives the caller's
        # axe-core-only background task loop, which would no-op on a file
        # ("No content body"). The scanner has no FastAPI BackgroundTasks
        # handle to actually fire the file-download task itself, so it hands
        # back (job_id, cloud_file_id) pairs for the route handler to fire
        # _canvas_scan_file_task per job — exactly the pattern
        # canvas_scan_routes.py's single-file scan endpoint already uses.
        # Without that background_tasks.add_task() call the job row is
        # inert: nothing in this app polls CloudJobQueue (JobProcessor is
        # never started), so a queued-but-unfired row sits PENDING forever.
        file_scan_jobs: List[Dict[str, str]] = []
        counts = {
            "page": 0,
            "assignment": 0,
            "announcement": 0,
            "quiz": 0,
            "discussion": 0,
            "file": 0,
            "skipped_empty": 0,
        }

        # Process pages — list endpoint doesn't include body, fetch each individually
        for page in pages:
            body = page.body
            updated_at = page.updated_at
            try:
                full_page = await self.canvas_client.get_page(course_id, page.url_slug)
                body = full_page.body
                updated_at = full_page.updated_at
            except Exception as e:
                logger.error("Failed to fetch page body for %s: %s", page.title, e)
            if not body or not body.strip():
                counts["skipped_empty"] += 1
                continue
            cf = self._upsert_cloud_file(
                course_id=course_id,
                content_source=CanvasContentType.PAGE,
                provider_file_id=page.page_id,
                file_name=page.title,
                content_body=body,
                content_slug=page.url_slug,
                content_updated_at=updated_at,
            )
            cloud_file_ids.append(cf.id)
            counts["page"] += 1

        # Process assignments
        for assignment in assignments:
            if not assignment.description or not assignment.description.strip():
                counts["skipped_empty"] += 1
                continue
            cf = self._upsert_cloud_file(
                course_id=course_id,
                content_source=CanvasContentType.ASSIGNMENT,
                provider_file_id=assignment.id,
                file_name=assignment.name,
                content_body=assignment.description,
                content_slug=None,
                content_updated_at=assignment.updated_at,
            )
            cloud_file_ids.append(cf.id)
            counts["assignment"] += 1

        # Process announcements
        for announcement in announcements:
            if not announcement.message or not announcement.message.strip():
                counts["skipped_empty"] += 1
                continue
            cf = self._upsert_cloud_file(
                course_id=course_id,
                content_source=CanvasContentType.ANNOUNCEMENT,
                provider_file_id=announcement.id,
                file_name=announcement.title,
                content_body=announcement.message,
                content_slug=None,
                content_updated_at=announcement.updated_at,
            )
            cloud_file_ids.append(cf.id)
            counts["announcement"] += 1

        # Process quizzes
        for quiz in quizzes:
            if not quiz.description or not quiz.description.strip():
                counts["skipped_empty"] += 1
                continue
            cf = self._upsert_cloud_file(
                course_id=course_id,
                content_source=CanvasContentType.QUIZ,
                provider_file_id=quiz.id,
                file_name=quiz.title,
                content_body=quiz.description,
                content_slug=None,
                content_updated_at=quiz.updated_at,
            )
            cloud_file_ids.append(cf.id)
            counts["quiz"] += 1

        # Process discussions
        for discussion in discussions:
            if not discussion.message or not discussion.message.strip():
                counts["skipped_empty"] += 1
                continue
            cf = self._upsert_cloud_file(
                course_id=course_id,
                content_source=CanvasContentType.DISCUSSION,
                provider_file_id=discussion.id,
                file_name=discussion.title,
                content_body=discussion.message,
                content_slug=None,
                content_updated_at=discussion.updated_at,
            )
            cloud_file_ids.append(cf.id)
            counts["discussion"] += 1

        # Process files. Unlike the HTML types above, files have no
        # content_body to run axe-core on directly — they're real uploaded
        # documents (pdf/docx/pptx/...) scanned by downloading and running
        # the document scanner via the CloudJobQueue pipeline (the same one
        # canvas_scan_routes.py's single-file scan endpoint uses). The row
        # is created here; it's inert until the route handler actually
        # fires the background task for it (see file_scan_jobs above).
        for file_info in files:
            cf = self._upsert_file_cloud_file(course_id=course_id, file_info=file_info)
            job_id = str(uuid.uuid4())
            counts["file"] += 1
            self.db.add(
                CloudJobQueue(
                    id=job_id,
                    department_id=self.department_id,
                    job_type=CloudJobType.SCAN.value,
                    provider="canvas",
                    provider_file_id=cf.provider_file_id,
                    cloud_file_id=cf.id,
                    credential_id=self.credential_id,
                    status=CloudJobStatus.PENDING.value,
                )
            )
            file_scan_jobs.append({"job_id": job_id, "cloud_file_id": cf.id})

        self.db.commit()

        logger.info(
            "Course content scan complete",
            extra={
                "course_id": course_id,
                "department_id": self.department_id,
                "counts": counts,
            },
        )

        return {
            "course_id": course_id,
            "cloud_file_ids": cloud_file_ids,
            "file_scan_jobs": file_scan_jobs,
            "counts": counts,
            "operation_kind": "deterministic_scan",
            "external_ai_used": False,
            "ai_used": False,
        }

    # ------------------------------------------------------------------
    # 2. scan_content_item — run axe-core on a single content item
    # ------------------------------------------------------------------

    async def scan_content_item(self, cloud_file: CloudFile) -> Dict[str, Any]:
        """
        Wrap the content item's HTML in a document skeleton, run axe-core
        via Playwright page.set_content(), and store the Scan + ScanResult.

        Args:
            cloud_file: CloudFile with content_body populated

        Returns:
            Dict with scan_id and issue count
        """
        if not cloud_file.content_body:
            return {
                "success": False,
                "scan_id": None,
                "issues": 0,
                "compliance_score": None,
                "error": "No content body",
                "error_code": "EMPTY_CONTENT",
                "operation_kind": "deterministic_scan",
                "external_ai_used": False,
                "ai_used": False,
            }

        wrapped_html = _wrap_html_fragment(
            cloud_file.content_body, cloud_file.file_name
        )

        # Create Scan record — no authenticated user for LTI-initiated scans
        scan = Scan(
            id=str(uuid.uuid4()),
            scan_type=ScanType.CANVAS_CONTENT,
            status=ScanStatus.PROCESSING,
            file_name=cloud_file.file_name,
            user_id=None,
            department_id=self.department_id,
        )
        self.db.add(scan)
        self.db.flush()

        try:
            # Run axe-core via Playwright
            axe_results = await self._run_axe_scan(wrapped_html)

            violations = axe_results.get("violations", [])
            issue_count = sum(len(v.get("nodes", [])) for v in violations)

            # Calculate simple compliance score
            passes = len(axe_results.get("passes", []))
            total_rules = passes + len(violations)
            if total_rules <= 0:
                raise DeterministicScanUnavailable()
            compliance_score = round(passes / total_rules * 100, 1)

            # Store ScanResult
            scan_result = ScanResult(
                id=str(uuid.uuid4()),
                scan_id=scan.id,
                compliance_score=compliance_score,
                axe_results=axe_results,
                issues=violations,
                critical_issues=sum(
                    1 for v in violations if v.get("impact") == "critical"
                ),
                high_issues=sum(1 for v in violations if v.get("impact") == "serious"),
                medium_issues=sum(
                    1 for v in violations if v.get("impact") == "moderate"
                ),
                low_issues=sum(1 for v in violations if v.get("impact") == "minor"),
            )
            self.db.add(scan_result)

            scan.status = ScanStatus.COMPLETED
            scan.completed_at = datetime.now(timezone.utc)

            # Update cloud file scan state
            cloud_file.last_scan_id = scan.id
            cloud_file.last_scanned_at = datetime.now(timezone.utc)
            cloud_file.last_compliance_score = compliance_score
            cloud_file.needs_rescan = False

            self.db.commit()

            logger.info(
                "Content scan complete",
                extra={
                    "cloud_file_id": cloud_file.id,
                    "scan_id": scan.id,
                    "issues": issue_count,
                    "score": compliance_score,
                },
            )

            return {
                "success": True,
                "scan_id": scan.id,
                "issues": issue_count,
                "compliance_score": compliance_score,
                "operation_kind": "deterministic_scan",
                "external_ai_used": False,
                "ai_used": False,
            }

        except Exception as exc:
            scan.status = ScanStatus.FAILED
            scan.error_message = "DETERMINISTIC_SCAN_UNAVAILABLE"
            cloud_file.needs_rescan = True
            self.db.commit()
            logger.error(
                "Content deterministic scan failed",
                extra={
                    "cloud_file_id": cloud_file.id,
                    "error_code": "DETERMINISTIC_SCAN_UNAVAILABLE",
                    "exception_type": type(exc).__name__,
                },
            )
            return {
                "success": False,
                "scan_id": scan.id,
                "issues": 0,
                "compliance_score": None,
                "error": "Deterministic accessibility scan unavailable",
                "error_code": "DETERMINISTIC_SCAN_UNAVAILABLE",
                "operation_kind": "deterministic_scan",
                "external_ai_used": False,
                "ai_used": False,
            }

    # ------------------------------------------------------------------
    # 3. remediate_content_item — bridge to HtmlRemediator
    # ------------------------------------------------------------------

    async def remediate_content_item(
        self,
        cloud_file: CloudFile,
        *,
        remediation_client: Any = None,
        alt_text_client: Any = None,
        requested_purposes: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        """
        Load accessibility issues from the last scan, run HtmlRemediator
        on the content via a temporary file, sanitize the output, and
        store the result in cloud_file.remediated_body.

        Args:
            cloud_file: CloudFile with content_body and a completed scan

        Returns:
            Dict with remediation result summary
        """
        requested_purposes = requested_purposes or set()
        remediation_tracker = _AIUsageTracker(
            remediation_client,
            requested="remediation" in requested_purposes,
        )
        alt_text_tracker = _AIUsageTracker(
            alt_text_client,
            requested="alt_text" in requested_purposes,
        )

        def usage_metadata() -> Dict[str, Any]:
            trackers = (remediation_tracker, alt_text_tracker)
            providers = [
                tracker.provider_used for tracker in trackers if tracker.provider_used
            ]
            return {
                "ai_used": any(tracker.ai_used for tracker in trackers),
                "external_ai_used": any(
                    tracker.external_ai_used for tracker in trackers
                ),
                "provider": providers[0] if providers else None,
                "purpose_decisions": {
                    "remediation": remediation_tracker.outcome,
                    "alt_text": alt_text_tracker.outcome,
                },
            }

        if not cloud_file.content_body:
            return {
                "success": False,
                "error": "No content body",
                **usage_metadata(),
            }

        # Load issues from last scan
        issues = []
        if cloud_file.last_scan_id:
            scan_result = (
                self.db.query(ScanResult)
                .filter(ScanResult.scan_id == cloud_file.last_scan_id)
                .first()
            )
            if scan_result and scan_result.issues:
                issues = scan_result.issues

        if not issues:
            return {
                "success": True,
                "fixed_count": 0,
                "message": "No issues to fix",
                **usage_metadata(),
            }

        from ..api.education.remediation_routes import (
            _normalize_issues_for_remediation,
        )

        normalized_issues = _normalize_issues_for_remediation(issues)
        alt_text_issues: List[Dict[str, Any]] = []
        remediation_issues: List[Dict[str, Any]] = []
        for raw_issue, normalized_issue in zip(issues, normalized_issues):
            if _is_alt_text_issue(raw_issue, normalized_issue):
                alt_text_issues.append(raw_issue)
            else:
                remediation_issues.append(normalized_issue)

        # Image-description issues are isolated from HtmlRemediator because its
        # text prompts cannot inspect Canvas images. Only the vision-bound
        # alt-text client may handle them.
        state_fields = (
            "remediated_body",
            "writeback_status",
            "has_remediated_version",
            "remediated_compliance_score",
            "remediated_issues_fixed",
            "remediated_issues_remaining",
        )
        original_state = tuple(
            getattr(cloud_file, field, None) for field in state_fields
        )
        durable_mutation_started = False

        def failed_remediation() -> Dict[str, Any]:
            return {
                "success": False,
                "error": "Content remediation failed",
                "error_code": "REMEDIATION_FAILED",
                **usage_metadata(),
            }

        try:
            from ..education.remediation.base import RemediationConfig
            from ..education.remediation.html_remediator import HtmlRemediator

            source_html = cloud_file.content_body
            images_described = 0
            if alt_text_issues and alt_text_client is not None:
                source_html, images_described = await self._describe_images(
                    cloud_file,
                    source_html,
                    alt_text_client=alt_text_tracker,
                )
            unresolved_alt_text = max(
                0,
                sum(_issue_node_count(issue) for issue in alt_text_issues)
                - images_described,
            )

            # The entire remediation, readback, sanitization, verification, and
            # score calculation occurs while the owned directory exists. Only
            # immutable pending values escape it. No ORM mutation or commit is
            # permitted until TemporaryDirectory.__exit__ has succeeded.
            wrapped_html = _wrap_html_fragment(source_html, cloud_file.file_name)
            with tempfile.TemporaryDirectory(prefix="aelira-canvas-html-") as temp_dir:
                artifact_root = Path(temp_dir)
                source_path = artifact_root / "source.html"
                source_path.write_text(wrapped_html, encoding="utf-8")

                config = RemediationConfig(
                    use_ai=remediation_client is not None,
                    create_backup=False,
                    output_directory=str(artifact_root),
                )
                remediator = HtmlRemediator(
                    str(source_path),
                    remediation_issues,
                    config=config,
                    ai_client=(
                        remediation_tracker if remediation_client is not None else None
                    ),
                )
                result = remediator.remediate()
                if result.success is not True:
                    return failed_remediation()

                fixed_count = result.fixed_count + images_described
                manual_count = result.manual_count + unresolved_alt_text
                failed_count = getattr(result, "failed_count", 0)

                output_path = Path(result.output_file or source_path).resolve()
                if not output_path.is_relative_to(artifact_root.resolve()):
                    return failed_remediation()
                remediated_doc = output_path.read_text(encoding="utf-8")
                body_fragment = _unwrap_html_fragment(remediated_doc)
                sanitized = _sanitize_html(body_fragment)

                verification = await self._verify_remediation(
                    cloud_file, sanitized, issues
                )
                remediated_score = getattr(result, "remediated_compliance_score", None)
                if (
                    verification is None
                    and remediated_score is None
                    and cloud_file.last_compliance_score is not None
                ):
                    total = fixed_count + manual_count + failed_count
                    if total > 0:
                        fix_ratio = fixed_count / total
                        original = cloud_file.last_compliance_score
                        remediated_score = min(
                            100.0, round(original + (100 - original) * fix_ratio, 1)
                        )
                if verification is not None:
                    remediated_score = verification.score

                pending = _PendingRemediation(
                    body=sanitize_for_postgres(sanitized),
                    fixed_count=fixed_count,
                    manual_count=manual_count,
                    failed_count=failed_count,
                    remediated_score=remediated_score,
                    verification=verification,
                )

            # Cleanup has now completed successfully. Durable ORM state begins
            # changing only after this boundary, followed by one commit.
            durable_mutation_started = True
            cloud_file.remediated_body = pending.body
            cloud_file.writeback_status = "pending_review"
            cloud_file.has_remediated_version = True
            cloud_file.remediated_compliance_score = pending.remediated_score

            verification = pending.verification
            if verification is not None:
                cloud_file.remediated_issues_fixed = verification.fixed
                cloud_file.remediated_issues_remaining = verification.remaining
                self.db.add(
                    Scan(
                        id=verification.scan_id,
                        scan_type=ScanType.CANVAS_CONTENT,
                        status=ScanStatus.COMPLETED,
                        file_name=f"{cloud_file.file_name} (remediated)",
                        user_id=None,
                        department_id=self.department_id,
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                self.db.add(
                    ScanResult(
                        id=str(uuid.uuid4()),
                        scan_id=verification.scan_id,
                        compliance_score=verification.score,
                        axe_results=json.loads(verification.axe_results_json),
                        issues=json.loads(verification.issues_json),
                        critical_issues=verification.critical_issues,
                        high_issues=verification.high_issues,
                        medium_issues=verification.medium_issues,
                        low_issues=verification.low_issues,
                    )
                )

            self.db.commit()

            if verification is not None:
                if verification.introduced:
                    logger.warning(
                        "Remediation introduced new issues",
                        extra={
                            "cloud_file_id": cloud_file.id,
                            "introduced": verification.introduced,
                        },
                    )
                logger.info(
                    "Content remediation verified by rescan",
                    extra={
                        "cloud_file_id": cloud_file.id,
                        "score": verification.score,
                        "fixed": verification.fixed,
                        "remaining": verification.remaining,
                        "introduced": verification.introduced,
                    },
                )
                return {
                    "success": True,
                    "verified": True,
                    "fixed_count": verification.fixed,
                    "issues_remaining": verification.remaining,
                    "issues_introduced": verification.introduced,
                    "manual_count": verification.remaining,
                    "remediated_score": verification.score,
                    "verification_scan_id": verification.scan_id,
                    **usage_metadata(),
                }

            logger.info(
                "Content remediation complete",
                extra={
                    "cloud_file_id": cloud_file.id,
                    "fixed_count": pending.fixed_count,
                    "remediated_score": pending.remediated_score,
                },
            )
            return {
                "success": True,
                "verified": False,
                "fixed_count": pending.fixed_count,
                "manual_count": pending.manual_count,
                "issues_remaining": pending.manual_count + pending.failed_count,
                "remediated_score": pending.remediated_score,
                **usage_metadata(),
            }

        except Exception as exc:
            try:
                self.db.rollback()
            except Exception:
                pass
            if durable_mutation_started:
                for field, value in zip(state_fields, original_state):
                    setattr(cloud_file, field, value)
            logger.error(
                "Content remediation failed",
                extra={
                    "cloud_file_id": str(cloud_file.id),
                    "error_type": type(exc).__name__,
                    "error_code": "REMEDIATION_FAILED",
                },
            )
            return failed_remediation()

    # ------------------------------------------------------------------
    # 3a. _describe_images — real alt text, from the actual image
    # ------------------------------------------------------------------

    _FILE_ID_PATTERN = re.compile(r"/files/(\d+)(?=$|[/?#])")

    async def _describe_images(
        self,
        cloud_file: CloudFile,
        html: str,
        *,
        alt_text_client: Any,
    ) -> tuple:
        """Write alt text for images that have none, from the image itself.

        The remediator's own alt-text path asks a text model to invent a
        description from an issue message and a code snippet, having never
        seen the image. That is how a chart came back marked decorative and
        a rubric came back described as a photograph.

        Documents carry their images inside them, which is why the file
        path has always produced real descriptions. Content stored by an LMS
        refers to images by URL, so the image has to be fetched first, with
        the credential, before the same vision service can look at it.

        Canvas' course-scoped file inventory is authoritative for this
        remediation operation: it is fetched immediately before downloads,
        and only IDs present in that inventory may reach the account-level
        download API or the vision client. This deliberately does not promise
        authorization beyond the operation's inventory snapshot.

        Returns the HTML and the number of images actually described.
        """
        soup = BeautifulSoup(html, "html.parser")
        targets = [
            img
            for img in soup.find_all("img")
            if img.get("alt") is None and img.get("role") != "presentation"
        ]
        if not targets:
            return html, 0

        cloud_file_id = str(getattr(cloud_file, "id", ""))
        candidates = []
        for index, img in enumerate(targets):
            raw_source = img.get("data-api-endpoint") or img.get("src", "")
            source = raw_source if isinstance(raw_source, str) else ""
            match = self._FILE_ID_PATTERN.search(source)
            if not match:
                logger.info(
                    "Image source is not an LMS file; manual review required",
                    extra={
                        "cloud_file_id": cloud_file_id,
                        "error_code": "IMAGE_SOURCE_NOT_LMS_FILE",
                    },
                )
                continue
            candidates.append((index, img, str(int(match.group(1)))))

        if not candidates:
            return html, 0

        raw_course_id = getattr(cloud_file, "provider_parent_id", None)
        course_id = str(raw_course_id).strip() if raw_course_id is not None else ""
        if not course_id:
            for _, _, file_id in candidates:
                logger.warning(
                    "Canvas image course binding unavailable; manual review required",
                    extra={
                        "cloud_file_id": cloud_file_id,
                        "course_id": course_id,
                        "file_id": file_id,
                        "error_type": "MissingCourseId",
                        "error_code": "IMAGE_COURSE_BINDING_MISSING",
                    },
                )
            return html, 0

        try:
            inventory = await self.canvas_client.list_course_files(course_id)
            if not isinstance(inventory, (list, tuple)):
                raise TypeError("invalid Canvas course file inventory")

            inventory_by_id: Dict[str, CanvasFileInfo] = {}
            allowed_image_mimes = {
                "image/png",
                "image/jpeg",
                "image/gif",
                "image/webp",
                "image/bmp",
            }
            max_image_bytes = get_settings().max_file_size_image
            for entry in inventory:
                if type(entry) is not CanvasFileInfo:
                    continue
                raw_id = entry.id
                if isinstance(raw_id, bool):
                    continue
                normalized = str(raw_id).strip() if raw_id is not None else ""
                if not normalized.isdecimal():
                    continue
                if (
                    not isinstance(entry.content_type, str)
                    or entry.content_type.casefold() not in allowed_image_mimes
                    or isinstance(entry.size, bool)
                    or not isinstance(entry.size, int)
                    or entry.size <= 0
                    or entry.size > max_image_bytes
                ):
                    continue
                inventory_by_id[str(int(normalized))] = entry
        except Exception as exc:
            for _, _, file_id in candidates:
                logger.warning(
                    "Canvas course file inventory failed; manual review required",
                    extra={
                        "cloud_file_id": cloud_file_id,
                        "course_id": course_id,
                        "file_id": file_id,
                        "error_type": type(exc).__name__,
                        "error_code": "IMAGE_COURSE_INVENTORY_FAILED",
                    },
                )
            return html, 0

        from ..education.image_alt_text import ImageAltTextGenerator
        from ..education.remediation.html_remediator import HtmlRemediator

        generator = ImageAltTextGenerator(lms_client=alt_text_client)
        described = 0

        # One owned directory contains every downloaded image. Per-image
        # failures remain manual work, but directory cleanup failure is outside
        # their exception boundary and therefore propagates to remediation.
        with tempfile.TemporaryDirectory(prefix="aelira-canvas-images-") as temp_dir:
            artifact_root = Path(temp_dir)
            for index, img, file_id in candidates:
                # The course-scoped inventory snapshot above is authoritative
                # at this point in the operation. Never infer membership from
                # HTML course hints or fall back to Canvas' global get_file.
                file_info = inventory_by_id.get(file_id)
                if file_info is None:
                    logger.warning(
                        "Canvas image is not in course inventory; manual review required",
                        extra={
                            "cloud_file_id": cloud_file_id,
                            "course_id": course_id,
                            "file_id": file_id,
                            "error_type": "CourseMembershipDenied",
                            "error_code": "IMAGE_FILE_NOT_IN_COURSE",
                        },
                    )
                    continue
                try:
                    result = await self.canvas_client.download_course_image(
                        file_info,
                        max_bytes=max_image_bytes,
                    )
                    image_data = getattr(result, "data", None)
                    observed_mime = getattr(result, "content_type", None)
                    observed_suffix = getattr(result, "suffix", None)
                    if (
                        not getattr(result, "success", False)
                        or not isinstance(image_data, bytes)
                        or not image_data
                        or observed_mime != file_info.content_type.casefold()
                        or observed_suffix
                        not in {".png", ".jpg", ".gif", ".webp", ".bmp"}
                    ):
                        logger.warning(
                            "Canvas image download failed; manual review required",
                            extra={
                                "cloud_file_id": cloud_file_id,
                                "course_id": course_id,
                                "file_id": file_id,
                                "error_type": "DownloadUnsuccessful",
                                "error_code": "IMAGE_DOWNLOAD_FAILED",
                            },
                        )
                        continue

                    partial_path = (artifact_root / f"image-{index}.part").resolve()
                    image_path = (
                        artifact_root / f"image-{index}{observed_suffix}"
                    ).resolve()
                    resolved_root = artifact_root.resolve()
                    if not partial_path.is_relative_to(
                        resolved_root
                    ) or not image_path.is_relative_to(resolved_root):
                        raise ValueError("Canvas image artifact escaped containment")
                    partial_path.write_bytes(image_data)
                    os.replace(partial_path, image_path)

                    generated = await generator.generate_alt_text(
                        str(image_path),
                        context=f"Image in {cloud_file.file_name}",
                        trusted_mime_type=observed_mime,
                        trusted_suffix=observed_suffix,
                    )
                    alt_text = (generated or {}).get("alt_text", "")
                    if not generated.get(
                        "success"
                    ) or not HtmlRemediator.is_usable_alt_text(alt_text):
                        logger.info(
                            "No usable image description; manual review required",
                            extra={
                                "cloud_file_id": cloud_file_id,
                                "course_id": course_id,
                                "file_id": file_id,
                                "error_type": "UnusableDescription",
                                "error_code": "IMAGE_DESCRIPTION_UNUSABLE",
                            },
                        )
                        continue

                    img["alt"] = alt_text.strip()
                    described += 1
                except Exception as exc:
                    logger.warning(
                        "Canvas image description failed; manual review required",
                        extra={
                            "cloud_file_id": cloud_file_id,
                            "course_id": course_id,
                            "file_id": file_id,
                            "error_type": type(exc).__name__,
                            "error_code": "IMAGE_DESCRIPTION_FAILED",
                        },
                    )

        return (str(soup) if described else html), described

    # ------------------------------------------------------------------
    # 3b. _verify_remediation — rescan what remediation produced
    # ------------------------------------------------------------------

    async def _verify_remediation(
        self,
        cloud_file: CloudFile,
        remediated_fragment: str,
        original_issues: List[Dict[str, Any]],
    ) -> Optional[_PendingVerification]:
        """Rescan the remediated content and report what actually changed.

        Without this the remediated score is an estimate derived from how
        many fixers ran, which cannot see a fix that did not work or a fix
        that broke something else. The rescan is the same axe-core pass the
        original scan used, so the two scores are comparable.

        Counting is at node level, matching the original scan's issue count:
        remaining counts nodes still failing a rule that failed before,
        introduced counts nodes failing a rule that did not fail before, and
        fixed is the drop in node count across the rules that failed before.

        Returns None when the rescan cannot run, in which case the caller
        keeps the estimate and marks the result unverified.
        """

        def _nodes_by_rule(violations: List[Dict[str, Any]]) -> Dict[str, int]:
            counts: Dict[str, int] = {}
            for v in violations or []:
                rule_id = v.get("id")
                if rule_id:
                    counts[rule_id] = counts.get(rule_id, 0) + len(v.get("nodes", []))
            return counts

        try:
            wrapped = _wrap_html_fragment(remediated_fragment, cloud_file.file_name)
            axe_results = await self._run_axe_scan(wrapped)
        except Exception as exc:
            logger.warning(
                "Remediation rescan failed; falling back to the estimate",
                extra={
                    "cloud_file_id": cloud_file.id,
                    "scan_id": cloud_file.last_scan_id,
                    "error_type": type(exc).__name__,
                    "error_code": "REMEDIATION_RESCAN_FAILED",
                },
            )
            return None

        violations = axe_results.get("violations", [])
        passes = len(axe_results.get("passes", []))
        total_rules = passes + len(violations)
        score = round(passes / total_rules * 100, 1) if total_rules > 0 else 100.0

        before = _nodes_by_rule(original_issues)
        after = _nodes_by_rule(violations)

        # A rule that failed before and fails harder afterwards has had
        # failures introduced as well as failures remaining. Counting the
        # whole after-total as "remaining" would hide that: the honest
        # split is what was already failing, and what is new on top.
        remaining = 0
        introduced = 0
        fixed = 0
        for rule, count in after.items():
            was = before.get(rule, 0)
            remaining += min(count, was)
            introduced += max(0, count - was)
        for rule, was in before.items():
            fixed += max(0, was - after.get(rule, 0))

        # Keep verification entirely local until the caller's owned artifact
        # directory has cleaned up. JSON strings make the nested provider data
        # immutable pending values rather than live mutable dictionaries.
        return _PendingVerification(
            scan_id=str(uuid.uuid4()),
            score=score,
            fixed=fixed,
            remaining=remaining,
            introduced=introduced,
            axe_results_json=json.dumps(axe_results, sort_keys=True),
            issues_json=json.dumps(violations, sort_keys=True),
            critical_issues=sum(
                1 for violation in violations if violation.get("impact") == "critical"
            ),
            high_issues=sum(
                1 for violation in violations if violation.get("impact") == "serious"
            ),
            medium_issues=sum(
                1 for violation in violations if violation.get("impact") == "moderate"
            ),
            low_issues=sum(
                1 for violation in violations if violation.get("impact") == "minor"
            ),
        )

    # ------------------------------------------------------------------
    # 3c. write_back_file — upload a remediated file to the Canvas course
    # ------------------------------------------------------------------

    def _lock_file_writeback_graph(
        self, requested: CloudFile
    ) -> tuple[CloudFile, Scan, RemediationArtifact]:
        """Lock and revalidate the exact current Canvas artifact authority."""
        artifact_id = requested.current_remediation_artifact_id
        if not artifact_id:
            raise ValueError("artifact_not_current")
        _, scan, cloud_file, _, artifact = self.artifact_service.lock_current(
            self.db,
            artifact_id=artifact_id,
            department_id=self.department_id,
            cloud_file_id=requested.id,
            provider="canvas",
        )
        if cloud_file is None or artifact is None:
            raise ValueError("artifact_not_current")
        credential = (
            self.db.query(CloudOAuthCredentials)
            .filter(CloudOAuthCredentials.id == self.credential_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if (
            credential is None
            or credential.id != cloud_file.credential_id
            or credential.department_id != self.department_id
            or credential.provider != "canvas"
            or not credential.is_active
        ):
            raise ValueError("credential_not_current")
        return cloud_file, scan, artifact

    def _persist_file_reconciliation(
        self,
        *,
        cloud_file: CloudFile,
        artifact: RemediationArtifact,
        approved_by: str,
        correlation_id: str,
        provider_result: Dict[str, Any],
        accessible_name: str,
    ) -> None:
        """Commit an ambiguity record after the main transaction rolled back."""
        self.db.add(
            ContentWritebackLog(
                id=str(uuid.uuid4()),
                cloud_file_id=cloud_file.id,
                original_body=(
                    f"canvas-file:{cloud_file.provider_file_id} {cloud_file.file_name}"
                ),
                remediated_body=f"canvas-file:unknown {accessible_name}",
                approved_by=approved_by,
                approved_at=artifact.approved_at,
                artifact_id=artifact.id,
                artifact_checksum=artifact.sha256,
                correlation_id=correlation_id,
                reconciliation_status="reconciliation_required",
                provider_result=provider_result,
            )
        )
        self.db.commit()

    async def write_back_file(
        self,
        cloud_file: CloudFile,
        approved_by: str,
    ) -> Dict[str, Any]:
        """Upload the exact current approved artifact from its verified descriptor."""
        if cloud_file.content_source != "file":
            return {
                "success": False,
                "stale": False,
                "error": "Not a file row; use write_back_content for content items",
            }
        try:
            cloud_file, _scan, artifact = self._lock_file_writeback_graph(cloud_file)
        except (ArtifactError, ValueError):
            self.db.rollback()
            return {
                "success": False,
                "stale": False,
                "error": "Managed remediation artifact is unavailable",
                "error_code": "artifact_unavailable",
            }
        unresolved = (
            self.db.query(ContentWritebackLog)
            .filter(
                ContentWritebackLog.artifact_id == artifact.id,
                ContentWritebackLog.reconciliation_status == "reconciliation_required",
            )
            .first()
        )
        if isinstance(unresolved, ContentWritebackLog):
            self.db.rollback()
            return {
                "success": False,
                "stale": False,
                "error": "Writeback reconciliation is unresolved",
                "error_code": "writeback_reconciliation_required",
                "retry_safe": False,
            }
        if cloud_file.writeback_status != "approved":
            self.db.rollback()
            return {
                "success": False,
                "stale": False,
                "error": "Artifact is not approved",
                "error_code": "artifact_not_approved",
            }
        course_id = cloud_file.provider_parent_id
        if not course_id:
            self.db.rollback()
            return {"success": False, "stale": False, "error": "File has no course"}

        original = Path(str(cloud_file.file_name))
        accessible_name = f"{original.stem}_accessible{original.suffix}"
        correlation_id = str(uuid.uuid4())
        try:
            with self.artifact_service.open_verified(
                self.db,
                artifact,
                department_id=str(artifact.department_id),
                scan_id=str(artifact.scan_id),
                cloud_file_id=str(artifact.cloud_file_id),
                require_approved=True,
                approval_checksum=str(artifact.sha256),
            ) as stream:
                if cloud_file.provider_modified_at is not None:
                    current_file = await self.canvas_client.get_file(
                        str(cloud_file.provider_file_id)
                    )
                    if current_file.updated_at > cloud_file.provider_modified_at:
                        self.db.rollback()
                        return {
                            "success": False,
                            "stale": True,
                            "error": "Canvas file changed since the scan",
                            "error_code": "canvas_file_stale",
                        }
                upload = await self.canvas_client.upload_file_stream(
                    course_id=str(course_id),
                    stream=stream,
                    size_bytes=int(artifact.size_bytes),
                    mime_type=str(artifact.mime_type),
                    file_name=accessible_name,
                    correlation_id=correlation_id,
                )
        except ArtifactError:
            self.db.rollback()
            return {
                "success": False,
                "stale": False,
                "error": "Managed remediation artifact is unavailable",
                "error_code": "artifact_unavailable",
            }
        except Exception as exc:
            self.db.rollback()
            logger.warning(
                "Canvas artifact upload failed",
                extra={
                    "cloud_file_id": str(cloud_file.id),
                    "error_type": type(exc).__name__,
                },
            )
            return {"success": False, "stale": False, "error": "Canvas upload failed"}

        upload_outcome = getattr(upload, "outcome", None)
        if upload_outcome == "indeterminate":
            self.db.rollback()
            durable_correlation = str(getattr(upload, "correlation_id", correlation_id))
            ambiguous_result = getattr(upload, "provider_result", None) or {
                "phase": "upload",
                "outcome": "indeterminate",
            }
            self._persist_file_reconciliation(
                cloud_file=cloud_file,
                artifact=artifact,
                approved_by=approved_by,
                correlation_id=durable_correlation,
                provider_result=ambiguous_result,
                accessible_name=accessible_name,
            )
            return {
                "success": False,
                "stale": False,
                "error": "Canvas upload outcome requires reconciliation",
                "error_code": "writeback_reconciliation_required",
                "correlation_id": durable_correlation,
                "retry_safe": False,
            }
        if not getattr(upload, "success", False):
            self.db.rollback()
            return {"success": False, "stale": False, "error": "Canvas upload failed"}

        now = datetime.now(timezone.utc)
        provider_result = {
            "correlation_id": correlation_id,
            "canvas_file_id": str(upload.file_id),
            "file_name": accessible_name,
            "url": getattr(upload, "web_view_link", None),
        }
        writeback_log = ContentWritebackLog(
            id=str(uuid.uuid4()),
            cloud_file_id=cloud_file.id,
            original_body=f"canvas-file:{cloud_file.provider_file_id} {cloud_file.file_name}",
            remediated_body=f"canvas-file:{upload.file_id} {accessible_name}",
            approved_by=approved_by,
            approved_at=artifact.approved_at,
            written_back_at=now,
            canvas_revision=str(upload.file_id),
            artifact_id=artifact.id,
            artifact_checksum=artifact.sha256,
            correlation_id=correlation_id,
            reconciliation_status="committed",
            provider_result=getattr(upload, "provider_result", None),
        )
        self.db.add(writeback_log)
        cloud_file.remediated_file_id = str(upload.file_id)
        cloud_file.writeback_status = "written_back"
        cloud_file.writeback_at = now
        if cloud_file.remediated_compliance_score is not None:
            cloud_file.last_compliance_score = cloud_file.remediated_compliance_score
        self.artifact_service.mark_written(
            self.db, artifact_id=str(artifact.id), provider_result=provider_result
        )
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            self._persist_file_reconciliation(
                cloud_file=cloud_file,
                artifact=artifact,
                approved_by=approved_by,
                correlation_id=correlation_id,
                provider_result=provider_result,
                accessible_name=accessible_name,
            )
            logger.error(
                "Canvas writeback reconciliation required",
                extra={
                    "correlation_id": correlation_id,
                    "artifact_id": str(artifact.id),
                    "artifact_checksum": str(artifact.sha256),
                    "canvas_file_id": str(upload.file_id),
                    "retry_safe": False,
                },
            )
            return {
                "success": False,
                "stale": False,
                "error": "Canvas accepted the file but reconciliation is required",
                "error_code": "writeback_reconciliation_required",
                "correlation_id": correlation_id,
                "retry_safe": False,
            }
        return {
            "success": True,
            "stale": False,
            "canvas_file_id": str(upload.file_id),
            "file_name": accessible_name,
            "url": getattr(upload, "web_view_link", None),
            "artifact_id": str(artifact.id),
            "artifact_checksum": str(artifact.sha256),
        }

    # ------------------------------------------------------------------
    # 4. write_back_content — push remediated HTML to Canvas
    # ------------------------------------------------------------------

    async def write_back_content(
        self,
        cloud_file: CloudFile,
        approved_by: str,
    ) -> Dict[str, Any]:
        """
        Write remediated content back to Canvas after stale-check.

        Safety: Compares cloud_file.content_updated_at against the current
        Canvas updated_at to detect edits made between scan and write-back.
        If Canvas content has been modified, returns stale=True instead of
        overwriting.

        Args:
            cloud_file: CloudFile with remediated_body
            approved_by: Email/identifier of the approving user

        Returns:
            Dict with success, stale flag, and optional error
        """
        if not cloud_file.remediated_body:
            return {"success": False, "stale": False, "error": "No remediated body"}

        if cloud_file.writeback_status != "approved":
            return {
                "success": False,
                "stale": False,
                "error": f"Cannot write back: status is '{cloud_file.writeback_status}', must be 'approved'",
            }

        content_source = cloud_file.content_source

        # Fetch current Canvas state for stale check
        try:
            current_updated_at = await self._get_canvas_updated_at(cloud_file)
        except Exception as e:
            return {
                "success": False,
                "stale": False,
                "error": f"Failed to check Canvas state: {e}",
            }

        # Stale check — Canvas content was modified since our scan
        if (
            cloud_file.content_updated_at
            and current_updated_at
            and current_updated_at > cloud_file.content_updated_at
        ):
            logger.warning(
                "Stale content detected — Canvas modified since scan",
                extra={
                    "cloud_file_id": cloud_file.id,
                    "scanned_at": str(cloud_file.content_updated_at),
                    "canvas_updated_at": str(current_updated_at),
                },
            )
            return {
                "success": False,
                "stale": True,
                "error": (
                    "Content is stale — Canvas was modified since the scan. "
                    "Re-scan required before write-back."
                ),
            }

        # Create audit log before write-back
        writeback_log = ContentWritebackLog(
            id=str(uuid.uuid4()),
            cloud_file_id=cloud_file.id,
            original_body=cloud_file.content_body,
            remediated_body=cloud_file.remediated_body,
            approved_by=approved_by,
            approved_at=datetime.now(timezone.utc),
        )
        self.db.add(writeback_log)

        # Push to Canvas
        try:
            await self._update_canvas_content(
                cloud_file,
                cloud_file.remediated_body,
                message="Accessibility remediation by Aelira",
            )

            # Update state — content on Canvas is now the remediated version
            writeback_log.written_back_at = datetime.now(timezone.utc)
            cloud_file.writeback_status = "written_back"
            cloud_file.writeback_at = datetime.now(timezone.utc)
            cloud_file.content_body = cloud_file.remediated_body

            # Update compliance score to the remediated score
            if cloud_file.remediated_compliance_score is not None:
                cloud_file.last_compliance_score = (
                    cloud_file.remediated_compliance_score
                )

            cloud_file.needs_rescan = False
            self.db.commit()

            logger.info(
                "Content written back to Canvas",
                extra={
                    "cloud_file_id": cloud_file.id,
                    "content_source": content_source,
                    "approved_by": approved_by,
                },
            )

            return {"success": True, "stale": False}

        except Exception as e:
            self.db.rollback()
            logger.error(
                "Write-back failed: %s",
                e,
                extra={"cloud_file_id": cloud_file.id},
            )
            return {"success": False, "stale": False, "error": str(e)}

    # ------------------------------------------------------------------
    # 5. rollback_content — restore original from audit log
    # ------------------------------------------------------------------

    async def rollback_content(self, cloud_file: CloudFile) -> Dict[str, Any]:
        """
        Restore the original content body from the most recent
        ContentWritebackLog entry and push it back to Canvas.

        Args:
            cloud_file: CloudFile to roll back

        Returns:
            Dict with success and optional error
        """
        # Find the most recent writeback log
        writeback_log = (
            self.db.query(ContentWritebackLog)
            .filter(ContentWritebackLog.cloud_file_id == cloud_file.id)
            .order_by(ContentWritebackLog.created_at.desc())
            .first()
        )

        if not writeback_log:
            return {
                "success": False,
                "error": "No writeback log found — nothing to roll back",
            }

        original_body = writeback_log.original_body

        try:
            await self._update_canvas_content(cloud_file, original_body)

            # Update state
            writeback_log.rollback_status = "rolled_back"
            writeback_log.rolled_back_at = datetime.now(timezone.utc)

            cloud_file.content_body = original_body
            cloud_file.writeback_status = "rolled_back"
            cloud_file.needs_rescan = True

            self.db.commit()

            logger.info(
                "Content rolled back to original",
                extra={
                    "cloud_file_id": cloud_file.id,
                    "writeback_log_id": writeback_log.id,
                },
            )

            return {"success": True}

        except Exception as e:
            self.db.rollback()
            logger.error(
                "Rollback failed: %s",
                e,
                extra={"cloud_file_id": cloud_file.id},
            )
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _upsert_cloud_file(
        self,
        course_id: str,
        content_source: CanvasContentType,
        provider_file_id: str,
        file_name: str,
        content_body: str,
        content_slug: Optional[str],
        content_updated_at: datetime,
    ) -> CloudFile:
        """
        Find an existing CloudFile by (department, provider, provider_file_id,
        content_source) or create a new one.

        Returns the CloudFile (new or updated).
        """
        existing = (
            self.db.query(CloudFile)
            .filter(
                CloudFile.department_id == self.department_id,
                CloudFile.provider == "canvas",
                CloudFile.provider_file_id == provider_file_id,
                CloudFile.content_source == content_source.value,
                CloudFile.provider_parent_id == course_id,
            )
            .first()
        )

        if existing:
            # Update existing record
            existing.file_name = file_name
            existing.content_body = sanitize_for_postgres(content_body)
            existing.content_slug = content_slug
            existing.content_updated_at = content_updated_at
            # Store course name/code in provider_metadata
            metadata = existing.provider_metadata or {}
            if isinstance(metadata, dict):
                if self.course_name:
                    metadata["course_name"] = self.course_name
                if self.course_code:
                    metadata["course_code"] = self.course_code
                existing.provider_metadata = metadata
            # Only mark for rescan if content hasn't just been written back
            if existing.writeback_status != "written_back":
                existing.needs_rescan = True
            return existing

        # Build provider_metadata with course info
        metadata = {}
        if self.course_name:
            metadata["course_name"] = self.course_name
        if self.course_code:
            metadata["course_code"] = self.course_code

        # Create new record
        cloud_file = CloudFile(
            id=str(uuid.uuid4()),
            department_id=self.department_id,
            credential_id=self.credential_id,
            provider="canvas",
            provider_file_id=provider_file_id,
            provider_parent_id=course_id,
            file_name=file_name,
            file_type="html",
            mime_type="text/html",
            content_source=content_source.value,
            content_body=sanitize_for_postgres(content_body),
            content_slug=content_slug,
            content_updated_at=content_updated_at,
            needs_rescan=True,
            provider_metadata=metadata if metadata else None,
        )
        self.db.add(cloud_file)
        return cloud_file

    def _upsert_file_cloud_file(self, course_id: str, file_info: Any) -> CloudFile:
        """
        Find an existing CloudFile for this Canvas file (by department,
        provider, provider_file_id — deliberately NOT scoped by
        content_source) or create a new one.

        Matches the lookup canvas_scan_routes.py's single-file scan
        endpoint uses, so a file scanned individually before a course scan
        (or vice versa) converges on the same row instead of duplicating —
        content_source is only ever added/refreshed here, never used to
        gate the lookup, since older rows may predate this field.
        """
        existing = (
            self.db.query(CloudFile)
            .filter(
                CloudFile.department_id == self.department_id,
                CloudFile.provider == "canvas",
                CloudFile.provider_file_id == file_info.id,
            )
            .first()
        )

        # Short type code (pdf, docx, ...) for the file_type column — NOT
        # the full MIME type, which belongs in mime_type. Mirrors
        # canvas_scan_routes.py's _get_file_type().
        file_type = (
            file_info.filename.rsplit(".", 1)[-1].lower()
            if file_info.filename and "." in file_info.filename
            else "unknown"
        )

        if existing:
            existing.file_name = file_info.display_name or file_info.filename
            existing.file_type = file_type
            existing.mime_type = file_info.content_type
            existing.file_size_bytes = file_info.size
            existing.web_view_link = file_info.url
            existing.provider_parent_id = course_id
            existing.content_source = CanvasContentType.FILE.value
            existing.needs_rescan = True
            return existing

        cloud_file = CloudFile(
            id=str(uuid.uuid4()),
            department_id=self.department_id,
            credential_id=self.credential_id,
            provider="canvas",
            provider_file_id=file_info.id,
            provider_parent_id=course_id,
            file_name=file_info.display_name or file_info.filename,
            file_type=file_type,
            mime_type=file_info.content_type,
            file_size_bytes=file_info.size,
            web_view_link=file_info.url,
            content_source=CanvasContentType.FILE.value,
            needs_rescan=True,
        )
        self.db.add(cloud_file)
        return cloud_file

    async def _run_axe_scan(self, html: str) -> Dict[str, Any]:
        """
        Run axe-core on HTML content via Playwright.

        Uses page.set_content() to load the HTML into a browser page
        without needing a web server.

        Args:
            html: Full HTML document string

        Returns:
            axe-core results dict with violations, passes, etc.
        """
        return await run_deterministic_axe(html)

    async def _get_canvas_updated_at(self, cloud_file: CloudFile) -> Optional[datetime]:
        """
        Fetch the current updated_at from Canvas for the given content item.

        Args:
            cloud_file: CloudFile with content_source and identifiers

        Returns:
            Canvas updated_at datetime, or None if unable to fetch
        """
        content_source = cloud_file.content_source
        course_id = cloud_file.provider_parent_id

        if content_source == "page":
            item = await self.canvas_client.get_page(course_id, cloud_file.content_slug)
            return item.updated_at
        elif content_source == "assignment":
            item = await self.canvas_client.get_assignment(
                course_id, cloud_file.provider_file_id
            )
            return item.updated_at
        elif content_source == "announcement":
            item = await self.canvas_client.get_announcement(
                course_id, cloud_file.provider_file_id
            )
            return item.updated_at
        elif content_source == "quiz":
            item = await self.canvas_client.get_quiz(
                course_id, cloud_file.provider_file_id
            )
            return item.updated_at
        elif content_source == "discussion":
            item = await self.canvas_client.get_discussion(
                course_id, cloud_file.provider_file_id
            )
            return item.updated_at
        else:
            logger.warning("Unknown content_source: %s", content_source)
            return None

    async def _update_canvas_content(
        self,
        cloud_file: CloudFile,
        body: str,
        message: Optional[str] = None,
    ) -> None:
        """
        Call the appropriate Canvas update method for the content type.

        Content type -> Canvas update method mapping:
        - page       -> update_page(course_id, slug, body=body, message=message)
        - assignment -> update_assignment(course_id, id, description=body)
        - announcement -> update_announcement(course_id, id, message=body)
        - quiz       -> update_quiz(course_id, id, description=body)
        - discussion -> update_discussion(course_id, id, message=body)

        Args:
            cloud_file: CloudFile with content_source and identifiers
            body: HTML body content to write
            message: Optional revision message for wiki page history (pages only)
        """
        content_source = cloud_file.content_source
        course_id = cloud_file.provider_parent_id

        if content_source == "page":
            await self.canvas_client.update_page(
                course_id,
                cloud_file.content_slug,
                body=body,
                message=message,
            )
        elif content_source == "assignment":
            await self.canvas_client.update_assignment(
                course_id,
                cloud_file.provider_file_id,
                description=body,
            )
        elif content_source == "announcement":
            await self.canvas_client.update_announcement(
                course_id,
                cloud_file.provider_file_id,
                message=body,
            )
        elif content_source == "quiz":
            await self.canvas_client.update_quiz(
                course_id,
                cloud_file.provider_file_id,
                description=body,
            )
        elif content_source == "discussion":
            await self.canvas_client.update_discussion(
                course_id,
                cloud_file.provider_file_id,
                message=body,
            )
        else:
            raise ValueError(f"Unknown content_source: {content_source}")


__all__ = [
    "CanvasContentScanner",
    "_wrap_html_fragment",
    "_unwrap_html_fragment",
    "_sanitize_html",
]
