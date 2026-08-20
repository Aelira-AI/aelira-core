"""
LLM Provider API endpoints.

Allows users to view and manage their LLM provider settings.

SECURITY: All endpoints require API key authentication.

Gemini Model Options (January 2026 Benchmarks):
- gemini-3-flash-preview: 100% accuracy, 5.4s - BEST QUALITY
- gemini-2.5-flash: 87% accuracy, 5.4s - balanced option
- gemini-2.0-flash-exp: 87% accuracy, 1.8s - FASTEST (free tier default)

Users can switch models via PUT /llm/providers/gemini/models
Users can bring their own API key via POST /llm/providers/add
"""

from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, ConfigDict, model_validator
from typing import Literal, Optional, Dict, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session
import logging

from src.ai.providers import (
    get_provider_manager,
    ProviderType,
    ProviderConfig,
)
from src.ai.providers.manager import get_rate_limiter
from src.ai.cache import get_llm_cache
from src.ai.lms_policy import LMS_AI_POLICY_VERSION
from src.auth.dependencies import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
    get_required_api_key,
)
from src.db.models import APIKey, AuditLog, AuditLogAction, Department, User, UserRole
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
    is_available: bool
    is_local: bool
    status: str
    text_model: Optional[str] = None
    code_model: Optional[str] = None
    vision_model: Optional[str] = None


class ProviderListResponse(BaseModel):
    """Response listing all providers."""

    primary: str
    fallback: Optional[str]
    providers: Dict[str, ProviderInfo]


class SetProviderRequest(BaseModel):
    """Request to set primary or fallback provider."""

    provider: str
    as_fallback: bool = False


class AddProviderRequest(BaseModel):
    """Request to add a new provider with API key."""

    provider: str
    api_key: str
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
    response_preview: Optional[str] = None
    error: Optional[str] = None


class LMSAIPolicyResponse(BaseModel):
    """Secret-free persisted policy for the authenticated department."""

    enabled: bool
    provider: Literal["ollama", "gemini", "openai", "anthropic", "xai"] | None
    purposes: list[Literal["remediation", "alt_text"]]
    version: int = LMS_AI_POLICY_VERSION


class LMSAIPolicyUpdate(BaseModel):
    """A complete replacement for a department's explicit LMS AI policy."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    provider: Literal["ollama", "gemini", "openai", "anthropic", "xai"] | None
    purposes: list[Literal["remediation", "alt_text"]]

    @model_validator(mode="after")
    def validate_consistency(self):
        if len(set(self.purposes)) != len(self.purposes):
            raise ValueError("purposes must not contain duplicates")
        if self.enabled and (self.provider is None or not self.purposes):
            raise ValueError("enabled policies require a provider and purpose")
        if not self.enabled and (self.provider is not None or self.purposes):
            raise ValueError("disabled policies cannot configure provider or purposes")
        return self


def _policy_response(department: Department) -> LMSAIPolicyResponse:
    return LMSAIPolicyResponse(
        enabled=department.lms_ai_enabled,
        provider=department.lms_ai_provider,
        purposes=list(department.lms_ai_purposes),
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
    """Return only the authenticated principal's department policy."""

    return _policy_response(_get_own_department(db, principal.department_id))


