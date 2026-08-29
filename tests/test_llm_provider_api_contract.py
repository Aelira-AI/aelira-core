"""Wire-contract coverage for durable workspace provider settings."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.api import llm_providers
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import Department, DepartmentAIProviderConfig, UserRole


class _Query:
    def __init__(self, model, department, rows):
        self.model = model
        self.department = department
        self.rows = rows

    def filter(self, *_criteria):
        return self

    def first(self):
        return self.department if self.model is Department else self.rows[0]

    def all(self):
        return self.rows if self.model is DepartmentAIProviderConfig else [self.department]


class _DB:
    def __init__(self, department, rows):
        self.department = department
        self.rows = rows

    def query(self, model):
        return _Query(model, self.department, self.rows)


def test_provider_list_preserves_keyed_wire_contract(monkeypatch):
    monkeypatch.setattr(
        llm_providers,
        "get_provider_manager",
        lambda: (_ for _ in ()).throw(
            AssertionError("workspace GET must not use the global manager")
        ),
    )
    department = SimpleNamespace(
        id="department",
        ai_provider_config_revision=7,
        ai_primary_provider="anthropic",
        ai_fallback_provider="ollama",
    )
    rows = [
        SimpleNamespace(
            department_id="department",
            provider="anthropic",
            api_key_encrypted="opaque",
            text_model="claude-text",
            code_model="claude-code",
            vision_model="claude-vision",
        ),
        SimpleNamespace(
            department_id="department",
            provider="ollama",
            api_key_encrypted=None,
            text_model="ollama-text",
            code_model="ollama-code",
            vision_model="ollama-vision",
        ),
    ]
    principal = AuthenticatedPrincipal(
        api_key=MagicMock(),
        user_id="contract-test",
        department_id="department",
        user_role=UserRole.ADMIN,
        auth_method="api_key",
    )

    payload = llm_providers.list_providers(
        principal, _DB(department, rows)
    ).model_dump()

    assert set(payload) == {
        "schema_version",
        "config_revision",
        "primary",
        "fallback",
        "providers",
    }
    assert payload["schema_version"] == 1
    assert payload["config_revision"] == 7
    assert payload["primary"] == "anthropic"
    assert payload["fallback"] == "ollama"
    assert set(payload["providers"]) == {
        "ollama",
        "gemini",
        "openai",
        "anthropic",
        "xai",
    }
    assert payload["providers"]["ollama"] == {
        "name": "ollama",
        "display_name": "Ollama",
        "configured": True,
        "is_available": True,
        "is_local": True,
        "status": "configured",
        "text_model": "ollama-text",
        "code_model": "ollama-code",
        "vision_model": "ollama-vision",
    }
