"""API keys are valid only while their owning user remains active and scoped."""

from unittest.mock import MagicMock

import bcrypt

from src.auth import redis_rate_limiter
from src.auth.auth_service import AuthService
from src.db.models import APIKey, AuthProvider, User

RAW_KEY = "aelira_live_0123456789abcdef0123456789abcdef0123456789abcdef"


def test_generated_api_key_prefixes_are_random_bearing_and_unique():
    first_key, _, first_prefix = AuthService.generate_api_key()
    second_key, _, second_prefix = AuthService.generate_api_key()

    assert len(first_prefix) == 20
    assert first_prefix.startswith("aelira_live_")
    assert first_prefix != "aelira_live_"
    assert first_key.startswith(first_prefix)
    assert second_key.startswith(second_prefix)
    assert first_prefix != second_prefix


def test_public_api_key_prefix_is_rejected_without_bcrypt(monkeypatch):
    monkeypatch.setattr(
        redis_rate_limiter, "get_redis_client", lambda: None, raising=False
    )
    checkpw = MagicMock(return_value=False)
    monkeypatch.setattr(bcrypt, "checkpw", checkpw)
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.all.return_value = [MagicMock(key_hash="hash") for _ in range(100)]
    db.query.return_value = query

    result = AuthService.validate_api_key(db, "aelira_live_")

    assert result is None
    db.query.assert_not_called()
    checkpw.assert_not_called()


def test_invalid_api_key_bcrypt_work_is_bounded_on_adversarial_results(monkeypatch):
    monkeypatch.setattr(
        redis_rate_limiter, "get_redis_client", lambda: None, raising=False
    )
    checkpw = MagicMock(return_value=False)
    monkeypatch.setattr(bcrypt, "checkpw", checkpw)
    candidates = [MagicMock(key_hash="hash") for _ in range(100)]
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.limit.return_value = query
    query.all.return_value = candidates
    db.query.return_value = query

    raw_key = "aelira_live_deadbeef00000000000000000000000000000000"
    result = AuthService.validate_api_key(db, raw_key)

    assert result is None
    prefix_filter = query.filter.call_args_list[0].args[0]
    assert prefix_filter.right.value == raw_key[:20]
    query.limit.assert_called_once_with(5)
    assert checkpw.call_count <= 5


def _key(*, department_id="dept-1"):
    key = MagicMock(spec=APIKey)
    key.id = "key-1"
    key.user_id = "user-1"
    key.department_id = department_id
    key.key_prefix = RAW_KEY[:20]
    key.key_hash = bcrypt.hashpw(RAW_KEY.encode(), bcrypt.gensalt()).decode()
    key.expires_at = None
    key.is_active = True
    return key


def _owner(*, is_active=True, department_id="dept-1"):
    owner = MagicMock(spec=User)
    owner.id = "user-1"
    owner.department_id = department_id
    owner.is_active = is_active
    owner.auth_provider = AuthProvider.GOOGLE
    return owner


def _db_for(*, key, owner, cached=False):
    db = MagicMock()
    key_query = MagicMock()
    key_query.filter.return_value = key_query
    key_query.limit.return_value = key_query
    if cached:
        key_query.first.return_value = key
    else:
        key_query.all.return_value = [key]
    owner_query = MagicMock()
    owner_query.filter.return_value = owner_query
    owner_query.first.return_value = owner
    db.query.side_effect = lambda model: key_query if model is APIKey else owner_query
    return db


def test_cached_api_key_fails_when_owning_user_is_inactive(monkeypatch):
    redis = MagicMock()
    redis.get.return_value = "key-1"
    monkeypatch.setattr(
        redis_rate_limiter, "get_redis_client", lambda: redis, raising=False
    )
    key = _key()

    result = AuthService.validate_api_key(
        _db_for(key=key, owner=_owner(is_active=False), cached=True), RAW_KEY
    )

    assert result is None
    redis.delete.assert_called_once()


def test_bcrypt_api_key_succeeds_for_active_tenant_consistent_owner(monkeypatch):
    monkeypatch.setattr(
        redis_rate_limiter, "get_redis_client", lambda: None, raising=False
    )
    key = _key()

    result = AuthService.validate_api_key(
        _db_for(key=key, owner=_owner(), cached=False), RAW_KEY
    )

    assert result is key


def test_bcrypt_api_key_fails_when_owner_tenant_differs(monkeypatch):
    monkeypatch.setattr(
        redis_rate_limiter, "get_redis_client", lambda: None, raising=False
    )
    key = _key(department_id="dept-1")

    result = AuthService.validate_api_key(
        _db_for(key=key, owner=_owner(department_id="dept-2"), cached=False), RAW_KEY
    )

    assert result is None
