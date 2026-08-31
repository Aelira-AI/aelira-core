"""Real PostgreSQL concurrency/fencing tests for the durable queue."""

from __future__ import annotations

import os
import asyncio
import signal
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, delete, event, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from conftest import require_disposable_postgres_url

from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudOAuthCredentials,
    CloudProvider,
    CloudWebhookSubscription,
    Department,
    RemediationArtifact,
    Scan,
    ScanResult,
    ScanStatus,
    ScanType,
    WorkerHeartbeat,
)
from src.jobs.contracts import JobFailure, JobSuccess, LostJobOwnership
from src.jobs.job_processor import ClaimedJob, JobProcessor
from src.jobs.registry import (
    EXECUTABLE_JOB_TYPES,
    JobRegistry,
    adapt_legacy_handler,
)
from src.jobs.local_scan_subprocess import _run_process

pytestmark = pytest.mark.integration


def test_finish_cannot_overwrite_committed_cancellation(pg_sessions):
    factory, department_id = pg_sessions
    now = datetime.now(timezone.utc)
    job_id = enqueue(
        factory,
        department_id,
        status="processing",
        claim_token="finish-cancel-claim",
        worker_id="finish-cancel-worker",
        claimed_at=now,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(minutes=1),
    )
    claim = ClaimedJob(
        job_id,
        "scan",
        {},
        "finish-cancel-claim",
        "finish-cancel-worker",
        1,
        3,
    )
    worker = JobProcessor(session_factory=factory, registry=registry_with(AsyncMock()))
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        job.last_error_code = "scan_cancel_requested"
        job.error_message = "scan_cancel_requested"
        db.commit()

    assert worker._finish(claim, JobSuccess({"success": True})) is True
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "failed"
        assert job.last_error_code == "scan_cancelled"
        assert job.result_data == {"cancelled": True}


def test_external_effect_begin_cannot_cross_committed_cancellation(pg_sessions):
    factory, department_id = pg_sessions
    now = datetime.now(timezone.utc)
    job_id = enqueue(
        factory,
        department_id,
        job_type="upload",
        status="processing",
        claim_token="upload-cancel-claim",
        worker_id="upload-cancel-worker",
        claimed_at=now,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(minutes=1),
    )
    claim = ClaimedJob(
        job_id,
        "upload",
        {},
        "upload-cancel-claim",
        "upload-cancel-worker",
        1,
        3,
    )
    worker = JobProcessor(
        session_factory=factory, registry=upload_registry(AsyncMock())
    )
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        job.last_error_code = "scan_cancel_requested"
        job.error_message = "scan_cancel_requested"
        db.commit()

    with pytest.raises(LostJobOwnership, match="fence unavailable"):
        worker._begin_external_effect_sync(claim)
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.external_effect_state is None
        assert job.external_effect_token is None
        assert job.last_error_code == "scan_cancel_requested"


def test_local_failure_terminal_cas_loses_to_committed_cancellation(pg_sessions):
    factory, department_id = pg_sessions
    scan_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="invalid-target",
                scan_type=ScanType.WEBSITE,
                status=ScanStatus.PROCESSING,
            )
        )
        db.commit()
    payload = {"scan_id": scan_id, "scan_kind": "local_web"}
    job_id = enqueue(
        factory,
        department_id,
        payload=payload,
        status="processing",
        claim_token="example-local-failure-cancel-claim",
        worker_id="local-failure-cancel-worker",
        claimed_at=now,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(minutes=1),
    )
    claim = ClaimedJob(
        job_id,
        "scan",
        payload,
        "example-local-failure-cancel-claim",
        "local-failure-cancel-worker",
        1,
        1,
    )
    failure = JobFailure.deterministic("local_scan_url_invalid")
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        job.last_error_code = "scan_cancel_requested"
        job.error_message = "scan_cancel_requested"
        db.commit()

    worker = JobProcessor(session_factory=factory, registry=registry_with(AsyncMock()))
    assert worker._finish(claim, failure) is True
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "failed"
        assert job.last_error_code == "scan_cancelled"
        assert db.get(Scan, scan_id) is None


@pytest.mark.asyncio
async def test_handler_finish_cannot_mutate_foreign_tenant_scan(pg_sessions):
    factory, department_id = pg_sessions
    foreign = seed_foreign_scan_contract(factory)
    try:
        job_id = enqueue(
            factory,
            department_id,
            payload={"scan_id": foreign["scan_id"]},
            max_retries=1,
        )

        async def fail_handler(_context, _db, _tokens):
            return JobFailure.deterministic("malformed_foreign_scan_reference")

        worker = processor(
            factory, "foreign-finish-worker", registry_with(fail_handler)
        )
        [claim] = worker.claim_batch()
        assert await worker.process_claim(claim) is True

        with factory() as db:
            queue = db.get(CloudJobQueue, job_id)
            assert queue.status == CloudJobStatus.FAILED.value
            assert queue.last_error_code == "malformed_foreign_scan_reference"
            assert_foreign_scan_contract(db, foreign)
    finally:
        cleanup_foreign_scan_contract(factory, foreign)


def test_expired_reaper_cannot_mutate_foreign_tenant_scan(pg_sessions):
    factory, department_id = pg_sessions
    foreign = seed_foreign_scan_contract(factory)
    try:
        job_id = enqueue(
            factory,
            department_id,
            payload={
                "scan_id": foreign["scan_id"],
                "scan_kind": "local_pdf",
            },
            status="processing",
            claim_token="foreign-reaper-claim",
            worker_id="foreign-reaper-old-worker",
            claimed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
            heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=2),
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            attempt_count=1,
            max_retries=1,
        )
        reaper = processor(
            factory, "foreign-reaper-new-worker", registry_with(AsyncMock())
        )

        assert reaper.reap_stale_jobs() == 1
        with factory() as db:
            queue = db.get(CloudJobQueue, job_id)
            assert queue.status == CloudJobStatus.FAILED.value
            assert queue.last_error_code == "job_lease_expired"
            assert_foreign_scan_contract(db, foreign)
    finally:
        cleanup_foreign_scan_contract(factory, foreign)


