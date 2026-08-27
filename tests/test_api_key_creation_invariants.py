"""Regression tests for explicit-only user API-key creation."""

import ast
import inspect
import textwrap
import threading
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api import auth_routes
from src.auth.auth_service import AuthService
from src.auth.session_service import SessionService
from src.db.models import APIKey, Department, User, UserRole
from src.services.account_deletion_service import AccountDeletionService

NO_ACTIVE_KEY_DETAIL = (
    "No active API key. Create one in Settings for programmatic access."
)


def test_session_access_identity_is_frozen_stable_bounded_and_not_an_api_key():
    first = auth_routes.SessionAccessIdentity.from_validated_session(
        user_id="session-user",
        department_id="session-dept",
        payload={"jti": "trusted-session-jti", "sub": "session-user"},
    )
    second = auth_routes.SessionAccessIdentity.from_validated_session(
        user_id="session-user",
        department_id="session-dept",
        payload={"jti": "trusted-session-jti", "sub": "session-user"},
    )

    assert first == second
    assert first.auth_method == "session"
    assert first.rate_limit_per_hour > 0
    assert len(first.id) <= 36
    assert not isinstance(first, APIKey)
    with pytest.raises(FrozenInstanceError):
        first.user_id = "other-user"


def test_magic_link_new_user_does_not_create_or_persist_an_api_key(monkeypatch):
    service = SessionService.__new__(SessionService)
    service.settings = MagicMock(open_signup=False)
    service._notify_admins_new_signup = MagicMock()
    service._send_welcome_email = MagicMock()

    user_query = MagicMock()
    user_query.filter.return_value = user_query
    user_query.first.return_value = None
    user_query.count.return_value = 0
    db = MagicMock()
    db.query.return_value = user_query
    added = []
    db.add.side_effect = added.append

    def assign_generated_ids():
        for row in added:
            if getattr(row, "id", None) is None:
                row.id = f"generated-{type(row).__name__.lower()}"

    db.flush.side_effect = assign_generated_ids
    monkeypatch.setattr(
        AccountDeletionService,
        "is_email_blocked",
        MagicMock(return_value=(False, None)),
    )
    create_key = MagicMock()
    monkeypatch.setattr(AuthService, "create_api_key", create_key)

    user, is_new = service.get_or_create_user_for_magic_link(
        db, "new.user@example.edu", name="New User", institution="Example University"
    )

    assert is_new is True
    assert user.email == "new.user@example.edu"
    assert user.role == UserRole.ADMIN
    create_key.assert_not_called()
    assert not any(isinstance(row, APIKey) for row in added)
    assert sum(isinstance(row, User) for row in added) == 1
    assert sum(isinstance(row, Department) for row in added) == 1
    db.commit.assert_called_once()
    lock_statement = str(db.execute.call_args.args[0])
    assert "pg_advisory_xact_lock" in lock_statement


def test_legacy_session_dependency_without_active_key_returns_compat_identity_and_never_creates(
    monkeypatch,
):
    user = MagicMock(id="session-user", department_id="session-dept")
    session_service = MagicMock()
    session_service.validate_session.return_value = (user, {"sub": user.id})
    monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
    create_key = MagicMock()
    monkeypatch.setattr(AuthService, "create_api_key", create_key)

    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.first.return_value = None
    db = MagicMock()
    db.query.return_value = query
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/legacy",
            "headers": [(b"cookie", b"aelira_access=valid-session")],
        }
    )

    identity = auth_routes.get_current_api_key(request, credentials=None, db=db)

    assert isinstance(identity, auth_routes.SessionAccessIdentity)
    assert identity.user_id == "session-user"
    assert identity.department_id == "session-dept"
    assert len(identity.id) <= 36
    create_key.assert_not_called()


def test_session_with_old_tenant_active_key_uses_current_session_tenant_without_key_query(
    monkeypatch,
):
    user = MagicMock(id="session-user", department_id="current-dept")
    session_service = MagicMock()
    session_service.validate_session.return_value = (
        user,
        {"sub": user.id, "jti": "trusted-session-jti"},
    )
    monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
    existing = MagicMock(
        spec=APIKey,
        id="existing-key",
        user_id=user.id,
        department_id="old-dept",
        rate_limit_per_hour=1,
    )
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.first.return_value = existing
    db = MagicMock()
    db.query.return_value = query
    limiter = MagicMock(return_value=(False, {}))
    monkeypatch.setattr(auth_routes.RateLimiter, "check_rate_limit", limiter)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/legacy",
            "headers": [(b"cookie", b"aelira_access=valid-session")],
        }
    )

    identity = auth_routes.get_current_api_key(request, credentials=None, db=db)

    assert isinstance(identity, auth_routes.SessionAccessIdentity)
    assert identity.user_id == "session-user"
    assert identity.department_id == "current-dept"
    db.query.assert_not_called()
    limiter.assert_not_called()


