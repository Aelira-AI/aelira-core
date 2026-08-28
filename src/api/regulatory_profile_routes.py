"""Tenant-scoped institution-admin regulatory profile settings."""

from datetime import date, datetime, timezone
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
)
from sqlalchemy.orm import Session

from ..auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ..db.database import get_db_dependency
from ..db.models import AuditLog, AuditLogAction, AuditLogStatus, Department, UserRole
from ..education.deadline_config import (
    DeadlineService,
    RegulatoryProfileValidationError,
)

router = APIRouter(prefix="/admin", tags=["administration"])
REGULATORY_PROFILE_SCHEMA_VERSION = 1
ManagedFrameworkCode = Literal[
    "US_ADA_TITLE_II",
    "EU_EAA",
    "UK_PSBAR",
    "CA_AODA",
    "AU_DDA",
    "NONE",
]
EntityClass = Literal["large", "small_or_special_district"]


class ManagedFrameworkResponse(BaseModel):
    code: ManagedFrameworkCode
    name: str
    default_country_code: Optional[str]
    requires_explicit_selection: bool
    requires_title_ii_entity_class: bool
    allows_custom_deadline: bool


class RegulatoryProfileResponse(BaseModel):
    """Safe persisted settings, implemented options, and canonical preview."""

    schema_version: Literal[1] = REGULATORY_PROFILE_SCHEMA_VERSION
    profile_revision: int = Field(description="Optimistic concurrency revision.")
    configuration_complete: bool
    country_code: Optional[str]
    regulatory_framework: Optional[str] = Field(default=None, max_length=50)
    title_ii_entity_class: Optional[EntityClass]
    custom_deadline: Optional[date]
    custom_deadline_verified: bool
    supported_frameworks: list[ManagedFrameworkResponse]
    deadline: Dict[str, Any]


class RegulatoryProfileUpdate(BaseModel):
    """A complete replacement for the caller's own regulatory profile."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "country_code": "US",
                "regulatory_framework": "US_ADA_TITLE_II",
                "title_ii_entity_class": "large",
                "custom_deadline": None,
                "custom_deadline_verified": False,
                "expected_revision": 0,
            }
        },
    )

    country_code: Optional[str] = Field(
        min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$"
    )
    regulatory_framework: Optional[ManagedFrameworkCode]
    title_ii_entity_class: Optional[EntityClass]
    custom_deadline: Optional[date]
    custom_deadline_verified: StrictBool
    expected_revision: StrictInt = Field(ge=0, description="Revision returned by GET.")

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else None

    @field_validator("custom_deadline", mode="before")
    @classmethod
    def require_date_only(cls, value):
        if isinstance(value, datetime) or (
            isinstance(value, str) and ("T" in value or " " in value)
        ):
            raise ValueError("custom_deadline must be a date in YYYY-MM-DD format")
        return value


class RegulatoryProfileRevisionConflictDetail(BaseModel):
    code: Literal["regulatory_profile_revision_conflict"]
    reason: Literal["stale_revision"]
    current: RegulatoryProfileResponse


class RegulatoryProfileConflictResponse(BaseModel):
    detail: RegulatoryProfileRevisionConflictDetail


def _require_regulatory_profile_admin(principal: AuthenticatedPrincipal) -> None:
    if principal.auth_method not in {
        "session",
        "api_key",
    } or principal.user_role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _get_own_department(
    db: Session, department_id: str, *, for_update: bool = False
) -> Department:
    query = db.query(Department).filter(Department.id == department_id)
    if for_update:
        query = query.with_for_update()
    department = query.first()
    if department is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return department


def _profile_values(department: Department) -> dict[str, Any]:
    custom_deadline = getattr(department, "custom_deadline", None)
    verified_at = getattr(department, "custom_deadline_verified_at", None)
    return {
        "country_code": getattr(department, "country_code", None),
        "regulatory_framework": getattr(department, "regulatory_framework", None),
        "title_ii_entity_class": getattr(department, "title_ii_entity_class", None),
        "custom_deadline": (
            custom_deadline.date().isoformat()
            if isinstance(custom_deadline, datetime)
            else (
                custom_deadline.isoformat()
                if isinstance(custom_deadline, date)
                else None
            )
        ),
        "custom_deadline_verified": (
            custom_deadline is not None and isinstance(verified_at, datetime)
        ),
    }


def _profile_response(department: Department) -> RegulatoryProfileResponse:
    deadline = DeadlineService.for_department(department).to_dict()
    custom_deadline = getattr(department, "custom_deadline", None)
    verified_at = getattr(department, "custom_deadline_verified_at", None)
    return RegulatoryProfileResponse(
        profile_revision=getattr(department, "regulatory_profile_revision", 0),
        configuration_complete=deadline["applicability"] != "configuration_required",
        country_code=getattr(department, "country_code", None),
        regulatory_framework=getattr(department, "regulatory_framework", None),
        title_ii_entity_class=getattr(department, "title_ii_entity_class", None),
        custom_deadline=(
            custom_deadline.date()
            if isinstance(custom_deadline, datetime)
            else custom_deadline if isinstance(custom_deadline, date) else None
        ),
        custom_deadline_verified=(
            custom_deadline is not None and isinstance(verified_at, datetime)
        ),
        supported_frameworks=DeadlineService.get_manageable_frameworks(),
        deadline=deadline,
    )


def _validation_error(exc: RegulatoryProfileValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "invalid_regulatory_profile",
            "field": exc.field,
            "reason": exc.reason,
            "message": exc.message,
        },
    )


@router.get("/regulatory-profile", response_model=RegulatoryProfileResponse)
def get_regulatory_profile(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Read the authenticated admin's own department profile."""

    _require_regulatory_profile_admin(principal)
    return _profile_response(_get_own_department(db, principal.department_id))


