"""
LLM Provider API endpoints.

Allows users to view and manage their LLM provider settings.

Workspace provider settings are provider-neutral, durable, and restricted to
non-LTI session or API-key administrators. Supported choices are Ollama,
Gemini, OpenAI, Anthropic, and xAI; no provider is selected by default.
"""

from fastapi import APIRouter, HTTPException, Depends, Query, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)
from typing import Any, Literal, Optional, Dict, Tuple, Union
from datetime import datetime, timezone
from sqlalchemy.orm import Session
import logging

from src.ai.providers import (
    get_provider_manager,
    ProviderType,
    ProviderConfig,
)
from src.ai.workspace_provider_config import (
    PROVIDER_DISPLAY_NAMES,
    SUPPORTED_WORKSPACE_PROVIDERS,
    test_provider_row,
)
from src.ai.providers.manager import get_rate_limiter
from src.ai.cache import get_llm_cache
from src.ai.lms_policy import LMS_AI_POLICY_VERSION
from src.ai.lms_readiness import ReadinessReason, resolve_lms_ai_readiness
from src.auth.dependencies import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
    get_required_api_key,
)
from src.db.models import (
    APIKey,
    AuditLog,
    AuditLogAction,
    Department,
    DepartmentAIProviderConfig,
    User,
    UserRole,
)
from src.db.database import get_db_dependency
from src.utils.encryption import (
    encrypt_api_key,
    decrypt_api_key,
    is_encryption_configured,
    EncryptionError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm", tags=["LLM Providers"])


class ProviderInfo(BaseModel):
    """Information about an LLM provider."""

    name: str
    display_name: str
    configured: bool
    is_available: bool
    is_local: bool
    status: str
    text_model: Optional[str] = None
    code_model: Optional[str] = None
    vision_model: Optional[str] = None


class ProviderListResponse(BaseModel):
    """Response listing all providers."""

    schema_version: Literal[1] = 1
    config_revision: int
    primary: Optional[str]
    fallback: Optional[str]
    providers: Dict[str, ProviderInfo]


class SetProviderRequest(BaseModel):
    """Request to set primary or fallback provider."""

    provider: str
    as_fallback: bool = False


class ProviderSelectionUpdate(BaseModel):
    """Complete workspace primary/fallback replacement."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt = Field(ge=0)
    primary: Literal["ollama", "gemini", "openai", "anthropic", "xai"] | None
    fallback: Literal["ollama", "gemini", "openai", "anthropic", "xai"] | None

    @model_validator(mode="after")
    def selections_must_be_distinct(self):
        if self.primary is not None and self.primary == self.fallback:
            raise ValueError("primary and fallback providers must be different")
        return self


class ProviderConfigUpdate(BaseModel):
    """Create or replace one workspace provider configuration."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt = Field(ge=0)
    # Credential validation happens inside the authorized route so framework
    # validation errors can never echo the submitted secret in a 422 response.
    api_key: Any = Field(
        default=None,
        json_schema_extra={"type": "string", "writeOnly": True},
    )
    clear_api_key: StrictBool = False
    text_model: Optional[str] = Field(default=None, max_length=128)
    code_model: Optional[str] = Field(default=None, max_length=128)
    vision_model: Optional[str] = Field(default=None, max_length=128)

    @field_validator("text_model", "code_model", "vision_model")
    @classmethod
    def validate_model_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        import re

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}", value):
            raise ValueError("invalid model identifier")
        return value

class AddProviderRequest(BaseModel):
    """Request to add a new provider with API key."""

    provider: str
    api_key: Any = Field(
        json_schema_extra={"type": "string", "writeOnly": True},
    )
    text_model: Optional[str] = None
    code_model: Optional[str] = None
    vision_model: Optional[str] = None


class UpdateModelsRequest(BaseModel):
    """Request to update models for an existing provider."""

    text_model: Optional[str] = None
    code_model: Optional[str] = None
    vision_model: Optional[str] = None


