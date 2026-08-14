"""Gemini alt-text generation must not silently return truncated text.

gemini-2.5-flash is a thinking model: its internal reasoning tokens count
against maxOutputTokens. At the production default (max_tokens=300) the model
spends ~286 tokens thinking and returns ~10 tokens of visible text with
finishReason=MAX_TOKENS — a mid-sentence fragment the demo page then displays
as the generated alt text (reproduced live 2026-08-12).

Two guarantees under test:
1. The request disables thinking (thinkingBudget: 0) so the whole token budget
   goes to visible output.
2. A MAX_TOKENS finish is treated as an error (routing to the Ollama fallback /
   human review), never returned as successful alt text.
"""

import asyncio
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.education.image_alt_text import ImageAltTextGenerator


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; records the request payload."""

    captured: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, params=None, json=None, timeout=None, headers=None):
        _FakeAsyncClient.captured = json
        _FakeAsyncClient.captured_url = url
        _FakeAsyncClient.captured_params = params
        _FakeAsyncClient.captured_headers = headers or {}
        return _FakeResponse(_FakeAsyncClient.response_payload)


def _gemini_payload(text, finish_reason):
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "finishReason": finish_reason,
            }
        ],
        "usageMetadata": {"promptTokenCount": 330, "candidatesTokenCount": 10},
    }


@pytest.fixture()
def generator(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.education.image_alt_text.httpx.AsyncClient", _FakeAsyncClient
    )
    gen = ImageAltTextGenerator()
    gen.gemini_api_key = "test-key"
    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return gen, str(img)


@pytest.mark.unit
def test_request_disables_thinking(generator):
    gen, img = generator
    _FakeAsyncClient.response_payload = _gemini_payload("A photo of a dog.", "STOP")
    asyncio.run(gen._generate_with_gemini(img, "describe", max_tokens=300))
    cfg = _FakeAsyncClient.captured.get("generationConfig", {})
    assert cfg.get("thinkingConfig", {}).get("thinkingBudget") == 0, (
        "generationConfig must set thinkingConfig.thinkingBudget=0 — otherwise "
        "gemini-2.5-flash spends maxOutputTokens on hidden reasoning and the "
        "visible alt text is silently truncated"
    )


@pytest.mark.unit
def test_api_key_travels_in_a_header_not_the_url(generator):
    """httpx logs the request URL at INFO, so a `?key=` would leak the key.

    Complements the AST guard in test_gemini_key_not_in_url.py by checking the
    request as actually issued.
    """
    gen, img = generator
    _FakeAsyncClient.response_payload = _gemini_payload("A photo of a dog.", "STOP")
    asyncio.run(gen._generate_with_gemini(img, "describe", max_tokens=300))

    assert _FakeAsyncClient.captured_headers.get("x-goog-api-key") == "test-key"
    assert "test-key" not in _FakeAsyncClient.captured_url
    assert "key" not in (_FakeAsyncClient.captured_params or {})


@pytest.mark.unit
def test_max_tokens_finish_is_an_error(generator):
    gen, img = generator
    _FakeAsyncClient.response_payload = _gemini_payload(
        'This bar chart titled "Treatment outcomes by group,', "MAX_TOKENS"
    )
    text, _ = asyncio.run(gen._generate_with_gemini(img, "describe", max_tokens=300))
    assert text.startswith("ERROR:"), (
        "a MAX_TOKENS finish means the text is an incomplete fragment; it must "
        "surface as an error (fallback/human review), not as generated alt text"
    )


@pytest.mark.unit
def test_stop_finish_returns_text(generator):
    gen, img = generator
    _FakeAsyncClient.response_payload = _gemini_payload(
        "A bar chart comparing treatment outcomes across four groups.", "STOP"
    )
    text, _ = asyncio.run(gen._generate_with_gemini(img, "describe", max_tokens=300))
    assert text == "A bar chart comparing treatment outcomes across four groups."
