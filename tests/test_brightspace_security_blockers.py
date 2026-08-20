"""Regression tests for Brightspace bearer, worker, and refresh blockers."""

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import CloudProvider, UserRole

PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]
PRIVATE_DNS = [(2, 1, 6, "", ("10.0.0.8", 443))]


def _credential(**overrides):
    values = {
        "id": "cred-1",
        "department_id": "dept-1",
        "provider": CloudProvider.BRIGHTSPACE.value,
        "provider_metadata": {
            "brightspace_instance_url": "https://brightspace.example"
        },
        "is_active": True,
        "access_token": "old-access",
        "refresh_token": "rotated-refresh",
        "token_expires_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "scopes": "core:*:*",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _principal(auth_method="session", role=UserRole.FACULTY, **overrides):
    values = {
        "api_key": MagicMock() if auth_method == "api_key" else None,
        "user_id": "user-1",
        "department_id": "dept-1",
        "user_role": role,
        "auth_method": auth_method,
    }
    values.update(overrides)
    return AuthenticatedPrincipal(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_method", ["api_key", "session"])
@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.SUPER_ADMIN])
async def test_account_managers_can_start_brightspace_connect(
    monkeypatch, auth_method, role
):
    from src.api.brightspace_routes import (
        BrightspaceConnectRequest,
        connect_brightspace,
    )

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    with (
        patch(
            "src.api.brightspace_routes.OAuthStateManager.create_state",
            return_value="state-1",
        ),
        patch(
            "src.api.brightspace_routes.get_brightspace_authorization_url",
            return_value="https://auth.brightspace.com/oauth2/auth",
        ),
    ):
        result = await connect_brightspace(
            BrightspaceConnectRequest(
                brightspace_instance_url="https://brightspace.example"
            ),
            db=MagicMock(),
            principal=_principal(auth_method, role),
        )
    assert result["state"] == "state-1"


@pytest.mark.asyncio
async def test_account_wide_lti_administrator_can_start_brightspace_connect(
    monkeypatch,
):
    from src.api.brightspace_routes import (
        BrightspaceConnectRequest,
        connect_brightspace,
    )

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    principal = _principal(
        "lti",
        role=UserRole.ADMIN,
        lti_staff_role="Administrator",
        lti_account_wide=True,
    )
    with (
        patch(
            "src.api.brightspace_routes.OAuthStateManager.create_state",
            return_value="state-1",
        ),
        patch(
            "src.api.brightspace_routes.get_brightspace_authorization_url",
            return_value="https://auth.brightspace.com/oauth2/auth",
        ),
    ):
        result = await connect_brightspace(
            BrightspaceConnectRequest(
                brightspace_instance_url="https://brightspace.example"
            ),
            db=MagicMock(),
            principal=principal,
        )
    assert result["state"] == "state-1"


