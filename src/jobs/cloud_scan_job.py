"""
Cloud Scan Job Handler

Downloads files from cloud storage, scans for accessibility issues
using existing processors, and stores results.
"""

import asyncio
import logging
import math
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone
from numbers import Real
from typing import Dict, Any, cast
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
from .contracts import LostJobOwnership
from ..utils.security import (
    PERSISTED_BRIGHTSPACE_ORIGIN_ERROR,
    PERSISTED_CANVAS_ORIGIN_ERROR,
    require_persisted_brightspace_origin,
    require_persisted_canvas_origin,
)

logger = logging.getLogger(__name__)

_DETERMINISTIC_SCAN_METADATA = {
    "operation_kind": "deterministic_scan",
    "external_ai_used": False,
    "ai_used": False,
}

_SCAN_FAILURE_MESSAGE = "Accessibility scan failed"


class ScanJobFailed(RuntimeError):
    """Sanitized failure propagated to background job callers."""

    def __init__(self, code: str = "SCAN_PROCESSING_FAILED") -> None:
        self.code = code
        super().__init__(_SCAN_FAILURE_MESSAGE)


def _normalize_processor_result(result: Any) -> Dict[str, Any]:
    """Convert processor output and derive success without inventing it."""
    if isinstance(result, dict):
        normalized = dict(result)
    elif hasattr(result, "model_dump"):
        normalized = result.model_dump()
    else:
        normalized = dict(result.__dict__)

    compliance_score = normalized.get("compliance_score")
    valid_score = False
    if isinstance(compliance_score, Real) and not isinstance(compliance_score, bool):
        numeric_score = cast(Any, compliance_score)
        valid_score = 0 <= numeric_score <= 100 and math.isfinite(numeric_score)
    success_is_explicit = "success" in normalized
    explicit_success = normalized.get("success")
    if success_is_explicit and type(explicit_success) is bool:
        success = explicit_success and not normalized.get("error") and valid_score
    elif success_is_explicit:
        success = False
    else:
        success = not normalized.get("error") and valid_score
    normalized["success"] = success
    if not success:
        normalized["compliance_score"] = None
        normalized["error"] = _SCAN_FAILURE_MESSAGE
        normalized["error_code"] = "SCAN_PROCESSING_FAILED"
    return normalized


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
        assert_owned: Any = None,
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
        self.assert_owned = assert_owned

    async def _checkpoint(self) -> None:
        if self.assert_owned is not None:
            await self.assert_owned()

    async def run(self, db: Session) -> Dict[str, Any]:
        """
        Run the scan job.

        Args:
            db: Database session

        Returns:
            Scan results
        """
        brightspace_origin = None
        if self.credential.provider == CloudProvider.BRIGHTSPACE.value:
            try:
                brightspace_origin = require_persisted_brightspace_origin(
                    self.credential
                )
            except ValueError as exc:
                raise ScanJobFailed("BRIGHTSPACE_CONNECTION_ORIGIN_INVALID") from exc

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
                    access_token, local_path, instance_url=brightspace_origin
                )
            else:
                export_result = await self._download_microsoft(access_token, local_path)

            if not export_result.get("success"):
                raise ScanJobFailed("DOWNLOAD_FAILED")

            # Get the actual downloaded file path
            actual_path = export_result.get("local_path", local_path)

            # Scan the file using appropriate processor
            scan_result = await self._scan_file(actual_path, db)
            if not scan_result.get("success"):
                raise ScanJobFailed(
                    scan_result.get("error_code", "SCAN_PROCESSING_FAILED")
                )

            return scan_result

    async def _refresh_token_if_needed(self, db: Session) -> str:
        """Refresh OAuth token if expired (with distributed lock).

        Returns:
            Decrypted access token.
        """
        return await self.token_manager.refresh_if_expired(self.credential, db)

    async def _persist_failed_scan(
        self, db: Session, file_type: str, error_code: str
    ) -> Dict[str, Any]:
        """Persist a failed processor/runtime outcome without a false score."""
        scan_types = {
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
            "pdf": ScanType.PDF,
        }
        dept_user = (
            db.query(User.id)
            .filter(
                User.department_id == self.credential.department_id,
                User.is_active == True,
            )
            .first()
        )
        scan = Scan(
            id=str(uuid.uuid4()),
            department_id=self.credential.department_id,
            scan_type=scan_types.get(file_type, ScanType.PDF),
            user_id=dept_user.id if dept_user else "system",
            file_name=self.cloud_file.file_name,
            file_size_bytes=getattr(self.cloud_file, "file_size_bytes", 0) or 0,
            status="FAILED",
            error_message=error_code,
        )
        db.add(scan)
        db.flush()
        self.cloud_file.needs_rescan = True
        await self._checkpoint()
        db.commit()
        return {
            "scan_id": scan.id,
            "file_id": self.cloud_file.id,
            "file_name": self.cloud_file.file_name,
            "compliance_score": None,
            "issues_found": 0,
            "success": False,
            "error": _SCAN_FAILURE_MESSAGE,
            "error_code": error_code,
            **_DETERMINISTIC_SCAN_METADATA,
        }

    async def _download_google(
        self, access_token: str, local_path: str
    ) -> Dict[str, Any]:
        """Download file from Google Drive."""
        integration = GoogleDriveIntegration(
            access_token=access_token,
            credential_id=self.credential.id,
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
            credential_id=self.credential.id,
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

        try:
            canvas_url = require_persisted_canvas_origin(self.credential)
        except ValueError:
            return {
                "success": False,
                "error": PERSISTED_CANVAS_ORIGIN_ERROR,
            }

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
        self,
        access_token: str,
        local_path: str,
        *,
        instance_url: str | None = None,
    ) -> Dict[str, Any]:
        """Download file from Brightspace LMS."""
        from src.integrations.brightspace.brightspace_api import BrightspaceAPIClient

        if instance_url is None:
            try:
                instance_url = require_persisted_brightspace_origin(self.credential)
            except ValueError:
                return {
                    "success": False,
                    "error": PERSISTED_BRIGHTSPACE_ORIGIN_ERROR,
                }

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
                await asyncio.to_thread(
                    Path(local_path).write_text, html_content, encoding="utf-8"
                )
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

                await asyncio.to_thread(Path(actual_path).write_bytes, file_bytes)
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
                    head = (await asyncio.to_thread(Path(file_path).read_bytes))[
                        :500
                    ].strip()
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

                processor = DocxProcessor(
                    generate_alt_text=False,
                    validate_alt_text=False,
                    enhance_descriptions=False,
                    simulate_color_blindness=False,
                )
                result = await asyncio.to_thread(processor.process_docx, file_path)

            elif file_type in ("pptx", "ppt"):
                from ..education.pptx_processor import PowerPointProcessor

                processor = PowerPointProcessor(
                    generate_alt_text=False,
                    validate_alt_text=False,
                    simulate_color_blindness=False,
                    detect_images_of_text=False,
                )
                result = await asyncio.to_thread(processor.process_pptx, file_path)

            elif file_type in ("xlsx", "xls"):
                from ..education.xlsx_processor import XlsxProcessor

                processor = XlsxProcessor(
                    generate_chart_descriptions=False,
                    generate_alt_text=False,
                    validate_alt_text=False,
                    simulate_color_blindness=False,
                )
                result = await asyncio.to_thread(processor.process_xlsx, file_path)

            elif file_type == "pdf":
                from ..education.pdf_processor import PDFProcessor

                processor = PDFProcessor(
                    generate_alt_text=False,
                    validate_alt_text=False,
                    enhance_descriptions=False,
                    simulate_color_blindness=False,
                )
                result = await asyncio.to_thread(processor.process_pdf, file_path)

            elif file_type in ("html", "htm"):
                from ..education.canvas_content_scanner import _wrap_html_fragment
                from ..education.deterministic_axe import run_deterministic_axe

                html_content = await asyncio.to_thread(
                    Path(file_path).read_text, encoding="utf-8", errors="replace"
                )

                if not html_content.strip():
                    result = {
                        "success": False,
                        "error": "Empty HTML content",
                        "issues": [],
                        "compliance_score": None,
                    }
                else:
                    if "<!doctype" not in html_content.lower()[:100]:
                        html_content = _wrap_html_fragment(html_content)
                    axe_results = await run_deterministic_axe(html_content)
                    violations = axe_results["violations"]
                    passes = len(axe_results["passes"])
                    total_rules = passes + len(violations)
                    result = {
                        "success": True,
                        "issues": violations,
                        "compliance_score": round(passes / total_rules * 100, 1),
                        "axe_results": axe_results,
                    }

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

                processor = MultimediaProcessor(use_gemini=False)
                result = await asyncio.to_thread(
                    processor.process_media,
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
                # Standalone images have no container-level alt attribute to
                # inspect. Record the existing missing-alt finding for manual
                # review without classifying or describing pixels with AI.
                issues = [
                    {
                        "severity": "critical",
                        "impact": "critical",
                        "description": "Image requires alt text",
                        "manual_review_required": True,
                        "operation_kind": "deterministic_scan",
                        "external_ai_used": False,
                        "ai_used": False,
                    }
                ]
                result = {
                    "success": True,
                    "issues": issues,
                    "compliance_score": 0.0,
                }

            else:
                result = {
                    "success": False,
                    "error": f"Unsupported file type: {file_type}",
                    "issues": [],
                    "compliance_score": None,
                }

            result = _normalize_processor_result(result)

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
                status="COMPLETED" if result["success"] else "FAILED",
                error_message=None if result["success"] else result["error_code"],
            )
            db.add(scan)
            db.flush()

            if not result["success"]:
                self.cloud_file.needs_rescan = True
                await self._checkpoint()
                db.commit()
                return {
                    "scan_id": scan.id,
                    "file_id": self.cloud_file.id,
                    "file_name": self.cloud_file.file_name,
                    "compliance_score": None,
                    "issues_found": 0,
                    "success": False,
                    "error": _SCAN_FAILURE_MESSAGE,
                    "error_code": result["error_code"],
                    **_DETERMINISTIC_SCAN_METADATA,
                }

            # Create scan result with compliance data
            from ..db.models import ScanResult

            scan_result = ScanResult(
                id=str(uuid.uuid4()),
                scan_id=scan_id,
                compliance_score=compliance_score,
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

            await self._checkpoint()
            db.commit()

            logger.info(
                f"Scanned cloud file {self.cloud_file.id}: "
                f"score={result.get('compliance_score')}, "
                f"issues={len(result.get('issues', []))}"
            )

            # Trigger email alerts for completed scan
            try:
                from .email_alert_job import trigger_scan_alerts

                await self._checkpoint()
                await trigger_scan_alerts(db, scan)
            except LostJobOwnership:
                db.rollback()
                raise
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
                "operation_kind": "deterministic_scan",
                "external_ai_used": False,
                "ai_used": False,
            }

        except LostJobOwnership:
            db.rollback()
            raise
        except ImportError as exc:
            logger.error(
                "Processor unavailable for cloud scan",
                extra={
                    "file_type": file_type,
                    "error_code": "PROCESSOR_UNAVAILABLE",
                    "exception_type": type(exc).__name__,
                },
            )
            return await self._persist_failed_scan(
                db, file_type, "PROCESSOR_UNAVAILABLE"
            )
        except Exception as exc:
            logger.error(
                "Scan processing failed for cloud file",
                extra={
                    "cloud_file_id": self.cloud_file.id,
                    "department_id": self.credential.department_id,
                    "file_type": self.cloud_file.file_type,
                    "error_code": "SCAN_PROCESSING_FAILED",
                    "exception_type": type(exc).__name__,
                },
            )
            return await self._persist_failed_scan(
                db, file_type, "SCAN_PROCESSING_FAILED"
            )


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
    payload = job.payload if isinstance(getattr(job, "payload", None), dict) else {}
    if any(
        payload.get(field) not in (None, getattr(job, field))
        for field in (
            "cloud_file_id",
            "credential_id",
            "provider",
            "provider_file_id",
        )
    ):
        raise ScanJobFailed("INVALID_JOB_SCOPE")

    # Get credential
    credential = (
        db.query(CloudOAuthCredentials)
        .filter(CloudOAuthCredentials.id == job.credential_id)
        .first()
    )

    if not credential:
        raise ScanJobFailed("CREDENTIAL_UNAVAILABLE")
    if (
        credential.department_id != job.department_id
        or credential.provider != job.provider
        or credential.is_active is not True
    ):
        raise ScanJobFailed("INVALID_JOB_SCOPE")

    scan_kind = payload.get("scan_kind", "cloud_file")
    if scan_kind == "canvas_course":
        return await _handle_canvas_course_scan(
            job, db, token_manager, credential, payload
        )

    # Get cloud file
    cloud_file = db.query(CloudFile).filter(CloudFile.id == job.cloud_file_id).first()

    if not cloud_file:
        raise ScanJobFailed("FILE_UNAVAILABLE")
    if (
        cloud_file.department_id != job.department_id
        or cloud_file.credential_id != credential.id
        or cloud_file.provider != credential.provider
    ):
        raise ScanJobFailed("INVALID_JOB_SCOPE")

    if scan_kind == "canvas_content":
        return await _handle_canvas_content_scan(
            job, db, token_manager, credential, cloud_file, payload
        )
    if scan_kind != "cloud_file":
        raise ScanJobFailed("INVALID_JOB_SCOPE")

    # Run scan
    scan_job = CloudScanJob(
        credential=credential,
        cloud_file=cloud_file,
        token_manager=token_manager,
        assert_owned=getattr(job, "_assert_owned", None),
    )
    result = await scan_job.run(db)
    if not result.get("success"):
        raise ScanJobFailed(result.get("error_code", "SCAN_PROCESSING_FAILED"))
    return result


