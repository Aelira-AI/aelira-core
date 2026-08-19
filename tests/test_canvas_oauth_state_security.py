"""Security invariants for the Canvas OAuth callback boundary."""

import base64
import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis
from fastapi import HTTPException

from src.auth.redis_rate_limiter import OAuthStateManager, OAuthStateStorageError

from src.api.canvas_routes import (
    CanvasConnectRequest,
    canvas_oauth_callback,
    connect_canvas,
    disconnect_canvas,
)
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import UserRole
from src.utils.security import (
    require_canvas_oauth_allowed_origin,
    validate_canvas_instance_origin,
)
from src.utils import security as security_utils


def _principal(
    role: UserRole = UserRole.ADMIN,
    auth_method: Literal["api_key", "session", "lti", "mock"] = "session",
):
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="department-1",
        user_role=role,
        auth_method=auth_method,
    )


def test_canvas_origin_is_canonicalized_after_public_dns_validation():
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with patch("src.utils.security.socket.getaddrinfo", return_value=public_dns):
        result = validate_canvas_instance_origin(
            "https://Canvas.Example.EDU:443/", environment="production"
        )

    assert result == "https://canvas.example.edu"


def test_production_canvas_origin_must_exactly_match_canonical_operator_allowlist():
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with patch("src.utils.security.socket.getaddrinfo", return_value=public_dns):
        assert (
            require_canvas_oauth_allowed_origin(
                "https://Canvas.Example.EDU:443/",
                environment="production",
                configured_origins="https://canvas.example.edu/",
            )
            == "https://canvas.example.edu"
        )
        with pytest.raises(ValueError, match="not authorized"):
            require_canvas_oauth_allowed_origin(
                "https://evil.canvas.example.edu",
                environment="production",
                configured_origins="https://canvas.example.edu",
            )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_deployed_canvas_oauth_requires_an_operator_allowlist(environment):
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with patch("src.utils.security.socket.getaddrinfo", return_value=public_dns):
        with pytest.raises(ValueError, match="CANVAS_OAUTH_ALLOWED_ORIGINS"):
            require_canvas_oauth_allowed_origin(
                "https://canvas.example.edu",
                environment=environment,
                configured_origins="",
            )


@pytest.mark.asyncio
async def test_connect_issues_opaque_state_bound_to_canonical_origin():
    principal = _principal()
    oauth = MagicMock()
    oauth.is_configured.return_value = True
    oauth.get_authorization_url.return_value = (
        "https://canvas.example.edu/login/oauth2/auth"
    )
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with (
        patch("src.api.canvas_routes.verify_department_access"),
        patch("src.api.canvas_routes.require_feature"),
        patch("src.api.canvas_routes.CanvasOAuthService", return_value=oauth),
        patch(
            "src.api.canvas_routes.OAuthStateManager.create_state",
            return_value="opaque-state-token",
        ) as create_state,
        patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
    ):
        result = await connect_canvas(
            CanvasConnectRequest(
                canvas_instance_url="https://Canvas.Example.EDU:443/",
                department_id="department-1",
            ),
            db=MagicMock(),
            principal=principal,
        )

    assert result["state"] == "opaque-state-token"
    create_state.assert_called_once_with(
        metadata={
            "provider": "canvas",
            "department_id": "department-1",
            "canvas_instance_url": "https://canvas.example.edu",
            "initiating_user_id": "user-1",
        },
        allow_memory_fallback=True,
    )
    oauth.get_authorization_url.assert_called_once_with(
        canvas_instance_url="https://canvas.example.edu",
        state="opaque-state-token",
    )