def test_cancellation_ack_cannot_delete_foreign_tenant_scan(pg_sessions):
    factory, department_id = pg_sessions
    foreign = seed_foreign_scan_contract(factory)
    try:
        now = datetime.now(timezone.utc)
        payload = {"scan_id": foreign["scan_id"], "scan_kind": "local_pdf"}
        job_id = enqueue(
            factory,
            department_id,
            payload=payload,
            status="processing",
            claim_token="foreign-cancel-claim",
            worker_id="foreign-cancel-worker",
            claimed_at=now,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(minutes=1),
            last_error_code="scan_cancel_requested",
            error_message="scan_cancel_requested",
        )
        claim = ClaimedJob(
            job_id,
            "scan",
            payload,
            "foreign-cancel-claim",
            "foreign-cancel-worker",
            1,
            1,
        )
        worker = JobProcessor(
            session_factory=factory, registry=registry_with(AsyncMock())
        )

        assert worker._acknowledge_cancellation(claim) is True
        with factory() as db:
            queue = db.get(CloudJobQueue, job_id)
            assert queue.status == CloudJobStatus.FAILED.value
            assert queue.last_error_code == "scan_cancelled"
            assert_foreign_scan_contract(db, foreign)
    finally:
        cleanup_foreign_scan_contract(factory, foreign)


def test_child_authority_is_connection_pinned_and_recovery_is_transaction_scoped(
    pg_sessions,
):
    from src.jobs.execution_authority import (
        acquire_child_execution_lock,
        try_acquire_recovery_lock,
    )

    factory, _department_id = pg_sessions
    job_id = str(uuid.uuid4())
    token = "pool-contention-claim"
    with factory() as child_db:
        authority = acquire_child_execution_lock(
            child_db, job_id=job_id, claim_token=token
        )
        child_db.commit()
        with factory() as recovery_db:
            assert not try_acquire_recovery_lock(
                recovery_db, job_id=job_id, claim_token=token
            )
            recovery_db.rollback()
        authority.close()

    with factory() as recovery_db:
        assert try_acquire_recovery_lock(recovery_db, job_id=job_id, claim_token=token)
        recovery_db.rollback()
    with factory() as next_recovery_db:
        assert try_acquire_recovery_lock(
            next_recovery_db, job_id=job_id, claim_token=token
        )
        next_recovery_db.commit()


def test_recovery_authority_releases_after_failed_transaction_rollback(pg_sessions):
    from src.jobs.execution_authority import try_acquire_recovery_lock

    factory, _department_id = pg_sessions
    job_id = str(uuid.uuid4())
    token = "rollback-recovery-claim"
    with factory() as recovery_db:
        recovery_db.execute(
            text("CREATE TEMP TABLE authority_parent (id integer PRIMARY KEY)")
        )
        recovery_db.execute(
            text(
                "CREATE TEMP TABLE authority_child ("
                "parent_id integer REFERENCES authority_parent(id) "
                "DEFERRABLE INITIALLY DEFERRED)"
            )
        )
        assert try_acquire_recovery_lock(recovery_db, job_id=job_id, claim_token=token)
        recovery_db.execute(text("INSERT INTO authority_child(parent_id) VALUES (404)"))
        with pytest.raises(IntegrityError):
            recovery_db.commit()
        recovery_db.rollback()
    with factory() as next_recovery_db:
        assert try_acquire_recovery_lock(
            next_recovery_db, job_id=job_id, claim_token=token
        )
        next_recovery_db.rollback()


def test_unlock_failure_invalidates_connection_and_does_not_leak_authority(
    pg_sessions,
):
    from src.jobs.execution_authority import (
        acquire_child_execution_lock,
        try_acquire_recovery_lock,
    )

    factory, _department_id = pg_sessions
    job_id = str(uuid.uuid4())
    token = "unlock-failure-claim"
    with factory() as child_db:
        authority = acquire_child_execution_lock(
            child_db, job_id=job_id, claim_token=token
        )
        assert authority.connection is not None

        def fail_unlock_only(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ):
            if "pg_advisory_unlock" in statement:
                raise RuntimeError("injected unlock transport failure")

        event.listen(authority.connection, "before_cursor_execute", fail_unlock_only)
        with pytest.raises(RuntimeError, match="injected unlock transport failure"):
            authority.close()
    with factory() as recovery_db:
        assert try_acquire_recovery_lock(recovery_db, job_id=job_id, claim_token=token)
        recovery_db.rollback()


def test_scan_timeout_monitors_skip_locked_and_fail_once(pg_sessions, monkeypatch):
    from src.jobs import scan_timeout

    factory, department_id = pg_sessions
    scan_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="old.pdf",
                scan_type=ScanType.PDF,
                status=ScanStatus.PROCESSING,
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
        )
        db.commit()
    monkeypatch.setattr(scan_timeout, "get_db", factory)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: scan_timeout.fail_stale_scans(), range(2)))

    assert sorted(results) == [0, 1]
    with factory() as db:
        assert db.get(Scan, scan_id).status == ScanStatus.FAILED


def test_scan_timeout_and_new_owner_serialize_on_scan_row(pg_sessions):
    from src.jobs.local_scan_job import LocalScanJobError, enqueue_local_scan_job
    from src.jobs.scan_timeout import _has_active_queue_owner, build_stale_scan_query

    factory, department_id = pg_sessions
    scan_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="old-site",
                scan_type=ScanType.WEBSITE,
                status=ScanStatus.PROCESSING,
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
        )
        db.commit()

    monitor_locked = Event()
    allow_monitor_commit = Event()

    def monitor() -> None:
        with factory() as db:
            stale = db.scalar(
                build_stale_scan_query(datetime.now(timezone.utc), limit=1)
            )
            assert stale is not None
            monitor_locked.set()
            assert allow_monitor_commit.wait(timeout=3)
            assert not _has_active_queue_owner(db, scan_id)
            stale.status = ScanStatus.FAILED
            stale.completed_at = datetime.now(timezone.utc)
            db.commit()

    def enqueue_after_monitor_lock() -> str:
        assert monitor_locked.wait(timeout=3)
        with factory() as db:
            stale_snapshot = db.get(Scan, scan_id)
            try:
                enqueue_local_scan_job(
                    db,
                    scan=stale_snapshot,
                    scan_kind="local_web",
                    options={
                        "url": "https://example.edu",
                        "mode": "quick",
                        "scan_images": False,
                        "scan_multimedia": False,
                        "scan_math": False,
                        "validate_alt_text": False,
                        "max_depth": 1,
                        "max_pages": 10,
                        "generate_code_fixes": True,
                        "capture_screenshots": True,
                    },
                )
            except LocalScanJobError as exc:
                return exc.code
        return "unexpected_enqueue"

    with ThreadPoolExecutor(max_workers=2) as pool:
        monitor_future = pool.submit(monitor)
        enqueue_future = pool.submit(enqueue_after_monitor_lock)
        assert monitor_locked.wait(timeout=3)
        time.sleep(0.1)
        assert not enqueue_future.done()
        allow_monitor_commit.set()
        monitor_future.result(timeout=3)
        assert enqueue_future.result(timeout=3) == "local_scan_scope_invalid"

    with factory() as db:
        assert db.get(Scan, scan_id).status == ScanStatus.FAILED
        assert (
            db.query(CloudJobQueue)
            .filter(CloudJobQueue.payload["scan_id"].as_string() == scan_id)
            .count()
            == 0
        )


