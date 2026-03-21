"""
Cloud Scan Job Handler

Downloads files from cloud storage, scans for accessibility issues
using existing processors, and stores results.
"""

import logging
import tempfile
import os
from datetime import datetime, timezone
from typing import Dict, Any
from sqlalchemy.orm import Session
import uuid

from ..db.models import (
    CloudJobQueue,
    CloudOAuthCredentials,
    CloudFile,
    CloudProvider,
    Scan,
    ScanType,
    User,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..integrations.google_workspace.google_drive import GoogleDriveIntegration
from ..integrations.microsoft_365.onedrive import OneDriveIntegration

logger = logging.getLogger(__name__)


class CloudScanJob:
    """
    Cloud file scanning job.

    Downloads files from cloud storage and scans for accessibility issues.
    """

    def __init__(
        self,
        credential: CloudOAuthCredentials,
        cloud_file: CloudFile,
        token_manager: OAuthTokenManager,
    ):
        """
        Initialize scan job.

        Args:
            credential: OAuth credentials for cloud provider
            cloud_file: Cloud file to scan
            token_manager: Token manager for decryption/refresh
        """
        self.credential = credential
        self.cloud_file = cloud_file
        self.token_manager = token_manager

    async def run(self, db: Session) -> Dict[str, Any]:
        """
        Run the scan job.

        Args:
            db: Database session

        Returns:
            Scan results
        """
        # Refresh token if needed (with distributed lock) and get access token
        access_token = await self._refresh_token_if_needed(db)

        # Download file to temp location
        with tempfile.TemporaryDirectory() as temp_dir:
            local_path = os.path.join(temp_dir, self.cloud_file.file_name or "file")

            if self.credential.provider == CloudProvider.GOOGLE.value:
                export_result = await self._download_google(access_token, local_path)
            elif self.credential.provider == CloudProvider.CANVAS.value:
                export_result = await self._download_canvas(access_token, local_path)
            else:
                export_result = await self._download_microsoft(access_token, local_path)

            if not export_result.get("success"):
                raise Exception(f"Download failed: {export_result.get('error')}")

            # Get the actual downloaded file path
            actual_path = export_result.get("local_path", local_path)

            # Scan the file using appropriate processor
            scan_result = await self._scan_file(actual_path, db)

            return scan_result

    async def _refresh_token_if_needed(self, db: Session) -> str:
        """Refresh OAuth token if expired (with distributed lock).

        Returns:
            Decrypted access token.
        """
        return await self.token_manager.refresh_if_expired(self.credential, db)

    async def _download_google(
        self, access_token: str, local_path: str
    ) -> Dict[str, Any]:
        """Download file from Google Drive."""
        integration = GoogleDriveIntegration(
            access_token=access_token,
            department_id=self.credential.department_id,
        )

        try:
            result = await integration.download_file(
                file_id=self.cloud_file.provider_file_id,
                local_path=local_path,
            )
            return {
                "success": result.success,
                "local_path": result.local_path,
                "file_name": result.file_name,
                "mime_type": result.mime_type,
                "size_bytes": result.size_bytes,
                "error": result.error,
            }
        finally:
            await integration.close()

    async def _download_microsoft(
        self, access_token: str, local_path: str
    ) -> Dict[str, Any]:
        """Download file from OneDrive/SharePoint."""
        integration = OneDriveIntegration(
            access_token=access_token,
            department_id=self.credential.department_id,
        )

        try:
            result = await integration.download_file(
                file_id=self.cloud_file.provider_file_id,
                local_path=local_path,
            )
            return {
                "success": result.success,
                "local_path": result.local_path,
                "file_name": result.file_name,
                "mime_type": result.mime_type,
                "size_bytes": result.size_bytes,
                "error": result.error,
            }
        finally:
            await integration.close()

    async def _download_canvas(
        self, access_token: str, local_path: str
    ) -> Dict[str, Any]:
        """Download file from Canvas LMS."""
        from ..integrations.canvas.canvas_api import CanvasAPIClient

        canvas_url = self.credential.provider_metadata.get("canvas_instance_url", "")
        if not canvas_url:
            return {
                "success": False,
                "error": "Canvas instance URL not found in credential metadata",
            }

        # Rewrite localhost for Docker networking
        if os.getenv("ENV") == "development" and "localhost" in canvas_url:
            canvas_url = canvas_url.replace("localhost", "host.docker.internal")

        client = CanvasAPIClient(
            canvas_instance_url=canvas_url,
            access_token=access_token,
        )

        try:
            result = await client.download_file(
                file_id=self.cloud_file.provider_file_id,
                local_path=local_path,
            )
            return {
                "success": result.success,
                "local_path": result.local_path or local_path,
                "file_name": result.file_name,
                "mime_type": result.content_type,
                "size_bytes": result.size,
                "error": result.error,
            }
        finally:
            await client.close()

    async def _scan_file(self, file_path: str, db: Session) -> Dict[str, Any]:
        """
        Scan file for accessibility issues using appropriate processor.

        Args:
            file_path: Path to downloaded file
            db: Database session

        Returns:
            Scan results
        """
        file_type = self.cloud_file.file_type.lower()

        try:
            # Import processors dynamically to avoid circular imports
            if file_type in ("docx", "doc"):
                from ..education.docx_processor import DocxProcessor

                processor = DocxProcessor()
                result = processor.process_docx(file_path)

            elif file_type in ("pptx", "ppt"):
                from ..education.powerpoint_processor import PowerPointProcessor

                processor = PowerPointProcessor()
                result = processor.process_pptx(file_path)

            elif file_type in ("xlsx", "xls"):
                from ..education.xlsx_processor import XlsxProcessor

                processor = XlsxProcessor()
                result = processor.process_xlsx(file_path)

            elif file_type == "pdf":
                from ..education.pdf_processor import PDFProcessor

                processor = PDFProcessor()
                result = processor.process_pdf(file_path)

            else:
                result = {
                    "success": False,
                    "error": f"Unsupported file type: {file_type}",
                    "issues": [],
                    "compliance_score": None,
                }

            # Normalize result to dict (processors may return Pydantic models)
            if not isinstance(result, dict):
                result = result.model_dump() if hasattr(result, "model_dump") else result.__dict__

            # Map file_type to valid ScanType enum
            _file_type_to_scan_type = {
                "pdf": ScanType.PDF,
                "docx": ScanType.WORD,
                "doc": ScanType.WORD,
                "pptx": ScanType.POWERPOINT,
                "ppt": ScanType.POWERPOINT,
                "xlsx": ScanType.EXCEL,
                "xls": ScanType.EXCEL,
            }
            scan_type = _file_type_to_scan_type.get(file_type, ScanType.PDF)

            # Find a user_id for this department (cloud scans are system-initiated)
            dept_user = (
                db.query(User.id)
                .filter(
                    User.department_id == self.credential.department_id,
                    User.is_active == True,
                )
                .first()
            )
            scan_user_id = dept_user.id if dept_user else "system"

            # Create scan record
            scan_id = str(uuid.uuid4())
            issues = result.get("issues", [])
            compliance_score = result.get("compliance_score")

            scan = Scan(
                id=scan_id,
                department_id=self.credential.department_id,
                scan_type=scan_type,
                user_id=scan_user_id,
                file_name=self.cloud_file.file_name,
                file_size_bytes=self.cloud_file.file_size_bytes or 0,
                status="COMPLETED" if compliance_score is not None else "FAILED",
            )
            db.add(scan)
            db.flush()

            # Create scan result with compliance data
            from ..db.models import ScanResult

            scan_result = ScanResult(
                id=str(uuid.uuid4()),
                scan_id=scan_id,
                compliance_score=compliance_score or 0,
                critical_issues=len([i for i in issues if isinstance(i, dict) and i.get("severity") == "critical"]),
                high_issues=len([i for i in issues if isinstance(i, dict) and i.get("severity") == "high"]),
                medium_issues=len([i for i in issues if isinstance(i, dict) and i.get("severity") == "medium"]),
                low_issues=len([i for i in issues if isinstance(i, dict) and i.get("severity") == "low"]),
                issues=issues,
            )
            db.add(scan_result)

            # Update cloud file record
            self.cloud_file.last_scan_id = scan.id
            self.cloud_file.last_scanned_at = datetime.now(timezone.utc)
            self.cloud_file.last_compliance_score = result.get("compliance_score")
            self.cloud_file.needs_rescan = False

            db.commit()

            logger.info(
                f"Scanned cloud file {self.cloud_file.id}: "
                f"score={result.get('compliance_score')}, "
                f"issues={len(result.get('issues', []))}"
            )

            # Trigger email alerts for completed scan
            try:
                from .email_alert_job import trigger_scan_alerts

                await trigger_scan_alerts(db, scan)
            except Exception as e:
                # Don't fail the scan if email alerts fail
                logger.warning(
                    f"Failed to send email alerts for cloud file {self.cloud_file.id} (dept={self.credential.department_id}): {e}"
                )

            return {
                "scan_id": scan.id,
                "file_id": self.cloud_file.id,
                "file_name": self.cloud_file.file_name,
                "compliance_score": result.get("compliance_score"),
                "issues_found": len(result.get("issues", [])),
                "success": result.get("success", False),
            }

        except ImportError as e:
            logger.error(f"Processor not available for {file_type}: {e}")
            return {
                "success": False,
                "error": f"Processor not available for {file_type}",
                "file_id": self.cloud_file.id,
            }
        except Exception as e:
            logger.error(
                f"Scan failed for cloud file {self.cloud_file.id} (dept={self.credential.department_id}, type={self.cloud_file.file_type}): {e}"
            )
            return {
                "success": False,
                "error": str(e),
                "file_id": self.cloud_file.id,
            }


async def handle_scan_job(
    job: CloudJobQueue,
    db: Session,
    token_manager: OAuthTokenManager,
) -> Dict[str, Any]:
    """
    Job handler for cloud scan jobs.

    Args:
        job: The job to process
        db: Database session
        token_manager: OAuth token manager

    Returns:
        Scan results
    """
    # Get credential
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(CloudOAuthCredentials.id == job.credential_id)
        .first()
    )

    if not credential:
        raise ValueError(f"Credential not found: {job.credential_id}")

    # Get cloud file
    cloud_file = db.query(CloudFile).filter(CloudFile.id == job.cloud_file_id).first()

    if not cloud_file:
        raise ValueError(f"Cloud file not found: {job.cloud_file_id}")

    # Run scan
    scan_job = CloudScanJob(
        credential=credential,
        cloud_file=cloud_file,
        token_manager=token_manager,
    )
    return await scan_job.run(db)