@pytest.mark.asyncio
async def test_production_rejects_public_attacker_origin_before_state_or_outbound_work():
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]
    resolved_hosts = []

    def resolve(host, *args, **kwargs):
        resolved_hosts.append(host)
        return public_dns

    db = MagicMock()

    with (
        patch.dict(
            "os.environ",
            {
                "ENV": "production",
                "CANVAS_OAUTH_ALLOWED_ORIGINS": "https://canvas.example.edu",
            },
        ),
        patch("src.utils.security.socket.getaddrinfo", side_effect=resolve),
        patch("src.api.canvas_routes.OAuthStateManager.create_state") as create_state,
        patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service,
        patch("src.api.canvas_routes.require_feature") as require_feature,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await connect_canvas(
                CanvasConnectRequest(
                    canvas_instance_url="https://globally-routable-attacker.example",
                    department_id="department-1",
                ),
                db=db,
                principal=_principal(),
            )

    assert exc_info.value.status_code == 400
    assert "globally-routable-attacker.example" not in resolved_hosts
    create_state.assert_not_called()
    oauth_service.assert_not_called()
    require_feature.assert_not_called()
    db.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_method", ["api_key", "session"])
async def test_faculty_connect_is_denied_before_state_external_or_database_work(
    auth_method,
):
    db = MagicMock()

    with (
        patch("src.api.canvas_routes.OAuthStateManager.create_state") as create_state,
        patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service,
        patch("src.api.canvas_routes.require_feature") as require_feature,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await connect_canvas(
                CanvasConnectRequest(
                    canvas_instance_url="http://localhost:3000",
                    department_id="department-1",
                ),
                db=db,
                principal=_principal(UserRole.FACULTY, auth_method),
            )

    assert exc_info.value.status_code == 403
    create_state.assert_not_called()
    oauth_service.assert_not_called()
    require_feature.assert_not_called()
    db.assert_not_called()


@pytest.mark.asyncio
async def test_faculty_disconnect_is_denied_before_database_work():
    db = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await disconnect_canvas(
            department_id="department-1",
            db=db,
            principal=_principal(UserRole.FACULTY, "session"),
        )

    assert exc_info.value.status_code == 403
    db.query.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("redis_client", [None, MagicMock()])
def test_strict_state_creation_fails_closed_without_redis_or_on_store_error(
    redis_client,
):
    if redis_client is not None:
        redis_client.setex.side_effect = redis.RedisError("store unavailable")

    with patch(
        "src.auth.redis_rate_limiter.get_redis_client", return_value=redis_client
    ):
        with pytest.raises(OAuthStateStorageError):
            OAuthStateManager.create_state(
                metadata={"provider": "canvas"}, allow_memory_fallback=False
            )


@pytest.mark.parametrize("redis_client", [None, MagicMock()])
def test_strict_state_verification_never_accepts_memory_on_redis_failure(redis_client):
    state = OAuthStateManager.create_state(
        metadata={"provider": "canvas"}, allow_memory_fallback=True
    )
    if redis_client is not None:
        redis_client.getdel.side_effect = redis.RedisError("read unavailable")

    with patch(
        "src.auth.redis_rate_limiter.get_redis_client", return_value=redis_client
    ):
        result = OAuthStateManager.verify_and_consume_state(
            state, allow_memory_fallback=False
        )

    assert result == (False, None)


@pytest.mark.asyncio
async def test_callback_revalidates_state_origin_before_oauth_or_network_calls():
    metadata = {
        "provider": "canvas",
        "department_id": "department-1",
        "canvas_instance_url": "https://127.0.0.1",
        "initiating_user_id": "user-1",
    }
    with (
        patch(
            "src.api.canvas_routes.OAuthStateManager.verify_and_consume_state",
            return_value=(True, metadata),
        ),
        patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await canvas_oauth_callback(
                code="valid-code",
                state="opaque-state-token",
                error=None,
                error_description=None,
                db=MagicMock(),
            )

    assert exc_info.value.status_code == 400
    oauth_service.assert_not_called()


@pytest.mark.asyncio
async def test_callback_rejects_state_origin_removed_from_operator_allowlist():
    metadata = {
        "provider": "canvas",
        "department_id": "department-1",
        "canvas_instance_url": "https://old-canvas.example.edu",
        "initiating_user_id": "user-1",
    }
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]
    db = MagicMock()

    with (
        patch.dict(
            "os.environ",
            {
                "ENV": "production",
                "CANVAS_OAUTH_ALLOWED_ORIGINS": "https://new-canvas.example.edu",
            },
        ),
        patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
        patch(
            "src.api.canvas_routes.OAuthStateManager.verify_and_consume_state",
            return_value=(True, metadata),
        ),
        patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await canvas_oauth_callback(
                code="attacker-code",
                state="opaque-state-token",
                error=None,
                error_description=None,
                db=db,
            )

    assert exc_info.value.status_code == 400
    oauth_service.assert_not_called()
    db.query.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_forged_legacy_base64_state_is_rejected_before_oauth_exchange():
    forged_state = base64.urlsafe_b64encode(
        json.dumps(
            {
                "csrf": "attacker-chosen",
                "department_id": "victim-department",
                "canvas_instance_url": "https://attacker.example",
            }
        ).encode()
    ).decode()

    with patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service:
        with pytest.raises(HTTPException) as exc_info:
            await canvas_oauth_callback(
                code="attacker-code",
                state=forged_state,
                error=None,
                error_description=None,
                db=MagicMock(),
            )

    assert exc_info.value.status_code == 400
    oauth_service.assert_not_called()


@pytest.mark.asyncio
async def test_issued_state_succeeds_once_and_replay_is_rejected():
    metadata = {
        "provider": "canvas",
        "department_id": "authoritative-department",
        "canvas_instance_url": "https://canvas.example.edu",
        "initiating_user_id": "user-1",
    }
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]
    credential = SimpleNamespace(
        access_token="access-token",
        refresh_token=None,
        expires_at=None,
        scope=None,
        user_id="canvas-user",
    )
    user = SimpleNamespace(
        email="teacher+admin@example.edu&canvas=error", name="Teacher"
    )
    oauth = MagicMock()
    oauth.exchange_code_for_token = AsyncMock(return_value=credential)
    api_client = MagicMock()
    api_client.get_current_user = AsyncMock(return_value=user)
    api_client.close = AsyncMock()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    OAuthStateManager._memory_states.clear()
    with patch("src.auth.redis_rate_limiter.get_redis_client", return_value=None):
        state = OAuthStateManager.create_state(metadata=metadata)
        with (
            patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
            patch(
                "src.api.canvas_routes.CanvasOAuthService", return_value=oauth
            ) as service,
            patch("src.api.canvas_routes.CanvasAPIClient", return_value=api_client),
            patch("src.api.canvas_routes.OAuthTokenManager") as token_manager,
        ):
            token_manager.return_value.encrypt_token.return_value = "encrypted"
            response = await canvas_oauth_callback(
                code="valid-code",
                state=state,
                error=None,
                error_description=None,
                db=db,
            )
            with pytest.raises(HTTPException) as replay_error:
                await canvas_oauth_callback(
                    code="valid-code",
                    state=state,
                    error=None,
                    error_description=None,
                    db=db,
                )

    assert response.status_code == 307
    assert (
        "email=teacher%2Badmin%40example.edu%26canvas%3Derror"
        in response.headers["location"]
    )
    assert replay_error.value.status_code == 400
    service.assert_called_once()
    oauth.exchange_code_for_token.assert_awaited_once_with(
        canvas_instance_url="https://canvas.example.edu",
        authorization_code="valid-code",
    )
    added_credential = db.add.call_args.args[0]
    assert added_credential.department_id == "authoritative-department"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_metadata,expire",
    [
        (
            {
                "provider": "microsoft",
                "department_id": "d",
                "canvas_instance_url": "https://canvas.example.edu",
                "initiating_user_id": "user-1",
            },
            False,
        ),
        (
            {
                "provider": "canvas",
                "department_id": "d",
                "canvas_instance_url": "https://canvas.example.edu",
                "initiating_user_id": "user-1",
            },
            True,
        ),
    ],
)
async def test_provider_mismatch_and_expired_state_are_rejected_before_oauth(
    state_metadata, expire
):
    OAuthStateManager._memory_states.clear()
    with patch("src.auth.redis_rate_limiter.get_redis_client", return_value=None):
        state = OAuthStateManager.create_state(metadata=state_metadata)
        if expire:
            OAuthStateManager._memory_states[state][
                "expires_at"
            ] = datetime.utcnow() - timedelta(seconds=1)
        with patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service:
            with pytest.raises(HTTPException) as exc_info:
                await canvas_oauth_callback(
                    code="valid-code",
                    state=state,
                    error=None,
                    error_description=None,
                    db=MagicMock(),
                )

    assert exc_info.value.status_code == 400
    oauth_service.assert_not_called()


@pytest.mark.asyncio
async def test_missing_state_is_rejected_before_oauth():
    with patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service:
        with pytest.raises(HTTPException) as exc_info:
            await canvas_oauth_callback(
                code="valid-code",
                state="",
                error=None,
                error_description=None,
                db=MagicMock(),
            )

    assert exc_info.value.status_code == 400
    oauth_service.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://canvas.example.edu",
        "https://user:password@canvas.example.edu",
        "https://canvas.example.edu/path",
        "https://canvas.example.edu?next=/path",
        "https://canvas.example.edu#fragment",
        "https://127.0.0.1",
        "https://[::1]",
    ],
)
def test_production_rejects_non_https_non_origin_and_private_urls(url):
    resolved_private = [(2, 1, 6, "", ("10.0.0.1", 0))]
    with patch("src.utils.security.socket.getaddrinfo", return_value=resolved_private):
        with pytest.raises(ValueError):
            validate_canvas_instance_origin(url, environment="production")


