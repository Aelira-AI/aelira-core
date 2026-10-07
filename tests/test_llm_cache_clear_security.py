"""Provider-scoped cache clearing uses canonical names and actual cache keys."""

from fnmatch import fnmatchcase
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ai.cache import CACHE_PREFIX, LLMCache
from src.ai.workspace_provider_config import SUPPORTED_WORKSPACE_PROVIDERS
from src.api import llm_providers
from src.auth.dependencies import get_required_api_key
from src.db.database import get_db_dependency
from src.db.models import UserRole


class _Redis:
    def __init__(self):
        self.values = {}
        self.patterns = []

    def setex(self, key, _ttl, value):
        self.values[key] = value

    def scan_iter(self, *, match):
        self.patterns.append(match)
        yield from (key for key in list(self.values) if fnmatchcase(key, match))

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key)
        return len(keys)


class _DB:
    def __init__(self, role):
        self.role = role

    def query(self, _model):
        return self

    def filter(self, *_criteria):
        return self

    def first(self):
        return SimpleNamespace(role=self.role)


@pytest.fixture()
def cache_route(monkeypatch):
    redis = _Redis()
    cache = LLMCache(redis_client=redis, enabled=True)
    db = _DB(UserRole.ADMIN)
    app = FastAPI()
    app.include_router(llm_providers.router)
    app.dependency_overrides[get_required_api_key] = lambda: (
        None,
        "synthetic-admin",
        "synthetic-department",
    )
    app.dependency_overrides[get_db_dependency] = lambda: db
    monkeypatch.setattr(llm_providers, "get_llm_cache", lambda: cache)
    with TestClient(app) as client:
        yield client, cache, redis, db


@pytest.mark.parametrize("selected", SUPPORTED_WORKSPACE_PROVIDERS)
def test_provider_clear_deletes_only_matching_actual_keys(cache_route, selected):
    client, cache, redis, _db = cache_route
    for name in SUPPORTED_WORKSPACE_PROVIDERS:
        assert cache.set(f"text-{name}", "response", provider=name)
        assert cache.set(
            f"vision-{name}", "response", provider=name, image_hash="pixels"
        )
    assert cache.set("no-provider", "response")
    requested_keys = {
        cache._make_key(f"text-{selected}", provider=selected),
        cache._make_key(f"vision-{selected}", provider=selected, image_hash="pixels"),
    }
    original_keys = set(redis.values)

    response = client.delete("/llm/cache", params={"provider": selected})

    assert response.status_code == 200
    assert response.json()["entries_deleted"] == 2
    assert response.json()["message"] == (
        f"Cleared 2 cache entries for provider: {selected}"
    )
    assert redis.patterns == [f"{CACHE_PREFIX}:{selected}:*"]
    assert set(redis.values) == original_keys - requested_keys
    assert all(key.startswith(f"{CACHE_PREFIX}:{selected}:") for key in requested_keys)


def test_omitted_provider_preserves_global_clear(cache_route):
    client, cache, redis, db = cache_route
    db.role = UserRole.SUPER_ADMIN
    assert cache.set("one", "response", provider="gemini")
    assert cache.set("two", "response", provider="ollama")
    assert cache.set("three", "response")

    response = client.delete("/llm/cache")

    assert response.status_code == 200
    assert response.json()["entries_deleted"] == 3
    assert response.json()["message"] == "Cleared 3 cache entries"
    assert redis.patterns == [f"{CACHE_PREFIX}*"]
    assert redis.values == {}


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "*",
        "gemini:*",
        "gemini?",
        "GEMINI",
        "gemini\nsecret",
        "secret-" + "x" * 1024,
    ],
)
def test_invalid_provider_never_reaches_cache_or_logs(cache_route, caplog, invalid):
    client, cache, redis, _db = cache_route
    assert cache.set("keep", "response", provider="gemini")
    original = redis.values.copy()

    response = client.delete("/llm/cache", params={"provider": invalid})

    assert response.status_code == 400
    assert response.json() == {"detail": "Unsupported cache provider"}
    assert invalid not in response.text or invalid == ""
    assert redis.patterns == []
    assert redis.values == original
    provider_logs = [
        record.getMessage()
        for record in caplog.records
        if record.name == llm_providers.__name__
    ]
    assert not provider_logs


def test_non_admin_remains_forbidden_before_provider_clear(cache_route):
    client, cache, redis, db = cache_route
    db.role = UserRole.FACULTY
    assert cache.set("keep", "response", provider="gemini")

    response = client.delete("/llm/cache", params={"provider": "*"})

    assert response.status_code == 403
    assert redis.patterns == []
    assert len(redis.values) == 1
