"""Fail-closed authorization policy for LTI launch roles.

The LTI role claim is an authorization input, not a display label. Only the
staff roles named here may enter Aelira through an LTI launch. Unknown,
missing, learner, and other non-staff roles are denied by default.
"""

from dataclasses import dataclass
from typing import Mapping, Sequence

from sqlalchemy.orm import Session

from src.db.models import AuthProvider, User, UserRole

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


def validate_lti_staff_token_payload(
    payload: Mapping[str, object], db: Session
) -> User | None:
    """Validate authorization claims in an already signature-verified LTI token.

    The database is authoritative for identity and tenancy. Claims only prove
    that this is a current staff launch token and must agree with the active LTI
    user row before it can be used.
    """

    if (
        payload.get("lti_launch") is not True
        or payload.get("lti_staff") is not True
        or type(payload.get("lti_authz_version")) is not int
        or payload.get("lti_authz_version") != 2
    ):
        return None

    staff_role = payload.get("lti_staff_role")
    asserted_roles = payload.get("lti_roles")
    if not isinstance(staff_role, str) or not isinstance(asserted_roles, list):
        return None
    if not all(isinstance(role, str) for role in asserted_roles):
        return None
    decision = authorize_lti_roles(asserted_roles)
    if (
        not decision.allowed
        or decision.aelira_role is None
        or decision.staff_role != staff_role
    ):
        return None

    user_id = payload.get("sub") or payload.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        return None
    alternate_user_id = payload.get("user_id")
    if alternate_user_id is not None and alternate_user_id != user_id:
        return None

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return None

    database_role = user.role.value if isinstance(user.role, UserRole) else user.role
    database_provider = (
        user.auth_provider.value
        if isinstance(user.auth_provider, AuthProvider)
        else user.auth_provider
    )
    if (
        user.is_active is not True
        or database_provider != AuthProvider.LTI.value
        or str(user.department_id) != payload.get("department_id")
        or user.role is not decision.aelira_role
        or payload.get("role") != database_role
        or payload.get("lti_account_wide") is not decision.account_wide
    ):
        return None

    return user
