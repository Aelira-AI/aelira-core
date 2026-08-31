"""Automatic timeout for stuck scans.

Periodically checks for scans stuck in PROCESSING or PENDING state
beyond a configurable threshold and marks them as FAILED.

Usage:
    Start the background loop in FastAPI startup:

        from src.jobs.scan_timeout import start_scan_timeout_loop
        asyncio.create_task(start_scan_timeout_loop())

Environment variables:
    SCAN_TIMEOUT_MINUTES: Minutes before a scan is considered stuck (default: 30)
    SCAN_TIMEOUT_CHECK_INTERVAL: Seconds between checks (default: 300 = 5 min)
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, select
from src.db.database import get_db
from src.db.models import CloudJobQueue, Scan, ScanStatus

logger = logging.getLogger(__name__)

SCAN_TIMEOUT_MINUTES = int(os.getenv("SCAN_TIMEOUT_MINUTES", "30"))
SCAN_TIMEOUT_CHECK_INTERVAL = int(os.getenv("SCAN_TIMEOUT_CHECK_INTERVAL", "300"))
SCAN_TIMEOUT_BATCH_SIZE = 100


def build_stale_scan_query(
    stale_cutoff: datetime, *, limit: int = SCAN_TIMEOUT_BATCH_SIZE
):
    """Build one bounded query excluding only currently queue-owned scans."""
    active_queue_owner = exists(
        select(CloudJobQueue.id).where(
            CloudJobQueue.job_type == "scan",
            CloudJobQueue.status.in_(("pending", "processing")),
            CloudJobQueue.payload["scan_id"].as_string() == Scan.id,
        )
    )
    return (
        select(Scan)
        .where(
            Scan.status.in_((ScanStatus.PROCESSING, ScanStatus.PENDING)),
            Scan.created_at < stale_cutoff,
            ~active_queue_owner,
        )
        .order_by(Scan.created_at.asc(), Scan.id.asc())
        .limit(limit)
        .with_for_update(of=Scan, skip_locked=True)
    )


def _has_active_queue_owner(db, scan_id: str) -> bool:
    """Recheck ownership after the scan row is locked and before mutation."""
    return (
        db.scalar(
            select(CloudJobQueue.id)
            .where(
                CloudJobQueue.job_type == "scan",
                CloudJobQueue.status.in_(("pending", "processing")),
                CloudJobQueue.payload["scan_id"].as_string() == scan_id,
            )
            .limit(1)
        )
        is not None
    )


def fail_stale_scans() -> int:
    """
    Find and fail scans stuck in PROCESSING or PENDING state.

    Returns:
        Number of scans marked as failed.
    """
    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=SCAN_TIMEOUT_MINUTES)
    failed_count = 0

    with get_db() as db:
        stale_scans = list(db.scalars(build_stale_scan_query(stale_cutoff)).all())

        if not stale_scans:
            return 0

        now = datetime.now(timezone.utc)

        for scan in stale_scans:
            # Enqueue takes this same Scan row lock.  This atomic recheck means
            # either a new queue owner publishes first and we skip, or timeout
            # publishes FAILED first and the enqueuer rejects the terminal scan.
            if _has_active_queue_owner(db, str(scan.id)):
                continue
            age_minutes = (now - scan.created_at).total_seconds() / 60
            previous_status = scan.status
            terminal_job = db.scalar(
                select(CloudJobQueue)
                .where(
                    CloudJobQueue.job_type == "scan",
                    CloudJobQueue.status.in_(("completed", "failed")),
                    CloudJobQueue.payload["scan_id"].as_string() == str(scan.id),
                )
                .order_by(CloudJobQueue.completed_at.desc().nullslast())
                .limit(1)
            )

            scan.status = ScanStatus.FAILED
            scan.completed_at = now
            if terminal_job is not None:
                queue_status = str(terminal_job.status)
                queue_error = getattr(terminal_job, "last_error_code", None)
                scan.error_message = (
                    str(queue_error)[:128]
                    if queue_status == "failed" and isinstance(queue_error, str)
                    else "scan_queue_terminal_disagreement"
                )
                scan.progress_message = "Scan failed"
            else:
                scan.error_message = (
                    f"Scan timed out after {int(age_minutes)} minutes "
                    f"(was {previous_status}, threshold: {SCAN_TIMEOUT_MINUTES}m). "
                    f"Please retry your scan."
                )

            logger.warning(
                "Timed out stale scan",
                extra={
                    "scan_id": scan.id,
                    "file_name": scan.file_name,
                    "previous_status": previous_status,
                    "age_minutes": int(age_minutes),
                    "user_id": scan.user_id,
                    "department_id": scan.department_id,
                },
            )
            failed_count += 1

        if failed_count:
            db.commit()
        else:
            db.rollback()

    if failed_count > 0:
        logger.info(
            f"Scan timeout: marked {failed_count} stale scan(s) as FAILED "
            f"(threshold: {SCAN_TIMEOUT_MINUTES}m)"
        )

    return failed_count


async def start_scan_timeout_loop():
    """
    Background loop that periodically checks for and fails stale scans.

    Runs indefinitely. Safe to call via asyncio.create_task() at startup.
    """
    logger.info(
        f"Scan timeout monitor started "
        f"(timeout: {SCAN_TIMEOUT_MINUTES}m, check interval: {SCAN_TIMEOUT_CHECK_INTERVAL}s)"
    )

    # Wait a bit after startup before first check (let DB initialize)
    await asyncio.sleep(30)

    while True:
        try:
            count = fail_stale_scans()
            if count > 0:
                logger.info(f"Scan timeout check: failed {count} stale scan(s)")
        except Exception as e:
            logger.error(f"Scan timeout check error: {e}")

        await asyncio.sleep(SCAN_TIMEOUT_CHECK_INTERVAL)
