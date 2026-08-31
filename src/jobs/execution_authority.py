"""Cross-process execution authority for killable durable-job children."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

_CANCEL_REQUESTED_CODE = "scan_cancel_requested"


def claim_advisory_lock_key(job_id: str, claim_token: str) -> int:
    """Map one immutable claim to a stable signed PostgreSQL bigint key."""
    digest = hashlib.sha256(f"{job_id}\0{claim_token}".encode()).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


def _is_postgresql(db: Session) -> bool:
    bind = db.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", None) == "postgresql"


@dataclass
class ChildExecutionAuthority:
    """A session lock pinned to one retained PostgreSQL connection."""

    connection: Connection | None
    key: int | None
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.connection is None or self.key is None:
            return
        connection = self.connection
        try:
            unlocked = connection.scalar(
                text("SELECT pg_advisory_unlock(:key)"), {"key": self.key}
            )
            if unlocked is not True:
                raise RuntimeError("child execution authority unlock failed")
        except Exception:
            # Session advisory locks survive ordinary rollback and pool return.
            # Invalidate the exact retained connection so server disconnect is
            # the fallback release mechanism and the pool cannot inherit it.
            connection.invalidate()
            raise
        finally:
            connection.close()


def acquire_child_execution_lock(
    db: Session, *, job_id: str, claim_token: str
) -> ChildExecutionAuthority:
    """Acquire child authority on one dedicated retained connection."""
    if not _is_postgresql(db):
        return ChildExecutionAuthority(None, None)
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    if not isinstance(engine, Engine):
        raise RuntimeError("PostgreSQL child authority requires an Engine")
    connection = engine.connect()
    key = claim_advisory_lock_key(job_id, claim_token)
    try:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
    except Exception:
        connection.invalidate()
        connection.close()
        raise
    return ChildExecutionAuthority(connection, key)


def try_acquire_recovery_lock(db: Session, *, job_id: str, claim_token: str) -> bool:
    """Prove child death for this transaction; commit/rollback releases it."""
    if not _is_postgresql(db):
        return False
    key = claim_advisory_lock_key(job_id, claim_token)
    acquired = db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key})
    return acquired is True


def claim_is_current(
    db: Session,
    *,
    job_id: str,
    claim_token: str,
    worker_id: str,
    lock_row: bool,
) -> bool:
    suffix = " FOR UPDATE" if lock_row else ""
    row = db.execute(
        text(
            "SELECT id FROM cloud_job_queue "
            "WHERE id = :job_id AND status = 'processing' "
            "AND claim_token = :claim_token AND worker_id = :worker_id "
            "AND (last_error_code IS NULL "
            "OR last_error_code != :cancel_code)" + suffix
        ),
        {
            "job_id": job_id,
            "claim_token": claim_token,
            "worker_id": worker_id,
            "cancel_code": _CANCEL_REQUESTED_CODE,
        },
    ).first()
    return row is not None


def install_child_commit_fence(
    *, job_id: str, claim_token: str, worker_id: str
) -> Callable[[], None]:
    """Fence every Session commit made by legacy code in this child process."""

    def before_commit(session: Session) -> None:
        if not claim_is_current(
            session,
            job_id=job_id,
            claim_token=claim_token,
            worker_id=worker_id,
            lock_row=True,
        ):
            raise RuntimeError("job ownership lost before child commit")

    event.listen(Session, "before_commit", before_commit)

    def remove() -> None:
        event.remove(Session, "before_commit", before_commit)

    return remove


def attach_child_checker(
    job: Any, db: Session, *, job_id: str, claim_token: str, worker_id: str
) -> None:
    """Attach the normal async ownership hook to a freshly loaded child row."""

    async def assert_owned() -> None:
        if not claim_is_current(
            db,
            job_id=job_id,
            claim_token=claim_token,
            worker_id=worker_id,
            lock_row=False,
        ):
            from src.jobs.contracts import LostJobOwnership

            raise LostJobOwnership("child claim ownership lost")

    setattr(job, "_assert_owned", assert_owned)
