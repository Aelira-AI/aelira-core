"""
Google Workspace Integration API Routes

Provides endpoints for:
- OAuth 2.0 connection flow
- Google Drive file listing
- File scanning with existing processors
- Auto-remediation with upload back to Drive
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from google.oauth2.credentials import Credentials
from contextlib import contextmanager, asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator, StringConstraints
from typing import List, Optional, Dict, Any, Literal, Annotated
from datetime import datetime, timezone
import logging
import os
import uuid

from ..db.database import get_db_dependency
from ..db.models import (
    APIKey,
    CloudOAuthCredentials,
    CloudFile,
    CloudJobQueue,
    CloudProvider,
    CloudJobType,
    CloudWebhookSubscription,
)
from ..api.auth_routes import get_current_api_key
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..middleware.quota import require_feature
from ..integrations.google_workspace.google_drive import GoogleDriveIntegration
from ..integrations.google_workspace.google_drive import IndeterminateProviderOutcome
from ..integrations.google_workspace.google_oauth import GoogleOAuthService
from ..integrations.google_workspace.google_docs import GoogleDocsService
from ..integrations.google_workspace.google_slides import GoogleSlidesService
from ..integrations.google_workspace.google_sheets import GoogleSheetsService
from ..config.settings import get_settings
from ..services.remediation_artifact_service import (
    ArtifactAuthorizationError,
    RemediationArtifactService,
)
from ..services.job_enqueue_service import enqueue_cloud_job
from ..integrations.cloud_base import (
    CloudFileInfo,
    CloudIntegrationError,
    CloudNotFoundError,
    CloudRateLimitError,
)

# Aliases for test compatibility
get_db = get_db_dependency
GoogleDriveService = GoogleDriveIntegration

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/google", tags=["google-workspace"])
settings = get_settings()


# ==================== Request/Response Models ====================

NonBlankIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class GoogleConnectRequest(BaseModel):
    """Request to initiate Google OAuth connection."""

    redirect_uri: str = Field(..., description="URI to redirect after OAuth")
    scopes: Optional[List[str]] = Field(
        default=None,
        description="OAuth scopes to request (defaults to Drive read/write)",
    )


class GoogleConnectResponse(BaseModel):
    """Response with OAuth authorization URL."""

    auth_url: str
    state: str


class GoogleCallbackRequest(BaseModel):
    """Request to complete OAuth callback."""

    code: str = Field(..., description="Authorization code from Google")
    state: str = Field(..., description="State parameter for verification")
    redirect_uri: str = Field(..., description="Same redirect_uri used in connect")


class GoogleCredentialResponse(BaseModel):
    """Response with connected credential info."""

    id: str
    provider: str
    provider_email: Optional[str]
    provider_name: Optional[str]
    is_active: bool
    last_sync_at: Optional[datetime]
    created_at: datetime


class GoogleFileResponse(BaseModel):
    """Response for a Google Drive file."""

    id: str
    provider_file_id: str
    file_name: str
    file_type: str
    mime_type: Optional[str]
    file_size_bytes: Optional[int]
    web_view_link: Optional[str]
    last_scanned_at: Optional[datetime]
    last_compliance_score: Optional[float]
    needs_rescan: bool


class GoogleFileListResponse(BaseModel):
    """Response for file listing."""

    files: List[GoogleFileResponse]
    next_page_token: Optional[str]
    total_count: int


class ScanFileRequest(BaseModel):
    """Request to scan a specific file."""

    file_id: NonBlankIdentifier = Field(
        ..., description="Cloud file ID (not provider file ID)"
    )


class ScanFolderRequest(BaseModel):
    """Request to scan all files in a folder."""

    folder_id: NonBlankIdentifier = Field(..., description="Google Drive folder ID")
    recursive: bool = Field(default=True, description="Scan subfolders recursively")


class RemediateFileRequest(BaseModel):
    """Request to remediate and re-upload a file."""

    file_id: NonBlankIdentifier = Field(..., description="Cloud file ID")
    upload_as_new: bool = Field(
        default=False, description="Upload as new file instead of replacing"
    )


class ScanResultResponse(BaseModel):
    """Response with scan results."""

    file_id: str
    job_id: str
    scan_id: Optional[str]
    compliance_score: Optional[float]
    issues_found: int
    status: str
    message: str


class JobStatusResponse(BaseModel):
    """Response for job status."""

    job_id: str
    status: str
    progress: int
    progress_message: Optional[str]
    result_data: Optional[Dict[str, Any]]
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]


class GoogleWebhookSubscriptionRequest(BaseModel):
    """Create a watch for one exact Google Drive resource."""

    notification_url: str = Field(..., min_length=1, max_length=1024)
    resource_id: str = Field(..., min_length=1, max_length=1024)

    @field_validator("notification_url", "resource_id")
    @classmethod
    def validate_identity_field(cls, value: str) -> str:
        """Reject whitespace-only provider identities before durable work."""
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


# ==================== Helper Functions ====================


def get_token_manager() -> OAuthTokenManager:
    """Get OAuth token manager instance."""
    encryption_key = os.environ.get("TOKEN_ENCRYPTION_KEY")
    if not encryption_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token encryption key not configured",
        )
    return OAuthTokenManager(encryption_key)


async def get_google_credential(
    api_key: APIKey,
    db: Session,
) -> CloudOAuthCredentials:
    """Get Google OAuth credential for department, refreshing if needed."""
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected. Please connect first via /google/connect",
        )

    # Check if token needs refresh
    token_manager = get_token_manager()
    if token_manager.is_token_expired(credential.token_expires_at):
        try:
            # Decrypt refresh token
            refresh_token = token_manager.decrypt_token(credential.refresh_token)

            # Refresh tokens
            new_access, new_refresh, new_expires = (
                await token_manager.refresh_google_token(refresh_token)
            )

            # Update credential
            credential.access_token = token_manager.encrypt_token(new_access)
            if new_refresh:
                credential.refresh_token = token_manager.encrypt_token(new_refresh)
            credential.token_expires_at = new_expires
            db.commit()

            logger.info(
                f"Refreshed Google OAuth token for department {api_key.department_id}"
            )

        except Exception as e:
            logger.error("Failed to refresh Google token: %s", type(e).__name__)
            credential.is_active = False
            credential.last_error = "Token refresh failed"
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google connection expired. Please reconnect.",
            )

    return credential


async def get_google_integration(
    credential: CloudOAuthCredentials,
) -> GoogleDriveIntegration:
    """Get Google Drive integration instance with valid access token."""
    token_manager = get_token_manager()
    access_token = token_manager.decrypt_token(credential.access_token)

    return GoogleDriveIntegration(
        access_token=access_token,
        credential_id=credential.id,
    )


def _google_webhook_expiration(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _google_webhook_manual_response(subscription: CloudWebhookSubscription) -> dict:
    """Return the stable fail-closed response for a possibly-created channel."""
    return {
        "success": False,
        "subscription_id": subscription.id,
        "status": "manual_required",
        "error_code": "webhook_provider_outcome_indeterminate",
        "retry_safe": False,
    }


def _google_webhook_success_response(
    subscription: CloudWebhookSubscription, *, replayed: bool
) -> dict:
    """Return the durable Google channel identity for initial-create replays."""
    return {
        "success": True,
        "subscription_id": subscription.id,
        "channel_id": subscription.subscription_id,
        "resource_id": subscription.provider_channel_resource_id,
        "expiration_time": subscription.expiration_time.isoformat(),
        "replayed": replayed,
    }


_GOOGLE_WEBHOOK_ACTIVE_IDENTITY_STATUSES = (
    "requesting",
    "indeterminate",
    "created",
    "renewed",
)


def _google_webhook_existing_response(
    subscription: CloudWebhookSubscription,
) -> dict:
    if subscription.renewal_status in {"created", "renewed"}:
        return _google_webhook_success_response(subscription, replayed=True)
    return _google_webhook_manual_response(subscription)


def _get_google_webhook_credential_identity(
    api_key: APIKey, db: Session
) -> CloudOAuthCredentials:
    """Load and validate credential identity without touching OAuth tokens."""
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active.is_(True),
        )
        .first()
    )
    if (
        credential is None
        or getattr(credential, "department_id", None) != api_key.department_id
        or getattr(credential, "provider", None) != CloudProvider.GOOGLE.value
        or getattr(credential, "is_active", None) is not True
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected. Please connect first via /google/connect",
        )
    return credential


@router.post("/webhooks")
async def create_google_webhook_subscription(
    request: GoogleWebhookSubscriptionRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Durably record a channel intent before creating an exact Drive watch."""
    raw_credential = _get_google_webhook_credential_identity(api_key, db)

    existing = (
        db.query(CloudWebhookSubscription)
        .filter(
            CloudWebhookSubscription.department_id == api_key.department_id,
            CloudWebhookSubscription.credential_id == raw_credential.id,
            CloudWebhookSubscription.provider == CloudProvider.GOOGLE.value,
            CloudWebhookSubscription.provider_resource_id == request.resource_id,
            CloudWebhookSubscription.notification_url == request.notification_url,
            CloudWebhookSubscription.renewal_status.in_(
                _GOOGLE_WEBHOOK_ACTIVE_IDENTITY_STATUSES
            ),
        )
        .first()
    )
    if existing is not None:
        return _google_webhook_existing_response(existing)

    # A terminal row only permits explicit replacement once another path has
    # durably marked the old provider channel inactive. Contradictory metadata
    # is not proof that Google stopped delivering to the old channel.
    unclear_terminal = (
        db.query(CloudWebhookSubscription)
        .filter(
            CloudWebhookSubscription.department_id == api_key.department_id,
            CloudWebhookSubscription.credential_id == raw_credential.id,
            CloudWebhookSubscription.provider == CloudProvider.GOOGLE.value,
            CloudWebhookSubscription.provider_resource_id == request.resource_id,
            CloudWebhookSubscription.notification_url == request.notification_url,
            CloudWebhookSubscription.is_active.is_(True),
            CloudWebhookSubscription.renewal_status.notin_(
                _GOOGLE_WEBHOOK_ACTIVE_IDENTITY_STATUSES
            ),
        )
        .first()
    )
    if unclear_terminal is not None and getattr(unclear_terminal, "is_active", False):
        return _google_webhook_manual_response(unclear_terminal)

    credential = await get_google_credential(api_key, db)
    if (
        getattr(credential, "id", None) != raw_credential.id
        or getattr(credential, "department_id", None) != api_key.department_id
        or getattr(credential, "provider", None) != CloudProvider.GOOGLE.value
        or getattr(credential, "is_active", None) is not True
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google credential identity changed during webhook creation",
        )

    pending_channel_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    subscription = CloudWebhookSubscription(
        id=str(uuid.uuid4()),
        department_id=api_key.department_id,
        credential_id=credential.id,
        provider=CloudProvider.GOOGLE.value,
        subscription_id=pending_channel_id,
        provider_resource_id=request.resource_id,
        expiration_time=started_at,
        notification_url=request.notification_url,
        is_active=False,
        renewal_status="requesting",
        renewal_result={
            "provider": CloudProvider.GOOGLE.value,
            "status": "requesting",
            "pending_channel_id": pending_channel_id,
        },
        pending_renewal_channel_id=pending_channel_id,
        pending_renewal_started_at=started_at,
    )
    db.add(subscription)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = (
            db.query(CloudWebhookSubscription)
            .filter(
                CloudWebhookSubscription.department_id == api_key.department_id,
                CloudWebhookSubscription.credential_id == credential.id,
                CloudWebhookSubscription.provider == CloudProvider.GOOGLE.value,
                CloudWebhookSubscription.provider_resource_id == request.resource_id,
                CloudWebhookSubscription.notification_url == request.notification_url,
                CloudWebhookSubscription.renewal_status.in_(
                    _GOOGLE_WEBHOOK_ACTIVE_IDENTITY_STATUSES
                ),
            )
            .first()
        )
        if concurrent is not None:
            return _google_webhook_existing_response(concurrent)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google webhook intent could not be persisted",
        )
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google webhook intent could not be persisted",
        )

    integration = await get_google_integration(credential)
    try:
        provider_result = await integration.create_webhook(
            notification_url=request.notification_url,
            resource_id=request.resource_id,
            channel_id=pending_channel_id,
        )
        channel_id = provider_result.get("channel_id")
        channel_resource_id = provider_result.get("resource_id")
        resource_uri = provider_result.get("resource_uri")
        expiration = _google_webhook_expiration(provider_result.get("expiration"))
        if not all(
            isinstance(value, str) and value
            for value in (channel_id, channel_resource_id, resource_uri)
        ) or not isinstance(expiration, datetime):
            raise ValueError("Google returned an incomplete webhook identity")
        if channel_id != pending_channel_id:
            raise IndeterminateProviderOutcome("webhook_provider_identity_mismatch")
        subscription.subscription_id = channel_id
        subscription.provider_channel_resource_id = channel_resource_id
        subscription.resource_uri = resource_uri
        subscription.expiration_time = expiration
        subscription.is_active = True
        subscription.renewal_status = "created"
        subscription.renewal_result = {
            "provider": CloudProvider.GOOGLE.value,
            "subscription_id": channel_id,
            "provider_resource_id": channel_resource_id,
            "resource_uri": resource_uri,
            "status": "created",
        }
        subscription.pending_renewal_channel_id = None
        subscription.pending_renewal_started_at = None
        try:
            db.commit()
        except Exception:
            # The provider may have accepted the channel while the durable row
            # remains at its already-committed requesting checkpoint.
            db.rollback()
            return _google_webhook_manual_response(subscription)
        return _google_webhook_success_response(subscription, replayed=False)
    except IndeterminateProviderOutcome as exc:
        subscription.renewal_status = "indeterminate"
        subscription.renewal_result = {
            "provider": CloudProvider.GOOGLE.value,
            "status": "indeterminate",
            "code": exc.code,
            "pending_channel_id": pending_channel_id,
            "retry_safe": False,
        }
        try:
            db.commit()
        except Exception:
            db.rollback()
        return _google_webhook_manual_response(subscription)
    except Exception:
        # Never erase the committed requesting checkpoint: replay must stop
        # rather than risk creating a second provider channel.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Google webhook creation failed",
        )
    finally:
        try:
            await integration.close()
        finally:
            integration.cleanup()


