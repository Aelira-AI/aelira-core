"""Provider-neutral type and model configuration definitions.

Open-core users explicitly choose any supported provider, may override its
models, and can bring their own API key via ``POST /llm/providers/add``.
"""

from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass, field


class ProviderType(str, Enum):
    """Available LLM provider types."""

    GEMINI = "gemini"
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    XAI = "xai"

    @classmethod
    def from_string(cls, value: str) -> "ProviderType":
        """Convert string to ProviderType, case-insensitive."""
        try:
            return cls(value.lower())
        except ValueError:
            raise ValueError(
                f"Unknown provider: {value}. "
                f"Available: {', '.join(p.value for p in cls)}"
            )


@dataclass
class ModelConfig:
    """Configuration for a specific model."""

    name: str
    max_tokens: int = 4096
    supports_vision: bool = False
    supports_code: bool = True
    context_window: int = 8192
    description: str = ""


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider."""

    provider_type: ProviderType
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    host: Optional[str] = None  # For Ollama

    # Model selections
    text_model: Optional[str] = None
    code_model: Optional[str] = None
    vision_model: Optional[str] = None
    embedding_model: Optional[str] = None

    # Behavior settings
    timeout: int = 120
    max_retries: int = 3
    enabled: bool = True

    # Provider-specific options
    options: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def default_for_provider(cls, provider_type: ProviderType) -> "ProviderConfig":
        """Get default configuration for a provider type."""
        defaults = {
            ProviderType.GEMINI: cls(
                provider_type=ProviderType.GEMINI,
                api_base="https://generativelanguage.googleapis.com/v1beta",
                # Model default used only when a workspace selects Gemini.
                text_model="gemini-2.5-flash",
                code_model="gemini-2.5-flash",
                vision_model="gemini-2.5-flash",
            ),
            ProviderType.OLLAMA: cls(
                provider_type=ProviderType.OLLAMA,
                host="http://localhost:11434",
                text_model="gemma3:4b",  # Warm humanized explanations (3.3GB)
                code_model="qwen2.5-coder:7b",  # Best HTML/ARIA code fixes (4.7GB)
                vision_model="qwen2.5vl:3b",  # OCR, charts, 125K context (3.2GB)
                embedding_model="nomic-embed-text",
            ),
            ProviderType.OPENAI: cls(
                provider_type=ProviderType.OPENAI,
                api_base="https://api.openai.com/v1",
                text_model="gpt-4o-mini",
                code_model="gpt-4o",
                vision_model="gpt-4o",
            ),
            ProviderType.ANTHROPIC: cls(
                provider_type=ProviderType.ANTHROPIC,
                api_base="https://api.anthropic.com/v1",
                text_model="claude-3-5-sonnet-20241022",
                code_model="claude-3-5-sonnet-20241022",
                vision_model="claude-3-5-sonnet-20241022",
            ),
            ProviderType.XAI: cls(
                provider_type=ProviderType.XAI,
                api_base="https://api.x.ai/v1",
                text_model="grok-2",
                code_model="grok-2",
                vision_model="grok-2-vision",
            ),
        }
        return defaults.get(provider_type, cls(provider_type=provider_type))


# Default model configurations per provider
PROVIDER_MODELS: Dict[ProviderType, Dict[str, ModelConfig]] = {
    ProviderType.GEMINI: {
        # Recommended for accuracy (paid tiers)
        "gemini-3-flash-preview": ModelConfig(
            name="gemini-3-flash-preview",
            max_tokens=8192,
            supports_vision=True,
            context_window=1048576,
            description="100% accuracy, 5.4s avg - high-accuracy Gemini option",
        ),
        # Recommended default — fast, good quality, affordable
        "gemini-2.5-flash": ModelConfig(
            name="gemini-2.5-flash",
            max_tokens=8192,
            supports_vision=True,
            context_window=1048576,
            description="Recommended default — fast, multimodal, Tier 1: 1,000 RPM",
        ),
        "gemini-2.5-pro": ModelConfig(
            name="gemini-2.5-pro",
            max_tokens=8192,
            supports_vision=True,
            context_window=1048576,
            description="Most capable pro model (higher cost)",
        ),
    },
    ProviderType.OLLAMA: {
        "llama3.2:3b": ModelConfig(
            name="llama3.2:3b",
            max_tokens=4096,
            context_window=128000,
            description="Fast general-purpose model",
        ),
        "qwen2.5-coder:3b": ModelConfig(
            name="qwen2.5-coder:3b",
            max_tokens=4096,
            supports_code=True,
            context_window=32768,
            description="Best for code generation (100% accuracy)",
        ),
        "qwen2.5-coder:1.5b": ModelConfig(
            name="qwen2.5-coder:1.5b",
            max_tokens=4096,
            supports_code=True,
            context_window=32768,
            description="Lighter coder model for limited RAM",
        ),
        "llava:7b": ModelConfig(
            name="llava:7b",
            max_tokens=4096,
            supports_vision=True,
            context_window=4096,
            description="Vision model for image analysis",
        ),
        "minicpm-v:latest": ModelConfig(
            name="minicpm-v:latest",
            max_tokens=4096,
            supports_vision=True,
            context_window=4096,
            description="Best accuracy vision model (54% vs moondream 10%)",
        ),
    },
    ProviderType.OPENAI: {
        "gpt-4o": ModelConfig(
            name="gpt-4o",
            max_tokens=16384,
            supports_vision=True,
            context_window=128000,
            description="Most capable OpenAI model",
        ),
        "gpt-4o-mini": ModelConfig(
            name="gpt-4o-mini",
            max_tokens=16384,
            supports_vision=True,
            context_window=128000,
            description="Fast and affordable",
        ),
        "gpt-4-turbo": ModelConfig(
            name="gpt-4-turbo",
            max_tokens=4096,
            supports_vision=True,
            context_window=128000,
            description="Previous generation flagship",
        ),
    },
    ProviderType.ANTHROPIC: {
        "claude-3-5-sonnet-20241022": ModelConfig(
            name="claude-3-5-sonnet-20241022",
            max_tokens=8192,
            supports_vision=True,
            context_window=200000,
            description="Best balance of speed and capability",
        ),
        "claude-3-opus-20240229": ModelConfig(
            name="claude-3-opus-20240229",
            max_tokens=4096,
            supports_vision=True,
            context_window=200000,
            description="Most capable Claude model",
        ),
        "claude-3-5-haiku-20241022": ModelConfig(
            name="claude-3-5-haiku-20241022",
            max_tokens=8192,
            supports_vision=True,
            context_window=200000,
            description="Fastest Claude model",
        ),
    },
    ProviderType.XAI: {
        "grok-2": ModelConfig(
            name="grok-2",
            max_tokens=131072,
            supports_vision=False,
            context_window=131072,
            description="Most capable Grok model for text and code",
        ),
        "grok-2-vision": ModelConfig(
            name="grok-2-vision",
            max_tokens=32768,
            supports_vision=True,
            context_window=32768,
            description="Grok model with vision capabilities",
        ),
        "grok-2-mini": ModelConfig(
            name="grok-2-mini",
            max_tokens=131072,
            supports_vision=False,
            context_window=131072,
            description="Fast and affordable Grok model",
        ),
    },
}
