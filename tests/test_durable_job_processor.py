"""Durable claimed queue contracts and worker lifecycle tests."""

from __future__ import annotations

import json
import asyncio
import importlib.util
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _complete_registry(handler):
    from src.jobs.registry import EXECUTABLE_JOB_TYPES, JobRegistry

    registry = JobRegistry()
    for job_type in EXECUTABLE_JOB_TYPES:
        registry.register(job_type, handler)
    return registry


def test_job_contracts_are_typed_immutable_and_json_safe():
    from src.jobs.contracts import (
        FailureKind,
        JobContext,
        JobFailure,
        JobSuccess,
        sanitize_json,
    )

    success = JobSuccess({"ok": True, "items": [1, "two"]})
    failure = JobFailure.deterministic("invalid_scope", {"secret": object()})
    claim_marker = "claim-1"
    context = JobContext(
        job_id="job-1",
        job_type="scan",
        payload={"scan_id": "scan-1"},
        claim_token=claim_marker,
        worker_id="worker-1",
        attempt_count=1,
        report_progress=AsyncMock(),
    )

    assert success.result == {"ok": True, "items": [1, "two"]}
    assert failure.kind is FailureKind.DETERMINISTIC
    assert failure.details == {"secret": "<non-json-value>"}
    assert json.loads(json.dumps(sanitize_json({"x": float("nan")}))) == {
        "x": "<non-finite-number>"
    }
    with pytest.raises(FrozenInstanceError):
        context.job_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("payload", [None, [], "text", 1, {"x": 1}.keys()])
def test_job_payload_requires_an_exact_dict(payload):
    from src.jobs.contracts import JobContext

    claim_marker = "claim-1"
    with pytest.raises(ValueError, match="must be an object"):
        JobContext(
            job_id="job-1",
            job_type="scan",
            payload=payload,
            claim_token=claim_marker,
            worker_id="worker-1",
            attempt_count=1,
            report_progress=AsyncMock(),
        )


def test_queue_model_rejects_non_object_payload_at_enqueue_boundary():
    from src.db.models import CloudJobQueue

    with pytest.raises(ValueError, match="payload must be an object"):
        CloudJobQueue(department_id="dept-1", job_type="scan", payload=[])


def test_worker_max_concurrency_is_strictly_bounded():
    from src.jobs.job_processor import JobProcessor

    assert JobProcessor(registry=_complete_registry(AsyncMock())).max_concurrency == 4
    for value in (0, 65, True, 1.5):
        with pytest.raises(ValueError, match="max_concurrency"):
            JobProcessor(
                max_concurrency=value, registry=_complete_registry(AsyncMock())
            )


@pytest.mark.asyncio
async def test_worker_never_claims_beyond_capacity_and_yields_to_handlers():
    from src.jobs.contracts import JobSuccess
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    events = []

    class BoundedProcessor(JobProcessor):
        def __init__(self):
            super().__init__(
                batch_size=50,
                max_concurrency=3,
                poll_interval=0.001,
                heartbeat_interval=0.001,
                registry=_complete_registry(AsyncMock()),
            )
            self.remaining = 20
            self.max_seen = 0

        def _set_worker_state(self, state):
            events.append(("state", state))

        def reap_stale_jobs(self, *, limit=100):
            return 0

        def claim_batch(self, *, limit=None):
            events.append(("claim", limit, len(self._inflight)))
            count = min(self.remaining, limit or self.batch_size)
            self.remaining -= count
            return [
                ClaimedJob(
                    str(self.remaining + i), "scan", {}, str(i), self.worker_id, 1, 3
                )
                for i in range(count)
            ]

        async def process_claim(self, claim):
            events.append(("start", claim.job_id, len(self._inflight)))
            self.max_seen = max(self.max_seen, len(self._inflight))
            await asyncio.sleep(0.002)
            if (
                self.remaining == 0
                and len([e for e in events if e[0] == "start"]) == 20
            ):
                self.request_drain()
            return JobSuccess()

    worker = BoundedProcessor()
    await asyncio.wait_for(worker.start(), timeout=2)

    assert worker.max_seen <= 3
    claims = [event for event in events if event[0] == "claim"]
    assert all(limit <= 3 - inflight for _, limit, inflight in claims)
    claim_positions = [
        index for index, event in enumerate(events) if event[0] == "claim"
    ]
    assert any(
        event[0] == "start"
        for event in events[claim_positions[0] + 1 : claim_positions[1]]
    )
    assert sum(event == ("state", "running") for event in events) > 1
    assert not worker._inflight


