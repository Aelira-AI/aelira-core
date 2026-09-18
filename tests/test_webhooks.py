"""Current webhook contracts using PostgreSQL and no live provider traffic.

Management placeholders are asserted as such. Callback jobs remain pending;
provider identity validation and worker execution are separate evidence.
"""

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

import integration_route_fixtures as fixtures
from src.api import webhook_routes as routes
from src.db.models import CloudJobQueue, CloudWebhookSubscription

pytestmark = pytest.mark.integration
integration_route = fixtures.integration_route


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_subscribe_returns_placeholder_without_persistence(integration_route, provider):
    case = integration_route
    before = case.db.query(CloudWebhookSubscription).count()
    response = case.client.post(
        f"/webhooks/{provider}/subscribe",
        params={"notification_url": "https://example.test/notifications"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "not_implemented",
        "message": f"Use /{provider}/connect flow to set up subscriptions",
    }
    case.db.expire_all()
    assert case.db.query(CloudWebhookSubscription).count() == before


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_subscribe_requires_notification_url_query(integration_route, provider):
    response = integration_route.client.post(f"/webhooks/{provider}/subscribe")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "notification_url"]


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_delete_subscription_persists_local_deactivation_and_retry(
    integration_route, provider
):
    """Local mutation does not claim provider revocation."""
    case = integration_route
    row = fixtures.subscription(case, fixtures.credential(case, provider))
    other = fixtures.subscription(case, fixtures.credential(case, provider, other=True))
    sibling = fixtures.subscription(
        case,
        fixtures.credential(case, "microsoft" if provider == "google" else "google"),
    )
    case.db.commit()
    for _ in range(2):
        response = case.client.delete(f"/webhooks/subscriptions/{row.id}")
        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "message": "Subscription deactivated",
        }
    case.db.expire_all()
    assert case.db.get(CloudWebhookSubscription, row.id).is_active is False
    assert case.db.get(CloudWebhookSubscription, other.id).is_active is True
    assert case.db.get(CloudWebhookSubscription, sibling.id).is_active is True


@pytest.mark.parametrize("target", ["missing", "other_department"])
def test_delete_subscription_requires_department_row(integration_route, target):
    case = integration_route
    row = fixtures.subscription(case, fixtures.credential(case, "google", other=True))
    case.db.commit()
    row_id = row.id if target == "other_department" else str(uuid4())
    response = case.client.delete(f"/webhooks/subscriptions/{row_id}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Subscription not found"}
    case.db.expire_all()
    assert case.db.get(CloudWebhookSubscription, row.id).is_active is True


def test_delete_subscription_commit_failure_preserves_active_row(integration_route):
    case = integration_route
    row = fixtures.subscription(case, fixtures.credential(case, "google"))
    case.db.commit()
    with patch.object(
        case.db, "commit", side_effect=SQLAlchemyError("fixture-only commit failure")
    ):
        response = case.client.delete(f"/webhooks/subscriptions/{row.id}")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    case.db.expire_all()
    assert case.db.get(CloudWebhookSubscription, row.id).is_active is True


def test_delete_subscription_requires_identity(integration_route):
    case = integration_route
    case.application.dependency_overrides.pop(routes.get_api_key_or_mock)
    response = case.client.delete(f"/webhooks/subscriptions/{uuid4()}")
    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication required. Provide 'Authorization: Bearer ***' header or login via dashboard."
    }


def test_webhook_health_reports_global_active_counts(integration_route):
    """Health includes both fixture departments; it is not a tenant dashboard."""
    case = integration_route
    expected = {
        provider: case.db.query(CloudWebhookSubscription)
        .filter_by(provider=provider, is_active=True)
        .count()
        for provider in ("google", "microsoft")
    }
    for provider in ("google", "microsoft"):
        fixtures.subscription(case, fixtures.credential(case, provider))
        fixtures.subscription(case, fixtures.credential(case, provider, other=True))
        fixtures.subscription(case, fixtures.credential(case, provider), active=False)
        expected[provider] += 2
    case.db.commit()
    response = case.client.get("/webhooks/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "cloud-webhooks",
        "active_subscriptions": expected,
    }


