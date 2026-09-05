"""Sentry initialization and bounded durable-worker failure reporting."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_EXPECTED_TERMINAL_CODES = frozenset({"scan_cancelled"})


def init_worker_sentry(settings: Any) -> bool:
    """Initialize Sentry for the standalone worker when a DSN is configured."""
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", settings.env),
        release=(
            os.getenv("SENTRY_RELEASE") or f"aelira-backend@{settings.api_version}"
        ),
        integrations=[SqlalchemyIntegration()],
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service", "worker")
    logger.info(
        "Sentry initialized for durable worker",
        extra={"environment": os.getenv("SENTRY_ENVIRONMENT", settings.env)},
    )
    return True


def capture_terminal_job_failure(
    *,
    job_id: str,
    job_type: str,
    error_code: str,
    failure_kind: str,
    attempt_count: int,
    max_retries: int,
) -> str | None:
    """Report one persisted terminal failure without payload or tenant data."""
    if not os.getenv("SENTRY_DSN") or error_code in _EXPECTED_TERMINAL_CODES:
        return None

    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            scope.set_tag("job_type", job_type[:50])
            scope.set_tag("error_code", error_code[:128])
            scope.set_tag("failure_kind", failure_kind[:32])
            scope.fingerprint = ["durable-job-failure", job_type[:50], error_code[:128]]
            scope.set_context(
                "durable_job",
                {
                    "job_id": job_id[:36],
                    "attempt_count": attempt_count,
                    "max_retries": max_retries,
                },
            )
            return sentry_sdk.capture_message(
                f"Durable job failed: {job_type[:50]}/{error_code[:128]}",
                level="error",
            )
    except Exception:
        logger.exception(
            "Failed to report durable job failure",
            extra={"job_type": job_type[:50], "error_code": error_code[:128]},
        )
        return None
