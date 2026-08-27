"""Provider-neutral contracts for the global image alt-text path."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from src.ai.providers.base import LLMResponse
from src.education.image_alt_text import ImageAltTextGenerator


def _image(tmp_path) -> str:
    path = tmp_path / "image.png"
    Image.new("RGB", (10, 10), color="blue").save(path)
    return str(path)


class _Manager:
    def __init__(
        self,
        response: LLMResponse,
        *,
        primary: str = "gemini",
        fallback: str | None = "ollama",
    ) -> None:
        self.response = response
        self.primary_type = SimpleNamespace(value=primary)
        self.fallback_type = (
            SimpleNamespace(value=fallback) if fallback is not None else None
        )
        self.calls: list[dict] = []

    async def analyze_image(self, **kwargs) -> LLMResponse:
        self.calls.append(kwargs)
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["gemini", "ollama", "openai", "anthropic", "xai"])
async def test_global_vision_uses_manager_result_provider_and_model(tmp_path, provider):
    manager = _Manager(
        LLMResponse.success_response(
            content="Blue square",
            provider=provider,
            model=f"{provider}-vision-model",
            inference_time=0.25,
            metadata={"attempted_providers": [provider]},
        ),
        primary=provider,
        fallback=None,
    )
    generator = ImageAltTextGenerator(allow_legacy_transport=True)

    with (
        patch("src.ai.providers.get_provider_manager", return_value=manager),
        patch.object(
            generator,
            "_generate_with_gemini",
            side_effect=AssertionError("direct Gemini transport forbidden"),
        ),
        patch.object(
            generator,
            "_generate_with_ollama",
            side_effect=AssertionError("direct Ollama transport forbidden"),
        ),
    ):
        result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is True
    assert result["provider"] == provider
    assert result["model"] == f"{provider}-vision-model"
    assert manager.calls[0]["image_data"].startswith(b"\x89PNG")
    assert manager.calls[0]["max_tokens"] == 300


@pytest.mark.asyncio
async def test_global_vision_preserves_manager_fallback_attribution(tmp_path):
    manager = _Manager(
        LLMResponse.success_response(
            content="Blue square",
            provider="anthropic",
            model="claude-vision-fallback",
            inference_time=0.4,
            metadata={"attempted_providers": ["openai", "anthropic"]},
        ),
        primary="openai",
        fallback="anthropic",
    )
    generator = ImageAltTextGenerator(allow_legacy_transport=True)

    with patch("src.ai.providers.get_provider_manager", return_value=manager):
        result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is True
    assert result["provider"] == "anthropic"
    assert result["model"] == "claude-vision-fallback"
    assert generator.usage_metadata["providers_attempted"] == (
        "openai",
        "anthropic",
    )


@pytest.mark.asyncio
async def test_global_vision_failure_is_fail_closed_and_redacted(tmp_path):
    manager = _Manager(
        LLMResponse.error_response(
            error="secret upstream detail",
            provider="ollama",
            model="",
            inference_time=0.3,
            metadata={"attempted_providers": ["xai", "ollama"]},
        ),
        primary="xai",
        fallback="ollama",
    )
    generator = ImageAltTextGenerator(allow_legacy_transport=True)

    with patch("src.ai.providers.get_provider_manager", return_value=manager):
        result = await generator.generate_alt_text(_image(tmp_path))

    assert result == {
        "success": False,
        "error": "provider_call_failed",
        "inference_time": 0.3,
        "provider": "ollama",
    }
    assert "secret upstream detail" not in str(result)
    assert generator.usage_metadata["providers_attempted"] == ("xai", "ollama")
    assert generator.usage_metadata["provider"] is None
    assert generator.usage_metadata["external_ai_used"] is True


def test_global_vision_health_uses_manager_without_implicit_ollama_probe():
    manager = MagicMock()
    manager.health_check.return_value = {
        "status": "healthy",
        "primary_provider": "openai",
        "fallback_provider": None,
        "providers": {
            "openai": {
                "status": "healthy",
                "vision_model": "gpt-vision",
            }
        },
    }
    generator = ImageAltTextGenerator(allow_legacy_transport=True)

    with (
        patch("src.ai.providers.get_provider_manager", return_value=manager),
        patch("ollama.list", side_effect=AssertionError("implicit Ollama probe")),
    ):
        result = generator.health_check()

    assert result["status"] == "healthy"
    assert result["provider"] == "openai"
    assert result["vision_model"] == "gpt-vision"
    assert result["vision_available"] is True
    manager.health_check.assert_called_once_with()


def test_global_vision_health_reports_healthy_fallback_route():
    manager = MagicMock()
    manager.health_check.return_value = {
        "status": "degraded",
        "primary_provider": "gemini",
        "fallback_provider": "ollama",
        "providers": {
            "gemini": {
                "status": "unhealthy",
                "vision_model": "gemini-vision",
            },
            "ollama": {
                "status": "healthy",
                "vision_model": "local-vision",
            },
        },
    }
    generator = ImageAltTextGenerator(allow_legacy_transport=True)

    with patch("src.ai.providers.get_provider_manager", return_value=manager):
        result = generator.health_check()

    assert result["status"] == "healthy"
    assert result["provider_manager_status"] == "degraded"
    assert result["provider"] == "ollama"
    assert result["vision_model"] == "local-vision"
    assert result["vision_available"] is True


@pytest.mark.asyncio
async def test_education_health_reports_selected_provider_without_ollama_probe():
    from src.api.education.remediation_routes import health_check

    manager = MagicMock()
    manager.health_check.return_value = {
        "status": "healthy",
        "primary_provider": "anthropic",
        "fallback_provider": None,
        "providers": {
            "anthropic": {
                "status": "healthy",
                "vision_model": "claude-vision",
            }
        },
    }

    with (
        patch("src.ai.providers.get_provider_manager", return_value=manager),
        patch("ollama.list", side_effect=AssertionError("implicit Ollama probe")),
    ):
        result = await health_check()

    assert result["status"] == "healthy"
    assert result["vision_provider"] == "anthropic"
    assert result["vision_status"] == "healthy"
    assert result["vision_model"] == "claude-vision"
    assert result["vision_available"] is True
    assert "ai-aria-labels" in result["features"]
    assert "ollama-aria-labels" not in result["features"]


@pytest.mark.asyncio
async def test_global_vision_manager_acquisition_failure_is_fail_closed(tmp_path):
    generator = ImageAltTextGenerator(allow_legacy_transport=True)

    with patch(
        "src.ai.providers.get_provider_manager",
        side_effect=RuntimeError("secret manager setup detail"),
    ):
        result = await generator.generate_alt_text(_image(tmp_path))

    assert result == {
        "success": False,
        "error": "provider_call_failed",
        "inference_time": 0.0,
        "provider": "none",
    }
    assert "secret manager setup detail" not in str(result)


@pytest.mark.asyncio
async def test_injected_lms_client_never_acquires_global_manager(tmp_path):
    client = MagicMock()
    client.provider = "gemini"
    client.analyze_image_sync.return_value = {
        "success": True,
        "content": "Blue square",
        "inference_time": 0.2,
        "provider": "gemini",
        "model": "purpose-bound-model",
    }
    generator = ImageAltTextGenerator(lms_client=client)

    with patch(
        "src.ai.providers.get_provider_manager",
        side_effect=AssertionError("LMS path must remain purpose-bound"),
    ):
        result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is True
    assert result["model"] == "purpose-bound-model"
    client.analyze_image_sync.assert_called_once()
