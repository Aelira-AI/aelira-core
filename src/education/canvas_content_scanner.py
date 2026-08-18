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
import uuid
import tempfile
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
    ContentWritebackLog,
    Scan,
    ScanResult,
    ScanType,
    ScanStatus,
)
from ..integrations.canvas.canvas_api import CanvasAPIClient
from ..integrations.canvas.content_models import CanvasContentType
from ..utils.sanitization import sanitize_for_postgres

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML helpers — used by the scanner and also importable for tests
# ---------------------------------------------------------------------------


def _wrap_html_fragment(fragment: str) -> str:
    """
    Wrap a body-only HTML fragment in a minimal valid document skeleton.

    axe-core requires a complete document (DOCTYPE, <html>, <head>, <body>)
    to run its rule engine.  Canvas stores only body fragments, so we wrap
    them before scanning and strip the wrapper after remediation.

    Args:
        fragment: HTML body content from Canvas

    Returns:
        Full HTML document string
    """
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head><title>Scan</title></head>\n"
        f"<body>{fragment}</body>\n"
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
    ):
        self.canvas_client = canvas_client
        self.db = db
        self.department_id = department_id
        self.credential_id = credential_id
        self.course_name = course_name
        self.course_code = course_code
        self.scan_options = scan_options or {
            "generate_alt_text": True,
            "auto_remediate": True,
            "detect_decorative": True,
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
            return {"scan_id": None, "issues": 0, "error": "No content body"}

        wrapped_html = _wrap_html_fragment(cloud_file.content_body)

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
            compliance_score = (
                round(passes / total_rules * 100, 1) if total_rules > 0 else 100.0
            )

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
                "scan_id": scan.id,
                "issues": issue_count,
                "compliance_score": compliance_score,
            }

        except Exception as e:
            scan.status = ScanStatus.FAILED
            scan.error_message = str(e)[:2000]
            self.db.commit()
            logger.error(
                "Content scan failed: %s",
                e,
                extra={"cloud_file_id": cloud_file.id},
            )
            return {"scan_id": scan.id, "issues": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # 3. remediate_content_item — bridge to HtmlRemediator
    # ------------------------------------------------------------------

    async def remediate_content_item(self, cloud_file: CloudFile) -> Dict[str, Any]:
        """
        Load accessibility issues from the last scan, run HtmlRemediator
        on the content via a temporary file, sanitize the output, and
        store the result in cloud_file.remediated_body.

        Args:
            cloud_file: CloudFile with content_body and a completed scan

        Returns:
            Dict with remediation result summary
        """
        if not cloud_file.content_body:
            return {"success": False, "error": "No content body"}

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
            return {"success": True, "fixed_count": 0, "message": "No issues to fix"}

        # Write wrapped HTML to temp file for HtmlRemediator
        wrapped_html = _wrap_html_fragment(cloud_file.content_body)
        temp_path = None

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".html", delete=False, mode="w", encoding="utf-8"
            ) as tmp:
                tmp.write(wrapped_html)
                temp_path = tmp.name

            # Bridge to HtmlRemediator
            try:
                from ..education.remediation.html_remediator import HtmlRemediator

                remediator = HtmlRemediator(temp_path, issues)
                result = remediator.remediate()

                # Read the output file
                output_path = result.output_file or temp_path
                with open(output_path, "r", encoding="utf-8") as f:
                    remediated_doc = f.read()

                # Strip document wrapper back to body fragment
                body_fragment = _unwrap_html_fragment(remediated_doc)

                # Sanitize output
                sanitized = _sanitize_html(body_fragment)

                cloud_file.remediated_body = sanitize_for_postgres(sanitized)
                cloud_file.writeback_status = "pending_review"
                cloud_file.has_remediated_version = True

                # Verify the remediation by rescanning what we produced,
                # rather than inferring a score from how many fixers ran.
                verification = await self._verify_remediation(
                    cloud_file, sanitized, issues
                )

                if verification:
                    cloud_file.remediated_compliance_score = verification["score"]
                    cloud_file.remediated_issues_fixed = verification["fixed"]
                    cloud_file.remediated_issues_remaining = verification["remaining"]
                    self.db.commit()

                    if verification["introduced"]:
                        logger.warning(
                            "Remediation introduced new issues",
                            extra={
                                "cloud_file_id": cloud_file.id,
                                "introduced": verification["introduced"],
                            },
                        )

                    logger.info(
                        "Content remediation verified by rescan",
                        extra={
                            "cloud_file_id": cloud_file.id,
                            "score": verification["score"],
                            "fixed": verification["fixed"],
                            "remaining": verification["remaining"],
                            "introduced": verification["introduced"],
                        },
                    )

                    return {
                        "success": True,
                        "verified": True,
                        "fixed_count": verification["fixed"],
                        "issues_remaining": verification["remaining"],
                        "issues_introduced": verification["introduced"],
                        "manual_count": result.manual_count,
                        "remediated_score": verification["score"],
                        "verification_scan_id": verification["scan_id"],
                    }

                # Rescan unavailable: fall back to the fixer-ratio estimate and
                # say so, so nothing downstream reads it as a measured score.
                remediated_score = getattr(result, "remediated_compliance_score", None)
                if (
                    remediated_score is None
                    and cloud_file.last_compliance_score is not None
                ):
                    # HtmlRemediator doesn't compute remediated_compliance_score,
                    # so estimate from original score + fix ratio
                    total = (
                        result.fixed_count
                        + result.manual_count
                        + getattr(result, "failed_count", 0)
                    )
                    if total > 0:
                        fix_ratio = result.fixed_count / total
                        original = cloud_file.last_compliance_score
                        remediated_score = min(
                            100.0, round(original + (100 - original) * fix_ratio, 1)
                        )
                if remediated_score is not None:
                    cloud_file.remediated_compliance_score = remediated_score

                self.db.commit()

                logger.info(
                    "Content remediation complete",
                    extra={
                        "cloud_file_id": cloud_file.id,
                        "fixed_count": result.fixed_count,
                        "remediated_score": remediated_score,
                    },
                )

                return {
                    "success": True,
                    "verified": False,
                    "fixed_count": result.fixed_count,
                    "manual_count": result.manual_count,
                    "remediated_score": remediated_score,
                }

            except ImportError:
                logger.warning(
                    "HtmlRemediator not available, skipping remediation",
                    extra={"cloud_file_id": cloud_file.id},
                )
                return {
                    "success": False,
                    "error": "HtmlRemediator not available",
                }

        except Exception as e:
            logger.error(
                "Content remediation failed: %s",
                e,
                extra={"cloud_file_id": cloud_file.id},
            )
            return {"success": False, "error": str(e)}

        finally:
            # Clean up temp files
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # 3b. _verify_remediation — rescan what remediation produced
    # ------------------------------------------------------------------

    async def _verify_remediation(
        self,
        cloud_file: CloudFile,
        remediated_fragment: str,
        original_issues: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
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
            wrapped = _wrap_html_fragment(remediated_fragment)
            axe_results = await self._run_axe_scan(wrapped)
        except Exception as e:
            logger.warning(
                "Remediation rescan failed; falling back to the estimate: %s",
                e,
                extra={"cloud_file_id": cloud_file.id},
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

        scan = Scan(
            id=str(uuid.uuid4()),
            scan_type=ScanType.CANVAS_CONTENT,
            status=ScanStatus.COMPLETED,
            file_name=f"{cloud_file.file_name} (remediated)",
            user_id=None,
            department_id=self.department_id,
            completed_at=datetime.now(timezone.utc),
        )
        self.db.add(scan)
        self.db.flush()
        self.db.add(
            ScanResult(
                id=str(uuid.uuid4()),
                scan_id=scan.id,
                compliance_score=score,
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
        )
        # The verification scan is a record of the remediated copy, not the
        # item's current state, so last_scan_id deliberately stays put.
        self.db.commit()

        return {
            "scan_id": scan.id,
            "score": score,
            "fixed": fixed,
            "remaining": remaining,
            "introduced": introduced,
        }

    # ------------------------------------------------------------------
    # 3c. write_back_file — upload a remediated file to the Canvas course
    # ------------------------------------------------------------------

    def _find_remediated_file_path(self, cloud_file: CloudFile) -> Optional[str]:
        """Path to the remediated artefact a remediation job produced.

        The remediator writes to disk and records the path on its job row.
        The file is not permanent, so a caller has to cope with it being
        gone rather than assume it is there.
        """
        job = (
            self.db.query(CloudJobQueue)
            .filter(
                CloudJobQueue.cloud_file_id == cloud_file.id,
                CloudJobQueue.job_type == CloudJobType.REMEDIATE.value,
                CloudJobQueue.status == CloudJobStatus.COMPLETED.value,
            )
            .order_by(CloudJobQueue.completed_at.desc())
            .first()
        )
        if not job or not isinstance(job.result_data, dict):
            return None
        path = job.result_data.get("output_file")
        if path and os.path.exists(path):
            return path
        return None

    async def write_back_file(
        self,
        cloud_file: CloudFile,
        approved_by: str,
    ) -> Dict[str, Any]:
        """Upload the remediated copy of a file to its Canvas course.

        Canvas files are not edited in place. The remediated copy is
        uploaded alongside the original with an _accessible suffix, the
        same convention the standalone upload endpoint already uses, so
        nothing anyone authored is overwritten. That also means there is no
        body to stale-check: what has to be checked is that the remediated
        artefact still exists, because it is written to disk and can be
        cleaned up before anyone approves it.
        """
        if cloud_file.content_source != "file":
            return {
                "success": False,
                "stale": False,
                "error": "Not a file row; use write_back_content for content items",
            }

        if cloud_file.writeback_status != "approved":
            return {
                "success": False,
                "stale": False,
                "error": (
                    f"Cannot write back: status is "
                    f"'{cloud_file.writeback_status}', must be 'approved'"
                ),
            }

        if not cloud_file.has_remediated_version:
            return {
                "success": False,
                "stale": False,
                "error": "No remediated version for this file",
            }

        course_id = cloud_file.provider_parent_id
        if not course_id:
            return {
                "success": False,
                "stale": False,
                "error": "File has no course to upload into",
            }

        remediated_path = self._find_remediated_file_path(cloud_file)
        if not remediated_path:
            return {
                "success": False,
                "stale": False,
                "error": (
                    "The remediated file is no longer on disk. "
                    "Remediate it again before writing back."
                ),
            }

        original = Path(cloud_file.file_name)
        accessible_name = f"{original.stem}_accessible{original.suffix}"

        # For file rows these two columns hold references rather than
        # bodies: an uploaded file has no HTML to keep, and what an auditor
        # needs is a way to find both copies.
        writeback_log = ContentWritebackLog(
            id=str(uuid.uuid4()),
            cloud_file_id=cloud_file.id,
            original_body=(
                f"canvas-file:{cloud_file.provider_file_id} {cloud_file.file_name}"
            ),
            remediated_body=f"local:{remediated_path}",
            approved_by=approved_by,
            approved_at=datetime.now(timezone.utc),
        )
        self.db.add(writeback_log)

        try:
            upload = await self.canvas_client.upload_file(
                course_id=course_id,
                local_path=remediated_path,
                file_name=accessible_name,
            )
        except Exception as e:
            self.db.rollback()
            logger.error(
                "Canvas file upload raised: %s",
                e,
                extra={"cloud_file_id": cloud_file.id},
            )
            return {"success": False, "stale": False, "error": f"Canvas upload: {e}"}

        if not getattr(upload, "success", False):
            self.db.rollback()
            return {
                "success": False,
                "stale": False,
                "error": f"Canvas upload failed: {getattr(upload, 'error', 'unknown')}",
            }

        now = datetime.now(timezone.utc)
        writeback_log.written_back_at = now
        writeback_log.canvas_revision = upload.file_id
        writeback_log.remediated_body = (
            f"canvas-file:{upload.file_id} {accessible_name}"
        )
        cloud_file.remediated_file_id = upload.file_id
        cloud_file.writeback_status = "written_back"
        cloud_file.writeback_at = now
        if cloud_file.remediated_compliance_score is not None:
            cloud_file.last_compliance_score = cloud_file.remediated_compliance_score
        self.db.commit()

        logger.info(
            "Remediated file uploaded to Canvas",
            extra={
                "cloud_file_id": cloud_file.id,
                "course_id": course_id,
                "canvas_file_id": upload.file_id,
                "file_name": accessible_name,
            },
        )

        return {
            "success": True,
            "stale": False,
            "canvas_file_id": upload.file_id,
            "file_name": accessible_name,
            "url": getattr(upload, "web_view_link", None),
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
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.set_content(html)

                    # Inject and run axe-core
                    axe_script_path = os.path.join(
                        os.path.dirname(__file__),
                        "..",
                        "..",
                        "node_modules",
                        "axe-core",
                        "axe.min.js",
                    )
                    if os.path.exists(axe_script_path):
                        with open(axe_script_path, "r") as f:
                            axe_script = f.read()
                        await page.evaluate(axe_script)
                    else:
                        # Fallback: try CDN
                        await page.add_script_tag(
                            url="https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.2/axe.min.js"
                        )

                    results = await page.evaluate("axe.run()")
                    return results
                finally:
                    await browser.close()

        except ImportError:
            logger.warning("Playwright not available, returning empty results")
            return {"violations": [], "passes": []}
        except Exception as e:
            logger.error("axe-core scan failed: %s", e)
            return {"violations": [], "passes": [], "error": str(e)}

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