async def _handle_canvas_course_scan(
    job: CloudJobQueue,
    db: Session,
    token_manager: OAuthTokenManager,
    credential: CloudOAuthCredentials,
    payload: dict[str, Any],
) -> Dict[str, Any]:
    """Discover a Canvas course and durably fan out its child scan jobs."""
    from ..education.canvas_content_scanner import CanvasContentScanner
    from ..integrations.canvas.canvas_api import CanvasAPIClient
    from ..integrations.canvas.content_models import CanvasContentType
    from ..utils.security import require_persisted_canvas_origin

    course_id = payload.get("course_id")
    raw_types = payload.get("content_types")
    if (
        job.provider != "canvas"
        or not isinstance(course_id, str)
        or not course_id
        or not isinstance(raw_types, list)
        or not raw_types
    ):
        raise ScanJobFailed("INVALID_JOB_SCOPE")
    try:
        content_types = [CanvasContentType(value) for value in raw_types]
    except (TypeError, ValueError) as exc:
        raise ScanJobFailed("INVALID_JOB_SCOPE") from exc
    origin = require_persisted_canvas_origin(credential)
    access_token = await token_manager.refresh_if_expired(credential, db)
    client = CanvasAPIClient(
        canvas_instance_url=origin,
        access_token=access_token,
        credential_id=credential.id,
    )
    try:
        scanner = CanvasContentScanner(
            canvas_client=client,
            db=db,
            department_id=job.department_id,
            credential_id=credential.id,
            scan_options=payload.get("scan_options"),
        )
        result = await scanner.scan_course_content(
            course_id, content_types=content_types
        )
        return {"success": True, **result}
    finally:
        await client.close()


