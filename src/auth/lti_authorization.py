"""Fail-closed authorization policy for LTI launch roles.

The LTI role claim is an authorization input, not a display label. Only the
staff roles named here may enter Aelira through an LTI launch. Unknown,
missing, learner, and other non-staff roles are denied by default.
"""

from dataclasses import dataclass
from typing import Sequence

from src.db.models import UserRole

_ACCOUNT_STAFF_ROLES = ("Administrator",)
_COURSE_STAFF_ROLES = (
    "Instructor",
    "TeachingAssistant",
    "ContentDeveloper",
)


@dataclass(frozen=True)
class LTIStaffAuthorization:
    """Authorization decision derived solely from asserted LTI roles."""

    allowed: bool
    aelira_role: UserRole | None
    staff_role: str | None
    account_wide: bool


def _terminal_role_component(role_uri: str) -> str:
    """Return the exact terminal role component from an LTI role URI."""

    value = str(role_uri or "").strip()
    if "#" in value:
        return value.rsplit("#", 1)[-1]
    if "/" in value:
        return value.rstrip("/").rsplit("/", 1)[-1]
    return value


def authorize_lti_roles(
    role_uris: Sequence[str] | None,
) -> LTIStaffAuthorization:
    """Resolve LTI roles into an explicit, fail-closed staff decision.

    Administrator takes precedence and receives account-wide scope. The three
    approved instructional staff roles receive course scope. Every other role
    set is denied, including an empty or missing claim.
    """

    asserted_roles = {
        _terminal_role_component(role_uri) for role_uri in (role_uris or []) if role_uri
    }

    for staff_role in _ACCOUNT_STAFF_ROLES:
        if staff_role in asserted_roles:
            return LTIStaffAuthorization(
                allowed=True,
                aelira_role=UserRole.ADMIN,
                staff_role=staff_role,
                account_wide=True,
            )

    for staff_role in _COURSE_STAFF_ROLES:
        if staff_role in asserted_roles:
            return LTIStaffAuthorization(
                allowed=True,
                aelira_role=UserRole.FACULTY,
                staff_role=staff_role,
                account_wide=False,
            )

    return LTIStaffAuthorization(
        allowed=False,
        aelira_role=None,
        staff_role=None,
        account_wide=False,
    )
