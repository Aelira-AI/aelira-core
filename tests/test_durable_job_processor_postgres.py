"""Real PostgreSQL concurrency/fencing tests for the durable queue."""

from __future__ import annotations

import os
import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.orm import sessionmaker

from src.db.models import (
    CloudJobQueue,
    CloudJobStatus,
    Department,
    WorkerHeartbeat,
)
from src.jobs.contracts import JobFailure, JobSuccess
from src.jobs.job_processor import JobProcessor
from src.jobs.registry import (
    EXECUTABLE_JOB_TYPES,
    JobRegistry,
    adapt_legacy_handler,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_sessions():
    url = os.environ["DATABASE_URL"]
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception:
        pytest.skip("PostgreSQL unavailable")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    department_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            Department(
                id=department_id,
                name="Task17 queue test",
                institution="Test",
                contact_email=f"{department_id}@example.test",
            )
        )
        db.commit()
    yield factory, department_id
    with factory() as db:
        db.execute(
            delete(WorkerHeartbeat).where(WorkerHeartbeat.worker_id.like("task17-%"))
        )
        db.execute(delete(Department).where(Department.id == department_id))
        db.commit()
    engine.dispose()


def registry_with(handler) -> JobRegistry:
    registry = JobRegistry()
    registry.register("scan", handler)
    return registry


def enqueue(factory, department_id, **values) -> str:
    job_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            CloudJobQueue(
                id=job_id,
                department_id=department_id,
                job_type="scan",
                payload=values.pop("payload", {}),
                status=values.pop("status", "pending"),
                **values,
            )
        )
        db.commit()
    return job_id


def processor(factory, worker_id, registry, **kwargs) -> JobProcessor:
    kwargs.setdefault("poll_interval", 0.01)
    kwargs.setdefault("heartbeat_interval", 0.01)
    value = JobProcessor(
        worker_id=worker_id,
        session_factory=factory,
        registry=registry,
        **kwargs,
    )
    value._token_manager = MagicMock()
    return value


def test_two_workers_cannot_double_claim_and_batch_is_committed_first(pg_sessions):
    factory, department_id = pg_sessions
    registry = registry_with(AsyncMock(return_value=JobSuccess({"ok": True})))
    ids = {enqueue(factory, department_id), enqueue(factory, department_id)}
    first = processor(factory, "task17-worker-a", registry, batch_size=2)
    second = processor(factory, "task17-worker-b", registry, batch_size=2)

    claims = first.claim_batch()
    assert {claim.job_id for claim in claims} == ids
    assert second.claim_batch() == []
    with factory() as db:
        rows = db.query(CloudJobQueue).filter(CloudJobQueue.id.in_(ids)).all()
        assert all(row.status == "processing" for row in rows)
        assert all(row.claim_token and row.worker_id == first.worker_id for row in rows)


@pytest.mark.asyncio
async def test_false_success_never_completes_and_deterministic_failure_is_terminal(
    pg_sessions,
):
    factory, department_id = pg_sessions
    legacy = AsyncMock(return_value={"success": False, "error_code": "invalid_scope"})
    registry = registry_with(adapt_legacy_handler(legacy))
    job_id = enqueue(factory, department_id)
    worker = processor(factory, "task17-worker-false", registry)

    [claim] = worker.claim_batch()
    assert await worker.process_claim(claim) is True
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "failed"
        assert job.last_error_code == "invalid_scope"
        assert job.claim_token is None
        assert job.result_data is None


