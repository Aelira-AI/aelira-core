"""Atomic transaction invariants for API-key mutations and audit records."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api import auth_routes
from src.auth.auth_service import AuthService
from src.db.models import User
from src.security.audit_service import AuditService

CREATE_FAILURE_DETAIL = "API key creation failed"
REVOKE_FAILURE_DETAIL = "API key revocation failed"


def _principal(*, current_key_id=None):
    api_key = None if current_key_id is None else MagicMock(id=current_key_id)
    return MagicMock(
        user_id="user-1",
        department_id="dept-1",
        api_key=api_key,
    )


def _api_key(*, active=True):
    return SimpleNamespace(
        id="key-1",
        name="Automation",
        key_prefix="aelira_live_12345678",
        rate_limit_per_hour=100,
        created_at=datetime.now(timezone.utc),
        last_used_at=None,
        expires_at=None,
        is_active=active,
    )


def _allow_create(monkeypatch):
    monkeypatch.setattr(
        auth_routes.RateLimiter,
        "check_rate_limit",
        MagicMock(return_value=(True, {})),
    )


def test_auth_create_commit_false_flushes_without_committing(monkeypatch):
    owner = MagicMock(spec=User, id="user-1", department_id="dept-1", is_active=True)
    owner_query = MagicMock()
    owner_query.filter.return_value = owner_query
    owner_query.with_for_update.return_value = owner_query
    owner_query.first.return_value = owner
    key_query = MagicMock()
    key_query.filter.return_value = key_query
    key_query.count.side_effect = [0, 0]
    db = MagicMock()
    db.query.side_effect = [owner_query, key_query, key_query]

    def assign_id():
        db.add.call_args.args[0].id = "key-1"

    db.flush.side_effect = assign_id
    monkeypatch.setattr(
        AuthService,
        "generate_api_key",
        MagicMock(return_value=("full-secret", "hash", "aelira_live_12345678")),
    )

    api_key, full_key = AuthService.create_api_key(db, "user-1", "dept-1", commit=False)

    assert api_key.id == "key-1"
    assert full_key == "full-secret"
    db.flush.assert_called_once_with()
    db.commit.assert_not_called()
    db.refresh.assert_not_called()


def test_auth_create_default_still_commits(monkeypatch):
    owner = MagicMock(spec=User, id="user-1", department_id="dept-1", is_active=True)
    owner_query = MagicMock()
    owner_query.filter.return_value = owner_query
    owner_query.with_for_update.return_value = owner_query
    owner_query.first.return_value = owner
    key_query = MagicMock()
    key_query.filter.return_value = key_query
    key_query.count.side_effect = [0, 0]
    db = MagicMock()
    db.query.side_effect = [owner_query, key_query, key_query]
    monkeypatch.setattr(
        AuthService,
        "generate_api_key",
        MagicMock(return_value=("full-secret", "hash", "aelira_live_12345678")),
    )

    AuthService.create_api_key(db, "user-1", "dept-1")

    db.commit.assert_called_once_with()
    db.refresh.assert_called_once_with(db.add.call_args.args[0])


def test_auth_revoke_commit_false_flushes_without_committing():
    api_key = _api_key()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = api_key
    db = MagicMock()
    db.query.return_value = query

    assert (
        AuthService.revoke_api_key(
            db,
            "key-1",
            "user-1",
            "dept-1",
            commit=False,
        )
        is True
    )

    assert api_key.is_active is False
    db.flush.assert_called_once_with()
    db.commit.assert_not_called()


def test_auth_revoke_default_still_commits():
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = _api_key()
    db = MagicMock()
    db.query.return_value = query

    assert AuthService.revoke_api_key(db, "key-1", "user-1", "dept-1") is True

    db.commit.assert_called_once_with()


def test_auth_revoke_query_is_scoped_to_current_department():
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = None
    db = MagicMock()
    db.query.return_value = query

    assert AuthService.revoke_api_key(db, "old-key", "user-1", "current-dept") is False

    predicates = " ".join(str(call.args[0]) for call in query.filter.call_args_list)
    assert "api_keys.user_id" in predicates
    assert "api_keys.department_id" in predicates
    db.commit.assert_not_called()
    db.flush.assert_not_called()


def test_auth_list_query_is_scoped_to_current_department():
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.all.return_value = []
    db = MagicMock()
    db.query.return_value = query

    assert AuthService.list_api_keys(db, "user-1", "current-dept") == []

    predicates = " ".join(str(arg) for arg in query.filter.call_args.args)
    assert "api_keys.user_id" in predicates
    assert "api_keys.department_id" in predicates


def test_api_key_audit_commit_false_flushes_without_committing():
    db = MagicMock()
    audit = AuditService(db)

    created = audit.log_api_key_create(
        "user-1", "dept-1", "key-1", "Automation", commit=False
    )
    revoked = audit.log_api_key_revoke("user-1", "dept-1", "key-1", commit=False)

    assert created.resource_id == "key-1"
    assert revoked.resource_id == "key-1"
    assert db.add.call_count == 2
    assert db.flush.call_count == 2
    db.commit.assert_not_called()


def test_create_success_commits_key_and_audit_once(monkeypatch):
    _allow_create(monkeypatch)
    api_key = _api_key()
    create = MagicMock(return_value=(api_key, "full-secret"))
    audit = MagicMock()
    monkeypatch.setattr(AuthService, "create_api_key", create)
    monkeypatch.setattr(auth_routes, "get_audit_service", lambda db: audit)
    db = MagicMock()

    result = auth_routes.create_api_key(
        auth_routes.CreateAPIKeyRequest(name="Automation"), _principal(), db
    )

    assert result["full_key"] == "full-secret"
    create.assert_called_once_with(
        db=db,
        user_id="user-1",
        department_id="dept-1",
        name="Automation",
        rate_limit_per_hour=100,
        expires_days=None,
        commit=False,
    )
    audit.log_api_key_create.assert_called_once_with(
        user_id="user-1",
        department_id="dept-1",
        api_key_id="key-1",
        key_name="Automation",
        commit=False,
    )
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_list_keys_scopes_moved_user_to_current_department(monkeypatch):
    list_keys = MagicMock(return_value=[])
    monkeypatch.setattr(AuthService, "list_api_keys", list_keys)
    db = MagicMock()

    result = auth_routes.list_api_keys(_principal(), db)

    assert result == []
    list_keys.assert_called_once_with(db, "user-1", "dept-1")


@pytest.mark.parametrize(
    "failure_stage", ["create", "audit_add", "audit_flush", "commit"]
)
def test_create_failure_rolls_back_and_never_returns_full_key(
    monkeypatch, failure_stage
):
    _allow_create(monkeypatch)
    create = MagicMock(return_value=(_api_key(), "full-secret"))
    db = MagicMock()
    audit = AuditService(db)
    if failure_stage == "create":
        create.side_effect = RuntimeError("key add failed")
    elif failure_stage == "audit_add":
        db.add.side_effect = RuntimeError("audit add failed")
    elif failure_stage == "audit_flush":
        db.flush.side_effect = RuntimeError("audit flush failed")
    else:
        db.commit.side_effect = RuntimeError("commit failed")
    monkeypatch.setattr(AuthService, "create_api_key", create)
    monkeypatch.setattr(auth_routes, "get_audit_service", lambda session: audit)

    with pytest.raises(HTTPException) as caught:
        auth_routes.create_api_key(
            auth_routes.CreateAPIKeyRequest(name="Automation"), _principal(), db
        )

    assert caught.value.status_code == 500
    assert caught.value.detail == CREATE_FAILURE_DETAIL
    assert "full-secret" not in str(caught.value.detail)
    db.rollback.assert_called_once_with()


def test_revoke_success_commits_key_and_audit_once(monkeypatch):
    revoke = MagicMock(return_value=True)
    audit = MagicMock()
    monkeypatch.setattr(AuthService, "revoke_api_key", revoke)
    monkeypatch.setattr(auth_routes, "get_audit_service", lambda db: audit)
    db = MagicMock()

    result = auth_routes.revoke_api_key("key-1", _principal(current_key_id="key-1"), db)

    assert result["success"] is True
    assert result["revoked_current_key"] is True
    revoke.assert_called_once_with(
        db,
        "key-1",
        "user-1",
        "dept-1",
        commit=False,
    )
    audit.log_api_key_revoke.assert_called_once_with(
        user_id="user-1",
        department_id="dept-1",
        api_key_id="key-1",
        commit=False,
    )
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


@pytest.mark.parametrize("failure_stage", ["audit", "commit"])
def test_revoke_failure_rolls_back_so_key_remains_active(monkeypatch, failure_stage):
    key = _api_key()
    db = MagicMock()

    def revoke(_db, _key_id, _user_id, _department_id, *, commit):
        assert commit is False
        key.is_active = False
        return True

    def rollback():
        key.is_active = True

    db.rollback.side_effect = rollback
    audit = MagicMock()
    if failure_stage == "audit":
        audit.log_api_key_revoke.side_effect = RuntimeError("audit flush failed")
    else:
        db.commit.side_effect = RuntimeError("commit failed")
    monkeypatch.setattr(AuthService, "revoke_api_key", revoke)
    monkeypatch.setattr(auth_routes, "get_audit_service", lambda session: audit)

    with pytest.raises(HTTPException) as caught:
        auth_routes.revoke_api_key("key-1", _principal(), db)

    assert caught.value.status_code == 500
    assert caught.value.detail == REVOKE_FAILURE_DETAIL
    assert key.is_active is True
    db.rollback.assert_called_once_with()
