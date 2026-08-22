"""Container health probe for the dedicated durable queue worker."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from src.db.database import SessionLocal
from src.db.models import WorkerHeartbeat


def main() -> None:
    worker_id = os.environ.get("JOB_WORKER_ID")
    if not worker_id:
        raise SystemExit(1)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    with SessionLocal() as db:
        heartbeat = db.get(WorkerHeartbeat, worker_id)
        healthy = bool(
            heartbeat is not None
            and heartbeat.status in {"running", "draining"}
            and heartbeat.heartbeat_at is not None
            and heartbeat.heartbeat_at >= cutoff
        )
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    main()
