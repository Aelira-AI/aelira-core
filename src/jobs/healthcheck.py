"""Container health probe for the dedicated durable queue worker."""

from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from src.db.database import SessionLocal
from src.db.models import CloudJobQueue, WorkerHeartbeat
from src.jobs.job_processor import build_runnable_pending_query
from src.jobs.registry import EXECUTABLE_JOB_TYPES


def _progress_watermark(heartbeat: WorkerHeartbeat) -> datetime | None:
    metadata = heartbeat.metadata_json
    raw = metadata.get("progress_watermark_at") if isinstance(metadata, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def main() -> None:
    worker_id = os.environ.get("JOB_WORKER_ID")
    if not worker_id:
        try:
            worker_id = (
                Path("/tmp/aelira-worker-id").read_text(encoding="utf-8").strip()
            )
        except OSError:
            worker_id = None
    if not worker_id:
        raise SystemExit(1)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    with SessionLocal() as db:
        heartbeat = db.get(WorkerHeartbeat, worker_id)
        live = bool(
            heartbeat is not None
            and heartbeat.status in {"running", "draining"}
            and heartbeat.heartbeat_at is not None
            and heartbeat.heartbeat_at >= cutoff
        )
        runnable_ids = build_runnable_pending_query(
            EXECUTABLE_JOB_TYPES, now=datetime.now(timezone.utc)
        ).subquery()
        runnable = bool(db.scalar(select(func.count()).select_from(runnable_ids)))
        owned_processing = (
            db.query(CloudJobQueue)
            .filter(
                CloudJobQueue.status == "processing",
                CloudJobQueue.worker_id == worker_id,
            )
            .all()
        )
        watermark = _progress_watermark(heartbeat) if heartbeat is not None else None
        now = datetime.now(timezone.utc)
        try:
            execution_seconds = float(
                os.environ.get("JOB_WORKER_MAX_EXECUTION_SECONDS", "3600")
            )
        except ValueError:
            execution_seconds = 3600.0
        execution_seconds = min(86400.0, max(1.0, execution_seconds))
        stalled_cutoff = now - timedelta(seconds=execution_seconds + 120.0)
        expired_owned = False
        stalled_owned = False
        for job in owned_processing:
            lease = job.lease_expires_at
            claimed = job.claimed_at
            if lease is not None and lease.tzinfo is None:
                lease = lease.replace(tzinfo=timezone.utc)
            if claimed is not None and claimed.tzinfo is None:
                claimed = claimed.replace(tzinfo=timezone.utc)
            if lease is None or lease < now:
                expired_owned = True
            elif claimed is None or claimed < stalled_cutoff:
                stalled_owned = True
        stalled_runnable = bool(
            runnable
            and not owned_processing
            and (watermark is None or watermark < cutoff)
        )
        healthy = (
            live and not expired_owned and not stalled_owned and not stalled_runnable
        )
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    main()