class TestResponse(BaseModel):
    """Response from provider test."""

    success: bool
    provider: str
    model: str
    inference_time: float
    error: Optional[str] = None


class LMSAIProviderReadinessResponse(BaseModel):
    """Bounded, secret-free readiness for one policy provider option."""

    ready: bool
    reason: ReadinessReason
    locality: Literal["local", "remote"]
    credential_source: Literal["local", "department_byok", "platform"] | None = None


class LMSAIPolicyResponse(BaseModel):
    """Secret-free persisted account policy and current provider readiness."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "schema_version": 1,
                "policy_revision": 4,
                "enabled": False,
                "provider": None,
                "remediation_enabled": False,
                "alt_text_enabled": False,
                "pilot_gemini_approved": False,
                "provider_readiness": {},
            }
        }
    )

    schema_version: Literal[1] = Field(
        default=LMS_AI_POLICY_VERSION, description="Response schema version."
    )
    policy_revision: int = Field(description="Optimistic concurrency revision.")
    enabled: bool
    provider: Literal["ollama", "gemini", "openai", "anthropic", "xai"] | None
    remediation_enabled: bool
    alt_text_enabled: bool
    pilot_gemini_approved: bool = Field(
        description="Read-only approval for the platform Gemini pilot lane."
    )
    provider_readiness: Dict[str, LMSAIProviderReadinessResponse]


class LMSAIPolicyUpdate(BaseModel):
    """A complete replacement for a department's explicit LMS AI policy."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "enabled": True,
                "provider": "ollama",
                "remediation_enabled": True,
                "alt_text_enabled": False,
                "expected_revision": 4,
            }
        },
    )

    enabled: StrictBool
    provider: Literal["ollama", "gemini", "openai", "anthropic", "xai"] | None
    remediation_enabled: StrictBool
    alt_text_enabled: StrictBool
    expected_revision: StrictInt = Field(ge=0, description="Revision returned by GET.")

    @model_validator(mode="after")
    def validate_consistency(self):
        has_purpose = self.remediation_enabled or self.alt_text_enabled
        if self.enabled and (self.provider is None or not has_purpose):
            raise ValueError("enabled policies require a provider and purpose")
        if not self.enabled and (self.provider is not None or has_purpose):
            raise ValueError("disabled policies cannot configure provider or purposes")
        return self


class LMSPolicyRevisionConflictDetail(BaseModel):
    code: Literal["policy_revision_conflict"]
    reason: Literal["stale_revision"]
    current: LMSAIPolicyResponse


class LMSProviderNotReadyDetail(BaseModel):
    code: Literal["provider_not_ready"]
    reason: ReadinessReason
    current: LMSAIPolicyResponse


class LMSPolicyConflictResponse(BaseModel):
    detail: Union[LMSPolicyRevisionConflictDetail, LMSProviderNotReadyDetail] = Field(
        discriminator="code"
    )


def _policy_response(department: Department) -> LMSAIPolicyResponse:
    purposes = set(department.lms_ai_purposes or [])
    readiness = resolve_lms_ai_readiness(department, decrypt_api_key=decrypt_api_key)
    return LMSAIPolicyResponse(
        policy_revision=department.lms_ai_policy_revision,
        enabled=department.lms_ai_enabled,
        provider=department.lms_ai_provider,
        remediation_enabled="remediation" in purposes,
        alt_text_enabled="alt_text" in purposes,
        pilot_gemini_approved=department.pilot_gemini_approved is True,
        provider_readiness={
            name: LMSAIProviderReadinessResponse(
                ready=value.ready,
                reason=value.reason,
                locality=value.locality,
                credential_source=value.credential_source,
            )
            for name, value in readiness.items()
        },
    )


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


def _require_provider_admin(principal: AuthenticatedPrincipal) -> None:
    """Restrict provider settings to normal workspace admin channels."""

    if principal.auth_method not in {"session", "api_key"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if principal.user_role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _provider_type(provider: str) -> ProviderType:
    try:
        return ProviderType.from_string(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported provider",
        ) from exc


def _validated_provider_api_key(update: ProviderConfigUpdate) -> str | None:
    """Validate a submitted secret without reflecting it through Pydantic errors."""

    if update.api_key is None:
        return None
    if update.clear_api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="API key actions cannot be combined",
        )
    if not isinstance(update.api_key, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid provider API key",
        )
    value = update.api_key.strip()
    if not value or len(value) > 4096:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid provider API key",
        )
    return value


