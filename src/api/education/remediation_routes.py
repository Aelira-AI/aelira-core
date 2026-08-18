"""Remediation endpoints — auto-fix, code remediation, download, batch."""

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ...ai.providers import get_provider_manager
from ...auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ...db.database import get_db_dependency
from ...db.models import CloudFile, CloudOAuthCredentials, ScanFix, ScanType
from ...db.scan_service import ScanService
from ...education.image_alt_text import ImageAltTextGenerator
from ...middleware.quota import require_feature
from ...utils.sanitization import sanitize_for_postgres
from ...utils.security import require_persisted_canvas_origin
from ._shared import (
    APPROVED_REVIEW_STATUSES,
    RemediationOptions,
)
from ._scope import authorize_scan_access

logger = logging.getLogger(__name__)
router = APIRouter()


def _sanitize_str(value: Optional[str]) -> Optional[str]:
    """Strip NUL (0x00) bytes that PostgreSQL text columns reject."""
    return sanitize_for_postgres(value)


def _get_bound_fallback_cloud_file(
    db: Session, scan_id: str, department_id: str
) -> CloudFile | None:
    """Return a non-LTI fallback CloudFile bound to the scan tenant."""
    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.last_scan_id == scan_id,
            CloudFile.department_id == department_id,
        )
        .first()
    )
    if (
        not cloud_file
        or cloud_file.last_scan_id != scan_id
        or cloud_file.department_id != department_id
    ):
        return None
    return cloud_file


def _get_bound_cloud_credential(
    db: Session, cloud_file: CloudFile, department_id: str
) -> CloudOAuthCredentials | None:
    """Return the credential bound to a CloudFile's tenant and provider."""
    if not cloud_file.credential_id:
        return None
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.id == cloud_file.credential_id,
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == cloud_file.provider,
        )
        .first()
    )
    if (
        not credential
        or credential.id != cloud_file.credential_id
        or credential.department_id != department_id
        or credential.provider != cloud_file.provider
    ):
        return None
    return credential


# ==================== Auto-Remediation Helpers ====================


def _infer_category(issue: dict) -> str:
    """Infer accessibility category from issue fields.

    Checks explicit category/type first, then falls back to keyword
    matching on description/message and WCAG rule numbers.
    """
    # Use explicit category/type/issue_type if present
    explicit = issue.get("category") or issue.get("type") or issue.get("issue_type")
    if explicit:
        # Map LaTeX-specific issue_type values to categories
        issue_type_map = {
            # LaTeX/general issue types
            "missing_title": "title",
            "missing_author": "title",
            "title_not_displayed": "title",
            "missing_lang": "language",
            "missing_language": "language",
            "missing_alt_text": "alt_text",
            "missing_figure_caption": "alt_text",
            "missing_table_caption": "table",
            "missing_table_structure": "table",
            "complex_table_no_header": "table",
            "equation_no_label": "aria",
            "color_only_emphasis": "color",
            "low_contrast_potential": "contrast",
            "low_color_contrast": "contrast",
            "unlabeled_hyperlink": "link",
            "links_missing_alt": "link",
            "vague_link_text": "link",
            "missing_list_structure": "list",
            # PDF scanner issue types
            "reading_order_mismatch": "reading_order",
            "unlabeled_form_fields": "form",
            "missing_tab_order": "form",
            "missing_structure_tree": "structure",
            "empty_structure_tree": "structure",
            "not_marked_tagged": "structure",
            "missing_content_marking": "structure",
            "empty_parent_tree": "structure",
            "missing_document_root": "structure",
            "missing_pdfua_identifier": "structure",
            "missing_bookmarks": "navigation",
            "missing_tounicode": "structure",
            "missing_role_map": "structure",
            "incomplete_role_map": "structure",
        }
        if explicit in issue_type_map:
            return issue_type_map[explicit]
        return explicit

    rule = (
        issue.get("rule")
        or issue.get("wcag_criteria")
        or issue.get("wcag_criterion")
        or ""
    ).lower()
    msg = (
        issue.get("message") or issue.get("description") or issue.get("title") or ""
    ).lower()

    # Keyword matching on description/message
    if "heading" in msg or "h1" in msg:
        return "heading"
    if "alt" in msg and ("text" in msg or "image" in msg):
        return "alt_text"
    if "contrast" in msg or "color" in msg:
        return "contrast"
    if "table" in msg:
        return "table"
    if "link" in msg or "url" in msg or "hyperlink" in msg:
        return "link"
    if "language" in msg or "lang" in msg:
        return "language"
    if "keyboard" in msg or "focus" in msg:
        return "keyboard"
    if "form" in msg or "label" in msg:
        return "form"
    if "title" in msg or "author" in msg:
        return "title"
    if "structure tree" in msg or "untagged" in msg or "tagged" in msg:
        return "structure"
    if "bookmark" in msg or "outline" in msg:
        return "navigation"
    if "list" in msg and ("fake" in msg or "bullet" in msg):
        return "list"
    if "equation" in msg:
        return "aria"

    # Fall back to WCAG rule number
    if "1.1" in rule:
        return "alt_text"
    if "1.3" in rule:
        return "structure"
    if "1.4" in rule:
        return "contrast"
    if "2.1" in rule:
        return "keyboard"
    if "2.4" in rule:
        return "navigation"
    if "3.1" in rule:
        return "language"
    if "4.1" in rule:
        return "aria"

    return "other"