async def _handle_canvas_content_scan(
    job: CloudJobQueue,
    db: Session,
    token_manager: OAuthTokenManager,
    credential: CloudOAuthCredentials,
    cloud_file: CloudFile,
    payload: dict[str, Any],
) -> Dict[str, Any]:
    """Run the deterministic Canvas HTML scan from immutable queue input."""
    from ..education.canvas_content_scanner import CanvasContentScanner
    from ..integrations.canvas.canvas_api import CanvasAPIClient
    from ..utils.security import require_persisted_canvas_origin

    if (
        job.provider != "canvas"
        or payload.get("course_id") != cloud_file.provider_parent_id
        or payload.get("content_source") != cloud_file.content_source
    ):
        raise ScanJobFailed("INVALID_JOB_SCOPE")
    origin = require_persisted_canvas_origin(credential)
    access_token = await token_manager.refresh_if_expired(credential, db)
    client = CanvasAPIClient(
        canvas_instance_url=origin,
        access_token=access_token,
        credential_id=credential.id,
    )
    try:
        scanner = CanvasContentScanner(
            canvas_client=client,
            db=db,
            department_id=job.department_id,
            credential_id=credential.id,
            scan_options=payload.get("scan_options"),
        )
        result = await scanner.scan_content_item(cloud_file)
        scan_options = payload.get("scan_options")
        if (
            result.get("success") is True
            and int(result.get("issues", 0) or 0) > 0
            and isinstance(scan_options, dict)
            and scan_options.get("auto_remediate") is True
        ):
            from .canvas_content_job import enqueue_canvas_content_remediation

            db.refresh(cloud_file)
            remediation_job = enqueue_canvas_content_remediation(
                db,
                cloud_file=cloud_file,
                options=scan_options,
                depends_on_job_id=str(job.id),
            )
            db.commit()
            result["remediation_job_id"] = str(remediation_job.id)
        return {"success": True, **result}
    finally:
        await client.close()
