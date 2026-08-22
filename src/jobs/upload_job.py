"""
Upload Job Processor

Processes cloud file upload jobs.
Uploads remediated files back to cloud storage (Google Drive, OneDrive, SharePoint).
"""

import asyncio
import logging
import os
import shutil
import tempfile
import httpx
from typing import Dict, Any
from sqlalchemy.orm import Session
from pathlib import Path

from ..db.models import (
    CloudOAuthCredentials,
    CloudProvider,
    CloudFile,
    RemediationArtifact,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..integrations.google_workspace.google_drive import GoogleDriveIntegration
from ..integrations.microsoft_365.onedrive import OneDriveIntegration
from .contracts import JobFailure, LostJobOwnership
from ..integrations.cloud_base import (
    CloudAuthError,
    CloudNotFoundError,
    CloudRateLimitError,
)
from ..services.remediation_artifact_service import (
    ArtifactError,
    RemediationArtifactService,
)

logger = logging.getLogger(__name__)


class IndeterminateProviderOutcome(RuntimeError):
    """The request body may have been accepted without an exact response."""


def classify_upload_exception(exc: Exception, *, provider: str) -> JobFailure:
    """Classify failures without persisting exception text, URLs, or paths."""
    details: dict[str, Any] = {"provider": provider}
    if isinstance(exc, IndeterminateProviderOutcome):
        return JobFailure.indeterminate(
            "provider_outcome_indeterminate", {**details, "retry_safe": False}
        )
    if isinstance(exc, CloudRateLimitError):
        return JobFailure.retryable("provider_rate_limited", details)
    if isinstance(exc, CloudAuthError):
        return JobFailure.deterministic("provider_auth_failed", details)
    if isinstance(exc, CloudNotFoundError):
        return JobFailure.deterministic("provider_input_invalid", details)
    if isinstance(exc, httpx.TimeoutException):
        return JobFailure.retryable("provider_timeout", details)
    if isinstance(exc, httpx.TransportError):
        return JobFailure.retryable("provider_network_error", details)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        retry_after = exc.response.headers.get("retry-after")
        if status == 429 or status >= 500 or status in (408, 425):
            try:
                details["retry_after"] = max(0, min(3600, int(retry_after or 0)))
            except ValueError:
                details["retry_after"] = 0
            code = "provider_rate_limited" if status == 429 else "provider_unavailable"
            return JobFailure.retryable(code, details)
        if status == 401:
            return JobFailure.deterministic("provider_auth_failed", details)
        if status == 403:
            return JobFailure.deterministic("provider_permission_denied", details)
        if 400 <= status < 500:
            return JobFailure.deterministic("provider_input_invalid", details)
    return JobFailure.indeterminate(
        "provider_failure_unclassified", {**details, "retry_safe": False}
    )


def _failure_from_provider_result(result: Any, *, provider: str) -> JobFailure:
    """Preserve structured adapter truth; an untyped failure is ambiguous."""
    kind = getattr(result, "failure_kind", None)
    status = getattr(result, "status_code", None)
    details: dict[str, Any] = {"provider": provider}
    retry_after = getattr(result, "retry_after", None)
    if isinstance(retry_after, int):
        details["retry_after"] = max(0, min(3600, retry_after))
    if kind == "retryable":
        code = "provider_rate_limited" if status == 429 else "provider_unavailable"
        return JobFailure.retryable(code, details)
    if kind == "deterministic":
        code = {
            401: "provider_auth_failed",
            403: "provider_permission_denied",
        }.get(status, "provider_upload_rejected")
        return JobFailure.deterministic(code, details)
    return JobFailure.indeterminate(
        "provider_outcome_indeterminate", {**details, "retry_safe": False}
    )


async def process_upload_job(
    job_data: Dict[str, Any],
    db: Session,
    *,
    assert_owned: Any = None,
    begin_external_effect: Any = None,
) -> Dict[str, Any] | JobFailure:
    """Resolve an approved managed artifact and upload its verified bytes."""
    if "file_path" in job_data or not isinstance(job_data.get("artifact_id"), str):
        return JobFailure.deterministic("managed_artifact_id_required")

    artifact = db.get(RemediationArtifact, job_data["artifact_id"])
    if artifact is None:
        return {
            "success": False,
            "uploaded": False,
            "error": "managed_artifact_unavailable",
        }
    if (
        artifact.department_id != job_data.get("department_id")
        or artifact.cloud_file_id != job_data.get("cloud_file_id")
        or artifact.provider != job_data.get("provider")
    ):
        return {
            "success": False,
            "uploaded": False,
            "error": "managed_artifact_unavailable",
        }

    temp_path: str | None = None
    try:
        service = RemediationArtifactService.from_settings()
        with service.open_verified(
            db,
            artifact,
            department_id=artifact.department_id,
            scan_id=artifact.scan_id,
            cloud_file_id=artifact.cloud_file_id,
            require_approved=True,
            approval_checksum=job_data.get("artifact_checksum"),
        ) as stream:
            with tempfile.NamedTemporaryFile(
                prefix="aelira-upload-",
                suffix=Path(artifact.filename).suffix,
                delete=False,
            ) as temporary:
                temp_path = temporary.name
                shutil.copyfileobj(stream, temporary)
        internal_data = dict(job_data)
        internal_data.pop("artifact_id", None)
        internal_data.pop("artifact_checksum", None)
        internal_data["file_path"] = temp_path
        return await _process_upload_path(
            internal_data,
            db,
            assert_owned=assert_owned,
            begin_external_effect=begin_external_effect,
        )
    except (ArtifactError, OSError):
        db.rollback()
        return {
            "success": False,
            "uploaded": False,
            "error": "managed_artifact_unavailable",
        }
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


async def _process_upload_path(
    job_data: Dict[str, Any],
    db: Session,
    *,
    assert_owned: Any = None,
    begin_external_effect: Any = None,
) -> Dict[str, Any] | JobFailure:
    """
    Upload a service-materialized managed artifact path.

    Uploads a file to cloud storage, creating a new version with "_remediated" suffix.

    Args:
        job_data: Job data including:
            - id: Job ID
            - file_path: Local file path to upload
            - cloud_file_id: Cloud file ID to get parent folder
            - department_id: Department ID (for fetching credentials)
            - provider: Cloud provider ("google" or "microsoft")
            - create_new_version: Whether to create new file (default: True)
        db: Database session

    Returns:
        Dict with:
            - success: bool
            - uploaded: bool
            - new_file_id: str (cloud file ID)
            - new_file_name: str (uploaded file name)
            - web_view_link: str (link to view file)
            - error: str (if failed)
    """
    file_path = job_data.get("file_path")
    cloud_file_id = job_data.get("cloud_file_id")
    department_id = job_data.get("department_id")
    provider = job_data.get("provider")
    create_new_version = job_data.get("create_new_version", True)

    try:
        logger.info(
            f"Processing upload job {job_data.get('id')} to {provider} for file {cloud_file_id}"
        )

        # Validate file exists
        if not file_path or not await asyncio.to_thread(Path(file_path).exists):
            return {
                "success": False,
                "uploaded": False,
                "error": f"File not found: {file_path}",
            }

        # Get cloud file record to find parent folder
        cloud_file = db.query(CloudFile).filter(CloudFile.id == cloud_file_id).first()
        if not cloud_file:
            return {
                "success": False,
                "uploaded": False,
                "error": f"Cloud file record not found: {cloud_file_id}",
            }

        # Get OAuth credentials for provider
        provider_map = {
            "google": CloudProvider.GOOGLE,
            "microsoft": CloudProvider.MICROSOFT,
            "blackboard": CloudProvider.BLACKBOARD,
        }
        provider_enum = provider_map.get(provider)

        if not provider_enum:
            return {
                "success": False,
                "uploaded": False,
                "error": f"Unsupported provider: {provider}",
            }

        credential = (
            db.query(CloudOAuthCredentials)
            .filter(
                CloudOAuthCredentials.id == cloud_file.credential_id,
                CloudOAuthCredentials.department_id == department_id,
                CloudOAuthCredentials.provider == provider_enum.value,
                CloudOAuthCredentials.is_active,
            )
            .first()
        )

        if not credential:
            return {
                "success": False,
                "uploaded": False,
                "error": f"No active {provider} credentials found for department",
            }

        # Refresh token if needed (with distributed lock to prevent races)
        token_manager = OAuthTokenManager()
        access_token = await token_manager.refresh_if_expired(credential, db)

        # Generate new file name with "_remediated" suffix
        original_path = Path(cloud_file.file_name)
        if create_new_version:
            new_file_name = f"{original_path.stem}_remediated{original_path.suffix}"
        else:
            new_file_name = original_path.name

        # The durable checkpoint commits in a separate short transaction. It must
        # be the final operation before any provider can receive request bytes.
        if assert_owned is not None:
            await assert_owned()
        if begin_external_effect is None:
            return JobFailure.deterministic("external_effect_checkpoint_unavailable")
        external_effect_token = await begin_external_effect()
        if provider == "google":
            result = await _upload_to_google(
                file_path=file_path,
                access_token=access_token,
                parent_folder_id=cloud_file.provider_parent_id,
                file_name=new_file_name,
                department_id=department_id,
                external_effect_token=external_effect_token,
            )
        elif provider == "microsoft":
            result = await _upload_to_microsoft(
                file_path=file_path,
                access_token=access_token,
                parent_folder_id=cloud_file.provider_parent_id,
                file_name=new_file_name,
                department_id=department_id,
                external_effect_token=external_effect_token,
            )
        elif provider == "blackboard":
            result = await _upload_to_blackboard(
                file_path=file_path,
                access_token=access_token,
                credential=credential,
                cloud_file=cloud_file,
                file_name=new_file_name,
                external_effect_token=external_effect_token,
            )
        else:
            return {
                "success": False,
                "uploaded": False,
                "error": f"Unsupported provider for upload: {provider}",
            }

        if isinstance(result, JobFailure):
            return result
        if result.get("success"):
            logger.info(
                f"Successfully uploaded file to {provider}: {result.get('new_file_name')} "
                f"-> {result.get('new_file_id')}"
            )

            # Update cloud file record to track remediated version
            cloud_file.has_remediated_version = True
            cloud_file.remediation_origin = "manual"
            cloud_file.remediated_file_id = result.get("new_file_id")
            if assert_owned is not None:
                await assert_owned()
            db.commit()

        return result

    except LostJobOwnership:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.warning(
            "Upload provider operation failed",
            extra={"provider": provider, "error_type": type(exc).__name__},
        )
        return classify_upload_exception(exc, provider=str(provider or "unknown"))


async def _upload_to_google(
    file_path: str,
    access_token: str,
    parent_folder_id: str = None,
    file_name: str = None,
    department_id: str = None,
    external_effect_token: str | None = None,
) -> Dict[str, Any] | JobFailure:
    """
    Upload file to Google Drive.

    Args:
        file_path: Local file path
        access_token: Google OAuth access token
        parent_folder_id: Parent folder ID
        file_name: Name for uploaded file
        department_id: Department ID

    Returns:
        Dict with upload results
    """
    try:
        integration = GoogleDriveIntegration(
            credential_id=department_id,
            access_token=access_token,
        )

        try:
            # Use existing upload_file method
            result = await integration.upload_file(
                local_path=file_path,
                folder_id=parent_folder_id,
                file_name=file_name,
            )

            if result.success:
                return {
                    "success": True,
                    "uploaded": True,
                    "new_file_id": result.file_id,
                    "new_file_name": file_name,
                    "web_view_link": result.web_view_link,
                    "provider": "google",
                }
            return _failure_from_provider_result(result, provider="google")

        finally:
            await integration.close()

    except Exception as exc:
        return classify_upload_exception(exc, provider="google")


async def _upload_to_microsoft(
    file_path: str,
    access_token: str,
    parent_folder_id: str = None,
    file_name: str = None,
    department_id: str = None,
    external_effect_token: str | None = None,
) -> Dict[str, Any] | JobFailure:
    """
    Upload file to Microsoft OneDrive/SharePoint.

    Args:
        file_path: Local file path
        access_token: Microsoft OAuth access token
        parent_folder_id: Parent folder ID
        file_name: Name for uploaded file
        department_id: Department ID

    Returns:
        Dict with upload results
    """
    try:
        integration = OneDriveIntegration(
            access_token=access_token,
            credential_id=department_id,
        )

        try:
            # Use existing upload_file method
            result = await integration.upload_file(
                local_path=file_path,
                folder_id=parent_folder_id,
                file_name=file_name,
            )

            if result.success:
                return {
                    "success": True,
                    "uploaded": True,
                    "new_file_id": result.file_id,
                    "new_file_name": file_name,
                    "web_view_link": result.web_view_link,
                    "provider": "microsoft",
                }
            return _failure_from_provider_result(result, provider="microsoft")

        finally:
            await integration.close()

    except Exception as exc:
        return classify_upload_exception(exc, provider="microsoft")


async def _upload_to_blackboard(
    file_path: str,
    access_token: str,
    credential: Any,  # CloudOAuthCredentials
    cloud_file: Any,  # CloudFile
    file_name: str = None,
    external_effect_token: str | None = None,
) -> Dict[str, Any] | JobFailure:
    """
    Upload file to Blackboard Learn.

    Args:
        file_path: Local file path
        access_token: Blackboard OAuth access token
        credential: Blackboard OAuth credential (contains blackboard_instance_url)
        cloud_file: CloudFile record (contains course_id in metadata)
        file_name: Name for uploaded file

    Returns:
        Dict with upload results
    """
    try:
        from ..integrations.blackboard import BlackboardAPIClient

        blackboard_instance_url = credential.provider_metadata.get(
            "blackboard_instance_url"
        )
        if not blackboard_instance_url:
            return {
                "success": False,
                "uploaded": False,
                "error": "Blackboard instance URL not found in credential metadata",
            }

        # Get course_id from cloud file metadata
        course_id = cloud_file.metadata.get("course_id")
        if not course_id:
            return {
                "success": False,
                "uploaded": False,
                "error": "Blackboard course ID not found in file metadata",
            }

        api_client = BlackboardAPIClient(
            blackboard_instance_url=blackboard_instance_url,
            access_token=access_token,
            credential_id=credential.id,
        )

        try:
            # Upload file to Blackboard course (creates new content item with _remediated suffix)
            result = await api_client.upload_file(
                course_id=course_id,
                local_path=file_path,
                parent_content_id=cloud_file.provider_parent_id,
                title=file_name,
            )

            if result.success:
                return {
                    "success": True,
                    "uploaded": True,
                    "new_file_id": result.file_id,
                    "new_file_name": result.file_name,
                    "web_view_link": result.web_view_link,
                    "provider": "blackboard",
                }
            return _failure_from_provider_result(result, provider="blackboard")

        finally:
            await api_client.close()

    except Exception as exc:
        return classify_upload_exception(exc, provider="blackboard")


async def handle_upload_job(
    job: Any,  # CloudJobQueue
    db: Session,
    token_manager: Any,  # OAuthTokenManager
) -> Dict[str, Any] | JobFailure:
    """
    Job handler for upload jobs (matches JobProcessor signature).

    Args:
        job: CloudJobQueue instance
        db: Database session
        token_manager: OAuth token manager (not used, but required by signature)

    Returns:
        Upload results
    """
    # Queue input is immutable. Filesystem paths are resolved only inside
    # process_upload_job from a managed artifact identifier.
    job_data: Dict[str, Any] = {
        "id": job.id,
        "cloud_file_id": job.cloud_file_id,
        "department_id": job.department_id,
        "provider": job.provider,
    }
    payload = job.payload if isinstance(getattr(job, "payload", None), dict) else {}
    if any(
        payload.get(field) not in (None, getattr(job, field))
        for field in ("cloud_file_id", "department_id", "provider")
    ):
        return {
            "success": False,
            "uploaded": False,
            "error": "invalid_job_scope",
        }
    for field in ("artifact_id", "artifact_checksum"):
        if field in payload:
            job_data[field] = payload[field]
    return await process_upload_job(
        job_data,
        db,
        assert_owned=getattr(job, "_assert_owned", None),
        begin_external_effect=getattr(job, "_begin_external_effect", None),
    )


__all__ = ["process_upload_job", "handle_upload_job"]
