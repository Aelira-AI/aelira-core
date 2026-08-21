"""
Background Job Processor

Processes jobs from the cloud_job_queue table:
- Fetches pending jobs in priority order
- Executes job handlers
- Updates job status and results
- Handles retries and error recovery

Can run as:
1. Background thread within FastAPI
2. Standalone worker process
3. Celery task (if using Celery)
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Callable
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import (
    CloudJobQueue,
    CloudJobStatus,
    CloudJobType,
)
from ..integrations.oauth_token_manager import OAuthTokenManager
import os

logger = logging.getLogger(__name__)

# Stale job recovery settings
STALE_JOB_THRESHOLD_MINUTES = int(os.getenv("STALE_JOB_THRESHOLD_MINUTES", "30"))
STALE_JOB_RECOVERY_INTERVAL = int(
    os.getenv("STALE_JOB_RECOVERY_INTERVAL", "300")
)  # 5 minutes


class JobProcessor:
    """
    Background job processor for cloud integration tasks.

    Processes jobs from the cloud_job_queue table in priority order.
    """

    def __init__(
        self,
        batch_size: int = 10,
        poll_interval: float = 5.0,
        max_retries: int = 3,
    ):
        """
        Initialize job processor.

        Args:
            batch_size: Number of jobs to process per batch
            poll_interval: Seconds between polling for new jobs
            max_retries: Maximum retry attempts for failed jobs
        """
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self._running = False
        self._handlers: Dict[str, Callable] = {}
        self._token_manager = None

    def _get_token_manager(self) -> OAuthTokenManager:
        """Get OAuth token manager (lazy initialization)."""
        if self._token_manager is None:
            encryption_key = os.environ.get("TOKEN_ENCRYPTION_KEY")
            if encryption_key:
                self._token_manager = OAuthTokenManager(encryption_key)
            else:
                raise ValueError("TOKEN_ENCRYPTION_KEY not configured")
        return self._token_manager

    def register_handler(self, job_type: str, handler: Callable):
        """
        Register a handler function for a job type.

        Args:
            job_type: Job type (from CloudJobType enum)
            handler: Async function that processes the job
        """
        self._handlers[job_type] = handler
        logger.info(f"Registered handler for job type: {job_type}")

    async def start(self):
        """Start the job processor loop."""
        self._running = True
        self._last_recovery_check = datetime.now(timezone.utc)
        logger.info("Job processor started")

        # Recover any stale jobs from previous runs on startup
        await self.recover_stale_jobs()

        while self._running:
            try:
                # Periodically check for stale jobs
                now = datetime.now(timezone.utc)
                if (
                    now - self._last_recovery_check
                ).total_seconds() >= STALE_JOB_RECOVERY_INTERVAL:
                    await self.recover_stale_jobs()
                    self._last_recovery_check = now

                # Process a batch of pending jobs
                processed = await self._process_batch()

                if processed == 0:
                    # No jobs found, wait before polling again
                    await asyncio.sleep(self.poll_interval)

            except Exception as exc:
                logger.error(
                    "Job processor error",
                    extra={"error_type": type(exc).__name__},
                )
                await asyncio.sleep(self.poll_interval)

    def stop(self):
        """Stop the job processor loop."""
        self._running = False
        logger.info("Job processor stopping")

    async def recover_stale_jobs(self, stale_threshold_minutes: int = None) -> int:
        """
        Recover jobs stuck in 'processing' state for too long.

        This handles scenarios where:
        - The server crashed while processing a job
        - A job hung and never completed
        - Network issues caused the processor to lose track of a job

        Jobs are either reset to 'pending' for retry (if under max_retries)
        or marked as 'failed' (if max retries exceeded).

        Args:
            stale_threshold_minutes: Minutes after which a processing job is considered stale.
                                    Defaults to STALE_JOB_THRESHOLD_MINUTES env var (30 min).

        Returns:
            Number of stale jobs recovered
        """
        if stale_threshold_minutes is None:
            stale_threshold_minutes = STALE_JOB_THRESHOLD_MINUTES

        stale_time = datetime.now(timezone.utc) - timedelta(
            minutes=stale_threshold_minutes
        )
        recovered_count = 0

        with get_db() as db:
            # Find jobs stuck in 'processing' state
            stale_jobs = (
                db.query(CloudJobQueue)
                .filter(
                    CloudJobQueue.status == CloudJobStatus.PROCESSING.value,
                    CloudJobQueue.started_at < stale_time,
                )
                .all()
            )

            if not stale_jobs:
                return 0

            logger.warning(f"Found {len(stale_jobs)} stale jobs to recover")

            for job in stale_jobs:
                try:
                    if job.retry_count < job.max_retries:
                        # Reset to pending for retry
                        job.status = CloudJobStatus.PENDING.value
                        job.retry_count += 1
                        job.error_message = (
                            f"Job timed out after {stale_threshold_minutes} minutes "
                            f"(retry {job.retry_count}/{job.max_retries})"
                        )
                        # Schedule retry with exponential backoff
                        backoff_minutes = min(5 * (2 ** (job.retry_count - 1)), 30)
                        job.scheduled_for = datetime.now(timezone.utc) + timedelta(
                            minutes=backoff_minutes
                        )
                        job.progress = 0

                        logger.warning(
                            f"Recovered stale job {job.id} (type={job.job_type}), "
                            f"retry {job.retry_count}/{job.max_retries}, "
                            f"scheduled in {backoff_minutes} minutes"
                        )
                        recovered_count += 1
                    else:
                        # Max retries exceeded, mark as failed
                        job.status = CloudJobStatus.FAILED.value
                        job.completed_at = datetime.now(timezone.utc)
                        job.error_message = (
                            f"Job failed after {job.max_retries} retries. "
                            f"Last failure: timed out after {stale_threshold_minutes} minutes"
                        )

                        logger.error(
                            f"Job {job.id} (type={job.job_type}) failed after "
                            f"{job.max_retries} retries"
                        )
                        recovered_count += 1

                except Exception as exc:
                    logger.error(
                        "Failed to recover stale job",
                        extra={"job_id": job.id, "error_type": type(exc).__name__},
                    )

            db.commit()

        if recovered_count > 0:
            logger.info(f"Recovered {recovered_count} stale jobs")

        return recovered_count

    async def _process_batch(self) -> int:
        """
        Process a batch of pending jobs.

        Returns:
            Number of jobs processed
        """
        with get_db() as db:
            # Fetch pending jobs in priority order
            jobs = (
                db.query(CloudJobQueue)
                .filter(
                    CloudJobQueue.status == CloudJobStatus.PENDING.value,
                    CloudJobQueue.scheduled_for <= datetime.now(timezone.utc),
                )
                .order_by(
                    CloudJobQueue.priority.asc(),
                    CloudJobQueue.created_at.asc(),
                )
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
                .all()
            )

            if not jobs:
                return 0

            processed = 0
            for job in jobs:
                try:
                    await self._process_job(job, db)
                    processed += 1
                except Exception as exc:
                    logger.error(
                        "Job batch item failed",
                        extra={"job_id": job.id, "error_type": type(exc).__name__},
                    )

            return processed

    async def _process_job(self, job: CloudJobQueue, db: Session):
        """
        Process a single job.

        Args:
            job: Job to process
            db: Database session
        """
        # Mark as processing
        job.status = CloudJobStatus.PROCESSING.value
        job.started_at = datetime.now(timezone.utc)
        job.progress = 0
        db.commit()

        logger.info(f"Processing job {job.id} (type={job.job_type})")

        try:
            # Get handler for job type
            handler = self._handlers.get(job.job_type)
            if not handler:
                raise ValueError(f"No handler registered for job type: {job.job_type}")

            # Execute handler
            result = await handler(job, db, self._get_token_manager())

            # Remediation owns its atomic artifact/outcome/job completion commit.
            if getattr(result, "handler_committed", False) is True:
                logger.info(f"Job {job.id} completed successfully")
                return

            # Mark as completed
            job.status = CloudJobStatus.COMPLETED.value
            job.completed_at = datetime.now(timezone.utc)
            job.progress = 100
            job.result_data = result
            db.commit()

            logger.info(f"Job {job.id} completed successfully")

        except Exception as exc:
            # Import locally to avoid coupling module initialization while still
            # recognizing the real typed terminal failure (including subclasses).
            from .remediation_job import (
                RemediationJobFailed,
                RetryableRemediationJobError,
                transition_retryable_remediation_job,
            )

            if (
                isinstance(exc, RemediationJobFailed)
                and exc.terminal_state_committed is True
            ):
                logger.warning(
                    "Remediation job reached a committed terminal failure",
                    extra={
                        "job_id": job.id,
                        "job_type": job.job_type,
                        "error_code": exc.code,
                    },
                )
                return

            if isinstance(exc, RetryableRemediationJobError):
                transition_retryable_remediation_job(job, db, exc)
                logger.warning(
                    "Remediation job queued after transient failure",
                    extra={
                        "job_id": job.id,
                        "job_type": job.job_type,
                        "error_code": exc.code,
                    },
                )
                return

            # A handler may leave the session in a failed transaction. Clear it
            # before touching queue state, then reload the job when supported.
            db.rollback()
            try:
                db.refresh(job)
            except Exception as refresh_exc:
                logger.warning(
                    "Could not refresh job after rollback",
                    extra={
                        "job_id": job.id,
                        "error_type": type(refresh_exc).__name__,
                    },
                )

            # Uncommitted remediation failures include terminal-commit failures;
            # preserve the ordinary transient retry path for them.
            deterministic_failure = (
                isinstance(exc, RemediationJobFailed) and exc.__cause__ is None
            )
            error_code = (
                exc.code
                if isinstance(exc, RemediationJobFailed)
                else "job_processing_failed"
            )
            logger.error(
                "Job failed",
                extra={
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "error_type": type(exc).__name__,
                    "error_code": error_code,
                },
            )

            # Update retry count
            job.retry_count += 1
            job.error_message = error_code

            if deterministic_failure or job.retry_count >= job.max_retries:
                # Deterministic remediation failures cannot succeed on retry.
                job.status = CloudJobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                logger.warning(f"Job {job.id} failed after {job.retry_count} retries")
            else:
                # Reset to pending for retry
                job.status = CloudJobStatus.PENDING.value
                job.progress = 0
                logger.info(
                    f"Job {job.id} will retry ({job.retry_count}/{job.max_retries})"
                )

            db.commit()

    async def process_single_job(self, job_id: str) -> Dict[str, Any]:
        """
        Process a single job by ID (for manual processing).

        Args:
            job_id: Job ID to process

        Returns:
            Job result data
        """
        with get_db() as db:
            job = db.query(CloudJobQueue).filter(CloudJobQueue.id == job_id).first()

            if not job:
                raise ValueError(f"Job not found: {job_id}")

            await self._process_job(job, db)

            return {
                "job_id": job.id,
                "status": job.status,
                "result": job.result_data,
                "error": job.error_message,
            }


# Global job processor instance
_job_processor: Optional[JobProcessor] = None


def get_job_processor() -> JobProcessor:
    """Get or create the global job processor instance."""
    global _job_processor
    if _job_processor is None:
        _job_processor = JobProcessor()
        # Register default handlers
        from .cloud_sync_job import handle_sync_job
        from .cloud_scan_job import handle_scan_job
        from .remediation_job import handle_remediation_job
        from .upload_job import handle_upload_job

        _job_processor.register_handler(CloudJobType.SYNC.value, handle_sync_job)
        _job_processor.register_handler(CloudJobType.SCAN.value, handle_scan_job)
        _job_processor.register_handler(
            CloudJobType.REMEDIATE.value, handle_remediation_job
        )
        _job_processor.register_handler(CloudJobType.UPLOAD.value, handle_upload_job)
    return _job_processor


async def process_pending_jobs(batch_size: int = 10) -> int:
    """
    Process pending jobs (convenience function for manual invocation).

    Args:
        batch_size: Number of jobs to process

    Returns:
        Number of jobs processed
    """
    processor = get_job_processor()
    processor.batch_size = batch_size
    return await processor._process_batch()


# Background task runner for FastAPI
async def start_job_processor_background():
    """Start job processor as a background task."""
    processor = get_job_processor()
    await processor.start()
