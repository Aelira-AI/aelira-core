"""Disposable, tenant-bound runtime for durable workspace AI providers.

Unlike :class:`ProviderManager`, this adapter owns no provider instances and
uses no process-global response cache.  Each operation resolves one workspace's
current selection, constructs only the attempted provider, and closes it before
returning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from src.ai.providers.base import LLMResponse
from src.ai.providers.types import ProviderType
from src.ai.workspace_provider_config import (
    ProviderFactory,
    create_provider_instance,
    provider_config_from_row,
)
from src.utils.async_helpers import run_async_from_sync


@dataclass(frozen=True)
class WorkspaceProviderRow:
    """Detached provider fields needed for one bounded operation."""

    provider: str
    api_key_encrypted: str | None
    text_model: str | None
    code_model: str | None
    vision_model: str | None


@dataclass(frozen=True)
class WorkspaceProviderSnapshot:
    """One freshly loaded workspace selection and its selected rows."""

    workspace_id: str
    primary: str | None
    fallback: str | None
    rows: Mapping[str, Any]


SnapshotLoader = Callable[[str], WorkspaceProviderSnapshot | None]
Decryptor = Callable[[str], str]


def load_workspace_provider_snapshot(
    workspace_id: str,
) -> WorkspaceProviderSnapshot | None:
    """Load only one workspace and its explicitly selected provider rows."""

    from src.db.database import SessionLocal
    from src.db.models import Department, DepartmentAIProviderConfig

    db = SessionLocal()
    try:
        department = db.query(Department).filter(Department.id == workspace_id).first()
        if department is None:
            return None
        selected = tuple(
            provider
            for provider in (
                department.ai_primary_provider,
                department.ai_fallback_provider,
            )
            if provider is not None
        )
        rows: dict[str, WorkspaceProviderRow] = {}
        if selected:
            persisted = (
                db.query(DepartmentAIProviderConfig)
                .filter(
                    DepartmentAIProviderConfig.department_id == workspace_id,
                    DepartmentAIProviderConfig.provider.in_(selected),
                )
                .all()
            )
            rows = {
                row.provider: WorkspaceProviderRow(
                    provider=row.provider,
                    api_key_encrypted=row.api_key_encrypted,
                    text_model=row.text_model,
                    code_model=row.code_model,
                    vision_model=row.vision_model,
                )
                for row in persisted
            }
        return WorkspaceProviderSnapshot(
            workspace_id=department.id,
            primary=department.ai_primary_provider,
            fallback=department.ai_fallback_provider,
            rows=rows,
        )
    finally:
        db.close()


def _production_decryptor(value: str) -> str:
    from src.utils.encryption import decrypt_api_key

    return decrypt_api_key(value)


class WorkspaceProviderRuntime:
    """Manager-compatible AI client bounded to exactly one workspace."""

    def __init__(
        self,
        workspace_id: str | None,
        *,
        snapshot_loader: SnapshotLoader = load_workspace_provider_snapshot,
        decryptor: Decryptor = _production_decryptor,
        provider_factory: ProviderFactory = create_provider_instance,
    ) -> None:
        self.workspace_id = workspace_id if isinstance(workspace_id, str) else None
        self._snapshot_loader = snapshot_loader
        self._decryptor = decryptor
        self._provider_factory = provider_factory

    @staticmethod
    def _error(code: str, attempted: list[str] | None = None) -> LLMResponse:
        return LLMResponse.error_response(
            error=code,
            provider=attempted[-1] if attempted else "none",
            model="",
            metadata={"attempted_providers": list(attempted or [])},
        )

    def _load(self) -> WorkspaceProviderSnapshot | None:
        if not self.workspace_id:
            return None
        try:
            snapshot = self._snapshot_loader(self.workspace_id)
        except Exception:
            return None
        if snapshot is None or snapshot.workspace_id != self.workspace_id:
            return None
        return snapshot

    async def _execute(
        self,
        operation: str,
        *,
        provider: ProviderType | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        snapshot = self._load()
        if snapshot is None:
            return self._error("workspace_provider_unavailable")
        if snapshot.primary is None:
            return self._error("workspace_provider_not_selected")
        if provider is not None and provider.value != snapshot.primary:
            return self._error("workspace_provider_override_forbidden")

        selected = [snapshot.primary]
        if snapshot.fallback is not None and snapshot.fallback != snapshot.primary:
            selected.append(snapshot.fallback)

        attempted: list[str] = []
        for provider_name in selected:
            row = snapshot.rows.get(provider_name)
            if row is None:
                continue
            instance = None
            config = None
            attempted.append(provider_name)
            try:
                provider_type = ProviderType(provider_name)
                # Cloud plaintext is materialized only for the provider whose
                # operation is about to be attempted.
                config = provider_config_from_row(row, decryptor=self._decryptor)
                instance = self._provider_factory(provider_type, config)
                if await instance.initialize() is not True:
                    continue
                method = getattr(instance, operation)
                response = await method(**kwargs)
                if not isinstance(response, LLMResponse):
                    continue
                if response.success:
                    response.metadata = dict(response.metadata or {})
                    response.metadata["attempted_providers"] = attempted.copy()
                    return response
            except Exception:
                continue
            finally:
                try:
                    if instance is not None:
                        try:
                            await instance.close()
                        except Exception:
                            pass
                finally:
                    if config is not None:
                        config.api_key = None

        if not attempted:
            return self._error("workspace_provider_unavailable")
        return self._error("workspace_provider_attempts_failed", attempted)

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: str | None = None,
        provider: ProviderType | None = None,
        use_cache: bool = True,
    ) -> LLMResponse:
        del use_cache  # A shared cache without a workspace namespace is unsafe.
        return await self._execute(
            "generate_text",
            provider=provider,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
        )

    async def generate_code(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
        provider: ProviderType | None = None,
    ) -> LLMResponse:
        return await self._execute(
            "generate_code",
            provider=provider,
            prompt=prompt,
            language=language,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 500,
        provider: ProviderType | None = None,
        use_cache: bool = True,
    ) -> LLMResponse:
        del use_cache
        return await self._execute(
            "analyze_image",
            provider=provider,
            image_data=image_data,
            prompt=prompt,
            max_tokens=max_tokens,
        )

    async def generate_embedding(
        self,
        text: str,
        provider: ProviderType | None = None,
    ) -> LLMResponse:
        return await self._execute("generate_embedding", provider=provider, text=text)

    @staticmethod
    def _response_to_dict(response: LLMResponse) -> dict[str, Any]:
        if response.success:
            result: dict[str, Any] = {
                "success": True,
                "content": response.content,
                "inference_time": response.inference_time or 0.0,
                "provider": response.provider,
                "model": response.model,
            }
        else:
            result = {
                "success": False,
                "error": response.error or "workspace_provider_unavailable",
                "inference_time": response.inference_time or 0.0,
                "provider": response.provider,
                "model": response.model,
            }
        if response.metadata:
            result["metadata"] = response.metadata
        return result

    def generate_text_sync(self, **kwargs: Any) -> dict[str, Any]:
        return self._response_to_dict(run_async_from_sync(self.generate_text(**kwargs)))

    def generate_code_sync(self, **kwargs: Any) -> dict[str, Any]:
        return self._response_to_dict(run_async_from_sync(self.generate_code(**kwargs)))

    def analyze_image_sync(self, **kwargs: Any) -> dict[str, Any]:
        return self._response_to_dict(run_async_from_sync(self.analyze_image(**kwargs)))

    def health_check(self) -> dict[str, Any]:
        snapshot = self._load()
        if snapshot is None:
            return {
                "status": "unhealthy",
                "primary_provider": None,
                "fallback_provider": None,
                "error": "workspace_provider_unavailable",
            }
        ready = snapshot.primary is not None and snapshot.primary in snapshot.rows
        return {
            "status": "healthy" if ready else "unhealthy",
            "primary_provider": snapshot.primary,
            "fallback_provider": snapshot.fallback,
            "error": None if ready else "workspace_provider_not_selected",
        }


def workspace_provider_runtime(workspace_id: str | None) -> WorkspaceProviderRuntime:
    """Create a tenant runtime without consulting process-global state."""

    return WorkspaceProviderRuntime(workspace_id)
