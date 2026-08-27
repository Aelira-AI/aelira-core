"""The real Ollama startup seam must invoke WCAG bootstrap safely."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai.wcag_bootstrap import BootstrapResult
from src.ai.ollama_client import OllamaClient


def test_application_embedding_provider_default_is_none():
    from src.config.settings import Settings

    assert Settings.model_fields["embedding_provider"].default == "none"


@pytest.mark.asyncio
async def test_initialization_bootstraps_after_knowledge_base_connection():
    client = OllamaClient(
        host="http://ollama.test:11434",
        enable_rag=True,
        embedding_model="configured-embed",
    )
    order: list[str] = []
    kb = AsyncMock()

    async def initialize() -> None:
        order.append("initialize")

    async def bootstrap():
        order.append("bootstrap")
        return BootstrapResult(
            seeded=1,
            embedded=1,
            failed=0,
            model_available=True,
            grounding_available=True,
        )

    kb.initialize.side_effect = initialize
    kb.bootstrap.side_effect = bootstrap
    client.kb = kb

    ready = await client.initialize()

    assert order == ["initialize", "bootstrap"]
    assert ready is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_method", ["initialize", "bootstrap"])
async def test_degraded_knowledge_base_does_not_abort_api_startup(failing_method):
    client = OllamaClient(
        host="http://ollama.test:11434",
        enable_rag=True,
        embedding_model="configured-embed",
    )
    kb = AsyncMock()
    getattr(kb, failing_method).side_effect = RuntimeError("dependency unavailable")
    client.kb = kb

    ready = await client.initialize()

    assert ready is False
    assert client.enable_rag is False


@pytest.mark.asyncio
async def test_missing_embedding_model_disables_rag_without_raising():
    client = OllamaClient(
        host="http://ollama.test:11434",
        enable_rag=True,
        embedding_model="configured-embed",
    )
    kb = AsyncMock()
    kb.bootstrap.return_value = BootstrapResult(
        seeded=112,
        embedded=0,
        failed=0,
        model_available=False,
        grounding_available=False,
    )
    client.kb = kb

    ready = await client.initialize()

    assert ready is False
    assert client.enable_rag is False


@pytest.mark.asyncio
async def test_total_embedding_failure_disables_rag_without_raising():
    client = OllamaClient(
        host="http://ollama.test:11434",
        enable_rag=True,
        embedding_model="configured-embed",
    )
    kb = AsyncMock()
    kb.bootstrap.return_value = BootstrapResult(
        seeded=112,
        embedded=0,
        failed=112,
        model_available=True,
        grounding_available=False,
    )
    client.kb = kb

    ready = await client.initialize()

    assert ready is False
    assert client.enable_rag is False


@pytest.mark.asyncio
async def test_concurrent_embedding_worker_stays_rag_enabled():
    client = OllamaClient(
        host="http://ollama.test:11434",
        enable_rag=True,
        embedding_model="configured-embed",
    )
    kb = AsyncMock()
    kb.bootstrap.return_value = BootstrapResult(
        seeded=0,
        embedded=0,
        failed=0,
        model_available=True,
        grounding_available=False,
        embedding_in_progress=True,
    )
    client.kb = kb

    ready = await client.initialize()

    assert ready is True
    assert client.enable_rag is True


def test_configured_embedding_model_reaches_knowledge_base():
    client = OllamaClient(
        host="http://ollama.test:11434",
        enable_rag=True,
        embedding_model="configured-embed",
    )

    assert client.kb is not None
    assert client.kb.ollama_host == "http://ollama.test:11434"
    assert client.kb.embedding_model == "configured-embed"


def test_configured_embedding_model_reaches_primary_gemini_rag(monkeypatch):
    import src.ai.gemini_client as gemini_module

    settings = SimpleNamespace(
        gemini_api_key="",
        gemini_api_base="https://gemini.test",
        gemini_text_model="gemini-test",
        gemini_code_model="gemini-code-test",
        use_gemini=False,
        ollama_host="http://ollama.test:11434",
        ollama_fallback_text="fallback-test",
        ollama_embedding_model="configured-embed",
        llm_fallback_provider="ollama",
    )
    monkeypatch.setattr(gemini_module, "get_settings", lambda: settings)

    client = gemini_module.GeminiClient(enable_rag=True)

    assert client.kb is not None
    assert client.kb.embedding_model == "configured-embed"


@pytest.mark.asyncio
async def test_provider_neutral_adapter_seeds_before_exact_grounding_is_ready():
    from src.ai.gemini_client import GeminiClient

    client = object.__new__(GeminiClient)
    client.enable_rag = True
    client._kb_initialized = False
    order: list[str] = []
    kb = AsyncMock()

    async def initialize() -> None:
        order.append("initialize")

    async def bootstrap():
        order.append("bootstrap")
        return BootstrapResult(
            seeded=112,
            embedded=0,
            failed=0,
            model_available=False,
            grounding_available=False,
        )

    kb.initialize.side_effect = initialize
    kb.bootstrap.side_effect = bootstrap
    client.kb = kb

    ready = await client.initialize_rag()

    assert ready is True
    assert client._kb_initialized is True
    assert order == ["initialize", "bootstrap"]


@pytest.mark.asyncio
@pytest.mark.parametrize("rag_ready", [True, False])
async def test_api_startup_propagates_wcag_state_to_provider_neutral_adapter(
    monkeypatch, rag_ready
):
    import src.api.main as api_main

    monkeypatch.setattr(
        api_main.accessibility_ai_client,
        "initialize_rag",
        AsyncMock(return_value=rag_ready),
    )
    monkeypatch.setattr(api_main, "initialize_provider_manager", AsyncMock())
    monkeypatch.setattr(
        api_main,
        "get_provider_manager",
        lambda: SimpleNamespace(primary_type=SimpleNamespace(value="test")),
    )

    def discard_background_task(coroutine):
        coroutine.close()
        return None

    monkeypatch.setattr(api_main.asyncio, "create_task", discard_background_task)
    api_main.accessibility_ai_client.enable_rag = not rag_ready

    await api_main.startup_event()

    assert api_main.accessibility_ai_client.enable_rag is rag_ready
