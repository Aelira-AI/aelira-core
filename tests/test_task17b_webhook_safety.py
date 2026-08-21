"""Google webhook renewal safety regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _subscription(**overrides):
    values = {
        "id": "sub-row",
        "department_id": "dept-1",
        "credential_id": "cred-1",
        "provider": "google",
        "subscription_id": "old-channel",
        "provider_resource_id": "watched-file",
        "provider_channel_resource_id": "old-resource",
        "resource_uri": "https://google.test/old-resource",
        "notification_url": "https://example.test/hooks/google",
        "expiration_time": datetime.now(timezone.utc),
        "is_active": True,
        "last_renewed_at": None,
        "renewal_status": None,
        "renewal_result": None,
        "pending_renewal_channel_id": None,
        "pending_renewal_started_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _job():
    return SimpleNamespace(
        id="job-1",
        payload={"subscription_id": "sub-row"},
        department_id="dept-1",
        credential_id="cred-1",
        provider="google",
    )


def _db(subscription):
    credential = SimpleNamespace(
        id="cred-1", department_id="dept-1", provider="google", is_active=True
    )
    db = MagicMock()
    db.get.side_effect = lambda model, _key: (
        subscription if model.__name__ == "CloudWebhookSubscription" else credential
    )
    return db


@pytest.mark.asyncio
async def test_google_accepted_timeout_is_indeterminate_without_identity_overwrite(
    monkeypatch,
):
    from src.jobs import webhook_refresh_job
    from src.jobs.contracts import FailureKind

    subscription = _subscription()
    db = _db(subscription)
    observed_pending = []

    async def accepted_then_timeout(**kwargs):
        observed_pending.append(
            (
                subscription.pending_renewal_channel_id,
                subscription.pending_renewal_started_at,
                subscription.renewal_status,
                db.commit.call_count,
                kwargs["channel_id"],
            )
        )
        raise webhook_refresh_job.IndeterminateProviderOutcome(
            "webhook_provider_outcome_indeterminate"
        )

    integration = SimpleNamespace(
        create_webhook=AsyncMock(side_effect=accepted_then_timeout), close=AsyncMock()
    )
    monkeypatch.setattr(
        webhook_refresh_job,
        "GoogleDriveIntegration",
        MagicMock(return_value=integration),
    )
    result = await webhook_refresh_job.handle_webhook_refresh_job(
        _job(),
        db,
        SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token")),
    )

    assert result.kind is FailureKind.INDETERMINATE
    assert result.details["retry_safe"] is False
    assert integration.create_webhook.await_count == 1
    assert observed_pending[0][0] == observed_pending[0][4]
    assert observed_pending[0][1] is not None
    assert observed_pending[0][2] == "requesting"
    assert observed_pending[0][3] == 2
    assert subscription.subscription_id == "old-channel"
    assert subscription.provider_channel_resource_id == "old-resource"
    assert subscription.resource_uri == "https://google.test/old-resource"
    assert subscription.renewal_status == "indeterminate"
    assert subscription.renewal_result == {
        "provider": "google",
        "status": "indeterminate",
        "code": "webhook_provider_outcome_indeterminate",
        "correlation_id": "job-1",
        "pending_channel_id": subscription.pending_renewal_channel_id,
    }


@pytest.mark.asyncio
async def test_google_indeterminate_same_job_replay_makes_no_second_request(
    monkeypatch,
):
    from src.jobs import webhook_refresh_job
    from src.jobs.contracts import FailureKind

    subscription = _subscription(
        renewal_status="indeterminate",
        renewal_result={"correlation_id": "job-1"},
        pending_renewal_channel_id="stable-channel",
        pending_renewal_started_at=datetime.now(timezone.utc),
    )
    integration_constructor = MagicMock()
    monkeypatch.setattr(
        webhook_refresh_job, "GoogleDriveIntegration", integration_constructor
    )

    result = await webhook_refresh_job.handle_webhook_refresh_job(
        _job(),
        _db(subscription),
        SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token")),
    )

    assert result.kind is FailureKind.INDETERMINATE
    assert result.details["retry_safe"] is False
    integration_constructor.assert_not_called()


@pytest.mark.asyncio
async def test_google_requesting_checkpoint_replay_never_blindly_posts(monkeypatch):
    from src.jobs import webhook_refresh_job
    from src.jobs.contracts import FailureKind

    subscription = _subscription(
        renewal_status="requesting",
        renewal_result={"correlation_id": "job-1"},
        pending_renewal_channel_id="stable-channel",
        pending_renewal_started_at=datetime.now(timezone.utc),
    )
    integration_constructor = MagicMock()
    monkeypatch.setattr(
        webhook_refresh_job, "GoogleDriveIntegration", integration_constructor
    )

    result = await webhook_refresh_job.handle_webhook_refresh_job(
        _job(),
        _db(subscription),
        SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token")),
    )

    assert result.kind is FailureKind.INDETERMINATE
    assert subscription.renewal_status == "indeterminate"
    integration_constructor.assert_not_called()


@pytest.mark.asyncio
async def test_google_pre_send_transient_reuses_pending_channel_on_retry(monkeypatch):
    from src.jobs import webhook_refresh_job
    from src.jobs.contracts import FailureKind

    subscription = _subscription()
    db = _db(subscription)
    integration = SimpleNamespace(
        create_webhook=AsyncMock(
            side_effect=webhook_refresh_job.GoogleWebhookRequestError(
                "webhook_provider_unavailable",
                request_started=False,
                retryable=True,
            )
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        webhook_refresh_job,
        "GoogleDriveIntegration",
        MagicMock(return_value=integration),
    )
    token_manager = SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token"))

    first = await webhook_refresh_job.handle_webhook_refresh_job(
        _job(), db, token_manager
    )
    pending_id = subscription.pending_renewal_channel_id
    second = await webhook_refresh_job.handle_webhook_refresh_job(
        _job(), db, token_manager
    )

    assert first.kind is FailureKind.RETRYABLE
    assert second.kind is FailureKind.RETRYABLE
    assert pending_id
    assert subscription.pending_renewal_channel_id == pending_id
    assert [
        call.kwargs["channel_id"] for call in integration.create_webhook.await_args_list
    ] == [pending_id, pending_id]


@pytest.mark.asyncio
async def test_google_success_clears_pending_and_same_job_replay_is_idempotent(
    monkeypatch,
):
    from src.jobs import webhook_refresh_job

    subscription = _subscription()
    db = _db(subscription)
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    integration = SimpleNamespace(
        create_webhook=AsyncMock(
            return_value={
                "channel_id": "ignored-provider-channel",
                "resource_id": "new-resource",
                "resource_uri": "https://google.test/new-resource",
                "expiration": str(int(expires.timestamp() * 1000)),
            }
        ),
        close=AsyncMock(),
    )
    constructor = MagicMock(return_value=integration)
    monkeypatch.setattr(webhook_refresh_job, "GoogleDriveIntegration", constructor)
    token_manager = SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token"))

    first = await webhook_refresh_job.handle_webhook_refresh_job(
        _job(), db, token_manager
    )
    active_channel = subscription.subscription_id
    second = await webhook_refresh_job.handle_webhook_refresh_job(
        _job(), db, token_manager
    )

    assert first["success"] is True
    assert second["success"] is True
    assert integration.create_webhook.await_count == 1
    assert active_channel == integration.create_webhook.await_args.kwargs["channel_id"]
    assert subscription.pending_renewal_channel_id is None
    assert subscription.pending_renewal_started_at is None
    assert subscription.renewal_status == "renewed"
    assert subscription.renewal_result["correlation_id"] == "job-1"


@pytest.mark.asyncio
async def test_google_create_webhook_marks_post_timeout_as_indeterminate(monkeypatch):
    from src.integrations.google_workspace.google_drive import (
        GoogleDriveIntegration,
        IndeterminateProviderOutcome,
    )

    client = SimpleNamespace(
        post=AsyncMock(side_effect=httpx.ReadTimeout("response lost"))
    )
    integration = GoogleDriveIntegration("cred-1", "token")
    monkeypatch.setattr(integration, "_get_client", AsyncMock(return_value=client))

    with pytest.raises(IndeterminateProviderOutcome):
        await integration.create_webhook(
            notification_url="https://example.test/hooks/google",
            resource_id="watched-file",
            channel_id="stable-channel",
        )
    client.post.assert_awaited_once()
