"""Singleton orchestration for bounded durable maintenance work."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

_MAINTENANCE_ADVISORY_KEY = 8_315_741_702_017


class DurableMaintenanceRunner:
    """Run cleanup and quarantine under one PostgreSQL session lock."""

    def __init__(
        self,
        *,
        cleanup: Any,
        orphan_scanner: Any,
        reconciliation: Any | None = None,
    ) -> None:
        self.cleanup = cleanup
        self.orphan_scanner = orphan_scanner
        self.reconciliation = reconciliation

    def run_once(self, db: Any, *, now: datetime | None = None) -> dict[str, Any]:
        acquired = db.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": _MAINTENANCE_ADVISORY_KEY},
        ).scalar_one()
        if acquired is not True:
            db.rollback()
            return {"acquired": False}
        now = now or datetime.now(timezone.utc)
        try:
            cleanup = self.cleanup.run_batch(db, now=now)
            recovery = self.orphan_scanner.recover_pending(db, now=now)
            quarantine = self.orphan_scanner.run_batch(db, now=now)
            purge = self.orphan_scanner.purge_reviewed(db, now=now)
            reconciliation = (
                self.reconciliation.backfill(db, now=now)
                if self.reconciliation is not None
                else 0
            )
            return {
                "acquired": True,
                "cleanup": cleanup,
                "quarantine_recovery": recovery,
                "quarantine": quarantine,
                "purge": purge,
                "reconciliation_enqueued": reconciliation,
            }
        finally:
            db.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": _MAINTENANCE_ADVISORY_KEY},
            )
            db.commit()


__all__ = ["DurableMaintenanceRunner"]