def _normalize_issues_for_remediation(issues: list) -> list:
    """Normalize raw scanner issues into the format the remediator expects.

    The remediator's ``can_auto_fix()`` and ``apply_fix()`` rely on a rich
    ``metadata`` dict (with ``generated_alt_text``, ``page_number``,
    ``issue_type``, ``paragraph_index``, etc.).  Scanner output stores these
    at the top level of the issue dict; this function copies them into the
    ``metadata`` sub-dict so the remediator can find them.

    Normalizes raw scan issues into the remediator input shape.
    """
    normalized = []
    for i, issue in enumerate(issues):
        category = _infer_category(issue)
        category_lower = category.lower()

        metadata = issue.get("metadata") or {}
        # Copy scanner top-level fields into metadata
        metadata.setdefault("page_number", issue.get("page_number", 1))
        metadata.setdefault("issue_type", issue.get("issue_type"))
        metadata.setdefault("element", issue.get("element"))
        metadata.setdefault("text", issue.get("text", ""))
        metadata.setdefault("bbox", issue.get("bbox"))
        metadata.setdefault("image_index", issue.get("image_index", 0))
        metadata.setdefault(
            "generated_alt_text",
            issue.get("generated_alt_text") or issue.get("alt_text"),
        )
        # Paragraph location (DOCX fixes)
        metadata.setdefault("paragraph_index", issue.get("paragraph_index"))
        metadata.setdefault("paragraph_indices", issue.get("paragraph_indices"))
        # Heading-specific
        metadata.setdefault(
            "suggested_level",
            issue.get("suggested_level") or issue.get("expected_level", 1),
        )
        metadata.setdefault("current_level", issue.get("current_level"))
        metadata.setdefault("expected_level", issue.get("expected_level"))
        # List-specific
        metadata.setdefault(
            "is_fake_list",
            category_lower == "list" or "fake" in str(issue.get("title", "")).lower(),
        )
        # Table-specific
        metadata.setdefault("has_data_rows", True)
        metadata.setdefault("table_index", issue.get("table_index"))
        # Link text
        metadata.setdefault("link_text", issue.get("link_text"))
        metadata.setdefault("link_url", issue.get("link_url"))
        # Title-specific
        metadata.setdefault("suggested_title", issue.get("suggested_title"))
        metadata.setdefault("existing_title", issue.get("existing_title"))
        # PPTX-specific: map scanner fields to remediator expectations
        # The PowerPointProcessor's computed `.issues` field already exposes
        # 0-based slide_index at the top level; only fall back to the legacy
        # 1-based slide_number / slide keys if the 0-based value is absent.
        # Without this branch, metadata.slide_index stayed None and every
        # PPTX alt-text fix bailed at "No slide index for alt text fix".
        if "slide_index" in issue and issue.get("slide_index") is not None:
            metadata.setdefault("slide_index", int(issue["slide_index"]))
        else:
            slide_num = issue.get("slide_number") or issue.get("slide")
            if slide_num is not None:
                metadata.setdefault("slide_index", int(slide_num) - 1)
        metadata.setdefault("shape_id", issue.get("shape_id"))
        metadata.setdefault("shape_name", issue.get("shape_name", ""))
        metadata.setdefault(
            "suggested_alt_text",
            issue.get("suggested_alt_text"),
        )

        suggested_fix = issue.get("suggested_fix") or issue.get("fix_suggestion") or ""

        normalized.append(
            {
                "id": issue.get("id", f"issue-{i}"),
                "category": category,
                "type": category,
                "severity": issue.get("severity", "medium"),
                "description": issue.get("description")
                or issue.get("message", "Accessibility issue"),
                "message": issue.get("message") or issue.get("description", ""),
                "location": issue.get("location", "Unknown"),
                "fix_suggestion": suggested_fix,
                "recommendation": issue.get("recommendation", ""),
                "metadata": metadata,
            }
        )
    return normalized


def _scanfix_to_issue_dict(fix) -> dict:
    """Convert a ScanFix ORM record to the dict format expected by HtmlRemediator.

    The BaseRemediator._normalize_issues() method consumes dicts with keys like
    ``id``, ``category``, ``severity``, ``description``, ``location``,
    ``original_content``, and ``fix_suggestion``.  The ``fixed_content`` from
    the review queue is passed as ``fix_suggestion`` so that the remediator
    uses the already-approved content rather than generating a new fix.
    """
    return {
        "id": fix.issue_id or fix.id,
        "category": fix.category or "other",
        "severity": fix.severity or "medium",
        "description": fix.description or "",
        "location": fix.location,
        "original_content": fix.original_content,
        "fix_suggestion": fix.fixed_content,
        "fixed_content": fix.fixed_content,
        "wcag_criteria": fix.wcag_criteria,
        "metadata": {},
    }


