"""
LLM Provider Abstraction Layer.

This module provides a unified interface for multiple LLM providers,
allowing users to choose between local (Ollama) and cloud (Gemini, OpenAI, Anthropic, xAI)
providers based on their preferences, hardware, and privacy requirements.

Usage:
    from src.ai.providers import get_provider_manager, ProviderType

    # Get the global provider manager
    manager = get_provider_manager()

    # Generate text using the user's preferred provider
    response = await manager.generate_text("Explain WCAG 2.1")

    # Or use a specific provider
    response = await manager.generate_text("Explain WCAG 2.1", provider=ProviderType.OLLAMA)

Configuration:
    Set via environment variables or user settings:
    - LLM_PROVIDER: Primary provider (gemini, ollama, openai, anthropic, xai)
    - LLM_FALLBACK_PROVIDER: Fallback provider when primary fails
    - OPENAI_API_KEY: OpenAI API key (for openai provider)
    - ANTHROPIC_API_KEY: Anthropic API key (for anthropic provider)
    - GEMINI_API_KEY: Gemini API key (for gemini provider)
    - XAI_API_KEY: xAI API key (for xai/grok provider)
    - OLLAMA_HOST: Ollama server URL (for ollama provider)
"""

from importlib import import_module

from .base import LLMProvider, LLMResponse, ProviderCapability
from .types import ProviderConfig, ProviderType

_MANAGER_EXPORTS = {
    "ProviderManager",
    "get_provider_manager",
    "initialize_provider_manager",
    "close_provider_manager",
}


def __getattr__(name):
    """Load process-wide manager APIs only when legacy callers request them."""
    if name in _MANAGER_EXPORTS:
        return getattr(import_module(".manager", __name__), name)
    raise AttributeError(name)


__all__ = [
    "LLMProvider",
    "LLMResponse",
    "ProviderCapability",
    "ProviderType",
    "ProviderConfig",
    "ProviderManager",
    "get_provider_manager",
    "initialize_provider_manager",
    "close_provider_manager",
]
