"""Crash-safety regressions for initial Google webhook creation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError


def _request():
    from src.api.google_routes import GoogleWebhookSubscriptionRequest

    return GoogleWebhookSubscriptionRequest(
        notification_url="https://example.test/hooks/google",
        resource_id="watched-file",
    )


def _credential():
    return SimpleNamespace(
        id="cred-1",
        department_id="dept-1",
        provider="google",
        is_active=True,
    )


def _provider_success(channel_id: str):
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    return {
        "channel_id": channel_id,
        "resource_id": "opaque-google-resource",
        "resource_uri": "https://google.test/files/watched-file",
        "expiration": str(int(expires.timestamp() * 1000)),
    }


@pytest.mark.parametrize("field", ["notification_url", "resource_id"])
def test_initial_google_webhook_rejects_blank_identity_fields(field):
    from src.api.google_routes import GoogleWebhookSubscriptionRequest

    values = {
        "notification_url": "https://example.test/hooks/google",
        "resource_id": "watched-file",
    }
    values[field] = "   "

    with pytest.raises(ValueError):
        GoogleWebhookSubscriptionRequest(**values)


def _db(existing=None, commit_side_effect=None):
    from src.db.models import CloudOAuthCredentials

    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value.first.return_value = existing
    credential_query = MagicMock()
    credential_query.filter.return_value.first.return_value = _credential()
    db.query.side_effect = lambda model: (
        credential_query if model is CloudOAuthCredentials else query
    )
    if commit_side_effect is not None:
        db.commit.side_effect = commit_side_effect
    return db


@pytest.mark.asyncio
async def test_initial_google_accepted_timeout_persists_indeterminate_intent(
    monkeypatch,
):
    from src.api import google_routes

    db = _db()
    observed = {}

    async def accepted_then_timeout(**kwargs):
        row = db.add.call_args.args[0]
        observed.update(
            channel_id=kwargs["channel_id"],
            status=row.renewal_status,
            active=row.is_active,
            commits=db.commit.call_count,
        )
        raise google_routes.IndeterminateProviderOutcome()

    integration = SimpleNamespace(
        create_webhook=AsyncMock(side_effect=accepted_then_timeout), close=AsyncMock()
    )
    monkeypatch.setattr(
        google_routes, "get_google_credential", AsyncMock(return_value=_credential())
    )
    monkeypatch.setattr(
        google_routes, "get_google_integration", AsyncMock(return_value=integration)
    )

    response = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )

    row = db.add.call_args.args[0]
    assert observed == {
        "channel_id": row.pending_renewal_channel_id,
        "status": "requesting",
        "active": False,
        "commits": 1,
    }
    assert row.renewal_status == "indeterminate"
    assert row.renewal_result["retry_safe"] is False
    assert response == {
        "success": False,
        "subscription_id": row.id,
        "status": "manual_required",
        "error_code": "webhook_provider_outcome_indeterminate",
        "retry_safe": False,
    }
    assert db.commit.call_count == 2
    db.rollback.assert_not_called()
    integration.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_initial_google_created_replay_skips_expired_token_and_all_side_effects(
    monkeypatch,
):
    from src.api import google_routes
    from src.db.models import CloudOAuthCredentials, CloudWebhookSubscription

    expires = datetime.now(timezone.utc) + timedelta(days=7)
    credential = SimpleNamespace(
        id="cred-1",
        department_id="dept-1",
        provider="google",
        is_active=True,
        token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        refresh_token="encrypted-refresh",
        access_token="encrypted-access",
    )
    existing = SimpleNamespace(
        id="intent-row",
        renewal_status="created",
        subscription_id="stable-channel",
        provider_channel_resource_id="stable-resource",
        expiration_time=expires,
        is_active=True,
    )
    db = MagicMock()

    def query(model):
        result = MagicMock()
        if model is CloudOAuthCredentials:
            result.filter.return_value.first.return_value = credential
        elif model is CloudWebhookSubscription:
            result.filter.return_value.first.return_value = existing
        else:  # pragma: no cover - makes unexpected lookup obvious
            raise AssertionError(f"unexpected query model: {model}")
        return result

    db.query.side_effect = query
    token_manager = SimpleNamespace(
        is_token_expired=MagicMock(return_value=True),
        decrypt_token=MagicMock(return_value="refresh-token"),
        refresh_google_token=AsyncMock(
            return_value=(
                "new-access",
                "new-refresh",
                datetime.now(timezone.utc) + timedelta(hours=1),
            )
        ),
        encrypt_token=MagicMock(side_effect=lambda value: f"encrypted-{value}"),
    )
    token_factory = MagicMock(return_value=token_manager)
    integration_factory = AsyncMock()
    monkeypatch.setattr(google_routes, "get_token_manager", token_factory)
    monkeypatch.setattr(google_routes, "get_google_integration", integration_factory)

    response = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )

    assert response == {
        "success": True,
        "subscription_id": "intent-row",
        "channel_id": "stable-channel",
        "resource_id": "stable-resource",
        "expiration_time": expires.isoformat(),
        "replayed": True,
    }
    token_factory.assert_not_called()
    token_manager.is_token_expired.assert_not_called()
    token_manager.decrypt_token.assert_not_called()
    token_manager.refresh_google_token.assert_not_awaited()
    token_manager.encrypt_token.assert_not_called()
    integration_factory.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_initial_google_new_identity_uses_real_expired_credential_path(
    monkeypatch,
):
    from src.api import google_routes
    from src.db.models import CloudOAuthCredentials, CloudWebhookSubscription

    credential = SimpleNamespace(
        id="cred-1",
        department_id="dept-1",
        provider="google",
        is_active=True,
        token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        refresh_token="encrypted-refresh",
        access_token="encrypted-access",
    )
    db = MagicMock()

    def query(model):
        result = MagicMock()
        if model is CloudOAuthCredentials:
            result.filter.return_value.first.return_value = credential
        elif model is CloudWebhookSubscription:
            result.filter.return_value.first.return_value = None
        else:  # pragma: no cover - makes unexpected lookup obvious
            raise AssertionError(f"unexpected query model: {model}")
        return result

    db.query.side_effect = query
    token_manager = SimpleNamespace(
        is_token_expired=MagicMock(return_value=True),
        decrypt_token=MagicMock(return_value="refresh-token"),
        refresh_google_token=AsyncMock(
            return_value=(
                "new-access",
                "new-refresh",
                datetime.now(timezone.utc) + timedelta(hours=1),
            )
        ),
        encrypt_token=MagicMock(side_effect=lambda value: f"encrypted-{value}"),
    )
    integration = SimpleNamespace(
        create_webhook=AsyncMock(
            side_effect=lambda **kwargs: _provider_success(kwargs["channel_id"])
        ),
        close=AsyncMock(),
    )
    token_factory = MagicMock(return_value=token_manager)
    integration_factory = AsyncMock(return_value=integration)
    monkeypatch.setattr(google_routes, "get_token_manager", token_factory)
    monkeypatch.setattr(google_routes, "get_google_integration", integration_factory)

    response = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )

    assert response["success"] is True
    token_factory.assert_called_once_with()
    token_manager.is_token_expired.assert_called_once()
    token_manager.decrypt_token.assert_called_once_with("encrypted-refresh")
    token_manager.refresh_google_token.assert_awaited_once_with("refresh-token")
    assert token_manager.encrypt_token.call_count == 2
    integration_factory.assert_awaited_once_with(credential)
    integration.create_webhook.assert_awaited_once()
    assert db.commit.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("durable_status", ["requesting", "indeterminate"])
async def test_initial_google_repeat_returns_same_manual_response_without_http(
    monkeypatch, durable_status
):
    from src.api import google_routes

    existing = SimpleNamespace(
        id="intent-row",
        renewal_status=durable_status,
        pending_renewal_channel_id="stable-channel",
    )
    db = _db(existing)
    integration_factory = AsyncMock()
    credential_factory = AsyncMock(return_value=_credential())
    monkeypatch.setattr(google_routes, "get_google_credential", credential_factory)
    monkeypatch.setattr(google_routes, "get_google_integration", integration_factory)

    response = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )

    assert response == {
        "success": False,
        "subscription_id": "intent-row",
        "status": "manual_required",
        "error_code": "webhook_provider_outcome_indeterminate",
        "retry_safe": False,
    }
    credential_factory.assert_not_awaited()
    integration_factory.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_initial_google_precommit_failure_never_posts(monkeypatch):
    from src.api import google_routes

    db = _db(commit_side_effect=RuntimeError("database unavailable"))
    integration_factory = AsyncMock()
    monkeypatch.setattr(
        google_routes, "get_google_credential", AsyncMock(return_value=_credential())
    )
    monkeypatch.setattr(google_routes, "get_google_integration", integration_factory)

    with pytest.raises(HTTPException) as exc:
        await google_routes.create_google_webhook_subscription(
            _request(), SimpleNamespace(department_id="dept-1"), db
        )

    assert exc.value.status_code == 503
    integration_factory.assert_not_awaited()
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_initial_google_concurrent_intent_returns_winner_without_post(
    monkeypatch,
):
    from src.api import google_routes

    winner = SimpleNamespace(id="winner-row", renewal_status="requesting")
    db = _db(commit_side_effect=IntegrityError("insert", {}, RuntimeError("duplicate")))
    db.query.return_value.filter.return_value.first.side_effect = [None, None, winner]
    integration_factory = AsyncMock()
    monkeypatch.setattr(
        google_routes, "get_google_credential", AsyncMock(return_value=_credential())
    )
    monkeypatch.setattr(google_routes, "get_google_integration", integration_factory)

    response = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )

    assert response["subscription_id"] == "winner-row"
    assert response["status"] == "manual_required"
    integration_factory.assert_not_awaited()
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_initial_google_success_commits_intent_before_post_and_finalizes(
    monkeypatch,
):
    from src.api import google_routes

    db = _db()

    async def succeed(**kwargs):
        row = db.add.call_args.args[0]
        assert db.commit.call_count == 1
        assert row.renewal_status == "requesting"
        assert row.is_active is False
        assert kwargs["channel_id"] == row.pending_renewal_channel_id
        return _provider_success(kwargs["channel_id"])

    integration = SimpleNamespace(
        create_webhook=AsyncMock(side_effect=succeed), close=AsyncMock()
    )
    monkeypatch.setattr(
        google_routes, "get_google_credential", AsyncMock(return_value=_credential())
    )
    monkeypatch.setattr(
        google_routes, "get_google_integration", AsyncMock(return_value=integration)
    )

    response = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )

    row = db.add.call_args.args[0]
    assert response["success"] is True
    assert response["subscription_id"] == row.id
    assert (
        row.subscription_id
        == integration.create_webhook.await_args.kwargs["channel_id"]
    )
    assert row.provider_channel_resource_id == "opaque-google-resource"
    assert row.is_active is True
    assert row.renewal_status == "created"
    assert row.pending_renewal_channel_id is None
    assert row.pending_renewal_started_at is None
    assert db.commit.call_count == 2


@pytest.mark.asyncio
async def test_initial_google_success_response_lost_replays_committed_identity(
    monkeypatch,
):
    from src.api import google_routes

    db = _db()
    integration = SimpleNamespace(create_webhook=AsyncMock(), close=AsyncMock())

    async def succeed(**kwargs):
        return _provider_success(kwargs["channel_id"])

    integration.create_webhook.side_effect = succeed
    factory = AsyncMock(return_value=integration)
    monkeypatch.setattr(
        google_routes, "get_google_credential", AsyncMock(return_value=_credential())
    )
    monkeypatch.setattr(google_routes, "get_google_integration", factory)

    first = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )
    committed = db.add.call_args.args[0]
    db.query.return_value.filter.return_value.first.return_value = committed
    second = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )

    assert first == {
        "success": True,
        "subscription_id": committed.id,
        "channel_id": committed.subscription_id,
        "resource_id": committed.provider_channel_resource_id,
        "expiration_time": committed.expiration_time.isoformat(),
        "replayed": False,
    }
    assert second == {**first, "replayed": True}
    assert integration.create_webhook.await_count == 1
    assert factory.await_count == 1
    assert db.add.call_count == 1
    assert db.commit.call_count == 2


@pytest.mark.asyncio
async def test_initial_google_concurrent_created_winner_replays_success_without_post(
    monkeypatch,
):
    from src.api import google_routes

    expires = datetime.now(timezone.utc) + timedelta(days=7)
    winner = SimpleNamespace(
        id="winner-row",
        renewal_status="created",
        subscription_id="winner-channel",
        provider_channel_resource_id="winner-resource",
        expiration_time=expires,
        is_active=True,
    )
    db = _db(commit_side_effect=IntegrityError("insert", {}, RuntimeError("duplicate")))
    db.query.return_value.filter.return_value.first.side_effect = [None, None, winner]
    integration_factory = AsyncMock()
    monkeypatch.setattr(
        google_routes, "get_google_credential", AsyncMock(return_value=_credential())
    )
    monkeypatch.setattr(google_routes, "get_google_integration", integration_factory)

    response = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )

    assert response == {
        "success": True,
        "subscription_id": "winner-row",
        "channel_id": "winner-channel",
        "resource_id": "winner-resource",
        "expiration_time": expires.isoformat(),
        "replayed": True,
    }
    integration_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_google_active_failed_identity_fails_closed(monkeypatch):
    from src.api import google_routes

    stale = SimpleNamespace(
        id="failed-row",
        renewal_status="failed",
        is_active=True,
        pending_renewal_channel_id=None,
    )
    db = _db()
    db.query.return_value.filter.return_value.first.side_effect = [None, stale]
    integration_factory = AsyncMock()
    credential_factory = AsyncMock(return_value=_credential())
    monkeypatch.setattr(google_routes, "get_google_credential", credential_factory)
    monkeypatch.setattr(google_routes, "get_google_integration", integration_factory)

    response = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )

    assert response["subscription_id"] == "failed-row"
    assert response["status"] == "manual_required"
    credential_factory.assert_not_awaited()
    integration_factory.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_initial_google_revoked_identity_allows_explicit_new_creation(
    monkeypatch,
):
    from src.api import google_routes

    db = _db()
    db.query.return_value.filter.return_value.first.side_effect = [None, None]
    integration = SimpleNamespace(
        create_webhook=AsyncMock(
            side_effect=lambda **kwargs: _provider_success(kwargs["channel_id"])
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        google_routes, "get_google_credential", AsyncMock(return_value=_credential())
    )
    monkeypatch.setattr(
        google_routes, "get_google_integration", AsyncMock(return_value=integration)
    )

    response = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )

    assert response["success"] is True
    assert response["replayed"] is False
    integration.create_webhook.assert_awaited_once()
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_initial_google_post_provider_commit_failure_is_manual_and_not_reposted(
    monkeypatch,
):
    from src.api import google_routes

    db = _db(commit_side_effect=[None, RuntimeError("commit lost")])

    async def succeed(**kwargs):
        return _provider_success(kwargs["channel_id"])

    integration = SimpleNamespace(
        create_webhook=AsyncMock(side_effect=succeed), close=AsyncMock()
    )
    factory = AsyncMock(return_value=integration)
    monkeypatch.setattr(
        google_routes, "get_google_credential", AsyncMock(return_value=_credential())
    )
    monkeypatch.setattr(google_routes, "get_google_integration", factory)

    first = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )
    intent = db.add.call_args.args[0]
    assert first["status"] == "manual_required"
    assert first["subscription_id"] == intent.id
    assert integration.create_webhook.await_count == 1
    assert db.rollback.call_count == 1

    durable = SimpleNamespace(
        id=intent.id,
        renewal_status="requesting",
        pending_renewal_channel_id=integration.create_webhook.await_args.kwargs[
            "channel_id"
        ],
    )
    db.query.return_value.filter.return_value.first.return_value = durable
    second = await google_routes.create_google_webhook_subscription(
        _request(), SimpleNamespace(department_id="dept-1"), db
    )
    assert second == first
    assert integration.create_webhook.await_count == 1


def test_initial_google_webhook_requires_auth_before_provider_work(monkeypatch):
    from src.api import google_routes

    provider_factory = AsyncMock()
    monkeypatch.setattr(google_routes, "get_google_integration", provider_factory)
    app = FastAPI()
    app.include_router(google_routes.router)
    app.dependency_overrides[google_routes.get_db_dependency] = lambda: MagicMock()

    def deny():
        raise HTTPException(status_code=401, detail="unauthorized")

    app.dependency_overrides[google_routes.get_current_api_key] = deny
    response = TestClient(app).post(
        "/google/webhooks",
        json={
            "notification_url": "https://example.test/hooks/google",
            "resource_id": "watched-file",
        },
    )

    assert response.status_code == 401
    provider_factory.assert_not_awaited()


def test_initial_google_intent_has_partial_unique_model_and_migration_fence():
    from pathlib import Path

    from src.db.models import CloudWebhookSubscription

    index = next(
        item
        for item in CloudWebhookSubscription.__table__.indexes
        if item.name == "uq_cloud_webhook_initial_intent"
    )
    migration = Path(
        "alembic/versions/2026_08_22_upload_external_effect_fence.py"
    ).read_text()

    assert index.unique is True
    assert [column.name for column in index.columns] == [
        "department_id",
        "credential_id",
        "provider",
        "provider_resource_id",
        "notification_url",
    ]
    predicate = str(index.dialect_options["postgresql"]["where"])
    assert "provider = 'google'" in predicate
    assert (
        "renewal_status IN ('requesting', 'indeterminate', 'created', 'renewed')"
        in predicate
    )
    assert "uq_cloud_webhook_initial_intent" in migration
    assert "'requesting', 'indeterminate', 'created', 'renewed'" in migration


def test_initial_google_index_rejects_direct_duplicate_created_identity():
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE cloud_webhook_subscriptions ("
                "id TEXT PRIMARY KEY, department_id TEXT NOT NULL, "
                "credential_id TEXT NOT NULL, provider TEXT NOT NULL, "
                "provider_resource_id TEXT, notification_url TEXT, "
                "renewal_status TEXT)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX uq_cloud_webhook_initial_intent ON "
                "cloud_webhook_subscriptions "
                "(department_id, credential_id, provider, provider_resource_id, notification_url) "
                "WHERE provider = 'google' AND renewal_status IN "
                "('requesting', 'indeterminate', 'created', 'renewed')"
            )
        )
        values = {
            "department_id": "dept-1",
            "credential_id": "cred-1",
            "provider": "google",
            "provider_resource_id": "watched-file",
            "notification_url": "https://example.test/hooks/google",
            "renewal_status": "created",
        }
        insert = text(
            "INSERT INTO cloud_webhook_subscriptions VALUES "
            "(:id, :department_id, :credential_id, :provider, "
            ":provider_resource_id, :notification_url, :renewal_status)"
        )
        connection.execute(insert, {**values, "id": "created-1"})
        with pytest.raises(IntegrityError):
            connection.execute(insert, {**values, "id": "created-2"})
