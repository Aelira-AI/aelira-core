"""Shared integration HTTP contracts; no live providers or workers."""

from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

import integration_route_fixtures as fixtures
from src.api import integration_routes as routes
from src.db.models import CloudJobQueue, CloudOAuthCredentials, CloudWebhookSubscription

pytestmark = pytest.mark.integration
integration_route = fixtures.integration_route


def empty_status(provider):
    return {
        "connected": False,
        "email": None,
        "fullname" if provider in {"moodle", "brightspace"} else "name": None,
        "last_sync_at": None,
    }


def test_status_without_connections_returns_all_six_provider_contracts(
    integration_route,
):
    response = integration_route.client.get("/integrations/status")
    assert response.status_code == 200
    assert response.json() == {
        provider: empty_status(provider) for provider in fixtures.PROVIDERS
    }


@pytest.mark.parametrize("provider", fixtures.PROVIDERS)
def test_status_serializes_persisted_provider_profile(integration_route, provider):
    case = integration_route
    row = fixtures.credential(case, provider)
    case.db.commit()
    response = case.client.get("/integrations/status")
    assert response.status_code == 200
    expected = {name: empty_status(name) for name in fixtures.PROVIDERS}
    expected[provider] = {
        "connected": True,
        "email": row.provider_email,
        (
            "fullname" if provider in {"moodle", "brightspace"} else "name"
        ): row.provider_name,
        "last_sync_at": fixtures.NOW.isoformat(),
    }
    assert response.json() == expected


def test_status_ignores_inactive_and_other_department_credentials(integration_route):
    case = integration_route
    for provider in fixtures.PROVIDERS:
        fixtures.credential(case, provider, active=False)
        fixtures.credential(case, provider, other=True)
    case.db.commit()
    response = case.client.get("/integrations/status")
    assert response.status_code == 200
    assert response.json() == {
        provider: empty_status(provider) for provider in fixtures.PROVIDERS
    }


@pytest.mark.parametrize(
    "path,provider",
    [
        ("/health", None),
        ("/health/google", "google"),
        ("/health/microsoft", "microsoft"),
    ],
)
def test_health_is_static_liveness_not_provider_probe(
    integration_route, path, provider
):
    case = integration_route
    case.application.dependency_overrides.clear()
    before = datetime.utcnow()
    with patch.object(
        case.db,
        "query",
        side_effect=AssertionError("liveness must not query connections"),
    ):
        response = case.client.get(f"/integrations{path}")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"status", "provider", "timestamp"}
    assert data["status"] == "healthy"
    assert data["provider"] == provider
    assert before <= datetime.fromisoformat(data["timestamp"]) <= datetime.utcnow()


def add_job(case, provider, status, *, other=False):
    state = {}
    if status in {"completed", "failed"}:
        state["completed_at"] = fixtures.NOW
    if status == "processing":
        state.update(
            claim_token=str(uuid4()),
            worker_id="fixture-worker",
            claimed_at=fixtures.NOW,
            heartbeat_at=fixtures.NOW,
            lease_expires_at=fixtures.NOW + timedelta(minutes=5),
        )
    case.db.add(
        CloudJobQueue(
            department_id=case.other_department.id if other else case.department.id,
            provider=provider,
            job_type="sync",
            status=status,
            payload={},
            **state,
        )
    )
    case.db.flush()


def test_metrics_empty_department_preserves_unknown_last_sync(integration_route):
    response = integration_route.client.get("/integrations/metrics")
    assert response.status_code == 200
    assert response.json() == {
        "total_jobs": 0,
        "completed_jobs": 0,
        "failed_jobs": 0,
        "pending_jobs": 0,
        "total_files_synced": 0,
        "last_sync_at": None,
    }


