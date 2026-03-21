"""
Base abstract class for LLM providers.

All provider implementations must inherit from LLMProvider and implement
the required abstract methods.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Flag, auto
from typing import Dict, Any, Optional, List
import time


class ProviderCapability(Flag):
    """Capabilities that a provider may support."""

    NONE = 0
    TEXT_GENERATION = auto()
    CODE_GENERATION = auto()
    VISION = auto()
    EMBEDDINGS = auto()
    STREAMING = auto()
    FUNCTION_CALLING = auto()

    @classmethod
    def all(cls) -> "ProviderCapability":
        """Return all capabilities."""
        return (
            cls.TEXT_GENERATION
            | cls.CODE_GENERATION
            | cls.VISION
            | cls.EMBEDDINGS
            | cls.STREAMING
            | cls.FUNCTION_CALLING
        )


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""

    success: bool
    content: str
    provider: str
    model: str
    inference_time: float
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Token usage (if available)
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    def __post_init__(self):
        """Ensure metadata is always a dict."""
        if self.metadata is None:
            self.metadata = {}

    @classmethod
    def error_response(
        cls,
        error: str,
        provider: str,
        model: str,
        inference_time: float = 0.0,
    ) -> "LLMResponse":
        """Create an error response."""
        return cls(
            success=False,
            content="",
            provider=provider,
            model=model,
            inference_time=inference_time,
            error=error,
        )

    @classmethod
    def success_response(
        cls,
        content: str,
        provider: str,
        model: str,
        inference_time: float,
        **kwargs,
    ) -> "LLMResponse":
        """Create a success response."""
        return cls(
            success=True,
            content=content,
            provider=provider,
            model=model,
            inference_time=inference_time,
            **kwargs,
        )


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.

    All provider implementations must inherit from this class and implement
    the required abstract methods. This ensures a consistent interface
    across all providers (Gemini, Ollama, OpenAI, Anthropic, etc.).
    """

    def __init__(self):
        """Initialize base provider."""
        self._initialized = False

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Provider name identifier.

        Returns:
            str: Provider name (e.g., 'gemini', 'ollama', 'openai')
        """
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """
        Human-readable provider name.

        Returns:
            str: Display name (e.g., 'Google Gemini', 'Ollama (Local)')
        """
        pass

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapability:
        """
        Provider capabilities.

        Returns:
            ProviderCapability: Flags indicating supported capabilities
        """
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if provider is configured and available.

        Returns:
            bool: True if provider can be used
        """
        pass

    @property
    def is_local(self) -> bool:
        """
        Check if provider runs locally (no data sent to external servers).

        Returns:
            bool: True for local providers like Ollama
        """
        return False

    @abstractmethod
    async def initialize(self) -> bool:
        """
        Initialize the provider (async setup).

        Call this before using the provider to set up connections,
        verify API keys, etc.

        Returns:
            bool: True if initialization successful
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Clean up provider resources.

        Call this when done using the provider.
        """
        pass

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """
        Generate text completion.

        Args:
            prompt: The input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0-1)
            system_prompt: Optional system instructions

        Returns:
            LLMResponse: Standardized response
        """
        pass

    @abstractmethod
    async def generate_code(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """
        Generate code.

        Args:
            prompt: The code generation prompt
            language: Target programming language
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0-1)

        Returns:
            LLMResponse: Standardized response with generated code
        """
        pass

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 500,
    ) -> LLMResponse:
        """
        Analyze an image with a prompt.

        Args:
            image_data: Raw image bytes
            prompt: Analysis prompt
            max_tokens: Maximum tokens for response

        Returns:
            LLMResponse: Standardized response

        Raises:
            NotImplementedError: If provider doesn't support vision
        """
        if not (self.capabilities & ProviderCapability.VISION):
            return LLMResponse.error_response(
                error=f"Provider {self.name} does not support vision capabilities",
                provider=self.name,
                model="",
                inference_time=0.0,
            )
        raise NotImplementedError("Vision not implemented for this provider")

    async def generate_embedding(
        self,
        text: str,
    ) -> LLMResponse:
        """
        Generate text embeddings.

        Args:
            text: Text to embed

        Returns:
            LLMResponse: Response with embedding in metadata['embedding']

        Raises:
            NotImplementedError: If provider doesn't support embeddings
        """
        if not (self.capabilities & ProviderCapability.EMBEDDINGS):
            return LLMResponse.error_response(
                error=f"Provider {self.name} does not support embeddings",
                provider=self.name,
                model="",
                inference_time=0.0,
            )
        raise NotImplementedError("Embeddings not implemented for this provider")

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """
        Check provider health and availability.

        Returns:
            Dict with status information:
                - status: 'healthy', 'degraded', or 'unhealthy'
                - provider: Provider name
                - models: Available models
                - error: Error message if unhealthy
        """
        pass

    def get_available_models(self) -> List[str]:
        """
        Get list of available models for this provider.

        Returns:
            List[str]: Available model names
        """
        return []

    def supports_model(self, model_name: str) -> bool:
        """
        Check if provider supports a specific model.

        Args:
            model_name: Model name to check

        Returns:
            bool: True if model is supported
        """
        return model_name in self.get_available_models()

    def _measure_time(self, start_time: float) -> float:
        """Calculate elapsed time from start."""
        return time.perf_counter() - start_time

    def __repr__(self) -> str:
        """String representation."""
        return f"{self.__class__.__name__}(name={self.name}, available={self.is_available})"
