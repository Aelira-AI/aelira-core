"""Policy-bound single-provider client for LMS remediation AI calls.

The client intentionally remains unreferenced by remediation code until Task 14
slice 3B. Every operation re-authorizes against a fresh database session and
constructs a fresh provider only after a durable pre-call audit record exists.
If the post-call audit cannot be persisted, the provider result is discarded
and a stable failure is returned; the outbound call cannot be undone.
"""

from __future__ import annotations

import asyncio

import math

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Callable, Mapping


from src.ai.lms_policy import LMS_AI_PROVIDERS, LMS_AI_PURPOSES, resolve_lms_ai_policy
from src.ai.lms_readiness import (
    canonical_loopback_ollama_host,
    resolve_lms_provider_config,
)
from src.ai.providers.base import LLMResponse
from src.ai.providers.types import ProviderConfig, ProviderType
from src.db.models import (
    AuditLog,
    AuditLogAction,
    AuditLogStatus,
    Department,
    User,
)

SessionFactory = Callable[[], Any]
ProviderFactory = Callable[[ProviderType, ProviderConfig], Any]
Decryptor = Callable[[str], str]


@dataclass(frozen=True)
class _DepartmentSnapshot:
    """Values copied while the ORM entity is attached to its session."""

    id: str
    byok_provider: str | None
    byok_api_key_encrypted: str | None
    pilot_gemini_approved: bool

    @classmethod
    def from_department(cls, department: Any) -> "_DepartmentSnapshot":
        return cls(
            id=department.id,
            byok_provider=department.byok_provider,
            byok_api_key_encrypted=department.byok_api_key_encrypted,
            pilot_gemini_approved=department.pilot_gemini_approved is True,
        )


def _default_session_factory() -> Any:
    from src.db.database import SessionLocal

    return SessionLocal()


def _default_decryptor(value: str) -> str:
    from src.utils.encryption import decrypt_api_key

    return decrypt_api_key(value)


def _default_provider_factory(
    provider_type: ProviderType, config: ProviderConfig
) -> Any:
    if provider_type is ProviderType.GEMINI:
        from src.ai.providers.gemini_provider import GeminiProvider

        return GeminiProvider(config)
    if provider_type is ProviderType.OLLAMA:
        from src.ai.providers.ollama_provider import OllamaProvider

        return OllamaProvider(config)
    if provider_type is ProviderType.OPENAI:
        from src.ai.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(config)
    if provider_type is ProviderType.ANTHROPIC:
        from src.ai.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(config)
    if provider_type is ProviderType.XAI:
        from src.ai.providers.xai_provider import XAIProvider

        return XAIProvider(config)
    raise ValueError("unsupported LMS AI provider")


