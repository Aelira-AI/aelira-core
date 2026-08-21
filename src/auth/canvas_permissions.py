"""Pure authorization policies for LMS access."""

from fastapi import HTTPException, status

from ..db.models import UserRole
from .dependencies import AuthenticatedPrincipal

_FORBIDDEN = "Forbidden"


def _deny() -> None:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)


def require_canvas_staff(
    principal: AuthenticatedPrincipal,
) -> AuthenticatedPrincipal:
    """Require an authenticated non-LTI user or validated LTI staff principal."""

    if principal.auth_method == "lti" and principal.lti_staff_role not in {
        "Administrator",
        "Instructor",
        "TeachingAssistant",
        "ContentDeveloper",
    }:
        _deny()
    return principal


def require_lti_platform_access(
    principal: AuthenticatedPrincipal,
    platform: str,
) -> AuthenticatedPrincipal:
    """Bind an LTI principal to the LMS that minted its launch token."""

    if principal.auth_method == "lti" and principal.lti_platform != platform:
        _deny()
    return principal


def require_lti_course_access(
    principal: AuthenticatedPrincipal,
    course_id: str,
    platform: str = "canvas",
) -> AuthenticatedPrincipal:
    """Apply provider/course scope to LTI users without changing other auth."""

    require_canvas_staff(principal)
    require_lti_platform_access(principal, platform)
    if principal.auth_method != "lti":
        return principal
    if not isinstance(course_id, str) or not course_id.strip():
        _deny()
    if principal.lti_account_wide:
        return principal
    if principal.lti_course_id != course_id:
        _deny()
    return principal


def require_lti_account_access(
    principal: AuthenticatedPrincipal,
    platform: str = "canvas",
) -> AuthenticatedPrincipal:
    """Require provider-bound account scope for LTI users."""

    require_canvas_staff(principal)
    require_lti_platform_access(principal, platform)
    if principal.auth_method == "lti" and not principal.lti_account_wide:
        _deny()
    return principal


def require_account_management(
    principal: AuthenticatedPrincipal,
    platform: str | None = None,
) -> AuthenticatedPrincipal:
    """Authorize account-level integration credential mutations."""

    if principal.auth_method == "lti":
        require_lti_account_access(
            principal, platform or principal.lti_platform or "canvas"
        )
        if principal.lti_staff_role != "Administrator":
            _deny()
        return principal
    if principal.user_role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
        _deny()
    return principal


def require_canvas_account_management(
    principal: AuthenticatedPrincipal,
) -> AuthenticatedPrincipal:
    """Backward-compatible Canvas name for the shared account policy."""

    return require_account_management(principal, platform="canvas")