def test_api_key_only_validate_rejects_session_identity_cleanly():
    identity = auth_routes.SessionAccessIdentity.from_validated_session(
        user_id="session-user",
        department_id="session-dept",
        payload={"jti": "trusted-session-jti"},
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_routes.validate_api_key(identity)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "A real API key is required for this endpoint"


def test_create_throttle_rejects_before_key_generation(monkeypatch):
    principal = MagicMock(
        user_id="session-user",
        department_id="session-dept",
        auth_method="session",
    )
    limiter = MagicMock(return_value=(False, {"Retry-After": "60"}))
    generate = MagicMock()
    monkeypatch.setattr(auth_routes.RateLimiter, "check_rate_limit", limiter)
    monkeypatch.setattr(AuthService, "generate_api_key", generate)

    with pytest.raises(HTTPException) as exc_info:
        auth_routes.create_api_key(
            auth_routes.CreateAPIKeyRequest(name="Automation"), principal, MagicMock()
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}
    limiter.assert_called_once_with(
        "api-key-create:session-user", 5, require_distributed=False
    )
    generate.assert_not_called()


def test_create_quota_error_maps_to_stable_conflict(monkeypatch):
    principal = MagicMock(user_id="user-1", department_id="dept-1")
    monkeypatch.setattr(
        auth_routes.RateLimiter,
        "check_rate_limit",
        MagicMock(return_value=(True, {})),
    )
    monkeypatch.setattr(
        AuthService,
        "create_api_key",
        MagicMock(
            side_effect=auth_routes.APIKeyQuotaError(
                "Active API key limit reached (10)"
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_routes.create_api_key(
            auth_routes.CreateAPIKeyRequest(name="Blocked"), principal, MagicMock()
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Active API key limit reached (10)"


def test_create_rejects_inactive_or_cross_tenant_owner_before_bcrypt(monkeypatch):
    owner = MagicMock(
        spec=User,
        id="user-1",
        department_id="other-dept",
        is_active=True,
    )
    user_query = MagicMock()
    user_query.filter.return_value = user_query
    user_query.with_for_update.return_value = user_query
    user_query.first.return_value = owner
    db = MagicMock()
    db.query.return_value = user_query
    generate = MagicMock()
    monkeypatch.setattr(AuthService, "generate_api_key", generate)

    with pytest.raises(auth_routes.APIKeyQuotaError):
        AuthService.create_api_key(db, "user-1", "dept-1", name="Blocked")

    generate.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize(
    ("active_count", "total_count", "detail"),
    [
        (10, 10, "Active API key limit reached (10)"),
        (0, 100, "API key lifetime limit reached (100)"),
    ],
)
def test_api_key_quota_rejects_under_user_lock_before_bcrypt(
    monkeypatch, active_count, total_count, detail
):
    owner = MagicMock(
        spec=User,
        id="user-1",
        department_id="dept-1",
        is_active=True,
    )
    user_query = MagicMock()
    user_query.filter.return_value = user_query
    user_query.with_for_update.return_value = user_query
    user_query.first.return_value = owner
    key_query = MagicMock()
    key_query.filter.return_value = key_query
    key_query.count.side_effect = [active_count, total_count]
    db = MagicMock()
    db.query.side_effect = [user_query, key_query, key_query]
    generate = MagicMock()
    monkeypatch.setattr(AuthService, "generate_api_key", generate)

    with pytest.raises(auth_routes.APIKeyQuotaError) as exc_info:
        AuthService.create_api_key(db, "user-1", "dept-1", name="Blocked")

    assert str(exc_info.value) == detail
    user_query.with_for_update.assert_called_once_with()
    generate.assert_not_called()
    db.add.assert_not_called()


def test_concurrent_creation_serializes_quota_and_creates_only_one(monkeypatch):
    state = {"active": 9, "total": 9, "created": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    class FakeQuery:
        def __init__(self, db, model):
            self.db = db
            self.model = model
            self.active_only = False

        def filter(self, *criteria):
            if self.model is APIKey and len(criteria) > 1:
                self.active_only = True
            return self

        def with_for_update(self):
            self.db.lock_requested = True
            return self

        def first(self):
            assert self.lock_requested_or_user
            lock.acquire()
            self.db.holds_lock = True
            return MagicMock(
                spec=User,
                id="user-1",
                department_id="dept-1",
                is_active=True,
            )

        @property
        def lock_requested_or_user(self):
            return self.model is User and self.db.lock_requested

        def count(self):
            return state["active" if self.active_only else "total"]

    class FakeDB:
        def __init__(self):
            self.lock_requested = False
            self.holds_lock = False

        def query(self, model):
            return FakeQuery(self, model)

        def add(self, row):
            assert self.holds_lock
            state["active"] += 1
            state["total"] += 1
            state["created"] += 1
            row.id = f"key-{state['created']}"

        def commit(self):
            if self.holds_lock:
                self.holds_lock = False
                lock.release()

        def rollback(self):
            if self.holds_lock:
                self.holds_lock = False
                lock.release()

        def refresh(self, row):
            return None

    monkeypatch.setattr(
        AuthService,
        "generate_api_key",
        MagicMock(return_value=("full", "hash", "prefix")),
    )
    outcomes = []

    def create():
        db = FakeDB()
        barrier.wait()
        try:
            AuthService.create_api_key(db, "user-1", "dept-1", name="Concurrent")
            outcomes.append("created")
        except auth_routes.APIKeyQuotaError:
            db.rollback()
            outcomes.append("rejected")

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(outcomes) == ["created", "rejected"]
    assert state["created"] == 1


@pytest.mark.parametrize(
    ("callable_under_test", "forbidden_owner"),
    [
        (SessionService.get_or_create_user_for_magic_link, "SessionService"),
        (auth_routes.get_current_api_key, "get_current_api_key"),
    ],
)
def test_session_paths_have_no_static_create_api_key_call(
    callable_under_test, forbidden_owner
):
    tree = ast.parse(textwrap.dedent(inspect.getsource(callable_under_test)))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_api_key"
    ]
    assert calls == [], f"{forbidden_owner} must not create API keys implicitly"