def _workspace_provider_rows(
    db: Session, department_id: str
) -> dict[str, DepartmentAIProviderConfig]:
    rows = (
        db.query(DepartmentAIProviderConfig)
        .filter(DepartmentAIProviderConfig.department_id == department_id)
        .all()
    )
    return {row.provider: row for row in rows}


def _workspace_provider_response(
    db: Session, department: Department
) -> ProviderListResponse:
    rows = _workspace_provider_rows(db, department.id)
    providers: dict[str, ProviderInfo] = {}
    for provider_name in SUPPORTED_WORKSPACE_PROVIDERS:
        row = rows.get(provider_name)
        defaults = ProviderConfig.default_for_provider(ProviderType(provider_name))
        providers[provider_name] = ProviderInfo(
            name=provider_name,
            display_name=PROVIDER_DISPLAY_NAMES[provider_name],
            configured=row is not None,
            is_available=row is not None,
            is_local=provider_name == "ollama",
            status="configured" if row is not None else "not_configured",
            text_model=(row.text_model or defaults.text_model) if row else None,
            code_model=(row.code_model or defaults.code_model) if row else None,
            vision_model=(row.vision_model or defaults.vision_model) if row else None,
        )
    return ProviderListResponse(
        config_revision=department.ai_provider_config_revision,
        primary=department.ai_primary_provider,
        fallback=department.ai_fallback_provider,
        providers=providers,
    )


def _locked_provider_department(
    db: Session,
    principal: AuthenticatedPrincipal,
    expected_revision: int,
) -> Department:
    department = _get_own_department(db, principal.department_id, for_update=True)
    if department.ai_provider_config_revision != expected_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "provider_config_revision_conflict",
                "reason": "stale_revision",
                "current": _workspace_provider_response(db, department).model_dump(),
            },
        )
    return department


def _provider_audit(
    *,
    principal: AuthenticatedPrincipal,
    action: str,
    provider: str | None,
    old_revision: int,
    new_revision: int,
    changed_fields: list[str],
    old_selection: dict[str, str | None],
    new_selection: dict[str, str | None],
) -> AuditLog:
    return AuditLog(
        user_id=principal.user_id,
        department_id=principal.department_id,
        action=AuditLogAction.AI_PROVIDER_CONFIG_UPDATE.value,
        resource_type="department_ai_provider_config",
        resource_id=principal.department_id,
        details={
            "schema_version": 1,
            "action": action,
            "provider": provider,
            "changed_fields": sorted(changed_fields),
            "old_selection": old_selection,
            "new_selection": new_selection,
            "old_revision": old_revision,
            "new_revision": new_revision,
            "outcome": "updated",
        },
        status="success",
    )


def _commit_provider_update(db: Session) -> None:
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Provider configuration update failed",
        ) from exc


def _require_policy_admin(principal: AuthenticatedPrincipal) -> None:
    # LTI authority comes only from the validated, immutable principal. Course
    # roles cannot be promoted by a request body or a database role alone.
    if principal.auth_method == "lti":
        if not (
            principal.lti_staff_role == "Administrator"
            and principal.lti_account_wide is True
            and principal.user_role is UserRole.ADMIN
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )
        return
    if principal.user_role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.get("/lms-policy", response_model=LMSAIPolicyResponse)