def _map_category_string(category_str: str):
    """Map a category string to IssueCategory enum value.

    Uses the same mapping as BaseRemediator._map_category().
    """
    from ...education.remediation.base import IssueCategory

    category_map = {
        "alt_text": IssueCategory.ALT_TEXT,
        "alternative_text": IssueCategory.ALT_TEXT,
        "image": IssueCategory.ALT_TEXT,
        "heading": IssueCategory.HEADING,
        "heading_structure": IssueCategory.HEADING,
        "contrast": IssueCategory.CONTRAST,
        "color_contrast": IssueCategory.CONTRAST,
        "table": IssueCategory.TABLE,
        "table_header": IssueCategory.TABLE,
        "link": IssueCategory.LINK,
        "hyperlink": IssueCategory.LINK,
        "list": IssueCategory.LIST,
        "list_structure": IssueCategory.LIST,
        "language": IssueCategory.LANGUAGE,
        "reading_order": IssueCategory.READING_ORDER,
        "form": IssueCategory.FORM,
        "aria": IssueCategory.ARIA,
        "navigation": IssueCategory.NAVIGATION,
        "structure": IssueCategory.STRUCTURE,
        "color": IssueCategory.COLOR,
        "chart": IssueCategory.CHART,
        "sheet": IssueCategory.SHEET,
        "title": IssueCategory.TITLE,
        "other": IssueCategory.OTHER,
    }

    normalized = category_str.lower().strip().replace(" ", "_").replace("-", "_")
    return category_map.get(normalized, IssueCategory.OTHER)


def _map_severity_string(severity_str: str):
    """Map a severity string to IssueSeverity enum value.

    Uses the same mapping as BaseRemediator._map_severity().
    """
    from ...education.remediation.base import IssueSeverity

    severity_map = {
        "critical": IssueSeverity.CRITICAL,
        "high": IssueSeverity.HIGH,
        "medium": IssueSeverity.MEDIUM,
        "low": IssueSeverity.LOW,
        "error": IssueSeverity.HIGH,
        "warning": IssueSeverity.MEDIUM,
        "info": IssueSeverity.LOW,
    }

    normalized = severity_str.lower().strip()
    return severity_map.get(normalized, IssueSeverity.MEDIUM)


# ==================== Auto-Remediation Endpoints ====================


