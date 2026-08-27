"""Focused contracts for neutral open-core provider selection."""

from __future__ import annotations

import pytest

from src.ai.providers.base import LLMResponse
from src.ai.providers.manager import ProviderManager
from src.ai.providers.types import ProviderConfig, ProviderType


class _FakeProvider:
    def __init__(
        self,
        provider_type: ProviderType,
        *,
        initializes: bool = True,
        generates: bool = True,
    ):
        self.provider_type = provider_type
        self.name = provider_type.value
        self.display_name = provider_type.value.title()
        self.initializes = initializes
        self.generates = generates
        self._initialized = False
        self.calls: list[str] = []

    @property
    def is_available(self) -> bool:
        return self._initialized

    @property
    def is_local(self) -> bool:
        return self.provider_type is ProviderType.OLLAMA

    async def initialize(self) -> bool:
        self.calls.append("initialize")
        self._initialized = self.initializes
        return self._initialized

    async def close(self) -> None:
        self.calls.append("close")
        self._initialized = False

    async def generate_text(self, **_kwargs) -> LLMResponse:
        self.calls.append("generate_text")
        if not self.generates:
            return LLMResponse.error_response(
                error=f"{self.name} failed",
                provider=self.name,
                model=f"{self.name}-model",
            )
        return LLMResponse.success_response(
            content=f"from {self.name}",
            provider=self.name,
            model=f"{self.name}-model",
            inference_time=0.01,
        )

    def health_check(self) -> dict[str, str]:
        return {
            "status": "healthy" if self._initialized else "unhealthy",
            "provider": self.name,
        }


def _clear_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)


def _install_fake_factory(
    monkeypatch: pytest.MonkeyPatch,
    manager: ProviderManager,
    *,
    failures: set[ProviderType] | None = None,
    generation_failures: set[ProviderType] | None = None,
) -> tuple[list[ProviderType], dict[ProviderType, _FakeProvider]]:
    created: list[ProviderType] = []
    instances: dict[ProviderType, _FakeProvider] = {}
    failures = failures or set()
    generation_failures = generation_failures or set()

    def create(provider_type: ProviderType) -> _FakeProvider:
        created.append(provider_type)
        provider = _FakeProvider(
            provider_type,
            initializes=provider_type not in failures,
            generates=provider_type not in generation_failures,
        )
        instances[provider_type] = provider
        return provider

    monkeypatch.setattr(manager, "_create_provider", create)
    return created, instances


def test_no_provider_environment_is_neutral(monkeypatch: pytest.MonkeyPatch):
    _clear_provider_environment(monkeypatch)

    manager = ProviderManager()

    assert manager.primary_type is None
    assert manager.fallback_type is None
    assert manager.health_check() == {
        "status": "unhealthy",
        "primary_provider": None,
        "fallback_provider": None,
        "providers": {},
    }


@pytest.mark.parametrize("value", ["none", "", "not-a-provider"])
def test_disabled_empty_or_invalid_primary_does_not_select_gemini(
    monkeypatch: pytest.MonkeyPatch, value: str
):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", value)

    manager = ProviderManager()

    assert manager.primary_type is None


@pytest.mark.asyncio
async def test_initialize_creates_only_explicitly_selected_primary_and_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    manager = ProviderManager()
    created, _ = _install_fake_factory(monkeypatch, manager)

    assert await manager.initialize() is True

    assert created == [ProviderType.ANTHROPIC, ProviderType.OPENAI]
    assert set(manager._providers) == {ProviderType.ANTHROPIC, ProviderType.OPENAI}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", list(ProviderType))
async def test_every_supported_primary_selection_drives_generation(
    monkeypatch: pytest.MonkeyPatch, provider_type: ProviderType
):
    monkeypatch.setenv("LLM_PROVIDER", provider_type.value)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "none")
    manager = ProviderManager()
    created, instances = _install_fake_factory(monkeypatch, manager)

    response = await manager.generate_text("explain this", use_cache=False)

    assert created == [provider_type]
    assert response.success is True
    assert response.provider == provider_type.value
    assert instances[provider_type].calls == ["initialize", "generate_text"]


@pytest.mark.asyncio
async def test_fallback_can_be_the_only_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    manager = ProviderManager()
    created, instances = _install_fake_factory(monkeypatch, manager)

    response = await manager.generate_text("explain this", use_cache=False)

    assert created == [ProviderType.OPENAI]
    assert response.success is True
    assert response.provider == "openai"
    assert instances[ProviderType.OPENAI].calls == ["initialize", "generate_text"]