@pytest.mark.asyncio
async def test_brightspace_connect_rejects_cross_department_before_state():
    from src.api.brightspace_routes import (
        BrightspaceConnectRequest,
        connect_brightspace,
    )

    db = MagicMock()
    with (
        patch("src.api.brightspace_routes.OAuthStateManager.create_state") as state,
        patch("src.api.brightspace_routes.get_brightspace_authorization_url") as oauth,
        pytest.raises(HTTPException) as caught,
    ):
        await connect_brightspace(
            BrightspaceConnectRequest(
                brightspace_instance_url="https://brightspace.example",
                department_id="other-dept",
            ),
            db=db,
            principal=_principal("session", UserRole.ADMIN),
        )
    assert caught.value.status_code == 403
    state.assert_not_called()
    oauth.assert_not_called()
    db.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "principal",
    [
        _principal("api_key"),
        _principal("session"),
        _principal(
            "lti",
            lti_course_id="course-1",
            lti_staff_role="Instructor",
            lti_account_wide=False,
        ),
    ],
)
async def test_non_account_managers_cannot_start_brightspace_connect(principal):
    from src.api.brightspace_routes import (
        BrightspaceConnectRequest,
        connect_brightspace,
    )

    db = MagicMock()
    with (
        patch("src.api.brightspace_routes.OAuthStateManager.create_state") as state,
        patch("src.api.brightspace_routes.get_brightspace_authorization_url") as oauth,
        pytest.raises(HTTPException) as caught,
    ):
        await connect_brightspace(
            BrightspaceConnectRequest(
                brightspace_instance_url="https://brightspace.example"
            ),
            db=db,
            principal=principal,
        )
    assert caught.value.status_code == 403
    state.assert_not_called()
    oauth.assert_not_called()
    db.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "principal",
    [
        _principal("api_key"),
        _principal("session"),
        _principal(
            "lti",
            lti_course_id="course-1",
            lti_staff_role="Instructor",
            lti_account_wide=False,
        ),
    ],
)
async def test_non_account_managers_cannot_disconnect_brightspace(principal):
    from src.api.brightspace_routes import disconnect_brightspace

    db = MagicMock()
    with pytest.raises(HTTPException) as caught:
        await disconnect_brightspace(principal=principal, db=db)
    assert caught.value.status_code == 403
    db.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    ["http://brightspace.example", "https://foreign.example"],
)
async def test_connect_rejects_unsafe_or_unallowlisted_origin_before_state(
    origin, monkeypatch
):
    from src.api.brightspace_routes import (
        BrightspaceConnectRequest,
        connect_brightspace,
    )

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    with (
        patch(
            "src.api.brightspace_routes.OAuthStateManager.create_state"
        ) as state_sink,
        patch(
            "src.api.brightspace_routes.get_brightspace_authorization_url"
        ) as auth_url,
        pytest.raises(HTTPException) as caught,
    ):
        await connect_brightspace(
            BrightspaceConnectRequest(brightspace_instance_url=origin),
            db=MagicMock(),
            principal=_principal("api_key", UserRole.ADMIN),
        )

    assert caught.value.status_code == 400
    state_sink.assert_not_called()
    auth_url.assert_not_called()


@pytest.mark.asyncio
async def test_connect_stores_only_canonical_allowlisted_origin(monkeypatch):
    from src.api.brightspace_routes import (
        BrightspaceConnectRequest,
        connect_brightspace,
    )

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    with (
        patch(
            "src.api.brightspace_routes.OAuthStateManager.create_state",
            return_value="state-1",
        ) as state_sink,
        patch(
            "src.api.brightspace_routes.get_brightspace_authorization_url",
            return_value="https://auth.brightspace.com/oauth2/auth",
        ) as auth_url,
    ):
        result = await connect_brightspace(
            BrightspaceConnectRequest(
                brightspace_instance_url="https://BRIGHTSPACE.EXAMPLE:443/"
            ),
            db=MagicMock(),
            principal=_principal("api_key", UserRole.ADMIN),
        )

    assert result["state"] == "state-1"
    assert state_sink.call_args.kwargs["metadata"]["brightspace_instance_url"] == (
        "https://brightspace.example"
    )
    assert auth_url.call_args.kwargs["brightspace_instance_url"] == (
        "https://brightspace.example"
    )


def test_production_requires_nonempty_brightspace_origin_allowlist(monkeypatch):
    from src.utils.security import require_brightspace_oauth_allowed_origin

    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", raising=False)
    with pytest.raises(ValueError, match="allowlist is required"):
        require_brightspace_oauth_allowed_origin("https://brightspace.example")


@pytest.mark.asyncio
async def test_brightspace_safe_transport_rejects_rebinding_before_connect():
    from src.integrations.brightspace.safe_http import BrightspaceSafeNetworkBackend

    wrapped = MagicMock()
    wrapped.connect_tcp = AsyncMock()
    backend = BrightspaceSafeNetworkBackend(network_backend=wrapped)
    with (
        patch(
            "src.integrations.brightspace.safe_http.socket.getaddrinfo",
            return_value=PRIVATE_DNS,
        ),
        pytest.raises(ValueError, match="not allowed"),
    ):
        await backend.connect_tcp("brightspace.example", 443)
    wrapped.connect_tcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_brightspace_safe_transport_pins_public_dns_and_client_is_hardened(
    monkeypatch,
):
    from src.integrations.brightspace.brightspace_api import BrightspaceAPIClient
    from src.integrations.brightspace.safe_http import BrightspaceSafeAsyncHTTPTransport

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    wrapped = MagicMock()
    wrapped.connect_tcp = AsyncMock(return_value=MagicMock())
    transport = BrightspaceSafeAsyncHTTPTransport(network_backend=wrapped)
    with patch(
        "src.integrations.brightspace.safe_http.socket.getaddrinfo",
        return_value=PUBLIC_DNS,
    ):
        await transport._pool._network_backend.connect_tcp("brightspace.example", 443)
    wrapped.connect_tcp.assert_awaited_once()
    assert wrapped.connect_tcp.await_args.args[:2] == ("93.184.216.34", 443)

    with patch(
        "src.integrations.brightspace.brightspace_api.httpx.AsyncClient"
    ) as ctor:
        client = BrightspaceAPIClient("https://brightspace.example", "bearer")
        await client._get_client()
    assert ctor.call_args.kwargs["trust_env"] is False
    assert ctor.call_args.kwargs["follow_redirects"] is False
    assert isinstance(
        ctor.call_args.kwargs["transport"], BrightspaceSafeAsyncHTTPTransport
    )
    assert client.api_base == "https://brightspace.example/d2l/api"