@pytest.mark.parametrize(
    "resolved_address",
    [
        "10.0.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "ff02::1",
    ],
)
def test_canvas_origin_rejects_all_non_public_dns_targets(resolved_address):
    resolved = [(2, 1, 6, "", (resolved_address, 0))]
    with patch("src.utils.security.socket.getaddrinfo", return_value=resolved):
        with pytest.raises(ValueError):
            validate_canvas_instance_origin(
                "https://canvas.example.edu", environment="production"
            )


def test_development_accepts_explicit_localhost_http_origin():
    assert (
        validate_canvas_instance_origin(
            "http://LOCALHOST:3000/", environment="development"
        )
        == "http://localhost:3000"
    )


@pytest.mark.asyncio
async def test_production_connect_returns_service_unavailable_when_state_store_fails():
    principal = _principal()
    oauth = MagicMock()
    oauth.is_configured.return_value = True
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with (
        patch.dict(
            "os.environ",
            {
                "ENV": "production",
                "CANVAS_OAUTH_ALLOWED_ORIGINS": "https://canvas.example.edu",
            },
        ),
        patch("src.api.canvas_routes.verify_department_access"),
        patch("src.api.canvas_routes.require_feature"),
        patch("src.api.canvas_routes.CanvasOAuthService", return_value=oauth),
        patch(
            "src.api.canvas_routes.OAuthStateManager.create_state",
            side_effect=OAuthStateStorageError("unavailable"),
        ) as create_state,
        patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await connect_canvas(
                CanvasConnectRequest(
                    canvas_instance_url="https://canvas.example.edu",
                    department_id="department-1",
                ),
                db=MagicMock(),
                principal=principal,
            )

    assert exc_info.value.status_code == 503
    assert create_state.call_args.kwargs["allow_memory_fallback"] is False
    oauth.get_authorization_url.assert_not_called()


@pytest.mark.asyncio
async def test_canvas_token_exchange_explicitly_disables_redirects():
    from src.integrations.canvas.canvas_oauth import CanvasOAuthService

    response = MagicMock()
    response.json.return_value = {"access_token": "token", "user": {"id": "1"}}
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    async_client = MagicMock()
    async_client.return_value.__aenter__ = AsyncMock(return_value=client)
    async_client.return_value.__aexit__ = AsyncMock(return_value=None)
    service = CanvasOAuthService(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://api.example.edu/callback",
    )

    with patch("src.integrations.canvas.canvas_oauth.httpx.AsyncClient", async_client):
        await service.exchange_code_for_token(
            canvas_instance_url="https://canvas.example.edu",
            authorization_code="code",
        )

    kwargs = async_client.call_args.kwargs
    assert kwargs["follow_redirects"] is False
    assert kwargs["trust_env"] is False
    from src.integrations.canvas.safe_http import CanvasSafeAsyncHTTPTransport

    assert isinstance(kwargs["transport"], CanvasSafeAsyncHTTPTransport)


@pytest.mark.asyncio
async def test_canvas_token_exchange_maps_dev_localhost_to_docker_origin(monkeypatch):
    from src.integrations.canvas.canvas_oauth import CanvasOAuthService

    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("CANVAS_DOCKER_ORIGIN", "http://canvas-docker.internal:3999")
    response = MagicMock()
    response.json.return_value = {"access_token": "token", "user": {"id": "1"}}
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    async_client = MagicMock()
    async_client.return_value.__aenter__ = AsyncMock(return_value=client)
    async_client.return_value.__aexit__ = AsyncMock(return_value=None)
    service = CanvasOAuthService(client_id="id", client_secret="secret")

    with patch("src.integrations.canvas.canvas_oauth.httpx.AsyncClient", async_client):
        await service.exchange_code_for_token("http://localhost:3000", "code")

    assert client.post.await_args.args[0] == (
        "http://canvas-docker.internal:3999/login/oauth2/token"
    )


@pytest.mark.asyncio
async def test_canvas_refresh_client_uses_safe_transport(monkeypatch):
    from src.integrations.canvas.canvas_oauth import CanvasOAuthService
    from src.integrations.canvas.safe_http import CanvasSafeAsyncHTTPTransport

    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("CANVAS_DOCKER_ORIGIN", raising=False)
    response = MagicMock()
    response.json.return_value = {"access_token": "new-token"}
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    async_client = MagicMock()
    async_client.return_value.__aenter__ = AsyncMock(return_value=client)
    async_client.return_value.__aexit__ = AsyncMock(return_value=None)
    service = CanvasOAuthService(client_id="id", client_secret="secret")

    with patch("src.integrations.canvas.canvas_oauth.httpx.AsyncClient", async_client):
        await service.refresh_access_token("http://localhost:3000", "refresh")

    kwargs = async_client.call_args.kwargs
    assert kwargs["trust_env"] is False
    assert isinstance(kwargs["transport"], CanvasSafeAsyncHTTPTransport)
    assert client.post.await_args.args[0] == (
        "http://host.docker.internal:3000/login/oauth2/token"
    )


@pytest.mark.asyncio
async def test_malformed_state_metadata_is_rejected_before_oauth():
    with (
        patch(
            "src.api.canvas_routes.OAuthStateManager.verify_and_consume_state",
            return_value=(True, "not-a-metadata-object"),
        ),
        patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await canvas_oauth_callback(
                code="valid-code",
                state="opaque-state",
                error=None,
                error_description=None,
                db=MagicMock(),
            )

    assert exc_info.value.status_code == 400
    oauth_service.assert_not_called()


@pytest.mark.asyncio
async def test_state_without_initiating_user_binding_is_rejected_before_oauth():
    metadata = {
        "provider": "canvas",
        "department_id": "department-1",
        "canvas_instance_url": "https://canvas.example.edu",
    }
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with (
        patch(
            "src.api.canvas_routes.OAuthStateManager.verify_and_consume_state",
            return_value=(True, metadata),
        ),
        patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
        patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await canvas_oauth_callback(
                code="valid-code",
                state="opaque-state",
                error=None,
                error_description=None,
                db=MagicMock(),
            )

    assert exc_info.value.status_code == 400
    oauth_service.assert_not_called()


@pytest.mark.asyncio
async def test_production_callback_requires_redis_backed_state_verification():
    with (
        patch.dict("os.environ", {"ENV": "production"}),
        patch(
            "src.api.canvas_routes.OAuthStateManager.verify_and_consume_state",
            return_value=(False, None),
        ) as verify_state,
        patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service,
    ):
        with pytest.raises(HTTPException):
            await canvas_oauth_callback(
                code="valid-code",
                state="opaque-state",
                error=None,
                error_description=None,
                db=MagicMock(),
            )

    verify_state.assert_called_once_with("opaque-state", allow_memory_fallback=False)
    oauth_service.assert_not_called()


@pytest.mark.asyncio
async def test_callback_refusal_uses_stable_code_without_logging_untrusted_reason(
    caplog,
):
    metadata = {
        "provider": "canvas",
        "department_id": "department-1",
        "canvas_instance_url": "http://localhost:3000",
        "initiating_user_id": "user-1",
    }
    sensitive_reason = "secret callback detail\nforged log line"

    with (
        patch(
            "src.api.canvas_routes.OAuthStateManager.verify_and_consume_state",
            return_value=(True, metadata),
        ),
        caplog.at_level(logging.WARNING, logger="src.api.canvas_routes"),
    ):
        response = await canvas_oauth_callback(
            code=None,
            state="opaque-state",
            error="access_denied",
            error_description=sensitive_reason,
            db=MagicMock(),
        )

    assert "canvas=error&code=oauth_refused" in response.headers["location"]
    assert "message=" not in response.headers["location"]
    assert sensitive_reason not in response.headers["location"]
    assert sensitive_reason not in caplog.text


@pytest.mark.asyncio
async def test_callback_exception_uses_stable_code_without_logging_exception_text(
    caplog,
):
    metadata = {
        "provider": "canvas",
        "department_id": "department-1",
        "canvas_instance_url": "http://localhost:3000",
        "initiating_user_id": "user-1",
    }
    sensitive_exception = "provider response contained a secret"
    oauth = MagicMock()
    oauth.exchange_code_for_token = AsyncMock(
        side_effect=RuntimeError(sensitive_exception)
    )

    with (
        patch(
            "src.api.canvas_routes.OAuthStateManager.verify_and_consume_state",
            return_value=(True, metadata),
        ),
        patch("src.api.canvas_routes.CanvasOAuthService", return_value=oauth),
        caplog.at_level(logging.ERROR, logger="src.api.canvas_routes"),
    ):
        response = await canvas_oauth_callback(
            code="code",
            state="opaque-state",
            error=None,
            error_description=None,
            db=MagicMock(),
        )

    assert "canvas=error&code=callback_failed" in response.headers["location"]
    assert "message=" not in response.headers["location"]
    assert sensitive_exception not in response.headers["location"]
    assert sensitive_exception not in caplog.text


def test_persisted_canvas_origin_helper_fails_closed_for_missing_metadata():
    with pytest.raises(ValueError, match="reconnect Canvas"):
        security_utils.require_persisted_canvas_origin({})


@pytest.mark.asyncio
async def test_get_canvas_client_rejects_stale_origin_before_token_use():
    from src.api.canvas_routes import _get_canvas_client

    credential = SimpleNamespace(
        id="credential-1",
        provider_metadata={"canvas_instance_url": "https://old-canvas.example.edu"},
        access_token="encrypted-access",
        refresh_token="encrypted-refresh",
        token_expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = credential
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with (
        patch.dict(
            "os.environ",
            {
                "ENV": "production",
                "CANVAS_OAUTH_ALLOWED_ORIGINS": "https://new-canvas.example.edu",
            },
        ),
        patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
        patch("src.api.canvas_routes.OAuthTokenManager") as token_manager,
        patch("src.api.canvas_routes.CanvasOAuthService") as oauth_service,
        patch("src.api.canvas_routes.CanvasAPIClient") as api_client,
    ):
        token_manager.return_value.is_token_expired.return_value = False
        with pytest.raises(HTTPException) as exc_info:
            await _get_canvas_client("department-1", db)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == (
        "Canvas connection is no longer authorized. Please reconnect your Canvas account."
    )
    token_manager.return_value.decrypt_token.assert_not_called()
    oauth_service.assert_not_called()
    api_client.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_get_canvas_client_rechecks_current_allowlist_on_every_use():
    from src.api.canvas_routes import _get_canvas_client

    credential = SimpleNamespace(
        id="credential-1",
        provider_metadata={"canvas_instance_url": "https://canvas.example.edu"},
        access_token="encrypted-access",
        refresh_token="encrypted-refresh",
        token_expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = credential
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with (
        patch.dict(
            "os.environ",
            {
                "ENV": "production",
                "CANVAS_OAUTH_ALLOWED_ORIGINS": "https://canvas.example.edu",
            },
        ),
        patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
        patch("src.api.canvas_routes.OAuthTokenManager") as token_manager,
        patch("src.api.canvas_routes.CanvasAPIClient") as api_client,
    ):
        token_manager.return_value.is_token_expired.return_value = False
        token_manager.return_value.decrypt_token.return_value = "access-token"
        await _get_canvas_client("department-1", db)

    api_client.assert_called_once_with(
        canvas_instance_url="https://canvas.example.edu",
        access_token="access-token",
        credential_id="credential-1",
    )

    with (
        patch.dict(
            "os.environ",
            {
                "ENV": "production",
                "CANVAS_OAUTH_ALLOWED_ORIGINS": "https://replacement.example.edu",
            },
        ),
        patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
        patch("src.api.canvas_routes.OAuthTokenManager") as revoked_manager,
        patch("src.api.canvas_routes.CanvasAPIClient") as revoked_client,
    ):
        revoked_manager.return_value.is_token_expired.return_value = False
        with pytest.raises(HTTPException):
            await _get_canvas_client("department-1", db)

    revoked_manager.return_value.decrypt_token.assert_not_called()
    revoked_client.assert_not_called()


@pytest.mark.asyncio
async def test_canvas_token_manager_rejects_stale_origin_before_decryption_or_refresh():
    from cryptography.fernet import Fernet

    from src.integrations.oauth_token_manager import OAuthTokenManager

    manager = OAuthTokenManager(encryption_key=Fernet.generate_key().decode())
    manager.decrypt_token = MagicMock(return_value="plaintext-token")
    credential = SimpleNamespace(
        id="credential-1",
        provider="canvas",
        provider_metadata={"canvas_instance_url": "https://old-canvas.example.edu"},
        access_token="encrypted-access",
        refresh_token="encrypted-refresh",
        token_expires_at=datetime.utcnow() - timedelta(hours=1),
        scopes=[],
    )
    db = MagicMock()
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with (
        patch.dict(
            "os.environ",
            {
                "ENV": "production",
                "CANVAS_OAUTH_ALLOWED_ORIGINS": "https://new-canvas.example.edu",
            },
        ),
        patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
        patch("src.auth.redis_rate_limiter.get_redis_client", return_value=None),
        patch("src.integrations.canvas.CanvasOAuthService") as oauth_service,
    ):
        with pytest.raises(ValueError, match="reconnect Canvas"):
            await manager.refresh_if_expired(credential, db)

    manager.decrypt_token.assert_not_called()
    oauth_service.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_cloud_scan_canvas_sink_rejects_stale_origin_before_client_creation():
    from src.jobs.cloud_scan_job import CloudScanJob

    credential = SimpleNamespace(
        provider_metadata={"canvas_instance_url": "https://old-canvas.example.edu"}
    )
    cloud_file = SimpleNamespace(provider_file_id="file-1")
    job = CloudScanJob(credential, cloud_file, MagicMock())
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with (
        patch.dict(
            "os.environ",
            {
                "ENV": "production",
                "CANVAS_OAUTH_ALLOWED_ORIGINS": "https://new-canvas.example.edu",
            },
        ),
        patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
        patch("src.integrations.canvas.canvas_api.CanvasAPIClient") as api_client,
    ):
        result = await job._download_canvas("access-token", "/tmp/file")

    assert result == {
        "success": False,
        "error": "Canvas connection origin is invalid or no longer authorized; reconnect Canvas",
    }
    api_client.assert_not_called()


@pytest.mark.asyncio
async def test_content_background_sink_rejects_stale_origin_before_token_use():
    from src.api.canvas_content_routes import _content_scan_task

    cloud_file = SimpleNamespace(id="file-1")
    credential = SimpleNamespace(
        id="credential-1",
        provider_metadata={"canvas_instance_url": "https://old-canvas.example.edu"},
        access_token="encrypted-access",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [
        cloud_file,
        credential,
    ]
    db_context = MagicMock()
    db_context.__enter__.return_value = db
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]

    with (
        patch.dict(
            "os.environ",
            {
                "ENV": "production",
                "CANVAS_OAUTH_ALLOWED_ORIGINS": "https://new-canvas.example.edu",
            },
        ),
        patch("src.utils.security.socket.getaddrinfo", return_value=public_dns),
        patch("src.db.database.get_db", return_value=db_context),
        patch(
            "src.integrations.oauth_token_manager.OAuthTokenManager"
        ) as token_manager,
        patch("src.integrations.canvas.CanvasAPIClient") as api_client,
    ):
        await _content_scan_task("file-1", "department-1", "credential-1")

    token_manager.return_value.decrypt_token.assert_not_called()
    api_client.assert_not_called()
