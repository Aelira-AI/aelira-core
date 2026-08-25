"""Standalone durable queue worker entry point: ``python -m src.jobs.worker``."""

from __future__ import annotations

import asyncio
import logging
import signal

from src.config.settings import get_settings
from src.db.database import SessionLocal
from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner
from src.services.durable_maintenance import DurableMaintenanceRunner
from src.services.canvas_reconciliation_service import CanvasReconciliationService
from src.services.canvas_content_provenance import maintain_canvas_content_evidence
from src.services.remediation_artifact_service import (
    RemediationArtifactCleanup,
    RemediationArtifactService,
)

from .job_processor import JobProcessor
from .registry import build_default_registry

logger = logging.getLogger(__name__)


async def run_maintenance_loop() -> None:
    """Run bounded singleton cleanup and reconciliation support periodically."""
    settings = get_settings()
    artifact_service = RemediationArtifactService.from_settings()
    runner = DurableMaintenanceRunner(
        cleanup=RemediationArtifactCleanup(
            service=artifact_service,
            batch_size=settings.remediation_artifact_cleanup_batch_size,
            staging_grace_seconds=(settings.remediation_artifact_staging_grace_seconds),
        ),
        orphan_scanner=ArtifactOrphanScanner(
            root=settings.remediation_artifact_dir,
            batch_size=settings.remediation_artifact_orphan_batch_size,
            grace_seconds=settings.remediation_artifact_orphan_grace_seconds,
            retention_days=(settings.remediation_artifact_quarantine_retention_days),
            max_visited_entries=(
                settings.remediation_artifact_orphan_max_visited_entries
            ),
            max_visited_directories=(
                settings.remediation_artifact_orphan_max_visited_directories
            ),
            max_directory_entries=(
                settings.remediation_artifact_orphan_max_directory_entries
            ),
            max_seconds=settings.remediation_artifact_orphan_max_seconds,
        ),
        reconciliation=CanvasReconciliationService(
            batch_size=settings.remediation_artifact_orphan_batch_size
        ),
    )
    while True:
        try:
            with SessionLocal() as db:
                await asyncio.to_thread(runner.run_once, db)
            with SessionLocal() as db:
                await asyncio.to_thread(maintain_canvas_content_evidence, db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Durable maintenance iteration failed")
        await asyncio.sleep(settings.durable_maintenance_interval_seconds)


async def run_worker() -> None:
    processor = JobProcessor(registry=build_default_registry())
    maintenance_task = asyncio.create_task(run_maintenance_loop())
    loop = asyncio.get_running_loop()

    def drain() -> None:
        logger.info("Worker drain requested", extra={"worker_id": processor.worker_id})
        processor.request_drain()

    for signame in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, signame, None)
        if signum is not None:
            try:
                loop.add_signal_handler(signum, drain)
            except NotImplementedError:
                signal.signal(signum, lambda *_: drain())
    try:
        await processor.start()
    finally:
        maintenance_task.cancel()
        await asyncio.gather(maintenance_task, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