def test_scaled_idle_worker_is_healthy_when_pending_job_dependency_is_owned(
    pg_sessions, monkeypatch
):
    from sqlalchemy import func, select

    from src.jobs import healthcheck
    from src.jobs.job_processor import build_runnable_pending_query

    factory, department_id = pg_sessions
    worker_id = f"scaled-idle-{uuid.uuid4()}"
    factory._test_worker_ids.add(worker_id)
    now = datetime.now(timezone.utc)
    prerequisite_id = enqueue(
        factory,
        department_id,
        status="processing",
        claim_token="other-replica-claim",
        worker_id="other-replica",
        claimed_at=now,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(minutes=1),
    )
    enqueue(
        factory,
        department_id,
        depends_on_job_id=prerequisite_id,
        scheduled_for=now - timedelta(seconds=1),
    )
    with factory() as db:
        db.add(
            WorkerHeartbeat(
                worker_id=worker_id,
                status="running",
                started_at=now,
                heartbeat_at=now,
                metadata_json={"progress_watermark_at": now.isoformat()},
            )
        )
        db.commit()
        runnable = build_runnable_pending_query(
            EXECUTABLE_JOB_TYPES, now=now
        ).subquery()
        assert db.scalar(select(func.count()).select_from(runnable)) == 0

    monkeypatch.setattr(healthcheck, "SessionLocal", factory)
    monkeypatch.setenv("JOB_WORKER_ID", worker_id)
    with pytest.raises(SystemExit) as exit_status:
        healthcheck.main()
    assert exit_status.value.code == 0


@pytest.mark.asyncio
async def test_local_scan_commit_then_response_loss_reconciles_on_exhausted_attempt(
    pg_sessions, monkeypatch
):
    from types import SimpleNamespace

    from src.jobs.local_scan_job import handle_local_scan_job

    factory, department_id = pg_sessions
    scan_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="https://example.edu",
                scan_type=ScanType.WEBSITE,
                status=ScanStatus.PROCESSING,
            )
        )
        db.commit()

    async def commit_then_lose_response(**_kwargs):
        with factory() as child_db:
            child_scan = child_db.get(Scan, scan_id)
            child_scan.status = ScanStatus.COMPLETED
            child_scan.completed_at = datetime.now(timezone.utc)
            child_db.add(
                ScanResult(
                    scan_id=scan_id,
                    compliance_score=100.0,
                    issues=[],
                )
            )
            child_db.commit()
        raise RuntimeError("child response transport lost")

    monkeypatch.setattr(
        "src.jobs.local_scan_subprocess.run_local_scan_subprocess",
        commit_then_lose_response,
    )
    job = SimpleNamespace(
        id=str(uuid.uuid4()),
        department_id=department_id,
        provider=None,
        credential_id=None,
        cloud_file_id=None,
        provider_file_id=None,
        claim_token="local-response-loss",
        worker_id="local-response-loss-worker",
        attempt_count=1,
        max_retries=1,
        payload={
            "scan_kind": "local_web",
            "scan_id": scan_id,
            "options": {
                "url": "https://example.edu",
                "mode": "quick",
                "scan_images": False,
                "scan_multimedia": False,
                "scan_math": False,
                "validate_alt_text": False,
                "max_depth": 1,
                "max_pages": 10,
                "generate_code_fixes": True,
                "capture_screenshots": True,
            },
        },
    )
    with factory() as db:
        result = await handle_local_scan_job(job, db)

    assert result == JobSuccess({"success": True, "scan_id": scan_id})
    with factory() as db:
        assert db.get(Scan, scan_id).status == ScanStatus.COMPLETED
        assert db.query(ScanResult).filter(ScanResult.scan_id == scan_id).count() == 1


def test_legacy_child_commit_fence_rejects_committed_cancellation(pg_sessions):
    from src.jobs.execution_authority import install_child_commit_fence

    factory, department_id = pg_sessions
    scan_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="legacy-large.pdf",
                scan_type=ScanType.PDF,
                status=ScanStatus.PROCESSING,
                progress=10,
            )
        )
        db.add(
            CloudJobQueue(
                id=job_id,
                department_id=department_id,
                job_type="scan",
                payload={"scan_id": scan_id},
                status="processing",
                claim_token="legacy-cancel-claim",
                worker_id="legacy-cancel-worker",
                claimed_at=now,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(minutes=1),
            )
        )
        db.commit()

    remove_fence = install_child_commit_fence(
        job_id=job_id,
        claim_token="legacy-cancel-claim",
        worker_id="legacy-cancel-worker",
    )
    try:
        with factory() as legacy_db:
            legacy_scan = legacy_db.get(Scan, scan_id)
            legacy_scan.progress = 99
            # Cancellation is a separate API process in production; raw SQL
            # models that transaction without inheriting the child listener.
            with legacy_db.get_bind().begin() as cancellation:
                cancellation.execute(
                    update(CloudJobQueue)
                    .where(CloudJobQueue.id == job_id)
                    .values(
                        last_error_code="scan_cancel_requested",
                        error_message="scan_cancel_requested",
                    )
                )
            with pytest.raises(RuntimeError, match="ownership lost"):
                legacy_db.commit()
            legacy_db.rollback()
    finally:
        remove_fence()

    with factory() as db:
        assert db.get(Scan, scan_id).progress == 10
        assert db.get(CloudJobQueue, job_id).last_error_code == "scan_cancel_requested"


