"""Container health probe for the dedicated durable queue worker."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from src.db.database import SessionLocal
from src.db.models import CloudJobQueue, WorkerHeartbeat
from src.jobs.job_processor import build_runnable_pending_query
from src.jobs.operational_health import classify_worker_health
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


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("liveness", "readiness"),
        default="readiness",
        help="probe a fresh heartbeat only or the complete queue readiness state",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a bounded machine-readable result without worker or job identity",
    )
    return parser.parse_args(argv)


def _emit(*, mode: str, health_state: str, machine_readable: bool) -> None:
    if not machine_readable:
        return
    print(
        json.dumps(
            {
                "mode": mode,
                "status": (
                    "healthy"
                    if health_state == "live" or health_state.startswith("healthy_")
                    else "degraded"
                ),
                "health_state": health_state,
            },
            sort_keys=True,
        )
    )


def main(argv: list[str] | None = None) -> None:
    args = _arguments([] if argv is None else argv)
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
        heartbeat_at = _aware(heartbeat.heartbeat_at if heartbeat is not None else None)
        live = bool(
            heartbeat is not None
            and heartbeat.status in {"running", "draining"}
            and heartbeat_at is not None
            and heartbeat_at >= cutoff
        )
        if args.mode == "liveness":
            health_state = "live" if live else "worker_unavailable"
            _emit(
                mode=args.mode,
                health_state=health_state,
                machine_readable=args.json,
            )
            raise SystemExit(0 if live else 1)
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
        expired_owned = 0
        stalled_owned = 0
        for job in owned_processing:
            lease = job.lease_expires_at
            claimed = job.claimed_at
            if lease is not None and lease.tzinfo is None:
                lease = lease.replace(tzinfo=timezone.utc)
            if claimed is not None and claimed.tzinfo is None:
                claimed = claimed.replace(tzinfo=timezone.utc)
            if lease is None or lease < now:
                expired_owned += 1
            elif claimed is None or claimed < stalled_cutoff:
                stalled_owned += 1
        health_state = classify_worker_health(
            live_workers=int(live),
            runnable_pending=int(runnable),
            processing_count=len(owned_processing),
            expired_processing=expired_owned,
            stalled_processing=stalled_owned,
            latest_progress=watermark,
            cutoff=cutoff,
        )
        healthy = health_state.startswith("healthy_")
        _emit(
            mode=args.mode,
            health_state=health_state,
            machine_readable=args.json,
        )
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
