"""Provider-neutral contracts for legacy accessibility analysis entry points."""

from __future__ import annotations

import pytest

from src.ai.providers.base import LLMResponse


class _ProviderManager:
    def __init__(self, provider: str, content: str) -> None:
        self.provider = provider
        self.content = content
        self.text_calls: list[str] = []
        self.code_calls: list[str] = []

    async def generate_text(self, prompt: str, **_kwargs) -> LLMResponse:
        self.text_calls.append(prompt)
        return LLMResponse.success_response(
            content=self.content,
            provider=self.provider,
            model=f"{self.provider}-text-test",
            inference_time=0.01,
        )

    def generate_text_sync(self, prompt: str, **_kwargs) -> LLMResponse:
        self.text_calls.append(prompt)
        return LLMResponse.success_response(
            content=self.content,
            provider=self.provider,
            model=f"{self.provider}-text-test",
            inference_time=0.01,
        )

    async def generate_code(self, prompt: str, **_kwargs) -> LLMResponse:
        self.code_calls.append(prompt)
        return LLMResponse.success_response(
            content=self.content,
            provider=self.provider,
            model=f"{self.provider}-code-test",
            inference_time=0.02,
        )


class _ExactKnowledgeBase:
    def __init__(self) -> None:
        self.rule_ids: list[str] = []
        self.search_called = False

    async def get_by_rule_id(self, rule_id: str):
        self.rule_ids.append(rule_id)
        return {
            "rule_id": rule_id,
            "title": "Image text alternatives",
            "wcag_criterion": "1.1.1",
            "wcag_level": "A",
            "description": "Images need equivalent text alternatives.",
            "severity_criteria": {"critical": "Content is unavailable."},
        }

    async def search(self, *_args, **_kwargs):
        self.search_called = True
        raise AssertionError("known rule IDs must not require semantic search")

    def format_guidelines_for_prompt(self, guidelines, **_kwargs) -> str:
        return f"Canonical rule: {guidelines[0]['rule_id']}"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["gemini", "ollama", "openai", "anthropic", "xai"])
async def test_selected_provider_drives_severity_prose(provider: str):
    from src.ai.gemini_client import GeminiClient

    manager = _ProviderManager(
        provider,
        '{"explanation":"Grounded explanation","business_impact":"Risk"}',
    )
    client = GeminiClient(enable_rag=False, provider_manager=manager)

    result = await client.classify_severity(
        rule_id="image-alt",
        impact="critical",
        html_snippet='<img src="example.png">',
        selector="img",
    )

    assert result["success"] is True
    assert result["provider"] == provider
    assert result["model"] == f"{provider}-text-test"
    assert result["explanation"] == "Grounded explanation"


@pytest.mark.asyncio
async def test_code_fix_uses_configured_provider_manager():
    from src.ai.gemini_client import GeminiClient

    manager = _ProviderManager(
        "anthropic",
        "FIXED CODE:\n```html\n<button>Save</button>\n```\nEXPLANATION:\nAdded a name.",
    )
    client = GeminiClient(enable_rag=False, provider_manager=manager)

    result = await client.generate_code_fix(
        html_snippet="<button></button>",
        rule_id="button-name",
        issue_description="Button has no accessible name",
    )

    assert result["provider"] == "anthropic"
    assert result["model"] == "anthropic-code-test"
    assert result["fixed_code"] == "<button>Save</button>"
    assert len(manager.code_calls) == 1


@pytest.mark.asyncio
async def test_exact_rule_grounding_does_not_require_embeddings():
    from src.ai.gemini_client import GeminiClient

    manager = _ProviderManager(
        "openai",
        '{"explanation":"WCAG 1.1.1 requires text alternatives.",'
        '"business_impact":"People may miss the content."}',
    )
    client = GeminiClient(enable_rag=False, provider_manager=manager)
    knowledge_base = _ExactKnowledgeBase()
    client.enable_rag = True
    client.kb = knowledge_base
    client._kb_initialized = True

    result = await client.classify_severity_with_rag(
        rule_id="image-alt",
        impact="critical",
        html_snippet='<img src="example.png">',
        selector="img",
    )

    assert result["success"] is True
    assert result["provider"] == "openai"
    assert result["rag_enabled"] is True
    assert result["rag_guidelines"][0]["rule_id"] == "image-alt"
    assert knowledge_base.rule_ids == ["image-alt"]
    assert knowledge_base.search_called is False
    assert "Canonical rule: image-alt" in manager.text_calls[0]


@pytest.mark.asyncio
async def test_provider_failure_preserves_deterministic_severity():
    from src.ai.gemini_client import GeminiClient

    class _FailingManager(_ProviderManager):
        async def generate_text(self, prompt: str, **_kwargs) -> LLMResponse:
            self.text_calls.append(prompt)
            return LLMResponse.error_response(
                error="No providers available", provider="none", model=""
            )

    client = GeminiClient(
        enable_rag=False,
        provider_manager=_FailingManager("none", ""),
    )

    result = await client.classify_severity(
        rule_id="image-alt",
        impact="critical",
        html_snippet="<img>",
        selector="img",
    )

    assert result["success"] is False
    assert result["severity"] == "Critical"
    assert result["severity_source"]
    assert result["provider"] == "none"
    assert result["explanation"] == ""


class _AccessibilityAdapter:
    def bind_provider_manager(self, _provider_manager):
        return self

    async def classify_severity_with_rag(self, **_kwargs):
        return {
            "success": True,
            "severity": "High",
            "severity_source": "deterministic-test",
            "explanation": "Grounded explanation",
            "business_impact": "Risk",
            "provider": "anthropic",
            "model": "claude-test",
            "inference_time": 0.03,
            "rag_enabled": True,
            "rag_guidelines": [{"rule_id": "button-name"}],
        }

    async def generate_code_fix(self, **_kwargs):
        return {
            "success": True,
            "fixed_code": "<button>Save</button>",
            "explanation": "Added an accessible name",
            "provider": "anthropic",
            "model": "claude-code-test",
            "inference_time": 0.04,
        }


@pytest.mark.asyncio
async def test_analyze_route_preserves_actual_provider_attribution(monkeypatch):
    import src.api.main as api_main

    monkeypatch.setattr(api_main, "accessibility_ai_client", _AccessibilityAdapter())
    request = api_main.ViolationAnalysisRequest(
        rule_id="button-name",
        impact="serious",
        html_snippet="<button></button>",
        selector="button",
        generate_fix=True,
    )

    result = await api_main.analyze_violation(request, api_key_info=(None, "", ""))

    assert result["provider"] == "anthropic"
    assert result["severity_source"] == "deterministic-test"
    assert result["fix"]["provider"] == "anthropic"
    assert result["fix"]["model"] == "claude-code-test"


@pytest.mark.asyncio
async def test_batch_route_preserves_actual_provider_attribution(monkeypatch):
    import src.api.main as api_main

    monkeypatch.setattr(api_main, "accessibility_ai_client", _AccessibilityAdapter())
    request = api_main.BatchAnalysisRequest(
        violations=[
            api_main.BatchAnalysisViolation(
                id="violation-1",
                rule_id="button-name",
                impact="serious",
                html_snippet="<button></button>",
                selector="button",
            )
        ],
        generate_fixes=True,
    )

    result = await api_main.batch_analyze_violations(
        request, api_key_info=(None, "", "")
    )

    row = result["results"][0]
    assert row["classification"]["provider"] == "anthropic"
    assert row["fix"]["provider"] == "anthropic"
