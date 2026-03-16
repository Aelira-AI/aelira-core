"""
Upload Job Processor

Processes cloud file upload jobs.
Uploads remediated files back to cloud storage (Google Drive, OneDrive, SharePoint).
"""

import logging
from typing import Dict, Any
from sqlalchemy.orm import Session
from pathlib import Path

from ..db.models import (
    CloudOAuthCredentials,
    CloudProvider,
    CloudFile,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..integrations.google_workspace.google_drive import GoogleDriveIntegration
from ..integrations.microsoft_365.onedrive import OneDriveIntegration

logger = logging.getLogger(__name__)


async def process_upload_job(
    job_data: Dict[str, Any],
    db: Session,
) -> Dict[str, Any]:
    """
    Process an upload job.

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
        if not file_path or not Path(file_path).exists():
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
            "canvas": CloudProvider.CANVAS,
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

        # Upload file based on provider
        if provider == "google":
            result = await _upload_to_google(
                file_path=file_path,
                access_token=access_token,
                parent_folder_id=cloud_file.provider_parent_id,
                file_name=new_file_name,
                department_id=department_id,
            )
        elif provider == "microsoft":
            result = await _upload_to_microsoft(
                file_path=file_path,
                access_token=access_token,
                parent_folder_id=cloud_file.provider_parent_id,
                file_name=new_file_name,
                department_id=department_id,
            )
        elif provider == "canvas":
            result = await _upload_to_canvas(
                file_path=file_path,
                access_token=access_token,
                credential=credential,
                cloud_file=cloud_file,
                file_name=new_file_name,
            )
        elif provider == "blackboard":
            result = await _upload_to_blackboard(
                file_path=file_path,
                access_token=access_token,
                credential=credential,
                cloud_file=cloud_file,
                file_name=new_file_name,
            )
        else:
            return {
                "success": False,
                "uploaded": False,
                "error": f"Unsupported provider for upload: {provider}",
            }

        if result.get("success"):
            logger.info(
                f"Successfully uploaded file to {provider}: {result.get('new_file_name')} "
                f"-> {result.get('new_file_id')}"
            )

            # Update cloud file record to track remediated version
            cloud_file.has_remediated_version = True
            cloud_file.remediated_file_id = result.get("new_file_id")
            db.commit()

        return result

    except Exception as e:
        logger.error(f"Error processing upload job: {e}", exc_info=True)
        return {
            "success": False,
            "uploaded": False,
            "error": str(e),
        }


async def _upload_to_google(
    file_path: str,
    access_token: str,
    parent_folder_id: str = None,
    file_name: str = None,
    department_id: str = None,
) -> Dict[str, Any]:
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
            else:
                return {
                    "success": False,
                    "uploaded": False,
                    "error": result.error or "Upload failed",
                }

        finally:
            await integration.close()

    except Exception as e:
        logger.error(f"Error uploading to Google Drive: {e}", exc_info=True)
        return {
            "success": False,
            "uploaded": False,
            "error": str(e),
        }


async def _upload_to_microsoft(
    file_path: str,
    access_token: str,
    parent_folder_id: str = None,
    file_name: str = None,
    department_id: str = None,
) -> Dict[str, Any]:
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
                    "provider": "microsoft",
                }
            else:
                return {
                    "success": False,
                    "uploaded": False,
                    "error": result.error or "Upload failed",
                }

        finally:
            await integration.close()

    except Exception as e:
        logger.error(f"Error uploading to Microsoft OneDrive: {e}", exc_info=True)
        return {
            "success": False,
            "uploaded": False,
            "error": str(e),
        }


async def _upload_to_canvas(
    file_path: str,
    access_token: str,
    credential: Any,  # CloudOAuthCredentials
    cloud_file: Any,  # CloudFile
    file_name: str = None,
) -> Dict[str, Any]:
    """
    Upload file to Canvas LMS.

    Args:
        file_path: Local file path
        access_token: Canvas OAuth access token
        credential: Canvas OAuth credential (contains canvas_instance_url)
        cloud_file: CloudFile record (contains course_id in metadata)
        file_name: Name for uploaded file

    Returns:
        Dict with upload results
    """
    try:
        from ..integrations.canvas import CanvasAPIClient

        canvas_instance_url = credential.metadata.get("canvas_instance_url")
        if not canvas_instance_url:
            return {
                "success": False,
                "uploaded": False,
                "error": "Canvas instance URL not found in credential metadata",
            }

        # Get course_id from cloud file metadata
        course_id = cloud_file.metadata.get("course_id")
        if not course_id:
            return {
                "success": False,
                "uploaded": False,
                "error": "Canvas course ID not found in file metadata",
            }

        api_client = CanvasAPIClient(
            canvas_instance_url=canvas_instance_url,
            access_token=access_token,
            credential_id=credential.id,
        )

        try:
            # Upload file to Canvas course (creates new file with _remediated suffix)
            result = await api_client.upload_file(
                course_id=course_id,
                local_path=file_path,
                folder_id=cloud_file.provider_parent_id,
                file_name=file_name,
            )

            if result.success:
                return {
                    "success": True,
                    "uploaded": True,
                    "new_file_id": result.file_id,
                    "new_file_name": result.file_name,
                    "web_view_link": result.web_view_link,
                    "provider": "canvas",
                }
            else:
                return {
                    "success": False,
                    "uploaded": False,
                    "error": result.error or "Upload failed",
                }

        finally:
            await api_client.close()

    except Exception as e:
        logger.error(f"Error uploading to Canvas: {e}", exc_info=True)
        return {
            "success": False,
            "uploaded": False,
            "error": str(e),
        }


async def _upload_to_blackboard(
    file_path: str,
    access_token: str,
    credential: Any,  # CloudOAuthCredentials
    cloud_file: Any,  # CloudFile
    file_name: str = None,
) -> Dict[str, Any]:
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

        blackboard_instance_url = credential.metadata.get("blackboard_instance_url")
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
            else:
                return {
                    "success": False,
                    "uploaded": False,
                    "error": result.error or "Upload failed",
                }

        finally:
            await api_client.close()

    except Exception as e:
        logger.error(f"Error uploading to Blackboard: {e}", exc_info=True)
        return {
            "success": False,
            "uploaded": False,
            "error": str(e),
        }


async def handle_upload_job(
    job: Any,  # CloudJobQueue
    db: Session,
    token_manager: Any,  # OAuthTokenManager
) -> Dict[str, Any]:
    """
    Job handler for upload jobs (matches JobProcessor signature).

    Args:
        job: CloudJobQueue instance
        db: Database session
        token_manager: OAuth token manager (not used, but required by signature)

    Returns:
        Upload results
    """
    return await process_upload_job(job.job_data, db)


__all__ = ["process_upload_job", "handle_upload_job"]
