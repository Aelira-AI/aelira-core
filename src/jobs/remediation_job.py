"""
Remediation Job Processor

Processes accessibility remediation jobs for scanned files.
Applies automated fixes to accessibility issues.
"""

import logging
import tempfile
import shutil
import uuid
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from ..db.models import (
    Scan,
    ScanResult,
    ScanFix,
    ReviewAuditLog,
    MatterhornResult as MatterhornResultModel,
    CloudFile,
    CloudOAuthCredentials,
    CloudJobQueue,
    CloudJobType,
    CloudJobStatus,
    CloudProvider,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
from ..integrations.google_workspace.google_drive import GoogleDriveIntegration
from ..integrations.microsoft_365.onedrive import OneDriveIntegration
from ..utils.security import (
    PERSISTED_CANVAS_ORIGIN_ERROR,
    require_persisted_canvas_origin,
)

logger = logging.getLogger(__name__)


async def process_remediation_job(
    job_data: Dict[str, Any],
    db: Session,
) -> Dict[str, Any]:
    """
    Process a remediation job.

    Downloads the scanned file, applies automated fixes, and generates
    a remediated output file.

    Args:
        job_data: Job data including:
            - scan_id: Scan ID to remediate
            - cloud_file_id: Cloud file ID (optional)
            - file_path: Path to file to remediate (optional)
            - department_id: Department ID
            - upload_to_cloud: Whether to upload back to cloud (default: False)
            - provider: Cloud provider (google/microsoft)
        db: Database session

    Returns:
        Dict with:
            - success: bool
            - fixed_count: int (number of issues fixed)
            - manual_count: int (issues needing manual review)
            - failed_count: int (fixes that failed)
            - output_file: str (path to remediated file)
            - backup_path: str (path to backup)
            - upload_job_id: str (if upload_to_cloud=True)
            - error: str (if failed)
    """
    scan_id = job_data.get("scan_id")
    cloud_file_id = job_data.get("cloud_file_id")
    file_path = job_data.get("file_path")
    department_id = job_data.get("department_id")
    upload_to_cloud = job_data.get("upload_to_cloud", False)
    provider = job_data.get("provider")

    try:
        logger.info(
            f"Processing remediation job for scan {scan_id}, department {department_id}"
        )

        # 1. Fetch scan from database
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            return {"success": False, "error": f"Scan not found: {scan_id}"}

        # 2. Get ScanResult with detailed issues
        scan_result = db.query(ScanResult).filter(ScanResult.scan_id == scan_id).first()
        if not scan_result or not scan_result.issues:
            return {
                "success": False,
                "error": "No issues found to remediate",
                "scan_id": scan_id,
            }

        # 3. Determine file path (cloud, manual upload, or explicit path)
        temp_file_path = None
        if cloud_file_id:
            # Download from cloud storage (Google Drive/OneDrive)
            download_result = await _download_cloud_file(
                cloud_file_id, department_id, db
            )
            if not download_result.get("success"):
                return {
                    "success": False,
                    "error": f"Failed to download cloud file: {download_result.get('error')}",
                }
            file_path = download_result.get("local_path")
            temp_file_path = file_path  # Mark for cleanup
        elif not file_path and scan.storage_path:
            # Use manually uploaded file from persistent storage
            file_path = scan.storage_path
            logger.info(f"Using manually uploaded file from storage: {file_path}")

        # 4. Validate file exists
        if not file_path or not Path(file_path).exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        # 5. Create backup
        backup_path = _create_backup(file_path)

        # 6. Parse issues into RemediationIssue objects
        issues = scan_result.issues or []
        logger.info(f"Processing {len(issues)} issues from scan")

        # 7. Select and instantiate remediator
        remediator = _get_remediator_for_scan_type(
            scan_type=scan.scan_type, file_path=file_path, issues=issues, use_ai=True
        )

        if not remediator:
            return {
                "success": False,
                "error": f"No remediator available for scan type: {scan.scan_type}",
            }

        # 8. Run remediation
        logger.info(f"Starting remediation with {remediator.__class__.__name__}")
        remediation_result = remediator.remediate()

        # 9. Queue upload job if requested and remediation succeeded
        upload_job_id = None
        if (
            upload_to_cloud
            and cloud_file_id
            and remediation_result.success
            and remediation_result.fixed_count > 0
        ):
            upload_job_id = await _queue_upload_job(
                file_path=remediation_result.output_file,
                cloud_file_id=cloud_file_id,
                department_id=department_id,
                provider=provider,
                db=db,
            )
            logger.info(f"Queued upload job: {upload_job_id}")

        # 10. Send email notification
        try:
            await _send_remediation_notification(
                scan=scan,
                result=remediation_result,
                department_id=department_id,
                db=db,
            )
        except Exception as e:
            # Don't fail the remediation if email notification fails
            logger.warning(f"Failed to send remediation notification: {e}")

        # 11. Update scan record with remediation results
        scan.completed_at = datetime.now(timezone.utc)
        scan.status = "remediated"

        # Store remediation metadata
        if not scan.metadata:
            scan.metadata = {}
        scan.metadata["remediation"] = {
            "fixed_count": remediation_result.fixed_count,
            "manual_count": remediation_result.manual_count,
            "failed_count": remediation_result.failed_count,
            "output_file": remediation_result.output_file,
            "backup_path": backup_path,
            "compliance_improvement": remediation_result.improvement,
            "remediated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Persist individual fixes to scan_fixes table (delete existing for idempotency on retry)
        db.query(ScanFix).filter(ScanFix.scan_id == scan_id).delete()
        for fix in remediation_result.fixed_issues:
            scan_fix = ScanFix(
                id=str(uuid.uuid4()),
                scan_id=scan_id,
                issue_id=fix.issue_id,
                category=(
                    fix.category.value
                    if hasattr(fix.category, "value")
                    else fix.category
                ),
                severity=(
                    fix.severity.value
                    if hasattr(fix.severity, "value")
                    else fix.severity
                ),
                description=fix.description,
                location=fix.location,
                original_content=fix.original_content,
                fixed_content=fix.fixed_content,
                fix_method=fix.fix_method,
                model_used=fix.model_used,
                confidence=fix.confidence,
                needs_review=fix.needs_review,
                review_status="auto_approved" if not fix.needs_review else "pending",
                wcag_criteria=fix.wcag_criteria,
                page_number=fix.page_number,
            )
            db.add(scan_fix)

        # Log remediation completion to audit trail
        auto_approved = sum(
            1 for f in remediation_result.fixed_issues if not f.needs_review
        )
        db.add(
            ReviewAuditLog(
                id=str(uuid.uuid4()),
                scan_id=scan_id,
                action="remediation_complete",
                details={
                    "total_fixes": len(remediation_result.fixed_issues),
                    "auto_approved": auto_approved,
                    "needs_review": len(remediation_result.fixed_issues)
                    - auto_approved,
                    "manual_issues": remediation_result.manual_count,
                    "failed_issues": remediation_result.failed_count,
                },
            )
        )

        # Run post-remediation Matterhorn validation (PDF only)
        if scan.scan_type in ("PDF", "pdf") and remediation_result.output_file:
            try:
                from ..education.validation.matterhorn import MatterhornValidator

                validator = MatterhornValidator()
                matterhorn = validator.validate(remediation_result.output_file)

                for cp in matterhorn.checkpoints:
                    db.add(
                        MatterhornResultModel(
                            id=str(uuid.uuid4()),
                            scan_id=scan_id,
                            checkpoint_id=cp.id,
                            checkpoint_name=cp.name,
                            status=cp.status.value,
                            severity=cp.severity,
                            details=cp.details,
                            page_number=cp.page_number,
                        )
                    )

                db.add(
                    ReviewAuditLog(
                        id=str(uuid.uuid4()),
                        scan_id=scan_id,
                        action="matterhorn_validation",
                        details={
                            "total": matterhorn.total,
                            "passed": matterhorn.passed,
                            "failed": matterhorn.failed,
                            "warnings": matterhorn.warnings,
                            "compliance_level": matterhorn.compliance_level,
                        },
                    )
                )

                logger.info(
                    "Matterhorn validation complete",
                    extra={
                        "scan_id": scan_id,
                        "passed": matterhorn.passed,
                        "failed": matterhorn.failed,
                        "compliance": matterhorn.compliance_level,
                    },
                )
            except ImportError:
                logger.warning(
                    "pikepdf not installed - skipping Matterhorn validation",
                    extra={"scan_id": scan_id},
                )
            except Exception as e:
                logger.error(
                    "Matterhorn validation failed",
                    extra={"scan_id": scan_id, "error": str(e)},
                )

        db.commit()

        return {
            "success": True,
            "fixed_count": remediation_result.fixed_count,
            "manual_count": remediation_result.manual_count,
            "failed_count": remediation_result.failed_count,
            "skipped_count": remediation_result.skipped_count,
            "output_file": remediation_result.output_file,
            "backup_path": backup_path,
            "compliance_improvement": remediation_result.improvement,
            "upload_job_id": upload_job_id,
            "scan_id": scan_id,
        }

    except Exception as e:
        logger.error(f"Error processing remediation job: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "scan_id": scan_id,
        }

    finally:
        # Cleanup temp file and directory if downloaded from cloud
        if temp_file_path:
            try:
                Path(temp_file_path).unlink(missing_ok=True)
                # Also remove the parent temp directory
                parent = Path(temp_file_path).parent
                if parent.name.startswith("aelira_remediation_"):
                    import shutil

                    shutil.rmtree(parent, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed to cleanup temp file {temp_file_path}: {e}")


async def _download_cloud_file(
    cloud_file_id: str, department_id: str, db: Session
) -> Dict[str, Any]:
    """
    Download file from Google Drive or OneDrive to temp directory.

    Args:
        cloud_file_id: Cloud file ID
        department_id: Department ID
        db: Database session

    Returns:
        Dict with success, local_path, error
    """
    try:
        # Get cloud file record
        cloud_file = db.query(CloudFile).filter(CloudFile.id == cloud_file_id).first()
        if not cloud_file:
            return {"success": False, "error": f"Cloud file not found: {cloud_file_id}"}

        # Get OAuth credentials
        credential = (
            db.query(CloudOAuthCredentials)
            .filter(
                CloudOAuthCredentials.department_id == department_id,
                CloudOAuthCredentials.provider == cloud_file.provider,
                CloudOAuthCredentials.is_active,
            )
            .first()
        )

        if not credential:
            return {
                "success": False,
                "error": f"No active OAuth credentials for provider {cloud_file.provider}",
            }

        # Refresh token if needed (with distributed lock to prevent races)
        if credential.provider == CloudProvider.CANVAS.value:
            require_persisted_canvas_origin(credential)
        token_manager = OAuthTokenManager()
        access_token = await token_manager.refresh_if_expired(credential, db)

        # Create temp directory
        temp_dir = tempfile.mkdtemp(prefix="aelira_remediation_")
        local_path = Path(temp_dir) / (cloud_file.file_name or "file")

        # Download file
        if credential.provider == CloudProvider.GOOGLE.value:
            integration = GoogleDriveIntegration(
                access_token=access_token,
                department_id=department_id,
            )
            try:
                result = await integration.download_file(
                    file_id=cloud_file.provider_file_id,
                    local_path=str(local_path),
                )
                return {
                    "success": result.success,
                    "local_path": result.local_path,
                    "error": result.error,
                }
            finally:
                await integration.close()

        elif credential.provider == CloudProvider.MICROSOFT.value:
            integration = OneDriveIntegration(
                access_token=access_token,
                department_id=department_id,
            )
            try:
                result = await integration.download_file(
                    file_id=cloud_file.provider_file_id,
                    local_path=str(local_path),
                )
                return {
                    "success": result.success,
                    "local_path": result.local_path,
                    "error": result.error,
                }
            finally:
                await integration.close()

        elif credential.provider == CloudProvider.CANVAS.value:
            from ..integrations.canvas import CanvasAPIClient

            try:
                canvas_instance_url = require_persisted_canvas_origin(credential)
            except ValueError:
                return {
                    "success": False,
                    "error": PERSISTED_CANVAS_ORIGIN_ERROR,
                }

            api_client = CanvasAPIClient(
                canvas_instance_url=canvas_instance_url,
                access_token=access_token,
                credential_id=credential.id,
            )
            try:
                result = await api_client.download_file(
                    file_id=cloud_file.provider_file_id,
                    local_path=str(local_path),
                )
                return {
                    "success": result.success,
                    "local_path": result.local_path,
                    "error": result.error,
                }
            finally:
                await api_client.close()

        elif credential.provider == CloudProvider.BLACKBOARD.value:
            from ..integrations.blackboard import BlackboardAPIClient

            blackboard_instance_url = credential.provider_metadata.get(
                "blackboard_instance_url"
            )
            if not blackboard_instance_url:
                return {
                    "success": False,
                    "error": "Blackboard instance URL not found in credential metadata",
                }

            # Get course_id from cloud file metadata
            course_id = cloud_file.metadata.get("course_id")
            if not course_id:
                return {
                    "success": False,
                    "error": "Blackboard course ID not found in file metadata",
                }

            api_client = BlackboardAPIClient(
                blackboard_instance_url=blackboard_instance_url,
                access_token=access_token,
                credential_id=credential.id,
            )
            try:
                result = await api_client.download_file(
                    course_id=course_id,
                    content_id=cloud_file.provider_file_id,
                    local_path=str(local_path),
                )
                return {
                    "success": result.success,
                    "local_path": result.local_path,
                    "error": result.error,
                }
            finally:
                await api_client.close()

        else:
            return {
                "success": False,
                "error": f"Unsupported provider for file download: {credential.provider}",
            }

    except Exception as e:
        logger.error(f"Error downloading cloud file {cloud_file_id}: {e}")
        return {"success": False, "error": str(e)}


def _create_backup(file_path: str) -> str:
    """
    Create backup copy in backups/ directory.

    Args:
        file_path: Path to file to backup

    Returns:
        Path to backup file
    """
    file_path_obj = Path(file_path)
    backup_dir = file_path_obj.parent / "backups"
    backup_dir.mkdir(exist_ok=True, parents=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{file_path_obj.stem}_backup_{timestamp}{file_path_obj.suffix}"
    backup_path = backup_dir / backup_name

    shutil.copy2(file_path, backup_path)

    logger.info(f"Created backup: {backup_path}")
    return str(backup_path)


def _get_remediator_for_scan_type(
    scan_type: str, file_path: str, issues: List[Dict[str, Any]], use_ai: bool
) -> Optional[Any]:
    """
    Instantiate appropriate remediator based on scan type.

    Args:
        scan_type: Type of scan (PDF, WORD, POWERPOINT, EXCEL)
        file_path: Path to file
        issues: List of issues from scan
        use_ai: Whether to use AI for fix generation

    Returns:
        Remediator instance or None
    """
    try:
        from ..education.remediation.base import RemediationConfig

        config = RemediationConfig(use_ai=use_ai)

        # Map scan types to remediator classes
        if scan_type in ("PDF", "pdf"):
            from ..education.remediation.pdf_remediator import PdfRemediator

            return PdfRemediator(
                file_path=file_path, issues=issues, config=config, ai_client=None
            )

        elif scan_type in ("WORD", "word", "DOCX", "docx"):
            from ..education.remediation.docx_remediator import DocxRemediator

            return DocxRemediator(
                file_path=file_path, issues=issues, config=config, ai_client=None
            )

        elif scan_type in ("POWERPOINT", "powerpoint", "PPTX", "pptx"):
            from ..education.remediation.pptx_remediator import PptxRemediator

            return PptxRemediator(
                file_path=file_path, issues=issues, config=config, ai_client=None
            )

        elif scan_type in ("EXCEL", "excel", "XLSX", "xlsx"):
            from ..education.remediation.xlsx_remediator import XlsxRemediator

            return XlsxRemediator(
                file_path=file_path, issues=issues, config=config, ai_client=None
            )

        else:
            logger.warning(f"No remediator available for scan type: {scan_type}")
            return None

    except ImportError as e:
        logger.error(f"Failed to import remediator for {scan_type}: {e}")
        return None


async def _queue_upload_job(
    file_path: str,
    cloud_file_id: str,
    department_id: str,
    provider: str,
    db: Session,
) -> str:
    """
    Queue upload job after successful remediation.

    Args:
        file_path: Path to remediated file
        cloud_file_id: Cloud file ID
        department_id: Department ID
        provider: Cloud provider (google/microsoft)
        db: Database session

    Returns:
        Upload job ID
    """
    job_id = str(uuid.uuid4())

    upload_job = CloudJobQueue(
        id=job_id,
        department_id=department_id,
        job_type=CloudJobType.UPLOAD.value,
        provider=provider,
        status=CloudJobStatus.PENDING.value,
        priority=2,  # High priority
        job_data={
            "file_path": file_path,
            "cloud_file_id": cloud_file_id,
            "create_new_version": True,  # Per user preference
        },
        cloud_file_id=cloud_file_id,
    )

    db.add(upload_job)
    db.commit()

    logger.info(f"Queued upload job {job_id} for file {cloud_file_id}")
    return job_id


async def _send_remediation_notification(
    scan: Scan,
    result: Any,  # RemediationResult
    department_id: str,
    db: Session,
):
    """
    Send email notification based on remediation result.

    Args:
        scan: Scan record
        result: RemediationResult
        department_id: Department ID
        db: Database session
    """
    try:
        from ..services.alert_service import AlertService

        alert_service = AlertService()

        # Get department to find contact email
        from ..db.models import Department

        department = db.query(Department).filter(Department.id == department_id).first()
        if not department:
            logger.warning(f"Department {department_id} not found for notifications")
            return

        to_emails = [str(department.contact_email)]

        # Determine notification type based on result
        if result.failed_count == 0 and result.fixed_count > 0:
            # Full success
            await alert_service.send_scan_complete_alert(
                to_emails=to_emails,
                scan_id=str(scan.id),
                file_name=str(scan.file_name),
                issues_found=result.manual_count,  # Issues still needing manual work
                compliance_score=result.remediated_compliance_score or 0.0,
                scan_url=f"/scans/{scan.id}",
                department_id=department_id,
                db=db,
            )
            logger.info(f"Sent success notification for scan {scan.id}")

        elif result.fixed_count > 0 and result.failed_count > 0:
            # Partial success
            await alert_service.send_remediation_partial_success_alert(
                to_emails=to_emails,
                scan_id=str(scan.id),
                file_name=str(scan.file_name),
                fixed_count=result.fixed_count,
                failed_count=result.failed_count,
                manual_count=result.manual_count,
                fixed_issues=[
                    {"description": fi.description} for fi in result.fixed_issues
                ],
                failed_issues=result.failed_issues,
                scan_url=f"/scans/{scan.id}",
                department_id=department_id,
                db=db,
            )
            logger.info(
                f"Sent partial success notification for scan {scan.id} "
                f"(fixed={result.fixed_count}, failed={result.failed_count})"
            )

        elif result.failed_count > 0 and result.fixed_count == 0:
            # Complete failure
            if result.failed_issues:
                error_message = result.failed_issues[0].get(
                    "error", "Automatic remediation was unable to fix this document."
                )
            else:
                error_message = (
                    "Automatic remediation was unable to fix any issues "
                    "in this document."
                )
            await alert_service.send_remediation_failure_alert(
                to_emails=to_emails,
                scan_id=str(scan.id),
                file_name=str(scan.file_name),
                error_message=error_message,
                scan_url=f"/scans/{scan.id}",
                department_id=department_id,
                db=db,
            )
            logger.info(
                f"Sent failure notification for scan {scan.id} "
                f"(failed={result.failed_count})"
            )

    except Exception as e:
        logger.error(f"Error sending remediation notification: {e}", exc_info=True)
        # Don't raise - email failure shouldn't break remediation


async def handle_remediation_job(
    job: Any,  # CloudJobQueue
    db: Session,
    token_manager: Any,  # OAuthTokenManager
) -> Dict[str, Any]:
    """
    Job handler for remediation jobs (matches JobProcessor signature).

    Builds job_data from CloudJobQueue columns since the model has no
    job_data column — the needed fields are spread across the job record.

    Args:
        job: CloudJobQueue instance
        db: Database session
        token_manager: OAuth token manager (not used, but required by signature)

    Returns:
        Remediation results
    """
    job_data = {
        "cloud_file_id": job.cloud_file_id,
        "department_id": job.department_id,
        "provider": job.provider,
        "upload_to_cloud": True,
    }
    # Extract scan_id from result_data if a prior scan was run
    if job.result_data and isinstance(job.result_data, dict):
        job_data["scan_id"] = job.result_data.get("scan_id")
    return await process_remediation_job(job_data, db)


__all__ = ["process_remediation_job", "handle_remediation_job"]
