"""Durable webhook subscription renewal handler."""

from __future__ import annotations

from datetime import datetime, timezone
import secrets
from typing import Any

import httpx
from sqlalchemy.orm import Session

from src.db.models import CloudOAuthCredentials, CloudWebhookSubscription
from src.integrations.google_workspace.google_drive import (
    GoogleDriveIntegration,
    GoogleWebhookRequestError,
    IndeterminateProviderOutcome,
)
from src.integrations.microsoft_365.onedrive import OneDriveIntegration

from .contracts import FailureKind, JobFailure


def _parse_google_expiration(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _renewal_failure(exc: Exception, provider: str) -> JobFailure:
    details = {"provider": provider}
    if isinstance(exc, IndeterminateProviderOutcome):
        return JobFailure.indeterminate(exc.code, {**details, "retry_safe": False})
    if isinstance(exc, GoogleWebhookRequestError):
        if exc.request_started:
            return JobFailure.indeterminate(
                "webhook_provider_outcome_indeterminate",
                {**details, "retry_safe": False},
            )
        if exc.retryable:
            return JobFailure.retryable(exc.code, details)
        return JobFailure.deterministic(exc.code, details)
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return JobFailure.retryable("webhook_provider_unavailable", details)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429 or status >= 500:
            return JobFailure.retryable("webhook_provider_unavailable", details)
        if status in (401, 403):
            return JobFailure.deterministic("webhook_provider_auth_failed", details)
    return JobFailure.deterministic("webhook_renewal_failed", details)


def _job_correlation(job: Any) -> str | None:
    value = getattr(job, "id", None) or getattr(job, "job_id", None)
    return value if isinstance(value, str) and value else None


def _success_result(subscription: Any, provider: str, *, replay: bool = False):
    result = {
        "success": True,
        "provider": provider,
        "subscription_id": subscription.id,
        "expiration_time": subscription.expiration_time.isoformat(),
    }
    if replay:
        result["idempotent_replay"] = True
    return result


async def handle_webhook_refresh_job(
    job: Any, db: Session, token_manager: Any
) -> dict[str, Any] | JobFailure:
    """Renew one validated Google or Microsoft subscription and audit it."""
    payload = job.payload if isinstance(getattr(job, "payload", None), dict) else {}
    subscription_id = payload.get("subscription_id")
    if not isinstance(subscription_id, str) or not subscription_id:
        return JobFailure.deterministic("webhook_refresh_scope_invalid")
    subscription = db.get(CloudWebhookSubscription, subscription_id)
    if subscription is None:
        return JobFailure.deterministic("webhook_refresh_scope_invalid")
    credential = db.get(CloudOAuthCredentials, subscription.credential_id)
    provider = getattr(subscription, "provider", None)
    if (
        credential is None
        or subscription.department_id != job.department_id
        or subscription.credential_id != getattr(job, "credential_id", None)
        or provider != getattr(job, "provider", None)
        or credential.id != subscription.credential_id
        or credential.department_id != subscription.department_id
        or credential.provider != provider
        or credential.is_active is not True
        or subscription.is_active is not True
        or provider not in {"google", "microsoft"}
    ):
        return JobFailure.deterministic("webhook_refresh_scope_invalid")

    correlation = _job_correlation(job)
    prior_result = (
        subscription.renewal_result
        if isinstance(subscription.renewal_result, dict)
        else {}
    )
    if (
        provider == "google"
        and subscription.renewal_status == "renewed"
        and correlation is not None
        and prior_result.get("correlation_id") == correlation
    ):
        return _success_result(subscription, provider, replay=True)
    if provider == "google" and subscription.renewal_status in {
        "requesting",
        "indeterminate",
    }:
        if subscription.renewal_status == "requesting":
            subscription.renewal_status = "indeterminate"
            subscription.renewal_result = {
                "provider": provider,
                "status": "indeterminate",
                "code": "webhook_provider_outcome_indeterminate",
                "correlation_id": correlation,
                "pending_channel_id": getattr(
                    subscription, "pending_renewal_channel_id", None
                ),
            }
            db.commit()
        return JobFailure.indeterminate(
            "webhook_provider_outcome_indeterminate",
            {"provider": provider, "retry_safe": False},
        )

    watched_resource_id = getattr(subscription, "provider_resource_id", None)
    if provider == "google" and (
        not isinstance(watched_resource_id, str) or not watched_resource_id.strip()
    ):
        subscription.renewal_status = "failed"
        subscription.renewal_result = {
            "provider": provider,
            "status": "failed",
            "code": "webhook_resource_identity_missing",
        }
        db.commit()
        return JobFailure.deterministic(
            "webhook_resource_identity_missing", {"provider": provider}
        )

    integration: Any = None
    try:
        access_token = await token_manager.refresh_if_expired(credential, db)
        if provider == "microsoft":
            integration = OneDriveIntegration(
                access_token=access_token,
                credential_id=credential.id,
            )
            renewal = await integration.renew_webhook(subscription.subscription_id)
            renewed_id = renewal.get("subscription_id")
            expiration = renewal.get("expiration_time")
        else:
            if not subscription.notification_url:
                return JobFailure.deterministic("webhook_refresh_scope_invalid")
            pending_channel_id = getattr(
                subscription, "pending_renewal_channel_id", None
            )
            if not isinstance(pending_channel_id, str) or not pending_channel_id:
                pending_channel_id = secrets.token_urlsafe(32)
                subscription.pending_renewal_channel_id = pending_channel_id
                subscription.pending_renewal_started_at = datetime.now(timezone.utc)
            subscription.renewal_status = "pending"
            subscription.renewal_result = {
                "provider": provider,
                "status": "pending",
                "correlation_id": correlation,
                "pending_channel_id": pending_channel_id,
            }
            # This commit is the durable no-blind-retry boundary before the POST.
            db.commit()
            integration = GoogleDriveIntegration(
                credential_id=credential.id, access_token=access_token
            )
            # A crash after this checkpoint is conservatively operator-reconciled;
            # a worker replay never guesses whether Google accepted the POST.
            subscription.renewal_status = "requesting"
            subscription.renewal_result = {
                "provider": provider,
                "status": "requesting",
                "correlation_id": correlation,
                "pending_channel_id": pending_channel_id,
            }
            db.commit()
            renewal = await integration.create_webhook(
                notification_url=subscription.notification_url,
                resource_id=subscription.provider_resource_id,
                channel_id=pending_channel_id,
            )
            renewed_id = pending_channel_id
            expiration = _parse_google_expiration(renewal.get("expiration"))
        if (
            not isinstance(renewed_id, str)
            or not renewed_id
            or not isinstance(expiration, datetime)
        ):
            if provider == "google":
                raise IndeterminateProviderOutcome(
                    "webhook_provider_response_incomplete"
                )
            raise ValueError("provider returned incomplete renewal")
        if provider == "google":
            returned_resource_id = renewal.get("resource_id")
            returned_resource_uri = renewal.get("resource_uri")
            if not isinstance(returned_resource_id, str) or not returned_resource_id:
                raise IndeterminateProviderOutcome(
                    "webhook_provider_response_incomplete"
                )
            if not isinstance(returned_resource_uri, str) or not returned_resource_uri:
                raise IndeterminateProviderOutcome(
                    "webhook_provider_response_incomplete"
                )
            subscription.provider_channel_resource_id = returned_resource_id
            subscription.resource_uri = returned_resource_uri
        subscription.subscription_id = renewed_id
        subscription.expiration_time = expiration
        subscription.last_renewed_at = datetime.now(timezone.utc)
        subscription.renewal_status = "renewed"
        subscription.renewal_result = {
            "provider": provider,
            "subscription_id": renewed_id,
            "status": "renewed",
        }
        if provider == "google":
            subscription.pending_renewal_channel_id = None
            subscription.pending_renewal_started_at = None
            subscription.renewal_result.update(
                {
                    "provider_resource_id": subscription.provider_channel_resource_id,
                    "resource_uri": subscription.resource_uri,
                    "correlation_id": correlation,
                }
            )
        db.commit()
        return _success_result(subscription, provider)
    except Exception as exc:
        db.rollback()
        failure = _renewal_failure(exc, provider)
        pending_channel_id = getattr(subscription, "pending_renewal_channel_id", None)
        if provider == "google" and failure.kind is FailureKind.INDETERMINATE:
            subscription.renewal_status = "indeterminate"
            subscription.renewal_result = {
                "provider": provider,
                "status": "indeterminate",
                "code": failure.code,
                "correlation_id": correlation,
                "pending_channel_id": pending_channel_id,
            }
        elif provider == "google" and failure.kind is FailureKind.RETRYABLE:
            subscription.renewal_status = "pending"
            subscription.renewal_result = {
                "provider": provider,
                "status": "pending",
                "code": failure.code,
                "correlation_id": correlation,
                "pending_channel_id": pending_channel_id,
            }
        else:
            subscription.renewal_status = "failed"
            subscription.renewal_result = {
                "provider": provider,
                "status": "failed",
                "code": failure.code,
            }
            if provider == "google":
                subscription.pending_renewal_channel_id = None
                subscription.pending_renewal_started_at = None
        db.commit()
        return failure
    finally:
        if integration is not None:
            await integration.close()


__all__ = [
    "GoogleWebhookRequestError",
    "IndeterminateProviderOutcome",
    "handle_webhook_refresh_job",
]