@pytest.mark.asyncio
async def test_slow_worker_finishes_before_response_and_next_worker_without_overlap():
    from src.api.brightspace_routes import _run_brightspace_worker

    events = []
    active = 0
    maximum = 0
    lock = threading.Lock()

    def worker(name):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            events.append(f"start-{name}")
        time.sleep(0.04)
        with lock:
            events.append(f"end-{name}")
            active -= 1
        return name

    first = await _run_brightspace_worker("dept-serial", worker, "one")
    second = await _run_brightspace_worker("dept-serial", worker, "two")
    assert (first, second) == ("one", "two")
    assert events == ["start-one", "end-one", "start-two", "end-two"]
    assert maximum == 1


@pytest.mark.asyncio
async def test_cancelled_worker_completes_before_department_slot_reuse():
    from src.api.brightspace_routes import _run_brightspace_worker

    events = []
    started = threading.Event()

    def slow():
        events.append("slow-start")
        started.set()
        time.sleep(0.06)
        events.append("slow-end")

    task = asyncio.create_task(_run_brightspace_worker("dept-cancel", slow))
    await asyncio.to_thread(started.wait, 1)
    task.cancel()
    follower = asyncio.create_task(
        _run_brightspace_worker("dept-cancel", lambda: events.append("next-start"))
    )
    with pytest.raises(asyncio.CancelledError):
        await task
    await follower
    assert events == ["slow-start", "slow-end", "next-start"]


@pytest.mark.asyncio
async def test_worker_executor_bounds_global_slots_across_departments():
    from src.api.brightspace_routes import (
        BRIGHTSPACE_WORKER_MAX_GLOBAL,
        _run_brightspace_worker,
    )

    active = 0
    maximum = 0
    lock = threading.Lock()

    def worker():
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.04)
        with lock:
            active -= 1

    await asyncio.gather(
        *(
            _run_brightspace_worker(f"global-dept-{index}", worker)
            for index in range(BRIGHTSPACE_WORKER_MAX_GLOBAL + 3)
        )
    )
    assert maximum == BRIGHTSPACE_WORKER_MAX_GLOBAL