def get_lms_ai_policy(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Return the admin's own account policy; never returns keys, hosts, or models."""

    _require_policy_admin(principal)
    return _policy_response(_get_own_department(db, principal.department_id))


@router.put(
    "/lms-policy",
    response_model=LMSAIPolicyResponse,
    responses={
        409: {
            "model": LMSPolicyConflictResponse,
            "description": "Policy revision conflict or selected provider not ready.",
            "content": {
                "application/json": {
                    "examples": {
                        "revision_conflict": {
                            "summary": "The submitted revision is stale",
                            "value": {
                                "detail": {
                                    "code": "policy_revision_conflict",
                                    "reason": "stale_revision",
                                    "current": {
                                        "schema_version": 1,
                                        "policy_revision": 4,
                                        "enabled": False,
                                        "provider": None,
                                        "remediation_enabled": False,
                                        "alt_text_enabled": False,
                                        "pilot_gemini_approved": False,
                                        "provider_readiness": {},
                                    },
                                }
                            },
                        },
                        "provider_not_ready": {
                            "summary": "The selected provider is not ready",
                            "value": {
                                "detail": {
                                    "code": "provider_not_ready",
                                    "reason": "credential_invalid",
                                    "current": {
                                        "schema_version": 1,
                                        "policy_revision": 4,
                                        "enabled": False,
                                        "provider": None,
                                        "remediation_enabled": False,
                                        "alt_text_enabled": False,
                                        "pilot_gemini_approved": False,
                                        "provider_readiness": {},
                                    },
                                }
                            },
                        },
                    }
                }
            },
        }
    },
)
def update_lms_ai_policy(
    update: LMSAIPolicyUpdate,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Replace the caller's department policy and audit it in one transaction."""

    _require_policy_admin(principal)
    department = _get_own_department(db, principal.department_id, for_update=True)
    old_revision = department.lms_ai_policy_revision
    if update.expected_revision != old_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "policy_revision_conflict",
                "reason": "stale_revision",
                "current": _policy_response(department).model_dump(),
            },
        )

    old_policy = {
        "enabled": department.lms_ai_enabled,
        "provider": department.lms_ai_provider,
        "remediation_enabled": "remediation" in (department.lms_ai_purposes or []),
        "alt_text_enabled": "alt_text" in (department.lms_ai_purposes or []),
    }
    purposes = [
        purpose
        for purpose, selected in (
            ("remediation", update.remediation_enabled),
            ("alt_text", update.alt_text_enabled),
        )
        if selected
    ]
    new_policy = {
        "enabled": update.enabled,
        "provider": update.provider,
        "remediation_enabled": update.remediation_enabled,
        "alt_text_enabled": update.alt_text_enabled,
    }

    if update.enabled and update.provider is not None:
        readiness = resolve_lms_ai_readiness(
            department, decrypt_api_key=decrypt_api_key
        )[update.provider]
        if not readiness.ready:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "provider_not_ready",
                    "reason": readiness.reason,
                    "current": _policy_response(department).model_dump(),
                },
            )

    department.lms_ai_enabled = update.enabled
    department.lms_ai_provider = update.provider
    department.lms_ai_purposes = purposes
    department.lms_ai_policy_revision = old_revision + 1
    db.add(
        AuditLog(
            user_id=principal.user_id,
            department_id=principal.department_id,
            action=AuditLogAction.LMS_AI_POLICY_UPDATE.value,
            resource_type="department",
            resource_id=principal.department_id,
            details={
                "old": old_policy,
                "new": new_policy,
                "old_revision": old_revision,
                "new_revision": old_revision + 1,
                "schema_version": LMS_AI_POLICY_VERSION,
                "outcome": "updated",
            },
            status="success",
        )
    )
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Policy update failed",
        ) from exc
    return _policy_response(department)


