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
from ..services.job_enqueue_service import enqueue_cloud_job
from .contracts import JobFailure, LostJobOwnership

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
        assert_owned: Any = None,
    ):
        """
        Initialize sync job.

        Args:
            credential: OAuth credentials for cloud provider
            token_manager: Token manager for decryption/refresh
        """
        self.credential = credential
        self.token_manager = token_manager
        self.assert_owned = assert_owned

    async def _checkpoint(self) -> None:
        if self.assert_owned is not None:
            await self.assert_owned()

    async def run(
        self, db: Session, folder_id: str = None, *, recursive: bool = True
    ) -> Dict[str, Any]:
        """
        Run the sync job.

        Args:
            db: Database session
            folder_id: Optional folder ID to sync (None for entire drive)

        Returns:
            Sync results including files discovered and updated
        """
        if self.credential.provider == CloudProvider.GOOGLE.value:
            return await self._sync_google(db, folder_id, recursive=recursive)
        elif self.credential.provider == CloudProvider.MICROSOFT.value:
            return await self._sync_microsoft(db, folder_id, recursive=recursive)
        else:
            raise ValueError(f"Unsupported provider: {self.credential.provider}")

    async def _sync_google(
        self, db: Session, folder_id: str = None, *, recursive: bool = True
    ) -> Dict[str, Any]:
        """Sync files from Google Drive."""
        # Refresh token using distributed lock (prevents race with concurrent jobs)
        access_token = await self.token_manager.refresh_if_expired(self.credential, db)

        integration = GoogleDriveIntegration(
            access_token=access_token,
            credential_id=self.credential.id,
        )

        try:
            results = {
                "provider": "google",
                "files_discovered": 0,
                "files_updated": 0,
                "files_unchanged": 0,
                "scan_jobs_created": 0,
            }

            pending_folders = [folder_id]
            visited_folder_ids = {folder_id} if folder_id is not None else set()
            current_folder_id = pending_folders.pop(0)
            page_token = None
            while True:
                file_infos, next_token = await integration.list_files(
                    folder_id=current_folder_id,
                    page_token=page_token,
                    page_size=100,
                )

                for file_info in file_infos:
                    if file_info.is_folder:
                        continue
                    # Check if file exists in our database
                    existing = (
                        db.query(CloudFile)
                        .filter(
                            CloudFile.department_id == self.credential.department_id,
                            CloudFile.provider == CloudProvider.GOOGLE.value,
                            CloudFile.provider_file_id == file_info.id,
                        )
                        .first()
                    )

                    if existing:
                        # Check if file changed
                        if file_info.version != existing.provider_version:
                            existing.file_name = file_info.name
                            existing.provider_version = file_info.version
                            existing.provider_modified_at = file_info.modified_at
                            existing.needs_rescan = True
                            results["files_updated"] += 1

                            # Create scan job for changed file (if none pending)
                            if self._enqueue_scan(
                                db,
                                cloud_file=existing,
                                provider=CloudProvider.GOOGLE.value,
                                provider_file_id=file_info.id,
                            ):
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
                            provider_file_id=file_info.id,
                            provider_parent_id=file_info.parent_id,
                            file_name=file_info.name,
                            file_type=self._get_file_type(file_info.name),
                            mime_type=file_info.mime_type,
                            file_size_bytes=file_info.size_bytes,
                            web_view_link=file_info.web_view_link,
                            provider_version=file_info.version,
                            provider_modified_at=file_info.modified_at,
                            needs_rescan=True,
                        )
                        db.add(cloud_file)
                        db.flush()

                        results["files_discovered"] += 1

                        # Create scan job for new file
                        if self._enqueue_scan(
                            db,
                            cloud_file=cloud_file,
                            provider=CloudProvider.GOOGLE.value,
                            provider_file_id=file_info.id,
                        ):
                            results["scan_jobs_created"] += 1

                await self._checkpoint()
                db.commit()

                if next_token:
                    page_token = next_token
                    continue
                if recursive:
                    child_folders = await integration.list_folders(current_folder_id)
                    for child in child_folders:
                        if child.id not in visited_folder_ids:
                            visited_folder_ids.add(child.id)
                            pending_folders.append(child.id)
                if pending_folders:
                    current_folder_id = pending_folders.pop(0)
                    page_token = None
                    continue
                break

            # Update credential last sync time
            self.credential.last_sync_at = datetime.now(timezone.utc)
            await self._checkpoint()
            db.commit()

            logger.info(
                f"Google sync complete: {results['files_discovered']} new, "
                f"{results['files_updated']} updated, {results['scan_jobs_created']} jobs created"
            )

            return results

        finally:
            await integration.close()

    async def _sync_microsoft(
        self, db: Session, folder_id: str = None, *, recursive: bool = True
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

            pending_folders = [folder_id]
            visited_folder_ids = {folder_id} if folder_id is not None else set()
            current_folder_id = pending_folders.pop(0)
            page_token = None
            while True:
                file_infos, next_token = await integration.list_files(
                    folder_id=current_folder_id,
                    page_token=page_token,
                    page_size=100,
                )

                for file_info in file_infos:
                    if file_info.is_folder:
                        continue
                    # Check if file exists in our database
                    existing = (
                        db.query(CloudFile)
                        .filter(
                            CloudFile.department_id == self.credential.department_id,
                            CloudFile.provider == CloudProvider.MICROSOFT.value,
                            CloudFile.provider_file_id == file_info.id,
                        )
                        .first()
                    )

                    if existing:
                        # Check if file changed
                        if file_info.version != existing.provider_version:
                            existing.file_name = file_info.name
                            existing.provider_version = file_info.version
                            existing.provider_modified_at = file_info.modified_at
                            existing.needs_rescan = True
                            results["files_updated"] += 1

                            # Create scan job for changed file (if none pending)
                            if self._enqueue_scan(
                                db,
                                cloud_file=existing,
                                provider=CloudProvider.MICROSOFT.value,
                                provider_file_id=file_info.id,
                            ):
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
                            provider_file_id=file_info.id,
                            provider_parent_id=file_info.parent_id,
                            file_name=file_info.name,
                            file_type=self._get_file_type(file_info.name),
                            mime_type=file_info.mime_type,
                            file_size_bytes=file_info.size_bytes,
                            web_view_link=file_info.web_view_link,
                            provider_version=file_info.version,
                            provider_modified_at=file_info.modified_at,
                            needs_rescan=True,
                        )
                        db.add(cloud_file)
                        db.flush()

                        results["files_discovered"] += 1

                        # Create scan job for new file
                        if self._enqueue_scan(
                            db,
                            cloud_file=cloud_file,
                            provider=CloudProvider.MICROSOFT.value,
                            provider_file_id=file_info.id,
                        ):
                            results["scan_jobs_created"] += 1

                await self._checkpoint()
                db.commit()

                if next_token:
                    page_token = next_token
                    continue
                if recursive:
                    child_folders = await integration.list_folders(current_folder_id)
                    for child in child_folders:
                        if child.id not in visited_folder_ids:
                            visited_folder_ids.add(child.id)
                            pending_folders.append(child.id)
                if pending_folders:
                    current_folder_id = pending_folders.pop(0)
                    page_token = None
                    continue
                break

            # Update credential last sync time
            self.credential.last_sync_at = datetime.now(timezone.utc)
            await self._checkpoint()
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

    def _enqueue_scan(
        self,
        db: Session,
        *,
        cloud_file: CloudFile,
        provider: str,
        provider_file_id: str,
    ) -> bool:
        """Enqueue one durable scan and report whether this call created it."""
        dedupe_key = (
            f"sync-scan:{provider}:{provider_file_id}:"
            f"{cloud_file.provider_version or 'current'}"
        )
        existed = self._has_pending_scan(db, cloud_file.id)
        enqueue_cloud_job(
            db,
            department_id=self.credential.department_id,
            job_type=CloudJobType.SCAN.value,
            payload={
                "cloud_file_id": cloud_file.id,
                "credential_id": self.credential.id,
                "provider": provider,
                "provider_file_id": provider_file_id,
                "provider_version": cloud_file.provider_version,
            },
            dedupe_key=dedupe_key,
            provider=provider,
            credential_id=self.credential.id,
            cloud_file_id=cloud_file.id,
            provider_file_id=provider_file_id,
        )
        return not existed

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
    payload = job.payload if isinstance(getattr(job, "payload", None), dict) else {}
    if payload.get("credential_id") not in (None, job.credential_id) or payload.get(
        "provider"
    ) not in (None, job.provider):
        return JobFailure.deterministic("invalid_job_scope")
    requested_folder_ids = payload.get("folder_ids")
    if requested_folder_ids is not None and (
        not isinstance(requested_folder_ids, list)
        or not requested_folder_ids
        or any(
            not isinstance(value, str) or not value for value in requested_folder_ids
        )
        or len(set(requested_folder_ids)) != len(requested_folder_ids)
    ):
        return JobFailure.deterministic("invalid_sync_folders")

    # Get credential
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(CloudOAuthCredentials.id == job.credential_id)
        .first()
    )

    if (
        not credential
        or credential.department_id != job.department_id
        or credential.provider != job.provider
    ):
        return JobFailure.deterministic("invalid_job_scope")

    # Query selected sync folders for this credential
    sync_folders = (
        db.query(CloudSyncFolder)
        .filter(
            CloudSyncFolder.credential_id == credential.id,
            CloudSyncFolder.is_active,
        )
        .all()
    )
    if requested_folder_ids is not None:
        requested = set(requested_folder_ids)
        sync_folders = [folder for folder in sync_folders if folder.id in requested]
        if {folder.id for folder in sync_folders} != requested:
            return JobFailure.deterministic("invalid_sync_folders")

    # PRIVACY CHECK: If no folders selected, skip sync
    if not sync_folders:
        logger.warning(
            f"No sync folders selected for credential {credential.id}. "
            f"Skipping sync to prevent syncing entire drive (privacy-conscious)."
        )
        return {
            "success": True,
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
        "success": True,
        "provider": credential.provider,
        "folders_processed": 0,
        "files_discovered": 0,
        "files_updated": 0,
        "files_unchanged": 0,
        "scan_jobs_created": 0,
        "folder_details": [],
    }

    sync_job = CloudSyncJob(
        credential=credential,
        token_manager=token_manager,
        assert_owned=getattr(job, "_assert_owned", None),
    )

    failed_folders = 0
    # Sync each selected folder
    for sync_folder in sync_folders:
        logger.info(
            f"Syncing folder: {sync_folder.folder_name} "
            f"(id={sync_folder.provider_folder_id}, "
            f"subfolders={sync_folder.sync_subfolders})"
        )

        try:
            folder_results = await sync_job.run(
                db,
                folder_id=sync_folder.provider_folder_id,
                recursive=bool(sync_folder.sync_subfolders),
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

        except LostJobOwnership:
            db.rollback()
            raise
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
            failed_folders += 1

    logger.info(
        f"Sync complete for {credential.provider}: "
        f"{total_results['folders_processed']} folders, "
        f"{total_results['files_discovered']} new files, "
        f"{total_results['files_updated']} updated files, "
        f"{total_results['scan_jobs_created']} scan jobs created"
    )

    if failed_folders:
        return JobFailure.retryable(
            "cloud_sync_partial_failure",
            {
                "processed": total_results["folders_processed"],
                "failed": failed_folders,
            },
        )
    return total_results