# ==================== Helper Functions ====================


# ==================== OAuth Connection Endpoints ====================


@router.post("/connect", response_model=GoogleConnectResponse)
async def connect_google(
    request: GoogleConnectRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Initiate Google OAuth 2.0 connection.

    Returns an authorization URL that the user should visit to grant access.
    After authorization, Google redirects to the specified redirect_uri with
    a code parameter that should be sent to /google/callback.

    Authentication:
    - Production: Requires valid API key (uses api_key.department_id)
    - Development: Can use query parameter department_id if no API key provided

    REQUIRES: cloud_integration feature
    """
    dept_id = api_key.department_id

    # Check feature access - Google integration requires cloud_integration feature
    await require_feature(
        db, dept_id, "cloud_integration", "Google Workspace Integration"
    )

    # Check if already connected
    existing = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == dept_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
            CloudOAuthCredentials.is_active,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google Workspace already connected. Disconnect first to reconnect.",
        )

    token_manager = get_token_manager()

    # Server-side CSRF state bound to this department, one-time use, TTL'd.
    from ..auth.redis_rate_limiter import OAuthStateManager

    state = OAuthStateManager.create_state(
        metadata={"department_id": dept_id, "provider": "google"}
    )

    auth_url = token_manager.get_google_auth_url(
        redirect_uri=request.redirect_uri,
        scopes=request.scopes,
        state=state,
    )

    logger.info(f"Generated Google OAuth URL for department {dept_id}")

    return GoogleConnectResponse(auth_url=auth_url, state=state)


@router.get("/callback")
async def google_callback_get(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(..., description="State parameter for verification"),
    error: Optional[str] = Query(default=None, description="Error from OAuth provider"),
    db: Session = Depends(get_db_dependency),
):
    """
    Complete Google OAuth callback (GET redirect from Google).

    This endpoint receives the OAuth redirect from Google after user authorization.
    It exchanges the authorization code for tokens and stores the connection.
    """
    # Handle OAuth errors
    if error:
        logger.error("Google OAuth authorization was denied")
        return RedirectResponse(
            url=f"{os.getenv('DASHBOARD_URL', 'http://localhost:5173')}/integrations?error=oauth_failed&message={error}"
        )

    # Verify + consume the server-side state (CSRF defence). department_id
    # comes ONLY from verified metadata, never the query string.
    from ..auth.redis_rate_limiter import OAuthStateManager

    is_valid, metadata = OAuthStateManager.verify_and_consume_state(state)
    department_id = (metadata or {}).get("department_id")
    if not is_valid or not department_id:
        logger.warning("Google OAuth callback with invalid/expired state")
        return RedirectResponse(
            url=f"{os.getenv('DASHBOARD_URL', 'http://localhost:5173')}/integrations?error=invalid_state"
        )

    token_manager = get_token_manager()

    try:
        # Exchange code for tokens
        # Use the backend URL as redirect_uri since that's what we registered
        token_data = await token_manager.exchange_google_code(
            code=code,
            redirect_uri=os.getenv(
                "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/google/callback"
            ),
        )

        # Delete any existing inactive credentials
        db.query(CloudOAuthCredentials).filter(
            CloudOAuthCredentials.department_id == department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
        ).delete()

        # Create new credential
        credential = CloudOAuthCredentials(
            id=str(uuid.uuid4()),
            department_id=department_id,
            provider=CloudProvider.GOOGLE.value,
            access_token=token_manager.encrypt_token(token_data["access_token"]),
            refresh_token=token_manager.encrypt_token(token_data["refresh_token"]),
            token_expires_at=token_data["expires_at"],
            provider_user_id=token_data.get("user_id"),
            provider_email=token_data.get("email"),
            provider_name=token_data.get("name"),
            scopes=token_data.get("scopes", []),
            is_active=True,
        )

        db.add(credential)
        db.commit()

        logger.info("Connected Google Workspace for department %s", department_id)

        # Redirect back to frontend with success
        return RedirectResponse(
            url=f"{os.getenv('DASHBOARD_URL', 'http://localhost:5173')}/integrations?success=google_connected&email={token_data.get('email', '')}"
        )

    except Exception as e:
        logger.error("Google OAuth callback failed: %s", type(e).__name__)
        return RedirectResponse(
            url=f"{os.getenv('DASHBOARD_URL', 'http://localhost:5173')}/integrations?error=exchange_failed&message={str(e)}"
        )


@router.post("/callback", response_model=GoogleCredentialResponse)
async def google_callback(
    request: GoogleCallbackRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Complete Google OAuth callback (POST - for programmatic use).

    Exchange the authorization code for tokens and store the connection.
    """
    # Verify state contains correct department ID
    if not request.state.startswith(api_key.department_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state parameter"
        )

    token_manager = get_token_manager()

    try:
        # Exchange code for tokens (also returns user info)
        token_data = await token_manager.exchange_google_code(
            code=request.code,
            redirect_uri=request.redirect_uri,
        )

        # Delete any existing inactive credentials
        db.query(CloudOAuthCredentials).filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
        ).delete()

        # Create new credential (user info is included in token_data from exchange)
        credential = CloudOAuthCredentials(
            id=str(uuid.uuid4()),
            department_id=api_key.department_id,
            provider=CloudProvider.GOOGLE.value,
            access_token=token_manager.encrypt_token(token_data["access_token"]),
            refresh_token=token_manager.encrypt_token(token_data["refresh_token"]),
            token_expires_at=token_data["expires_at"],
            provider_user_id=token_data.get("user_id"),
            provider_email=token_data.get("email"),
            provider_name=token_data.get("name"),
            scopes=token_data.get("scopes", []),
            is_active=True,
        )

        db.add(credential)
        db.commit()
        db.refresh(credential)

        logger.info(
            "Connected Google Workspace for department %s", api_key.department_id
        )

        return GoogleCredentialResponse(
            id=credential.id,
            provider=credential.provider,
            provider_email=credential.provider_email,
            provider_name=credential.provider_name,
            is_active=credential.is_active,
            last_sync_at=credential.last_sync_at,
            created_at=credential.created_at,
        )

    except Exception as e:
        logger.error("Google OAuth callback failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth callback failed: {str(e)}",
        )