@pytest.mark.asyncio
async def test_failed_primary_uses_only_the_explicit_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "xai")
    manager = ProviderManager()
    created, instances = _install_fake_factory(
        monkeypatch, manager, failures={ProviderType.GEMINI}
    )

    response = await manager.generate_text("explain this", use_cache=False)

    assert created == [ProviderType.GEMINI, ProviderType.XAI]
    assert response.success is True
    assert response.provider == "xai"
    assert instances[ProviderType.GEMINI].calls == ["initialize"]
    assert instances[ProviderType.XAI].calls == ["initialize", "generate_text"]


@pytest.mark.asyncio
async def test_failed_chain_reports_each_provider_actually_attempted(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "ollama")
    manager = ProviderManager()
    _, instances = _install_fake_factory(
        monkeypatch,
        manager,
        generation_failures={ProviderType.GEMINI, ProviderType.OLLAMA},
    )

    response = await manager.generate_text("explain this", use_cache=False)

    assert response.success is False
    assert response.provider == "ollama"
    assert response.metadata["attempted_providers"] == ["gemini", "ollama"]
    assert instances[ProviderType.GEMINI].calls == ["initialize", "generate_text"]
    assert instances[ProviderType.OLLAMA].calls == ["initialize", "generate_text"]


@pytest.mark.asyncio
async def test_no_selected_provider_returns_a_bounded_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_provider_environment(monkeypatch)
    manager = ProviderManager()
    created, _ = _install_fake_factory(monkeypatch, manager)

    response = await manager.generate_text("explain this", use_cache=False)

    assert created == []
    assert response.success is False
    assert response.provider == "none"
    assert response.error == "No providers available"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", list(ProviderType))
async def test_byok_provider_can_be_added_and_selected_from_neutral_state(
    monkeypatch: pytest.MonkeyPatch, provider_type: ProviderType
):
    _clear_provider_environment(monkeypatch)
    manager = ProviderManager()
    _, instances = _install_fake_factory(monkeypatch, manager)

    added = await manager.add_provider(
        provider_type, ProviderConfig.default_for_provider(provider_type)
    )

    assert added is True
    assert manager.primary_type is None
    assert manager.set_primary_provider(provider_type) is True

    response = await manager.generate_text("explain this", use_cache=False)

    assert response.success is True
    assert response.provider == provider_type.value
    assert instances[provider_type].calls == ["initialize", "generate_text"]


@pytest.mark.asyncio
async def test_single_healthy_provider_reports_healthy(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)
    manager = ProviderManager()
    _install_fake_factory(monkeypatch, manager)
    await manager.initialize()

    health = manager.health_check()

    assert health["status"] == "healthy"
    assert health["primary_provider"] == "anthropic"
    assert health["fallback_provider"] is None
    assert set(health["providers"]) == {"anthropic"}


def test_ollama_primary_model_names_override_legacy_fallback_names(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("OLLAMA_TEXT_MODEL", "primary-text")
    monkeypatch.setenv("OLLAMA_CODE_MODEL", "primary-code")
    monkeypatch.setenv("OLLAMA_VISION_MODEL", "primary-vision")
    monkeypatch.setenv("OLLAMA_FALLBACK_TEXT", "legacy-text")
    monkeypatch.setenv("OLLAMA_FALLBACK_CODE", "legacy-code")
    monkeypatch.setenv("OLLAMA_FALLBACK_VISION", "legacy-vision")

    config = ProviderManager().configs[ProviderType.OLLAMA]

    assert config.text_model == "primary-text"
    assert config.code_model == "primary-code"
    assert config.vision_model == "primary-vision"


def test_ollama_legacy_model_names_remain_compatible(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_provider_environment(monkeypatch)
    monkeypatch.delenv("OLLAMA_TEXT_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_CODE_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_VISION_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_FALLBACK_TEXT", "legacy-text")
    monkeypatch.setenv("OLLAMA_FALLBACK_CODE", "legacy-code")
    monkeypatch.setenv("OLLAMA_FALLBACK_VISION", "legacy-vision")

    config = ProviderManager().configs[ProviderType.OLLAMA]

    assert config.text_model == "legacy-text"
    assert config.code_model == "legacy-code"
    assert config.vision_model == "legacy-vision"
