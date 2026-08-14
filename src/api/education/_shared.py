"""Shared helpers, models, and constants for education routes."""

import asyncio
import hashlib
import logging
import uuid
from typing import List, Optional

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ...auth.dependencies import get_required_api_key
from ...db.database import get_db_dependency  # noqa: F401 — re-exported for route files
from ...db.models import (
    APIKey,  # noqa: F401 — re-exported for route files
    Scan,  # noqa: F401 — re-exported for route files
    ScanStatus,  # noqa: F401 — re-exported for route files
    SecurityScanResult,
)
from ...middleware.quota import (
    check_quota,
    increment_usage,  # noqa: F401 — re-exported for route files
    require_feature,  # noqa: F401 — re-exported for route files
    check_image_quota,
    increment_image_usage,  # noqa: F401 — re-exported for route files
)
from ...security.document_validator import ThreatLevel, validate_document

logger = logging.getLogger(__name__)

# Auth alias used by all route files
get_api_key_or_mock = get_required_api_key

MAX_SCANFIX_ISSUES = 50  # Cap persisted issues per scan to limit DB/review load

APPROVED_REVIEW_STATUSES = {"approved", "edited", "auto_approved"}


def _run_in_thread(sync_func, *args):
    """Wrap a sync background task to run in a thread pool, keeping the event loop free."""

    async def _wrapper():
        await asyncio.to_thread(sync_func, *args)

    return _wrapper


def _stable_hash(text: str) -> str:
    """Return a deterministic short hash for issue ID generation.

    Python's built-in hash() is randomized per process (PYTHONHASHSEED),
    so the same description produces different IDs across restarts.
    This uses MD5 (not for security, just uniqueness) truncated to 8 hex chars.
    """
    return hashlib.md5(text.encode()).hexdigest()[:8]


class RemediationOptions(BaseModel):
    """Options for remediation request."""

    use_ai: bool = Field(default=True, description="Use AI for generating fixes")

    # LaTeX options
    latex_formats: List[str] = Field(
        default=["tex", "pdf", "html"],
        description="Output formats for LaTeX: tex, pdf, html (can specify multiple)",
    )

    # Multimedia options
    multimedia_format: str = Field(
        default="individual",
        description="Output format for multimedia: individual or zip",
    )
    include_original_in_zip: bool = Field(
        default=True,
        description="Include original media file in ZIP archive",
    )

    @field_validator("latex_formats")
    @classmethod
    def validate_latex_formats(cls, v: List[str]) -> List[str]:
        """Validate LaTeX formats are valid options."""
        valid_formats = {"tex", "pdf", "html"}
        for fmt in v:
            if fmt.lower() not in valid_formats:
                raise ValueError(
                    f"Invalid LaTeX format '{fmt}'. Must be one of: tex, pdf, html"
                )
        return [fmt.lower() for fmt in v]

    @field_validator("multimedia_format")
    @classmethod
    def validate_multimedia_format(cls, v: str) -> str:
        """Validate multimedia format is valid."""
        valid_formats = {"individual", "zip"}
        if v.lower() not in valid_formats:
            raise ValueError(
                f"Invalid multimedia format '{v}'. Must be 'individual' or 'zip'"
            )
        return v.lower()


async def check_scan_quota(
    db: Session,
    department_id: str,
    pages: int = 0,
    feature: str = None,
) -> None:
    """
    Check if department has quota for this scan operation.

    Args:
        db: Database session
        department_id: Department ID to check
        pages: Number of pages that will be processed
        feature: Feature being accessed (for feature gating)

    Raises:
        HTTPException 429: If quota is exceeded
        HTTPException 403: If feature is not available on this tier
    """
    # Skip quota check for mock auth (check prefix for dynamic session IDs)
    from ...config.settings import get_settings

    settings = get_settings()
    if (
        department_id
        and department_id.startswith("dev-dept-")
        and settings.env.lower() == "development"
    ):
        return

    result = await check_quota(db, department_id, pages)

    if not result.allowed:
        logger.warning(
            f"Quota exceeded for department {department_id}: {result.message}"
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "message": result.message,
                "remaining": result.to_dict()["remaining"],
                "resets_at": result.resets_at.isoformat() if result.resets_at else None,
            },
        )


async def check_image_analysis_quota(
    db: Session,
    department_id: str,
    count: int = 1,
) -> None:
    """
    Check if department has quota for image analysis operations.

    Args:
        db: Database session
        department_id: Department ID to check
        count: Number of images being analyzed

    Raises:
        HTTPException 429: If image quota is exceeded
    """
    # Skip quota check for mock auth (check prefix for dynamic session IDs)
    from ...config.settings import get_settings

    settings = get_settings()
    if (
        department_id
        and department_id.startswith("dev-dept-")
        and settings.env.lower() == "development"
    ):
        return

    result = await check_image_quota(db, department_id, count)

    if not result.allowed:
        logger.warning(
            f"Image quota exceeded for department {department_id}: {result.message}"
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "image_quota_exceeded",
                "message": result.message,
                "remaining_images": result.remaining_scans,  # Reused field
                "resets_at": result.resets_at.isoformat() if result.resets_at else None,
            },
        )