@pytest.mark.asyncio
async def test_expired_lease_fences_old_worker_and_new_worker_completes(pg_sessions):
    factory, department_id = pg_sessions
    old_handler = AsyncMock(return_value=JobSuccess({"owner": "old"}))
    new_handler = AsyncMock(return_value=JobSuccess({"owner": "new"}))
    job_id = enqueue(factory, department_id)
    old = processor(
        factory, "task17-worker-old", registry_with(old_handler), max_retries=3
    )
    new = processor(
        factory, "task17-worker-new", registry_with(new_handler), max_retries=3
    )
    [old_claim] = old.claim_batch()
    with factory() as db:
        db.execute(
            update(CloudJobQueue)
            .where(CloudJobQueue.id == job_id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        db.commit()

    assert new.reap_stale_jobs() == 1
    [new_claim] = new.claim_batch()
    assert await old.process_claim(old_claim) is False
    old_handler.assert_not_awaited()
    assert await new.process_claim(new_claim) is True
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == CloudJobStatus.COMPLETED.value
        assert job.result_data == {"owner": "new"}
        assert job.attempt_count == 2


@pytest.mark.asyncio
async def test_reaped_worker_checkpoint_prevents_domain_commit_after_blocking_work(
    pg_sessions,
):
    factory, department_id = pg_sessions
    blocking_started = asyncio.Event()

    async def old_handler(context, db, _token_manager):
        blocking_started.set()
        await asyncio.to_thread(time.sleep, 0.15)
        await context.assert_owned()
        department = db.get(Department, department_id)
        department.name = "stale worker side effect"
        db.commit()
        return JobSuccess({"owner": "old"})

    job_id = enqueue(factory, department_id)
    old = processor(
        factory,
        "task17-worker-stale-domain",
        registry_with(old_handler),
        lease_seconds=1,
        heartbeat_interval=1,
    )
    reaper = processor(
        factory,
        "task17-worker-stale-reaper",
        registry_with(AsyncMock(return_value=JobSuccess())),
    )
    [claim] = old.claim_batch()
    old_task = asyncio.create_task(old.process_claim(claim))
    await asyncio.wait_for(blocking_started.wait(), timeout=1)
    with factory() as db:
        db.execute(
            update(CloudJobQueue)
            .where(CloudJobQueue.id == job_id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        db.commit()
    assert reaper.reap_stale_jobs() == 1

    assert await asyncio.wait_for(old_task, timeout=2) is False
    with factory() as db:
        assert db.get(Department, department_id).name == "Task17 queue test"
        job = db.get(CloudJobQueue, job_id)
        assert job.status == CloudJobStatus.PENDING.value
        assert job.result_data is None


@pytest.mark.asyncio
async def test_to_thread_blocking_work_keeps_lease_heartbeat_alive(pg_sessions):
    factory, department_id = pg_sessions
    blocking_started = asyncio.Event()

    async def blocking_handler(_context, _db, _token_manager):
        blocking_started.set()
        await asyncio.to_thread(time.sleep, 0.25)
        return JobSuccess({"heartbeat": "alive"})

    job_id = enqueue(factory, department_id)
    worker = processor(
        factory,
        "task17-worker-heartbeat-through-blocking",
        registry_with(blocking_handler),
        lease_seconds=0.12,
        heartbeat_interval=0.02,
    )
    reaper = processor(
        factory,
        "task17-worker-heartbeat-reaper",
        registry_with(AsyncMock(return_value=JobSuccess())),
    )
    [claim] = worker.claim_batch()
    task = asyncio.create_task(worker.process_claim(claim))
    await asyncio.wait_for(blocking_started.wait(), timeout=1)
    await asyncio.sleep(0.16)

    assert reaper.reap_stale_jobs() == 0
    assert await asyncio.wait_for(task, timeout=1) is True
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == CloudJobStatus.COMPLETED.value
        assert job.result_data == {"heartbeat": "alive"}


def test_dependency_must_complete_before_claim(pg_sessions):
    factory, department_id = pg_sessions
    registry = registry_with(AsyncMock(return_value=JobSuccess()))
    parent_id = enqueue(factory, department_id)
    child_id = enqueue(factory, department_id, depends_on_job_id=parent_id)
    worker = processor(factory, "task17-worker-dependency", registry)

    first = worker.claim_batch()
    assert [claim.job_id for claim in first] == [parent_id]
    worker._finish(first[0], JobSuccess())
    second = worker.claim_batch()
    assert [claim.job_id for claim in second] == [child_id]


def test_dependency_cycle_is_bounded_and_marked_dependency_failed(pg_sessions):
    factory, department_id = pg_sessions
    registry = registry_with(AsyncMock(return_value=JobSuccess()))
    first_id = enqueue(factory, department_id)
    second_id = enqueue(factory, department_id, depends_on_job_id=first_id)
    with factory() as db:
        db.execute(
            update(CloudJobQueue)
            .where(CloudJobQueue.id == first_id)
            .values(depends_on_job_id=second_id)
        )
        db.commit()
    worker = processor(factory, "task17-worker-dependency-cycle", registry)

    assert worker.claim_batch() == []
    with factory() as db:
        for job_id in (first_id, second_id):
            job = db.get(CloudJobQueue, job_id)
            assert job.status == CloudJobStatus.FAILED.value
            assert job.last_error_code == "dependency_failed"


def test_retry_exhaustion_and_worker_heartbeat_state(pg_sessions):
    factory, department_id = pg_sessions
    registry = registry_with(AsyncMock(return_value=JobFailure.retryable("temporary")))
    job_id = enqueue(factory, department_id, max_retries=1)
    worker = processor(factory, "task17-worker-retry", registry, max_retries=1)
    worker._set_worker_state("running")
    [claim] = worker.claim_batch()
    assert worker._finish(claim, JobFailure.retryable("temporary")) is True

    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        heartbeat = db.get(WorkerHeartbeat, worker.worker_id)
        assert job.status == "failed"
        assert job.completed_at is not None
        assert heartbeat.status == "running"
        assert heartbeat.jobs_claimed == 1


@pytest.mark.asyncio
async def test_worker_cleanly_transitions_running_draining_stopped(pg_sessions):
    factory, _ = pg_sessions
    registry = JobRegistry()
    for job_type in EXECUTABLE_JOB_TYPES:
        registry.register(job_type, AsyncMock(return_value=JobSuccess()))
    worker = processor(factory, "task17-worker-lifecycle", registry)

    task = asyncio.create_task(worker.start())
    for _ in range(50):
        with factory() as db:
            heartbeat = db.get(WorkerHeartbeat, worker.worker_id)
            if heartbeat is not None and heartbeat.status == "running":
                break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("worker did not publish running heartbeat")

    worker.request_drain()
    await asyncio.wait_for(task, timeout=2)
    with factory() as db:
        heartbeat = db.get(WorkerHeartbeat, worker.worker_id)
        assert heartbeat.status == "stopped"
        assert heartbeat.stopped_at is not None


def test_malformed_legacy_payload_is_quarantined_while_valid_job_is_claimed(
    pg_sessions,
):
    factory, department_id = pg_sessions
    registry = registry_with(AsyncMock(return_value=JobSuccess()))
    malformed_id = enqueue(factory, department_id)
    valid_id = enqueue(factory, department_id, payload={"scan_id": "valid"})
    worker = processor(factory, "task17-worker-payload", registry, batch_size=2)

    with factory() as db:
        db.execute(
            text(
                "ALTER TABLE cloud_job_queue DROP CONSTRAINT ck_cloud_job_queue_payload_object"
            )
        )
        db.execute(
            text("UPDATE cloud_job_queue SET payload = '[]'::jsonb WHERE id = :job_id"),
            {"job_id": malformed_id},
        )
        db.commit()
    try:
        claims = worker.claim_batch()
        assert [claim.job_id for claim in claims] == [valid_id]
        with factory() as db:
            malformed = db.get(CloudJobQueue, malformed_id)
            assert malformed.status == "failed"
            assert malformed.last_error_code == "invalid_job_payload"
            assert malformed.completed_at is not None
            assert malformed.claim_token is None
    finally:
        with factory() as db:
            db.execute(
                text(
                    "ALTER TABLE cloud_job_queue ADD CONSTRAINT "
                    "ck_cloud_job_queue_payload_object "
                    "CHECK (jsonb_typeof(payload) = 'object') NOT VALID"
                )
            )
            db.commit()


def test_failed_dependencies_propagate_one_generation_per_tick(pg_sessions):
    factory, department_id = pg_sessions
    registry = registry_with(AsyncMock(return_value=JobSuccess()))
    parent_id = enqueue(
        factory,
        department_id,
        status="failed",
        completed_at=datetime.now(timezone.utc),
    )
    child_id = enqueue(factory, department_id, depends_on_job_id=parent_id)
    grandchild_id = enqueue(factory, department_id, depends_on_job_id=child_id)
    worker = processor(factory, "task17-worker-failed-dependency", registry)

    assert worker.claim_batch() == []
    with factory() as db:
        assert db.get(CloudJobQueue, child_id).status == "failed"
        assert db.get(CloudJobQueue, grandchild_id).status == "pending"
    assert worker.claim_batch() == []
    with factory() as db:
        grandchild = db.get(CloudJobQueue, grandchild_id)
        assert grandchild.status == "failed"
        assert grandchild.last_error_code == "dependency_failed"
        assert grandchild.completed_at is not None


def test_missing_legacy_dependency_is_quarantined(pg_sessions):
    factory, department_id = pg_sessions
    registry = registry_with(AsyncMock(return_value=JobSuccess()))
    worker = processor(factory, "task17-worker-missing-dependency", registry)
    with factory() as db:
        db.execute(
            text(
                "ALTER TABLE cloud_job_queue DROP CONSTRAINT fk_cloud_job_queue_dependency"
            )
        )
        db.commit()
    try:
        child_id = enqueue(
            factory,
            department_id,
            depends_on_job_id=str(uuid.uuid4()),
        )
        assert worker.claim_batch() == []
        with factory() as db:
            child = db.get(CloudJobQueue, child_id)
            assert child.status == "failed"
            assert child.last_error_code == "dependency_failed"
            assert child.completed_at is not None
    finally:
        with factory() as db:
            db.execute(
                text(
                    "ALTER TABLE cloud_job_queue ADD CONSTRAINT "
                    "fk_cloud_job_queue_dependency FOREIGN KEY (depends_on_job_id) "
                    "REFERENCES cloud_job_queue(id) ON DELETE RESTRICT NOT VALID"
                )
            )
            db.commit()


@pytest.mark.asyncio
async def test_two_running_workers_respect_independent_concurrency_caps(pg_sessions):
    factory, department_id = pg_sessions
    active = {"task17-bounded-a": 0, "task17-bounded-b": 0}
    peaks = dict(active)

    async def handler(context, _db, _token_manager):
        worker_id = context.worker_id
        active[worker_id] += 1
        peaks[worker_id] = max(peaks[worker_id], active[worker_id])
        try:
            await asyncio.sleep(0.02)
            return JobSuccess()
        finally:
            active[worker_id] -= 1

    registry_a = JobRegistry()
    registry_b = JobRegistry()
    for job_type in EXECUTABLE_JOB_TYPES:
        registry_a.register(job_type, handler)
        registry_b.register(job_type, handler)
    job_ids = {enqueue(factory, department_id) for _ in range(20)}
    first = processor(
        factory,
        "task17-bounded-a",
        registry_a,
        batch_size=20,
        max_concurrency=2,
    )
    second = processor(
        factory,
        "task17-bounded-b",
        registry_b,
        batch_size=20,
        max_concurrency=2,
    )

    tasks = [asyncio.create_task(first.start()), asyncio.create_task(second.start())]
    try:
        for _ in range(300):
            with factory() as db:
                completed = (
                    db.query(CloudJobQueue)
                    .filter(
                        CloudJobQueue.id.in_(job_ids),
                        CloudJobQueue.status == CloudJobStatus.COMPLETED.value,
                    )
                    .count()
                )
            if completed == len(job_ids):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("two bounded workers did not drain the queue")
    finally:
        first.request_drain()
        second.request_drain()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)

    assert 0 < peaks[first.worker_id] <= 2
    assert 0 < peaks[second.worker_id] <= 2