def _run_coroutine(coroutine: Any) -> Any:
    """Run a provider coroutine from sync code, including an active event loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coroutine).result()


def _sanitize_model_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}", value
    ):
        return None
    return value


def _compatible_result(response: LLMResponse, *, model: str | None) -> dict[str, Any]:
    if response.success:
        return {
            "success": True,
            "content": response.content,
            "inference_time": response.inference_time or 0.0,
            "provider": response.provider,
            "model": model or "",
            "ai_used": True,
            "external_ai_used": response.provider != "ollama",
            "purpose_outcome": "used",
        }
    return {
        "success": False,
        "error": "provider_call_failed",
        "inference_time": response.inference_time or 0.0,
        "provider": response.provider,
        "model": model or "",
        "ai_used": True,
        "external_ai_used": response.provider != "ollama",
        "purpose_outcome": "attempted_failed",
    }


@dataclass(frozen=True)
class LMSRemediationClient:
    """Immutable LMS policy binding with per-operation provider lifecycle."""

    department_id: str
    provider: str
    purpose: str
    actor_id: str | None = None
    job_id: str | None = None
    scan_id: str | None = None
    cloud_file_id: str | None = None
    session_factory: SessionFactory = field(
        default=_default_session_factory, repr=False, compare=False
    )
    provider_factory: ProviderFactory = field(
        default=_default_provider_factory, repr=False, compare=False
    )
    decrypt_api_key: Decryptor = field(
        default=_default_decryptor, repr=False, compare=False
    )
    environment: Mapping[str, str] | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.department_id, str) or not self.department_id:
            raise ValueError("department_id must be non-empty")
        if self.provider not in LMS_AI_PROVIDERS:
            raise ValueError("provider must be an LMS AI provider")
        if self.purpose not in LMS_AI_PURPOSES:
            raise ValueError("purpose must be remediation or alt_text")

    @classmethod
    def bind_if_allowed(
        cls,
        *,
        department_id: str,
        purpose: str,
        actor_id: str | None = None,
        job_id: str | None = None,
        scan_id: str | None = None,
        cloud_file_id: str | None = None,
        session_factory: SessionFactory = _default_session_factory,
        provider_factory: ProviderFactory = _default_provider_factory,
        decrypt_api_key: Decryptor = _default_decryptor,
        environment: Mapping[str, str] | None = None,
    ) -> "LMSRemediationClient | None":
        """Fresh-resolve one purpose and return an immutable binding if allowed.

        This check deliberately does not resolve credentials or construct a
        provider. It is an early fail-closed gate only; every client operation
        performs its own policy and credential checks again before dispatch.
        """
        session = None
        try:
            if not isinstance(department_id, str) or not department_id:
                return None
            session = session_factory()
            department = session.get(Department, department_id)
            if department is None:
                return None
            decision = resolve_lms_ai_policy(department, purpose)
            if not decision.allowed or decision.provider is None:
                return None
            return cls(
                department_id=department_id,
                provider=decision.provider,
                purpose=purpose,
                actor_id=actor_id,
                job_id=job_id,
                scan_id=scan_id,
                cloud_file_id=cloud_file_id,
                session_factory=session_factory,
                provider_factory=provider_factory,
                decrypt_api_key=decrypt_api_key,
                environment=environment,
            )
        except Exception:
            return None
        finally:
            if session is not None:
                session.close()

    def generate_text_sync(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        return self._execute(
            "text",
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
        )

    def generate_code_sync(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        return self._execute(
            "code",
            prompt=prompt,
            language=language,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def analyze_image_sync(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 500,
    ) -> dict[str, Any]:
        if self.purpose != "alt_text":
            return self._failure("purpose_operation_mismatch")
        return self._execute(
            "vision", image_data=image_data, prompt=prompt, max_tokens=max_tokens
        )

    def _execute(self, operation: str, **arguments: Any) -> dict[str, Any]:
        decision, snapshot, error_code = self._authorize()
        locality = "local" if self.provider == "ollama" else "remote"
        if error_code is not None:
            audit_error = self._audit_or_error(
                operation=operation,
                provider=self.provider,
                locality=locality,
                credential_source=None,
                policy_version=getattr(decision, "version", None),
                policy_reason=getattr(decision, "reason", error_code),
                call_made=False,
                outcome="denied",
                error_code=error_code,
                model=None,
            )
            return self._failure(audit_error or error_code)
        if snapshot is None:
            return self._failure("policy_resolution_failed")

        config, credential_source, credential_error = self._resolve_credentials(
            snapshot
        )
        if credential_error is not None:
            audit_error = self._audit_or_error(
                operation=operation,
                provider=self.provider,
                locality=locality,
                credential_source=None,
                policy_version=decision.version,
                policy_reason=decision.reason,
                call_made=False,
                outcome="denied",
                error_code=credential_error,
                model=None,
            )
            return self._failure(audit_error or credential_error)

        audit_error = self._audit_or_error(
            operation=operation,
            provider=self.provider,
            locality=locality,
            credential_source=credential_source,
            policy_version=decision.version,
            policy_reason=decision.reason,
            call_made=False,
            outcome="allowed",
            error_code=None,
            model=None,
        )
        if audit_error is not None:
            return self._failure(audit_error)
        if config is None:
            return self._failure("credentials_unavailable")

        current_decision, current_snapshot, recheck_error = self._authorize()
        if recheck_error is not None or current_snapshot is None:
            recheck_error = recheck_error or "policy_resolution_failed"
            audit_error = self._audit_or_error(
                operation=operation,
                provider=self.provider,
                locality=locality,
                credential_source=credential_source,
                policy_version=getattr(current_decision, "version", decision.version),
                policy_reason=getattr(current_decision, "reason", recheck_error),
                call_made=False,
                outcome="denied",
                error_code=recheck_error,
                model=None,
            )
            return self._failure(audit_error or recheck_error)

        config, credential_source, credential_error = self._resolve_credentials(
            current_snapshot
        )
        if credential_error is not None or config is None:
            credential_error = credential_error or "credentials_unavailable"
            audit_error = self._audit_or_error(
                operation=operation,
                provider=self.provider,
                locality=locality,
                credential_source=None,
                policy_version=current_decision.version,
                policy_reason=current_decision.reason,
                call_made=False,
                outcome="denied",
                error_code=credential_error,
                model=None,
            )
            return self._failure(audit_error or credential_error)

        provider_instance = None
        response: LLMResponse | None = None
        call_error: str | None = None
        call_made = False
        try:
            provider_type = ProviderType(self.provider)
            provider_instance = self.provider_factory(provider_type, config)
            response, call_error, call_made = _run_coroutine(
                self._invoke_and_close(provider_instance, operation, arguments)
            )
        except Exception:
            call_error = "provider_call_failed"

        model = _sanitize_model_identifier(
            response.model if response is not None else None
        )
        if response is not None and not response.success:
            call_error = "provider_call_failed"
        post_audit_error = self._audit_or_error(
            operation=operation,
            provider=self.provider,
            locality=locality,
            credential_source=credential_source,
            policy_version=current_decision.version,
            policy_reason=current_decision.reason,
            call_made=call_made,
            outcome=(
                "success"
                if response is not None and response.success and call_error is None
                else "failure"
            ),
            error_code=call_error,
            model=model,
        )
        if post_audit_error is not None:
            return self._failure(
                "post_call_audit_failed",
                model=model,
                ai_used=call_made,
                external_ai_used=call_made and self.provider != "ollama",
            )
        if call_error is not None or response is None:
            return self._failure(
                call_error or "provider_call_failed",
                model=model,
                ai_used=call_made,
                external_ai_used=call_made and self.provider != "ollama",
            )
        return _compatible_result(response, model=model)

    async def _invoke_and_close(
        self, provider_instance: Any, operation: str, arguments: dict[str, Any]
    ) -> tuple[LLMResponse | None, str | None, bool]:
        response: LLMResponse | None = None
        error: str | None = None
        call_made = True
        try:
            try:
                initialized = await provider_instance.initialize()
            except Exception:
                error = "provider_initialization_failed"
            else:
                if initialized is not True:
                    error = "provider_initialization_failed"
                else:
                    method_name = {
                        "text": "generate_text",
                        "code": "generate_code",
                        "vision": "analyze_image",
                    }[operation]
                    try:
                        candidate = await getattr(provider_instance, method_name)(
                            **arguments
                        )
                    except Exception:
                        error = "provider_call_failed"
                    else:
                        if not isinstance(candidate, LLMResponse):
                            error = "invalid_provider_response"
                        elif candidate.success and not self._valid_success_response(
                            candidate
                        ):
                            error = "invalid_provider_response"
                        else:
                            response = candidate
        finally:
            try:
                await provider_instance.close()
            except Exception:
                error = error or "provider_close_failed"
        return response, error, call_made

    def _valid_success_response(self, response: LLMResponse) -> bool:
        inference_time = response.inference_time
        return (
            response.success is True
            and response.provider == self.provider
            and isinstance(response.content, str)
            and isinstance(inference_time, Real)
            and not isinstance(inference_time, bool)
            and math.isfinite(inference_time)
            and inference_time >= 0
            and _sanitize_model_identifier(response.model) == response.model
        )

    def _authorize(self) -> tuple[Any, Any | None, str | None]:
        session = None
        try:
            session = self.session_factory()
            department = session.get(Department, self.department_id)
            if department is None:
                return None, None, "department_not_found"
            decision = resolve_lms_ai_policy(department, self.purpose)
            if not decision.allowed:
                return decision, None, "policy_denied"
            if decision.provider != self.provider:
                return decision, None, "provider_changed"
            snapshot = _DepartmentSnapshot.from_department(department)
            return decision, snapshot, None
        except Exception:
            return None, None, "policy_resolution_failed"
        finally:
            if session is not None:
                session.close()

    def _resolve_credentials(
        self, department: _DepartmentSnapshot
    ) -> tuple[ProviderConfig | None, str | None, str | None]:
        config, readiness = resolve_lms_provider_config(
            department,
            self.provider,
            environment=self.environment,
            decrypt_api_key=self.decrypt_api_key,
        )
        if readiness.ready:
            return config, readiness.credential_source, None
        error_codes = {
            "credentials_forbidden": "ollama_credentials_forbidden",
            "ambient_key_forbidden": "ollama_credentials_forbidden",
            "host_not_loopback": "ollama_host_not_local",
            "credentials_missing": "credentials_unavailable",
            "credential_invalid": "credential_decryption_failed",
            "pilot_not_approved": "credentials_unavailable",
            "platform_key_missing": "credentials_unavailable",
        }
        return None, None, error_codes.get(readiness.reason, readiness.reason)

    @staticmethod
    def _canonical_loopback_ollama_host(host: object) -> str | None:
        return canonical_loopback_ollama_host(host)

    def _audit_or_error(self, **details: Any) -> str | None:
        session = None
        try:
            session = self.session_factory()
            resolved_department = session.get(Department, self.department_id)
            resolved_user = None
            if resolved_department is not None and self.actor_id is not None:
                candidate = session.get(User, self.actor_id)
                if (
                    candidate is not None
                    and candidate.department_id == resolved_department.id
                ):
                    resolved_user = candidate
            record = AuditLog(
                user_id=resolved_user.id if resolved_user is not None else None,
                department_id=(
                    resolved_department.id if resolved_department is not None else None
                ),
                action=AuditLogAction.LMS_AI_EXECUTION.value,
                resource_type="lms_ai_execution",
                resource_id=self.job_id or self.scan_id or self.cloud_file_id,
                details={
                    "department_id": self.department_id,
                    "actor_id": self.actor_id,
                    "job_id": self.job_id,
                    "scan_id": self.scan_id,
                    "cloud_file_id": self.cloud_file_id,
                    "purpose": self.purpose,
                    **details,
                },
                status=(
                    AuditLogStatus.SUCCESS.value
                    if details["outcome"] in {"allowed", "success"}
                    else AuditLogStatus.FAILURE.value
                ),
            )
            session.add(record)
            session.commit()
            return None
        except Exception:
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass
            return "audit_write_failed"
        finally:
            if session is not None:
                session.close()

    def _failure(
        self,
        error_code: str,
        *,
        model: str | None = None,
        ai_used: bool = False,
        external_ai_used: bool = False,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "error": error_code,
            "inference_time": 0.0,
            "provider": self.provider,
            "model": model or "",
            "ai_used": ai_used,
            "external_ai_used": external_ai_used,
            "purpose_outcome": (
                "attempted_failed" if ai_used else "denied_at_dispatch"
            ),
        }
