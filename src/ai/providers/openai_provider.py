"""
OpenAI LLM Provider.

Implements the LLMProvider interface for OpenAI's API (GPT-4, etc.).
Allows users to use their own OpenAI API keys.
"""

import base64
import time
import httpx
import logging
from typing import Dict, Any, Optional, List

from .base import LLMProvider, LLMResponse, ProviderCapability
from .types import ProviderConfig, ProviderType

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """
    OpenAI API provider.

    Supports text generation, code generation, and vision capabilities
    via the OpenAI API.
    """

    def __init__(self, config: Optional[ProviderConfig] = None):
        """
        Initialize OpenAI provider.

        Args:
            config: Optional provider configuration.
        """
        super().__init__()

        if config is None:
            config = ProviderConfig.default_for_provider(ProviderType.OPENAI)

        self.config = config
        self.api_key = config.api_key
        self.api_base = config.api_base or "https://api.openai.com/v1"
        self.text_model = config.text_model or "gpt-4o-mini"
        self.code_model = config.code_model or "gpt-4o"
        self.vision_model = config.vision_model or "gpt-4o"
        self.timeout = config.timeout

    @property
    def name(self) -> str:
        return "openai"

    @property
    def display_name(self) -> str:
        return "OpenAI"

    @property
    def capabilities(self) -> ProviderCapability:
        return (
            ProviderCapability.TEXT_GENERATION
            | ProviderCapability.CODE_GENERATION
            | ProviderCapability.VISION
            | ProviderCapability.EMBEDDINGS
            | ProviderCapability.STREAMING
            | ProviderCapability.FUNCTION_CALLING
        )

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    @property
    def is_local(self) -> bool:
        return False

    async def initialize(self) -> bool:
        """Initialize OpenAI provider."""
        if not self.api_key:
            logger.warning("OpenAI API key not configured")
            return False

        self._initialized = True
        logger.info(f"OpenAI provider initialized with models: text={self.text_model}")
        return True

    async def close(self) -> None:
        """Close OpenAI provider."""
        self._initialized = False

    def _build_messages(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        image_data: Optional[bytes] = None,
    ) -> List[Dict[str, Any]]:
        """Build messages array for OpenAI API."""
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if image_data:
            # Vision request
            image_b64 = base64.b64encode(image_data).decode("utf-8")
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                            },
                        },
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": prompt})

        return messages

    async def _call_api(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 500,
        temperature: float = 0.3,
    ) -> tuple[str, float, Optional[Dict[str, int]]]:
        """Make async call to OpenAI API."""
        start_time = time.perf_counter()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                    timeout=float(self.timeout),
                )

            elapsed = time.perf_counter() - start_time

            if response.status_code != 200:
                error_detail = response.text[:500]
                logger.warning(
                    f"OpenAI API error: {response.status_code} - {error_detail}"
                )
                return f"ERROR: {response.status_code} - {error_detail}", elapsed, None

            data = response.json()
            choices = data.get("choices", [])
            usage = data.get("usage", {})

            if choices:
                content = choices[0].get("message", {}).get("content", "")
                return content.strip(), elapsed, usage

            return "ERROR: No content in response", elapsed, None

        except httpx.TimeoutException:
            elapsed = time.perf_counter() - start_time
            return f"ERROR: Request timed out after {self.timeout}s", elapsed, None
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"OpenAI API exception: {e}")
            return f"ERROR: {e}", elapsed, None

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """Generate text using OpenAI."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="OpenAI API key not configured",
                provider=self.name,
                model=self.text_model,
            )

        messages = self._build_messages(prompt, system_prompt)
        content, elapsed, usage = await self._call_api(
            self.text_model, messages, max_tokens, temperature
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.text_model,
                inference_time=elapsed,
            )

        response = LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.text_model,
            inference_time=elapsed,
        )

        if usage:
            response.prompt_tokens = usage.get("prompt_tokens")
            response.completion_tokens = usage.get("completion_tokens")
            response.total_tokens = usage.get("total_tokens")

        return response

    async def generate_code(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Generate code using OpenAI."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="OpenAI API key not configured",
                provider=self.name,
                model=self.code_model,
            )

        system_prompt = f"You are an expert {language} developer. Generate clean, well-documented code."
        messages = self._build_messages(prompt, system_prompt)
        content, elapsed, usage = await self._call_api(
            self.code_model, messages, max_tokens, temperature
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.code_model,
                inference_time=elapsed,
            )

        response = LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.code_model,
            inference_time=elapsed,
        )

        if usage:
            response.prompt_tokens = usage.get("prompt_tokens")
            response.completion_tokens = usage.get("completion_tokens")
            response.total_tokens = usage.get("total_tokens")

        return response

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 500,
    ) -> LLMResponse:
        """Analyze an image using OpenAI Vision."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="OpenAI API key not configured",
                provider=self.name,
                model=self.vision_model,
            )

        messages = self._build_messages(prompt, image_data=image_data)
        content, elapsed, usage = await self._call_api(
            self.vision_model, messages, max_tokens, temperature=0.3
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.vision_model,
                inference_time=elapsed,
            )

        response = LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.vision_model,
            inference_time=elapsed,
        )

        if usage:
            response.prompt_tokens = usage.get("prompt_tokens")
            response.completion_tokens = usage.get("completion_tokens")
            response.total_tokens = usage.get("total_tokens")

        return response

    async def generate_embedding(
        self,
        text: str,
    ) -> LLMResponse:
        """Generate text embeddings using OpenAI."""
        if not self.is_available:
            return LLMResponse.error_response(
                error="OpenAI API key not configured",
                provider=self.name,
                model="text-embedding-3-small",
            )

        start_time = time.perf_counter()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "text-embedding-3-small",
                        "input": text,
                    },
                    timeout=float(self.timeout),
                )

            elapsed = time.perf_counter() - start_time

            if response.status_code != 200:
                return LLMResponse.error_response(
                    error=f"API error: {response.status_code}",
                    provider=self.name,
                    model="text-embedding-3-small",
                    inference_time=elapsed,
                )

            data = response.json()
            embedding = data.get("data", [{}])[0].get("embedding", [])

            return LLMResponse.success_response(
                content="",
                provider=self.name,
                model="text-embedding-3-small",
                inference_time=elapsed,
                metadata={"embedding": embedding, "dimensions": len(embedding)},
            )

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            return LLMResponse.error_response(
                error=str(e),
                provider=self.name,
                model="text-embedding-3-small",
                inference_time=elapsed,
            )

    def health_check(self) -> Dict[str, Any]:
        """Check OpenAI provider health."""
        if not self.api_key:
            return {
                "status": "unhealthy",
                "provider": self.name,
                "error": "API key not configured",
            }

        return {
            "status": "healthy",
            "provider": self.name,
            "display_name": self.display_name,
            "text_model": self.text_model,
            "code_model": self.code_model,
            "vision_model": self.vision_model,
            "api_base": self.api_base,
            "is_local": self.is_local,
        }

    def get_available_models(self) -> List[str]:
        """Get available OpenAI models."""
        return [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo",
        ]
