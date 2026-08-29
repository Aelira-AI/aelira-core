"""Tenant-bound provider configuration helpers.

Database rows are authoritative. Provider instances are constructed for one
bounded operation and are never registered with the process-global manager.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import re
from typing import Any, Callable

from src.ai.providers.types import ProviderConfig, ProviderType
from src.db.models import DepartmentAIProviderConfig

SUPPORTED_WORKSPACE_PROVIDERS = tuple(provider.value for provider in ProviderType)

PROVIDER_DISPLAY_NAMES = {
    "ollama": "Ollama",
    "gemini": "Google Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "xai": "xAI",
}

ProviderFactory = Callable[[ProviderType, ProviderConfig], Any]


def _safe_model_identifier(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}", value
    ):
        return value
    return ""


def _safe_inference_time(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else 0.0


def create_provider_instance(
    provider_type: ProviderType, config: ProviderConfig
) -> Any:
    """Construct one provider without touching global provider state."""

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
    raise ValueError("unsupported workspace provider")


def provider_config_from_row(
    row: DepartmentAIProviderConfig,
    *,
    decryptor: Callable[[str], str],
) -> ProviderConfig:
    """Resolve one database row into a disposable provider configuration."""

    provider_type = ProviderType(row.provider)
    config = ProviderConfig.default_for_provider(provider_type)
    if provider_type is ProviderType.OLLAMA:
        config.host = os.getenv("OLLAMA_HOST", config.host or "http://localhost:11434")
        config.api_key = None
    else:
        if not row.api_key_encrypted:
            raise ValueError("provider credential unavailable")
        config.api_key = decryptor(row.api_key_encrypted)

    if row.text_model is not None:
        config.text_model = row.text_model
    if row.code_model is not None:
        config.code_model = row.code_model
    if row.vision_model is not None:
        config.vision_model = row.vision_model
    return config


@dataclass(frozen=True)
class ProviderTestResult:
    success: bool
    provider: str
    model: str
    inference_time: float
    error: str | None = None


async def test_provider_row(
    row: DepartmentAIProviderConfig,
    *,
    decryptor: Callable[[str], str],
    provider_factory: ProviderFactory = create_provider_instance,
) -> ProviderTestResult:
    """Test one fresh tenant-bound provider and always close it."""

    instance = None
    try:
        config = provider_config_from_row(row, decryptor=decryptor)
        provider_type = ProviderType(row.provider)
        instance = provider_factory(provider_type, config)
        if await instance.initialize() is not True:
            return ProviderTestResult(
                success=False,
                provider=row.provider,
                model=_safe_model_identifier(config.text_model),
                inference_time=0.0,
                error="provider_initialization_failed",
            )
        response = await instance.generate_text(
            prompt="State that the provider connection is active in five words or fewer.",
            max_tokens=20,
            temperature=0,
        )
        if response.success is not True:
            return ProviderTestResult(
                success=False,
                provider=row.provider,
                model=_safe_model_identifier(config.text_model),
                inference_time=_safe_inference_time(response.inference_time),
                error="provider_test_failed",
            )
        return ProviderTestResult(
            success=True,
            provider=row.provider,
            model=_safe_model_identifier(config.text_model),
            inference_time=_safe_inference_time(response.inference_time),
        )
    except Exception:
        return ProviderTestResult(
            success=False,
            provider=row.provider,
            model=_safe_model_identifier(row.text_model),
            inference_time=0.0,
            error="provider_test_failed",
        )
    finally:
        if instance is not None:
            try:
                await instance.close()
            except Exception:
                pass
