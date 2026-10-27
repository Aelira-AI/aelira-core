"""Microsoft webhook clientState is a per-subscription secret, stored only as a hash."""

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.integrations.microsoft_365.onedrive import (
    OneDriveIntegration,
    client_state_matches,
    hash_client_state,
    new_client_state,
)


def test_secrets_are_random_and_fit_graph_limits():
    values = {new_client_state() for _ in range(50)}
    assert len(values) == 50
    assert all(32 <= len(value) <= 128 for value in values)


def test_only_the_matching_secret_passes_the_stored_hash():
    secret = new_client_state()
    stored = hash_client_state(secret)
    assert stored == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in stored
    assert client_state_matches(secret, stored)
    for presented in ("", secret + "x", secret.upper(), "x" * 129, None, 7):
        assert not client_state_matches(presented, stored)
    # A subscription created before secrets has no hash and matches nothing.
    assert not client_state_matches(secret, None)
    assert not client_state_matches(secret, "")


def _integration(requests):
    def record(request):
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": "sub-1",
                "resource": "/me/drive/root",
                "expirationDateTime": "2026-10-29T00:00:00Z",
            },
        )

    integration = OneDriveIntegration(access_token="graph-token", department_id="d")
    integration._http_client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return integration


@pytest.mark.asyncio
@pytest.mark.parametrize("client_state", [None, "", "x" * 129])
async def test_create_webhook_requires_a_secret(client_state):
    requests = []
    integration = _integration(requests)
    try:
        with pytest.raises(ValueError):
            await integration.create_webhook(
                "https://api.example.test/webhooks/microsoft",
                client_state=client_state,
            )
    finally:
        await integration.close()
    assert requests == []


@pytest.mark.asyncio
async def test_create_webhook_sends_the_secret_not_the_department():
    requests = []
    integration = _integration(requests)
    secret = new_client_state()
    try:
        await integration.create_webhook(
            "https://api.example.test/webhooks/microsoft", client_state=secret
        )
    finally:
        await integration.close()
    (request,) = requests
    body = json.loads(request.content)
    assert body["clientState"] == secret != "d"  # never the department ID


@pytest.mark.asyncio
async def test_refresh_job_will_not_renew_a_subscription_without_a_secret(
    monkeypatch,
):
    from src.jobs import webhook_refresh_job
    from src.jobs.contracts import FailureKind

    subscription = SimpleNamespace(
        id="sub-row",
        department_id="dept-1",
        credential_id="cred-1",
        provider="microsoft",
        subscription_id="graph-sub",
        provider_resource_id="/me/drive/root",
        notification_url="https://api.example.test/webhooks/microsoft",
        client_state_hash=None,
        expiration_time=datetime.now(timezone.utc),
        is_active=True,
        last_renewed_at=None,
        renewal_status="created",
        renewal_result=None,
        pending_renewal_channel_id="stale-channel",
        pending_renewal_started_at=datetime.now(timezone.utc),
    )
    credential = SimpleNamespace(
        id="cred-1", department_id="dept-1", provider="microsoft", is_active=True
    )
    db = MagicMock()
    db.get.side_effect = lambda model, _key: (
        subscription if model.__name__ == "CloudWebhookSubscription" else credential
    )
    integration_class = MagicMock()
    monkeypatch.setattr(webhook_refresh_job, "OneDriveIntegration", integration_class)
    job = SimpleNamespace(
        id="job-1",
        payload={"subscription_id": "sub-row"},
        department_id="dept-1",
        credential_id="cred-1",
        provider="microsoft",
    )

    result = await webhook_refresh_job.handle_webhook_refresh_job(
        job, db, SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token"))
    )

    assert result.kind is FailureKind.DETERMINISTIC
    assert result.code == "webhook_client_state_missing"
    assert subscription.is_active is False
    assert subscription.renewal_status == "failed"
    assert subscription.pending_renewal_channel_id is None
    assert subscription.pending_renewal_started_at is None
    db.commit.assert_called_once()
    integration_class.assert_not_called()
