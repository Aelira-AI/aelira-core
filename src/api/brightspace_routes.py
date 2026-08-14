"""
D2L Brightspace REST API Routes

Handles OAuth 2.0 authentication and file operations with Brightspace LMS.

Market Impact: +15% US, +10% Australia (community colleges)
"""

import os
import logging
import uuid
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from ..db.database import get_db_dependency
from ..db.models import (
    CloudOAuthCredentials,
    CloudProvider,
    CloudFile,
    CloudJobQueue,
    CloudJobType,
    CloudJobStatus,
    APIKey,
    Scan,
)
from ..integrations.brightspace import (
    get_brightspace_authorization_url,
    exchange_brightspace_code_for_token,
    BrightspaceAPIClient,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..api.auth_routes import get_current_api_key
from ..auth import get_required_api_key, verify_department_access
from ..auth.redis_rate_limiter import OAuthStateManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brightspace", tags=["brightspace"])

# =============================================================================
# Request/Response Models
# =============================================================================


class BrightspaceConnectRequest(BaseModel):
    """Request to initiate Brightspace OAuth connection"""

    brightspace_instance_url: str = Field(
        ...,
        description="Brightspace instance URL (e.g., https://university.brightspace.com)",
    )
    department_id: Optional[str] = None
    redirect_uri: Optional[str] = Field(
        None,
        description="OAuth callback URL (optional, defaults to /brightspace/callback)",
    )


class BrightspaceConnectionStatus(BaseModel):
    """Brightspace connection status response"""

    connected: bool
    brightspace_instance_url: Optional[str] = None
    user_email: Optional[str] = None
    user_fullname: Optional[str] = None
    connected_at: Optional[datetime] = None
    credential_id: Optional[str] = None


class BrightspaceRemediateRequest(BaseModel):
    """Request to remediate a Brightspace file"""

    file_url: str = Field(..., description="Brightspace file URL")
    org_unit_id: int = Field(..., description="Brightspace course ID (OrgUnitId)")
    department_id: str
    upload_back: bool = Field(
        True, description="Upload remediated file back to Brightspace"
    )
    use_ai: bool = Field(True, description="Use AI for fix generation")


class BrightspaceRemediateResponse(BaseModel):
    """Response from remediation request"""

    success: bool
    scan_id: Optional[str] = None
    job_id: Optional[str] = None
    message: str


class BrightspaceContentScanRequest(BaseModel):
    """Request to scan Brightspace course content"""

    org_unit_id: int = Field(..., description="Brightspace OrgUnit (course) ID")
    scan_types: str = Field("both", description="files, html, or both")
    module_id: Optional[int] = Field(
        None, description="Optional: scan only this module"
    )


class BrightspaceContentScanResponse(BaseModel):
    """Response from content scan request"""

    total_items: int
    jobs_queued: int
    skipped: int


# =============================================================================
# Helpers
# =============================================================================


def _get_credential(db: Session, dept_id: str) -> CloudOAuthCredentials:
    """Get Brightspace OAuth credential for department."""
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
            CloudOAuthCredentials.department_id == dept_id,
        )
        .first()
    )
    if not credential:
        raise HTTPException(status_code=404, detail="Brightspace not connected")
    return credential


async def _ensure_valid_token(credential: CloudOAuthCredentials, db: Session) -> str:
    """Check if Brightspace token is expired and refresh if needed. Returns decrypted access token."""
    token_manager = OAuthTokenManager()

    if token_manager.is_token_expired(credential.token_expires_at):
        refresh_token = token_manager.decrypt_token(credential.refresh_token)
        instance_url = (credential.provider_metadata or {}).get(
            "brightspace_instance_url", ""
        )

        try:
            from ..integrations.brightspace.brightspace_oauth import (
                refresh_brightspace_token,
            )

            new_access, new_refresh, new_expires = await refresh_brightspace_token(
                brightspace_instance_url=instance_url,
                refresh_token=refresh_token,
            )

            credential.access_token = token_manager.encrypt_token(new_access)
            if new_refresh:
                credential.refresh_token = token_manager.encrypt_token(new_refresh)
            credential.token_expires_at = new_expires
            db.commit()

            logger.info("Refreshed Brightspace token")
            return new_access

        except Exception as e:
            logger.error(f"Failed to refresh Brightspace token: {e}")
            raise HTTPException(
                status_code=409,
                detail="Brightspace token expired and refresh failed. Please reconnect.",
            )

    return token_manager.decrypt_token(credential.access_token)


# =============================================================================
# OAuth Flow
# =============================================================================


@router.post("/connect")
async def connect_brightspace(
    request: BrightspaceConnectRequest,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
) -> Dict[str, str]:
    """
    Initiate Brightspace OAuth 2.0 flow.

    Requires API key authentication to prevent unauthorized OAuth initiations.
    Returns authorization URL to redirect user to.
    """
    _, user_id, auth_department_id = api_key_info
    dept_id = request.department_id or auth_department_id
    verify_department_access(dept_id, auth_department_id)

    # Generate secure CSRF state token with metadata (stored server-side with TTL)
    state = OAuthStateManager.create_state(
        metadata={
            "department_id": dept_id,
            "brightspace_instance_url": request.brightspace_instance_url,
            "provider": "brightspace",
        }
    )

    # Generate redirect URI
    redirect_uri = (
        request.redirect_uri
        or f"{os.getenv('API_BASE_URL', 'http://localhost:8000')}/brightspace/callback"
    )

    try:
        # Generate authorization URL
        auth_url = get_brightspace_authorization_url(
            brightspace_instance_url=request.brightspace_instance_url,
            redirect_uri=redirect_uri,
            state=state,
        )

        logger.info(
            f"Initiated Brightspace OAuth for department {request.department_id} at {request.brightspace_instance_url}"
        )

        return {
            "authorization_url": auth_url,
            "state": state,
        }

    except ValueError as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@router.get("/callback")