def test_webhook_health_query_failure_after_success(integration_route):
    case = integration_route
    assert case.client.get("/webhooks/health").status_code == 200
    with patch.object(
        case.db, "query", side_effect=SQLAlchemyError("fixture-only query failure")
    ):
        response = case.client.get("/webhooks/health")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert case.client.get("/webhooks/health").status_code == 200


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_microsoft_validation_echoes_plain_text_without_database(
    integration_route, method
):
    case = integration_route
    with patch.object(
        case.db,
        "query",
        side_effect=AssertionError("validation must not query subscriptions"),
    ):
        response = case.client.request(
            method,
            "/webhooks/microsoft",
            params={"validationToken": "fixture validation + token"},
        )
    assert response.status_code == 200
    assert response.text == "fixture validation + token"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


def test_microsoft_get_validation_requires_token(integration_route):
    response = integration_route.client.get("/webhooks/microsoft")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "validationToken"]


@pytest.mark.parametrize("payload", [[], {"value": "invalid"}])
def test_microsoft_rejects_invalid_notification_envelope(integration_route, payload):
    case = integration_route
    response = case.client.post("/webhooks/microsoft", json=payload)
    assert response.status_code == 400
    assert response.content == b""
    assert (
        case.db.query(CloudJobQueue).filter_by(department_id=case.department.id).count()
        == 0
    )


def callback(case, provider, row):
    if provider == "google":
        return case.client.post(
            "/webhooks/google",
            headers={
                "X-Goog-Channel-ID": row.subscription_id,
                "X-Goog-Resource-State": "update",
                "X-Goog-Resource-ID": "fixture-resource",
                "X-Goog-Message-Number": "1",
            },
        )
    return case.client.post(
        "/webhooks/microsoft",
        json={
            "value": [
                {
                    "subscriptionId": row.subscription_id,
                    "clientState": row.department_id,
                    "changeType": "updated",
                    "resource": "drive/root/fixture-item",
                }
            ]
        },
    )


@pytest.mark.parametrize("provider,status", [("google", 200), ("microsoft", 202)])
def test_supported_callback_persists_one_pending_reconciliation_job(
    integration_route, provider, status
):
    case = integration_route
    row = fixtures.subscription(case, fixtures.credential(case, provider))
    case.db.commit()
    before = datetime.now(timezone.utc)
    for _ in range(2):
        response = callback(case, provider, row)
        assert response.status_code == status
        assert response.content == b""
    case.db.expire_all()
    persisted = case.db.get(CloudWebhookSubscription, row.id)
    assert before <= persisted.last_notification_at <= datetime.now(timezone.utc)
    job = case.db.query(CloudJobQueue).filter_by(department_id=case.department.id).one()
    assert job.provider == provider and job.credential_id == row.credential_id
    assert job.job_type == "sync" and job.status == "pending" and job.priority == 3
    expected = {
        "credential_id": row.credential_id,
        "provider": provider,
        "subscription_id": row.id,
    }
    expected.update(
        {
            "resource_id": "fixture-resource",
            "resource_state": "update",
            "message_number": "1",
        }
        if provider == "google"
        else {"resource": "drive/root/fixture-item", "change_type": "updated"}
    )
    assert job.payload == expected


@pytest.mark.parametrize("provider,status", [("google", 500), ("microsoft", 503)])
def test_callback_enqueue_failure_rolls_back_notification_time(
    integration_route, provider, status
):
    """Only enqueue failure is substituted; these are supported local callbacks."""
    case = integration_route
    row = fixtures.subscription(case, fixtures.credential(case, provider))
    case.db.commit()
    with patch.object(
        routes,
        "enqueue_cloud_job",
        side_effect=SQLAlchemyError("fixture-only enqueue failure"),
    ):
        response = callback(case, provider, row)
    assert response.status_code == status
    assert response.content == (
        b"Internal Server Error" if provider == "google" else b""
    )
    case.db.expire_all()
    assert case.db.get(CloudWebhookSubscription, row.id).last_notification_at is None
    assert (
        case.db.query(CloudJobQueue).filter_by(department_id=case.department.id).count()
        == 0
    )