@router.put(
    "/regulatory-profile",
    response_model=RegulatoryProfileResponse,
    responses={
        409: {
            "model": RegulatoryProfileConflictResponse,
            "description": "The submitted profile revision is stale.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "code": "regulatory_profile_revision_conflict",
                            "reason": "stale_revision",
                            "current": {
                                "schema_version": 1,
                                "profile_revision": 2,
                                "configuration_complete": False,
                                "country_code": None,
                                "regulatory_framework": None,
                                "title_ii_entity_class": None,
                                "custom_deadline": None,
                                "custom_deadline_verified": False,
                                "supported_frameworks": [],
                                "deadline": {},
                            },
                        }
                    }
                }
            },
        }
    },
)
def update_regulatory_profile(
    update: RegulatoryProfileUpdate,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Atomically replace and audit the caller's own department profile."""

    _require_regulatory_profile_admin(principal)
    try:
        validated = DeadlineService.validate_regulatory_profile(
            country_code=update.country_code,
            regulatory_framework=update.regulatory_framework,
            title_ii_entity_class=update.title_ii_entity_class,
            custom_deadline=update.custom_deadline,
            custom_deadline_verified=update.custom_deadline_verified,
        )
    except RegulatoryProfileValidationError as exc:
        raise _validation_error(exc) from exc

    department = _get_own_department(db, principal.department_id, for_update=True)
    old_revision = department.regulatory_profile_revision
    if update.expected_revision != old_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "regulatory_profile_revision_conflict",
                "reason": "stale_revision",
                "current": _profile_response(department).model_dump(mode="json"),
            },
        )

    old_profile = _profile_values(department)
    new_profile = {
        "country_code": validated.country_code,
        "regulatory_framework": validated.regulatory_framework,
        "title_ii_entity_class": validated.title_ii_entity_class,
        "custom_deadline": (
            validated.custom_deadline.date().isoformat()
            if validated.custom_deadline is not None
            else None
        ),
        "custom_deadline_verified": update.custom_deadline_verified,
    }
    try:
        department.country_code = validated.country_code
        department.regulatory_framework = validated.regulatory_framework
        department.title_ii_entity_class = validated.title_ii_entity_class
        department.custom_deadline = validated.custom_deadline
        department.custom_deadline_verified_at = (
            datetime.now(timezone.utc)
            if validated.custom_deadline is not None and update.custom_deadline_verified
            else None
        )
        department.regulatory_profile_revision = old_revision + 1
        db.add(
            AuditLog(
                user_id=principal.user_id,
                department_id=principal.department_id,
                action=AuditLogAction.REGULATORY_PROFILE_UPDATE.value,
                resource_type="department",
                resource_id=principal.department_id,
                details={
                    "old": old_profile,
                    "new": new_profile,
                    "old_revision": old_revision,
                    "new_revision": old_revision + 1,
                    "schema_version": REGULATORY_PROFILE_SCHEMA_VERSION,
                    "outcome": "updated",
                },
                status=AuditLogStatus.SUCCESS.value,
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Regulatory profile update failed",
        ) from exc
    return _profile_response(department)