@router.get("/providers", response_model=ProviderListResponse)
def list_providers(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Return the caller's durable, secret-free workspace configuration."""

    _require_provider_admin(principal)
    department = _get_own_department(db, principal.department_id)
    return _workspace_provider_response(db, department)


@router.put("/providers/selection", response_model=ProviderListResponse)
def update_workspace_provider_selection_route(
    update: ProviderSelectionUpdate,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    return _update_workspace_provider_selection(update, principal, db)


@router.put("/providers/{provider}", response_model=ProviderListResponse)
def configure_workspace_provider(
    provider: str,
    update: ProviderConfigUpdate,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Create or update one provider in the caller's workspace."""

    _require_provider_admin(principal)
    provider_type = _provider_type(provider)
    provider_name = provider_type.value
    api_key = _validated_provider_api_key(update)
    department = _locked_provider_department(db, principal, update.expected_revision)
    old_revision = department.ai_provider_config_revision
    old_selection = {
        "primary": department.ai_primary_provider,
        "fallback": department.ai_fallback_provider,
    }
    row = (
        db.query(DepartmentAIProviderConfig)
        .filter(
            DepartmentAIProviderConfig.department_id == principal.department_id,
            DepartmentAIProviderConfig.provider == provider_name,
        )
        .first()
    )

    if provider_type is ProviderType.OLLAMA:
        if api_key is not None or update.clear_api_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Ollama does not accept an API key",
            )
        encrypted_key = None
    else:
        if update.clear_api_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Delete the provider configuration to remove its key",
            )
        if api_key is None and row is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A provider API key is required",
            )
        encrypted_key = row.api_key_encrypted if row is not None else None
        if api_key is not None:
            if not is_encryption_configured():
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Provider credential encryption is unavailable",
                )
            try:
                encrypted_key = encrypt_api_key(api_key)
            except EncryptionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Provider credential encryption failed",
                ) from exc

    if row is None:
        row = DepartmentAIProviderConfig(
            department_id=principal.department_id,
            provider=provider_name,
        )
        db.add(row)
    row.api_key_encrypted = encrypted_key
    for field_name in ("text_model", "code_model", "vision_model"):
        if field_name in update.model_fields_set:
            setattr(row, field_name, getattr(update, field_name))

    changed_fields = ["provider_configuration"]
    if api_key is not None:
        changed_fields.append("credential")
    for field_name in ("text_model", "code_model", "vision_model"):
        if field_name in update.model_fields_set:
            changed_fields.append(field_name)

    department.ai_provider_config_revision = old_revision + 1
    db.add(
        _provider_audit(
            principal=principal,
            action="configure",
            provider=provider_name,
            old_revision=old_revision,
            new_revision=old_revision + 1,
            changed_fields=changed_fields,
            old_selection=old_selection,
            new_selection=old_selection,
        )
    )
    _commit_provider_update(db)
    return _workspace_provider_response(db, department)


def _update_workspace_provider_selection(
    update: ProviderSelectionUpdate,
    principal: AuthenticatedPrincipal,
    db: Session,
):
    """Atomically replace durable primary and fallback selections."""

    _require_provider_admin(principal)
    department = _locked_provider_department(db, principal, update.expected_revision)
    configured = set(_workspace_provider_rows(db, principal.department_id))
    for selected in (update.primary, update.fallback):
        if selected is not None and selected not in configured:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "provider_not_configured",
                    "provider": selected,
                },
            )

    old_revision = department.ai_provider_config_revision
    old_selection = {
        "primary": department.ai_primary_provider,
        "fallback": department.ai_fallback_provider,
    }
    new_selection = {"primary": update.primary, "fallback": update.fallback}
    department.ai_primary_provider = update.primary
    department.ai_fallback_provider = update.fallback
    department.ai_provider_config_revision = old_revision + 1
    db.add(
        _provider_audit(
            principal=principal,
            action="selection",
            provider=None,
            old_revision=old_revision,
            new_revision=old_revision + 1,
            changed_fields=["primary", "fallback"],
            old_selection=old_selection,
            new_selection=new_selection,
        )
    )
    _commit_provider_update(db)
    return _workspace_provider_response(db, department)