def test_task17_migration_backfills_legacy_nulls_and_constrains_payload_object(
    monkeypatch,
):
    path = Path("alembic/versions/2026_08_21_task17a_durable_jobs.py")
    spec = importlib.util.spec_from_file_location("task17a_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []
    monkeypatch.setattr(migration.op, "add_column", lambda *a, **k: None)
    monkeypatch.setattr(migration.op, "alter_column", lambda *a, **k: None)
    monkeypatch.setattr(migration.op, "create_foreign_key", lambda *a, **k: None)
    monkeypatch.setattr(migration.op, "create_check_constraint", lambda *a, **k: None)
    monkeypatch.setattr(migration.op, "create_index", lambda *a, **k: None)
    monkeypatch.setattr(migration.op, "create_table", lambda *a, **k: None)
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()
    sql = "\n".join(str(statement).lower() for statement in statements)
    assert "status = coalesce(status, 'pending')" in sql
    assert "priority = coalesce(priority, 5)" in sql
    assert "progress = coalesce(progress, 0)" in sql
    assert "retry_count = coalesce(retry_count, 0)" in sql
    assert "max_retries = coalesce(max_retries, 3)" in sql
    assert "scheduled_for = coalesce(scheduled_for, created_at, now())" in sql
    assert "payload = coalesce(payload, '{}'::jsonb)" in sql

    checks = []
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda name, table, condition: checks.append((name, condition)),
    )
    migration.upgrade()
    assert (
        "ck_cloud_job_queue_payload_object",
        "jsonb_typeof(payload) = 'object'",
    ) in checks


@pytest.mark.asyncio
async def test_registry_adapters_only_return_explicit_success():
    from src.jobs.contracts import FailureKind, JobFailure, JobSuccess
    from src.jobs.registry import adapt_legacy_handler

    context = SimpleNamespace(job_id="job-1")
    session = MagicMock()
    token_manager = MagicMock()

    good = adapt_legacy_handler(AsyncMock(return_value={"success": True, "value": 3}))
    false = adapt_legacy_handler(
        AsyncMock(return_value={"success": False, "error_code": "scan_failed"})
    )
    malformed = adapt_legacy_handler(AsyncMock(return_value={"value": 3}))

    assert await good(context, session, token_manager) == JobSuccess(
        {"success": True, "value": 3}
    )
    false_result = await false(context, session, token_manager)
    malformed_result = await malformed(context, session, token_manager)
    assert false_result == JobFailure.deterministic("scan_failed")
    assert malformed_result.kind is FailureKind.INDETERMINATE
    assert malformed_result.code == "malformed_handler_result"


def test_registry_startup_validation_covers_every_executable_type():
    from src.jobs.registry import EXECUTABLE_JOB_TYPES, JobRegistry

    registry = JobRegistry()
    for job_type in sorted(EXECUTABLE_JOB_TYPES)[:-1]:
        registry.register(job_type, AsyncMock())

    with pytest.raises(RuntimeError, match="Missing job handlers"):
        registry.validate()

    registry.register(sorted(EXECUTABLE_JOB_TYPES)[-1], AsyncMock())
    registry.validate()


