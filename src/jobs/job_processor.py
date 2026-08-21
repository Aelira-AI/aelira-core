"""Atomic, lease-fenced durable database job processor."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, aliased

from ..db.database import SessionLocal
from ..db.models import CloudJobQueue, CloudJobStatus, WorkerHeartbeat
from ..integrations.oauth_token_manager import OAuthTokenManager
from .contracts import (
    FailureKind,
    JobContext,
    JobFailure,
    JobResult,
    JobSuccess,
    LostJobOwnership,
)
from .registry import JobRegistry, adapt_legacy_handler, build_default_registry

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_claim_query(registered_types: set[str] | frozenset[str], *, limit: int):
    """Build the dependency-gated PostgreSQL claim selection."""
    dependency = aliased(CloudJobQueue)
    return (
        select(CloudJobQueue)
        .outerjoin(dependency, CloudJobQueue.depends_on_job_id == dependency.id)
        .where(
            CloudJobQueue.status == CloudJobStatus.PENDING.value,
            CloudJobQueue.scheduled_for <= utcnow(),
            CloudJobQueue.job_type.in_(sorted(registered_types)),
            or_(
                CloudJobQueue.depends_on_job_id.is_(None),
                dependency.status == CloudJobStatus.COMPLETED.value,
            ),
        )
        .order_by(CloudJobQueue.priority.asc(), CloudJobQueue.created_at.asc())
        .limit(limit)
        .with_for_update(of=CloudJobQueue, skip_locked=True)
    )


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    job_type: str
    payload: dict[str, Any]
    claim_token: str
    worker_id: str
    attempt_count: int
    max_retries: int


class JobProcessor:
    """Multi-worker-safe durable queue processor with lease fencing."""

    def __init__(
        self,
        batch_size: int = 10,
        poll_interval: float = 5.0,
        max_retries: int = 3,
        *,
        max_concurrency: int | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 90,
        heartbeat_interval: float = 15.0,
        reaper_interval: float = 30.0,
        session_factory: Callable[[], Session] = SessionLocal,
        registry: JobRegistry | None = None,
        max_execution_seconds: float | None = None,
    ) -> None:
        self.batch_size = batch_size
        if max_concurrency is None:
            raw_max_concurrency = os.environ.get("JOB_WORKER_MAX_CONCURRENCY", "4")
            try:
                max_concurrency = int(raw_max_concurrency)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "max_concurrency must be an integer from 1 to 64"
                ) from exc
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 64:
            raise ValueError("max_concurrency must be an integer from 1 to 64")
        self.max_concurrency = max_concurrency
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.worker_id = (
            worker_id
            or os.environ.get("JOB_WORKER_ID")
            or (f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}")
        )
        self.lease_seconds = lease_seconds
        self.heartbeat_interval = min(heartbeat_interval, max(1.0, lease_seconds / 3))
        self.reaper_interval = reaper_interval
        if max_execution_seconds is None:
            raw_max_execution = os.environ.get(
                "JOB_WORKER_MAX_EXECUTION_SECONDS", "3600"
            )
            try:
                max_execution_seconds = float(raw_max_execution)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "max_execution_seconds must be from 1 to 86400"
                ) from exc
        if (
            isinstance(max_execution_seconds, bool)
            or not isinstance(max_execution_seconds, (int, float))
            or not 1 <= max_execution_seconds <= 86400
        ):
            raise ValueError("max_execution_seconds must be from 1 to 86400")
        self.max_execution_seconds = float(max_execution_seconds)
        self.session_factory = session_factory
        self.registry = registry or JobRegistry()
        self._legacy_handlers: dict[str, Callable[..., Any]] = {}
        self._token_manager: OAuthTokenManager | None = None
        self._running = False
        self._draining = False
        self._stop_event = asyncio.Event()
        self._inflight: set[asyncio.Task[Any]] = set()
        self._last_reaper = utcnow() - timedelta(seconds=reaper_interval)

    def _get_token_manager(self) -> OAuthTokenManager:
        if self._token_manager is None:
            key = os.environ.get("TOKEN_ENCRYPTION_KEY")
            if not key:
                raise ValueError("TOKEN_ENCRYPTION_KEY not configured")
            self._token_manager = OAuthTokenManager(key)
        return self._token_manager

    def register_handler(self, job_type: str, handler: Callable[..., Any]) -> None:
        self._legacy_handlers[job_type] = handler
        self.registry.register(job_type, adapt_legacy_handler(handler))

    async def _process_job(self, job: Any, db: Session) -> None:
        """Legacy direct-call test seam; standalone workers use claimed jobs only."""
        job.status = CloudJobStatus.PROCESSING.value
        job.started_at = utcnow()
        job.progress = 0
        db.commit()
        try:
            handler = self._legacy_handlers.get(job.job_type)
            if handler is None:
                raise ValueError("unregistered_job_type")
            result = await handler(job, db, self._get_token_manager())
            if getattr(result, "handler_committed", False) is True:
                return
            if not isinstance(result, dict) or result.get("success") is False:
                raise ValueError("malformed_handler_result")
            job.status = CloudJobStatus.COMPLETED.value
            job.completed_at = utcnow()
            job.progress = 100
            job.result_data = result
            db.commit()
        except Exception as exc:
            from .remediation_job import (
                RemediationJobFailed,
                RetryableRemediationJobError,
                transition_retryable_remediation_job,
            )

            if (
                isinstance(exc, RemediationJobFailed)
                and exc.terminal_state_committed is True
            ):
                return
            if isinstance(exc, RetryableRemediationJobError):
                transition_retryable_remediation_job(job, db, exc)
                return
            db.rollback()
            try:
                db.refresh(job)
            except Exception:
                logger.warning("Could not refresh legacy job after rollback")
            deterministic = (
                isinstance(exc, RemediationJobFailed) and exc.__cause__ is None
            )
            code = (
                exc.code
                if isinstance(exc, RemediationJobFailed)
                else "job_processing_failed"
            )
            job.retry_count += 1
            job.error_message = code
            if deterministic or job.retry_count >= job.max_retries:
                job.status = CloudJobStatus.FAILED.value
                job.completed_at = utcnow()
            else:
                job.status = CloudJobStatus.PENDING.value
                job.completed_at = None
                job.progress = 0
            db.commit()

    def _set_worker_state(self, state: str) -> None:
        now = utcnow()
        with self.session_factory() as db:
            heartbeat = db.get(WorkerHeartbeat, self.worker_id)
            if heartbeat is None:
                heartbeat = WorkerHeartbeat(
                    worker_id=self.worker_id,
                    status=state,
                    started_at=now,
                    heartbeat_at=now,
                    metadata_json={"pid": os.getpid(), "host": socket.gethostname()},
                )
                db.add(heartbeat)
            else:
                heartbeat.status = state
                heartbeat.heartbeat_at = now
                heartbeat.stopped_at = now if state == "stopped" else None
            db.commit()

    def _fail_blocked_dependencies(self, db: Session, *, limit: int) -> int:
        """Fail a bounded set of children whose dependency cannot succeed."""
        dependency = aliased(CloudJobQueue)
        now = utcnow()
        blocked = list(
            db.scalars(
                select(CloudJobQueue)
                .outerjoin(dependency, CloudJobQueue.depends_on_job_id == dependency.id)
                .where(
                    CloudJobQueue.status == CloudJobStatus.PENDING.value,
                    CloudJobQueue.depends_on_job_id.is_not(None),
                    or_(
                        dependency.id.is_(None),
                        dependency.status == CloudJobStatus.FAILED.value,
                    ),
                )
                .order_by(CloudJobQueue.created_at.asc())
                .limit(limit)
                .with_for_update(of=CloudJobQueue, skip_locked=True)
            ).all()
        )
        for job in blocked:
            job.status = CloudJobStatus.FAILED.value
            job.completed_at = now
            job.error_message = "dependency_failed"
            job.last_error_code = "dependency_failed"
            job.last_error_retryable = False
            job.progress_message = "Failed"
            job.updated_at = now
        return len(blocked)

    def _fail_dependency_cycles(self, db: Session, *, limit: int) -> int:
        """Detect cycles within one bounded locked dependency window."""
        now = utcnow()
        candidates = list(
            db.scalars(
                select(CloudJobQueue)
                .where(
                    CloudJobQueue.status == CloudJobStatus.PENDING.value,
                    CloudJobQueue.depends_on_job_id.is_not(None),
                )
                .order_by(CloudJobQueue.created_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )
        by_id = {str(job.id): job for job in candidates}
        cycle_ids: set[str] = set()
        for start_id in by_id:
            path: list[str] = []
            positions: dict[str, int] = {}
            current_id: str | None = start_id
            while current_id is not None and current_id in by_id:
                if current_id in positions:
                    cycle_ids.update(path[positions[current_id] :])
                    break
                positions[current_id] = len(path)
                path.append(current_id)
                dependency_id = by_id[current_id].depends_on_job_id
                current_id = str(dependency_id) if dependency_id is not None else None
        for job_id in cycle_ids:
            job = by_id[job_id]
            job.status = CloudJobStatus.FAILED.value
            job.completed_at = now
            job.error_message = "dependency_failed"
            job.last_error_code = "dependency_failed"
            job.last_error_retryable = False
            job.progress_message = "Failed"
            job.updated_at = now
        return len(cycle_ids)

    def claim_batch(self, *, limit: int | None = None) -> list[ClaimedJob]:
        """Claim every selected row in one transaction, then return detached data."""
        registered = self.registry.job_types
        if not registered or self._draining:
            return []
        now = utcnow()
        lease = now + timedelta(seconds=self.lease_seconds)
        claims: list[ClaimedJob] = []
        claim_limit = min(
            self.batch_size, limit if limit is not None else self.batch_size
        )
        if claim_limit <= 0:
            return []
        with self.session_factory() as db:
            self._fail_dependency_cycles(
                db, limit=max(self.batch_size, claim_limit, 100)
            )
            self._fail_blocked_dependencies(db, limit=max(self.batch_size, claim_limit))
            jobs = list(
                db.scalars(build_claim_query(registered, limit=claim_limit)).all()
            )
            for job in jobs:
                if type(job.payload) is not dict:
                    job.status = CloudJobStatus.FAILED.value
                    job.completed_at = now
                    job.error_message = "invalid_job_payload"
                    job.last_error_code = "invalid_job_payload"
                    job.last_error_retryable = False
                    job.progress_message = "Failed"
                    job.updated_at = now
                    continue
                token = str(uuid.uuid4())
                job.status = CloudJobStatus.PROCESSING.value
                job.claim_token = token
                job.worker_id = self.worker_id
                job.claimed_at = now
                job.heartbeat_at = now
                job.lease_expires_at = lease
                job.started_at = job.started_at or now
                job.completed_at = None
                job.progress = 0
                job.progress_message = None
                job.error_message = None
                job.last_error_code = None
                job.last_error_retryable = None
                job.attempt_count = (job.attempt_count or 0) + 1
                job.retry_count = max(0, job.attempt_count - 1)
                claims.append(
                    ClaimedJob(
                        str(job.id),
                        str(job.job_type),
                        dict(job.payload),
                        token,
                        self.worker_id,
                        job.attempt_count,
                        (
                            job.max_retries
                            if job.max_retries is not None
                            else self.max_retries
                        ),
                    )
                )
            if jobs:
                heartbeat = db.get(WorkerHeartbeat, self.worker_id)
                if heartbeat is not None:
                    heartbeat.jobs_claimed = (heartbeat.jobs_claimed or 0) + len(claims)
                    heartbeat.heartbeat_at = now
            db.commit()
        return claims

    @staticmethod
    def _fence(claim: ClaimedJob):
        return and_(
            CloudJobQueue.id == claim.job_id,
            CloudJobQueue.status == CloudJobStatus.PROCESSING.value,
            CloudJobQueue.claim_token == claim.claim_token,
            CloudJobQueue.worker_id == claim.worker_id,
        )

    def _fenced_update(self, claim: ClaimedJob, values: dict[str, Any]) -> bool:
        with self.session_factory() as db:
            result = db.execute(
                update(CloudJobQueue).where(self._fence(claim)).values(**values)
            )
            if result.rowcount != 1:
                db.rollback()
                return False
            db.commit()
            return True

    def _owns_claim(self, claim: ClaimedJob) -> bool:
        with self.session_factory() as db:
            return (
                db.scalar(select(CloudJobQueue.id).where(self._fence(claim)))
                is not None
            )

    async def report_progress(
        self, claim: ClaimedJob, progress: int, message: str | None = None
    ) -> bool:
        bounded = max(0, min(99, int(progress)))
        return await asyncio.to_thread(
            self._fenced_update,
            claim,
            {"progress": bounded, "progress_message": message, "updated_at": utcnow()},
        )

    async def _assert_owned(self, claim: ClaimedJob) -> None:
        if not await asyncio.to_thread(self._owns_claim, claim):
            raise LostJobOwnership("job ownership lost")

    async def _claim_heartbeat(
        self, claim: ClaimedJob, ownership_lost: asyncio.Event
    ) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                now = utcnow()
                if not await asyncio.to_thread(
                    self._fenced_update,
                    claim,
                    {
                        "heartbeat_at": now,
                        "lease_expires_at": now + timedelta(seconds=self.lease_seconds),
                        "updated_at": now,
                    },
                ):
                    ownership_lost.set()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Claim heartbeat failed closed",
                extra={"job_id": claim.job_id, "error_type": type(exc).__name__},
            )
            ownership_lost.set()

    @staticmethod
    def _clear_claim_values() -> dict[str, Any]:
        return {
            "claim_token": None,
            "worker_id": None,
            "claimed_at": None,
            "heartbeat_at": None,
            "lease_expires_at": None,
        }

    def _backoff(self, attempt_count: int) -> timedelta:
        base = min(300.0, 5.0 * (2 ** max(0, attempt_count - 1)))
        return timedelta(seconds=base * random.SystemRandom().uniform(0.8, 1.2))

    def _finish(self, claim: ClaimedJob, result: JobResult) -> bool:
        now = utcnow()
        clear = self._clear_claim_values()
        if isinstance(result, JobSuccess):
            values = {
                **clear,
                "status": CloudJobStatus.COMPLETED.value,
                "completed_at": now,
                "progress": 100,
                "progress_message": "Completed",
                "result_data": result.result,
                "error_message": None,
                "last_error_code": None,
                "last_error_retryable": None,
                "updated_at": now,
            }
        else:
            retryable = result.kind in {
                FailureKind.RETRYABLE,
                FailureKind.INDETERMINATE,
            }
            exhausted = claim.attempt_count >= claim.max_retries
            if retryable and not exhausted:
                values = {
                    **clear,
                    "status": CloudJobStatus.PENDING.value,
                    "scheduled_for": now + self._backoff(claim.attempt_count),
                    "completed_at": None,
                    "progress": 0,
                    "progress_message": "Queued for retry",
                    "result_data": result.details,
                    "error_message": result.code,
                    "last_error_code": result.code,
                    "last_error_retryable": True,
                    "updated_at": now,
                }
            else:
                values = {
                    **clear,
                    "status": CloudJobStatus.FAILED.value,
                    "completed_at": now,
                    "progress_message": "Failed",
                    "result_data": None,
                    "error_message": result.code,
                    "last_error_code": result.code,
                    "last_error_retryable": retryable,
                    "updated_at": now,
                }
        return self._fenced_update(claim, values)

    def _handler_terminal_committed(self, job_id: str) -> bool:
        with self.session_factory() as db:
            job = db.get(CloudJobQueue, job_id)
            return bool(
                job is not None
                and job.status
                in {
                    CloudJobStatus.COMPLETED.value,
                    CloudJobStatus.FAILED.value,
                }
                and job.claim_token is None
                and job.worker_id is None
            )

    def _record_outcome(self, *, completed: bool) -> None:
        with self.session_factory() as db:
            heartbeat = db.get(WorkerHeartbeat, self.worker_id)
            if heartbeat is None:
                return
            field = "jobs_completed" if completed else "jobs_failed"
            setattr(heartbeat, field, (getattr(heartbeat, field) or 0) + 1)
            heartbeat.heartbeat_at = utcnow()
            db.commit()

    async def process_claim(self, claim: ClaimedJob) -> bool:
        if not await asyncio.to_thread(self._owns_claim, claim):
            return False
        ownership_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._claim_heartbeat(claim, ownership_lost))
        result: JobResult
        try:
            handler = self.registry.get(claim.job_type)
            if handler is None:
                result = JobFailure.deterministic("unregistered_job_type")
            else:
                context = JobContext(
                    job_id=claim.job_id,
                    job_type=claim.job_type,
                    payload=claim.payload,
                    claim_token=claim.claim_token,
                    worker_id=claim.worker_id,
                    attempt_count=claim.attempt_count,
                    report_progress=lambda progress, message=None: self.report_progress(
                        claim, progress, message
                    ),
                    assert_owned=lambda: self._assert_owned(claim),
                )
                try:
                    with self.session_factory() as db:
                        handler_task = asyncio.ensure_future(
                            handler(context, db, self._get_token_manager())
                        )
                        lost_task = asyncio.create_task(ownership_lost.wait())
                        try:
                            async with asyncio.timeout(self.max_execution_seconds):
                                done, _ = await asyncio.wait(
                                    {handler_task, lost_task},
                                    return_when=asyncio.FIRST_COMPLETED,
                                )
                        except TimeoutError:
                            handler_task.cancel()
                            await asyncio.gather(handler_task, return_exceptions=True)
                            db.rollback()
                            result = JobFailure.retryable("job_execution_timeout")
                        else:
                            if ownership_lost.is_set():
                                handler_task.cancel()
                                await asyncio.gather(
                                    handler_task, return_exceptions=True
                                )
                                db.rollback()
                                return False
                            result = await handler_task
                            try:
                                await context.assert_owned()
                            except LostJobOwnership:
                                db.rollback()
                                return False
                        finally:
                            lost_task.cancel()
                            await asyncio.gather(lost_task, return_exceptions=True)
                except LostJobOwnership:
                    return False
                except Exception as exc:
                    if getattr(exc, "terminal_state_committed", False) is True:
                        committed = self._handler_terminal_committed(claim.job_id)
                        if committed:
                            self._record_outcome(completed=False)
                        return committed
                    logger.exception(
                        "Job handler raised",
                        extra={
                            "job_id": claim.job_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    result = JobFailure.indeterminate("job_handler_exception")
            if not isinstance(result, JobSuccess | JobFailure):
                result = JobFailure.indeterminate("malformed_handler_result")
            if isinstance(result, JobSuccess) and result.handler_committed:
                committed = self._handler_terminal_committed(claim.job_id)
                if committed:
                    self._record_outcome(completed=True)
                return committed
            finished = self._finish(claim, result)
            if finished:
                self._record_outcome(completed=isinstance(result, JobSuccess))
            return finished
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    def _inflight_done(self, task: asyncio.Task[Any]) -> None:
        self._inflight.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.error(
                "Inflight job task failed",
                extra={"error_type": type(exc).__name__},
            )

    def reap_stale_jobs(self, *, limit: int = 100) -> int:
        now = utcnow()
        recovered = 0
        with self.session_factory() as db:
            jobs = list(
                db.scalars(
                    select(CloudJobQueue)
                    .where(
                        CloudJobQueue.status == CloudJobStatus.PROCESSING.value,
                        CloudJobQueue.lease_expires_at < now,
                    )
                    .order_by(CloudJobQueue.lease_expires_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                ).all()
            )
            for job in jobs:
                exhausted = (job.attempt_count or 0) >= (
                    job.max_retries if job.max_retries is not None else self.max_retries
                )
                job.status = (
                    CloudJobStatus.FAILED.value
                    if exhausted
                    else CloudJobStatus.PENDING.value
                )
                job.completed_at = now if exhausted else None
                job.scheduled_for = now if not exhausted else job.scheduled_for
                job.error_message = "job_lease_expired"
                job.last_error_code = "job_lease_expired"
                job.last_error_retryable = True
                job.progress = 0
                for key, value in self._clear_claim_values().items():
                    setattr(job, key, value)
                recovered += 1
            db.commit()
        return recovered

    async def _heartbeat_worker(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self.heartbeat_interval)
                self._set_worker_state("draining" if self._draining else "running")
        except asyncio.CancelledError:
            raise

    async def start(self) -> None:
        self.registry.validate()
        self._running = True
        self._draining = False
        self._stop_event.clear()
        self._set_worker_state("running")
        worker_heartbeat = asyncio.create_task(self._heartbeat_worker())
        try:
            while self._running:
                now = utcnow()
                if (now - self._last_reaper).total_seconds() >= self.reaper_interval:
                    self.reap_stale_jobs()
                    self._last_reaper = now
                available = self.max_concurrency - len(self._inflight)
                claims = (
                    self.claim_batch(limit=min(self.batch_size, available))
                    if not self._draining and available > 0
                    else []
                )
                for claim in claims:
                    task = asyncio.create_task(self.process_claim(claim))
                    self._inflight.add(task)
                    task.add_done_callback(self._inflight_done)
                if claims:
                    # Start handlers and their claim heartbeats before claiming again.
                    await asyncio.sleep(0)
                if self._draining and not self._inflight:
                    break
                if self._inflight and (available <= 0 or not claims or self._draining):
                    stop_waiter = asyncio.create_task(self._stop_event.wait())
                    try:
                        await asyncio.wait(
                            {*self._inflight, stop_waiter},
                            timeout=self.poll_interval,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    finally:
                        stop_waiter.cancel()
                        await asyncio.gather(stop_waiter, return_exceptions=True)
                elif not claims:
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(), self.poll_interval
                        )
                    except TimeoutError:
                        pass
        finally:
            if self._inflight:
                await asyncio.gather(*tuple(self._inflight), return_exceptions=True)
            self._running = False
            worker_heartbeat.cancel()
            await asyncio.gather(worker_heartbeat, return_exceptions=True)
            self._set_worker_state("stopped")

    def request_drain(self) -> None:
        self._draining = True
        self._stop_event.set()
        if self._running:
            self._set_worker_state("draining")

    async def drain(self) -> None:
        self.request_drain()
        if self._inflight:
            await asyncio.gather(*tuple(self._inflight), return_exceptions=True)
        self._running = False
        self._stop_event.set()

    def stop(self) -> None:
        self.request_drain()

    async def _process_batch(self) -> int:
        claims = self.claim_batch()
        if claims:
            await asyncio.gather(*(self.process_claim(claim) for claim in claims))
        return len(claims)


_job_processor: JobProcessor | None = None


def get_job_processor() -> JobProcessor:
    global _job_processor
    if _job_processor is None:
        _job_processor = JobProcessor(registry=build_default_registry())
    return _job_processor


async def process_pending_jobs(batch_size: int = 10) -> int:
    processor = get_job_processor()
    processor.batch_size = batch_size
    return await processor._process_batch()


async def start_job_processor_background() -> None:
    """Compatibility hook; API startup intentionally does not invoke this."""
    await get_job_processor().start()