@router.delete("/providers/{provider}", response_model=ProviderListResponse)
def delete_workspace_provider(
    provider: str,
    expected_revision: int = Query(ge=0),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Delete one unselected provider configuration."""

    _require_provider_admin(principal)
    provider_name = _provider_type(provider).value
    department = _locked_provider_department(db, principal, expected_revision)
    if provider_name in {
        department.ai_primary_provider,
        department.ai_fallback_provider,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "provider_is_selected", "provider": provider_name},
        )
    row = (
        db.query(DepartmentAIProviderConfig)
        .filter(
            DepartmentAIProviderConfig.department_id == principal.department_id,
            DepartmentAIProviderConfig.provider == provider_name,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    old_revision = department.ai_provider_config_revision
    selection = {
        "primary": department.ai_primary_provider,
        "fallback": department.ai_fallback_provider,
    }
    db.delete(row)
    department.ai_provider_config_revision = old_revision + 1
    db.add(
        _provider_audit(
            principal=principal,
            action="delete",
            provider=provider_name,
            old_revision=old_revision,
            new_revision=old_revision + 1,
            changed_fields=["provider_configuration", "credential"],
            old_selection=selection,
            new_selection=selection,
        )
    )
    _commit_provider_update(db)
    return _workspace_provider_response(db, department)


@router.post("/providers/primary")
def set_primary_provider(
    request: SetProviderRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Compatibility wrapper for the historical primary-selection route."""

    _require_provider_admin(principal)
    department = _get_own_department(db, principal.department_id, for_update=True)
    update = ProviderSelectionUpdate(
        expected_revision=department.ai_provider_config_revision,
        primary=(
            department.ai_primary_provider if request.as_fallback else request.provider
        ),
        fallback=(
            request.provider if request.as_fallback else department.ai_fallback_provider
        ),
    )
    current = _update_workspace_provider_selection(update, principal, db)
    return {
        "success": True,
        "message": f"Set {request.provider} as {'fallback' if request.as_fallback else 'primary'} provider",
        "primary": current.primary,
        "fallback": current.fallback,
        "config_revision": current.config_revision,
    }


@router.post("/providers/add")
def add_provider(
    request: AddProviderRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Compatibility wrapper for historical BYOK configuration."""

    _require_provider_admin(principal)
    department = _get_own_department(db, principal.department_id, for_update=True)
    current = configure_workspace_provider(
        request.provider,
        ProviderConfigUpdate(
            expected_revision=department.ai_provider_config_revision,
            api_key=request.api_key,
            text_model=request.text_model,
            code_model=request.code_model,
            vision_model=request.vision_model,
        ),
        principal,
        db,
    )
    return {
        "success": True,
        "message": f"Provider {request.provider} configured successfully",
        "provider": request.provider,
        "byok_saved": True,
        "warning": None,
        "config_revision": current.config_revision,
    }


@router.put("/providers/{provider}/models")
def update_provider_models(
    provider: str,
    request: UpdateModelsRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Compatibility wrapper for durable model updates."""

    _require_provider_admin(principal)
    provider_name = _provider_type(provider).value
    department = _get_own_department(db, principal.department_id, for_update=True)
    row = (
        db.query(DepartmentAIProviderConfig)
        .filter(
            DepartmentAIProviderConfig.department_id == principal.department_id,
            DepartmentAIProviderConfig.provider == provider_name,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    current = configure_workspace_provider(
        provider_name,
        ProviderConfigUpdate(
            expected_revision=department.ai_provider_config_revision,
            text_model=request.text_model,
            code_model=request.code_model,
            vision_model=request.vision_model,
        ),
        principal,
        db,
    )
    provider_info = current.providers[provider_name]
    return {
        "success": True,
        "message": f"Updated models for {provider}",
        "provider": provider,
        "text_model": provider_info.text_model,
        "code_model": provider_info.code_model,
        "vision_model": provider_info.vision_model,
        "config_revision": current.config_revision,
    }


@router.post("/providers/test", response_model=TestResponse)
async def test_provider(
    provider: Optional[str] = None,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Test one fresh provider instance from the caller's workspace row."""

    _require_provider_admin(principal)
    department = _get_own_department(db, principal.department_id)
    provider_name = _provider_type(provider).value if provider else department.ai_primary_provider
    if provider_name is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "primary_provider_not_selected"},
        )
    row = (
        db.query(DepartmentAIProviderConfig)
        .filter(
            DepartmentAIProviderConfig.department_id == principal.department_id,
            DepartmentAIProviderConfig.provider == provider_name,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    result = await test_provider_row(row, decryptor=decrypt_api_key)
    return TestResponse(**result.__dict__)


@router.get("/providers/{provider}/models")
def list_provider_models(
    provider: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Return static configured models without constructing a provider."""

    _require_provider_admin(principal)
    provider_name = _provider_type(provider).value
    department = _get_own_department(db, principal.department_id)
    response = _workspace_provider_response(db, department)
    info = response.providers[provider_name]
    if not info.is_available:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return {
        "provider": provider_name,
        "models": [
            model
            for model in (info.text_model, info.code_model, info.vision_model)
            if model is not None
        ],
    }


@router.get("/health")
async def llm_health():
    """
    Check health of all LLM providers.
    """
    manager = get_provider_manager()

    if not manager._initialized:
        await manager.initialize()

    return manager.health_check()


@router.get("/models/recommended")
async def get_recommended_models(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    Get recommended models with benchmark performance data.

    Returns models tested in January 2026 with accuracy and speed metrics.
    Use this to choose the best model for your needs.

    Higher-accuracy models can be selected via:
    PUT /llm/providers/gemini/models
    """
    return {
        "gemini": {
            "recommended_for_accuracy": {
                "model": "gemini-3-flash-preview",
                "accuracy": "100%",
                "avg_time": "5.4s",
                "description": "Best accuracy for WCAG classification and code fixes",
                "best_for": ["critical_compliance"],
            },
            "recommended_for_speed": {
                "model": "gemini-2.0-flash-exp",
                "accuracy": "87%",
                "avg_time": "1.8s",
                "description": "Fastest response time, good for bulk operations",
                "best_for": ["bulk_scanning", "real_time"],
            },
            "balanced": {
                "model": "gemini-2.5-flash",
                "accuracy": "87%",
                "avg_time": "5.4s",
                "description": "Good balance between accuracy and capabilities",
                "best_for": ["general_use"],
            },
            "all_available": [
                "gemini-3-flash-preview",
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-2.0-flash-exp",
                "gemini-2.0-flash",
            ],
        },
        "ollama": {
            "recommended_for_code": {
                "model": "qwen2.5-coder:3b",
                "accuracy": "100%",
                "avg_time": "3.1s",
                "description": "Best for code generation and fixes",
                "best_for": ["code_fixes", "local_deployment"],
            },
            "recommended_for_classification": {
                "model": "qwen2.5-coder:1.5b",
                "accuracy": "60%",
                "avg_time": "4.2s",
                "description": "Fast classification for limited hardware",
                "best_for": ["classification", "low_ram"],
            },
            "recommended_for_vision": {
                "model": "minicpm-v:latest",
                "accuracy": "54%",
                "avg_time": "39s",
                "description": "Best local vision model for alt-text generation",
                "best_for": ["image_description", "offline"],
            },
        },
        "usage": {
            "switch_gemini_model": "PUT /llm/providers/gemini/models",
            "bring_own_key": "POST /llm/providers/add",
            "test_provider": "POST /llm/providers/test",
        },
    }


@router.get("/byok/status")
def get_byok_status(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Compatibility status for workspace-owned provider configuration."""

    _require_provider_admin(principal)
    department = _get_own_department(db, principal.department_id)
    rows = _workspace_provider_rows(db, principal.department_id)
    reported_provider = department.ai_primary_provider
    if reported_provider is None and len(rows) == 1:
        reported_provider = next(iter(rows))
    reported_row = rows.get(reported_provider) if reported_provider else None
    return {
        "configured": bool(rows),
        "provider": reported_provider,
        "configured_at": (
            reported_row.configured_at.isoformat()
            if reported_row is not None and reported_row.configured_at
            else None
        ),
        "tier": department.tier,
        "pilot_gemini_approved": department.pilot_gemini_approved,
        "encryption_available": is_encryption_configured(),
        "config_revision": department.ai_provider_config_revision,
    }


@router.post("/byok/load")
def load_byok_provider(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Deprecated no-op: durable rows need no process-global loading."""

    _require_provider_admin(principal)
    department = _get_own_department(db, principal.department_id)
    rows = _workspace_provider_rows(db, principal.department_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    reported_provider = department.ai_primary_provider
    if reported_provider is None and len(rows) == 1:
        reported_provider = next(iter(rows))
    return {
        "success": True,
        "message": "Workspace provider configuration is already durable",
        "provider": reported_provider,
        "config_revision": department.ai_provider_config_revision,
    }


@router.delete("/byok")
def delete_byok_config(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Compatibility wrapper deleting the selected provider configuration."""

    _require_provider_admin(principal)
    department = _get_own_department(db, principal.department_id, for_update=True)
    configured_rows = _workspace_provider_rows(db, principal.department_id)
    provider = department.ai_primary_provider
    if provider is None and len(configured_rows) == 1:
        provider = next(iter(configured_rows))
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    old_provider = provider
    row = (
        db.query(DepartmentAIProviderConfig)
        .filter(
            DepartmentAIProviderConfig.department_id == principal.department_id,
            DepartmentAIProviderConfig.provider == provider,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    old_revision = department.ai_provider_config_revision
    old_selection = {
        "primary": department.ai_primary_provider,
        "fallback": department.ai_fallback_provider,
    }
    db.delete(row)
    department.ai_primary_provider = None
    if department.ai_fallback_provider == provider:
        department.ai_fallback_provider = None
    department.ai_provider_config_revision = old_revision + 1
    new_selection = {
        "primary": department.ai_primary_provider,
        "fallback": department.ai_fallback_provider,
    }
    db.add(
        _provider_audit(
            principal=principal,
            action="delete",
            provider=provider,
            old_revision=old_revision,
            new_revision=old_revision + 1,
            changed_fields=["provider_configuration", "credential", "primary"],
            old_selection=old_selection,
            new_selection=new_selection,
        )
    )
    _commit_provider_update(db)
    return {
        "success": True,
        "message": "BYOK configuration deleted",
        "previous_provider": old_provider,
    }


@router.get("/rate-limits")
async def get_rate_limit_status(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    Get current rate limit status for all providers.

    Returns the current usage and limits for each provider, including:
    - Requests per minute (RPM) used vs limit
    - Requests per day (RPD) used vs limit
    - Whether the provider is currently rate limited

    Requires authentication via API key.
    """
    rate_limiter = get_rate_limiter()
    usage = rate_limiter.get_usage()

    return {
        "providers": usage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/cache/stats")
async def get_cache_statistics(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    Get LLM response cache statistics.

    Returns cache hit/miss rates and other performance metrics:
    - Total cache hits and misses
    - Hit rate percentage
    - Number of cached entries
    - Cache enabled status

    Requires authentication via API key.
    """
    cache = get_llm_cache()
    stats = cache.get_stats()

    return {
        "cache": stats,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.delete("/cache")
async def clear_cache(
    provider: Optional[str] = None,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Clear the LLM response cache.

    Optionally filter by provider to only clear cache entries for a specific provider.

    Args:
        provider: Optional provider name to filter (e.g., "gemini", "ollama")

    Requires authentication via API key.
    Requires ADMIN or SUPER_ADMIN role (cache is shared across departments).
    """
    api_key_obj, user_id, _ = api_key_info

    # Verify admin role - cache is global, so clearing requires elevated privileges
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        raise HTTPException(
            status_code=403,
            detail="Admin access required to clear cache",
        )

    cache = get_llm_cache()

    if provider:
        pattern = f"llm_cache:{provider}:*"
        deleted = cache.clear_all(pattern=pattern)
        message = f"Cleared {deleted} cache entries for provider: {provider}"
    else:
        deleted = cache.clear_all()
        message = f"Cleared {deleted} cache entries"

    logger.info(f"{message} (by user {user_id})")

    return {
        "success": True,
        "message": message,
        "entries_deleted": deleted,
    }
