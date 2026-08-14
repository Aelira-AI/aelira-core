"""
Tests for the /ready Kubernetes readiness probe (#5).

Uses FastAPI's TestClient with the database and Redis dependencies
overridden via app.dependency_overrides — no real Postgres or Redis
connection is made.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, get_db_dependency, get_redis_client


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Ensure each test starts from a clean dependency_overrides state."""
    app.dependency_overrides.pop(get_db_dependency, None)
    app.dependency_overrides.pop(get_redis_client, None)
    yield
    app.dependency_overrides.pop(get_db_dependency, None)
    app.dependency_overrides.pop(get_redis_client, None)


@pytest.fixture(autouse=True)
def _redis_enabled(monkeypatch):
    """Pin redis_enabled=True so these tests exercise the enabled path.

    conftest.py sets REDIS_ENABLED=false for suite speed, and /ready
    deliberately reports "disabled" (not a failure) in that case — the
    disabled path has its own test below.
    """
    from src.api.main import settings

    monkeypatch.setattr(settings, "redis_enabled", True)


def _healthy_db():
    """A DB session mock whose SELECT 1 succeeds."""
    db = MagicMock()
    db.execute.return_value = None
    return db


def _healthy_redis():
    """A Redis client mock whose PING succeeds."""
    redis_client = MagicMock()
    redis_client.ping.return_value = True
    return redis_client


class TestReadinessHappyPath:
    def test_ready_when_db_and_redis_healthy(self, client):
        app.dependency_overrides[get_db_dependency] = lambda: _healthy_db()
        app.dependency_overrides[get_redis_client] = lambda: _healthy_redis()

        response = client.get("/ready")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "checks": {"database": "ok", "redis": "ok"},
        }


class TestReadinessDatabaseDown:
    def test_returns_503_when_database_query_raises(self, client):
        db = MagicMock()
        db.execute.side_effect = Exception("connection refused")
        app.dependency_overrides[get_db_dependency] = lambda: db
        app.dependency_overrides[get_redis_client] = lambda: _healthy_redis()

        response = client.get("/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["database"] == "failed"
        assert body["checks"]["redis"] == "ok"


class TestReadinessRedisDown:
    def test_returns_503_when_redis_client_is_none(self, client):
        app.dependency_overrides[get_db_dependency] = lambda: _healthy_db()
        app.dependency_overrides[get_redis_client] = lambda: None

        response = client.get("/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["database"] == "ok"
        assert body["checks"]["redis"] == "failed"

    def test_returns_503_when_redis_ping_raises(self, client):
        redis_client = MagicMock()
        redis_client.ping.side_effect = ConnectionError("redis unreachable")
        app.dependency_overrides[get_db_dependency] = lambda: _healthy_db()
        app.dependency_overrides[get_redis_client] = lambda: redis_client

        response = client.get("/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["redis"] == "failed"

    def test_returns_503_when_redis_ping_returns_false(self, client):
        redis_client = MagicMock()
        redis_client.ping.return_value = False
        app.dependency_overrides[get_db_dependency] = lambda: _healthy_db()
        app.dependency_overrides[get_redis_client] = lambda: redis_client

        response = client.get("/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["redis"] == "failed"


class TestReadinessBothDown:
    def test_returns_503_with_both_checks_failed(self, client):
        db = MagicMock()
        db.execute.side_effect = Exception("db down")
        app.dependency_overrides[get_db_dependency] = lambda: db
        app.dependency_overrides[get_redis_client] = lambda: None

        response = client.get("/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["database"] == "failed"
        assert body["checks"]["redis"] == "failed"


class TestReadinessRedisDisabled:
    def test_disabled_redis_reports_disabled_and_stays_ready(self, client, monkeypatch):
        """A deployment that deliberately disables Redis is still ready."""
        from src.api.main import settings

        monkeypatch.setattr(settings, "redis_enabled", False)
        app.dependency_overrides[get_db_dependency] = lambda: _healthy_db()
        app.dependency_overrides[get_redis_client] = lambda: None

        response = client.get("/ready")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "checks": {"database": "ok", "redis": "disabled"},
        }
