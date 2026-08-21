"""Operational status for the dedicated durable queue workers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db.database import get_db_dependency
from src.db.models import CloudJobQueue, WorkerHeartbeat
from src.auth.canvas_permissions import require_account_management
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal

router = APIRouter(prefix="/api/jobs", tags=["Job workers"])


@router.get("/worker-status")
def worker_status(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db_dependency),
):
    """Return bounded queue depth and aggregate worker liveness metrics."""
    # Operational queue depth and worker topology are account-management data,
    # not a course-scoped health surface.
    require_account_management(principal)
    queue = {
        status: count
        for status, count in db.query(
            CloudJobQueue.status, func.count(CloudJobQueue.id)
        ).group_by(CloudJobQueue.status)
    }
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    live_workers = (
        db.query(func.count(WorkerHeartbeat.worker_id))
        .filter(
            WorkerHeartbeat.status.in_(("running", "draining")),
            WorkerHeartbeat.heartbeat_at >= cutoff,
        )
        .scalar()
        or 0
    )
    draining_workers = (
        db.query(func.count(WorkerHeartbeat.worker_id))
        .filter(
            WorkerHeartbeat.status == "draining",
            WorkerHeartbeat.heartbeat_at >= cutoff,
        )
        .scalar()
        or 0
    )
    latest = db.query(func.max(WorkerHeartbeat.heartbeat_at)).scalar()
    return {
        "status": "healthy" if live_workers else "degraded",
        "queue": {
            "pending": queue.get("pending", 0),
            "processing": queue.get("processing", 0),
            "completed": queue.get("completed", 0),
            "failed": queue.get("failed", 0),
        },
        "workers": {
            "live": live_workers,
            "draining": draining_workers,
            "latest_heartbeat_at": latest,
        },
    }