def test_brightspace_terminal_commit_cannot_win_after_cancellation(pg_sessions):
    from src.jobs.brightspace_content_job import _commit_terminal_outcome
    from src.jobs.contracts import LostJobOwnership

    factory, department_id = pg_sessions
    job_id = enqueue(
        factory,
        department_id,
        job_type="remediate",
        payload={"execution": "brightspace_content"},
        status="processing",
        claim_token="example-brightspace-cancel-claim",
        worker_id="brightspace-cancel-worker",
        claimed_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        last_error_code="scan_cancel_requested",
        error_message="scan_cancel_requested",
        max_retries=0,
    )
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        with pytest.raises(LostJobOwnership):
            _commit_terminal_outcome(
                db,
                job,
                {
                    "success": True,
                    "status": "completed",
                    "fixed_count": 1,
                    "manual_count": 0,
                    "failed_count": 0,
                    "skipped_count": 0,
                    "download_available": True,
                },
            )
        db.rollback()
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "processing"
        assert job.last_error_code == "scan_cancel_requested"


def test_brightspace_domain_and_queue_outcome_commit_atomically_once(pg_sessions):
    from src.jobs.brightspace_content_job import _commit_terminal_outcome
    from src.jobs.contracts import LostJobOwnership
    from src.jobs.execution_authority import install_child_commit_fence

    factory, department_id = pg_sessions
    credential_id = str(uuid.uuid4())
    cloud_file_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add(
            CloudOAuthCredentials(
                id=credential_id,
                department_id=department_id,
                provider=CloudProvider.BRIGHTSPACE.value,
                access_token="encrypted-test-token",
                refresh_token="encrypted-test-refresh",
                token_expires_at=now + timedelta(hours=1),
                is_active=True,
            )
        )
        db.add(
            CloudFile(
                id=cloud_file_id,
                department_id=department_id,
                credential_id=credential_id,
                provider=CloudProvider.BRIGHTSPACE.value,
                provider_file_id="topic-7",
                provider_parent_id="course-42",
                file_name="page.html",
                file_type="html",
                content_body="<p>source</p>",
            )
        )
        db.flush()
        db.add(
            CloudJobQueue(
                id=job_id,
                department_id=department_id,
                job_type="remediate",
                payload={"execution": "brightspace_content"},
                provider=CloudProvider.BRIGHTSPACE.value,
                credential_id=credential_id,
                cloud_file_id=cloud_file_id,
                provider_file_id="topic-7",
                status="processing",
                claim_token="atomic-claim",
                worker_id="atomic-worker",
                claimed_at=now,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(minutes=1),
                max_retries=0,
            )
        )
        db.commit()

    outcome = {
        "success": True,
        "status": "completed",
        "fixed_count": 1,
        "manual_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "download_available": True,
        "ai_used": False,
        "external_ai_used": False,
        "providers": [],
        "purpose_decisions": {"remediation": "used"},
    }
    with factory() as db:
        cloud_file = db.get(CloudFile, cloud_file_id)
        job = db.get(CloudJobQueue, job_id)
        remove_fence = install_child_commit_fence(
            job_id=job_id,
            claim_token="atomic-claim",
            worker_id="atomic-worker",
        )
        cloud_file.remediated_body = "<p>one durable effect</p>"
        cloud_file.has_remediated_version = True
        # The child removes its legacy-processor listener immediately before
        # the explicit queue-row-fenced terminal transaction.
        remove_fence()
        _commit_terminal_outcome(db, job, outcome)

    # Simulate loss of the child response after the single database commit.
    with factory() as db:
        cloud_file = db.get(CloudFile, cloud_file_id)
        job = db.get(CloudJobQueue, job_id)
        assert cloud_file.remediated_body == "<p>one durable effect</p>"
        assert job.status == "completed"
        assert job.result_data["fixed_count"] == 1
        assert job.claim_token is None
        with pytest.raises(LostJobOwnership):
            _commit_terminal_outcome(db, job, outcome)
        db.rollback()
        assert cloud_file.remediated_body == "<p>one durable effect</p>"


@pytest.mark.asyncio
async def test_cancellation_terminalizes_only_after_child_group_is_reaped(
    pg_sessions, tmp_path
):
    factory, department_id = pg_sessions
    scan_id = str(uuid.uuid4())
    started = tmp_path / "cancel-started"
    late = tmp_path / "cancel-late"
    with factory() as db:
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="large.pdf",
                scan_type=ScanType.PDF,
                status=ScanStatus.PROCESSING,
            )
        )
        db.commit()

    async def handler(_context, _db, _tokens):
        code = (
            "import pathlib,time;"
            f"pathlib.Path({str(started)!r}).write_text('started');"
            "time.sleep(1);"
            f"pathlib.Path({str(late)!r}).write_text('late')"
        )
        await _run_process(
            (os.sys.executable, "-c", code),
            timeout_seconds=None,
            termination_grace_seconds=0.1,
        )
        return JobSuccess({"success": True, "scan_id": scan_id})

    job_id = enqueue(
        factory,
        department_id,
        payload={"scan_id": scan_id},
        dedupe_key=f"local-scan:{scan_id}",
        max_retries=1,
    )
    worker = processor(
        factory,
        "cancel-race-worker",
        registry_with(handler),
        heartbeat_interval=0.01,
        lease_seconds=30,
    )
    [claim] = worker.claim_batch()
    task = asyncio.create_task(worker.process_claim(claim))
    for _ in range(200):
        if started.exists():
            break
        await asyncio.sleep(0.01)
    assert started.exists()
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        job.last_error_code = "scan_cancel_requested"
        job.error_message = "scan_cancel_requested"
        db.commit()
    with factory() as db:
        requested = db.get(CloudJobQueue, job_id)
        assert requested.status == "processing"
        assert requested.claim_token is not None

    assert await asyncio.wait_for(task, timeout=3) is True
    await asyncio.sleep(1.1)
    assert not late.exists()
    with factory() as db:
        terminal = db.get(CloudJobQueue, job_id)
        assert terminal.status == "failed"
        assert terminal.last_error_code == "scan_cancelled"
        assert terminal.claim_token is None
        assert db.get(Scan, scan_id) is None


