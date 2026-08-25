"""Regression tests for the Task17B specification blockers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def test_queue_model_preserves_migration_constraint_names():
    from src.db.models import CloudJobQueue

    constraint_names = {
        constraint.name for constraint in CloudJobQueue.__table__.constraints
    }
    assert "ck_cloud_job_queue_payload_object" in constraint_names
    dependency = next(iter(CloudJobQueue.__table__.c.depends_on_job_id.foreign_keys))
    assert dependency.constraint.name == "fk_cloud_job_queue_dependency"


def test_onedrive_constructor_rejects_credential_identifier_scope():
    from src.integrations.microsoft_365.onedrive import OneDriveIntegration

    with pytest.raises(TypeError, match="credential_id"):
        OneDriveIntegration(access_token="token", credential_id="credential-1")


@pytest.mark.asyncio
async def test_onedrive_webhook_uses_department_scope_as_client_state():
    from src.integrations.microsoft_365.onedrive import OneDriveIntegration

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.extensions)
        captured["body"] = request.content
        return httpx.Response(
            201,
            json={"id": "provider-sub", "resource": "/me/drive/root"},
            request=request,
        )

    integration = OneDriveIntegration(
        access_token="token",
        department_id="department-authority",
    )
    temporary_directory = Path(integration._temp_dir)
    assert integration.credential_id is None
    integration._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await integration.create_webhook("https://example.test/hooks/microsoft")
    finally:
        await integration.close()

    assert b'"clientState":"department-authority"' in captured["body"]
    assert b"credential" not in captured["body"]
    assert not temporary_directory.exists()


@pytest.mark.asyncio
async def test_onedrive_close_failure_is_sanitized_and_still_cleans_temp(caplog):
    from src.integrations.microsoft_365.onedrive import OneDriveIntegration

    sentinel = "/private/provider/token=secret"
    integration = OneDriveIntegration("token", "department-authority")
    temporary_directory = Path(integration._temp_dir)
    integration._http_client = SimpleNamespace(
        is_closed=False,
        aclose=AsyncMock(side_effect=RuntimeError(sentinel)),
    )

    with caplog.at_level(logging.WARNING):
        await integration.close()

    assert integration._http_client is None
    assert not temporary_directory.exists()
    assert sentinel not in caplog.text
    assert "token=secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("client_state", [None, "", "another-department"])
async def test_microsoft_webhook_requires_exact_department_client_state(
    monkeypatch, client_state
):
    from src.api import webhook_routes

    subscription = SimpleNamespace(
        id="subscription-row",
        subscription_id="provider-subscription",
        department_id="department-authority",
        credential_id="credential-1",
        last_notification_at=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = subscription

    @contextmanager
    def scoped_db():
        yield db

    enqueue = MagicMock()
    monkeypatch.setattr(webhook_routes, "get_db", scoped_db)
    monkeypatch.setattr(webhook_routes, "enqueue_cloud_job", enqueue)
    request = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "value": [
                    {
                        "subscriptionId": "provider-subscription",
                        "clientState": client_state,
                        "changeType": "updated",
                        "resource": "/me/drive/root",
                    }
                ]
            }
        )
    )

    response = await webhook_routes.microsoft_graph_webhook(
        request,
        validationToken=None,
    )

    assert response.status_code == 202
    assert subscription.last_notification_at is None
    enqueue.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_microsoft_webhook_filters_department_and_never_logs_provider_values(
    monkeypatch, caplog
):
    from src.api import webhook_routes

    subscription = SimpleNamespace(
        id="subscription-row",
        subscription_id="provider-subscription",
        department_id="department-authority",
        credential_id="credential-1",
        last_notification_at=None,
    )
    query = MagicMock()
    query.filter.return_value.first.return_value = subscription
    db = MagicMock()
    db.query.return_value = query

    @contextmanager
    def scoped_db():
        yield db

    monkeypatch.setattr(webhook_routes, "get_db", scoped_db)
    enqueue = MagicMock()
    monkeypatch.setattr(webhook_routes, "enqueue_cloud_job", enqueue)
    secret_subscription = "subscription-token-secret"
    secret_resource = "/drives/private-provider-path/token=secret"
    request = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "value": [
                    {
                        "subscriptionId": secret_subscription,
                        "clientState": "department-authority",
                        "changeType": "updated",
                        "resource": secret_resource,
                    }
                ]
            }
        )
    )

    with caplog.at_level(logging.INFO, logger=webhook_routes.logger.name):
        response = await webhook_routes.microsoft_graph_webhook(
            request,
            validationToken=None,
        )

    filter_sql = " ".join(str(expression) for expression in query.filter.call_args.args)
    assert "cloud_webhook_subscriptions.department_id" in filter_sql
    assert "cloud_webhook_subscriptions.subscription_id" in filter_sql
    assert response.status_code == 202
    assert secret_subscription not in caplog.text
    assert secret_resource not in caplog.text
    assert "token=secret" not in caplog.text
    dedupe_key = enqueue.call_args.kwargs["dedupe_key"]
    assert dedupe_key.startswith("webhook:microsoft:")
    assert secret_subscription not in dedupe_key
    assert secret_resource not in dedupe_key


@pytest.mark.asyncio
async def test_microsoft_validation_echo_does_not_log_validation_token(caplog):
    from src.api import webhook_routes

    validation_token = "graph-validation-token-secret"
    with caplog.at_level(logging.INFO, logger=webhook_routes.logger.name):
        response = await webhook_routes.microsoft_graph_validation(validation_token)

    assert response.body == validation_token.encode()
    assert validation_token not in caplog.text


@pytest.mark.asyncio
async def test_microsoft_webhook_enqueue_and_rollback_failures_are_sanitized(
    monkeypatch,
):
    from src.api import webhook_routes

    subscription = SimpleNamespace(
        id="subscription-row",
        subscription_id="provider-subscription",
        department_id="department-authority",
        credential_id="credential-1",
        last_notification_at=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = subscription
    db.rollback.side_effect = RuntimeError("/database/path password=secret")

    @contextmanager
    def scoped_db():
        yield db

    monkeypatch.setattr(webhook_routes, "get_db", scoped_db)
    monkeypatch.setattr(
        webhook_routes,
        "enqueue_cloud_job",
        MagicMock(side_effect=RuntimeError("provider token=secret")),
    )
    request = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "value": [
                    {
                        "subscriptionId": "provider-subscription",
                        "clientState": "department-authority",
                        "changeType": "updated",
                        "resource": "/me/drive/root",
                    }
                ]
            }
        )
    )

    with patch.object(webhook_routes.logger, "error") as log_error:
        response = await webhook_routes.microsoft_graph_webhook(
            request,
            validationToken=None,
        )

    assert response.status_code == 503
    serialized_log = repr(log_error.call_args_list)
    assert "password=secret" not in serialized_log
    assert "token=secret" not in serialized_log
    assert [
        call.kwargs["extra"]["exception_type"] for call in log_error.call_args_list
    ] == ["RuntimeError", "RuntimeError"]


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ([], 400),
        ({"value": {}}, 400),
        ({"value": ["not-an-object"]}, 202),
    ],
)
@pytest.mark.asyncio
async def test_microsoft_webhook_rejects_or_skips_malformed_envelopes(
    monkeypatch, body, expected_status
):
    from src.api import webhook_routes

    database = MagicMock()

    @contextmanager
    def scoped_db():
        yield database

    monkeypatch.setattr(webhook_routes, "get_db", scoped_db)
    response = await webhook_routes.microsoft_graph_webhook(
        SimpleNamespace(json=AsyncMock(return_value=body)),
        validationToken=None,
    )

    assert response.status_code == expected_status
    database.query.assert_not_called()


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://localhost/aelira_test_prod",
        "postgresql://localhost/aelira_production_test",
        "postgresql+asyncpg://localhost/aelira_test",
        "sqlite:////tmp/aelira_test",
        "postgresql://prod-db.example/aelira_test",
        "postgresql://remote.example/aelira_test",
    ],
)
def test_destructive_database_fence_rejects_near_misses(database_url):
    from conftest import require_disposable_postgres_url

    environment = {"ALLOW_DESTRUCTIVE_MIGRATION_TESTS": "1"}
    with pytest.raises(RuntimeError):
        require_disposable_postgres_url(
            database_url,
            destructive=True,
            environment=environment,
        )


def test_destructive_database_fence_requires_opt_in_and_accepts_exact_local_test():
    from conftest import require_disposable_postgres_url

    database_url = "postgresql://localhost/aelira_migration_test"
    with pytest.raises(RuntimeError, match="ALLOW_DESTRUCTIVE"):
        require_disposable_postgres_url(
            database_url,
            destructive=True,
            environment={},
        )
    assert (
        require_disposable_postgres_url(
            database_url,
            destructive=True,
            environment={"ALLOW_DESTRUCTIVE_MIGRATION_TESTS": "1"},
        )
        == database_url
    )

    with pytest.raises(RuntimeError, match="ALLOW_REMOTE"):
        require_disposable_postgres_url(
            "postgresql://ci-postgres/aelira_migration_test",
            destructive=True,
            environment={
                "CI": "true",
                "ALLOW_DESTRUCTIVE_MIGRATION_TESTS": "1",
            },
        )


def test_suite_database_selection_never_uses_local_application_database():
    from conftest import _select_suite_database_url

    environment = {
        "DATABASE_URL": "postgresql://prod.example/aelira",
        "CI": "false",
    }
    selected = _select_suite_database_url(environment)
    assert selected.endswith("/aelira_test")
    assert "prod.example" not in selected

    environment["TEST_DATABASE_URL"] = "postgresql://127.0.0.1/aelira_migration_test"
    assert _select_suite_database_url(environment) == environment["TEST_DATABASE_URL"]


def test_reconciliation_has_its_own_exact_job_type_and_handler():
    from src.db.models import CloudJobType
    from src.jobs.registry import EXECUTABLE_JOB_TYPES, build_default_registry

    assert CloudJobType.RECONCILE.value == "canvas_reconcile"
    assert "canvas_reconcile" in EXECUTABLE_JOB_TYPES
    assert "webhook_refresh" in EXECUTABLE_JOB_TYPES
    registry = build_default_registry()
    assert registry.get("canvas_reconcile") is not registry.get("webhook_refresh")


def test_reconciliation_scheduling_never_uses_webhook_refresh():
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.services.canvas_reconciliation_service import CanvasReconciliationService

    assert "CloudJobType.RECONCILE.value" in inspect.getsource(
        CanvasContentScanner._persist_file_reconciliation
    )
    assert "job_type=CloudJobType.RECONCILE.value" in inspect.getsource(
        CanvasReconciliationService.backfill
    )


@pytest.mark.asyncio
async def test_webhook_refresh_renews_microsoft_and_persists_audit(monkeypatch):
    from src.jobs import webhook_refresh_job

    now = datetime.now(timezone.utc)
    subscription = SimpleNamespace(
        id="sub-row",
        department_id="dept-1",
        credential_id="cred-1",
        provider="microsoft",
        subscription_id="provider-sub",
        resource_uri="/me/drive/root",
        notification_url="https://example.test/hooks/microsoft",
        expiration_time=now,
        is_active=True,
        last_renewed_at=None,
        renewal_status=None,
        renewal_result=None,
    )
    credential = SimpleNamespace(
        id="cred-1", department_id="dept-1", provider="microsoft", is_active=True
    )
    db = MagicMock()
    db.get.side_effect = lambda model, key: (
        subscription if model.__name__ == "CloudWebhookSubscription" else credential
    )
    integration = SimpleNamespace(
        renew_webhook=AsyncMock(
            return_value={
                "subscription_id": "provider-sub",
                "expiration_time": now + timedelta(days=2),
            }
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        webhook_refresh_job,
        "OneDriveIntegration",
        MagicMock(return_value=integration),
    )
    token_manager = SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token"))
    job = SimpleNamespace(
        payload={"subscription_id": "sub-row"},
        department_id="dept-1",
        credential_id="cred-1",
        provider="microsoft",
    )

    result = await webhook_refresh_job.handle_webhook_refresh_job(
        job, db, token_manager
    )

    assert result["success"] is True
    webhook_refresh_job.OneDriveIntegration.assert_called_once_with(
        access_token="token",
        department_id="dept-1",
    )
    assert "credential_id" not in (
        webhook_refresh_job.OneDriveIntegration.call_args.kwargs
    )
    integration.renew_webhook.assert_awaited_once_with("provider-sub")
    assert subscription.renewal_status == "renewed"
    assert subscription.renewal_result == {
        "provider": "microsoft",
        "subscription_id": "provider-sub",
        "status": "renewed",
    }
    assert subscription.expiration_time > now
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_google_webhook_creation_persists_exact_watched_identity(monkeypatch):
    from src.api import google_routes
    from src.db.models import CloudOAuthCredentials, CloudWebhookSubscription

    raw_expiration = datetime.now(timezone.utc) + timedelta(days=6)
    expiration = datetime.fromtimestamp(
        int(raw_expiration.timestamp() * 1000) / 1000, timezone.utc
    )

    async def create_webhook(**kwargs):
        return {
            "channel_id": kwargs["channel_id"],
            "resource_id": "opaque-google-resource",
            "resource_uri": "https://www.googleapis.com/drive/v3/files/watched-file",
            "expiration": str(int(expiration.timestamp() * 1000)),
        }

    integration = SimpleNamespace(
        create_webhook=AsyncMock(side_effect=create_webhook),
        close=AsyncMock(),
    )
    credential = SimpleNamespace(
        id="cred-1",
        department_id="dept-1",
        provider="google",
        is_active=True,
    )
    monkeypatch.setattr(
        google_routes,
        "get_google_credential",
        AsyncMock(return_value=credential),
    )
    monkeypatch.setattr(
        google_routes,
        "get_google_integration",
        AsyncMock(return_value=integration),
    )
    db = MagicMock()
    subscription_query = MagicMock()
    subscription_query.filter.return_value.first.return_value = None
    credential_query = MagicMock()
    credential_query.filter.return_value.first.return_value = credential
    db.query.side_effect = lambda model: (
        credential_query if model is CloudOAuthCredentials else subscription_query
    )

    result = await google_routes.create_google_webhook_subscription(
        google_routes.GoogleWebhookSubscriptionRequest(
            notification_url="https://example.test/hooks/google",
            resource_id="watched-file",
        ),
        api_key=SimpleNamespace(department_id="dept-1"),
        db=db,
    )

    row = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], CloudWebhookSubscription)
    )
    integration.create_webhook.assert_awaited_once_with(
        notification_url="https://example.test/hooks/google",
        resource_id="watched-file",
        channel_id=row.subscription_id,
    )
    assert row.provider_resource_id == "watched-file"
    assert row.provider_channel_resource_id == "opaque-google-resource"
    assert row.resource_uri.endswith("/files/watched-file")
    assert (
        row.subscription_id
        == integration.create_webhook.await_args.kwargs["channel_id"]
    )
    assert row.expiration_time == expiration
    assert result["subscription_id"] == row.id
    assert db.commit.call_count == 2


@pytest.mark.asyncio
async def test_google_webhook_renewal_uses_persisted_watched_file_id(monkeypatch):
    from src.jobs import webhook_refresh_job

    now = datetime.now(timezone.utc)
    expiration = now + timedelta(days=7)
    subscription = SimpleNamespace(
        id="sub-row",
        department_id="dept-1",
        credential_id="cred-1",
        provider="google",
        subscription_id="old-channel",
        provider_resource_id="exact-watched-file",
        provider_channel_resource_id="old-opaque-resource",
        resource_uri="https://wrong.example/resource-uri-is-not-an-id",
        notification_url="https://example.test/hooks/google",
        expiration_time=now,
        is_active=True,
        last_renewed_at=None,
        renewal_status=None,
        renewal_result=None,
    )
    credential = SimpleNamespace(
        id="cred-1", department_id="dept-1", provider="google", is_active=True
    )
    db = MagicMock()
    db.get.side_effect = lambda model, key: (
        subscription if model.__name__ == "CloudWebhookSubscription" else credential
    )
    integration = SimpleNamespace(
        create_webhook=AsyncMock(
            return_value={
                "channel_id": "new-channel",
                "resource_id": "new-opaque-resource",
                "resource_uri": "https://google.test/new-resource-uri",
                "expiration": str(int(expiration.timestamp() * 1000)),
            }
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        webhook_refresh_job,
        "GoogleDriveIntegration",
        MagicMock(return_value=integration),
    )
    token_manager = SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token"))
    job = SimpleNamespace(
        payload={"subscription_id": "sub-row"},
        department_id="dept-1",
        credential_id="cred-1",
        provider="google",
    )

    result = await webhook_refresh_job.handle_webhook_refresh_job(
        job, db, token_manager
    )

    assert result["success"] is True
    integration.create_webhook.assert_awaited_once()
    renewal_request = integration.create_webhook.await_args.kwargs
    assert renewal_request["notification_url"] == subscription.notification_url
    assert renewal_request["resource_id"] == "exact-watched-file"
    assert renewal_request["channel_id"] == subscription.subscription_id
    assert subscription.subscription_id == renewal_request["channel_id"]
    assert subscription.provider_channel_resource_id == "new-opaque-resource"
    assert subscription.resource_uri == "https://google.test/new-resource-uri"
    assert subscription.renewal_result["provider_resource_id"] == "new-opaque-resource"
    assert subscription.pending_renewal_channel_id is None
    assert subscription.pending_renewal_started_at is None
    assert db.commit.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_identity", [None, ""])
async def test_google_webhook_legacy_missing_identity_is_deterministic_without_provider_call(
    monkeypatch, missing_identity
):
    from src.jobs import webhook_refresh_job
    from src.jobs.contracts import FailureKind

    subscription = SimpleNamespace(
        id="sub-row",
        department_id="dept-1",
        credential_id="cred-1",
        provider="google",
        subscription_id="old-channel",
        provider_resource_id=missing_identity,
        provider_channel_resource_id="opaque-resource",
        resource_uri="https://google.test/not-a-file-id",
        notification_url="https://example.test/hooks/google",
        expiration_time=datetime.now(timezone.utc),
        is_active=True,
        renewal_status=None,
        renewal_result=None,
    )
    credential = SimpleNamespace(
        id="cred-1", department_id="dept-1", provider="google", is_active=True
    )
    db = MagicMock()
    db.get.side_effect = lambda model, key: (
        subscription if model.__name__ == "CloudWebhookSubscription" else credential
    )
    constructor = MagicMock()
    monkeypatch.setattr(webhook_refresh_job, "GoogleDriveIntegration", constructor)
    token_manager = SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token"))
    job = SimpleNamespace(
        payload={"subscription_id": "sub-row"},
        department_id="dept-1",
        credential_id="cred-1",
        provider="google",
    )

    result = await webhook_refresh_job.handle_webhook_refresh_job(
        job, db, token_manager
    )

    assert result.kind is FailureKind.DETERMINISTIC
    assert result.code == "webhook_resource_identity_missing"
    constructor.assert_not_called()
    token_manager.refresh_if_expired.assert_not_awaited()
    assert subscription.renewal_status == "failed"
    db.commit.assert_called_once()


@pytest.mark.parametrize(
    ("exc", "expected_kind", "expected_code"),
    [
        (httpx.ConnectError("offline"), "retryable", "provider_network_error"),
        (httpx.ReadTimeout("late"), "retryable", "provider_timeout"),
        (
            httpx.HTTPStatusError(
                "busy",
                request=httpx.Request("POST", "https://provider.test/upload"),
                response=httpx.Response(
                    503,
                    headers={"retry-after": "999999"},
                    request=httpx.Request("POST", "https://provider.test/upload"),
                ),
            ),
            "retryable",
            "provider_unavailable",
        ),
        (
            httpx.HTTPStatusError(
                "denied",
                request=httpx.Request("POST", "https://provider.test/upload"),
                response=httpx.Response(
                    403,
                    request=httpx.Request("POST", "https://provider.test/upload"),
                ),
            ),
            "deterministic",
            "provider_permission_denied",
        ),
    ],
)
def test_upload_exception_classification_is_typed_and_sanitized(
    exc, expected_kind, expected_code
):
    from src.jobs.upload_job import classify_upload_exception

    failure = classify_upload_exception(exc, provider="google")
    assert failure.kind.value == expected_kind
    assert failure.code == expected_code
    assert failure.details.get("retry_after", 0) <= 3600
    assert "provider.test" not in repr(failure)
    assert "/upload" not in repr(failure)


def test_upload_ambiguous_post_body_is_indeterminate_without_blind_retry():
    from src.jobs.contracts import FailureKind
    from src.jobs.upload_job import (
        IndeterminateProviderOutcome,
        classify_upload_exception,
    )

    failure = classify_upload_exception(
        IndeterminateProviderOutcome("provider response lost"), provider="canvas"
    )
    assert failure.kind is FailureKind.INDETERMINATE
    assert failure.code == "provider_outcome_indeterminate"
    assert failure.details == {"provider": "canvas", "retry_safe": False}


@pytest.mark.asyncio
async def test_onedrive_small_upload_returns_file_id_and_job_success(
    tmp_path, monkeypatch
):
    from src.integrations.microsoft_365.onedrive import OneDriveIntegration
    from src.jobs.contracts import JobContext, JobSuccess
    from src.jobs.registry import adapt_legacy_handler
    from src.jobs.upload_job import _upload_to_microsoft

    local = tmp_path / "small.docx"
    local.write_bytes(b"small")
    response = MagicMock()
    response.json.return_value = {"id": "small-file-id", "webUrl": "https://view/small"}
    response.raise_for_status.return_value = None
    client = SimpleNamespace(put=AsyncMock(return_value=response))
    integration = OneDriveIntegration("token", "dept-1")
    monkeypatch.setattr(integration, "_get_client", AsyncMock(return_value=client))

    upload = await integration.upload_file(str(local), file_name="small.docx")

    assert upload.success is True
    assert upload.file_id == "small-file-id"
    monkeypatch.setattr(
        "src.jobs.upload_job.OneDriveIntegration",
        MagicMock(
            return_value=SimpleNamespace(
                upload_file=AsyncMock(return_value=upload), close=AsyncMock()
            )
        ),
    )

    async def legacy(_job, _db, _tokens):
        return await _upload_to_microsoft(
            str(local), "token", file_name="small.docx", department_id="dept-1"
        )

    context = JobContext(
        job_id="job-1",
        job_type="upload",
        payload={},
        claim_token="claim",
        worker_id="worker",
        attempt_count=1,
        report_progress=AsyncMock(return_value=True),
    )
    db = MagicMock()
    db.get.return_value = context
    adapted = await adapt_legacy_handler(legacy)(context, db, MagicMock())
    assert isinstance(adapted, JobSuccess)
    assert adapted.result["new_file_id"] == "small-file-id"


@pytest.mark.asyncio
async def test_onedrive_session_upload_returns_file_id(tmp_path, monkeypatch):
    from src.integrations.microsoft_365 import onedrive

    local = tmp_path / "large.docx"
    local.write_bytes(b"x" * (4 * 1024 * 1024))
    session_response = MagicMock()
    session_response.json.return_value = {"uploadUrl": "https://upload.test/session"}
    session_response.raise_for_status.return_value = None
    graph_client = SimpleNamespace(post=AsyncMock(return_value=session_response))
    final_response = MagicMock(
        status_code=201,
        json=MagicMock(
            return_value={"id": "session-file-id", "webUrl": "https://view/session"}
        ),
    )
    upload_client = SimpleNamespace(put=AsyncMock(return_value=final_response))

    class UploadClientContext:
        async def __aenter__(self):
            return upload_client

        async def __aexit__(self, *_args):
            return None

    integration = onedrive.OneDriveIntegration("token", "dept-1")
    monkeypatch.setattr(
        integration, "_get_client", AsyncMock(return_value=graph_client)
    )
    monkeypatch.setattr(
        onedrive.httpx, "AsyncClient", MagicMock(return_value=UploadClientContext())
    )

    result = await integration.upload_file(str(local), file_name="large.docx")

    assert result.success is True
    assert result.file_id == "session-file-id"


@pytest.mark.asyncio
async def test_legacy_adapter_preserves_typed_upload_failure():
    from src.jobs.contracts import JobContext, JobFailure
    from src.jobs.registry import adapt_legacy_handler

    expected = JobFailure.retryable("provider_rate_limited", {"retry_after": 30})
    handler = adapt_legacy_handler(AsyncMock(return_value=expected))
    context = JobContext(
        job_id="job-1",
        job_type="upload",
        payload={},
        claim_token="claim",
        worker_id="worker",
        attempt_count=1,
        report_progress=AsyncMock(return_value=True),
    )
    db = MagicMock()
    db.get.return_value = context
    assert await handler(context, db, MagicMock()) is expected


def test_orphan_maintenance_queries_are_sql_bounded_and_no_snapshot_all():
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    known = inspect.getsource(ArtifactOrphanScanner._is_known_key)
    purge = inspect.getsource(ArtifactOrphanScanner.purge_reviewed)
    assert ".all()" not in known
    assert purge.index(".limit(self.batch_size)") < purge.index(".all()")
    assert "with_for_update(skip_locked=True)" in purge


def test_orphan_scan_has_hard_visit_bounds_and_persisted_cursor(tmp_path):
    from src.db.models import MaintenanceCursor
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    department = "11111111-1111-4111-8111-111111111111"
    scan = "22222222-2222-4222-8222-222222222222"
    artifact = "33333333-3333-4333-8333-333333333333"
    directory = tmp_path / department / scan / artifact
    directory.mkdir(parents=True)
    for index in range(10_000):
        leaf = f"{index:08x}-4444-4444-8444-444444444444.pdf"
        (directory / leaf).write_bytes(b"live")

    cursor = MaintenanceCursor(key="artifact_orphan_scan", cursor_json={})
    db = MagicMock()
    db.get.side_effect = lambda model, key: (
        cursor if model is MaintenanceCursor else None
    )
    query = MagicMock()
    query.filter.return_value.first.return_value = SimpleNamespace(storage_key="known")
    db.query.return_value = query
    scanner = ArtifactOrphanScanner(
        root=tmp_path,
        batch_size=5,
        grace_seconds=300,
        retention_days=7,
        max_visited_entries=40,
        max_visited_directories=8,
        max_seconds=5,
    )

    first = scanner.run_batch(db)
    first_cursor = dict(cursor.cursor_json)
    second = scanner.run_batch(db)

    assert first["visited_entries"] <= 40
    assert first["visited_directories"] <= 8
    assert first["complete"] is False
    assert first_cursor
    assert first["overflow_manual"] == 1
    assert first_cursor["status"] == "overflow_manual"
    assert cursor.cursor_json == first_cursor
    assert second["visited_entries"] <= 40
    assert second["overflow_manual"] == 1


@pytest.mark.asyncio
async def test_reconciliation_rejects_identical_preexisting_file_without_candidate_id():
    from src.services.canvas_reconciliation_service import CanvasReconciliationObserver

    content = b"same bytes"
    checksum = hashlib.sha256(content).hexdigest()
    candidate = SimpleNamespace(
        id="preexisting",
        filename="lecture_accessible.pdf",
        display_name="lecture_accessible.pdf",
        updated_at=datetime.now(timezone.utc),
    )

    async def download(_file_id, destination):
        Path(destination).write_bytes(content)
        return SimpleNamespace(success=True)

    client = SimpleNamespace(
        list_course_files=AsyncMock(return_value=[candidate]),
        download_file=AsyncMock(side_effect=download),
    )
    result = await CanvasReconciliationObserver(client).observe_exact(
        course_id="course-7",
        source_file_id="source-3",
        candidate_file_id=None,
        expected_file_name="lecture_accessible.pdf",
        artifact_checksum=checksum,
        correlation_id="correlation-5",
    )

    assert result.outcome == "indeterminate"
    client.download_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_absent_candidate_is_indeterminate_not_retry_safe_absent():
    from src.services.canvas_reconciliation_service import CanvasReconciliationObserver

    client = SimpleNamespace(
        list_course_files=AsyncMock(return_value=[]),
        download_file=AsyncMock(),
    )
    result = await CanvasReconciliationObserver(client).observe_exact(
        course_id="course-7",
        source_file_id="source-3",
        candidate_file_id="candidate-9",
        expected_file_name="lecture_accessible.pdf",
        artifact_checksum="a" * 64,
        correlation_id="correlation-5",
    )
    assert result.outcome == "indeterminate"