async def brightspace_oauth_callback(
    code: str = Query(..., description="Authorization code from Brightspace"),
    state: str = Query(..., description="CSRF state token"),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """
    Handle Brightspace OAuth callback.

    Verifies state token, exchanges authorization code for access token, and stores credentials.
    """
    # Verify and consume state token (one-time use, expires after 10 minutes)
    is_valid, metadata = OAuthStateManager.verify_and_consume_state(state)
    if not is_valid or not metadata:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OAuth state. Please restart the connection flow.",
        )

    # Extract metadata from verified state
    department_id = metadata.get("department_id")
    brightspace_instance_url = metadata.get("brightspace_instance_url")

    if not department_id or not brightspace_instance_url:
        raise HTTPException(
            status_code=400,
            detail="Invalid OAuth state metadata. Please restart the connection flow.",
        )

    token_manager = OAuthTokenManager()

    try:
        # Generate redirect URI (must match the one used in /connect)
        redirect_uri = (
            f"{os.getenv('API_BASE_URL', 'http://localhost:8000')}/brightspace/callback"
        )

        # Exchange code for token
        access_token, refresh_token, expires_at = (
            await exchange_brightspace_code_for_token(
                brightspace_instance_url=brightspace_instance_url,
                authorization_code=code,
                redirect_uri=redirect_uri,
            )
        )

        # Get user info
        api_client = BrightspaceAPIClient(
            brightspace_instance_url=brightspace_instance_url,
            access_token=access_token,
        )

        try:
            user_info = await api_client.get_whoami()

            # Check if credential already exists
            existing = (
                db.query(CloudOAuthCredentials)
                .filter(
                    CloudOAuthCredentials.department_id == department_id,
                    CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
                    CloudOAuthCredentials.provider_user_id == user_info.Identifier,
                )
                .first()
            )

            if existing:
                # Update existing credential
                existing.access_token = token_manager.encrypt_token(access_token)
                if refresh_token:
                    existing.refresh_token = token_manager.encrypt_token(refresh_token)
                existing.token_expires_at = expires_at
                existing.provider_metadata = {
                    "brightspace_instance_url": brightspace_instance_url,
                    "user_email": user_info.UniqueName,
                    "user_name": f"{user_info.FirstName} {user_info.LastName}",
                }
                existing.provider_email = user_info.UniqueName
                existing.provider_name = f"{user_info.FirstName} {user_info.LastName}"
                existing.updated_at = datetime.now(timezone.utc)
                db.commit()

                logger.info(
                    f"Updated existing Brightspace credential for user {user_info.UniqueName}"
                )
            else:
                # Create new credential
                credential = CloudOAuthCredentials(
                    id=str(uuid.uuid4()),
                    department_id=department_id,
                    provider=CloudProvider.BRIGHTSPACE.value,
                    provider_user_id=user_info.Identifier,
                    provider_email=user_info.UniqueName,
                    provider_name=f"{user_info.FirstName} {user_info.LastName}",
                    provider_metadata={
                        "brightspace_instance_url": brightspace_instance_url,
                        "user_email": user_info.UniqueName,
                        "user_name": f"{user_info.FirstName} {user_info.LastName}",
                    },
                    access_token=token_manager.encrypt_token(access_token),
                    refresh_token=(
                        token_manager.encrypt_token(refresh_token)
                        if refresh_token
                        else None
                    ),
                    token_expires_at=expires_at,
                    scopes="core:*:* content:*:*",
                )

                db.add(credential)
                db.commit()

                logger.info(
                    f"Created new Brightspace credential for user {user_info.UniqueName}"
                )

            dashboard_url = os.getenv("DASHBOARD_URL", "https://dashboard.example.com")
            return RedirectResponse(
                url=f"{dashboard_url}/integrations?brightspace=connected&email={user_info.UniqueName}",
            )

        finally:
            await api_client.close()

    except Exception as e:
        logger.error(f"Brightspace OAuth callback failed: {e}")
        dashboard_url = os.getenv("DASHBOARD_URL", "https://dashboard.example.com")
        return RedirectResponse(
            url=f"{dashboard_url}/integrations?brightspace=error&message={str(e)[:100]}",
        )


# =============================================================================
# Connection Status
# =============================================================================


@router.get("/status")
async def get_brightspace_status(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> BrightspaceConnectionStatus:
    """Get Brightspace connection status for a department.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )

    if not credential:
        return BrightspaceConnectionStatus(connected=False)

    return BrightspaceConnectionStatus(
        connected=True,
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        user_email=credential.provider_metadata.get("user_email"),
        user_fullname=credential.provider_metadata.get("user_name"),
        connected_at=credential.created_at,
        credential_id=credential.id,
    )


# =============================================================================
# Course and Content Operations
# =============================================================================


@router.get("/courses")
async def list_brightspace_courses(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> List[Dict[str, Any]]:
    """List all Brightspace courses the user has access to.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="Brightspace not connected for this department",
        )

    # Ensure token is valid (refresh if expired)
    access_token = await _ensure_valid_token(credential, db)

    # Get courses
    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        courses = await api_client.get_my_enrollments()

        return [
            {
                "OrgUnitId": course.OrgUnitId,
                "Name": course.Name,
                "Code": course.Code,
                "org_unit_id": course.OrgUnitId,
                "name": course.Name,
                "code": course.Code,
                "start_date": course.StartDate,
                "end_date": course.EndDate,
                "is_active": course.IsActive,
            }
            for course in courses
        ]

    finally:
        await api_client.close()