@pytest.mark.asyncio
async def test_ensure_valid_token_uses_manager_then_reloads_exact_active_credential(
    monkeypatch,
):
    from src.api.brightspace_routes import _ensure_valid_token

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    stale = _credential()
    fresh = _credential(
        access_token="x",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db = MagicMock()
    db.get.return_value = fresh
    manager = MagicMock()
    manager.refresh_if_expired = AsyncMock(return_value="ignored-stale-return")
    manager.decrypt_token.return_value = "x"

    with patch("src.api.brightspace_routes.OAuthTokenManager", return_value=manager):
        token = await _ensure_valid_token(stale, db)

    from src.db.models import CloudOAuthCredentials

    manager.refresh_if_expired.assert_awaited_once_with(stale, db)
    db.get.assert_called_with(CloudOAuthCredentials, "cred-1", populate_existing=True)
    manager.decrypt_token.assert_called_once_with("x")
    assert token == "x"
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_valid_token_rejects_revocation_while_waiting(monkeypatch):
    from src.api.brightspace_routes import _ensure_valid_token

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    stale = _credential()
    revoked = _credential(is_active=False)
    db = MagicMock()
    db.get.return_value = revoked
    manager = MagicMock()
    manager.refresh_if_expired = AsyncMock(return_value="new-access")

    with (
        patch("src.api.brightspace_routes.OAuthTokenManager", return_value=manager),
        pytest.raises(HTTPException) as caught,
    ):
        await _ensure_valid_token(stale, db)

    assert caught.value.status_code == 409
    manager.decrypt_token.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("dns", [PUBLIC_DNS, PRIVATE_DNS])
async def test_callback_rejects_unallowed_or_private_state_origin_before_token_or_bearer(
    monkeypatch, dns
):
    from src.api.brightspace_routes import brightspace_oauth_callback

    monkeypatch.setenv("ENV", "production")
    allowed = (
        "https://trusted.example" if dns == PUBLIC_DNS else "https://private.example"
    )
    state_origin = (
        "https://attacker.example" if dns == PUBLIC_DNS else "https://private.example"
    )
    monkeypatch.setenv("BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", allowed)
    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", lambda *_a, **_k: dns)
    with (
        patch(
            "src.api.brightspace_routes.OAuthStateManager.verify_and_consume_state",
            return_value=(
                True,
                {
                    "department_id": "dept-1",
                    "brightspace_instance_url": state_origin,
                },
            ),
        ),
        patch(
            "src.api.brightspace_routes.exchange_brightspace_code_for_token",
            new=AsyncMock(),
        ) as token_sink,
        patch("src.api.brightspace_routes.BrightspaceAPIClient") as bearer_sink,
        pytest.raises(HTTPException) as caught,
    ):
        await brightspace_oauth_callback("code", "state", MagicMock())

    assert caught.value.status_code == 400
    token_sink.assert_not_awaited()
    bearer_sink.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_brightspace_refresh_uses_one_locked_rotation(monkeypatch):
    from cryptography.fernet import Fernet

    from src.integrations.oauth_token_manager import OAuthTokenManager

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )

    current = _credential()

    class FakeDB:
        def get(self, model, key, populate_existing=False):
            assert key == "cred-1"
            assert populate_existing is True
            return current

        def commit(self):
            return None

        def refresh(self, value):
            return None

    class FakeRedis:
        def __init__(self):
            self.value = None

        def set(self, _key, value, nx=False, ex=None):
            assert nx is True and ex is not None
            if self.value is not None:
                return False
            self.value = value
            return True

        def eval(self, script, _keys, _key, owner, *args):
            if "pexpire" in script:
                return int(self.value == owner)
            if self.value == owner:
                self.value = None
                return 1
            return 0

    manager = OAuthTokenManager(encryption_key=Fernet.generate_key().decode())
    manager.decrypt_token = MagicMock(side_effect=lambda value: value)
    manager.encrypt_token = MagicMock(side_effect=lambda value: value)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)

    async def rotate(**kwargs):
        assert kwargs["refresh_token"] == "rotated-refresh"
        await asyncio.sleep(0.05)
        return "new-access", "new-rotated-refresh", expires

    with (
        patch("src.auth.redis_rate_limiter.get_redis_client", return_value=FakeRedis()),
        patch(
            "src.integrations.brightspace.brightspace_oauth.refresh_brightspace_token",
            new=AsyncMock(side_effect=rotate),
        ) as refresh,
    ):
        tokens = await asyncio.gather(
            manager.refresh_if_expired(_credential(), FakeDB(), lock_timeout=2),
            manager.refresh_if_expired(_credential(), FakeDB(), lock_timeout=2),
        )

    assert tokens == ["new-access", "new-access"]
    refresh.assert_awaited_once()
    assert current.refresh_token == "new-rotated-refresh"
    assert current.access_token == "new-access"