async def validate_uploaded_file(
    file: UploadFile,
    db: Session,
    department_id: str,
    scan_id: Optional[str] = None,
    allow_sanitized: bool = True,
) -> bytes:
    """
    Validate an uploaded file for security threats.

    This function checks for:
    - Malicious macros in Office documents
    - Suspicious PDF elements (JavaScript, auto-actions, etc.)
    - File type spoofing (magic byte validation)
    - Zip bombs and archive attacks
    - Prompt injection patterns

    Args:
        file: The uploaded file
        db: Database session for logging
        department_id: Department ID for logging
        scan_id: Optional scan ID to associate with security result
        allow_sanitized: If True, sanitize and allow medium-risk files

    Returns:
        File content as bytes (possibly sanitized)

    Raises:
        HTTPException 400: If file fails security validation
    """
    # Check Content-Length before reading full file into memory
    from ...config.settings import get_settings

    settings = get_settings()
    content_length = getattr(file, "size", None)
    if content_length and content_length > getattr(
        settings, "max_file_size_pdf", 50 * 1024 * 1024
    ):
        raise HTTPException(status_code=400, detail="File too large")

    # Read file content
    content = await file.read()
    await file.seek(0)  # Reset for potential re-read

    # Enforce size limit after read (in case Content-Length was missing/wrong)
    max_size = getattr(settings, "max_file_size_pdf", 50 * 1024 * 1024)
    if len(content) > max_size:
        raise HTTPException(status_code=400, detail="File too large")

    filename = file.filename or "unknown"

    # Skip validation for mock auth in dev (check prefix for dynamic session IDs)
    if (
        department_id
        and department_id.startswith("dev-dept-")
        and settings.env.lower() == "development"
    ):
        logger.debug(f"Skipping security validation for mock auth: {filename}")
        return content

    try:
        # Validate the document
        result = await validate_document(filename, content)

        # Log security scan result
        security_result = SecurityScanResult(
            id=str(uuid.uuid4()),
            scan_id=scan_id,
            department_id=department_id,
            filename=filename,
            file_hash=result.file_hash,
            file_type=result.file_type,
            file_size=len(content),
            is_safe=result.is_safe,
            threat_level=result.threat_level.value,
            findings=(
                [
                    (
                        f.to_dict()
                        if hasattr(f, "to_dict")
                        else {
                            "category": f.category,
                            "description": f.description,
                            "threat_level": f.threat_level.value,
                            "details": f.details,
                        }
                    )
                    for f in result.findings
                ]
                if result.findings
                else None
            ),
            was_sanitized=False,
            was_blocked=not result.is_safe,
            blocked_reason=(
                result.findings[0].description
                if result.findings and not result.is_safe
                else None
            ),
        )
        db.add(security_result)
        db.commit()

        # Handle threats based on severity
        if result.threat_level == ThreatLevel.CRITICAL:
            logger.warning(
                f"CRITICAL security threat detected in file '{filename}' "
                f"from department {department_id}: {result.findings[0].description if result.findings else 'Unknown'}"
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "security_threat_detected",
                    "threat_level": "critical",
                    "message": "This file contains critical security threats and cannot be processed.",
                    "findings": [
                        {
                            "category": f.category,
                            "description": f.description,
                            "threat_level": f.threat_level.value,
                        }
                        for f in result.findings
                        if f.threat_level in [ThreatLevel.CRITICAL, ThreatLevel.HIGH]
                    ],
                },
            )

        if result.threat_level == ThreatLevel.HIGH:
            if allow_sanitized:
                # Try to sanitize the document
                from ...security.document_validator import sanitize_document

                sanitized_content, sanitized_result = await sanitize_document(
                    filename, content
                )

                # Update security result
                security_result.was_sanitized = True
                db.commit()

                logger.info(
                    f"Sanitized high-risk file '{filename}' from department {department_id}"
                )
                return sanitized_content
            else:
                logger.warning(
                    f"HIGH security threat detected in file '{filename}' "
                    f"from department {department_id}"
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "security_threat_detected",
                        "threat_level": "high",
                        "message": "This file contains security threats. Please remove macros or suspicious elements.",
                        "findings": [
                            {
                                "category": f.category,
                                "description": f.description,
                                "remediation": f.remediation,
                            }
                            for f in result.findings
                            if f.threat_level in [ThreatLevel.HIGH, ThreatLevel.MEDIUM]
                        ],
                    },
                )

        # MEDIUM and LOW threats are logged but allowed
        if result.threat_level == ThreatLevel.MEDIUM:
            logger.info(
                f"Medium-risk elements detected in file '{filename}' "
                f"from department {department_id}: "
                f"{', '.join(f.description for f in result.findings if f.threat_level == ThreatLevel.MEDIUM)}"
            )

        return content

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Security validation error for file '{filename}': {e}")
        # Fail closed: reject files that can't be validated
        raise HTTPException(
            status_code=400,
            detail="File could not be validated. Please try again or contact support.",
        )