def test_duplicate_cancellation_keeps_scan_until_last_claim_acknowledges(pg_sessions):
    factory, department_id = pg_sessions
    scan_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="large.pdf",
                scan_type=ScanType.PDF,
                status=ScanStatus.PROCESSING,
            )
        )
        db.commit()
    job_ids = [
        enqueue(
            factory,
            department_id,
            payload={"scan_id": scan_id},
            dedupe_key=f"cancel-duplicate:{index}:{scan_id}",
            status="processing",
            claim_token=f"claim-{index}",
            worker_id=f"cancel-worker-{index}",
            claimed_at=now,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(minutes=1),
            last_error_code="scan_cancel_requested",
            error_message="scan_cancel_requested",
        )
        for index in range(2)
    ]
    worker = JobProcessor(session_factory=factory, registry=registry_with(AsyncMock()))
    claims = [
        ClaimedJob(
            job_id,
            "scan",
            {"scan_id": scan_id},
            f"claim-{index}",
            f"cancel-worker-{index}",
            1,
            3,
        )
        for index, job_id in enumerate(job_ids)
    ]

    assert worker._acknowledge_cancellation(claims[0]) is True
    with factory() as db:
        assert db.get(Scan, scan_id) is not None
    assert worker._acknowledge_cancellation(claims[1]) is True
    with factory() as db:
        assert db.get(Scan, scan_id) is None


def test_expired_cancellation_waits_for_live_child_then_recovers_dead_child(
    pg_sessions,
):
    from src.jobs.execution_authority import acquire_child_execution_lock

    factory, department_id = pg_sessions
    scan_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="large.pdf",
                scan_type=ScanType.PDF,
                status=ScanStatus.PROCESSING,
            )
        )
        db.commit()
    job_id = enqueue(
        factory,
        department_id,
        payload={"scan_id": scan_id, "scan_kind": "local_pdf"},
        status="processing",
        claim_token="expired-cancel-claim",
        worker_id="expired-cancel-worker",
        claimed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        last_error_code="scan_cancel_requested",
        error_message="scan_cancel_requested",
    )
    reaper = processor(
        factory,
        "expired-cancel-reaper",
        registry_with(AsyncMock()),
    )

    child_authority = factory()
    authority = acquire_child_execution_lock(
        child_authority,
        job_id=job_id,
        claim_token="expired-cancel-claim",
    )
    try:
        assert reaper.reap_stale_jobs() == 0
        with factory() as db:
            job = db.get(CloudJobQueue, job_id)
            assert job.status == "processing"
            assert job.last_error_code == "scan_cancel_requested"
            assert job.claim_token == "expired-cancel-claim"
            assert db.get(Scan, scan_id) is not None
    finally:
        authority.close()
        child_authority.close()

    assert reaper.reap_stale_jobs() == 1
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "failed"
        assert job.last_error_code == "scan_cancelled"
        assert job.claim_token is None
        assert db.get(Scan, scan_id) is None


@pytest.mark.skipif(sys.platform != "linux", reason="requires SIGSTOP/SIGKILL")
def test_frozen_child_stays_nonterminal_and_hard_dead_child_recovers_without_late_effect(
    pg_sessions, tmp_path
):
    from src.jobs.execution_authority import claim_advisory_lock_key

    factory, department_id = pg_sessions
    scan_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    claim_token = "hard-death-claim"
    started = tmp_path / "advisory-child-started"
    late = tmp_path / "advisory-child-late"
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="frozen.pdf",
                scan_type=ScanType.PDF,
                status=ScanStatus.PROCESSING,
            )
        )
        db.add(
            CloudJobQueue(
                id=job_id,
                department_id=department_id,
                job_type="scan",
                payload={"scan_id": scan_id, "scan_kind": "local_pdf"},
                status="processing",
                claim_token=claim_token,
                worker_id="hard-death-worker",
                claimed_at=now - timedelta(minutes=2),
                heartbeat_at=now - timedelta(minutes=2),
                lease_expires_at=now - timedelta(seconds=1),
                last_error_code="scan_cancel_requested",
                error_message="scan_cancel_requested",
            )
        )
        db.commit()
    advisory_key = claim_advisory_lock_key(job_id, claim_token)
    code = (
        "import os,pathlib,time;"
        "from sqlalchemy import create_engine,text;"
        "engine=create_engine(os.environ['TEST_MIGRATION_DATABASE_URL']);"
        "connection=engine.connect();"
        f"connection.execute(text('SELECT pg_advisory_lock({advisory_key})'));"
        f"pathlib.Path({str(started)!r}).write_text('started');"
        "time.sleep(2);"
        f"pathlib.Path({str(late)!r}).write_text('late')"
    )
    child = subprocess.Popen((sys.executable, "-c", code))
    reaper = processor(factory, "hard-death-reaper", registry_with(AsyncMock()))
    try:
        for _ in range(200):
            if started.exists():
                break
            if child.poll() is not None:
                pytest.fail("advisory child exited before acquiring authority")
            time.sleep(0.01)
        assert started.exists()
        os.kill(child.pid, signal.SIGSTOP)
        assert reaper.reap_stale_jobs() == 0
        with factory() as db:
            assert db.get(CloudJobQueue, job_id).status == "processing"

        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=3)
        assert reaper.reap_stale_jobs() == 1
        time.sleep(2.1)
        assert not late.exists()
        with factory() as db:
            job = db.get(CloudJobQueue, job_id)
            assert job.status == "failed"
            assert job.last_error_code == "scan_cancelled"
            assert job.claim_token is None
            assert db.get(Scan, scan_id) is None
    finally:
        if child.poll() is None:
            os.kill(child.pid, signal.SIGKILL)
            child.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "linux", reason="requires SIGSTOP/SIGKILL")
