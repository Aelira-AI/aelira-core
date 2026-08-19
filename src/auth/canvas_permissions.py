"""Pure authorization policies for Canvas access."""

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


def require_lti_course_access(
    principal: AuthenticatedPrincipal, course_id: str
) -> AuthenticatedPrincipal:
    """Apply course scope to LTI users without changing non-LTI behavior."""

    require_canvas_staff(principal)
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
) -> AuthenticatedPrincipal:
    """Require account-wide scope for LTI users; preserve non-LTI access."""

    require_canvas_staff(principal)
    if principal.auth_method == "lti" and not principal.lti_account_wide:
        _deny()
    return principal


def require_canvas_account_management(
    principal: AuthenticatedPrincipal,
) -> AuthenticatedPrincipal:
    """Authorize account-level Canvas credential mutations.

    LTI relies on its authoritative account-wide Administrator assertion.
    Other authentication methods require an Aelira ADMIN or SUPER_ADMIN role;
    this includes the development-only mock administrator.
    """
    if principal.auth_method == "lti":
        require_lti_account_access(principal)
        if principal.lti_staff_role != "Administrator":
            _deny()
        return principal
    if principal.user_role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
        _deny()
    return principal
