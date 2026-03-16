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

from sqlalchemy import update

from src.db.database import get_db
from src.db.models import Scan, ScanStatus

logger = logging.getLogger(__name__)

SCAN_TIMEOUT_MINUTES = int(os.getenv("SCAN_TIMEOUT_MINUTES", "30"))
SCAN_TIMEOUT_CHECK_INTERVAL = int(os.getenv("SCAN_TIMEOUT_CHECK_INTERVAL", "300"))


def fail_stale_scans() -> int:
    """
    Find and fail scans stuck in PROCESSING or PENDING state.

    Returns:
        Number of scans marked as failed.
    """
    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=SCAN_TIMEOUT_MINUTES)
    failed_count = 0

    with get_db() as db:
        # Find scans stuck in PROCESSING
        stale_processing = (
            db.query(Scan)
            .filter(
                Scan.status == ScanStatus.PROCESSING,
                Scan.created_at < stale_cutoff,
            )
            .all()
        )

        # Find scans stuck in PENDING (never picked up)
        stale_pending = (
            db.query(Scan)
            .filter(
                Scan.status == ScanStatus.PENDING,
                Scan.created_at < stale_cutoff,
            )
            .all()
        )

        stale_scans = stale_processing + stale_pending

        if not stale_scans:
            return 0

        now = datetime.now(timezone.utc)

        for scan in stale_scans:
            age_minutes = (now - scan.created_at).total_seconds() / 60
            previous_status = scan.status

            scan.status = ScanStatus.FAILED
            scan.completed_at = now
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

        db.commit()

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
