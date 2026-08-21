"""Dedicated durable Canvas reconciliation handler."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .contracts import JobFailure


async def handle_reconciliation_job(
    job: Any, db: Session, token_manager: Any
) -> dict[str, Any] | JobFailure:
    from src.services.canvas_reconciliation_service import CanvasReconciliationService

    payload = job.payload if isinstance(getattr(job, "payload", None), dict) else {}
    return await CanvasReconciliationService().handle_job(
        db,
        payload=payload,
        department_id=job.department_id,
        token_manager=token_manager,
        assert_owned=getattr(job, "_assert_owned", None),
    )


__all__ = ["handle_reconciliation_job"]
