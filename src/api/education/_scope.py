"""Authorization helpers shared by education scan and remediation routes."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...auth.dependencies import AuthenticatedPrincipal
from ...db.models import CloudFile, CloudProvider, Scan


def require_supported_scan_course(principal: AuthenticatedPrincipal) -> None:
    """Course IDs from different LMS platforms are not interchangeable."""
    if (
        principal.auth_method == "lti"
        and not principal.lti_account_wide
        and principal.lti_platform != "canvas"
    ):
        raise HTTPException(status_code=404, detail="Scan not found")


def authorize_scan_access(
    db: Session, scan: Scan, principal: AuthenticatedPrincipal
) -> CloudFile | None:
    """Authorize a scan before any read, mutation, or external side effect.

    Non-LTI and account-wide LTI administrators retain department-level access.
    Course-scoped LTI staff may access only scans linked to a Canvas CloudFile in
    their exact launch course. Missing, generic, non-Canvas, and cross-tenant
    links are deliberately hidden as a scan-level 404.
    """
    require_supported_scan_course(principal)
    return _authorize_scan_access(db, scan, principal, course_provider="canvas")


def authorize_review_scan_access(
    db: Session, scan: Scan, principal: AuthenticatedPrincipal
) -> CloudFile | None:
    """Authorize explicit document review for the exact LMS launch platform."""
    provider = principal.lti_platform
    if principal.auth_method == "lti" and not principal.lti_account_wide:
        if provider not in {
            CloudProvider.CANVAS.value,
            CloudProvider.BRIGHTSPACE.value,
        }:
            raise HTTPException(status_code=404, detail="Scan not found")
    return _authorize_scan_access(db, scan, principal, course_provider=provider)


def _authorize_scan_access(
    db: Session,
    scan: Scan,
    principal: AuthenticatedPrincipal,
    *,
    course_provider: str | None,
) -> CloudFile | None:
    if scan.department_id != principal.department_id:
        if principal.auth_method == "lti":
            raise HTTPException(status_code=404, detail="Scan not found")
        raise HTTPException(status_code=403, detail="Access denied")

    if principal.auth_method != "lti" or principal.lti_account_wide:
        return None

    if getattr(scan, "document_source", None) != "cloud_file" or not getattr(
        scan, "document_id", None
    ):
        raise HTTPException(status_code=404, detail="Scan not found")

    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.last_scan_id == scan.id,
            CloudFile.department_id == principal.department_id,
            CloudFile.provider == course_provider,
            CloudFile.provider_parent_id == principal.lti_course_id,
        )
        .first()
    )
    if (
        not cloud_file
        or cloud_file.last_scan_id != scan.id
        or cloud_file.department_id != principal.department_id
        or cloud_file.provider != course_provider
        or str(cloud_file.id) != str(scan.document_id)
        or str(cloud_file.provider_parent_id) != str(principal.lti_course_id)
    ):
        raise HTTPException(status_code=404, detail="Scan not found")

    return cloud_file
