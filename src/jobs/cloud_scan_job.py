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
            elif self.credential.provider == CloudProvider.BRIGHTSPACE.value:
                export_result = await self._download_brightspace(
                    access_token, local_path
                )
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

    async def _download_brightspace(
        self, access_token: str, local_path: str
    ) -> Dict[str, Any]:
        """Download file from Brightspace LMS."""
        from src.integrations.brightspace.brightspace_api import BrightspaceAPIClient

        instance_url = (self.credential.provider_metadata or {}).get(
            "brightspace_instance_url", ""
        )
        if not instance_url:
            return {"success": False, "error": "Brightspace instance URL not found"}

        metadata = self.cloud_file.provider_metadata or {}
        org_unit_id = metadata.get("org_unit_id")
        topic_type = metadata.get("topic_type", "file")
        topic_id = int(self.cloud_file.provider_file_id)

        if not org_unit_id:
            return {"success": False, "error": "org_unit_id not found in file metadata"}

        client = BrightspaceAPIClient(
            brightspace_instance_url=instance_url,
            access_token=access_token,
        )

        try:
            if topic_type == "html":
                html_content = await client.get_topic_html(int(org_unit_id), topic_id)
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                # Store HTML in content_body for remediation
                self.cloud_file.content_body = html_content
                return {
                    "success": True,
                    "local_path": local_path,
                    "file_name": f"{self.cloud_file.file_name or 'content'}.html",
                    "mime_type": "text/html",
                    "size_bytes": len(html_content.encode("utf-8")),
                }
            else:
                file_bytes, content_type = await client.get_topic_file(
                    int(org_unit_id), topic_id
                )

                # Map content-type to file extension for proper scan detection
                _mime_to_ext = {
                    "text/html": ".html",
                    "application/pdf": ".pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                    "application/msword": ".doc",
                    "application/vnd.ms-powerpoint": ".ppt",
                    "application/vnd.ms-excel": ".xls",
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "video/mp4": ".mp4",
                }
                ext = _mime_to_ext.get(content_type, "")
                file_name = self.cloud_file.file_name or f"topic_{topic_id}"
                actual_path = local_path + ext if ext else local_path

                with open(actual_path, "wb") as f:
                    f.write(file_bytes)
                # Store HTML content for remediation
                if content_type == "text/html" or ext == ".html":
                    try:
                        self.cloud_file.content_body = file_bytes.decode(
                            "utf-8", errors="replace"
                        )
                    except Exception:
                        pass
                return {
                    "success": True,
                    "local_path": actual_path,
                    "file_name": file_name,
                    "mime_type": content_type,
                    "size_bytes": len(file_bytes),
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

        # Detect actual file type from extension if file_type is generic
        if file_type in ("file", "unknown", "html"):
            ext = os.path.splitext(file_path)[1].lower().lstrip(".")
            _known_extensions = {
                "html",
                "htm",
                "docx",
                "doc",
                "pptx",
                "ppt",
                "xlsx",
                "xls",
                "pdf",
                "jpg",
                "jpeg",
                "png",
                "gif",
                "bmp",
                "webp",
                "svg",
                "tiff",
                "mp4",
                "mp3",
                "wav",
                "avi",
                "mov",
                "webm",
                "ogg",
                "m4a",
                "flac",
            }
            if ext in _known_extensions:
                file_type = ext
            else:
                # Check if file content looks like HTML
                try:
                    with open(file_path, "rb") as f:
                        head = f.read(500).strip()
                    if (
                        head[:1] == b"<"
                        or b"<html" in head.lower()
                        or b"<!doctype" in head.lower()
                    ):
                        file_type = "html"
                except Exception:
                    pass

        try:
            # Import processors dynamically to avoid circular imports
            if file_type in ("docx", "doc"):
                from ..education.docx_processor import DocxProcessor

                processor = DocxProcessor()
                result = processor.process_docx(file_path)

            elif file_type in ("pptx", "ppt"):
                from ..education.pptx_processor import PowerPointProcessor

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

            elif file_type in ("html", "htm"):
                # Scan HTML content using axe-core via Playwright
                from ..education.canvas_content_scanner import _wrap_html_fragment

                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    html_content = f.read()

                if not html_content.strip():
                    result = {
                        "success": False,
                        "error": "Empty HTML content",
                        "issues": [],
                        "compliance_score": None,
                    }
                else:
                    # Wrap fragment if not a full document
                    if "<!doctype" not in html_content.lower()[:100]:
                        html_content = _wrap_html_fragment(html_content)

                    # Run axe-core scan
                    from playwright.async_api import async_playwright

                    async with async_playwright() as p:
                        browser = await p.chromium.launch(headless=True)
                        try:
                            page = await browser.new_page()
                            await page.set_content(html_content)

                            axe_script = os.path.join(
                                os.path.dirname(__file__),
                                "..",
                                "..",
                                "node_modules",
                                "axe-core",
                                "axe.min.js",
                            )
                            if os.path.exists(axe_script):
                                await page.add_script_tag(path=axe_script)
                            else:
                                await page.add_script_tag(
                                    url="https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.0/axe.min.js"
                                )

                            axe_results = await page.evaluate("() => axe.run()")
                            violations = axe_results.get("violations", [])
                            passes = len(axe_results.get("passes", []))
                            total_rules = passes + len(violations)
                            compliance_score = (
                                round(passes / total_rules * 100, 1)
                                if total_rules > 0
                                else 100.0
                            )

                            result = {
                                "success": True,
                                "issues": violations,
                                "compliance_score": compliance_score,
                            }
                        finally:
                            await browser.close()

            elif file_type in (
                "mp4",
                "mp3",
                "wav",
                "avi",
                "mov",
                "webm",
                "ogg",
                "m4a",
                "flac",
            ):
                from ..education.multimedia_processor import MultimediaProcessor

                processor = MultimediaProcessor()
                result = processor.process_media(
                    file_path,
                    generate_captions=False,
                    generate_audio_descriptions=False,
                    generate_spoken_descriptions=False,
                    detect_flashing=True,
                    enhance_captions=False,
                    generate_transcript=False,
                )

            elif file_type in (
                "jpg",
                "jpeg",
                "png",
                "gif",
                "bmp",
                "webp",
                "svg",
                "tiff",
            ):
                from ..education.image_alt_text import ImageAltTextGenerator

                generator = ImageAltTextGenerator()
                analysis = await generator.analyze_image_comprehensive(file_path)

                issues = []
                if analysis.get("success"):
                    type_info = analysis.get("type_detection", {})
                    is_decorative = type_info.get("is_decorative", False)
                    if is_decorative:
                        compliance_score = 100.0
                    else:
                        # Informative image without alt text = issue
                        compliance_score = 0.0
                        issues.append(
                            {
                                "severity": "critical",
                                "impact": "critical",
                                "description": "Image requires alt text",
                                "suggested_alt": analysis.get("description", {}).get(
                                    "alt_text", ""
                                ),
                            }
                        )
                else:
                    compliance_score = None

                result = {
                    "success": analysis.get("success", False),
                    "issues": issues,
                    "compliance_score": compliance_score,
                }

            else:
                result = {
                    "success": False,
                    "error": f"Unsupported file type: {file_type}",
                    "issues": [],
                    "compliance_score": None,
                }

            # Normalize result to dict (processors may return Pydantic models)
            if not isinstance(result, dict):
                result = (
                    result.model_dump()
                    if hasattr(result, "model_dump")
                    else result.__dict__
                )

            # Map file_type to valid ScanType enum
            _file_type_to_scan_type = {
                "pdf": ScanType.PDF,
                "docx": ScanType.WORD,
                "doc": ScanType.WORD,
                "pptx": ScanType.POWERPOINT,
                "ppt": ScanType.POWERPOINT,
                "xlsx": ScanType.EXCEL,
                "xls": ScanType.EXCEL,
                "html": ScanType.CANVAS_CONTENT,
                "htm": ScanType.CANVAS_CONTENT,
                "mp4": ScanType.VIDEO,
                "mp3": ScanType.VIDEO,
                "wav": ScanType.VIDEO,
                "avi": ScanType.VIDEO,
                "mov": ScanType.VIDEO,
                "webm": ScanType.VIDEO,
                "ogg": ScanType.VIDEO,
                "jpg": ScanType.IMAGE,
                "jpeg": ScanType.IMAGE,
                "png": ScanType.IMAGE,
                "gif": ScanType.IMAGE,
                "bmp": ScanType.IMAGE,
                "webp": ScanType.IMAGE,
                "svg": ScanType.IMAGE,
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
                critical_issues=len(
                    [
                        i
                        for i in issues
                        if isinstance(i, dict)
                        and (
                            i.get("severity") == "critical"
                            or i.get("impact") == "critical"
                        )
                    ]
                ),
                high_issues=len(
                    [
                        i
                        for i in issues
                        if isinstance(i, dict)
                        and (
                            i.get("severity") == "high" or i.get("impact") == "serious"
                        )
                    ]
                ),
                medium_issues=len(
                    [
                        i
                        for i in issues
                        if isinstance(i, dict)
                        and (
                            i.get("severity") == "medium"
                            or i.get("impact") == "moderate"
                        )
                    ]
                ),
                low_issues=len(
                    [
                        i
                        for i in issues
                        if isinstance(i, dict)
                        and (i.get("severity") == "low" or i.get("impact") == "minor")
                    ]
                ),
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