@router.delete("/disconnect")
async def disconnect_google(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Disconnect Google Workspace integration.

    Revokes the OAuth token and removes stored credentials.
    """
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    # Managed artifacts use RESTRICT parents and must be handled explicitly.
    try:
        RemediationArtifactService.from_settings().delete_for_credential(
            db,
            department_id=api_key.department_id,
            credential_id=credential.id,
        )
    except ArtifactAuthorizationError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="artifact_cleanup_required"
        ) from None

    try:
        # Only revoke after the complete managed-child set is authorized.
        token_manager = get_token_manager()
        access_token = token_manager.decrypt_token(credential.access_token)
        await token_manager.revoke_google_token(access_token)
    except Exception as e:
        logger.warning(
            "Failed to revoke Google token (may already be revoked): %s",
            type(e).__name__,
        )

    try:
        # Finalize children and credential in the same transaction as artifact rows.
        db.query(CloudJobQueue).filter(
            CloudJobQueue.credential_id == credential.id
        ).delete()
        db.query(CloudFile).filter(CloudFile.credential_id == credential.id).delete()
        db.delete(credential)
        db.commit()
    except Exception:
        db.rollback()
        raise

    logger.info(f"Disconnected Google Workspace for department {api_key.department_id}")

    return {"success": True, "message": "Google Workspace disconnected"}


@router.get("/status", response_model=GoogleCredentialResponse)
async def google_status(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """
    Get Google Workspace connection status.

    REQUIRES: cloud_integration feature
    """
    # Check feature access
    await require_feature(
        db, api_key.department_id, "cloud_integration", "Google Workspace Integration"
    )

    credential = (
        db.query(CloudOAuthCredentials)
        .filter(
            CloudOAuthCredentials.department_id == api_key.department_id,
            CloudOAuthCredentials.provider == CloudProvider.GOOGLE.value,
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Workspace not connected",
        )

    return GoogleCredentialResponse(
        id=credential.id,
        provider=credential.provider,
        provider_email=credential.provider_email,
        provider_name=credential.provider_name,
        is_active=credential.is_active,
        last_sync_at=credential.last_sync_at,
        created_at=credential.created_at,
    )


# ==================== File Workflow Helpers ====================


@contextmanager
def _google_file_operation(db: Session):
    """Keep provider/database failures bounded and roll back route mutations."""
    try:
        yield
    except HTTPException:
        db.rollback()
        raise
    except CloudNotFoundError:
        db.rollback()
        raise HTTPException(status_code=404, detail="File not found") from None
    except CloudRateLimitError:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail="Google Drive rate limit exceeded. Please try again.",
        ) from None
    except CloudIntegrationError:
        db.rollback()
        raise HTTPException(
            status_code=502, detail="Google Drive request failed."
        ) from None
    except Exception as exc:
        db.rollback()
        logger.error("Google file operation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="Unable to complete Google file operation. Please try again.",
        ) from None


@asynccontextmanager
async def _google_drive(credential: CloudOAuthCredentials):
    integration = await get_google_integration(credential)
    try:
        yield integration
    finally:
        try:
            await integration.close()
        finally:
            integration.cleanup()


def _track_google_file(
    db: Session, credential: CloudOAuthCredentials, info: CloudFileInfo
) -> CloudFile:
    """Persist the shared adapter DTO without relying on provider-only fields."""
    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.department_id == credential.department_id,
            CloudFile.credential_id == credential.id,
            CloudFile.provider == CloudProvider.GOOGLE.value,
            CloudFile.provider_file_id == info.id,
        )
        .first()
    )
    file_type = {
        "application/vnd.google-apps.document": "docx",
        "application/vnd.google-apps.presentation": "pptx",
        "application/vnd.google-apps.spreadsheet": "xlsx",
    }.get(info.mime_type, info.file_type.value if info.file_type else "unknown")
    if cloud_file is None:
        cloud_file = CloudFile(
            id=str(uuid.uuid4()),
            department_id=credential.department_id,
            credential_id=credential.id,
            provider=CloudProvider.GOOGLE.value,
            provider_file_id=info.id,
            needs_rescan=True,
        )
        db.add(cloud_file)
    elif cloud_file.provider_version != info.version:
        cloud_file.needs_rescan = True
    cloud_file.provider_parent_id = info.parent_id
    cloud_file.file_name = info.name
    cloud_file.file_type = file_type
    cloud_file.mime_type = info.mime_type
    cloud_file.file_size_bytes = info.size_bytes
    cloud_file.web_view_link = info.web_view_link
    cloud_file.provider_version = info.version
    cloud_file.provider_modified_at = info.modified_at
    db.flush()
    return cloud_file


def _google_tracked_file(
    db: Session, credential: CloudOAuthCredentials, file_id: str
) -> CloudFile:
    cloud_file = (
        db.query(CloudFile)
        .filter(
            CloudFile.id == file_id,
            CloudFile.department_id == credential.department_id,
            CloudFile.credential_id == credential.id,
            CloudFile.provider == CloudProvider.GOOGLE.value,
        )
        .first()
    )
    if cloud_file is None:
        raise HTTPException(status_code=404, detail="File not found")
    return cloud_file


def _enqueue_google_scan(
    db: Session, credential: CloudOAuthCredentials, cloud_file: CloudFile
) -> CloudJobQueue:
    return enqueue_cloud_job(
        db,
        department_id=credential.department_id,
        job_type=CloudJobType.SCAN.value,
        payload={
            "cloud_file_id": cloud_file.id,
            "credential_id": credential.id,
            "provider": CloudProvider.GOOGLE.value,
            "provider_file_id": cloud_file.provider_file_id,
        },
        dedupe_key=f"scan:google:{cloud_file.id}:{cloud_file.provider_version or 'current'}",
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider=CloudProvider.GOOGLE.value,
        provider_file_id=cloud_file.provider_file_id,
        priority=5,
    )


def _google_job_response(job: CloudJobQueue) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        progress_message=job.progress_message,
        result_data=job.result_data,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


# ==================== File Listing Endpoints ====================


@router.get("/drive/files", response_model=GoogleFileListResponse)
async def list_drive_files(
    folder_id: Optional[str] = Query(None, description="Folder ID (None for root)"),
    page_token: Optional[str] = Query(None, description="Page token for pagination"),
    page_size: int = Query(50, ge=1, le=100),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """List accessible files and atomically persist their current metadata."""
    with _google_file_operation(db):
        await require_feature(
            db,
            api_key.department_id,
            "cloud_integration",
            "Google Workspace Integration",
        )
        credential = await get_google_credential(api_key, db)
        async with _google_drive(credential) as integration:
            file_infos, next_token = await integration.list_files(
                folder_id=folder_id, page_token=page_token, page_size=page_size
            )
        files = [_track_google_file(db, credential, info) for info in file_infos]
        credential.last_sync_at = datetime.now(timezone.utc)
        db.commit()
        return GoogleFileListResponse(
            files=[
                GoogleFileResponse(
                    id=file.id,
                    provider_file_id=file.provider_file_id,
                    file_name=file.file_name,
                    file_type=file.file_type,
                    mime_type=file.mime_type,
                    file_size_bytes=file.file_size_bytes,
                    web_view_link=file.web_view_link,
                    last_scanned_at=file.last_scanned_at,
                    last_compliance_score=file.last_compliance_score,
                    needs_rescan=file.needs_rescan,
                )
                for file in files
            ],
            next_page_token=next_token,
            total_count=len(files),
        )


@router.get("/drive/folders")
async def list_google_drive_folders(
    parent_id: Optional[str] = Query(
        None, description="Parent folder ID (None for root)"
    ),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """List folders using the connected department's refreshed credential."""
    with _google_file_operation(db):
        await require_feature(
            db,
            api_key.department_id,
            "cloud_integration",
            "Google Workspace Integration",
        )
        credential = await get_google_credential(api_key, db)
        async with _google_drive(credential) as integration:
            folders = await integration.list_folders(parent_id=parent_id)
        return {
            "folders": [
                {
                    "id": folder.id,
                    "name": folder.name,
                    "parent_id": folder.parent_id,
                    "web_view_link": folder.web_view_link,
                }
                for folder in folders
            ]
        }


# ==================== Scanning Endpoints ====================


@router.post("/scan/file", response_model=ScanResultResponse)
async def scan_file(
    request: ScanFileRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Queue a scan for a file belonging to the active Google connection."""
    with _google_file_operation(db):
        await require_feature(
            db,
            api_key.department_id,
            "cloud_integration",
            "Google Workspace Integration",
        )
        credential = await get_google_credential(api_key, db)
        cloud_file = _google_tracked_file(db, credential, request.file_id)
        job = _enqueue_google_scan(db, credential, cloud_file)
        db.commit()
        return ScanResultResponse(
            file_id=cloud_file.id,
            job_id=job.id,
            scan_id=None,
            compliance_score=None,
            issues_found=0,
            status="queued",
            message=f"Scan job {job.id} queued for processing",
        )


@router.post("/scan/folder", response_model=Dict[str, Any])
async def scan_folder(
    request: ScanFolderRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Queue folder scans, honoring recursive traversal through the adapter."""
    with _google_file_operation(db):
        await require_feature(
            db,
            api_key.department_id,
            "cloud_integration",
            "Google Workspace Integration",
        )
        credential = await get_google_credential(api_key, db)
        async with _google_drive(credential) as integration:
            files = [
                info
                async for info in integration.list_all_files(
                    folder_id=request.folder_id, recursive=request.recursive
                )
            ]
        jobs = [
            _enqueue_google_scan(
                db, credential, _track_google_file(db, credential, info)
            )
            for info in files
        ]
        db.commit()
        return {
            "success": True,
            "folder_id": request.folder_id,
            "files_found": len(files),
            "jobs_created": len(jobs),
            "job_ids": [job.id for job in jobs],
            "message": f"Created {len(jobs)} scan jobs for folder",
        }


@router.post("/remediate", response_model=Dict[str, Any])
async def remediate_file(
    request: RemediateFileRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Queue remediation for a scanned file in the active Google connection."""
    with _google_file_operation(db):
        await require_feature(
            db,
            api_key.department_id,
            "cloud_integration",
            "Google Workspace Integration",
        )
        credential = await get_google_credential(api_key, db)
        cloud_file = _google_tracked_file(db, credential, request.file_id)
        if not cloud_file.last_scan_id:
            raise HTTPException(
                status_code=400,
                detail="File has not been scanned yet. Scan first before remediation.",
            )
        job = enqueue_cloud_job(
            db,
            department_id=api_key.department_id,
            job_type=CloudJobType.REMEDIATE.value,
            payload={
                "cloud_file_id": cloud_file.id,
                "credential_id": credential.id,
                "provider": CloudProvider.GOOGLE.value,
                "provider_file_id": cloud_file.provider_file_id,
                "scan_id": cloud_file.last_scan_id,
                "upload_as_new": request.upload_as_new,
            },
            dedupe_key=(
                f"remediate:google:{cloud_file.id}:{cloud_file.last_scan_id}:upload-new={str(request.upload_as_new).lower()}"
            ),
            cloud_file_id=cloud_file.id,
            credential_id=credential.id,
            provider=CloudProvider.GOOGLE.value,
            provider_file_id=cloud_file.provider_file_id,
            priority=3,
        )
        db.commit()
        return {
            "success": True,
            "job_id": job.id,
            "file_id": cloud_file.id,
            "status": "queued",
            "message": f"Remediation job {job.id} queued for processing",
        }


# ==================== Job Status Endpoints ====================


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Return one Google job in the authenticated department."""
    with _google_file_operation(db):
        job = (
            db.query(CloudJobQueue)
            .filter(
                CloudJobQueue.id == job_id,
                CloudJobQueue.department_id == api_key.department_id,
                CloudJobQueue.provider == CloudProvider.GOOGLE.value,
            )
            .first()
        )
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return _google_job_response(job)


@router.get("/jobs", response_model=List[JobStatusResponse])
async def list_jobs(
    status: Optional[Literal["pending", "processing", "completed", "failed"]] = Query(
        None
    ),
    limit: int = Query(20, ge=1, le=100),
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """List Google jobs for the department with validated status filtering."""
    with _google_file_operation(db):
        query = db.query(CloudJobQueue).filter(
            CloudJobQueue.department_id == api_key.department_id,
            CloudJobQueue.provider == CloudProvider.GOOGLE.value,
        )
        if status:
            query = query.filter(CloudJobQueue.status == status)
        jobs = query.order_by(CloudJobQueue.created_at.desc()).limit(limit).all()
        return [_google_job_response(job) for job in jobs]


# ==================== Account Management ====================


@router.get("/account")
async def get_google_account(
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Get connected Google account information."""
    oauth_service = GoogleOAuthService()
    account_info = oauth_service.get_account_info(
        department_id=api_key.department_id, db=db
    )
    if not account_info:
        raise HTTPException(status_code=404, detail="Google Workspace not connected")
    return account_info


# ==================== File Operations ====================


@router.get("/drive/files/{file_id}")
async def get_file_metadata(
    file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Get provider metadata using the authenticated department's connection."""
    with _google_file_operation(db):
        credential = await get_google_credential(api_key, db)
        async with _google_drive(credential) as integration:
            info = await integration.get_file_info(file_id)
        if info is None:
            raise HTTPException(status_code=404, detail="File not found")
        return {
            "id": info.id,
            "name": info.name,
            "mime_type": info.mime_type,
            "size": info.size_bytes,
            "created_time": info.created_at,
            "modified_time": info.modified_at,
            "web_view_link": info.web_view_link,
        }


@router.get("/drive/files/{file_id}/download")
async def download_file(
    file_id: str,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Return downloaded bytes, using and cleaning only a route-owned temp path."""
    with _google_file_operation(db):
        credential = await get_google_credential(api_key, db)
        with TemporaryDirectory(prefix="aelira-google-download-") as directory:
            owned_path = Path(directory) / "download"
            async with _google_drive(credential) as integration:
                result = await integration.download_file(
                    file_id, local_path=str(owned_path)
                )
            if not result.success:
                raise CloudIntegrationError("download_failed")
            if result.local_path != str(owned_path) or owned_path.is_symlink():
                raise RuntimeError("download_path_mismatch")
            content = owned_path.read_bytes()
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="download"'},
        )


# ==================== Document Export ====================


class ExportDocRequest(BaseModel):
    """Export a Google Doc as response bytes; server output paths are unsupported."""

    file_id: NonBlankIdentifier
    output_path: Optional[str] = Field(
        None, description="Unsupported legacy field; omit to download response bytes"
    )


class ExportSlidesRequest(ExportDocRequest):
    """Export Google Slides as PPTX response bytes."""


class ExportSheetsRequest(ExportDocRequest):
    """Export Google Sheets as XLSX response bytes."""


async def _export_google_document(
    request: ExportDocRequest,
    api_key: APIKey,
    db: Session,
    *,
    service_type,
    method: str,
    extension: str,
    mime_type: str,
):
    with _google_file_operation(db):
        if request.output_path is not None:
            raise HTTPException(
                status_code=422,
                detail="output_path is not supported; download the response content instead.",
            )
        credential = await get_google_credential(api_key, db)
        token = get_token_manager().decrypt_token(credential.access_token)
        service = service_type(credentials=Credentials(token=token))
        content = await run_in_threadpool(
            getattr(service, method), file_id=request.file_id
        )
        if not isinstance(content, bytes):
            raise TypeError("export_bytes_required")
        return Response(
            content=content,
            media_type=mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="export.{extension}"'
            },
        )


@router.post("/docs/export")
async def export_google_doc(
    request: ExportDocRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Download a Google Doc as DOCX bytes."""
    return await _export_google_document(
        request,
        api_key,
        db,
        service_type=GoogleDocsService,
        method="export_to_docx",
        extension="docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.post("/slides/export")
async def export_google_slides(
    request: ExportSlidesRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Download Google Slides as PPTX bytes."""
    return await _export_google_document(
        request,
        api_key,
        db,
        service_type=GoogleSlidesService,
        method="export_to_pptx",
        extension="pptx",
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@router.post("/sheets/export")
async def export_google_sheets(
    request: ExportSheetsRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Download Google Sheets as XLSX bytes."""
    return await _export_google_document(
        request,
        api_key,
        db,
        service_type=GoogleSheetsService,
        method="export_to_xlsx",
        extension="xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ==================== File Upload ====================


class UploadFileRequest(BaseModel):
    """Legacy local-path upload request; this operation is unsupported."""

    file_path: str = Field(..., description="Unsupported server-local path")
    parent_folder_id: Optional[str] = None
    file_id: Optional[str] = None


@router.post("/upload")
async def upload_file(
    request: UploadFileRequest,
    api_key: APIKey = Depends(get_current_api_key),
    db: Session = Depends(get_db_dependency),
):
    """Refuse unsupported local-path uploads without reading files or inventing IDs."""
    raise HTTPException(
        status_code=501,
        detail="Local-path upload is not supported. Use the approved remediation artifact workflow.",
    )


# ==================== Health Check ====================


@router.get("/health")
async def google_health():
    """Health check for Google integration."""
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    return {
        "status": (
            "healthy" if google_client_id and google_client_secret else "unconfigured"
        ),
        "service": "google-workspace",
        "configured": bool(google_client_id and google_client_secret),
        "features": [
            "oauth-connection",
            "drive-file-listing",
            "file-scanning",
            "auto-remediation",
        ],
    }