@pytest.mark.asyncio
async def test_brightspace_refresh_redis_outage_fails_closed_before_http(monkeypatch):
    from cryptography.fernet import Fernet

    from src.integrations.oauth_token_manager import (
        OAuthTokenManager,
        TokenRefreshError,
    )

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    current = _credential()
    db = MagicMock()
    db.get.return_value = current
    manager = OAuthTokenManager(encryption_key=Fernet.generate_key().decode())
    manager.decrypt_token = MagicMock(side_effect=lambda value: value)
    with (
        patch(
            "src.auth.redis_rate_limiter.get_redis_client",
            side_effect=ConnectionError("redis secret detail"),
        ),
        patch(
            "src.integrations.brightspace.brightspace_oauth.refresh_brightspace_token",
            new=AsyncMock(),
        ) as refresh,
        pytest.raises(
            TokenRefreshError,
            match="Brightspace token refresh coordination unavailable",
        ) as caught,
    ):
        await manager.refresh_if_expired(current, db)
    refresh.assert_not_awaited()
    db.commit.assert_not_called()
    assert "redis secret detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_google_refresh_preserves_historical_unlocked_redis_outage_behavior():
    from cryptography.fernet import Fernet

    from src.integrations.oauth_token_manager import OAuthTokenManager

    credential = _credential(
        provider=CloudProvider.GOOGLE.value,
        provider_metadata={},
    )
    db = MagicMock()
    manager = OAuthTokenManager(encryption_key=Fernet.generate_key().decode())
    manager.decrypt_token = MagicMock(side_effect=lambda value: value)
    manager.encrypt_token = MagicMock(side_effect=lambda value: value)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    manager.refresh_google_token = AsyncMock(
        return_value=("google-access", "google-refresh", expires)
    )
    with patch(
        "src.auth.redis_rate_limiter.get_redis_client",
        side_effect=ConnectionError("offline"),
    ):
        result = await manager.refresh_if_expired(credential, db)
    assert result == "google-access"
    manager.refresh_google_token.assert_awaited_once_with("rotated-refresh")
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_brightspace_refresh_renews_lease_during_long_http(monkeypatch):
    from cryptography.fernet import Fernet

    from src.integrations.oauth_token_manager import OAuthTokenManager

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    current = _credential()

    class RedisLease:
        def __init__(self):
            self.value = None
            self.renewals = 0

        def set(self, key, value, nx=False, ex=None):
            assert nx is True and ex == 1
            if self.value is not None:
                return False
            self.value = value
            return True

        def eval(self, script, keys, key, owner, *args):
            assert keys == 1 and key == "token_refresh:cred-1"
            if "pexpire" in script:
                if self.value != owner:
                    return 0
                self.renewals += 1
                return 1
            if self.value == owner:
                self.value = None
                return 1
            return 0

    redis = RedisLease()
    db = MagicMock()
    db.get.return_value = current
    manager = OAuthTokenManager(encryption_key=Fernet.generate_key().decode())
    manager.decrypt_token = MagicMock(side_effect=lambda value: value)
    manager.encrypt_token = MagicMock(side_effect=lambda value: value)

    async def slow_refresh(**_kwargs):
        await asyncio.sleep(0.45)
        return (
            "new-access",
            "new-refresh",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )

    with (
        patch("src.auth.redis_rate_limiter.get_redis_client", return_value=redis),
        patch(
            "src.integrations.brightspace.brightspace_oauth.refresh_brightspace_token",
            new=AsyncMock(side_effect=slow_refresh),
        ),
    ):
        result = await manager.refresh_if_expired(current, db, lock_timeout=1)
    assert result == "new-access"
    assert redis.renewals >= 1
    assert redis.value is None


@pytest.mark.asyncio
async def test_lost_brightspace_refresh_lock_never_deletes_successor_or_persists(
    monkeypatch,
):
    from cryptography.fernet import Fernet

    from src.integrations.oauth_token_manager import (
        OAuthTokenManager,
        TokenRefreshError,
    )

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    current = _credential()

    class RedisSuccessor:
        value = None

        def set(self, _key, value, nx=False, ex=None):
            self.value = value
            return True

        def eval(self, script, _keys, _key, owner, *args):
            if "pexpire" in script:
                self.value = "successor-owner"
                return 0
            if self.value == owner:
                self.value = None
                return 1
            return 0

    redis = RedisSuccessor()
    db = MagicMock()
    db.get.return_value = current
    manager = OAuthTokenManager(encryption_key=Fernet.generate_key().decode())
    manager.decrypt_token = MagicMock(side_effect=lambda value: value)
    manager.encrypt_token = MagicMock(side_effect=lambda value: value)

    async def slow_refresh(**_kwargs):
        await asyncio.sleep(0.4)
        return (
            "stale-access",
            "stale-refresh",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )

    with (
        patch("src.auth.redis_rate_limiter.get_redis_client", return_value=redis),
        patch(
            "src.integrations.brightspace.brightspace_oauth.refresh_brightspace_token",
            new=AsyncMock(side_effect=slow_refresh),
        ),
        pytest.raises(TokenRefreshError, match="Brightspace token refresh lock lost"),
    ):
        await manager.refresh_if_expired(current, db, lock_timeout=1)
    assert redis.value == "successor-owner"
    db.commit.assert_not_called()
    assert current.access_token == "old-access"


