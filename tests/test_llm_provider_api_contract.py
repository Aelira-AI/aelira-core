"""Wire-contract coverage for the provider settings API."""

import pytest

from src.ai.providers.types import ProviderType
from src.api import llm_providers


class _Provider:
    def __init__(self, provider_type: ProviderType) -> None:
        self.display_name = provider_type.value.title()
        self.is_available = provider_type == ProviderType.OLLAMA
        self.is_local = provider_type == ProviderType.OLLAMA
        self._provider_type = provider_type

    def health_check(self):
        return {
            "status": "healthy" if self.is_available else "not_configured",
            "text_model": f"{self._provider_type.value}-text",
            "code_model": f"{self._provider_type.value}-code",
            "vision_model": f"{self._provider_type.value}-vision",
        }


class _ProviderManager:
    _initialized = True
    primary_type = ProviderType.ANTHROPIC
    fallback_type = ProviderType.OLLAMA

    def __init__(self) -> None:
        self.providers = {
            provider_type: _Provider(provider_type) for provider_type in ProviderType
        }

    def health_check(self):
        return {}

    def get_provider(self, provider_type: ProviderType):
        return self.providers[provider_type]


@pytest.mark.asyncio
async def test_provider_list_preserves_keyed_wire_contract(monkeypatch):
    monkeypatch.setattr(
        llm_providers,
        "get_provider_manager",
        lambda: _ProviderManager(),
    )

    response = await llm_providers.list_providers(
        api_key_info=(None, "contract-test", "department"),
    )
    payload = response.model_dump()

    assert set(payload) == {"primary", "fallback", "providers"}
    assert payload["primary"] == "anthropic"
    assert payload["fallback"] == "ollama"
    assert set(payload["providers"]) == {
        "ollama",
        "gemini",
        "openai",
        "anthropic",
        "xai",
    }
    assert payload["providers"]["ollama"] == {
        "name": "ollama",
        "display_name": "Ollama",
        "is_available": True,
        "is_local": True,
        "status": "healthy",
        "text_model": "ollama-text",
        "code_model": "ollama-code",
        "vision_model": "ollama-vision",
    }
