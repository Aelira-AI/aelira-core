"""
Cloud Sync Job Handler

Synchronizes files from cloud storage (Google Drive, OneDrive) to our database.
Discovers new files, detects changes, and queues scan jobs for modified files.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any
from sqlalchemy.orm import Session
import uuid

from ..db.models import (
    CloudJobQueue,
    CloudOAuthCredentials,
    CloudFile,
    CloudProvider,
    CloudJobType,
    CloudJobStatus,
    CloudSyncFolder,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..integrations.google_workspace.google_drive import GoogleDriveIntegration
from ..integrations.microsoft_365.onedrive import OneDriveIntegration

logger = logging.getLogger(__name__)


class CloudSyncJob:
    """
    Cloud file synchronization job.

    Discovers files from cloud storage and syncs metadata to our database.
    """

    def __init__(
        self,
        credential: CloudOAuthCredentials,
        token_manager: OAuthTokenManager,
    ):
        """
        Initialize sync job.

        Args:
            credential: OAuth credentials for cloud provider
            token_manager: Token manager for decryption/refresh
        """
        self.credential = credential
        self.token_manager = token_manager

    async def run(self, db: Session, folder_id: str = None) -> Dict[str, Any]:
        """
        Run the sync job.

        Args:
            db: Database session
            folder_id: Optional folder ID to sync (None for entire drive)

        Returns:
            Sync results including files discovered and updated
        """
        if self.credential.provider == CloudProvider.GOOGLE.value:
            return await self._sync_google(db, folder_id)
        elif self.credential.provider == CloudProvider.MICROSOFT.value:
            return await self._sync_microsoft(db, folder_id)
        else:
            raise ValueError(f"Unsupported provider: {self.credential.provider}")

    async def _sync_google(self, db: Session, folder_id: str = None) -> Dict[str, Any]:
        """Sync files from Google Drive."""
        # Refresh token using distributed lock (prevents race with concurrent jobs)
        access_token = await self.token_manager.refresh_if_expired(self.credential, db)

        integration = GoogleDriveIntegration(
            access_token=access_token,
            department_id=self.credential.department_id,
        )

        try:
            results = {
                "provider": "google",
                "files_discovered": 0,
                "files_updated": 0,
                "files_unchanged": 0,
                "scan_jobs_created": 0,
            }

            # Paginate through all files
            page_token = None
            while True:
                file_infos, next_token = await integration.list_files(
                    folder_id=folder_id,
                    page_token=page_token,
                    page_size=100,
                )

                for file_info in file_infos:
                    # Check if file exists in our database
                    existing = (
                        db.query(CloudFile)
                        .filter(
                            CloudFile.department_id == self.credential.department_id,
                            CloudFile.provider == CloudProvider.GOOGLE.value,
                            CloudFile.provider_file_id == file_info.provider_file_id,
                        )
                        .first()
                    )

                    if existing:
                        # Check if file changed
                        if file_info.version != existing.provider_version:
                            existing.file_name = file_info.name
                            existing.provider_version = file_info.version
                            existing.provider_modified_at = file_info.modified_time
                            existing.needs_rescan = True
                            results["files_updated"] += 1

                            # Create scan job for changed file (if none pending)
                            if not self._has_pending_scan(db, existing.id):
                                scan_job = CloudJobQueue(
                                    id=str(uuid.uuid4()),
                                    department_id=self.credential.department_id,
                                    job_type=CloudJobType.SCAN.value,
                                    cloud_file_id=existing.id,
                                    credential_id=self.credential.id,
                                    provider=CloudProvider.GOOGLE.value,
                                    provider_file_id=file_info.provider_file_id,
                                    status=CloudJobStatus.PENDING.value,
                                    priority=5,
                                )
                                db.add(scan_job)
                                results["scan_jobs_created"] += 1
                        else:
                            results["files_unchanged"] += 1
                    else:
                        # New file - create tracking record
                        cloud_file = CloudFile(
                            id=str(uuid.uuid4()),
                            department_id=self.credential.department_id,
                            credential_id=self.credential.id,
                            provider=CloudProvider.GOOGLE.value,
                            provider_file_id=file_info.provider_file_id,
                            provider_parent_id=(
                                file_info.parents[0] if file_info.parents else None
                            ),
                            file_name=file_info.name,
                            file_type=self._get_file_type(file_info.name),
                            mime_type=file_info.mime_type,
                            file_size_bytes=file_info.size,
                            web_view_link=file_info.web_view_link,
                            provider_version=file_info.version,
                            provider_modified_at=file_info.modified_time,
                            needs_rescan=True,
                        )
                        db.add(cloud_file)
                        db.flush()

                        results["files_discovered"] += 1

                        # Create scan job for new file
                        scan_job = CloudJobQueue(
                            id=str(uuid.uuid4()),
                            department_id=self.credential.department_id,
                            job_type=CloudJobType.SCAN.value,
                            cloud_file_id=cloud_file.id,
                            credential_id=self.credential.id,
                            provider=CloudProvider.GOOGLE.value,
                            provider_file_id=file_info.provider_file_id,
                            status=CloudJobStatus.PENDING.value,
                            priority=5,
                        )
                        db.add(scan_job)
                        results["scan_jobs_created"] += 1

                db.commit()

                if not next_token:
                    break
                page_token = next_token

            # Update credential last sync time
            self.credential.last_sync_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(
                f"Google sync complete: {results['files_discovered']} new, "
                f"{results['files_updated']} updated, {results['scan_jobs_created']} jobs created"
            )

            return results

        finally:
            await integration.close()

    async def _sync_microsoft(
        self, db: Session, folder_id: str = None
    ) -> Dict[str, Any]:
        """Sync files from OneDrive/SharePoint."""
        # Refresh token using distributed lock (prevents race with concurrent jobs)
        access_token = await self.token_manager.refresh_if_expired(self.credential, db)

        integration = OneDriveIntegration(
            access_token=access_token,
            department_id=self.credential.department_id,
        )

        try:
            results = {
                "provider": "microsoft",
                "files_discovered": 0,
                "files_updated": 0,
                "files_unchanged": 0,
                "scan_jobs_created": 0,
            }

            # Paginate through all files
            page_token = None
            while True:
                file_infos, next_token = await integration.list_files(
                    folder_id=folder_id,
                    page_token=page_token,
                    page_size=100,
                )

                for file_info in file_infos:
                    # Check if file exists in our database
                    existing = (
                        db.query(CloudFile)
                        .filter(
                            CloudFile.department_id == self.credential.department_id,
                            CloudFile.provider == CloudProvider.MICROSOFT.value,
                            CloudFile.provider_file_id == file_info.provider_file_id,
                        )
                        .first()
                    )

                    if existing:
                        # Check if file changed
                        if file_info.version != existing.provider_version:
                            existing.file_name = file_info.name
                            existing.provider_version = file_info.version
                            existing.provider_modified_at = file_info.modified_time
                            existing.needs_rescan = True
                            results["files_updated"] += 1

                            # Create scan job for changed file (if none pending)
                            if not self._has_pending_scan(db, existing.id):
                                scan_job = CloudJobQueue(
                                    id=str(uuid.uuid4()),
                                    department_id=self.credential.department_id,
                                    job_type=CloudJobType.SCAN.value,
                                    cloud_file_id=existing.id,
                                    credential_id=self.credential.id,
                                    provider=CloudProvider.MICROSOFT.value,
                                    provider_file_id=file_info.provider_file_id,
                                    status=CloudJobStatus.PENDING.value,
                                    priority=5,
                                )
                                db.add(scan_job)
                                results["scan_jobs_created"] += 1
                        else:
                            results["files_unchanged"] += 1
                    else:
                        # New file - create tracking record
                        cloud_file = CloudFile(
                            id=str(uuid.uuid4()),
                            department_id=self.credential.department_id,
                            credential_id=self.credential.id,
                            provider=CloudProvider.MICROSOFT.value,
                            provider_file_id=file_info.provider_file_id,
                            provider_parent_id=(
                                file_info.parents[0] if file_info.parents else None
                            ),
                            file_name=file_info.name,
                            file_type=self._get_file_type(file_info.name),
                            mime_type=file_info.mime_type,
                            file_size_bytes=file_info.size,
                            web_view_link=file_info.web_view_link,
                            provider_version=file_info.version,
                            provider_modified_at=file_info.modified_time,
                            needs_rescan=True,
                        )
                        db.add(cloud_file)
                        db.flush()

                        results["files_discovered"] += 1

                        # Create scan job for new file
                        scan_job = CloudJobQueue(
                            id=str(uuid.uuid4()),
                            department_id=self.credential.department_id,
                            job_type=CloudJobType.SCAN.value,
                            cloud_file_id=cloud_file.id,
                            credential_id=self.credential.id,
                            provider=CloudProvider.MICROSOFT.value,
                            provider_file_id=file_info.provider_file_id,
                            status=CloudJobStatus.PENDING.value,
                            priority=5,
                        )
                        db.add(scan_job)
                        results["scan_jobs_created"] += 1

                db.commit()

                if not next_token:
                    break
                page_token = next_token

            # Update credential last sync time
            self.credential.last_sync_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(
                f"Microsoft sync complete: {results['files_discovered']} new, "
                f"{results['files_updated']} updated, {results['scan_jobs_created']} jobs created"
            )

            return results

        finally:
            await integration.close()

    def _has_pending_scan(self, db: Session, cloud_file_id: str) -> bool:
        """Check if a pending scan job already exists for this file."""
        return (
            db.query(CloudJobQueue)
            .filter(
                CloudJobQueue.cloud_file_id == cloud_file_id,
                CloudJobQueue.job_type == CloudJobType.SCAN.value,
                CloudJobQueue.status == CloudJobStatus.PENDING.value,
            )
            .first()
        ) is not None

    def _get_file_type(self, file_name: str) -> str:
        """Extract file type from filename."""
        if not file_name or "." not in file_name:
            return "unknown"
        return file_name.rsplit(".", 1)[-1].lower()


async def handle_sync_job(
    job: CloudJobQueue,
    db: Session,
    token_manager: OAuthTokenManager,
) -> Dict[str, Any]:
    """
    Job handler for cloud sync jobs.

    PRIVACY-CRITICAL: Only syncs folders that have been explicitly selected in cloud_sync_folders table.
    If no folders are selected, the sync is skipped (prevents syncing entire drives).

    Args:
        job: The job to process
        db: Database session
        token_manager: OAuth token manager

    Returns:
        Sync results including folders processed and files discovered
    """
    # Get credential
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(CloudOAuthCredentials.id == job.credential_id)
        .first()
    )

    if not credential:
        raise ValueError(f"Credential not found: {job.credential_id}")

    # Query selected sync folders for this credential
    sync_folders = (
        db.query(CloudSyncFolder)
        .filter(
            CloudSyncFolder.credential_id == credential.id,
            CloudSyncFolder.is_active,
        )
        .all()
    )

    # PRIVACY CHECK: If no folders selected, skip sync
    if not sync_folders:
        logger.warning(
            f"No sync folders selected for credential {credential.id}. "
            f"Skipping sync to prevent syncing entire drive (privacy-conscious)."
        )
        return {
            "provider": credential.provider,
            "folders_processed": 0,
            "files_discovered": 0,
            "files_updated": 0,
            "files_unchanged": 0,
            "scan_jobs_created": 0,
            "message": "No folders selected for sync. Please select folders first.",
        }

    # Aggregate results across all folders
    total_results = {
        "provider": credential.provider,
        "folders_processed": 0,
        "files_discovered": 0,
        "files_updated": 0,
        "files_unchanged": 0,
        "scan_jobs_created": 0,
        "folder_details": [],
    }

    sync_job = CloudSyncJob(credential=credential, token_manager=token_manager)

    # Sync each selected folder
    for sync_folder in sync_folders:
        logger.info(
            f"Syncing folder: {sync_folder.folder_name} "
            f"(id={sync_folder.provider_folder_id}, "
            f"subfolders={sync_folder.sync_subfolders})"
        )

        try:
            folder_results = await sync_job.run(
                db, folder_id=sync_folder.provider_folder_id
            )

            # Aggregate results
            total_results["files_discovered"] += folder_results.get(
                "files_discovered", 0
            )
            total_results["files_updated"] += folder_results.get("files_updated", 0)
            total_results["files_unchanged"] += folder_results.get("files_unchanged", 0)
            total_results["scan_jobs_created"] += folder_results.get(
                "scan_jobs_created", 0
            )
            total_results["folders_processed"] += 1

            total_results["folder_details"].append(
                {
                    "folder_id": sync_folder.provider_folder_id,
                    "folder_name": sync_folder.folder_name,
                    "files_discovered": folder_results.get("files_discovered", 0),
                    "files_updated": folder_results.get("files_updated", 0),
                    "scan_jobs_created": folder_results.get("scan_jobs_created", 0),
                }
            )

        except Exception as e:
            logger.error(
                f"Failed to sync folder {sync_folder.folder_name} "
                f"(id={sync_folder.provider_folder_id}): {e}"
            )
            total_results["folder_details"].append(
                {
                    "folder_id": sync_folder.provider_folder_id,
                    "folder_name": sync_folder.folder_name,
                    "error": str(e),
                }
            )

    logger.info(
        f"Sync complete for {credential.provider}: "
        f"{total_results['folders_processed']} folders, "
        f"{total_results['files_discovered']} new files, "
        f"{total_results['files_updated']} updated files, "
        f"{total_results['scan_jobs_created']} scan jobs created"
    )

    return total_results