def test_expired_live_child_is_not_requeued_until_hard_death(pg_sessions, tmp_path):
    from src.jobs.execution_authority import claim_advisory_lock_key

    factory, department_id = pg_sessions
    scan_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    claim_token = "ordinary-expired-claim"
    started = tmp_path / "ordinary-child-started"
    late = tmp_path / "ordinary-child-late"
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="ordinary-frozen.pdf",
                scan_type=ScanType.PDF,
                status=ScanStatus.PROCESSING,
            )
        )
        db.add(
            CloudJobQueue(
                id=job_id,
                department_id=department_id,
                job_type="scan",
                payload={"scan_id": scan_id, "scan_kind": "local_pdf"},
                status="processing",
                claim_token=claim_token,
                worker_id="ordinary-expired-worker",
                claimed_at=now - timedelta(minutes=2),
                heartbeat_at=now - timedelta(minutes=2),
                lease_expires_at=now - timedelta(seconds=1),
                attempt_count=1,
                max_retries=1,
            )
        )
        db.commit()
    advisory_key = claim_advisory_lock_key(job_id, claim_token)
    code = (
        "import os,pathlib,time;"
        "from sqlalchemy import create_engine,text;"
        "engine=create_engine(os.environ['TEST_MIGRATION_DATABASE_URL']);"
        "connection=engine.connect();"
        f"connection.execute(text('SELECT pg_advisory_lock({advisory_key})'));"
        f"pathlib.Path({str(started)!r}).write_text('started');"
        "time.sleep(2);"
        f"pathlib.Path({str(late)!r}).write_text('late')"
    )
    child = subprocess.Popen((sys.executable, "-c", code))
    reaper = processor(factory, "ordinary-expired-reaper", registry_with(AsyncMock()))
    try:
        for _ in range(200):
            if started.exists():
                break
            if child.poll() is not None:
                pytest.fail("ordinary child exited before acquiring authority")
            time.sleep(0.01)
        assert started.exists()
        os.kill(child.pid, signal.SIGSTOP)
        assert reaper.reap_stale_jobs() == 0
        with factory() as db:
            job = db.get(CloudJobQueue, job_id)
            assert job.status == "processing"
            assert job.claim_token == claim_token
            assert db.get(Scan, scan_id).status == ScanStatus.PROCESSING

        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=3)
        assert reaper.reap_stale_jobs() == 1
        time.sleep(2.1)
        assert not late.exists()
        with factory() as db:
            job = db.get(CloudJobQueue, job_id)
            assert job.status == "failed"
            assert job.last_error_code == "job_lease_expired"
            assert job.claim_token is None
            assert db.get(Scan, scan_id).status == ScanStatus.FAILED
    finally:
        if child.poll() is None:
            os.kill(child.pid, signal.SIGKILL)
            child.wait(timeout=3)


