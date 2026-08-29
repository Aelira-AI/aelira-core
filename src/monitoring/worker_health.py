"""Prometheus collector for privacy-bounded durable-worker health."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from prometheus_client import REGISTRY
from prometheus_client.core import GaugeMetricFamily, Metric

from src.db.database import SessionLocal
from src.jobs.operational_health import (
    WorkerHealthSnapshot,
    collect_worker_health_snapshot,
)


def _database_snapshot() -> WorkerHealthSnapshot:
    with SessionLocal() as db:
        return collect_worker_health_snapshot(db)


class WorkerHealthCollector:
    """Collect aggregate worker state without identity-bearing labels."""

    def __init__(
        self, snapshot_provider: Callable[[], WorkerHealthSnapshot] = _database_snapshot
    ) -> None:
        self._snapshot_provider = snapshot_provider

    def describe(self) -> Iterable[Metric]:
        """Describe every fixed series without touching the database."""
        for name in (
            "aelira_worker_health_collection_success",
            "aelira_worker_live_workers",
            "aelira_worker_draining_workers",
            "aelira_worker_runnable_pending_jobs",
            "aelira_worker_processing_jobs",
            "aelira_worker_expired_leases",
            "aelira_worker_stalled_jobs",
            "aelira_worker_jobs_claimed",
            "aelira_worker_jobs_completed",
            "aelira_worker_jobs_failed",
            "aelira_worker_latest_heartbeat_age_seconds",
            "aelira_worker_latest_progress_age_seconds",
            "aelira_worker_oldest_running_job_age_seconds",
            "aelira_worker_oldest_pending_job_age_seconds",
        ):
            yield GaugeMetricFamily(name, name.replace("_", " "))
        yield GaugeMetricFamily(
            "aelira_worker_health_state",
            "Current bounded durable-worker health state",
            labels=["state"],
        )

    @staticmethod
    def _gauge(name: str, documentation: str, value: float) -> GaugeMetricFamily:
        return GaugeMetricFamily(name, documentation, value=value)

    def collect(self) -> Iterable[Metric]:
        try:
            snapshot = self._snapshot_provider()
        except Exception:
            yield self._gauge(
                "aelira_worker_health_collection_success",
                "Whether the worker health snapshot was collected successfully",
                0.0,
            )
            return

        yield self._gauge(
            "aelira_worker_health_collection_success",
            "Whether the worker health snapshot was collected successfully",
            1.0,
        )
        yield self._gauge(
            "aelira_worker_live_workers",
            "Workers with a fresh running or draining heartbeat",
            snapshot.live_workers,
        )
        yield self._gauge(
            "aelira_worker_draining_workers",
            "Workers with a fresh draining heartbeat",
            snapshot.draining_workers,
        )
        yield self._gauge(
            "aelira_worker_runnable_pending_jobs",
            "Runnable pending jobs supported by this worker release",
            snapshot.runnable_pending,
        )
        yield self._gauge(
            "aelira_worker_processing_jobs",
            "Jobs currently in processing state",
            snapshot.queue["processing"],
        )
        yield self._gauge(
            "aelira_worker_expired_leases",
            "Processing jobs with an absent or expired lease",
            snapshot.expired_processing,
        )
        yield self._gauge(
            "aelira_worker_stalled_jobs",
            "Processing jobs beyond the configured execution bound",
            snapshot.stalled_processing,
        )
        yield self._gauge(
            "aelira_worker_jobs_claimed",
            "Jobs claimed by recorded worker sessions",
            snapshot.jobs_claimed,
        )
        yield self._gauge(
            "aelira_worker_jobs_completed",
            "Jobs completed by recorded worker sessions",
            snapshot.jobs_completed,
        )
        yield self._gauge(
            "aelira_worker_jobs_failed",
            "Jobs failed by recorded worker sessions",
            snapshot.jobs_failed,
        )
        for name, documentation, value in (
            (
                "aelira_worker_latest_heartbeat_age_seconds",
                "Age of the latest recorded worker heartbeat",
                snapshot.latest_heartbeat_age_seconds,
            ),
            (
                "aelira_worker_latest_progress_age_seconds",
                "Age of the latest worker progress watermark",
                snapshot.latest_progress_age_seconds,
            ),
            (
                "aelira_worker_oldest_running_job_age_seconds",
                "Age of the oldest currently processing job",
                snapshot.oldest_running_job_age_seconds,
            ),
            (
                "aelira_worker_oldest_pending_job_age_seconds",
                "Age of the oldest pending job",
                snapshot.oldest_pending_age_seconds,
            ),
        ):
            if value is not None:
                yield self._gauge(name, documentation, value)
        state = GaugeMetricFamily(
            "aelira_worker_health_state",
            "Current bounded durable-worker health state",
            labels=["state"],
        )
        state.add_metric([snapshot.health_state], 1.0)
        yield state


_registered = False


def register_worker_health_collector() -> None:
    """Register the database-backed collector once per API process."""
    global _registered
    if _registered:
        return
    REGISTRY.register(WorkerHealthCollector())
    _registered = True
