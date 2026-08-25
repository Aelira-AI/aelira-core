"""
Cloud Webhook API Routes

Handles incoming webhooks from:
- Google Drive (push notifications)
- Microsoft Graph (subscriptions)

These webhooks notify us when files change in cloud storage,
allowing us to trigger automatic rescans.
"""

from fastapi import APIRouter, Request, HTTPException, Query, Depends
from fastapi.responses import Response, PlainTextResponse
from sqlalchemy.orm import Session
from typing import Optional, Tuple
import hashlib
import logging
from datetime import datetime, timezone

from ..auth.dependencies import get_required_api_key as get_api_key_or_mock
from ..db.database import get_db, get_db_dependency
from ..db.models import (
    APIKey,
    CloudWebhookSubscription,
    CloudProvider,
    CloudJobType,
)
from ..services.job_enqueue_service import enqueue_cloud_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["cloud-webhooks"])


# =============================================================================
# Google Drive Push Notifications
# =============================================================================


@router.post("/google")
async def google_drive_webhook(
    request: Request,
):
    """
    Handle Google Drive push notifications.

    Google sends notifications here when files change in watched folders.
    Headers contain:
    - X-Goog-Channel-ID: Our subscription ID
    - X-Goog-Resource-State: Type of change (sync, add, remove, update, etc.)
    - X-Goog-Resource-ID: Google's resource identifier
    - X-Goog-Message-Number: Sequence number
    """
    # Extract headers
    channel_id = request.headers.get("X-Goog-Channel-ID")
    resource_state = request.headers.get("X-Goog-Resource-State")
    resource_id = request.headers.get("X-Goog-Resource-ID")
    message_number = request.headers.get("X-Goog-Message-Number")

    logger.info(
        f"Google webhook: channel={channel_id}, state={resource_state}, "
        f"resource={resource_id}, msg={message_number}"
    )

    # Sync state is just confirmation that the subscription is active
    if resource_state == "sync":
        return Response(status_code=200)

    # Validate subscription exists
    with get_db() as db:
        subscription = (
            db.query(CloudWebhookSubscription)
            .filter(
                CloudWebhookSubscription.subscription_id == channel_id,
                CloudWebhookSubscription.provider == CloudProvider.GOOGLE.value,
                CloudWebhookSubscription.is_active,
            )
            .first()
        )

        if not subscription:
            logger.warning(f"Unknown Google webhook channel: {channel_id}")
            # Return 200 anyway to prevent Google from retrying
            return Response(status_code=200)

        # Update last notification time
        subscription.last_notification_at = datetime.now(timezone.utc)
        enqueue_cloud_job(
            db,
            department_id=subscription.department_id,
            job_type=CloudJobType.SYNC.value,
            payload={
                "credential_id": subscription.credential_id,
                "provider": CloudProvider.GOOGLE.value,
                "subscription_id": subscription.id,
                "resource_id": resource_id,
                "resource_state": resource_state,
                "message_number": message_number,
            },
            dedupe_key=(
                f"webhook:google:{subscription.subscription_id}:"
                f"{message_number or resource_id or resource_state or 'change'}"
            ),
            credential_id=subscription.credential_id,
            provider=CloudProvider.GOOGLE.value,
            priority=3,
        )
        db.commit()

    return Response(status_code=200)


# =============================================================================
# Microsoft Graph Subscriptions
# =============================================================================