def test_claim_query_is_skip_locked_and_dependency_gated():
    from sqlalchemy.dialects import postgresql

    from src.jobs.job_processor import build_claim_query

    sql = str(
        build_claim_query({"scan", "sync"}, limit=7).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()

    assert "for update" in sql
    assert "skip locked" in sql
    assert "limit 7" in sql
    assert "depends_on_job_id" in sql
    assert "completed" in sql


def test_queue_json_columns_compile_for_postgres_and_sqlite():
    from sqlalchemy.dialects import postgresql, sqlite

    from src.db.models import CloudJobQueue, WorkerHeartbeat

    for column in (CloudJobQueue.payload, WorkerHeartbeat.metadata_json):
        assert column.type.compile(dialect=postgresql.dialect()) == "JSONB"
        assert column.type.compile(dialect=sqlite.dialect()) == "JSON"


def test_worker_module_does_not_import_fastapi_app():
    import ast
    from pathlib import Path

    worker = Path("src/jobs/worker.py")
    tree = ast.parse(worker.read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert "src.api.main" not in imported


def test_all_compose_modes_have_dedicated_worker_with_shared_storage():
    from pathlib import Path

    import yaml

    for name in (
        "docker-compose.prod.yml",
        "docker-compose.dev.yml",
        "docker-compose.quickstart.yml",
    ):
        compose = yaml.safe_load(Path(name).read_text())
        api = compose["services"]["api"]
        worker = compose["services"]["worker"]
        command = worker["command"]
        if isinstance(command, list):
            command = " ".join(command)
        assert "python -m src.jobs.worker" in command
        assert "upload_data:/app/uploads" in api["volumes"]
        assert "upload_data:/app/uploads" in worker["volumes"]
        assert (
            worker["environment"]["DATABASE_URL"] == api["environment"]["DATABASE_URL"]
        )
        assert worker["environment"]["JOB_WORKER_ID"]
        assert str(worker["environment"]["SKIP_MIGRATIONS"]).lower() == "true"
        assert worker["depends_on"]["api"]["condition"] == "service_healthy"
        assert "src.jobs.healthcheck" in str(worker["healthcheck"]["test"])


@pytest.mark.asyncio
async def test_heartbeat_database_error_fails_closed_and_rolls_back_handler_session():
    from src.jobs.contracts import JobSuccess
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    cancelled = asyncio.Event()
    handler_db = MagicMock()

    async def handler(_context, _db, _token_manager):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return JobSuccess()

    class HeartbeatFailureProcessor(JobProcessor):
        def _owns_claim(self, _claim):
            return True

        def _fenced_update(self, _claim, _values):
            raise RuntimeError("database unavailable: sensitive detail")

    factory = MagicMock()
    factory.return_value.__enter__.return_value = handler_db
    worker = HeartbeatFailureProcessor(
        heartbeat_interval=0.001,
        registry=_complete_registry(handler),
        session_factory=factory,
    )
    worker._token_manager = MagicMock()
    claim = ClaimedJob("job-1", "scan", {}, "token-1", worker.worker_id, 1, 3)

    assert await asyncio.wait_for(worker.process_claim(claim), timeout=1) is False
    assert cancelled.is_set()
    handler_db.rollback.assert_called()
    handler_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_custom_handler_result_must_use_typed_job_contract():
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    async def malformed(_context, _db, _token_manager):
        return {"success": True}

    class CapturingProcessor(JobProcessor):
        def _owns_claim(self, _claim):
            return True

        def _fenced_update(self, _claim, values):
            self.finished_values = values
            return True

        def _record_outcome(self, *, completed):
            self.completed = completed

    worker = CapturingProcessor(
        heartbeat_interval=60,
        registry=_complete_registry(malformed),
        session_factory=MagicMock(),
    )
    worker._token_manager = MagicMock()
    claim = ClaimedJob("job-1", "scan", {}, "token-1", worker.worker_id, 1, 1)

    assert await worker.process_claim(claim) is True
    assert worker.finished_values["status"] == "failed"
    assert worker.finished_values["last_error_code"] == "malformed_handler_result"


@pytest.mark.asyncio
async def test_max_execution_timeout_is_bounded_and_retryable():
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    cancelled = asyncio.Event()

    async def too_slow(_context, _db, _token_manager):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    class CapturingProcessor(JobProcessor):
        def _owns_claim(self, claim):
            return True

        def _fenced_update(self, claim, values):
            self.finished_values = values
            return True

        def _record_outcome(self, *, completed):
            pass

    for invalid in (0, 86401, True, "one hour"):
        with pytest.raises(ValueError, match="max_execution_seconds"):
            JobProcessor(
                registry=_complete_registry(too_slow),
                max_execution_seconds=invalid,
            )

    worker = CapturingProcessor(
        heartbeat_interval=60,
        registry=_complete_registry(too_slow),
        session_factory=MagicMock(),
        max_execution_seconds=1,
    )
    worker._token_manager = MagicMock()
    claim = ClaimedJob("job-1", "scan", {}, "token-1", worker.worker_id, 1, 2)

    assert await asyncio.wait_for(worker.process_claim(claim), timeout=2) is True
    assert cancelled.is_set()
    assert worker.finished_values["status"] == "pending"
    assert worker.finished_values["last_error_code"] == "job_execution_timeout"
