"""
Ollama LLM Provider (Local).

Implements the LLMProvider interface for Ollama, providing local AI inference
without sending data to external servers.
"""

import base64
import time
import logging
import asyncio
from typing import Dict, Any, Optional, List

from .base import LLMProvider, LLMResponse, ProviderCapability
from .types import ProviderConfig, ProviderType

logger = logging.getLogger(__name__)

# Models that require thinking mode to be disabled
THINKING_MODE_MODELS = ["qwen3", "deepseek-r1"]


class OllamaProvider(LLMProvider):
    """
    Ollama local LLM provider.

    Runs AI inference locally using Ollama, ensuring data privacy
    and no external API dependencies.
    """

    def __init__(self, config: Optional[ProviderConfig] = None):
        """
        Initialize Ollama provider.

        Args:
            config: Optional provider configuration. If not provided,
                   will use defaults.
        """
        super().__init__()

        if config is None:
            config = ProviderConfig.default_for_provider(ProviderType.OLLAMA)

        self.config = config
        self.host = config.host or "http://localhost:11434"
        self.text_model = config.text_model or "llama3.2:3b"
        self.code_model = config.code_model or "qwen2.5-coder:3b"
        self.vision_model = config.vision_model or "llava:7b"
        self.embedding_model = config.embedding_model or "nomic-embed-text"
        self.timeout = config.timeout

        self._available_models: List[str] = []

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def display_name(self) -> str:
        return "Ollama (Local)"

    @property
    def capabilities(self) -> ProviderCapability:
        return (
            ProviderCapability.TEXT_GENERATION
            | ProviderCapability.CODE_GENERATION
            | ProviderCapability.VISION
            | ProviderCapability.EMBEDDINGS
        )

    @property
    def is_available(self) -> bool:
        """Check if Ollama is running and has models."""
        try:
            import ollama

            models_response = ollama.list()
            if hasattr(models_response, "models"):
                return len(models_response.models) > 0
            elif isinstance(models_response, dict) and "models" in models_response:
                return len(models_response["models"]) > 0
            return False
        except Exception:
            return False

    @property
    def is_local(self) -> bool:
        return True

    async def initialize(self) -> bool:
        """Initialize Ollama provider."""
        try:
            import ollama

            # Check if Ollama is running
            models_response = ollama.list()

            if hasattr(models_response, "models"):
                self._available_models = [m.model for m in models_response.models]
            elif isinstance(models_response, dict) and "models" in models_response:
                self._available_models = [m["name"] for m in models_response["models"]]
            else:
                self._available_models = []

            if not self._available_models:
                logger.warning("Ollama is running but no models are installed")
                return False

            self._initialized = True
            logger.info(
                f"Ollama provider initialized with {len(self._available_models)} models"
            )
            return True

        except ImportError:
            logger.error("ollama package not installed. Run: pip install ollama")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Ollama: {e}")
            return False

    async def close(self) -> None:
        """Close Ollama provider."""
        self._initialized = False
        self._available_models = []

    @staticmethod
    def _requires_no_think(model_name: str) -> bool:
        """Check if model requires thinking mode to be disabled."""
        model_lower = model_name.lower()
        return any(
            thinking_model in model_lower for thinking_model in THINKING_MODE_MODELS
        )

    def _prepare_prompt(self, prompt: str, model_name: str) -> str:
        """Prepare prompt with model-specific prefixes."""
        if self._requires_no_think(model_name):
            return f"/no_think\n\n{prompt}"
        return prompt

    @staticmethod
    def _clean_response(content: str) -> str:
        """Clean response content, removing thinking mode artifacts."""
        import re

        content = re.sub(r"<think>\s*</think>", "", content)
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        return content.strip()

    def _check_model_available(self, model: str) -> bool:
        """Check if a model is available."""
        return any(model in m for m in self._available_models)

    async def _call_ollama(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 500,
        temperature: float = 0.3,
        images: Optional[List[str]] = None,
    ) -> tuple[str, float]:
        """Make call to Ollama."""
        start_time = time.perf_counter()

        try:
            import ollama

            options = {
                "temperature": temperature,
                "num_predict": max_tokens,
            }

            # Add images if provided (for vision models)
            if images:
                messages[-1]["images"] = images

            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: ollama.chat(
                    model=model,
                    messages=messages,
                    options=options,
                ),
            )

            elapsed = time.perf_counter() - start_time
            content = self._clean_response(response["message"]["content"])
            return content, elapsed

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"Ollama error: {e}")
            return f"ERROR: {e}", elapsed

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """Generate text using Ollama."""
        if not self._initialized:
            await self.initialize()

        if not self._check_model_available(self.text_model):
            return LLMResponse.error_response(
                error=f"Model {self.text_model} not available. Install with: ollama pull {self.text_model}",
                provider=self.name,
                model=self.text_model,
            )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        prepared_prompt = self._prepare_prompt(prompt, self.text_model)
        messages.append({"role": "user", "content": prepared_prompt})

        content, elapsed = await self._call_ollama(
            self.text_model, messages, max_tokens, temperature
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.text_model,
                inference_time=elapsed,
            )

        return LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.text_model,
            inference_time=elapsed,
        )

    async def generate_code(
        self,
        prompt: str,
        language: str = "html",
        max_tokens: int = 1000,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Generate code using Ollama."""
        if not self._initialized:
            await self.initialize()

        if not self._check_model_available(self.code_model):
            return LLMResponse.error_response(
                error=f"Model {self.code_model} not available. Install with: ollama pull {self.code_model}",
                provider=self.name,
                model=self.code_model,
            )

        system_prompt = f"You are an expert {language} developer. Generate clean, well-documented code."
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        prepared_prompt = self._prepare_prompt(prompt, self.code_model)
        messages.append({"role": "user", "content": prepared_prompt})

        content, elapsed = await self._call_ollama(
            self.code_model, messages, max_tokens, temperature
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.code_model,
                inference_time=elapsed,
            )

        return LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.code_model,
            inference_time=elapsed,
        )

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 500,
    ) -> LLMResponse:
        """Analyze an image using Ollama Vision models."""
        if not self._initialized:
            await self.initialize()

        if not self._check_model_available(self.vision_model):
            return LLMResponse.error_response(
                error=f"Vision model {self.vision_model} not available. Install with: ollama pull {self.vision_model}",
                provider=self.name,
                model=self.vision_model,
            )

        # Encode image as base64
        image_b64 = base64.b64encode(image_data).decode("utf-8")

        messages = [{"role": "user", "content": prompt}]
        content, elapsed = await self._call_ollama(
            self.vision_model,
            messages,
            max_tokens,
            temperature=0.3,
            images=[image_b64],
        )

        if content.startswith("ERROR:"):
            return LLMResponse.error_response(
                error=content,
                provider=self.name,
                model=self.vision_model,
                inference_time=elapsed,
            )

        return LLMResponse.success_response(
            content=content,
            provider=self.name,
            model=self.vision_model,
            inference_time=elapsed,
        )

    async def generate_embedding(
        self,
        text: str,
    ) -> LLMResponse:
        """Generate text embeddings using Ollama."""
        if not self._initialized:
            await self.initialize()

        if not self._check_model_available(self.embedding_model):
            return LLMResponse.error_response(
                error=f"Embedding model {self.embedding_model} not available",
                provider=self.name,
                model=self.embedding_model,
            )

        start_time = time.perf_counter()

        try:
            import ollama

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: ollama.embeddings(
                    model=self.embedding_model,
                    prompt=text,
                ),
            )

            elapsed = time.perf_counter() - start_time
            embedding = response.get("embedding", [])

            return LLMResponse.success_response(
                content="",
                provider=self.name,
                model=self.embedding_model,
                inference_time=elapsed,
                metadata={"embedding": embedding, "dimensions": len(embedding)},
            )

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            return LLMResponse.error_response(
                error=str(e),
                provider=self.name,
                model=self.embedding_model,
                inference_time=elapsed,
            )

    def health_check(self) -> Dict[str, Any]:
        """Check Ollama provider health."""
        try:
            import ollama

            models_response = ollama.list()

            if hasattr(models_response, "models"):
                available = [m.model for m in models_response.models]
            elif isinstance(models_response, dict) and "models" in models_response:
                available = [m["name"] for m in models_response["models"]]
            else:
                available = []

            text_available = any(self.text_model in m for m in available)
            code_available = any(self.code_model in m for m in available)
            vision_available = any(self.vision_model in m for m in available)

            status = "healthy" if (text_available and code_available) else "degraded"
            if not available:
                status = "unhealthy"

            return {
                "status": status,
                "provider": self.name,
                "display_name": self.display_name,
                "host": self.host,
                "is_local": self.is_local,
                "text_model": self.text_model,
                "text_model_available": text_available,
                "code_model": self.code_model,
                "code_model_available": code_available,
                "vision_model": self.vision_model,
                "vision_model_available": vision_available,
                "embedding_model": self.embedding_model,
                "total_models": len(available),
                "available_models": available,
            }

        except ImportError:
            return {
                "status": "unhealthy",
                "provider": self.name,
                "error": "ollama package not installed",
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "provider": self.name,
                "error": str(e),
            }

    def get_available_models(self) -> List[str]:
        """Get available Ollama models."""
        return self._available_models.copy()

    @staticmethod
    def get_recommended_models_for_hardware(
        ram_gb: int, has_gpu: bool = False
    ) -> Dict[str, Any]:
        """Get recommended model configuration based on hardware specs."""
        # Updated Jan 2026: minicpm-v (54% accuracy) replaces moondream (10% accuracy)
        if has_gpu:
            return {
                "profile": "performance",
                "text_model": "llama3.2:3b",
                "code_model": "qwen2.5-coder:3b",
                "vision_model": "minicpm-v:latest",  # Best accuracy (54%)
                "reason": "GPU available - using larger models for maximum accuracy",
            }
        elif ram_gb >= 32:
            return {
                "profile": "performance",
                "text_model": "llama3.2:3b",
                "code_model": "qwen2.5-coder:3b",
                "vision_model": "minicpm-v:latest",  # 54% accuracy, 5GB
                "reason": "32GB+ RAM - using 3B model for all tasks",
            }
        elif ram_gb >= 16:
            return {
                "profile": "recommended",
                "text_model": "llama3.2:3b",
                "code_model": "qwen2.5-coder:1.5b",
                "vision_model": "minicpm-v:latest",  # 54% accuracy, fits in 16GB
                "reason": "16-32GB RAM - balanced speed/accuracy",
            }
        else:
            return {
                "profile": "minimal",
                "text_model": "llama3.2:1b",
                "code_model": "qwen2.5-coder:1.5b",
                "vision_model": "minicpm-v:latest",  # Still use minicpm-v for accuracy
                "reason": "Under 16GB RAM - using smaller models",
            }