@router.post("/microsoft")
async def microsoft_graph_webhook(
    request: Request,
    validationToken: str = Query(None),
):
    """
    Handle Microsoft Graph subscription notifications.

    Microsoft sends notifications here when files change in OneDrive/SharePoint.
    For subscription validation, Microsoft sends a GET with validationToken.

    Notification body contains:
    - subscriptionId: Our subscription ID
    - changeType: Type of change (updated, created, deleted)
    - resource: The resource path that changed
    - clientState: Our verification token (department_id)
    """
    # Handle subscription validation
    if validationToken:
        if len(validationToken) > 512:
            return Response(status_code=400)
        logger.info("Microsoft webhook validation request received")
        return PlainTextResponse(content=validationToken, status_code=200)

    try:
        body = await request.json()
    except Exception:
        logger.warning("Microsoft webhook: invalid JSON body")
        return Response(status_code=400)

    if not isinstance(body, dict) or not isinstance(body.get("value", []), list):
        logger.warning("Microsoft webhook: invalid notification envelope")
        return Response(status_code=400)

    # Process notifications. Provider-supplied identifiers and resource paths are
    # deliberately excluded from logs because they are not trusted diagnostics.
    notifications = body.get("value", [])

    for notification in notifications:
        if not isinstance(notification, dict):
            logger.warning("Microsoft webhook: invalid notification")
            continue
        subscription_id = notification.get("subscriptionId")
        change_type = notification.get("changeType")
        resource = notification.get("resource")
        client_state = notification.get("clientState")  # Our department_id

        if (
            not isinstance(subscription_id, str)
            or not subscription_id
            or len(subscription_id) > 255
            or not isinstance(client_state, str)
            or not client_state
            or len(client_state) > 64
            or not isinstance(change_type, str)
            or not change_type
            or len(change_type) > 64
            or not isinstance(resource, str)
            or not resource
            or len(resource) > 1024
        ):
            logger.warning("Microsoft webhook: invalid notification fields")
            continue

        logger.info("Microsoft webhook notification received")

        # Validate subscription exists
        with get_db() as db:
            subscription = (
                db.query(CloudWebhookSubscription)
                .filter(
                    CloudWebhookSubscription.subscription_id == subscription_id,
                    CloudWebhookSubscription.provider == CloudProvider.MICROSOFT.value,
                    CloudWebhookSubscription.department_id == client_state,
                    CloudWebhookSubscription.is_active,
                )
                .first()
            )

            if not subscription:
                logger.warning("Unknown Microsoft webhook subscription")
                continue

            # Verify client state matches department ID
            if client_state != subscription.department_id:
                logger.warning("Microsoft webhook client state mismatch")
                continue

            # Update last notification time and durably enqueue reconciliation.
            try:
                subscription.last_notification_at = datetime.now(timezone.utc)
                enqueue_cloud_job(
                    db,
                    department_id=subscription.department_id,
                    job_type=CloudJobType.SYNC.value,
                    payload={
                        "credential_id": subscription.credential_id,
                        "provider": CloudProvider.MICROSOFT.value,
                        "subscription_id": subscription.id,
                        "resource": resource,
                        "change_type": change_type,
                    },
                    dedupe_key="webhook:microsoft:"
                    + hashlib.sha256(
                        f"{subscription_id}\0{resource}\0{change_type}".encode()
                    ).hexdigest(),
                    credential_id=subscription.credential_id,
                    provider=CloudProvider.MICROSOFT.value,
                    priority=3,
                )
                db.commit()
            except Exception as exc:
                try:
                    db.rollback()
                except Exception as rollback_exc:
                    logger.error(
                        "Microsoft webhook enqueue rollback failed",
                        extra={"exception_type": type(rollback_exc).__name__[:64]},
                    )
                logger.error(
                    "Microsoft webhook enqueue failed",
                    extra={"exception_type": type(exc).__name__[:64]},
                )
                return Response(status_code=503)

    return Response(status_code=202)


@router.get("/microsoft")
async def microsoft_graph_validation(validationToken: str = Query(..., max_length=512)):
    """
    Handle Microsoft Graph subscription validation (GET request).

    When creating a subscription, Microsoft sends a GET request with
    validationToken that we must echo back.
    """
    logger.info("Microsoft webhook validation GET received")
    return PlainTextResponse(content=validationToken, status_code=200)


# =============================================================================
# Subscription Management
# =============================================================================


@router.post("/google/subscribe")
async def create_google_subscription(
    request: Request,
    notification_url: str = Query(..., description="URL to receive notifications"),
    folder_id: str = Query(None, description="Folder ID to watch (None for root)"),
):
    """
    Create a Google Drive push notification subscription.

    Requires valid Google OAuth credentials for the department.
    """
    # This would be called from the integration setup flow
    # For now, return placeholder
    return {
        "status": "not_implemented",
        "message": "Use /google/connect flow to set up subscriptions",
    }


@router.post("/microsoft/subscribe")
async def create_microsoft_subscription(
    request: Request,
    notification_url: str = Query(..., description="URL to receive notifications"),
):
    """
    Create a Microsoft Graph subscription.

    Requires valid Microsoft OAuth credentials for the department.
    """
    # This would be called from the integration setup flow
    return {
        "status": "not_implemented",
        "message": "Use /microsoft/connect flow to set up subscriptions",
    }


@router.delete("/subscriptions/{subscription_id}")
async def delete_subscription(
    subscription_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Delete a webhook subscription. Requires authentication.
    """
    _, user_id, department_id = api_key_info
    subscription = (
        db.query(CloudWebhookSubscription)
        .filter(
            CloudWebhookSubscription.id == subscription_id,
            CloudWebhookSubscription.department_id == department_id,
        )
        .first()
    )

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    subscription.is_active = False
    db.commit()

    # Note: Would also need to call Google/Microsoft API to delete
    # the subscription on their end

    return {"success": True, "message": "Subscription deactivated"}


# =============================================================================
# Health Check
# =============================================================================


@router.get("/health")
async def webhook_health():
    """Check webhook endpoint health."""
    with get_db() as db:
        # Count active subscriptions
        google_subs = (
            db.query(CloudWebhookSubscription)
            .filter(
                CloudWebhookSubscription.provider == CloudProvider.GOOGLE.value,
                CloudWebhookSubscription.is_active,
            )
            .count()
        )

        microsoft_subs = (
            db.query(CloudWebhookSubscription)
            .filter(
                CloudWebhookSubscription.provider == CloudProvider.MICROSOFT.value,
                CloudWebhookSubscription.is_active,
            )
            .count()
        )

    return {
        "status": "healthy",
        "service": "cloud-webhooks",
        "active_subscriptions": {
            "google": google_subs,
            "microsoft": microsoft_subs,
        },
    }
