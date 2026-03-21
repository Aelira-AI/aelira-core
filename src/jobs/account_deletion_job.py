"""
Account Deletion Background Job

Processes scheduled account deletions after the 30-day grace period.
Runs on a schedule (e.g., hourly via cron or APScheduler).

Usage:
    from src.jobs.account_deletion_job import process_scheduled_deletions
    process_scheduled_deletions()  # Processes all expired grace periods
"""

import logging
from datetime import datetime, timezone

from ..db.database import get_db
from ..db.models import User
from ..services.account_deletion_service import get_account_deletion_service

logger = logging.getLogger(__name__)


def process_scheduled_deletions() -> int:
    """
    Find and execute all scheduled deletions that have passed their grace period.

    Returns:
        Number of accounts deleted
    """
    with get_db() as db:
        try:
            now = datetime.now(timezone.utc)

            # Find users with expired grace periods
            users_to_delete = (
                db.query(User)
                .filter(
                    User.deletion_scheduled_for.isnot(None),
                    User.deletion_scheduled_for <= now,
                    User.is_active == False,  # noqa: E712
                )
                .all()
            )

            if not users_to_delete:
                logger.debug("No scheduled deletions to process")
                return 0

            logger.info(
                f"Processing {len(users_to_delete)} scheduled account deletions"
            )

            service = get_account_deletion_service()
            deleted_count = 0

            for user in users_to_delete:
                try:
                    success = service.execute_scheduled_deletion(db, user.id)
                    if success:
                        deleted_count += 1
                        logger.info(f"Executed scheduled deletion for user {user.id}")
                    else:
                        logger.warning(
                            f"Skipped deletion for user {user.id} (not eligible)"
                        )
                except Exception as e:
                    logger.error(f"Failed to delete user {user.id}: {e}")
                    # Continue processing other users
                    db.rollback()

            logger.info(
                f"Completed scheduled deletions: {deleted_count}/{len(users_to_delete)} processed"
            )
            return deleted_count

        except Exception as e:
            logger.error(f"Error processing scheduled deletions: {e}")
            return 0