@router.put("/lms-policy", response_model=LMSAIPolicyResponse)
def update_lms_ai_policy(
    update: LMSAIPolicyUpdate,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Replace the caller's department policy and audit it in one transaction."""

    _require_policy_admin(principal)
    department = _get_own_department(db, principal.department_id, for_update=True)
    old_policy = {
        "enabled": department.lms_ai_enabled,
        "provider": department.lms_ai_provider,
        "purposes": list(department.lms_ai_purposes),
    }
    new_policy = {
        "enabled": update.enabled,
        "provider": update.provider,
        "purposes": list(update.purposes),
    }

    department.lms_ai_enabled = update.enabled
    department.lms_ai_provider = update.provider
    department.lms_ai_purposes = list(update.purposes)
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
                "version": LMS_AI_POLICY_VERSION,
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
async def list_providers(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    List all available LLM providers.

    Requires authentication via API key.

    Returns information about each provider's status and capabilities.
    """
    manager = get_provider_manager()

    # Initialize if needed
    if not manager._initialized:
        await manager.initialize()

    manager.health_check()

    providers = {}
    for ptype in ProviderType:
        provider = manager.get_provider(ptype)
        if provider:
            provider_health = provider.health_check()
            providers[ptype.value] = ProviderInfo(
                name=ptype.value,
                display_name=provider.display_name,
                is_available=provider.is_available,
                is_local=provider.is_local,
                status=provider_health.get("status", "unknown"),
                text_model=provider_health.get("text_model"),
                code_model=provider_health.get("code_model"),
                vision_model=provider_health.get("vision_model"),
            )
        else:
            # Provider not initialized
            providers[ptype.value] = ProviderInfo(
                name=ptype.value,
                display_name=ptype.value.title(),
                is_available=False,
                is_local=ptype == ProviderType.OLLAMA,
                status="not_configured",
            )

    return ProviderListResponse(
        primary=manager.primary_type.value,
        fallback=manager.fallback_type.value if manager.fallback_type else None,
        providers=providers,
    )


@router.post("/providers/primary")
async def set_primary_provider(
    request: SetProviderRequest,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    Set the primary or fallback LLM provider.

    Requires authentication via API key.

    The provider must already be configured and available.
    """
    try:
        provider_type = ProviderType.from_string(request.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    manager = get_provider_manager()

    if not manager._initialized:
        await manager.initialize()

    if request.as_fallback:
        success = manager.set_fallback_provider(provider_type)
        action = "fallback"
    else:
        success = manager.set_primary_provider(provider_type)
        action = "primary"

    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Provider {request.provider} is not available. "
            f"Configure it first using POST /llm/providers/add",
        )

    return {
        "success": True,
        "message": f"Set {request.provider} as {action} provider",
        "primary": manager.primary_type.value,
        "fallback": manager.fallback_type.value if manager.fallback_type else None,
    }


@router.post("/providers/add")
async def add_provider(
    request: AddProviderRequest,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Add or update a provider with an API key (BYOK - Bring Your Own Key).

    Requires authentication via API key.

    Use this to configure OpenAI, Anthropic, or Gemini with your own API key.
    This persists your BYOK configuration to your department for pilot/department tiers.
    """
    try:
        provider_type = ProviderType.from_string(request.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Build config
    config = ProviderConfig.default_for_provider(provider_type)
    config.api_key = request.api_key

    if request.text_model:
        config.text_model = request.text_model
    if request.code_model:
        config.code_model = request.code_model
    if request.vision_model:
        config.vision_model = request.vision_model

    manager = get_provider_manager()

    success = await manager.add_provider(provider_type, config)

    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to initialize provider {request.provider}. "
            f"Check your API key and try again.",
        )

    # Persist BYOK configuration to department for pilot/department tiers
    api_key_obj, _, _ = api_key_info
    byok_persisted = False
    encryption_warning = None

    if api_key_obj and api_key_obj.department_id:
        department = (
            db.query(Department)
            .filter(Department.id == api_key_obj.department_id)
            .first()
        )
        if department:
            department.byok_provider = request.provider
            department.byok_configured_at = datetime.now(timezone.utc)

            # Encrypt and persist the API key if encryption is configured
            if is_encryption_configured():
                try:
                    encrypted_key = encrypt_api_key(request.api_key)
                    department.byok_api_key_encrypted = encrypted_key
                    byok_persisted = True
                    logger.info(
                        f"Persisted encrypted BYOK key for department {department.id} "
                        f"(provider: {request.provider})"
                    )
                except EncryptionError as e:
                    logger.warning(f"Failed to encrypt BYOK key: {e}")
                    encryption_warning = (
                        "API key could not be encrypted for persistent storage"
                    )
            else:
                encryption_warning = (
                    "BYOK_ENCRYPTION_KEY not configured. "
                    "API key is active but not persisted for future sessions."
                )
                logger.warning(
                    f"BYOK encryption not configured - key for department "
                    f"{department.id} will not persist across restarts"
                )

            db.commit()

    return {
        "success": True,
        "message": f"Provider {request.provider} configured successfully",
        "provider": request.provider,
        "byok_saved": byok_persisted,
        "warning": encryption_warning,
    }


@router.put("/providers/{provider}/models")
async def update_provider_models(
    provider: str,
    request: UpdateModelsRequest,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    Update models for an existing provider without changing the API key.

    Requires authentication via API key.
    """
    try:
        provider_type = ProviderType.from_string(provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    manager = get_provider_manager()

    if not manager._initialized:
        await manager.initialize()

    provider_instance = manager.get_provider(provider_type)
    if not provider_instance or not provider_instance.is_available:
        raise HTTPException(
            status_code=404,
            detail=f"Provider {provider} is not configured. "
            f"Use POST /llm/providers/add to configure it first.",
        )

    # Update the models on the existing provider
    if request.text_model:
        provider_instance.config.text_model = request.text_model
    if request.code_model:
        provider_instance.config.code_model = request.code_model
    if request.vision_model:
        provider_instance.config.vision_model = request.vision_model

    return {
        "success": True,
        "message": f"Updated models for {provider}",
        "provider": provider,
        "text_model": provider_instance.config.text_model,
        "code_model": provider_instance.config.code_model,
        "vision_model": provider_instance.config.vision_model,
    }


@router.post("/providers/test", response_model=TestResponse)
async def test_provider(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    provider: Optional[str] = None,
):
    """
    Test an LLM provider with a simple prompt.

    Requires authentication via API key.

    If no provider specified, tests the primary provider.
    """
    manager = get_provider_manager()

    if not manager._initialized:
        await manager.initialize()

    provider_type = None
    if provider:
        try:
            provider_type = ProviderType.from_string(provider)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Test with a simple accessibility-related prompt
    response = await manager.generate_text(
        prompt="What is WCAG 2.1? Answer in one sentence.",
        max_tokens=100,
        temperature=0.3,
        provider=provider_type,
    )

    return TestResponse(
        success=response.success,
        provider=response.provider,
        model=response.model,
        inference_time=response.inference_time,
        response_preview=response.content[:200] if response.content else None,
        error=response.error,
    )


@router.get("/providers/{provider}/models")
async def list_provider_models(
    provider: str,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    List available models for a specific provider.

    Requires authentication via API key.
    """
    try:
        provider_type = ProviderType.from_string(provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    manager = get_provider_manager()
    provider_instance = manager.get_provider(provider_type)

    if not provider_instance:
        raise HTTPException(
            status_code=404,
            detail=f"Provider {provider} not initialized",
        )

    return {
        "provider": provider,
        "models": provider_instance.get_available_models(),
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
                "best_for": ["free_tier", "bulk_scanning", "real_time"],
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
async def get_byok_status(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get BYOK (Bring Your Own Key) configuration status for the authenticated department.

    Returns information about the persisted API key configuration without exposing
    the actual key.

    Requires authentication via API key.
    """
    api_key_obj, _, _ = api_key_info

    if not api_key_obj or not api_key_obj.department_id:
        return {
            "configured": False,
            "message": "No department associated with this API key",
        }

    department = (
        db.query(Department).filter(Department.id == api_key_obj.department_id).first()
    )

    if not department:
        return {
            "configured": False,
            "message": "Department not found",
        }

    has_encrypted_key = bool(department.byok_api_key_encrypted)

    return {
        "configured": has_encrypted_key,
        "provider": department.byok_provider,
        "configured_at": (
            department.byok_configured_at.isoformat()
            if department.byok_configured_at
            else None
        ),
        "tier": department.tier,
        "pilot_gemini_approved": department.pilot_gemini_approved,
        "encryption_available": is_encryption_configured(),
    }


@router.post("/byok/load")
async def load_byok_provider(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Load the persisted BYOK provider configuration for the authenticated department.

    This decrypts the stored API key and initializes the provider.
    Call this on application startup or when you need to restore BYOK configuration.

    Requires authentication via API key.
    """
    api_key_obj, _, _ = api_key_info

    if not api_key_obj or not api_key_obj.department_id:
        raise HTTPException(
            status_code=400,
            detail="No department associated with this API key",
        )

    department = (
        db.query(Department).filter(Department.id == api_key_obj.department_id).first()
    )

    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    if not department.byok_api_key_encrypted:
        raise HTTPException(
            status_code=404,
            detail="No BYOK API key configured for this department",
        )

    if not department.byok_provider:
        raise HTTPException(
            status_code=400,
            detail="BYOK provider type not set",
        )

    # Decrypt the API key
    try:
        decrypted_key = decrypt_api_key(department.byok_api_key_encrypted)
    except EncryptionError as e:
        logger.error(f"Failed to decrypt BYOK key for department {department.id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to decrypt BYOK API key. The encryption key may have changed.",
        )

    # Initialize the provider
    try:
        provider_type = ProviderType.from_string(department.byok_provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    config = ProviderConfig.default_for_provider(provider_type)
    config.api_key = decrypted_key

    manager = get_provider_manager()
    success = await manager.add_provider(provider_type, config)

    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize provider {department.byok_provider}. "
            f"The stored API key may be invalid.",
        )

    logger.info(
        f"Loaded BYOK provider {department.byok_provider} for department {department.id}"
    )

    return {
        "success": True,
        "message": f"BYOK provider {department.byok_provider} loaded successfully",
        "provider": department.byok_provider,
    }


@router.delete("/byok")
async def delete_byok_config(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Delete the BYOK configuration for the authenticated department.

    This removes the encrypted API key from storage.

    Requires authentication via API key.
    """
    api_key_obj, _, _ = api_key_info

    if not api_key_obj or not api_key_obj.department_id:
        raise HTTPException(
            status_code=400,
            detail="No department associated with this API key",
        )

    department = (
        db.query(Department).filter(Department.id == api_key_obj.department_id).first()
    )

    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    old_provider = department.byok_provider
    department.byok_provider = None
    department.byok_api_key_encrypted = None
    department.byok_configured_at = None
    db.commit()

    logger.info(
        f"Deleted BYOK configuration for department {department.id} "
        f"(was: {old_provider})"
    )

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
