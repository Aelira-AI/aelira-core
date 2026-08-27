"""Public configuration contracts for provider-neutral open-core installs."""

from pathlib import Path

import yaml

from src.config.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_KEYS = {
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "OLLAMA_HOST",
}


def _api_environment(compose_name: str) -> dict:
    compose = yaml.safe_load((REPO_ROOT / compose_name).read_text())
    return compose["services"]["api"]["environment"]


def test_no_implicit_provider_defaults():
    assert Settings.model_fields["llm_provider"].default == "none"
    assert Settings.model_fields["llm_fallback_provider"].default == "none"
    assert Settings.model_fields["embedding_provider"].default == "none"

    example = (REPO_ROOT / ".env.example").read_text()
    assert "LLM_PROVIDER=none" in example
    assert "LLM_FALLBACK_PROVIDER=none" in example
    assert "EMBEDDING_PROVIDER=none" in example
    assert "GEMINI_API_KEY=your-" not in example


def test_quickstart_exposes_every_provider_without_selecting_one():
    environment = _api_environment("docker-compose.quickstart.yml")

    assert environment["LLM_PROVIDER"] == "${LLM_PROVIDER:-none}"
    assert environment["LLM_FALLBACK_PROVIDER"] == ("${LLM_FALLBACK_PROVIDER:-none}")
    assert environment["EMBEDDING_PROVIDER"] == "${EMBEDDING_PROVIDER:-none}"
    assert PROVIDER_KEYS <= environment.keys()


def test_development_compose_exposes_every_provider_without_selecting_one():
    environment = _api_environment("docker-compose.dev.yml")

    assert environment["LLM_PROVIDER"] == "${LLM_PROVIDER:-none}"
    assert environment["LLM_FALLBACK_PROVIDER"] == ("${LLM_FALLBACK_PROVIDER:-none}")
    assert environment["EMBEDDING_PROVIDER"] == "${EMBEDDING_PROVIDER:-none}"
    assert PROVIDER_KEYS <= environment.keys()


def test_production_compose_exposes_every_provider_without_selecting_one():
    environment = _api_environment("docker-compose.prod.yml")

    assert environment["LLM_PROVIDER"] == "${LLM_PROVIDER:-none}"
    assert environment["LLM_FALLBACK_PROVIDER"] == ("${LLM_FALLBACK_PROVIDER:-none}")
    assert environment["EMBEDDING_PROVIDER"] == "${EMBEDDING_PROVIDER:-none}"
    assert environment["OLLAMA_HOST"] == "${OLLAMA_HOST:-http://ollama:11434}"
    assert PROVIDER_KEYS <= environment.keys()