@pytest.mark.asyncio
async def test_cancelled_brightspace_refresh_releases_only_owned_lock(monkeypatch):
    from cryptography.fernet import Fernet

    from src.integrations.oauth_token_manager import OAuthTokenManager

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    current = _credential()

    class RedisLease:
        value = None

        def set(self, _key, value, nx=False, ex=None):
            self.value = value
            return True

        def eval(self, script, _keys, _key, owner, *args):
            if "pexpire" in script:
                return int(self.value == owner)
            if self.value == owner:
                self.value = None
                return 1
            return 0

    redis = RedisLease()
    db = MagicMock()
    db.get.return_value = current
    manager = OAuthTokenManager(encryption_key=Fernet.generate_key().decode())
    manager.decrypt_token = MagicMock(side_effect=lambda value: value)
    started = asyncio.Event()

    async def blocked_refresh(**_kwargs):
        started.set()
        await asyncio.Event().wait()

    with (
        patch("src.auth.redis_rate_limiter.get_redis_client", return_value=redis),
        patch(
            "src.integrations.brightspace.brightspace_oauth.refresh_brightspace_token",
            new=AsyncMock(side_effect=blocked_refresh),
        ),
    ):
        task = asyncio.create_task(
            manager.refresh_if_expired(current, db, lock_timeout=1)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert redis.value is None
    db.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["exchange", "refresh"])
async def test_brightspace_token_transport_is_fixed_and_hardened(operation):
    from src.integrations.brightspace import brightspace_oauth

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_in": 3600,
    }
    client = AsyncMock()
    client.post.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None
    with patch.object(
        brightspace_oauth.httpx, "AsyncClient", return_value=context
    ) as ctor:
        if operation == "exchange":
            await brightspace_oauth.exchange_brightspace_code_for_token(
                "https://tenant.brightspace.example/attacker/path",
                "code",
                "https://app.example/callback",
                client_id="client",
                client_secret="x",
            )
        else:
            await brightspace_oauth.refresh_brightspace_token(
                "https://tenant.brightspace.example/attacker/path",
                "refresh-secret",
                client_id="client",
                client_secret="x",
            )
    assert ctor.call_args.kwargs["trust_env"] is False
    assert ctor.call_args.kwargs["follow_redirects"] is False
    assert 0 < ctor.call_args.kwargs["timeout"] <= 30
    assert client.post.await_args.args[0] == (
        "https://auth.brightspace.com/core/connect/token"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://auth.brightspace.com/core/connect/token",
        "https://user@auth.brightspace.com/core/connect/token",
        "https://foreign.example/core/connect/token",
        "https://auth.brightspace.com/attacker/token",
        "https://auth.brightspace.com/core/connect/token?next=evil",
    ],
)
async def test_brightspace_token_transport_rejects_noncanonical_endpoint_before_http(
    monkeypatch, endpoint
):
    from src.integrations.brightspace import brightspace_oauth

    monkeypatch.setattr(brightspace_oauth, "BRIGHTSPACE_TOKEN_URL", endpoint)
    with (
        patch.object(brightspace_oauth.httpx, "AsyncClient") as ctor,
        pytest.raises(brightspace_oauth.BrightspaceOAuthError, match="endpoint"),
    ):
        await brightspace_oauth.refresh_brightspace_token(
            "https://tenant.example",
            "refresh-secret",
            client_id="client",
            client_secret="x",
        )
    ctor.assert_not_called()


@pytest.mark.asyncio
async def test_brightspace_token_redirect_is_rejected_and_failure_is_sanitized(caplog):
    import httpx

    from src.integrations.brightspace import brightspace_oauth

    request = httpx.Request("POST", "https://auth.brightspace.com/core/connect/token")
    response = httpx.Response(
        302,
        headers={"location": "https://evil.example/steal"},
        request=request,
    )
    client = AsyncMock()
    client.post.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None
    with (
        patch.object(brightspace_oauth.httpx, "AsyncClient", return_value=context),
        pytest.raises(
            brightspace_oauth.BrightspaceOAuthError,
            match="Brightspace OAuth token request failed",
        ) as caught,
    ):
        await brightspace_oauth.refresh_brightspace_token(
            "https://tenant.example",
            "refresh-secret",
            client_id="client-secret-id",
            client_secret="x",
        )
    combined = str(caught.value) + caplog.text
    assert "refresh-secret" not in combined
    assert "x" not in combined
    assert "evil.example" not in combined