@router.post("/remediate/batch")
async def batch_remediate(
    scan_ids: List[str],
    background_tasks: BackgroundTasks,
    use_ai: bool = True,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """Validate and queue a batch without allowing partial authorization."""
    return await _batch_remediate_impl(
        scan_ids=scan_ids,
        use_ai=use_ai,
        background_tasks=background_tasks,
        db=db,
        principal=principal,
    )


@router.post("/remediate/{scan_id}")
async def remediate_scan(
    scan_id: str,
    request: Request,
    options: Optional[RemediationOptions] = None,
    use_ai: bool = True,  # Keep for backwards compatibility
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Auto-remediate accessibility issues for a completed scan.

    Automatically fix accessibility issues in the scanned document.
    REQUIRES API KEY IN PRODUCTION.

    This endpoint triggers the auto-remediation engine to fix as many
    accessibility issues as possible in the scanned document.

    Args:
        scan_id: The scan ID to remediate
        options: Remediation options (use_ai, latex_formats, multimedia_format)
        use_ai: Whether to use AI for generating fixes (default: True, deprecated)

    Returns:
        Remediation result with fixed and manual issues counts,
        and path to the remediated document.
    """
    from ...education.remediation import (
        RemediationConfig,
        DocxRemediator,
        PptxRemediator,
        PdfRemediator,
        XlsxRemediator,
        LatexRemediator,
        MultimediaRemediator,
    )
    from ...education.remediation.base import OutputFormat

    _, user_id, department_id = principal.as_legacy_tuple()

    # Get the scan
    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorized_cloud_file = authorize_scan_access(db, scan, principal)

    if not scan.result:
        raise HTTPException(status_code=400, detail="Scan has no results to remediate")

    # Get issues from scan result
    issues = []
    if scan.result.issues:
        if isinstance(scan.result.issues, list):
            issues = scan.result.issues
        elif isinstance(scan.result.issues, dict):
            issues = scan.result.issues.get("details", [])

    if not issues:
        return {
            "success": True,
            "message": "No issues to remediate",
            "fixed_count": 0,
            "manual_count": 0,
        }

    # Check if we have a stored file path (from manual upload)
    file_path = scan.storage_path

    # Fall back to scan.result.file_path for backward compatibility
    if not file_path and hasattr(scan.result, "file_path"):
        file_path = scan.result.file_path

    # Fall back: re-download from cloud provider if this was a cloud scan
    if not file_path or not os.path.exists(file_path):
        from ...db.models import CloudProvider
        from ...integrations.oauth_token_manager import OAuthTokenManager

        cloud_file = authorized_cloud_file or _get_bound_fallback_cloud_file(
            db, scan_id, principal.department_id
        )
        if cloud_file and cloud_file.credential_id:
            credential = _get_bound_cloud_credential(
                db, cloud_file, principal.department_id
            )
            if credential:
                try:
                    canvas_url = None
                    if credential.provider == CloudProvider.CANVAS.value:
                        canvas_url = require_persisted_canvas_origin(credential)
                    token_manager = OAuthTokenManager()
                    access_token = await token_manager.refresh_if_expired(
                        credential, db
                    )

                    # Determine provider and download
                    import tempfile

                    temp_dir = tempfile.mkdtemp()
                    local_path = os.path.join(
                        temp_dir, f"{cloud_file.file_name or 'file'}"
                    )

                    if credential.provider == CloudProvider.CANVAS.value:
                        from ...integrations.canvas.canvas_api import CanvasAPIClient

                        assert canvas_url is not None
                        if (
                            os.getenv("ENV") == "development"
                            and "localhost" in canvas_url
                        ):
                            canvas_url = canvas_url.replace(
                                "localhost", "host.docker.internal"
                            )

                        client = CanvasAPIClient(
                            canvas_instance_url=canvas_url, access_token=access_token
                        )
                        try:
                            dl_result = await client.download_file(
                                file_id=cloud_file.provider_file_id,
                                local_path=local_path,
                            )
                            if dl_result.success:
                                file_path = dl_result.local_path
                                # Update scan storage_path so future remediations don't re-download
                                scan.storage_path = file_path
                                db.commit()
                                logger.info(
                                    f"Re-downloaded Canvas file for remediation: {cloud_file.file_name}"
                                )
                        finally:
                            await client.close()

                    elif credential.provider == CloudProvider.GOOGLE.value:
                        from ...integrations.google_workspace.google_drive import (
                            GoogleDriveIntegration,
                        )

                        drive = GoogleDriveIntegration(
                            credential_id=credential.id, access_token=access_token
                        )
                        try:
                            dl_result = await drive.download_file(
                                file_id=cloud_file.provider_file_id,
                                local_path=local_path,
                            )
                            if dl_result.success:
                                file_path = dl_result.local_path
                                scan.storage_path = file_path
                                db.commit()
                                logger.info(
                                    f"Re-downloaded Google Drive file for remediation: {cloud_file.file_name}"
                                )
                        finally:
                            await drive.close()

                    elif credential.provider == CloudProvider.BRIGHTSPACE.value:
                        from ...integrations.brightspace.brightspace_api import (
                            BrightspaceAPIClient,
                        )

                        instance_url = (credential.provider_metadata or {}).get(
                            "brightspace_instance_url", ""
                        )
                        metadata = cloud_file.provider_metadata or {}
                        org_unit_id = metadata.get("org_unit_id")
                        topic_id = int(cloud_file.provider_file_id)

                        # Add proper extension from URL
                        url = metadata.get("url", "")
                        if "." in url:
                            url_ext = url.rsplit(".", 1)[-1].lower()
                            local_path = os.path.join(
                                temp_dir, f"{cloud_file.file_name or 'file'}.{url_ext}"
                            )

                        bs_client = BrightspaceAPIClient(
                            brightspace_instance_url=instance_url,
                            access_token=access_token,
                        )
                        try:
                            file_bytes, content_type = await bs_client.get_topic_file(
                                int(org_unit_id), topic_id
                            )
                            with open(local_path, "wb") as f:
                                f.write(file_bytes)
                            file_path = local_path
                            scan.storage_path = file_path
                            db.commit()
                            logger.info(
                                f"Re-downloaded Brightspace file for remediation: {cloud_file.file_name}"
                            )
                        finally:
                            await bs_client.close()

                except Exception as e:
                    logger.error(
                        f"Failed to re-download cloud file for remediation: {e}"
                    )

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=400,
            detail="Original file not available for remediation. Please re-upload and scan.",
        )

    # Determine document type and get appropriate remediator
    scan_type = scan.scan_type

    # Map scan type to remediator
    remediator_map = {
        ScanType.WORD: DocxRemediator,
        ScanType.EXCEL: XlsxRemediator,
        ScanType.PDF: PdfRemediator,
        ScanType.POWERPOINT: PptxRemediator,
        ScanType.LATEX: LatexRemediator,
        ScanType.MULTIMEDIA: MultimediaRemediator,
        ScanType.VIDEO: MultimediaRemediator,
    }

    # Special case: LaTeX scan with a PDF file should use PdfRemediator
    # (This happens when a PDF is uploaded to the LaTeX scanner for math-aware scanning)
    file_ext = Path(file_path).suffix.lower()
    if scan_type == ScanType.LATEX and file_ext == ".pdf":
        logger.info(f"LaTeX scan with PDF file - using PdfRemediator for {file_path}")
        RemediatorClass = PdfRemediator
    else:
        RemediatorClass = remediator_map.get(scan_type)

    # Special case: IMAGE scan — generate alt text via AI
    if scan_type == ScanType.IMAGE:
        from ...education.image_alt_text import ImageAltTextGenerator

        generator = ImageAltTextGenerator()
        analysis = await generator.analyze_image_comprehensive(
            image_path=file_path,
            context=f"Educational course content: {scan.file_name}",
        )

        alt_text = analysis.get("description", {}).get("alt_text", "")
        is_decorative = analysis.get("type_detection", {}).get("is_decorative", False)

        scan.status = "COMPLETED"
        scan.remediation_status = "completed"
        db.commit()

        return {
            "success": True,
            "message": (
                "Image alt text generated"
                if alt_text
                else "Image classified as decorative"
            ),
            "fixed_count": 1 if alt_text or is_decorative else 0,
            "manual_count": 0,
            "remediated_alt_text": alt_text,
            "is_decorative": is_decorative,
        }

    if not RemediatorClass:
        raise HTTPException(
            status_code=400,
            detail=f"Remediation not supported for scan type: {scan_type}",
        )

    # Build remediation config with options
    config = RemediationConfig(
        use_ai=options.use_ai if options else use_ai,
        verify_fixes=True,
        create_backup=True,
        output_directory=str(Path(file_path).parent),
    )

    # Apply LaTeX-specific options (use defaults if no options provided)
    if scan_type == ScanType.LATEX:
        latex_formats = options.latex_formats if options else ["tex", "pdf", "html"]
        config.latex_output_formats = [
            OutputFormat(fmt) for fmt in latex_formats if fmt in ["tex", "pdf", "html"]
        ]

    # Apply Multimedia-specific options
    if scan_type == ScanType.MULTIMEDIA and options:
        config.multimedia_output_format = OutputFormat(options.multimedia_format)
        config.include_original_in_zip = options.include_original_in_zip

    # Normalize issues for the remediator — populate metadata dict from
    # top-level fields so can_auto_fix() / apply_fix() work correctly.
    # Normalize raw scan issues into the remediator input shape.
    normalized_issues = _normalize_issues_for_remediation(issues)
    logger.info(
        f"Normalized {len(normalized_issues)} issues for remediation (scan {scan_id})"
    )

    effective_use_ai = options.use_ai if options else use_ai

    try:
        # Get AI client for alt text generation
        ai_client = get_provider_manager()

        # Create remediator and run remediation
        remediator = RemediatorClass(
            file_path=file_path,
            issues=normalized_issues,
            config=config,
            ai_client=ai_client,
        )

        result = remediator.remediate()

        # Persist fixes to scan_fixes table for the review workflow
        import uuid as _uuid

        for fix in result.fixed_issues:
            db.add(
                ScanFix(
                    id=str(_uuid.uuid4()),
                    scan_id=scan_id,
                    issue_id=fix.issue_id,
                    category=(
                        fix.category.value
                        if hasattr(fix.category, "value")
                        else fix.category
                    ),
                    severity=(
                        fix.severity.value
                        if hasattr(fix.severity, "value")
                        else fix.severity
                    ),
                    description=_sanitize_str(fix.description),
                    location=_sanitize_str(getattr(fix, "location", None)),
                    original_content=_sanitize_str(
                        getattr(fix, "original_content", None)
                    ),
                    fixed_content=_sanitize_str(getattr(fix, "fixed_content", None)),
                    fix_method=_sanitize_str(getattr(fix, "fix_method", None)),
                    model_used=getattr(fix, "model_used", None),
                    confidence=getattr(fix, "confidence", 0.5),
                    needs_review=getattr(fix, "needs_review", True),
                    review_status=(
                        "auto_approved"
                        if not getattr(fix, "needs_review", True)
                        else "pending"
                    ),
                    wcag_criteria=getattr(fix, "wcag_criteria", None),
                    page_number=getattr(fix, "page_number", None),
                )
            )
        db.commit()

        # Run Matterhorn validation on the remediated file
        try:
            from ...education.validation.matterhorn import MatterhornValidator
            from ...db import models as _dbm

            output_path = result.output_file
            if (
                output_path
                and os.path.exists(output_path)
                and output_path.endswith(".pdf")
            ):
                validator = MatterhornValidator()
                mh_result = validator.validate(output_path)
                for cp in mh_result.checkpoints:
                    db.add(
                        _dbm.MatterhornResult(
                            id=str(_uuid.uuid4()),
                            scan_id=scan_id,
                            checkpoint_id=cp.id,
                            checkpoint_name=_sanitize_str(cp.name),
                            status=cp.status.value,
                            severity=cp.severity,
                            details=_sanitize_str(cp.details),
                            page_number=cp.page_number,
                        )
                    )
                db.commit()
                logger.info(
                    f"Matterhorn validation complete for {scan_id}: "
                    f"{mh_result.passed}/{mh_result.total} passed"
                )
        except Exception as mh_err:
            logger.warning(f"Matterhorn validation skipped for {scan_id}: {mh_err}")

        # Log remediation completion
        from ...security.audit_service import AuditService

        AuditService(db).log_remediation_complete(
            user_id=user_id,
            department_id=department_id,
            scan_id=scan_id,
            file_type=scan_type.value if scan_type else "unknown",
            use_ai=effective_use_ai,
            total_issues=result.total_issues,
            fixed_count=result.fixed_count,
            manual_count=result.manual_count,
            original_score=result.original_compliance_score,
            remediated_score=result.remediated_compliance_score,
            improvement=result.improvement,
            duration_seconds=result.duration_seconds,
            request=request,
        )

        # Update CloudFile if this scan came from a cloud integration
        try:
            cloud_file = authorized_cloud_file or _get_bound_fallback_cloud_file(
                db, scan_id, principal.department_id
            )
            if cloud_file:
                cloud_file.has_remediated_version = True
                db.commit()
        except Exception as cf_err:
            logger.warning(f"Failed to update CloudFile remediation status: {cf_err}")

        return {
            "success": result.success,
            "scan_id": scan_id,
            "original_file": result.original_file,
            "output_file": result.output_file,
            "total_issues": result.total_issues,
            "fixed_count": result.fixed_count,
            "manual_count": result.manual_count,
            "failed_count": result.failed_count,
            "original_score": result.original_compliance_score,
            "remediated_score": result.remediated_compliance_score,
            "improvement": result.improvement,
            "duration_seconds": result.duration_seconds,
            "fixed_issues": [
                {
                    "id": f.issue_id,
                    "category": f.category.value,
                    "severity": f.severity.value,
                    "description": f.description,
                    "fix_method": f.fix_method,
                }
                for f in result.fixed_issues
            ],
            "manual_issues": [
                {
                    "id": m.issue_id,
                    "category": m.category.value,
                    "severity": m.severity.value,
                    "description": m.description,
                    "reason": m.reason,
                    "recommendation": m.recommendation,
                }
                for m in result.manual_issues
            ],
            "warnings": result.warnings,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Remediation failed for scan {scan_id}: {e}", exc_info=True)
        # Log remediation failure
        try:
            from ...security.audit_service import AuditService

            AuditService(db).log_remediation_failed(
                user_id=user_id,
                department_id=department_id,
                scan_id=scan_id,
                file_type=scan_type.value if scan_type else "unknown",
                use_ai=effective_use_ai,
                error=str(e),
                request=request,
            )
        except Exception:
            pass  # Audit logging should never break the main flow
        raise HTTPException(
            status_code=500, detail="Remediation failed. Please try again."
        )


# ==================== Code Remediation Endpoint ====================


@router.post("/code/remediate/{scan_id}")
def remediate_code_scan(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Remediate a code (HTML) scan using approved ScanFix records.

    This endpoint loads fixes that have been reviewed and approved (or
    auto-approved) for a code scan, converts them to the format expected
    by HtmlRemediator, runs the remediator, and returns a structured result.

    Only HTML files can be auto-remediated; CSS and JS files are not supported.
    """
    from ...education.remediation.html_remediator import HtmlRemediator
    from ...education.remediation.base import RemediationConfig

    _, user_id, department_id = principal.as_legacy_tuple()

    # 1. Verify scan exists and belongs to user's department
    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorize_scan_access(db, scan, principal)

    # Must be a code scan
    scan_type_value = (
        scan.scan_type.value
        if hasattr(scan.scan_type, "value")
        else str(scan.scan_type)
    )
    if scan_type_value.upper() not in ("CODE",):
        raise HTTPException(
            status_code=400,
            detail=f"This endpoint only supports code scans. Scan type is: {scan_type_value}",
        )

    # 2. Verify the file is HTML
    file_path = scan.storage_path
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=400,
            detail="Original file not available for remediation. Please re-upload and scan.",
        )

    file_ext = Path(file_path).suffix.lower()
    if file_ext not in (".html", ".htm"):
        raise HTTPException(
            status_code=400,
            detail=f"Only HTML files can be auto-remediated. File is: {file_ext}",
        )

    # 3. Query approved ScanFix records
    approved_fixes = (
        db.query(ScanFix)
        .filter(
            ScanFix.scan_id == scan_id,
            ScanFix.review_status.in_(list(APPROVED_REVIEW_STATUSES)),
        )
        .all()
    )

    if not approved_fixes:
        raise HTTPException(
            status_code=400,
            detail="No approved fixes to apply. Review and approve fixes first.",
        )

    # 4. Convert ScanFix -> issue dicts for HtmlRemediator
    issue_dicts = [_scanfix_to_issue_dict(fix) for fix in approved_fixes]

    logger.info(
        f"Code remediation for scan {scan_id}: {len(issue_dicts)} approved fixes",
        extra={"user_id": user_id, "department_id": department_id},
    )

    # 5. Initialize HtmlRemediator
    config = RemediationConfig(
        use_ai=False,  # We already have approved fixes; no AI needed
        verify_fixes=True,
        create_backup=True,
        output_directory=str(Path(file_path).parent),
    )

    try:
        remediator = HtmlRemediator(
            file_path=file_path,
            issues=issue_dicts,
            config=config,
            ai_client=None,
        )

        # 6. Run remediation
        result = remediator.remediate()

        # 7. Update ScanFix records — mark applied ones
        applied_issue_ids = {f.issue_id for f in result.fixed_issues}
        failed_issue_ids = {f.get("issue_id") for f in result.failed_issues}
        now = datetime.now(timezone.utc)

        for fix in approved_fixes:
            fix_issue_id = fix.issue_id or fix.id
            if fix_issue_id in applied_issue_ids:
                fix.review_status = "applied"
                fix.updated_at = now
            elif fix_issue_id in failed_issue_ids:
                fix.review_status = "apply_failed"
                fix.updated_at = now

        # 8. Commit
        db.commit()

        logger.info(
            f"Code remediation complete for scan {scan_id}: "
            f"{result.fixed_count} fixed, {result.manual_count} manual, "
            f"{result.failed_count} failed",
            extra={"user_id": user_id, "department_id": department_id},
        )

        # 9. Return structured result
        return {
            "success": result.success,
            "scan_id": scan_id,
            "fixes_applied": result.fixed_count,
            "fixes_failed": result.failed_count,
            "manual_fixes": result.manual_count,
            "output_file": result.output_file,
            "original_score": result.original_compliance_score,
            "remediated_score": result.remediated_compliance_score,
            "fixed_issues": [
                {
                    "id": f.issue_id,
                    "category": f.category.value,
                    "severity": f.severity.value,
                    "description": f.description,
                    "fix_method": f.fix_method,
                }
                for f in result.fixed_issues
            ],
            "manual_issues": [
                {
                    "id": m.issue_id,
                    "category": m.category.value,
                    "severity": m.severity.value,
                    "description": m.description,
                    "reason": m.reason,
                }
                for m in result.manual_issues
            ],
            "warnings": result.warnings,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Code remediation failed for scan {scan_id}: {e}",
            exc_info=True,
            extra={"user_id": user_id, "department_id": department_id},
        )
        raise HTTPException(
            status_code=500, detail="Code remediation failed. Please try again."
        )


@router.get("/scans/{scan_id}/remediated")
async def download_remediated_file(
    scan_id: str,
    request: Request,
    format: Optional[str] = None,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    Download the remediated document for a scan.

    For LaTeX scans, use ?format=tex|pdf|html to select format.
    For Multimedia scans, returns ZIP if created, otherwise caption file.

    REQUIRES API KEY IN PRODUCTION
    """
    from fastapi.responses import FileResponse
    from ...utils.file_storage import get_remediated_file_path

    _, user_id, department_id = principal.as_legacy_tuple()

    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorize_scan_access(db, scan, principal)

    file_path = scan.storage_path
    if not file_path:
        raise HTTPException(status_code=404, detail="Original file not found")

    # Get base remediated path
    remediated_base = get_remediated_file_path(file_path)
    remediated_dir = Path(remediated_base).parent
    base_stem = Path(file_path).stem + "_remediated"

    # Determine which file to return based on scan type and format request
    scan_type = scan.scan_type
    file_ext = Path(file_path).suffix.lower()

    # Special case: LaTeX scan with a PDF file - treat as PDF download
    if scan_type == ScanType.LATEX and file_ext == ".pdf":
        target_path = Path(remediated_base)
        if not target_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Remediated PDF not found. Run remediation first.",
            )

    elif scan_type == ScanType.LATEX:
        # LaTeX (.tex file): support tex, pdf, html formats
        format_ext = (format or "tex").lower()
        if format_ext not in ["tex", "pdf", "html"]:
            format_ext = "tex"

        target_path = remediated_dir / f"{base_stem}.{format_ext}"

        if not target_path.exists():
            # Try original .tex if requested format not available
            target_path = Path(remediated_base)
            if not target_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"Remediated {format_ext.upper()} not found. Run remediation with latex_formats=['{format_ext}']",
                )

    elif scan_type == ScanType.MULTIMEDIA:
        # Multimedia: check for ZIP first, then individual files
        zip_path = remediated_dir / f"{Path(file_path).stem}_accessible.zip"
        vtt_path = remediated_dir / f"{Path(file_path).stem}.vtt"
        transcript_path = remediated_dir / f"{Path(file_path).stem}_transcript.txt"

        if format == "zip" and zip_path.exists():
            target_path = zip_path
        elif format == "vtt" and vtt_path.exists():
            target_path = vtt_path
        elif format == "transcript" and transcript_path.exists():
            target_path = transcript_path
        elif zip_path.exists():
            target_path = zip_path
        elif vtt_path.exists():
            target_path = vtt_path
        elif transcript_path.exists():
            target_path = transcript_path
        else:
            raise HTTPException(
                status_code=404,
                detail="No remediated files found. Run remediation first.",
            )
    else:
        # Standard document types
        target_path = Path(remediated_base)
        if not target_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Remediated file not found. Please run remediation first.",
            )

    # Determine MIME type
    mime_types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".tex": "application/x-tex",
        ".html": "text/html",
        ".zip": "application/zip",
        ".vtt": "text/vtt",
        ".txt": "text/plain",
    }

    suffix = target_path.suffix.lower()
    media_type = mime_types.get(suffix, "application/octet-stream")

    # Log download event
    from ...security.audit_service import AuditService

    AuditService(db).log_remediation_download(
        user_id=user_id,
        department_id=department_id,
        scan_id=scan_id,
        file_type=scan_type.value if scan_type else "unknown",
        format=format or suffix.lstrip("."),
        request=request,
    )

    return FileResponse(
        path=str(target_path),
        filename=target_path.name,
        media_type=media_type,
    )


@router.get("/scans/{scan_id}/remediated/formats")
async def list_remediated_formats(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    """
    List available remediated file formats for a scan.

    Returns which formats are available for download.
    """
    _, user_id, department_id = principal.as_legacy_tuple()

    scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    authorize_scan_access(db, scan, principal)

    file_path = scan.storage_path
    if not file_path:
        return {"available_formats": [], "message": "No file stored"}

    from ...utils.file_storage import get_remediated_file_path

    remediated_base = get_remediated_file_path(file_path)
    remediated_dir = Path(remediated_base).parent
    base_stem = Path(file_path).stem
    remediated_stem = f"{base_stem}_remediated"

    available = []
    scan_type = scan.scan_type
    file_ext = Path(file_path).suffix.lower()

    # Special case: LaTeX scan with a PDF file - treat as standard PDF
    if scan_type == ScanType.LATEX and file_ext == ".pdf":
        path = Path(remediated_base)
        if path.exists():
            available.append(
                {
                    "format": "pdf",
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "download_url": f"/education/scans/{scan_id}/remediated",
                }
            )

    elif scan_type == ScanType.LATEX:
        # LaTeX .tex file: Check for each LaTeX output format
        for ext in ["tex", "pdf", "html"]:
            path = remediated_dir / f"{remediated_stem}.{ext}"
            if path.exists():
                available.append(
                    {
                        "format": ext,
                        "filename": path.name,
                        "size_bytes": path.stat().st_size,
                        "download_url": f"/education/scans/{scan_id}/remediated?format={ext}",
                    }
                )

    elif scan_type == ScanType.MULTIMEDIA:
        # Check for multimedia outputs
        zip_path = remediated_dir / f"{base_stem}_accessible.zip"
        vtt_path = remediated_dir / f"{base_stem}.vtt"
        transcript_path = remediated_dir / f"{base_stem}_transcript.txt"
        ad_path = remediated_dir / f"{base_stem}_audio_descriptions.txt"

        for path, fmt in [
            (zip_path, "zip"),
            (vtt_path, "vtt"),
            (transcript_path, "transcript"),
            (ad_path, "audio_descriptions"),
        ]:
            if path.exists():
                available.append(
                    {
                        "format": fmt,
                        "filename": path.name,
                        "size_bytes": path.stat().st_size,
                        "download_url": f"/education/scans/{scan_id}/remediated?format={fmt}",
                    }
                )
    else:
        # Standard document
        path = Path(remediated_base)
        if path.exists():
            available.append(
                {
                    "format": path.suffix.lstrip("."),
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "download_url": f"/education/scans/{scan_id}/remediated",
                }
            )

    return {
        "scan_id": scan_id,
        "scan_type": scan_type.value,
        "available_formats": available,
        "remediation_complete": len(available) > 0,
    }


async def _batch_remediate_impl(
    scan_ids: List[str],
    use_ai: bool,
    background_tasks: BackgroundTasks,
    db: Session,
    principal: AuthenticatedPrincipal,
):
    """
    Batch remediate multiple scans.

    Starts remediation for multiple scans in the background.
    Returns immediately with a batch ID to track progress.
    REQUIRES API KEY IN PRODUCTION.
    REQUIRES: bulk_api feature (tier-gated via TIER_QUOTAS; enabled on all core tiers)
    """
    _, user_id, department_id = principal.as_legacy_tuple()

    if not scan_ids:
        raise HTTPException(status_code=400, detail="No scan IDs provided")

    if len(scan_ids) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 scans per batch")

    batch_id = str(uuid.uuid4())

    # Resolve and authorize the complete request before feature checks, tasks,
    # writes, or external clients. This makes mixed-scope batches atomic.
    valid_scans = []
    for scan_id in scan_ids:
        scan = ScanService.get_scan_with_result(db=db, scan_id=scan_id)
        if not scan:
            raise HTTPException(status_code=404, detail="Scan not found")
        authorize_scan_access(db, scan, principal)
        if not scan.result:
            raise HTTPException(
                status_code=400, detail="Scan has no results to remediate"
            )
        valid_scans.append(scan_id)

    # Batch remediation requires bulk_api feature.
    await require_feature(db, department_id, "bulk_api", "Batch Remediation")

    # Queue batch remediation in background
    # For now, return the plan - actual background processing would be added
    return {
        "success": True,
        "batch_id": batch_id,
        "total_scans": len(valid_scans),
        "scans_queued": valid_scans,
        "message": "Batch remediation queued. Check individual scan statuses for progress.",
        "note": "Batch background processing coming soon. For now, remediate individually.",
    }


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    # Check if llava model is available
    generator = ImageAltTextGenerator()
    vision_health = generator.health_check()

    return {
        "status": "healthy",
        "service": "education-api-v2",
        "database": "enabled",
        "vision_model": vision_health.get("vision_model"),
        "vision_available": vision_health.get("vision_available", False),
        "features": [
            "pdf-ocr",
            "pdf-remediation",
            "powerpoint-scanning",
            "latex-mathml",
            "database-storage",
            "scan-history",
            "ollama-aria-labels",
            "image-alt-text",
            "code-scanning",
            "compliance-dashboard",
            "auto-remediation",
            "focus-order-analysis",
            "cvd-analysis",
        ],
    }
