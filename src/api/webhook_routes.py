"""
Cloud Webhook API Routes

Handles incoming webhooks from:
- Google Drive (push notifications)
- Microsoft Graph (subscriptions)

These webhooks notify us when files change in cloud storage,
allowing us to trigger automatic rescans.
"""

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, Query, Depends
from fastapi.responses import Response, PlainTextResponse
from sqlalchemy.orm import Session
from typing import Optional, Tuple
import logging
from datetime import datetime, timezone
import uuid

from ..auth.dependencies import get_required_api_key as get_api_key_or_mock
from ..db.database import get_db, get_db_dependency
from ..db.models import (
    APIKey,
    CloudWebhookSubscription,
    CloudJobQueue,
    CloudProvider,
    CloudJobType,
    CloudJobStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["cloud-webhooks"])


# =============================================================================
# Google Drive Push Notifications
# =============================================================================


@router.post("/google")
async def google_drive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
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
        db.commit()

        # Get department ID from subscription
        department_id = subscription.department_id
        credential_id = subscription.credential_id

    # Queue background task to process the change
    background_tasks.add_task(
        _process_google_change,
        department_id=department_id,
        credential_id=credential_id,
        resource_id=resource_id,
        resource_state=resource_state,
    )

    return Response(status_code=200)


async def _process_google_change(
    department_id: str,
    credential_id: str,
    resource_id: str,
    resource_state: str,
):
    """
    Process a Google Drive file change notification.

    Creates sync jobs to discover and scan changed files.
    """
    logger.info(
        f"Processing Google change: resource={resource_id}, state={resource_state}"
    )

    with get_db() as db:
        # For now, create a sync job to refresh all files
        # Google notifications don't include specific file IDs,
        # so we need to use the changes API or delta queries
        job = CloudJobQueue(
            id=str(uuid.uuid4()),
            department_id=department_id,
            job_type=CloudJobType.SYNC.value,
            credential_id=credential_id,
            provider=CloudProvider.GOOGLE.value,
            status=CloudJobStatus.PENDING.value,
            priority=3,  # Higher priority for webhook-triggered jobs
            progress_message=f"Triggered by webhook: {resource_state}",
        )
        db.add(job)
        db.commit()

        logger.info(f"Created sync job {job.id} for Google webhook")


# =============================================================================
# Microsoft Graph Subscriptions
# =============================================================================


@router.post("/microsoft")
async def microsoft_graph_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
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

    # Process notifications
    notifications = body.get("value", [])

    for notification in notifications:
        subscription_id = notification.get("subscriptionId")
        change_type = notification.get("changeType")
        resource = notification.get("resource")
        client_state = notification.get("clientState")  # Our department_id

        logger.info(
            f"Microsoft webhook: subscription={subscription_id}, "
            f"change={change_type}, resource={resource}"
        )

        # Validate subscription exists
        with get_db() as db:
            subscription = (
                db.query(CloudWebhookSubscription)
                .filter(
                    CloudWebhookSubscription.subscription_id == subscription_id,
                    CloudWebhookSubscription.provider == CloudProvider.MICROSOFT.value,
                    CloudWebhookSubscription.is_active,
                )
                .first()
            )

            if not subscription:
                logger.warning(
                    f"Unknown Microsoft webhook subscription: {subscription_id}"
                )
                continue

            # Verify client state matches department ID
            if client_state and client_state != subscription.department_id:
                logger.warning(
                    f"Microsoft webhook client state mismatch: "
                    f"expected={subscription.department_id}, got={client_state}"
                )
                continue

            # Update last notification time
            subscription.last_notification_at = datetime.now(timezone.utc)
            db.commit()

            # Queue background task
            background_tasks.add_task(
                _process_microsoft_change,
                department_id=subscription.department_id,
                credential_id=subscription.credential_id,
                resource=resource,
                change_type=change_type,
            )

    return Response(status_code=202)


@router.get("/microsoft")
async def microsoft_graph_validation(validationToken: str = Query(..., max_length=512)):
    """
    Handle Microsoft Graph subscription validation (GET request).

    When creating a subscription, Microsoft sends a GET request with
    validationToken that we must echo back.
    """
    logger.info(f"Microsoft webhook validation GET: {validationToken}")
    return PlainTextResponse(content=validationToken, status_code=200)


async def _process_microsoft_change(
    department_id: str,
    credential_id: str,
    resource: str,
    change_type: str,
):
    """
    Process a Microsoft OneDrive/SharePoint file change notification.

    Creates sync jobs to discover and scan changed files.
    """
    logger.info(f"Processing Microsoft change: resource={resource}, type={change_type}")

    with get_db() as db:
        # Create a sync job to refresh files
        # Microsoft notifications can include the specific resource path,
        # but we'll do a full sync for simplicity
        job = CloudJobQueue(
            id=str(uuid.uuid4()),
            department_id=department_id,
            job_type=CloudJobType.SYNC.value,
            credential_id=credential_id,
            provider=CloudProvider.MICROSOFT.value,
            status=CloudJobStatus.PENDING.value,
            priority=3,
            progress_message=f"Triggered by webhook: {change_type}",
            result_data={"resource": resource, "change_type": change_type},
        )
        db.add(job)
        db.commit()

        logger.info(f"Created sync job {job.id} for Microsoft webhook")


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