def test_reaper_first_authority_blocks_late_child_start_and_fences_execution(
    pg_sessions,
):
    from src.jobs.execution_authority import (
        acquire_child_execution_lock,
        claim_is_current,
        try_acquire_recovery_lock as real_try_acquire_recovery_lock,
    )

    factory, department_id = pg_sessions
    job_id = enqueue(
        factory,
        department_id,
        payload={"scan_kind": "local_pdf"},
        status="processing",
        claim_token="reaper-first-claim",
        worker_id="reaper-first-old-worker",
        claimed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        attempt_count=1,
        max_retries=1,
    )
    recovery_acquired = Event()
    allow_reaper_commit = Event()

    def gated_recovery(db, *, job_id, claim_token):
        acquired = real_try_acquire_recovery_lock(
            db, job_id=job_id, claim_token=claim_token
        )
        if acquired:
            recovery_acquired.set()
            assert allow_reaper_commit.wait(timeout=3)
        return acquired

    def late_child_attempt() -> bool:
        with factory() as child_db:
            authority = acquire_child_execution_lock(
                child_db, job_id=job_id, claim_token="reaper-first-claim"
            )
            try:
                return claim_is_current(
                    child_db,
                    job_id=job_id,
                    claim_token="reaper-first-claim",
                    worker_id="reaper-first-old-worker",
                    lock_row=False,
                )
            finally:
                authority.close()

    reaper = processor(factory, "reaper-first-new-worker", registry_with(AsyncMock()))
    with (
        patch(
            "src.jobs.execution_authority.try_acquire_recovery_lock",
            side_effect=gated_recovery,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        reaper_future = pool.submit(reaper.reap_stale_jobs)
        assert recovery_acquired.wait(timeout=3)
        child_future = pool.submit(late_child_attempt)
        time.sleep(0.05)
        assert not child_future.done()
        allow_reaper_commit.set()
        assert reaper_future.result(timeout=3) == 1
        assert child_future.result(timeout=3) is False

    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "failed"
        assert job.last_error_code == "job_lease_expired"
        assert job.claim_token is None


@pytest.fixture
def pg_sessions():
    url = os.getenv("TEST_MIGRATION_DATABASE_URL")
    required = os.getenv("REQUIRE_WORKER_POSTGRES_TESTS", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if not url:
        if required:
            pytest.fail("required worker PostgreSQL URL is missing")
        pytest.skip("requires TEST_MIGRATION_DATABASE_URL")
    require_disposable_postgres_url(url, destructive=True)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        if required:
            pytest.fail(
                f"required worker PostgreSQL is unavailable: {type(exc).__name__}"
            )
        pytest.skip("PostgreSQL unavailable")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    factory._test_worker_ids = set()
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
        if factory._test_worker_ids:
            db.execute(
                delete(WorkerHeartbeat).where(
                    WorkerHeartbeat.worker_id.in_(factory._test_worker_ids)
                )
            )
        scan_ids = select(Scan.id).where(Scan.department_id == department_id)
        db.execute(
            delete(RemediationArtifact).where(
                RemediationArtifact.department_id == department_id
            )
        )
        db.execute(delete(ScanResult).where(ScanResult.scan_id.in_(scan_ids)))
        db.execute(delete(Scan).where(Scan.department_id == department_id))
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
                job_type=values.pop("job_type", "scan"),
                payload=values.pop("payload", {}),
                status=values.pop("status", "pending"),
                **values,
            )
        )
        db.commit()
    return job_id


def seed_foreign_scan_contract(factory) -> dict[str, str]:
    department_id = str(uuid.uuid4())
    scan_id = str(uuid.uuid4())
    result_id = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            Department(
                id=department_id,
                name="Foreign tenant",
                institution="Foreign test",
                contact_email=f"{department_id}@example.test",
            )
        )
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                file_name="foreign.pdf",
                scan_type=ScanType.PDF,
                status=ScanStatus.PROCESSING,
            )
        )
        db.add(
            ScanResult(
                id=result_id,
                scan_id=scan_id,
                compliance_score=73.0,
                issues=[{"code": "foreign-tenant-sentinel"}],
            )
        )
        db.add(
            RemediationArtifact(
                id=artifact_id,
                department_id=department_id,
                scan_id=scan_id,
                provider="local",
                scan_type="PDF",
                storage_key=f"tenant-isolation/{artifact_id}",
                filename="foreign-remediated.pdf",
                mime_type="application/pdf",
                size_bytes=8,
                sha256="a" * 64,
                lifecycle_status="available",
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db.commit()
    return {
        "department_id": department_id,
        "scan_id": scan_id,
        "result_id": result_id,
        "artifact_id": artifact_id,
    }


def assert_foreign_scan_contract(db, foreign: dict[str, str]) -> None:
    scan = db.get(Scan, foreign["scan_id"])
    result = db.get(ScanResult, foreign["result_id"])
    artifact = db.get(RemediationArtifact, foreign["artifact_id"])
    assert scan is not None
    assert scan.department_id == foreign["department_id"]
    assert scan.status == ScanStatus.PROCESSING
    assert result is not None
    assert result.scan_id == foreign["scan_id"]
    assert result.compliance_score == 73.0
    assert result.issues == [{"code": "foreign-tenant-sentinel"}]
    assert artifact is not None
    assert artifact.department_id == foreign["department_id"]
    assert artifact.scan_id == foreign["scan_id"]
    assert artifact.sha256 == "a" * 64


def cleanup_foreign_scan_contract(factory, foreign: dict[str, str]) -> None:
    with factory() as db:
        db.execute(
            delete(RemediationArtifact).where(
                RemediationArtifact.id == foreign["artifact_id"]
            )
        )
        db.execute(delete(ScanResult).where(ScanResult.id == foreign["result_id"]))
        db.execute(delete(Scan).where(Scan.id == foreign["scan_id"]))
        db.execute(delete(Department).where(Department.id == foreign["department_id"]))
        db.commit()


def processor(factory, worker_id, registry, **kwargs) -> JobProcessor:
    kwargs.setdefault("poll_interval", 0.01)
    kwargs.setdefault("heartbeat_interval", 0.01)
    with factory() as db:
        if db.get(WorkerHeartbeat, worker_id) is not None:
            raise RuntimeError(f"fixture worker ID already exists: {worker_id}")
    factory._test_worker_ids.add(worker_id)
    value = JobProcessor(
        worker_id=worker_id,
        session_factory=factory,
        registry=registry,
        **kwargs,
    )
    value._token_manager = MagicMock()
    return value


def upload_registry(handler) -> JobRegistry:
    registry = JobRegistry()
    registry.register("upload", handler)
    return registry


def _column_constraints(db, *, column_name: str, constraint_type: str):
    """Return exact PostgreSQL definitions for constraints touching one column."""
    return list(
        db.execute(
            text("""
                SELECT constraint_row.conname AS name,
                       pg_get_constraintdef(constraint_row.oid, true) AS definition,
                       constraint_row.convalidated AS validated
                  FROM pg_constraint AS constraint_row
                  JOIN pg_attribute AS attribute_row
                    ON attribute_row.attrelid = constraint_row.conrelid
                   AND attribute_row.attnum = ANY(constraint_row.conkey)
                 WHERE constraint_row.conrelid = 'cloud_job_queue'::regclass
                   AND constraint_row.contype = :constraint_type
                   AND attribute_row.attname = :column_name
                 ORDER BY constraint_row.conname
                """),
            {
                "column_name": column_name,
                "constraint_type": constraint_type,
            },
        )
        .mappings()
        .all()
    )


def _drop_constraints(db, constraints) -> None:
    quote = db.bind.dialect.identifier_preparer.quote
    for constraint in constraints:
        db.execute(
            text(
                "ALTER TABLE cloud_job_queue DROP CONSTRAINT "
                f"{quote(constraint['name'])}"
            )
        )


def _restore_constraints(db, constraints) -> None:
    quote = db.bind.dialect.identifier_preparer.quote
    for constraint in constraints:
        definition = constraint["definition"]
        if not constraint["validated"] and "NOT VALID" not in definition.upper():
            definition = f"{definition} NOT VALID"
        db.execute(
            text(
                "ALTER TABLE cloud_job_queue ADD CONSTRAINT "
                f"{quote(constraint['name'])} {definition}"
            )
        )


@pytest.mark.asyncio
async def test_upload_checkpoint_crash_is_reaped_manual_and_never_reclaimed(
    pg_sessions,
):
    factory, department_id = pg_sessions
    provider = AsyncMock()
    job_id = enqueue(factory, department_id, job_type="upload")
    first = processor(
        factory,
        "task17-upload-crashed",
        upload_registry(AsyncMock()),
        lease_seconds=1,
    )
    second = processor(
        factory,
        "task17-upload-retry",
        upload_registry(provider),
    )
    [claim] = first.claim_batch()

    token = await first.begin_external_effect(claim)
    assert await first.begin_external_effect(claim) == token
    with factory() as db:
        db.execute(
            update(CloudJobQueue)
            .where(CloudJobQueue.id == job_id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        db.commit()

    assert second.reap_stale_jobs() == 1
    assert second.claim_batch() == []
    provider.assert_not_awaited()
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "failed"
        assert job.last_error_code == "upload_outcome_indeterminate"
        assert job.last_error_retryable is False
        assert job.result_data == {"retry_safe": False, "manual_required": True}
        assert job.external_effect_state == "indeterminate"
        assert job.external_effect_token == token


@pytest.mark.asyncio
async def test_upload_timeout_after_checkpoint_is_terminal_without_retry(pg_sessions):
    factory, department_id = pg_sessions
    accepted = asyncio.Event()

    async def accepted_then_hang(context, _db, _tokens):
        await context.begin_external_effect()
        accepted.set()
        await asyncio.sleep(10)

    job_id = enqueue(factory, department_id, job_type="upload")
    worker = processor(
        factory,
        "task17-upload-timeout",
        upload_registry(accepted_then_hang),
        max_execution_seconds=1,
    )
    [claim] = worker.claim_batch()
    assert await worker.process_claim(claim) is True
    assert accepted.is_set()
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "failed"
        assert job.last_error_code == "upload_outcome_indeterminate"
        assert job.external_effect_state == "indeterminate"


@pytest.mark.asyncio
async def test_upload_success_confirms_effect_and_completes_once(pg_sessions):
    factory, department_id = pg_sessions
    provider_calls = 0

    async def upload_once(context, _db, _tokens):
        nonlocal provider_calls
        await context.begin_external_effect()
        provider_calls += 1
        return JobSuccess({"uploaded": True})

    job_id = enqueue(factory, department_id, job_type="upload")
    worker = processor(factory, "task17-upload-success", upload_registry(upload_once))
    [claim] = worker.claim_batch()
    assert await worker.process_claim(claim) is True
    assert await worker._process_batch() == 0
    assert provider_calls == 1
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "completed"
        assert job.external_effect_state == "confirmed"
        assert job.external_effect_token is not None


@pytest.mark.asyncio
async def test_upload_pre_request_failure_keeps_bounded_retry(pg_sessions):
    factory, department_id = pg_sessions

    async def fail_before_request(_context, _db, _tokens):
        return JobFailure.retryable("artifact_temporarily_unavailable")

    job_id = enqueue(factory, department_id, job_type="upload")
    worker = processor(
        factory,
        "task17-upload-pre-request",
        upload_registry(fail_before_request),
    )
    [claim] = worker.claim_batch()
    assert await worker.process_claim(claim) is True
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "pending"
        assert job.external_effect_state is None
        assert job.external_effect_token is None
        assert job.last_error_retryable is True


@pytest.mark.asyncio
async def test_upload_heartbeat_ownership_loss_reaps_manual(pg_sessions):
    factory, department_id = pg_sessions
    checkpointed = asyncio.Event()

    async def wait_after_checkpoint(context, _db, _tokens):
        await context.begin_external_effect()
        checkpointed.set()
        await asyncio.Event().wait()

    job_id = enqueue(factory, department_id, job_type="upload")
    worker = processor(
        factory,
        "task17-upload-heartbeat-loss",
        upload_registry(wait_after_checkpoint),
        heartbeat_interval=0.02,
        lease_seconds=1,
    )
    reaper = processor(
        factory,
        "task17-upload-heartbeat-reaper",
        upload_registry(AsyncMock()),
    )
    [claim] = worker.claim_batch()
    task = asyncio.create_task(worker.process_claim(claim))
    await asyncio.wait_for(checkpointed.wait(), timeout=1)
    with factory() as db:
        db.execute(
            update(CloudJobQueue)
            .where(CloudJobQueue.id == job_id)
            .values(
                worker_id="task17-stolen-owner",
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
        db.commit()

    assert await asyncio.wait_for(task, timeout=1) is False
    assert reaper.reap_stale_jobs() == 1
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == "failed"
        assert job.last_error_code == "upload_outcome_indeterminate"
        assert job.external_effect_state == "indeterminate"


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
async def test_google_requesting_webhook_is_terminal_manual_without_requeue(
    pg_sessions, monkeypatch
):
    from src.jobs import webhook_refresh_job

    factory, department_id = pg_sessions
    credential_id = str(uuid.uuid4())
    subscription_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add(
            CloudOAuthCredentials(
                id=credential_id,
                department_id=department_id,
                provider="google",
                access_token="encrypted-access-token",
                refresh_token="encrypted-refresh-token",
                token_expires_at=now + timedelta(hours=1),
                is_active=True,
            )
        )
        db.add(
            CloudWebhookSubscription(
                id=subscription_id,
                department_id=department_id,
                credential_id=credential_id,
                provider="google",
                subscription_id="active-channel",
                provider_resource_id="watched-file",
                expiration_time=now + timedelta(days=1),
                notification_url="https://example.test/hooks/google",
                is_active=True,
                renewal_status="requesting",
                renewal_result={"correlation_id": job_id},
                pending_renewal_channel_id="safe-pending-channel",
                pending_renewal_started_at=now,
            )
        )
        db.add(
            CloudJobQueue(
                id=job_id,
                department_id=department_id,
                credential_id=credential_id,
                provider="google",
                job_type="webhook_refresh",
                payload={"subscription_id": subscription_id},
                max_retries=3,
            )
        )
        db.commit()

    integration_constructor = MagicMock()
    monkeypatch.setattr(
        webhook_refresh_job, "GoogleDriveIntegration", integration_constructor
    )
    registry = JobRegistry()
    registry.register(
        "webhook_refresh",
        adapt_legacy_handler(webhook_refresh_job.handle_webhook_refresh_job),
    )
    worker = processor(factory, "task17-worker-google-manual", registry)

    [claim] = worker.claim_batch()
    assert claim.attempt_count == 1
    assert await worker.process_claim(claim) is True
    integration_constructor.assert_not_called()

    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        subscription = db.get(CloudWebhookSubscription, subscription_id)
        assert job.status == CloudJobStatus.FAILED.value
        assert job.attempt_count == 1
        assert job.completed_at is not None
        assert job.last_error_code == "webhook_provider_outcome_indeterminate"
        assert job.last_error_retryable is False
        assert job.result_data == {
            "provider": "google",
            "retry_safe": False,
            "manual_required": True,
        }
        assert job.claim_token is None
        assert job.worker_id is None
        assert subscription.renewal_status == "indeterminate"
        assert not ({"raw", "token", "path"} & job.result_data.keys())

    assert await worker._process_batch() == 0
    assert worker.reap_stale_jobs() == 0
    integration_constructor.assert_not_called()
    with factory() as db:
        job = db.get(CloudJobQueue, job_id)
        assert job.status == CloudJobStatus.FAILED.value
        assert job.attempt_count == 1


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
        payload_constraints = [
            constraint
            for constraint in _column_constraints(
                db,
                column_name="payload",
                constraint_type="c",
            )
            if constraint["name"] == "ck_cloud_job_queue_payload_object"
            or (
                "jsonb_typeof" in constraint["definition"].lower()
                and "payload" in constraint["definition"].lower()
            )
        ]
        assert payload_constraints
        _drop_constraints(db, payload_constraints)
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
                    "UPDATE cloud_job_queue SET payload = '{}'::jsonb WHERE id = :job_id"
                ),
                {"job_id": malformed_id},
            )
            _restore_constraints(db, payload_constraints)
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
    child_id = None
    with factory() as db:
        dependency_constraints = _column_constraints(
            db,
            column_name="depends_on_job_id",
            constraint_type="f",
        )
        assert dependency_constraints
        _drop_constraints(db, dependency_constraints)
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
            if child_id is not None:
                db.execute(
                    update(CloudJobQueue)
                    .where(CloudJobQueue.id == child_id)
                    .values(depends_on_job_id=None)
                )
            _restore_constraints(db, dependency_constraints)
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