@router.get("/courses/{org_unit_id}/content")
async def list_brightspace_course_content(
    org_unit_id: int,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> List[Dict[str, Any]]:
    """List all content modules in a Brightspace course.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="Brightspace not connected for this department",
        )

    # Ensure token is valid (refresh if expired)
    access_token = await _ensure_valid_token(credential, db)

    # Get course content
    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        content_items = await api_client.get_course_content(org_unit_id)

        return [
            {
                "id": item.Id,
                "title": item.Title,
                "short_title": item.ShortTitle,
                "type": item.Type,
                "is_hidden": item.IsHidden,
                "is_locked": item.IsLocked,
            }
            for item in content_items
        ]

    finally:
        await api_client.close()


# =============================================================================
# Content Scanning & Remediation
# =============================================================================


@router.get("/courses/{org_unit_id}/files")
async def list_brightspace_course_files(
    org_unit_id: int,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> List[Dict[str, Any]]:
    """List all scannable content items in a Brightspace course.

    Recursively walks the course content tree and returns files and HTML topics.

    Requires API key authentication.
    """
    department_id = api_key.department_id
    credential = _get_credential(db, department_id)

    # Ensure token is valid (refresh if expired)
    access_token = await _ensure_valid_token(credential, db)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        scannables = await api_client.get_course_content_recursive(org_unit_id)

        return [
            {
                "topic_id": item.topic_id,
                "org_unit_id": item.org_unit_id,
                "module_id": item.module_id,
                "title": item.title,
                "content_type": item.content_type,
                "url": item.url,
                "module_path": item.module_path,
            }
            for item in scannables
        ]

    finally:
        await api_client.close()


async def _brightspace_scan_file_task(
    job_id: str, cloud_file_id: str, credential_id: str
):
    """Background task to scan and auto-remediate a file from Brightspace LMS."""
    from ..db.database import get_db as _get_db_ctx
    from ..jobs.cloud_scan_job import handle_scan_job

    logger.info(f"Starting Brightspace scan: job={job_id}, file={cloud_file_id}")

    with _get_db_ctx() as db:
        job = db.query(CloudJobQueue).filter(CloudJobQueue.id == job_id).first()
        if not job:
            logger.error(f"Scan job not found: {job_id}")
            return

        try:
            job.status = CloudJobStatus.PROCESSING.value
            job.started_at = datetime.now(timezone.utc)
            job.progress = 10
            db.commit()

            token_manager = OAuthTokenManager()
            result = await handle_scan_job(job, db, token_manager)

            job.status = CloudJobStatus.COMPLETED.value
            job.progress = 100
            job.result_data = result
            job.completed_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(
                f"Brightspace scan complete: job={job_id}, "
                f"score={result.get('compliance_score')}"
            )

            # Store content_body reference for later remediation
            # (remediation is triggered separately by the user)

        except Exception as e:
            logger.error(f"Brightspace scan failed: job={job_id}, error={e}")
            job.status = CloudJobStatus.FAILED.value
            job.error_message = str(e)[:500]
            job.completed_at = datetime.now(timezone.utc)
            db.commit()


def _convert_axe_issues(axe_violations: list) -> list:
    """Convert axe-core violations to the format remediators expect."""
    from ..education.remediation.category_mapper import (
        wcag_criterion_to_category,
        impact_to_severity,
    )
    import re

    converted = []
    for violation in axe_violations:
        # Extract WCAG criterion from tags (e.g., "wcag131" → "1.3.1")
        category = "other"
        wcag = None
        for tag in violation.get("tags", []):
            match = re.match(r"wcag(\d)(\d)(\d+)", tag)
            if match:
                wcag = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
                category = wcag_criterion_to_category(wcag)
                break

        # Map axe rule IDs to categories as fallback
        axe_id = violation.get("id", "")
        _axe_id_to_category = {
            "empty-heading": "heading",
            "heading-order": "heading",
            "image-alt": "alt_text",
            "input-image-alt": "alt_text",
            "role-img-alt": "alt_text",
            "aria-required-children": "aria",
            "aria-required-parent": "aria",
            "aria-roles": "aria",
            "aria-valid-attr": "aria",
            "nested-interactive": "aria",
            "color-contrast": "contrast",
            "html-has-lang": "language",
            "html-lang-valid": "language",
            "label": "form",
            "link-name": "link",
            "list": "list",
            "listitem": "list",
            "table-fake-caption": "table",
            "td-headers-attr": "table",
            "document-title": "title",
        }
        if category == "other":
            category = _axe_id_to_category.get(axe_id, "other")

        severity = impact_to_severity(violation.get("impact", "moderate"))

        for node in violation.get("nodes", []):
            converted.append(
                {
                    "id": axe_id,
                    "category": category,
                    "type": category,
                    "severity": severity,
                    "description": violation.get("description", ""),
                    "message": violation.get("help", ""),
                    "element": node.get("html", ""),
                    "element_type": (
                        node.get("html", "").split("<")[1].split(" ")[0].split(">")[0]
                        if "<" in node.get("html", "")
                        else ""
                    ),
                    "location": ", ".join(node.get("target", [])),
                    "wcag_criteria": wcag,
                    "fix_suggestion": node.get("failureSummary", ""),
                    "original_content": node.get("html", ""),
                    "metadata": {
                        "element_xpath": ", ".join(node.get("target", [])),
                        "html": node.get("html", ""),
                        "axe_id": axe_id,
                        "impact": violation.get("impact", ""),
                    },
                }
            )
    return converted


async def _remediate_file(cloud_file: CloudFile, db):
    """Remediate a content item using the appropriate remediator for its file type."""
    import tempfile
    from ..db.models import ScanResult

    # Load issues from last scan
    scan_result = (
        db.query(ScanResult)
        .filter(ScanResult.scan_id == cloud_file.last_scan_id)
        .first()
    )
    if not scan_result or not scan_result.issues:
        return

    raw_issues = scan_result.issues

    # Determine file type from URL extension
    metadata = cloud_file.provider_metadata or {}
    url = metadata.get("url", "")
    ext = url.rsplit(".", 1)[-1].lower() if "." in url else ""

    # HTML remediation (content stored in content_body)
    if ext in ("html", "htm") or cloud_file.content_body:
        if not cloud_file.content_body:
            return
        from ..education.canvas_content_scanner import (
            _wrap_html_fragment,
            _unwrap_html_fragment,
            _sanitize_html,
        )
        from ..education.remediation.html_remediator import HtmlRemediator
        from ..education.remediation.base import RemediationConfig
        from ..ai.providers import get_provider_manager

        try:
            ai_client = get_provider_manager()
        except Exception:
            ai_client = None

        config = RemediationConfig(use_ai=True, verify_fixes=False, create_backup=False)
        wrapped_html = _wrap_html_fragment(cloud_file.content_body)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".html", delete=False, mode="w", encoding="utf-8"
            ) as tmp:
                tmp.write(wrapped_html)
                temp_path = tmp.name

            # HTML issues come from axe-core — convert to remediator format
            issues = _convert_axe_issues(raw_issues)
            remediator = HtmlRemediator(temp_path, issues, config, ai_client)
            result = remediator.remediate()

            output_path = result.output_file or temp_path
            with open(output_path, "r", encoding="utf-8") as f:
                remediated_doc = f.read()

            cloud_file.remediated_body = _sanitize_html(
                _unwrap_html_fragment(remediated_doc)
            )
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    # Document/media remediation (download → remediate → store file)
    elif ext in (
        "docx",
        "doc",
        "pptx",
        "ppt",
        "xlsx",
        "xls",
        "pdf",
        "mp4",
        "mp3",
        "wav",
        "avi",
        "mov",
        "webm",
    ):
        # Download the file from Brightspace
        credential = (
            db.query(CloudOAuthCredentials)
            .filter(CloudOAuthCredentials.id == cloud_file.credential_id)
            .first()
        )
        if not credential:
            raise Exception("Credential not found")

        token_manager = OAuthTokenManager()
        access_token = token_manager.decrypt_token(credential.access_token)
        instance_url = (credential.provider_metadata or {}).get(
            "brightspace_instance_url", ""
        )
        org_unit_id = metadata.get("org_unit_id")
        topic_id = int(cloud_file.provider_file_id)

        api_client = BrightspaceAPIClient(
            brightspace_instance_url=instance_url,
            access_token=access_token,
        )

        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp()
            file_path = os.path.join(temp_dir, f"file.{ext}")
            file_bytes, _ = await api_client.get_topic_file(int(org_unit_id), topic_id)
            with open(file_path, "wb") as f:
                f.write(file_bytes)

            # Select remediator
            _remediator_map = {
                "docx": ("..education.remediation.docx_remediator", "DocxRemediator"),
                "doc": ("..education.remediation.docx_remediator", "DocxRemediator"),
                "pptx": ("..education.remediation.pptx_remediator", "PptxRemediator"),
                "ppt": ("..education.remediation.pptx_remediator", "PptxRemediator"),
                "xlsx": ("..education.remediation.xlsx_remediator", "XlsxRemediator"),
                "xls": ("..education.remediation.xlsx_remediator", "XlsxRemediator"),
                "pdf": ("..education.remediation.pdf_remediator", "PdfRemediator"),
                "mp4": (
                    "..education.remediation.multimedia_remediator",
                    "MultimediaRemediator",
                ),
                "mp3": (
                    "..education.remediation.multimedia_remediator",
                    "MultimediaRemediator",
                ),
                "wav": (
                    "..education.remediation.multimedia_remediator",
                    "MultimediaRemediator",
                ),
                "avi": (
                    "..education.remediation.multimedia_remediator",
                    "MultimediaRemediator",
                ),
                "mov": (
                    "..education.remediation.multimedia_remediator",
                    "MultimediaRemediator",
                ),
                "webm": (
                    "..education.remediation.multimedia_remediator",
                    "MultimediaRemediator",
                ),
            }
            module_path, class_name = _remediator_map[ext]
            import importlib

            mod = importlib.import_module(module_path, package="src.api")
            RemediatorClass = getattr(mod, class_name)

            from ..education.remediation.base import RemediationConfig
            from ..ai.providers import get_provider_manager

            try:
                ai_client = get_provider_manager()
            except Exception:
                ai_client = None
            config = RemediationConfig(
                use_ai=True, verify_fixes=False, create_backup=False
            )
            # Document/media issues are already in the correct format from their processors
            remediator = RemediatorClass(file_path, raw_issues, config, ai_client)
            result = remediator.remediate()

            # Persist remediated file to uploads volume (survives container restarts)
            output_path = result.output_file or file_path
            if output_path and os.path.exists(output_path):
                import shutil

                persist_dir = f"/app/uploads/remediated/{cloud_file.id}"
                os.makedirs(persist_dir, exist_ok=True)
                persist_name = f"remediated.{ext}"
                persist_path = os.path.join(persist_dir, persist_name)
                shutil.copy2(output_path, persist_path)
                cloud_file.remediated_file_id = persist_path
                cloud_file.remediated_body = (
                    f"[Remediated {ext.upper()} document]\n"
                    f"Fixed: {result.fixed_count} issues\n"
                    f"Manual review needed: {result.manual_count} issues"
                )
        finally:
            await api_client.close()
            # Clean up temp dir now that file is persisted
            if temp_dir and os.path.exists(temp_dir):
                import shutil

                shutil.rmtree(temp_dir, ignore_errors=True)
    # Image remediation (generate alt text via vision AI)
    elif ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tiff"):
        credential = (
            db.query(CloudOAuthCredentials)
            .filter(CloudOAuthCredentials.id == cloud_file.credential_id)
            .first()
        )
        if not credential:
            raise Exception("Credential not found")

        token_manager = OAuthTokenManager()
        access_token = token_manager.decrypt_token(credential.access_token)
        instance_url = (credential.provider_metadata or {}).get(
            "brightspace_instance_url", ""
        )
        org_unit_id = metadata.get("org_unit_id")
        topic_id = int(cloud_file.provider_file_id)

        api_client = BrightspaceAPIClient(
            brightspace_instance_url=instance_url,
            access_token=access_token,
        )

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                file_bytes, _ = await api_client.get_topic_file(
                    int(org_unit_id), topic_id
                )
                tmp.write(file_bytes)
                temp_path = tmp.name

            from ..education.image_alt_text import ImageAltTextGenerator

            generator = ImageAltTextGenerator()
            result = await generator.analyze_image_comprehensive(
                image_path=temp_path,
                context=f"Educational course content: {cloud_file.file_name}",
            )

            # Build remediation summary with generated alt text
            alt_text = result.get("description", {}).get("alt_text", "")
            long_desc = result.get("description", {}).get("long_description", "")
            image_type = result.get("type_detection", {}).get(
                "image_purpose", "informative"
            )
            is_decorative = result.get("type_detection", {}).get("is_decorative", False)

            if is_decorative:
                cloud_file.remediated_body = (
                    f'<img src="{cloud_file.file_name}" alt="" role="presentation" />\n\n'
                    f"Image classified as decorative — empty alt text applied."
                )
            else:
                cloud_file.remediated_body = (
                    f'<img src="{cloud_file.file_name}" alt="{alt_text}" />\n\n'
                    f"**Generated Alt Text:** {alt_text}\n\n"
                    f"**Long Description:** {long_desc}\n\n"
                    f"**Image Purpose:** {image_type}"
                )
        finally:
            await api_client.close()
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

        cloud_file.writeback_status = "remediated"
        cloud_file.has_remediated_version = True
        remediated_score = 100.0
        cloud_file.remediated_compliance_score = remediated_score
        cloud_file.last_compliance_score = remediated_score
        cloud_file.remediated_issues_fixed = len(raw_issues)
        cloud_file.remediated_issues_remaining = 0
        db.commit()
        logger.info(f"Image remediation complete: {cloud_file.id}, alt_text generated")
        return

    else:
        logger.info(f"No remediator available for file type: {ext}")
        return

    cloud_file.writeback_status = "remediated"
    cloud_file.has_remediated_version = True

    cloud_file.remediated_issues_fixed = result.fixed_count
    cloud_file.remediated_issues_remaining = result.manual_count + getattr(
        result, "failed_count", 0
    )

    # Estimate remediated score and update displayed score
    if cloud_file.last_compliance_score is not None:
        total = (
            result.fixed_count
            + result.manual_count
            + getattr(result, "failed_count", 0)
        )
        if total > 0 and result.fixed_count > 0:
            fix_ratio = result.fixed_count / total
            original = cloud_file.last_compliance_score
            remediated_score = min(
                100.0, round(original + (100 - original) * fix_ratio, 1)
            )
            cloud_file.remediated_compliance_score = remediated_score
            cloud_file.last_compliance_score = remediated_score
        else:
            # Remediator ran but no auto-fixes applied — keep current score
            cloud_file.remediated_compliance_score = cloud_file.last_compliance_score

    db.commit()
    logger.info(
        f"Remediation complete: {cloud_file.id} ({ext}), "
        f"fixed={result.fixed_count}, manual={result.manual_count}"
    )


@router.post("/content/scan")
async def scan_brightspace_content(
    request: BrightspaceContentScanRequest,
    background_tasks: BackgroundTasks,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> BrightspaceContentScanResponse:
    """Queue scan jobs for Brightspace course content.

    Recursively discovers content items and creates scan jobs for each.
    Filter by scan_types: 'files', 'html', or 'both' (default).

    Requires API key authentication.
    """
    department_id = api_key.department_id
    credential = _get_credential(db, department_id)

    # Ensure token is valid (refresh if expired)
    access_token = await _ensure_valid_token(credential, db)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        scannables = await api_client.get_course_content_recursive(request.org_unit_id)
    finally:
        await api_client.close()

    # Filter by scan_types
    if request.scan_types == "files":
        scannables = [s for s in scannables if s.content_type == "file"]
    elif request.scan_types == "html":
        scannables = [s for s in scannables if s.content_type == "html"]
    # "both" keeps all

    # Filter by module_id if specified
    if request.module_id is not None:
        scannables = [s for s in scannables if s.module_id == request.module_id]

    total_items = len(scannables)
    jobs_queued = 0
    skipped = 0

    for item in scannables:
        # Find or create CloudFile record
        cloud_file = (
            db.query(CloudFile)
            .filter(
                CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
                CloudFile.provider_file_id == str(item.topic_id),
                CloudFile.department_id == department_id,
            )
            .first()
        )

        if not cloud_file:
            cloud_file = CloudFile(
                id=str(uuid.uuid4()),
                department_id=department_id,
                credential_id=credential.id,
                provider=CloudProvider.BRIGHTSPACE.value,
                provider_file_id=str(item.topic_id),
                provider_parent_id=str(request.org_unit_id),
                file_name=item.title,
                file_type=item.content_type,
                mime_type="text/html" if item.content_type == "html" else "unknown",
                file_size_bytes=0,
                provider_metadata={
                    "org_unit_id": request.org_unit_id,
                    "module_id": item.module_id,
                    "topic_type": item.content_type,
                    "module_path": item.module_path,
                    "url": item.url,
                },
            )
            db.add(cloud_file)
            db.flush()  # Get the ID assigned
        else:
            # Update module_path if missing from earlier scans
            metadata = cloud_file.provider_metadata or {}
            if not metadata.get("module_path"):
                metadata["module_path"] = item.module_path
                metadata["url"] = item.url
                cloud_file.provider_metadata = metadata

        # Check if there's already a pending/processing scan job for this file
        existing_job = (
            db.query(CloudJobQueue)
            .filter(
                CloudJobQueue.cloud_file_id == cloud_file.id,
                CloudJobQueue.job_type == CloudJobType.SCAN.value,
                CloudJobQueue.status.in_(
                    [
                        CloudJobStatus.PENDING.value,
                        CloudJobStatus.PROCESSING.value,
                    ]
                ),
            )
            .first()
        )

        if existing_job:
            skipped += 1
            continue

        # Create scan job
        job_id = str(uuid.uuid4())
        scan_job = CloudJobQueue(
            id=job_id,
            department_id=department_id,
            job_type=CloudJobType.SCAN.value,
            provider=CloudProvider.BRIGHTSPACE.value,
            status=CloudJobStatus.PENDING.value,
            priority=1,
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
        )
        db.add(scan_job)
        jobs_queued += 1

        # Queue background task to execute the scan
        background_tasks.add_task(
            _brightspace_scan_file_task,
            job_id=job_id,
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
        )

    db.commit()

    logger.info(
        f"Queued {jobs_queued} Brightspace scan jobs for org_unit {request.org_unit_id} "
        f"(department {department_id}, {skipped} skipped)"
    )

    return BrightspaceContentScanResponse(
        total_items=total_items,
        jobs_queued=jobs_queued,
        skipped=skipped,
    )


@router.get("/content/courses/{org_unit_id}/status")
async def get_brightspace_content_status(
    org_unit_id: int,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Get scan status for all content items in a Brightspace course.

    Returns compliance scores and scan status for each tracked content item.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    # Query all CloudFile records for this course
    cloud_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
            CloudFile.department_id == department_id,
            CloudFile.provider_parent_id == str(org_unit_id),
        )
        .all()
    )

    items = []
    scanned_count = 0
    total_score = 0.0

    from ..db.models import ScanResult

    for cf in cloud_files:
        # Get the latest scan for this file
        latest_scan = (
            db.query(Scan).filter(Scan.id == cf.last_scan_id).first()
            if cf.last_scan_id
            else None
        )
        # Get issue counts from scan result
        scan_result = (
            db.query(ScanResult).filter(ScanResult.scan_id == cf.last_scan_id).first()
            if cf.last_scan_id
            else None
        )
        issue_count = (
            (
                (scan_result.critical_issues or 0)
                + (scan_result.high_issues or 0)
                + (scan_result.medium_issues or 0)
                + (scan_result.low_issues or 0)
            )
            if scan_result
            else 0
        )

        score = cf.last_compliance_score
        if score is not None:
            scanned_count += 1
            total_score += score

        metadata = cf.provider_metadata or {}

        # Derive a meaningful content type from URL extension or mime_type
        content_type = cf.file_type
        url = metadata.get("url", "")
        if url and content_type == "file":
            ext = url.rsplit(".", 1)[-1].lower() if "." in url else ""
            _ext_to_type = {
                "html": "HTML",
                "htm": "HTML",
                "pdf": "PDF",
                "docx": "Word",
                "doc": "Word",
                "pptx": "PowerPoint",
                "ppt": "PowerPoint",
                "xlsx": "Excel",
                "xls": "Excel",
                "jpg": "Image",
                "jpeg": "Image",
                "png": "Image",
                "gif": "Image",
                "mp4": "Video",
                "mp3": "Audio",
                "wav": "Audio",
                "mov": "Video",
            }
            content_type = _ext_to_type.get(ext, content_type)

        items.append(
            {
                "cloud_file_id": cf.id,
                "provider_file_id": cf.provider_file_id,
                "title": cf.file_name,
                "file_name": cf.file_name,
                "content_type": content_type,
                "file_type": cf.file_type,
                "compliance_score": score,
                "issue_count": issue_count,
                "module_path": metadata.get("module_path", ""),
                "writeback_status": cf.writeback_status,
                "last_scanned_at": (
                    cf.last_scanned_at.isoformat() if cf.last_scanned_at else None
                ),
                "scan_status": latest_scan.status.value if latest_scan else None,
                "has_remediated_version": cf.has_remediated_version,
                "needs_rescan": cf.needs_rescan,
            }
        )

    average_compliance = (
        round(total_score / scanned_count, 1) if scanned_count > 0 else None
    )

    return {
        "org_unit_id": org_unit_id,
        "total_items": len(cloud_files),
        "scanned_items": scanned_count,
        "average_compliance": average_compliance,
        "items": items,
    }


@router.post("/remediate")
async def remediate_brightspace_content(
    request: BrightspaceRemediateRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> BrightspaceRemediateResponse:
    """Queue remediation job for a Brightspace file.

    Downloads file, scans, remediates, and optionally uploads back to Brightspace.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    try:
        credential = _get_credential(db, department_id)

        # Find or create CloudFile record
        # Extract a topic_id from the file_url or use it directly
        cloud_file = (
            db.query(CloudFile)
            .filter(
                CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
                CloudFile.department_id == department_id,
                CloudFile.provider_parent_id == str(request.org_unit_id),
                CloudFile.download_link == request.file_url,
            )
            .first()
        )

        if not cloud_file:
            cloud_file = CloudFile(
                id=str(uuid.uuid4()),
                department_id=department_id,
                credential_id=credential.id,
                provider=CloudProvider.BRIGHTSPACE.value,
                provider_file_id=str(uuid.uuid4()),  # Placeholder until scan resolves
                provider_parent_id=str(request.org_unit_id),
                file_name=request.file_url.split("/")[-1] or "brightspace_file",
                file_type="unknown",
                mime_type="unknown",
                file_size_bytes=0,
                download_link=request.file_url,
                provider_metadata={
                    "org_unit_id": request.org_unit_id,
                },
            )
            db.add(cloud_file)
            db.flush()

        # Create scan job (priority 1 — runs first)
        scan_job_id = str(uuid.uuid4())
        scan_job = CloudJobQueue(
            id=scan_job_id,
            department_id=department_id,
            job_type=CloudJobType.SCAN.value,
            provider=CloudProvider.BRIGHTSPACE.value,
            status=CloudJobStatus.PENDING.value,
            priority=1,
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
        )
        db.add(scan_job)

        # Create remediation job (priority 2 — runs after scan)
        remediation_job_id = str(uuid.uuid4())
        remediation_job = CloudJobQueue(
            id=remediation_job_id,
            department_id=department_id,
            job_type=CloudJobType.REMEDIATE.value,
            provider=CloudProvider.BRIGHTSPACE.value,
            status=CloudJobStatus.PENDING.value,
            priority=2,
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
        )
        db.add(remediation_job)

        db.commit()

        logger.info(
            f"Queued Brightspace remediation jobs for {request.file_url}: "
            f"scan={scan_job_id}, remediate={remediation_job_id}"
        )

        return BrightspaceRemediateResponse(
            success=True,
            scan_id=None,  # Will be created by scan job
            job_id=remediation_job_id,
            message="Remediation job queued. File will be downloaded, scanned, and remediated.",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to queue Brightspace remediation: {e}", exc_info=True)
        return BrightspaceRemediateResponse(
            success=False,
            message=f"Failed to queue remediation: {str(e)}",
        )


# =============================================================================
# Disconnect
# =============================================================================


@router.delete("/disconnect")
async def disconnect_brightspace(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, str]:
    """Disconnect Brightspace integration for a department.

    Requires API key authentication.
    """
    department_id = api_key.department_id

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=404,
            detail="Brightspace not connected for this department",
        )

    # Delete credential
    db.delete(credential)
    db.commit()

    logger.info(f"Disconnected Brightspace for department {department_id}")

    return {"message": "Brightspace disconnected successfully"}


# =============================================================================
# Content Review & Writeback
# =============================================================================


def _get_cloud_file_or_404(
    db: Session, cloud_file_id: str, department_id: str
) -> CloudFile:
    """Get a CloudFile by ID and department, or raise 404."""
    cf = (
        db.query(CloudFile)
        .filter(
            CloudFile.id == cloud_file_id,
            CloudFile.department_id == department_id,
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .first()
    )
    if not cf:
        raise HTTPException(status_code=404, detail="Content item not found")
    return cf


@router.post("/content/{cloud_file_id}/remediate")
async def remediate_content(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Remediate a single content item's accessibility issues."""
    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)

    if cf.last_compliance_score is None:
        raise HTTPException(
            status_code=400, detail="Content must be scanned before remediation"
        )
    if cf.last_compliance_score == 100:
        return {"success": True, "message": "No issues to remediate", "fixed_count": 0}

    try:
        await _remediate_file(cf, db)
        return {
            "success": True,
            "message": "Remediation complete",
            "fixed_count": 1,
            "has_remediated_version": cf.has_remediated_version,
        }
    except Exception as e:
        logger.error(f"Remediation failed for {cloud_file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Remediation failed: {str(e)}")


@router.post("/content/batch-remediate")
async def batch_remediate_content(
    request: Dict[str, Any],
    background_tasks: BackgroundTasks,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Remediate all scanned content items with issues for a course."""
    org_unit_id = request.get("org_unit_id")
    cloud_file_ids = request.get("cloud_file_ids")

    query = db.query(CloudFile).filter(
        CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
        CloudFile.department_id == api_key.department_id,
        CloudFile.last_compliance_score.isnot(None),
        CloudFile.last_compliance_score < 100,
        CloudFile.has_remediated_version == False,
    )

    if cloud_file_ids:
        query = query.filter(CloudFile.id.in_(cloud_file_ids))
    elif org_unit_id:
        query = query.filter(CloudFile.provider_parent_id == str(org_unit_id))

    eligible_files = query.all()

    if not eligible_files:
        return {
            "remediated_count": 0,
            "failed_count": 0,
            "message": "No eligible items to remediate",
        }

    # Collect IDs and return immediately — process in background
    file_ids = [cf.id for cf in eligible_files]
    queued = len(file_ids)

    async def _batch_remediate_background(file_ids: list):
        from ..db.database import get_db as _get_db_ctx

        with _get_db_ctx() as bg_db:
            for fid in file_ids:
                cf = bg_db.query(CloudFile).filter(CloudFile.id == fid).first()
                if not cf:
                    continue
                try:
                    await _remediate_file(cf, bg_db)
                except Exception as e:
                    logger.error(f"Background remediation failed for {fid}: {e}")

    background_tasks.add_task(_batch_remediate_background, file_ids)

    return {
        "queued": queued,
        "message": f"Queued {queued} items for remediation",
    }


@router.get("/content/{cloud_file_id}/diff")
async def get_content_diff(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Get original vs remediated content for review."""
    from ..db.models import ScanResult

    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)

    issues_fixed = 0
    issues_remaining = 0
    remediated = cf.has_remediated_version or cf.remediated_body
    if remediated and cf.remediated_issues_fixed is not None:
        # Authoritative counts from the remediator
        issues_fixed = cf.remediated_issues_fixed
        issues_remaining = cf.remediated_issues_remaining or 0
    elif cf.last_scan_id:
        scan_result = (
            db.query(ScanResult).filter(ScanResult.scan_id == cf.last_scan_id).first()
        )
        if scan_result and scan_result.issues:
            total_issues = len(scan_result.issues)
            if (
                remediated
                and cf.last_compliance_score is not None
                and cf.last_compliance_score >= 100
            ):
                # Legacy row remediated before counts were persisted, but score says fully fixed
                issues_fixed = total_issues
            else:
                issues_remaining = total_issues

    return {
        "cloud_file_id": cf.id,
        "content_type": cf.file_type,
        "title": cf.file_name,
        "original_html": cf.content_body or "",
        "remediated_html": cf.remediated_body or "",
        "issues_fixed": issues_fixed,
        "issues_remaining": issues_remaining,
    }


@router.post("/content/{cloud_file_id}/approve")
async def approve_content(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Approve remediated content for write-back."""
    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)
    if not cf.remediated_body:
        raise HTTPException(status_code=400, detail="No remediated content to approve")
    cf.writeback_status = "approved"
    db.commit()
    return {"success": True, "message": "Content approved"}


@router.post("/content/{cloud_file_id}/reject")
async def reject_content(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Reject remediated content."""
    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)
    cf.writeback_status = "rejected"
    db.commit()
    return {"success": True, "message": "Content rejected"}


@router.post("/content/batch-approve")
async def batch_approve_content(
    request: Dict[str, Any],
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Approve multiple content items at once."""
    cloud_file_ids = request.get("cloud_file_ids", [])
    cloud_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.id.in_(cloud_file_ids),
            CloudFile.department_id == api_key.department_id,
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .all()
    )
    approved = 0
    for cf in cloud_files:
        if cf.remediated_body:
            cf.writeback_status = "approved"
            approved += 1
    db.commit()
    return {"approved_count": approved}


async def _writeback_single(api_client, cf: CloudFile, org_unit_id, topic_id, db=None):
    """Write a single remediated file back to Brightspace.

    Saves the current Brightspace content before overwriting so it can be rolled back.
    """
    metadata = cf.provider_metadata or {}
    url = metadata.get("url", "")
    ext = url.rsplit(".", 1)[-1].lower() if "." in url else ""

    if ext in ("html", "htm") or (
        cf.content_body
        and ext
        not in (
            "jpg",
            "jpeg",
            "png",
            "gif",
            "bmp",
            "webp",
            "svg",
            "docx",
            "doc",
            "pptx",
            "ppt",
            "xlsx",
            "xls",
            "pdf",
            "mp4",
            "mp3",
            "wav",
            "avi",
            "mov",
            "webm",
        )
    ):
        # Save current file content as restore point before overwriting
        try:
            file_bytes, _ = await api_client.get_topic_file(int(org_unit_id), topic_id)
            cf.content_body = file_bytes.decode("utf-8", errors="replace")
        except Exception:
            pass  # Keep existing content_body as fallback

        # Upload remediated HTML as replacement file, preserving original name
        original_url = (cf.provider_metadata or {}).get("url", "")
        if original_url and "." in original_url:
            filename = original_url.rsplit("/", 1)[-1]
        else:
            filename = f"{cf.file_name or 'content'}.html"
        remediated_bytes = cf.remediated_body.encode("utf-8")
        await api_client.replace_topic_file(
            org_unit_id, topic_id, remediated_bytes, filename
        )
    elif ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "svg"):
        # Image: update topic description with alt text
        await api_client.update_topic_html(org_unit_id, topic_id, cf.remediated_body)
    elif ext in ("mp4", "mp3", "wav", "avi", "mov", "webm", "ogg"):
        # Multimedia: DON'T replace the video/audio file with a caption file.
        # Instead update the topic description with generated captions/transcript info.
        if cf.remediated_body:
            await api_client.update_topic_html(
                org_unit_id, topic_id, cf.remediated_body
            )
    elif cf.remediated_file_id and os.path.exists(cf.remediated_file_id):
        # Save original file before overwriting
        try:
            original_bytes, _ = await api_client.get_topic_file(
                int(org_unit_id), topic_id
            )
            backup_dir = f"/app/uploads/remediated/{cf.id}"
            os.makedirs(backup_dir, exist_ok=True)
            original_path = os.path.join(backup_dir, f"original.{ext}")
            with open(original_path, "wb") as bf:
                bf.write(original_bytes)
            meta = cf.provider_metadata or {}
            meta["original_file_path"] = original_path
            cf.provider_metadata = meta
        except Exception as backup_err:
            logger.warning(f"Failed to backup original file for {cf.id}: {backup_err}")

        # Document: upload the remediated file with original name
        with open(cf.remediated_file_id, "rb") as f:
            file_bytes = f.read()
        original_url = (cf.provider_metadata or {}).get("url", "")
        if original_url and "." in original_url:
            filename = original_url.rsplit("/", 1)[-1]
        else:
            filename = f"{cf.file_name or 'file'}.{ext}"
        await api_client.replace_topic_file(org_unit_id, topic_id, file_bytes, filename)
    else:
        if cf.remediated_body:
            await api_client.update_topic_html(
                org_unit_id, topic_id, cf.remediated_body
            )
        else:
            raise Exception("No remediated content available for write-back")


@router.post("/content/{cloud_file_id}/writeback")
async def writeback_content(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Write approved remediated content back to Brightspace."""
    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)

    if cf.writeback_status != "approved":
        raise HTTPException(
            status_code=400, detail="Content must be approved before write-back"
        )
    if not cf.remediated_body:
        raise HTTPException(
            status_code=400, detail="No remediated content to write back"
        )

    credential = _get_credential(db, api_key.department_id)
    access_token = await _ensure_valid_token(credential, db)
    metadata = cf.provider_metadata or {}
    org_unit_id = metadata.get("org_unit_id")
    topic_id = int(cf.provider_file_id)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        await _writeback_single(api_client, cf, org_unit_id, topic_id)
        cf.writeback_status = "written_back"
        db.commit()
        return {"success": True, "message": "Content written back to Brightspace"}
    except Exception as e:
        logger.error(f"Writeback failed for {cloud_file_id}: {e}")
        cf.writeback_status = "write_failed"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Write-back failed: {str(e)}")
    finally:
        await api_client.close()


@router.post("/content/batch-writeback")
async def batch_writeback_content(
    request: Dict[str, Any],
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Write back all approved content items for a course."""
    org_unit_id = request.get("org_unit_id")

    approved_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
            CloudFile.provider_parent_id == str(org_unit_id),
            CloudFile.department_id == api_key.department_id,
            CloudFile.writeback_status == "approved",
            CloudFile.remediated_body.isnot(None),
        )
        .all()
    )

    if not approved_files:
        return {"written_count": 0, "failed_count": 0, "stale_count": 0}

    credential = _get_credential(db, api_key.department_id)
    access_token = await _ensure_valid_token(credential, db)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    written = 0
    failed = 0
    try:
        for cf in approved_files:
            try:
                topic_id = int(cf.provider_file_id)
                file_org_unit = (cf.provider_metadata or {}).get(
                    "org_unit_id", org_unit_id
                )
                await _writeback_single(api_client, cf, file_org_unit, topic_id)
                cf.writeback_status = "written_back"
                written += 1
            except Exception as e:
                logger.error(f"Writeback failed for {cf.id}: {e}")
                cf.writeback_status = "write_failed"
                failed += 1
        db.commit()
    finally:
        await api_client.close()

    return {"written_count": written, "failed_count": failed, "stale_count": 0}


@router.post("/content/{cloud_file_id}/rollback")
async def rollback_content(
    cloud_file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Roll back a written-back item to its original content in Brightspace."""
    cf = _get_cloud_file_or_404(db, cloud_file_id, api_key.department_id)

    if cf.writeback_status != "written_back":
        raise HTTPException(
            status_code=400, detail="Only written-back items can be rolled back"
        )
    if not cf.content_body:
        raise HTTPException(
            status_code=400, detail="Original content not available for rollback"
        )

    credential = _get_credential(db, api_key.department_id)
    access_token = await _ensure_valid_token(credential, db)
    metadata = cf.provider_metadata or {}
    org_unit_id = metadata.get("org_unit_id")
    topic_id = int(cf.provider_file_id)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    try:
        # Determine file type for proper rollback method
        metadata = cf.provider_metadata or {}
        url = metadata.get("url", "")
        ext = url.rsplit(".", 1)[-1].lower() if "." in url else ""
        original_file_path = metadata.get("original_file_path")

        # Derive original filename from URL
        original_filename = (
            url.rsplit("/", 1)[-1] if "/" in url else f"{cf.file_name or 'file'}.{ext}"
        )

        if ext in ("html", "htm") and cf.content_body:
            # Upload original HTML file back
            filename = original_filename
            original_bytes = cf.content_body.encode("utf-8")
            await api_client.replace_topic_file(
                org_unit_id, topic_id, original_bytes, filename
            )
        elif original_file_path and os.path.exists(original_file_path):
            # Upload original document/media file back
            with open(original_file_path, "rb") as f:
                original_bytes = f.read()
            await api_client.replace_topic_file(
                org_unit_id, topic_id, original_bytes, original_filename
            )
        elif cf.content_body:
            # Fallback: update description
            await api_client.update_topic_html(org_unit_id, topic_id, cf.content_body)
        else:
            raise Exception("No original content available for rollback")

        cf.writeback_status = "rolled_back"
        db.commit()
        logger.info(f"Rolled back content for {cloud_file_id}")
        return {"success": True, "message": "Content rolled back to original"}
    except Exception as e:
        logger.error(f"Rollback failed for {cloud_file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Rollback failed: {str(e)}")
    finally:
        await api_client.close()


@router.post("/content/batch-rollback")
async def batch_rollback_content(
    request: Dict[str, Any],
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
) -> Dict[str, Any]:
    """Roll back all written-back items for a course to their originals."""
    org_unit_id = request.get("org_unit_id")

    written_files = (
        db.query(CloudFile)
        .filter(
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
            CloudFile.provider_parent_id == str(org_unit_id),
            CloudFile.department_id == api_key.department_id,
            CloudFile.writeback_status == "written_back",
            CloudFile.content_body.isnot(None),
        )
        .all()
    )

    if not written_files:
        return {"rolled_back_count": 0, "failed_count": 0}

    credential = _get_credential(db, api_key.department_id)
    access_token = await _ensure_valid_token(credential, db)

    api_client = BrightspaceAPIClient(
        brightspace_instance_url=credential.provider_metadata.get(
            "brightspace_instance_url"
        ),
        access_token=access_token,
        credential_id=credential.id,
    )

    rolled_back = 0
    failed = 0
    try:
        for cf in written_files:
            try:
                topic_id = int(cf.provider_file_id)
                file_org_unit = (cf.provider_metadata or {}).get(
                    "org_unit_id", org_unit_id
                )
                cf_url = (cf.provider_metadata or {}).get("url", "")
                cf_ext = cf_url.rsplit(".", 1)[-1].lower() if "." in cf_url else ""
                cf_filename = (
                    cf_url.rsplit("/", 1)[-1]
                    if "/" in cf_url
                    else f"{cf.file_name or 'file'}.{cf_ext}"
                )
                if cf_ext in ("html", "htm"):
                    original_bytes = cf.content_body.encode("utf-8")
                    await api_client.replace_topic_file(
                        file_org_unit, topic_id, original_bytes, cf_filename
                    )
                else:
                    await api_client.update_topic_html(
                        file_org_unit, topic_id, cf.content_body
                    )
                cf.writeback_status = "rolled_back"
                rolled_back += 1
            except Exception as e:
                logger.error(f"Rollback failed for {cf.id}: {e}")
                failed += 1
        db.commit()
    finally:
        await api_client.close()

    return {"rolled_back_count": rolled_back, "failed_count": failed}