def test_metrics_counts_department_jobs_and_latest_active_sync(integration_route):
    case = integration_route
    fixtures.credential(case, "google", synced_at=fixtures.NOW - timedelta(days=1))
    fixtures.credential(case, "microsoft")
    fixtures.credential(
        case, "canvas", active=False, synced_at=fixtures.NOW + timedelta(days=2)
    )
    fixtures.credential(
        case, "google", other=True, synced_at=fixtures.NOW + timedelta(days=3)
    )
    for state in ("completed", "failed", "pending", "processing"):
        add_job(case, "google", state)
    add_job(case, "microsoft", "completed")
    add_job(case, "google", "completed", other=True)
    case.db.commit()
    response = case.client.get("/integrations/metrics")
    assert response.status_code == 200
    data = response.json()
    assert datetime.fromisoformat(data.pop("last_sync_at")) == fixtures.NOW
    # Current compatibility alias, not a distinct file inventory measurement.
    assert data == {
        "total_jobs": 5,
        "completed_jobs": 2,
        "failed_jobs": 1,
        "pending_jobs": 1,
        "total_files_synced": 2,
    }


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_provider_metrics_count_only_requested_provider(integration_route, provider):
    case = integration_route
    fixtures.credential(case, provider)
    for state in ("completed", "failed", "pending", "processing"):
        add_job(case, provider, state)
    add_job(case, "microsoft" if provider == "google" else "google", "completed")
    add_job(case, provider, "completed", other=True)
    case.db.commit()
    response = case.client.get(f"/integrations/metrics/{provider}")
    assert response.status_code == 200
    data = response.json()
    assert datetime.fromisoformat(data.pop("last_sync_at")) == fixtures.NOW
    assert data == {
        "total_jobs": 4,
        "completed_jobs": 1,
        "failed_jobs": 1,
        "pending_jobs": 1,
        "total_files_synced": 1,
    }


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_webhook_status_remains_placeholder_with_saved_subscription(
    integration_route, provider
):
    case = integration_route
    sub = fixtures.subscription(case, fixtures.credential(case, provider))
    case.db.commit()
    response = case.client.get(f"/integrations/webhooks/{provider}")
    assert response.status_code == 200
    assert response.json() == {
        "provider": provider,
        "subscriptions": [],
        "message": f"Webhook subscription management for {provider} coming soon",
    }
    case.db.expire_all()
    assert case.db.get(CloudWebhookSubscription, sub.id).is_active is True


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_disconnect_deactivates_only_matching_local_credential(
    integration_route, provider
):
    """Local deactivation does not claim remote revocation or subscription cleanup."""
    case = integration_route
    row = fixtures.credential(case, provider)
    other = fixtures.credential(case, provider, other=True)
    alternative = fixtures.credential(
        case, "microsoft" if provider == "google" else "google"
    )
    sub = fixtures.subscription(case, row)
    case.db.commit()
    response = case.client.delete(f"/integrations/{provider}")
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": f"{provider} integration disconnected successfully",
        "provider": provider,
    }
    case.db.expire_all()
    assert case.db.get(CloudOAuthCredentials, row.id).is_active is False
    assert case.db.get(CloudOAuthCredentials, other.id).is_active is True
    assert case.db.get(CloudOAuthCredentials, alternative.id).is_active is True
    assert case.db.get(CloudWebhookSubscription, sub.id).is_active is True
    assert case.client.get("/integrations/status").json()[provider] == empty_status(
        provider
    )
    retry = case.client.delete(f"/integrations/{provider}")
    assert retry.status_code == 404
    assert retry.json() == {"detail": f"{provider} integration not connected"}


@pytest.mark.parametrize("operation", ["metrics", "webhooks", "disconnect"])
@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_provider_operations_require_active_local_connection(
    integration_route, operation, provider
):
    case = integration_route
    fixtures.credential(case, provider, active=False)
    fixtures.credential(case, provider, other=True)
    case.db.commit()
    response = (
        case.client.delete(f"/integrations/{provider}")
        if operation == "disconnect"
        else case.client.get(f"/integrations/{operation}/{provider}")
    )
    assert response.status_code == 404
    assert response.json() == {"detail": f"{provider} integration not connected"}


@pytest.mark.parametrize("operation", ["metrics", "webhooks", "disconnect"])
def test_provider_operations_reject_unsupported_provider(integration_route, operation):
    case = integration_route
    response = (
        case.client.delete("/integrations/unsupported")
        if operation == "disconnect"
        else case.client.get(f"/integrations/{operation}/unsupported")
    )
    assert response.status_code == 400
    noun = "provider" if operation == "webhooks" else "integration"
    assert response.json() == {
        "detail": f"Invalid {noun}: unsupported. Must be 'google' or 'microsoft'"
    }


@pytest.mark.parametrize(
    "path", ["status", "metrics", "metrics/google", "webhooks/google"]
)
def test_read_query_failure_after_success_is_unsuccessful(integration_route, path):
    case = integration_route
    fixtures.credential(case, "google")
    case.db.commit()
    assert case.client.get(f"/integrations/{path}").status_code == 200
    with patch.object(
        case.db, "query", side_effect=SQLAlchemyError("fixture-only database failure")
    ):
        response = case.client.get(f"/integrations/{path}")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert case.client.get(f"/integrations/{path}").status_code == 200


def test_disconnect_commit_failure_rolls_back_deactivation(integration_route):
    case = integration_route
    row = fixtures.credential(case, "google")
    case.db.commit()
    assert case.client.get("/integrations/status").json()["google"]["connected"] is True
    with patch.object(
        case.db, "commit", side_effect=SQLAlchemyError("fixture-only commit failure")
    ):
        response = case.client.delete("/integrations/google")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    case.db.expire_all()
    assert case.db.get(CloudOAuthCredentials, row.id).is_active is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "status"),
        ("GET", "metrics"),
        ("GET", "metrics/google"),
        ("GET", "webhooks/google"),
        ("DELETE", "google"),
    ],
)
def test_protected_integration_routes_require_identity(integration_route, method, path):
    case = integration_route
    case.application.dependency_overrides.pop(routes.get_current_api_key)
    response = case.client.request(method, f"/integrations/{path}")
    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication required. Provide API key in Authorization header or login via dashboard."
    }
    assert response.headers["www-authenticate"] == "Bearer"
