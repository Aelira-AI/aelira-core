"""Atomic and fail-closed rate-limit regression tests."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import redis
from fastapi import HTTPException

from src.api import auth_routes
from src.auth import redis_rate_limiter
from src.auth.auth_service import AuthService
from src.auth.redis_rate_limiter import RateLimitStorageUnavailable, RedisRateLimiter


class AtomicFakeRedis:
    def __init__(self):
        self.counts = {}
        self.ttls = {}
        self.lock = threading.Lock()
        self.scripts = []
        self.expire_calls = 0

    def eval(self, script, number_of_keys, key, window_seconds):
        assert number_of_keys == 1
        assert str(key) not in script
        self.scripts.append(script)
        with self.lock:
            count = self.counts.get(key, 0) + 1
            self.counts[key] = count
            if count == 1:
                self.ttls[key] = int(window_seconds)
                self.expire_calls += 1
            return [count, self.ttls[key]]


def test_redis_check_is_one_fixed_atomic_script_and_does_not_overwrite_first_count(
    monkeypatch,
):
    fake = AtomicFakeRedis()
    monkeypatch.setattr(redis_rate_limiter, "get_redis_client", lambda: fake)

    first_allowed, first_headers = RedisRateLimiter.check_rate_limit("key-1", 2)
    second_allowed, second_headers = RedisRateLimiter.check_rate_limit("key-1", 2)
    third_allowed, third_headers = RedisRateLimiter.check_rate_limit("key-1", 2)

    assert [first_allowed, second_allowed, third_allowed] == [True, True, False]
    assert [
        first_headers["X-RateLimit-Remaining"],
        second_headers["X-RateLimit-Remaining"],
        third_headers["X-RateLimit-Remaining"],
    ] == ["1", "0", "0"]
    assert next(iter(fake.counts.values())) == 3
    assert fake.expire_calls == 1
    assert len(set(fake.scripts)) == 1
    script = fake.scripts[0]
    assert script.count("INCR") == 1
    assert script.count("EXPIRE") == 1
    assert script.count("TTL") == 1
    assert "GET" not in script
    assert "SET" not in script


def test_concurrent_redis_requests_allow_exactly_limit(monkeypatch):
    fake = AtomicFakeRedis()
    monkeypatch.setattr(redis_rate_limiter, "get_redis_client", lambda: fake)

    with ThreadPoolExecutor(max_workers=32) as executor:
        outcomes = list(
            executor.map(
                lambda _: RedisRateLimiter.check_rate_limit("concurrent", 25)[0],
                range(100),
            )
        )

    assert sum(outcomes) == 25
    assert fake.expire_calls == 1


def test_concurrent_memory_fallback_allows_exactly_limit(monkeypatch):
    monkeypatch.setattr(redis_rate_limiter, "get_redis_client", lambda: None)
    RedisRateLimiter.reset_rate_limit("memory-concurrent")

    with ThreadPoolExecutor(max_workers=32) as executor:
        outcomes = list(
            executor.map(
                lambda _: RedisRateLimiter.check_rate_limit("memory-concurrent", 25)[0],
                range(100),
            )
        )

    assert sum(outcomes) == 25


def test_memory_reset_uses_the_same_class_level_lock(monkeypatch):
    monkeypatch.setattr(redis_rate_limiter, "get_redis_client", lambda: None)
    started = threading.Event()
    finished = threading.Event()

    def reset():
        started.set()
        RedisRateLimiter.reset_rate_limit("locked-reset")
        finished.set()

    RedisRateLimiter._memory_lock.acquire()
    try:
        thread = threading.Thread(target=reset)
        thread.start()
        assert started.wait(timeout=1)
        assert not finished.wait(timeout=0.05)
    finally:
        RedisRateLimiter._memory_lock.release()

    thread.join(timeout=1)
    assert finished.is_set()


@pytest.mark.parametrize(
    "client",
    [
        None,
        MagicMock(eval=MagicMock(side_effect=redis.RedisError("redis://:secret@host"))),
    ],
)
def test_required_distributed_storage_fails_closed_without_secret(
    monkeypatch, caplog, client
):
    monkeypatch.setattr(redis_rate_limiter, "get_redis_client", lambda: client)

    with pytest.raises(RateLimitStorageUnavailable) as exc_info:
        RedisRateLimiter.check_rate_limit("sensitive", 5, require_distributed=True)

    assert str(exc_info.value) == "Distributed rate limit storage is unavailable"
    assert "secret" not in caplog.text


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_production_like_key_creation_redis_outage_returns_503_before_bcrypt(
    monkeypatch, environment
):
    monkeypatch.setattr(
        auth_routes, "get_settings", lambda: SimpleNamespace(env=environment)
    )
    monkeypatch.setattr(redis_rate_limiter, "get_redis_client", lambda: None)
    create = MagicMock()
    generate = MagicMock()
    monkeypatch.setattr(AuthService, "create_api_key", create)
    monkeypatch.setattr(AuthService, "generate_api_key", generate)
    principal = MagicMock(user_id="user-1", department_id="dept-1")

    with pytest.raises(HTTPException) as exc_info:
        auth_routes.create_api_key(
            auth_routes.CreateAPIKeyRequest(name="Blocked"), principal, MagicMock()
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "API key creation rate limit is unavailable"
    create.assert_not_called()
    generate.assert_not_called()


def test_development_key_creation_requests_locked_fallback(monkeypatch):
    monkeypatch.setattr(
        auth_routes, "get_settings", lambda: SimpleNamespace(env="development")
    )
    limiter = MagicMock(return_value=(False, {}))
    monkeypatch.setattr(auth_routes.RateLimiter, "check_rate_limit", limiter)
    principal = MagicMock(user_id="user-1", department_id="dept-1")

    with pytest.raises(HTTPException) as exc_info:
        auth_routes.create_api_key(
            auth_routes.CreateAPIKeyRequest(name="Limited"), principal, MagicMock()
        )

    assert exc_info.value.status_code == 429
    limiter.assert_called_once_with(
        "api-key-create:user-1", 5, require_distributed=False
    )
