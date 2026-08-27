"""Web scanning and code scanning endpoints — website, batch, sitemap, static code analysis."""

import logging
import hashlib
import os
import time
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db.database import get_db_dependency
from ...db.models import APIKey, Scan, ScanType
from ...education.code_scanner import CodeScanner, CodeScanResult
from ...education.web_scanner import WebScanner
from ...middleware.quota import require_feature
from ...scanners.scan_mode import ScanMode
from ._shared import (
    MAX_SCANFIX_ISSUES,
    _stable_hash,
    get_api_key_or_mock,
    validate_uploaded_file,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== Pydantic Models ====================


class WebScanRequest(BaseModel):
    """Request model for web scanning"""

    url: str
    mode: ScanMode = ScanMode.QUICK  # New: Scan thoroughness level
    scan_images: bool = False
    scan_multimedia: bool = False
    scan_math: bool = False
    validate_alt_text: bool = False  # NEW: Validate existing alt text accuracy
    max_depth: int = 1
    max_pages: int = 10
    generate_code_fixes: bool = True
    capture_screenshots: bool = True


class BatchWebScanRequest(BaseModel):
    urls: List[str]  # List of URLs to scan
    mode: ScanMode = ScanMode.COMPREHENSIVE
    scan_images: bool = False
    scan_multimedia: bool = False
    scan_math: bool = False
    max_depth: int = 1  # Depth for each URL
    max_pages: int = 10  # Max pages per URL
    generate_code_fixes: bool = True
    capture_screenshots: bool = True


class SitemapScanRequest(BaseModel):
    sitemap_url: str  # URL to sitemap.xml
    mode: ScanMode = ScanMode.COMPREHENSIVE
    scan_images: bool = False
    scan_multimedia: bool = False
    scan_math: bool = False
    max_pages: int = 50  # Max URLs to scan from sitemap
    generate_code_fixes: bool = True
    capture_screenshots: bool = True
    priority_patterns: Optional[List[str]] = None  # Prioritize URLs matching patterns


# ==================== Web Scanning Endpoint ====================


@router.post("/web/scan", response_model=dict)
async def scan_website(
    request: WebScanRequest,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Scan a website for WCAG 2.2 accessibility compliance

    NON-BLOCKING: Returns scan_id immediately, processes in the durable worker
    Use GET /api/education/scans/{scan_id}/progress to check status

    REQUIRES API KEY IN PRODUCTION
    REQUIRES: website feature (tier-gated via TIER_QUOTAS; enabled on all core tiers)

    Features:
    - Playwright + axe-core for WCAG 2.2 compliance
    - Image scanning with AI alt text (if enabled)
    - Multimedia caption checking (if enabled)
    - LaTeX/MathML detection with AI descriptions (if enabled)
    - AI content analysis (readability, clarity)
    - Qwen Coder generated code fixes (if enabled)
    - Multi-page crawling (configurable depth)

    Args:
        url: Website URL to scan
        scan_images: Enable image alt text scanning
        scan_multimedia: Enable multimedia caption checking
        scan_math: Enable LaTeX/MathML scanning
        max_depth: Maximum crawl depth (1 = single page)
        max_pages: Maximum pages to scan
        generate_code_fixes: Generate AI code fixes for issues

    Returns:
        scan_id and status (processing happens in background)
    """
    logger.info(
        f"[ENDPOINT CALLED] scan_website function entry - request received: {request.url}"
    )
    _, user_id, department_id = api_key_info

    # Check feature access (tier-gated via TIER_QUOTAS)
    await require_feature(
        db, department_id, "website", "Website Accessibility Scanning"
    )

    # Extract parameters from request body
    url = request.url
    mode = request.mode
    scan_images = request.scan_images
    scan_multimedia = request.scan_multimedia
    scan_math = request.scan_math
    validate_alt_text = request.validate_alt_text
    max_depth = request.max_depth
    max_pages = request.max_pages
    generate_code_fixes = request.generate_code_fixes
    capture_screenshots = request.capture_screenshots

    logger.info(
        f"Starting web scan for: {url} (user={user_id}, mode={mode.value}, engines={mode.engines})"
    )

    # Validate URL (including SSRF protection)
    from ...utils.security import validate_url_not_private

    try:
        validate_url_not_private(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Create scan record with PROCESSING status
    from ...db.models import ScanStatus

    scan = Scan(
        scan_type=ScanType.WEBSITE,
        status=ScanStatus.PROCESSING,
        file_name=url,
        file_size_bytes=0,
        user_id=user_id,
        department_id=department_id,
        progress=0,
        progress_message="Scan queued for processing...",
    )
    db.add(scan)
    db.flush()
    scan_id = scan.id

    from ...jobs.local_scan_job import enqueue_local_scan_job

    enqueue_local_scan_job(
        db,
        scan=scan,
        scan_kind="local_web",
        options={
            "url": url,
            "mode": mode.value,
            "scan_images": scan_images,
            "scan_multimedia": scan_multimedia,
            "scan_math": scan_math,
            "validate_alt_text": validate_alt_text,
            "max_depth": max_depth,
            "max_pages": max_pages,
            "generate_code_fixes": generate_code_fixes,
            "capture_screenshots": capture_screenshots,
        },
    )
    db.commit()
    db.refresh(scan)

    logger.info(f"Created scan {scan_id} for {url}, queued durable job")

    # Return immediately with scan_id
    return {
        "scan_id": scan_id,
        "status": "processing",
        "message": "Scan started. Use GET /api/education/scans/{scan_id}/progress to check status.",
        "progress_url": f"/api/education/scans/{scan_id}/progress",
    }


# ==================== Batch Web Scanning ====================


@router.post("/web/batch-scan", response_model=dict)
async def batch_scan_websites(
    request: BatchWebScanRequest,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Scan multiple websites in one request

    NON-BLOCKING: Returns batch_scan_id immediately, processes in background
    Use GET /api/education/scans/{scan_id}/progress to check status

    REQUIRES API KEY IN PRODUCTION
    REQUIRES: website + bulk_api features (not available on free/plus tiers)

    Features:
    - Scan up to 50 URLs in one request
    - Each URL scanned with full web scanner capabilities
    - Aggregate results across all URLs
    - Track progress for entire batch

    Use Cases:
    - Scan all course module pages
    - Scan department website sections
    - Scan faculty profile pages
    - Compliance audit of entire site structure

    Args:
        urls: List of URLs to scan (max 50)
        mode: Scan mode (quick/comprehensive/deep)
        scan_images: Enable image scanning
        scan_multimedia: Enable multimedia scanning
        scan_math: Enable LaTeX/MathML scanning
        max_depth: Crawl depth per URL
        max_pages: Max pages per URL
        generate_code_fixes: Generate AI code fixes
        capture_screenshots: Capture element screenshots

    Returns:
        batch_scan_id and status
    """
    logger.info(f"[ENDPOINT CALLED] batch_scan_websites - {len(request.urls)} URLs")
    _, user_id, department_id = api_key_info

    # Check feature access - Batch scanning requires website + bulk_api
    await require_feature(db, department_id, "website", "Website Scanning")
    await require_feature(db, department_id, "bulk_api", "Batch Scanning")

    # Validate request
    if not request.urls:
        raise HTTPException(status_code=400, detail="URLs list cannot be empty")

    if len(request.urls) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 URLs allowed per batch")

    # Validate all URLs (format + SSRF protection)
    from urllib.parse import urlparse

    from ...utils.security import validate_url_not_private

    for url in request.urls:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(status_code=400, detail=f"Invalid URL format: {url}")
        if parsed.scheme not in ("http", "https"):
            raise HTTPException(
                status_code=400, detail=f"URL must use http or https: {url}"
            )
        try:
            validate_url_not_private(url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"URL not allowed: {url} — {e}")

    # Create batch scan record
    from ...db.models import ScanStatus

    batch_scan = Scan(
        scan_type=ScanType.WEBSITE,
        status=ScanStatus.PROCESSING,
        file_name=f"Batch scan ({len(request.urls)} URLs)",
        file_size_bytes=0,
        user_id=user_id,
        department_id=department_id,
        progress=0,
        progress_message=f"Batch scan queued ({len(request.urls)} URLs)...",
    )
    db.add(batch_scan)
    db.flush()
    batch_scan_id = batch_scan.id

    from ...jobs.local_scan_job import enqueue_local_scan_job

    enqueue_local_scan_job(
        db,
        scan=batch_scan,
        scan_kind="local_web_batch",
        options={
            "urls": list(request.urls),
            "mode": request.mode.value,
            "scan_images": request.scan_images,
            "scan_multimedia": request.scan_multimedia,
            "scan_math": request.scan_math,
            "max_depth": request.max_depth,
            "max_pages": request.max_pages,
            "generate_code_fixes": request.generate_code_fixes,
            "capture_screenshots": request.capture_screenshots,
        },
    )
    db.commit()
    db.refresh(batch_scan)

    logger.info(f"Created batch scan {batch_scan_id}, queued durable job")

    return {
        "batch_scan_id": batch_scan_id,
        "status": "processing",
        "total_urls": len(request.urls),
        "message": f"Batch scan started for {len(request.urls)} URLs. Use GET /api/education/scans/{{scan_id}}/progress to check status.",
        "progress_url": f"/api/education/scans/{batch_scan_id}/progress",
    }


# ==================== Sitemap-Based Scanning ====================


@router.post("/web/scan-sitemap", response_model=dict)
async def scan_from_sitemap(
    request: SitemapScanRequest,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Scan website using sitemap.xml for URL discovery

    NON-BLOCKING: Returns scan_id immediately, processes in background
    Use GET /api/education/scans/{scan_id}/progress to check status

    REQUIRES API KEY IN PRODUCTION
    REQUIRES: website + bulk_api features (not available on free/plus tiers)

    Features:
    - Parse sitemap.xml to discover URLs
    - Respect priority patterns (scan important pages first)
    - Efficient large-scale site auditing
    - Track progress across all discovered URLs

    Use Cases:
    - Full department website audit
    - Pre-launch compliance check
    - Periodic compliance monitoring
    - Generate accessibility report for entire site

    Args:
        sitemap_url: URL to sitemap.xml (e.g., https://example.edu/sitemap.xml)
        mode: Scan mode (quick/comprehensive/deep)
        scan_images: Enable image scanning
        scan_multimedia: Enable multimedia scanning
        scan_math: Enable LaTeX/MathML scanning
        max_pages: Maximum URLs to scan from sitemap
        generate_code_fixes: Generate AI code fixes
        capture_screenshots: Capture element screenshots
        priority_patterns: URL patterns to prioritize (e.g., ["/courses/", "/faculty/"])

    Returns:
        scan_id and status
    """
    logger.info(f"[ENDPOINT CALLED] scan_from_sitemap - sitemap: {request.sitemap_url}")
    _, user_id, department_id = api_key_info

    # Check feature access - Sitemap scanning requires website + bulk_api
    await require_feature(db, department_id, "website", "Website Scanning")
    await require_feature(db, department_id, "bulk_api", "Sitemap Scanning")

    # Validate sitemap URL (including SSRF protection)
    from ...utils.security import validate_url_not_private

    try:
        validate_url_not_private(request.sitemap_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Create sitemap scan record
    from ...db.models import ScanStatus

    sitemap_scan = Scan(
        scan_type=ScanType.WEBSITE,
        status=ScanStatus.PROCESSING,
        file_name=f"Sitemap scan: {request.sitemap_url}",
        file_size_bytes=0,
        user_id=user_id,
        department_id=department_id,
        progress=0,
        progress_message="Fetching and parsing sitemap...",
    )
    db.add(sitemap_scan)
    db.flush()
    sitemap_scan_id = sitemap_scan.id

    from ...jobs.local_scan_job import enqueue_local_scan_job

    enqueue_local_scan_job(
        db,
        scan=sitemap_scan,
        scan_kind="local_web_sitemap",
        options={
            "sitemap_url": request.sitemap_url,
            "mode": request.mode.value,
            "scan_images": request.scan_images,
            "scan_multimedia": request.scan_multimedia,
            "scan_math": request.scan_math,
            "max_pages": request.max_pages,
            "generate_code_fixes": request.generate_code_fixes,
            "capture_screenshots": request.capture_screenshots,
            "priority_patterns": list(request.priority_patterns or []),
        },
    )
    db.commit()
    db.refresh(sitemap_scan)

    logger.info(f"Created sitemap scan {sitemap_scan_id}, queued durable job")

    return {
        "scan_id": sitemap_scan_id,
        "status": "processing",
        "sitemap_url": request.sitemap_url,
        "message": "Sitemap scan started. Parsing sitemap and queuing URLs. Use GET /api/education/scans/{scan_id}/progress to check status.",
        "progress_url": f"/api/education/scans/{sitemap_scan_id}/progress",
    }


# ==================== Background Jobs ====================


def process_web_scan_background(
    scan_id: str,
    url: str,
    mode: str,  # scan mode (quick/comprehensive/deep)
    scan_images: bool,
    scan_multimedia: bool,
    scan_math: bool,
    validate_alt_text: bool,
    max_depth: int,
    max_pages: int,
    generate_code_fixes: bool,
    capture_screenshots: bool,
):
    """
    Durable-worker function for a web scan, using the sync Playwright API.

    Args:
        scan_id: Unique scan identifier
        url: Website URL to scan
        mode: Scan mode (quick/comprehensive/deep) - determines which engines run
        scan_images: Enable image alt text scanning
        scan_multimedia: Enable multimedia caption checking
        scan_math: Enable LaTeX/MathML scanning
        validate_alt_text: Validate existing alt text accuracy using AI vision
        max_depth: Maximum crawl depth
        max_pages: Maximum pages to scan
        generate_code_fixes: Generate AI code fixes
        capture_screenshots: Capture screenshots of issues
    """
    from ...db.database import SessionLocal
    from ...db.models import ScanResult, ScanFix, ScanStatus
    from datetime import datetime

    # Create new DB session for this thread
    db = SessionLocal()

    try:
        logger.info(f"[BACKGROUND] Starting scan {scan_id} for {url}")

        # Get scan record
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error(f"[BACKGROUND] Scan {scan_id} not found in database")
            return

        # Update progress
        scan.progress = 5
        scan.progress_message = "Initializing web scanner..."
        db.commit()

        # Define progress callback
        def progress_callback(current: int, total: int, message: str):
            """Update scan progress in database"""
            try:
                thread_db = SessionLocal()
                thread_scan = thread_db.query(Scan).filter(Scan.id == scan_id).first()
                if thread_scan:
                    # Calculate raw progress percentage
                    if total is not None and total > 0 and current is not None:
                        raw_progress = (current / total) * 100
                        # Cap at 95% to show work is still in progress (never show 100% until actually done)
                        thread_scan.progress = min(int(raw_progress), 95)
                    else:
                        thread_scan.progress = 0

                    thread_scan.progress_message = message
                    thread_db.commit()
                thread_db.close()
            except Exception as e:
                logger.error(f"[BACKGROUND] Error updating progress: {e}")

        # Initialize scanner
        scanner = WebScanner(
            scan_images=scan_images,
            scan_multimedia=scan_multimedia,
            scan_math=scan_math,
            validate_alt_text=validate_alt_text,
            max_depth=max_depth,
            max_pages=max_pages,
            use_ai_analysis=generate_code_fixes,
            capture_screenshots=capture_screenshots,
            progress_callback=progress_callback,
        )

        # Perform axe-core scan (always runs)
        start_time = time.time()
        axe_start = time.time()
        logger.info(f"[BACKGROUND] Starting scan with mode={mode}")
        logger.info(f"[BACKGROUND] Calling scanner.scan_website (axe-core) for {url}")
        result = scanner.scan_website(url)
        axe_duration_ms = int((time.time() - axe_start) * 1000)
        logger.info(
            f"[BACKGROUND] Axe-core scan completed for {url}, pages: {result.pages_scanned}, duration: {axe_duration_ms}ms"
        )

        # Initialize Pa11y tracking variables
        pa11y_result = None
        pa11y_duration_ms = None
        merged_results = None
        engines_used = ["axe-core"]

        # Run Pa11y for comprehensive/deep modes
        # DISABLED: Pa11y fails to launch browser when running as root without --no-sandbox
        # Playwright + axe-core provides comprehensive results already
        if False and mode in ("comprehensive", "deep"):
            logger.info(f"[BACKGROUND] Running Pa11y scan (mode={mode})")
            try:
                from ...scanners.pa11y_scanner import Pa11yScanner
                from ...scanners.result_merger import ResultMerger
                import asyncio

                # Create event loop for async Pa11y scanner
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                pa11y_scanner = Pa11yScanner(timeout=120)

                # Scan first page with Pa11y
                try:
                    pa11y_start = time.time()
                    pa11y_result = loop.run_until_complete(
                        pa11y_scanner.scan(url, runner="axe")
                    )
                    pa11y_duration_ms = int((time.time() - pa11y_start) * 1000)
                    logger.info(
                        f"[BACKGROUND] Pa11y scan completed: {pa11y_result.total_issues} issues, duration: {pa11y_duration_ms}ms"
                    )

                    # Convert result to axe-core format for merging
                    axe_results_json = {
                        "url": url,
                        "violations": [
                            {
                                "id": issue.impact
                                or "unknown",  # Use first page's issues
                                "impact": issue.impact or "serious",
                                "description": (
                                    result.pages[0].issues[0].description
                                    if result.pages
                                    else ""
                                ),
                                "help": (
                                    result.pages[0].issues[0].description
                                    if result.pages
                                    else ""
                                ),
                                "helpUrl": "",
                                "nodes": [
                                    {
                                        "html": (
                                            page_result.issues[i].element
                                            if i < len(page_result.issues)
                                            else ""
                                        ),
                                        "target": [
                                            (
                                                page_result.issues[i].selector
                                                if i < len(page_result.issues)
                                                else ""
                                            )
                                        ],
                                        "failureSummary": (
                                            page_result.issues[i].description
                                            if i < len(page_result.issues)
                                            else ""
                                        ),
                                    }
                                    for i, page_result in enumerate(
                                        result.pages[:1]
                                    )  # First page only
                                ],
                                "tags": [],
                            }
                            for issue in (
                                result.pages[0].issues if result.pages else []
                            )
                        ],
                    }

                    # Merge results with deduplication
                    merged_results = ResultMerger.merge_axe_and_pa11y_results(
                        axe_results_json, pa11y_result.to_dict()
                    )
                    logger.info(
                        f"[BACKGROUND] Merged results: {merged_results['total_issues']} unique issues "
                        + f"(axe: {merged_results['engine_counts']['axe-core']}, "
                        + f"pa11y: {merged_results['engine_counts']['pa11y']}, "
                        + f"both: {merged_results['engine_counts']['both']})"
                    )

                    engines_used.append("pa11y")

                except Exception as pa11y_error:
                    logger.error(f"[BACKGROUND] Pa11y scan failed: {pa11y_error}")
                    pa11y_result = None
                    pa11y_duration_ms = None
                finally:
                    loop.close()

            except ImportError as e:
                logger.warning(
                    f"[BACKGROUND] Pa11y scanner not available (mode={mode}): {e}"
                )
        else:
            logger.info(
                f"[BACKGROUND] Skipping Pa11y scan (mode={mode}, only axe-core)"
            )

        # Update scan metadata (keep progress at 95% while storing results)
        scan.progress = 95
        scan.progress_message = "Storing scan results..."
        scan.pages = result.pages_scanned
        scan.processing_time_ms = int((time.time() - start_time) * 1000)
        scan.completed_at = datetime.utcnow()
        db.commit()

        # Calculate issue counts
        summary = result.summary
        critical = summary.get("critical", 0)
        high = summary.get("serious", 0)
        medium = summary.get("moderate", 0)
        low = summary.get("minor", 0)

        # Collect all issues from all pages (limit to first 100 for database size)
        all_issues = []
        for page_result in result.pages[:10]:  # First 10 pages to avoid huge JSON
            for issue in page_result.issues[:20]:  # First 20 issues per page
                all_issues.append(
                    {
                        "page_url": issue.page_url
                        or page_result.url,  # Use issue-specific page_url if available
                        "page_title": page_result.title,
                        "impact": issue.impact,  # critical, serious, moderate, minor
                        "criterion": issue.criterion,
                        "description": issue.description,
                        "help_url": issue.help_url,
                        "element": (
                            issue.element[:200] if issue.element else ""
                        ),  # Truncate long HTML
                        "fix": issue.fix,
                        "generated_code_fix": (
                            issue.generated_code_fix[:500]
                            if issue.generated_code_fix
                            else None
                        ),
                        # Location information for precise element identification
                        "selector": issue.selector,  # CSS selector
                        "xpath": issue.xpath,  # XPath (if available)
                        "screenshot": issue.screenshot,  # Base64-encoded screenshot (if capture_screenshots enabled)
                    }
                )

        logger.info(
            f"[BACKGROUND] Collected {len(all_issues)} issues from {result.pages_scanned} pages"
        )

        # Calculate coverage percentage based on mode

        coverage_pct = {"quick": 90.0, "comprehensive": 95.0, "deep": 98.0}.get(
            mode, 90.0
        )

        # Collect image scan results from all pages
        image_results = []
        for page_result in result.pages:
            for image_scan in page_result.image_scans:
                image_results.append(
                    {
                        "page_url": page_result.url,
                        "image_url": image_scan.url,
                        "has_alt_text": image_scan.has_alt_text,
                        "alt_text_quality": image_scan.alt_text_quality,
                        "suggested_alt_text": image_scan.suggested_alt_text,
                    }
                )

        logger.info(f"[BACKGROUND] Collected {len(image_results)} image scan results")

        # Store scan result with multi-engine data
        scan_result = ScanResult(
            scan_id=scan.id,
            compliance_score=result.overall_compliance_score,
            wcag_level="AA",
            critical_issues=critical,
            high_issues=high,
            medium_issues=medium,
            low_issues=low,
            issues={"details": all_issues},  # Legacy field
            structure={
                "pages_scanned": result.pages_scanned,
                "root_url": result.root_url,
                "scan_time": result.total_scan_time,
            },
            # Multi-engine fields
            scan_mode=mode,
            axe_results={"details": all_issues},  # Store axe-core results separately
            pa11y_results=pa11y_result.to_dict() if pa11y_result else None,
            ai_vision_results=(
                {"images": image_results} if image_results else None
            ),  # Add image scan results
            merged_results=merged_results,
            engines_used=engines_used,
            axe_issues=critical + high + medium + low,
            pa11y_issues=pa11y_result.total_issues if pa11y_result else None,
            issues_found_by_both=(
                merged_results["engine_counts"]["both"] if merged_results else None
            ),
            unique_issues=(
                merged_results["total_issues"]
                if merged_results
                else (critical + high + medium + low)
            ),
            estimated_coverage_pct=coverage_pct,
            axe_duration_ms=axe_duration_ms,
            pa11y_duration_ms=pa11y_duration_ms,
        )
        db.add(scan_result)
        db.commit()

        # ---- Persist ScanFix records for the review queue ----
        import uuid as _uuid
        from ...education.remediation.category_mapper import (
            wcag_criterion_to_category,
            impact_to_severity,
            impact_to_confidence,
        )

        total_web_issues = len(all_issues)
        if total_web_issues > MAX_SCANFIX_ISSUES:
            logger.warning(
                f"Web scan {scan_id}: {total_web_issues} issues found, "
                f"capping at {MAX_SCANFIX_ISSUES} for review queue"
            )

        for issue_dict in all_issues[:MAX_SCANFIX_ISSUES]:
            criterion = issue_dict.get("criterion", "")
            impact = issue_dict.get("impact", "moderate")
            code_fix = issue_dict.get("generated_code_fix")
            human_fix = issue_dict.get("fix")
            selector = issue_dict.get("selector", "")
            page_url = issue_dict.get("page_url", "")

            db.add(
                ScanFix(
                    id=str(_uuid.uuid4()),
                    scan_id=scan.id,
                    issue_id=f"web-{criterion}-{_stable_hash(issue_dict.get('description', ''))}",
                    category=wcag_criterion_to_category(criterion),
                    severity=impact_to_severity(impact),
                    description=issue_dict.get("description", ""),
                    location=f"{page_url} | {selector}" if selector else page_url,
                    original_content=issue_dict.get("element", ""),
                    fixed_content=code_fix or human_fix or "",
                    fix_method="ai" if code_fix else "heuristic",
                    model_used="gemini" if code_fix else None,
                    confidence=impact_to_confidence(impact),
                    needs_review=True,
                    review_status="pending",
                    wcag_criteria=criterion,
                    page_number=None,
                )
            )

        db.commit()
        persisted_web = min(total_web_issues, MAX_SCANFIX_ISSUES)
        logger.info(
            f"Web scan {scan_id}: persisted {persisted_web}/{total_web_issues} ScanFix records"
        )

        # NOW set to 100% complete (after all database operations are done)
        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Scan complete!"
        db.commit()

        logger.info(f"[BACKGROUND] Successfully stored results for scan {scan_id}")

    except Exception as e:
        logger.error(
            f"[BACKGROUND] Error processing scan {scan_id}: {str(e)}", exc_info=True
        )

        # Update scan to failed
        try:
            scan = db.query(Scan).filter(Scan.id == scan_id).first()
            if scan:
                scan.status = ScanStatus.FAILED
                scan.error_message = str(e)
                scan.progress = 0
                scan.progress_message = f"Scan failed: {str(e)}"
                db.commit()
        except Exception as db_error:
            logger.error(f"[BACKGROUND] Failed to update scan status: {db_error}")

    finally:
        db.close()


def process_batch_web_scan_background(
    batch_scan_id: str,
    urls: List[str],
    mode: str,
    scan_images: bool,
    scan_multimedia: bool,
    scan_math: bool,
    max_depth: int,
    max_pages: int,
    generate_code_fixes: bool,
    capture_screenshots: bool,
):
    """Background task to process batch web scan"""
    from ...db.database import SessionLocal
    from ...db.models import Scan, ScanStatus, ScanResult

    db = SessionLocal()

    def progress_callback(current, total, message):
        """Update progress in database"""
        try:
            scan = db.query(Scan).filter(Scan.id == batch_scan_id).first()
            if scan:
                overall_progress = int((current / total) * 100)
                scan.progress = overall_progress
                scan.progress_message = message
                db.commit()
        except Exception as e:
            logger.warning(f"Failed to update progress: {e}")

    try:
        logger.info(
            f"[BATCH BACKGROUND] Starting batch scan {batch_scan_id} for {len(urls)} URLs"
        )

        # Aggregate results across all URLs
        all_pages = []
        total_issues = {
            "critical": 0,
            "serious": 0,
            "moderate": 0,
            "minor": 0,
            "total": 0,
        }
        total_scan_time = 0
        total_compliance_scores = []

        # Track progress across all URLs
        total_urls = len(urls)

        for idx, url in enumerate(urls, 1):
            try:
                logger.info(f"[BATCH] Scanning URL {idx}/{total_urls}: {url}")
                progress_callback(
                    idx, total_urls, f"Scanning URL {idx}/{total_urls}: {url[:50]}..."
                )

                # Create scanner for this URL
                scanner = WebScanner(
                    scan_images=scan_images,
                    scan_multimedia=scan_multimedia,
                    scan_math=scan_math,
                    max_depth=max_depth,
                    max_pages=max_pages,
                    use_ai_analysis=generate_code_fixes,
                    capture_screenshots=capture_screenshots,
                )

                # Scan the URL
                result = scanner.scan_website(url)

                # Aggregate results
                all_pages.extend(result.pages)
                total_scan_time += result.total_scan_time
                total_compliance_scores.append(result.overall_compliance_score)

                # Aggregate issue counts
                for severity, count in result.summary.items():
                    total_issues[severity] = total_issues.get(severity, 0) + count

            except Exception as e:
                logger.error(f"[BATCH] Error scanning {url}: {e}", exc_info=True)
                # Continue with next URL even if one fails

        # Calculate overall compliance score (average)
        overall_compliance_score = (
            sum(total_compliance_scores) / len(total_compliance_scores)
            if total_compliance_scores
            else 0
        )

        # Group issues across ALL pages

        scanner_temp = WebScanner()  # Temp scanner for helper methods
        grouped_issues = scanner_temp._group_issues_across_pages(all_pages)

        # Create aggregated result
        batch_result = {
            "batch_scan_id": batch_scan_id,
            "total_urls_scanned": len(urls),
            "total_pages_scanned": len(all_pages),
            "total_scan_time": total_scan_time,
            "overall_compliance_score": overall_compliance_score,
            "issue_summary": total_issues,
            "grouped_issues": grouped_issues,
            "urls_scanned": urls,
        }

        # Store result in database
        scan_result = ScanResult(
            scan_id=batch_scan_id,
            result_data=batch_result,
            compliance_score=overall_compliance_score,
        )
        db.add(scan_result)

        # Update scan status to completed
        scan = db.query(Scan).filter(Scan.id == batch_scan_id).first()
        if scan:
            scan.status = ScanStatus.COMPLETED
            scan.progress = 100
            scan.progress_message = (
                f"Batch scan complete ({len(urls)} URLs, {len(all_pages)} pages)"
            )
            scan.compliance_score = overall_compliance_score
            db.commit()

        logger.info(f"[BATCH BACKGROUND] Completed batch scan {batch_scan_id}")

    except Exception as e:
        logger.error(
            f"[BATCH BACKGROUND] Error processing batch scan {batch_scan_id}: {str(e)}",
            exc_info=True,
        )

        # Update scan to failed
        try:
            scan = db.query(Scan).filter(Scan.id == batch_scan_id).first()
            if scan:
                scan.status = ScanStatus.FAILED
                scan.error_message = str(e)
                scan.progress = 0
                scan.progress_message = f"Batch scan failed: {str(e)}"
                db.commit()
        except Exception as db_error:
            logger.error(f"[BATCH BACKGROUND] Failed to update scan status: {db_error}")

    finally:
        db.close()


def process_sitemap_scan_background(
    sitemap_scan_id: str,
    sitemap_url: str,
    mode: str,
    scan_images: bool,
    scan_multimedia: bool,
    scan_math: bool,
    max_pages: int,
    generate_code_fixes: bool,
    capture_screenshots: bool,
    priority_patterns: List[str],
):
    """Background task to process sitemap-based web scan"""
    from ...db.database import SessionLocal
    from ...db.models import Scan, ScanStatus, ScanResult
    import defusedxml.ElementTree as ET  # XXE-safe: no entity expansion/external DTDs

    db = SessionLocal()

    def progress_callback(current, total, message):
        """Update progress in database"""
        try:
            scan = db.query(Scan).filter(Scan.id == sitemap_scan_id).first()
            if scan:
                overall_progress = int((current / total) * 100)
                scan.progress = overall_progress
                scan.progress_message = message
                db.commit()
        except Exception as e:
            logger.warning(f"Failed to update progress: {e}")

    try:
        logger.info(
            f"[SITEMAP BACKGROUND] Starting sitemap scan {sitemap_scan_id} for {sitemap_url}"
        )

        # Validate sitemap URL against SSRF before fetching
        from src.utils.security import safe_requests_get, validate_url_not_private

        # Fetch sitemap — validates the URL and every redirect hop against SSRF
        progress_callback(0, 100, "Fetching sitemap...")
        response = safe_requests_get(sitemap_url, timeout=30)
        response.raise_for_status()

        # Parse sitemap XML
        progress_callback(10, 100, "Parsing sitemap...")
        root = ET.fromstring(response.content)

        # Extract URLs from sitemap
        # Handle both sitemap.xml formats (with/without namespace)
        urls = []
        namespaces = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        # Try with namespace first
        for url_elem in root.findall(".//sm:url/sm:loc", namespaces):
            urls.append(url_elem.text)

        # If no URLs found, try without namespace
        if not urls:
            for url_elem in root.findall(".//url/loc"):
                urls.append(url_elem.text)

        if not urls:
            raise Exception("No URLs found in sitemap")

        logger.info(f"[SITEMAP] Found {len(urls)} URLs in sitemap")

        # Apply priority patterns if configured
        if priority_patterns:

            def get_url_priority(url):
                for i, pattern in enumerate(priority_patterns):
                    if pattern in url:
                        return len(priority_patterns) - i
                return 0

            urls.sort(key=get_url_priority, reverse=True)
            logger.info(
                f"[SITEMAP] Sorted URLs by priority patterns: {priority_patterns}"
            )

        # Limit to max_pages
        if len(urls) > max_pages:
            urls = urls[:max_pages]
            logger.info(f"[SITEMAP] Limited to {max_pages} URLs")

        progress_callback(20, 100, f"Found {len(urls)} URLs, starting scans...")

        # Aggregate results across all URLs
        all_pages = []
        total_issues = {
            "critical": 0,
            "serious": 0,
            "moderate": 0,
            "minor": 0,
            "total": 0,
        }
        total_scan_time = 0
        total_compliance_scores = []

        # Scan each URL from sitemap
        total_urls = len(urls)
        base_progress = 20  # Already at 20% after sitemap parsing
        scan_progress_range = 80  # 80% for scanning

        for idx, url in enumerate(urls, 1):
            try:
                # Validate each URL from sitemap against SSRF
                try:
                    validate_url_not_private(url)
                except ValueError:
                    logger.warning(
                        f"[SITEMAP] Skipping private/reserved URL: {url[:100]}"
                    )
                    continue

                current_progress = base_progress + int(
                    (idx / total_urls) * scan_progress_range
                )
                logger.info(f"[SITEMAP] Scanning URL {idx}/{total_urls}: {url}")
                progress_callback(
                    current_progress,
                    100,
                    f"Scanning URL {idx}/{total_urls}: {url[:50]}...",
                )

                # Create scanner (single page scan per sitemap URL)
                scanner = WebScanner(
                    scan_images=scan_images,
                    scan_multimedia=scan_multimedia,
                    scan_math=scan_math,
                    max_depth=1,  # Don't crawl links from sitemap URLs
                    max_pages=1,  # One page per URL
                    use_ai_analysis=generate_code_fixes,
                    capture_screenshots=capture_screenshots,
                )

                # Scan the URL
                result = scanner.scan_website(url)

                # Aggregate results
                all_pages.extend(result.pages)
                total_scan_time += result.total_scan_time
                total_compliance_scores.append(result.overall_compliance_score)

                # Aggregate issue counts
                for severity, count in result.summary.items():
                    total_issues[severity] = total_issues.get(severity, 0) + count

            except Exception as e:
                logger.error(f"[SITEMAP] Error scanning {url}: {e}", exc_info=True)
                # Continue with next URL even if one fails

        # Calculate overall compliance score (average)
        overall_compliance_score = (
            sum(total_compliance_scores) / len(total_compliance_scores)
            if total_compliance_scores
            else 0
        )

        # Group issues across ALL pages

        scanner_temp = WebScanner()  # Temp scanner for helper methods
        grouped_issues = scanner_temp._group_issues_across_pages(all_pages)

        # Create aggregated result
        sitemap_result = {
            "sitemap_scan_id": sitemap_scan_id,
            "sitemap_url": sitemap_url,
            "total_urls_discovered": len(urls),
            "total_pages_scanned": len(all_pages),
            "total_scan_time": total_scan_time,
            "overall_compliance_score": overall_compliance_score,
            "issue_summary": total_issues,
            "grouped_issues": grouped_issues,
            "urls_scanned": urls,
        }

        # Store result in database
        scan_result = ScanResult(
            scan_id=sitemap_scan_id,
            result_data=sitemap_result,
            compliance_score=overall_compliance_score,
        )
        db.add(scan_result)

        # Update scan status to completed
        scan = db.query(Scan).filter(Scan.id == sitemap_scan_id).first()
        if scan:
            scan.status = ScanStatus.COMPLETED
            scan.progress = 100
            scan.progress_message = (
                f"Sitemap scan complete ({len(urls)} URLs, {len(all_pages)} pages)"
            )
            scan.compliance_score = overall_compliance_score
            db.commit()

        logger.info(f"[SITEMAP BACKGROUND] Completed sitemap scan {sitemap_scan_id}")

    except Exception as e:
        logger.error(
            f"[SITEMAP BACKGROUND] Error processing sitemap scan {sitemap_scan_id}: {str(e)}",
            exc_info=True,
        )

        # Update scan to failed
        try:
            scan = db.query(Scan).filter(Scan.id == sitemap_scan_id).first()
            if scan:
                scan.status = ScanStatus.FAILED
                scan.error_message = str(e)
                scan.progress = 0
                scan.progress_message = f"Sitemap scan failed: {str(e)}"
                db.commit()
        except Exception as db_error:
            logger.error(
                f"[SITEMAP BACKGROUND] Failed to update scan status: {db_error}"
            )

    finally:
        db.close()


# ==================== Code Scanner Endpoints ====================


def process_code_background(
    file_path: str,
    file_content: bytes,
    filename: str,
    scan_id: str,
    scan_images: bool,
    generate_fixes: bool,
    validate_alt_text: bool,
    user_id: str,
    department_id: str,
):
    """Background task to process code file asynchronously - TRUE REAL-TIME PROGRESS!"""
    from ...db.database import SessionLocal
    from ...db.models import Scan, ScanStatus, ScanResult, ScanFix
    from sqlalchemy.sql import func
    import hashlib

    db = SessionLocal()

    try:
        start_time = time.time()
        logger.info(f"[BACKGROUND] Processing Code: {filename} (scan_id={scan_id})")

        # Get scan record
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error(f"[BACKGROUND] Scan {scan_id} not found!")
            return

        # Define progress callback
        def update_progress(current: int, total: int, message: str):
            """Update progress using a separate DB session to avoid blocking"""
            progress_db = None
            try:
                if total is not None and total > 0 and current is not None:
                    progress_pct = 10 + int((current / total) * 80)
                else:
                    progress_pct = 10

                progress_db = SessionLocal()
                progress_scan = (
                    progress_db.query(Scan).filter(Scan.id == scan_id).first()
                )
                if progress_scan:
                    progress_scan.progress = min(progress_pct, 90)
                    progress_scan.progress_message = message
                    progress_db.commit()
                    logger.info(
                        f"[BACKGROUND] Code Progress: {progress_pct}% - {message}"
                    )
            except Exception as e:
                logger.error(f"[BACKGROUND] Failed to update Code progress: {e}")
            finally:
                if progress_db:
                    progress_db.close()

        # Initialize scanner with progress callback
        scanner = CodeScanner(
            scan_images=scan_images,
            generate_fixes=generate_fixes,
            validate_alt_text=validate_alt_text,
            progress_callback=update_progress,
        )

        # Perform scan
        result: CodeScanResult = scanner.scan_uploaded_code(
            file_path=file_path, project_name=filename
        )

        processing_time = int((time.time() - start_time) * 1000)
        logger.info(f"[BACKGROUND] Code processed in {processing_time}ms: {filename}")

        # Calculate issue counts
        critical = sum(1 for issue in result.issues if issue.severity == "critical")
        high = sum(1 for issue in result.issues if issue.severity == "serious")
        medium = sum(1 for issue in result.issues if issue.severity == "moderate")
        low = sum(1 for issue in result.issues if issue.severity == "minor")

        # Update scan record
        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Processing complete"
        scan.processing_time_ms = processing_time
        scan.pages = result.files_analyzed
        scan.file_hash = hashlib.sha256(file_content).hexdigest()
        scan.completed_at = func.now()

        # Create ScanResult
        scan_result = ScanResult(
            scan_id=scan.id,
            compliance_score=result.compliance_score,
            wcag_level="AA",
            critical_issues=critical,
            high_issues=high,
            medium_issues=medium,
            low_issues=low,
            issues=[issue.dict() for issue in result.issues[:MAX_SCANFIX_ISSUES]],
            structure={
                "project_name": result.project_name,
                "files_analyzed": result.files_analyzed,
                "total_lines": result.total_lines,
                "images_count": len(result.images),
            },
            suggestions=result.recommendations,
            ocr_used=False,
            ollama_used=generate_fixes,
            ollama_calls=len(result.issues) if generate_fixes else 0,
        )

        db.add(scan_result)
        db.commit()

        # ---- Persist ScanFix records for the review queue ----
        import uuid as _uuid
        from ...education.remediation.category_mapper import (
            code_rule_to_category,
            impact_to_severity,
            impact_to_confidence,
        )

        total_issues = len(result.issues)
        if total_issues > MAX_SCANFIX_ISSUES:
            logger.warning(
                f"Code scan {scan_id}: {total_issues} issues found, "
                f"capping at {MAX_SCANFIX_ISSUES} for review queue"
            )

        for issue_dict in (
            issue.dict() for issue in result.issues[:MAX_SCANFIX_ISSUES]
        ):
            scanner_cat = issue_dict.get("category", "html")
            rule = issue_dict.get("rule", "")
            severity_raw = issue_dict.get("severity", "moderate")
            ai_fix = issue_dict.get("ai_generated_fix")
            human_fix = issue_dict.get("fix_suggestion", "")
            file_path = issue_dict.get("file_path", "")
            line_num = issue_dict.get("line_number")

            db.add(
                ScanFix(
                    id=str(_uuid.uuid4()),
                    scan_id=scan.id,
                    issue_id=f"code-{rule}-{_stable_hash(issue_dict.get('description', ''))}",
                    category=code_rule_to_category(scanner_cat, rule),
                    severity=impact_to_severity(severity_raw),
                    description=issue_dict.get("description", ""),
                    location=f"{file_path}:{line_num}" if line_num else file_path,
                    original_content=issue_dict.get("code_snippet", ""),
                    fixed_content=ai_fix or human_fix or "",
                    fix_method="ai" if ai_fix else "heuristic",
                    model_used="gemini" if ai_fix else None,
                    confidence=impact_to_confidence(severity_raw),
                    needs_review=True,
                    review_status="pending",
                    wcag_criteria=issue_dict.get("wcag_criterion", ""),
                    page_number=None,
                )
            )

        db.commit()
        persisted = min(total_issues, MAX_SCANFIX_ISSUES)
        logger.info(
            f"Code scan {scan_id}: persisted {persisted}/{total_issues} ScanFix records"
        )

        logger.info(f"[BACKGROUND] Code Scan {scan_id} completed successfully")

    except Exception as e:
        logger.error(
            f"[BACKGROUND] Error processing Code {filename}: {str(e)}", exc_info=True
        )
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if scan:
            scan.status = ScanStatus.FAILED
            scan.error_message = str(e)
            scan.progress_message = f"Processing failed: {str(e)}"
            db.commit()
    finally:
        try:
            os.unlink(file_path)
        except Exception:
            pass
        db.close()


@router.post("/code/scan", response_model=dict)
async def scan_code(
    file: UploadFile = File(...),
    scan_images: bool = False,
    generate_fixes: bool = True,
    validate_alt_text: bool = False,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Scan uploaded website code (HTML/CSS/JS) for accessibility issues

    Static code analysis for web accessibility
    REQUIRES API KEY IN PRODUCTION
    REQUIRES: website feature (tier-gated via TIER_QUOTAS; enabled on all core tiers)

    Features:
    - HTML structure analysis (semantic markup, ARIA)
    - CSS analysis (color contrast, font sizes, focus indicators)
    - JavaScript analysis (keyboard navigation)
    - Image detection and alt text validation
    - Form accessibility checking
    - Heading hierarchy validation
    - AI-powered code fixes using Qwen Coder

    Accepts:
    - Single files: .html, .htm, .css, .js
    - ZIP archives: multiple files analyzed together

    Args:
        file: Code file (html, css, js, or zip)
        scan_images: Analyze images with AI (default: False)
        generate_fixes: Generate AI code fixes (default: True)
        validate_alt_text: Validate existing alt text quality (default: False)

    Returns:
        Comprehensive code scan results with issues, recommendations, and fixes
    """
    _, user_id, department_id = api_key_info

    # Check feature access - code scanning requires the website feature
    await require_feature(db, department_id, "website", "Code Accessibility Scanning")

    # Validate file type
    allowed_extensions = [".html", ".htm", ".css", ".js", ".zip"]
    file_ext = os.path.splitext(file.filename)[1].lower()

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file_ext}. Allowed: {', '.join(allowed_extensions)}",
        )

    # Security validation - check for malicious content (especially in ZIP files)
    content = await validate_uploaded_file(file, db, department_id)

    # Check file size limit
    from ...config.settings import get_settings

    settings = get_settings()
    if len(content) > settings.max_file_size_code:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_code / (1024*1024):.0f}MB",
        )

    # Create scan record first to get scan_id
    from ...db.models import Scan, ScanType, ScanStatus

    scan = Scan(
        scan_type=ScanType.CODE,
        status=ScanStatus.PROCESSING,
        file_name=file.filename,
        file_size_bytes=len(content),
        user_id=user_id,
        department_id=department_id,
        progress=0,
        progress_message="Starting code analysis...",
    )
    db.add(scan)
    db.flush()

    from ...utils.file_storage import save_uploaded_file

    await file.seek(0)
    storage_path = await save_uploaded_file(file, department_id, scan.id)
    scan.storage_path = storage_path

    from ...jobs.local_scan_job import enqueue_local_scan_job

    enqueue_local_scan_job(
        db,
        scan=scan,
        scan_kind="local_code",
        options={
            "scan_images": scan_images,
            "generate_fixes": generate_fixes,
            "validate_alt_text": validate_alt_text,
        },
        input_sha256=hashlib.sha256(content).hexdigest(),
    )

    db.commit()
    db.refresh(scan)

    logger.info(
        f"Created scan {scan.id} for Code: {file.filename} (generate_fixes={generate_fixes})"
    )

    # Return immediately with scan_id
    return {
        "success": True,
        "scan_id": str(scan.id),
        "status": "PROCESSING",
        "message": "Code analysis started. Poll /api/education/scans/{scan_id}/progress for updates.",
        "progress": 0,
        "progress_message": "Starting code analysis...",
    }
