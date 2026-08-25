"""Durability and provenance contracts for queued Canvas stored HTML."""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from src.db.models import CloudJobStatus
from src.jobs.canvas_content_job import (
    MAX_COMPRESSED_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_QUEUE_PAYLOAD_BYTES,
    MAX_SNAPSHOT_BYTES,
    _decode_snapshot,
    _encode_snapshot,
    handle_canvas_content_job,
)
from src.jobs.contracts import JobContext, JobFailure, JobSuccess
from src.services.canvas_content_provenance import (
    EVIDENCE_MAX_BYTES,
    EVIDENCE_MAX_ROWS_PER_FILE,
    _allowlisted_diagnostics,
    canvas_candidate_fingerprint,
    canvas_content_candidate_is_current,
    canvas_content_sha256,
    publish_canvas_content_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/2026_08_25_canvas_content_queue.py"


def _snapshot() -> dict:
    return {
        "version": 1,
        "department_id": "dept-1",
        "cloud_file_id": "file-1",
        "provider": "canvas",
        "provider_file_id": "page-1",
        "scan_id": "scan-1",
        "content_body": "<p>source</p>",
        "content_sha256": canvas_content_sha256("<p>source</p>"),
        "issues": [{"id": "html-has-lang", "impact": "serious"}],
        "issues_sha256": "0" * 64,
        "options": {"use_ai": False},
        "options_sha256": "1" * 64,
        "last_compliance_score": 75.0,
    }


def test_snapshot_is_deterministic_detached_and_bounded():
    original = _snapshot()
    payload, digest = _encode_snapshot(original)
    decoded = _decode_snapshot(payload)
    original["content_body"] = "mutated after enqueue"

    assert decoded["content_body"] == "<p>source</p>"
    assert payload["snapshot_sha256"] == digest
    assert len(base64.b64decode(payload["snapshot"])) <= MAX_COMPRESSED_BYTES
    assert len(str(payload).encode()) <= MAX_QUEUE_PAYLOAD_BYTES
    assert MAX_SNAPSHOT_BYTES == 8 * 1024 * 1024
    assert MAX_OUTPUT_BYTES == 8 * 1024 * 1024


@pytest.mark.parametrize("tamper", ["snapshot", "snapshot_sha256", "version"])
def test_snapshot_tampering_is_rejected(tamper):
    payload, _ = _encode_snapshot(_snapshot())
    if tamper == "snapshot":
        replacement = "A" if payload["snapshot"][0] != "A" else "B"
        payload["snapshot"] = replacement + payload["snapshot"][1:]
    elif tamper == "snapshot_sha256":
        payload["snapshot_sha256"] = "f" * 64
    else:
        payload["version"] = 2

    with pytest.raises(ValueError, match="invalid_job_payload"):
        _decode_snapshot(payload)


def test_candidate_fingerprint_binds_every_authority_dimension():
    values = {
        "department_id": "dept-1",
        "cloud_file_id": "file-1",
        "source_sha256": "a" * 64,
        "scan_id": "scan-1",
        "producer_job_id": "job-1",
        "snapshot_sha256": "b" * 64,
        "candidate_sha256": "c" * 64,
    }
    baseline = canvas_candidate_fingerprint(**values)
    for key in values:
        changed = dict(values)
        changed[key] = changed[key] + "-changed"
        assert canvas_candidate_fingerprint(**changed) != baseline


def _current_graph():
    cloud_file = SimpleNamespace(
        id="file-1",
        department_id="dept-1",
        provider="canvas",
        provider_file_id="page-1",
        content_source="page",
        content_body="<p>source</p>",
        remediated_body="<p>candidate</p>",
        last_scan_id="scan-1",
        provider_metadata={},
    )
    publish_canvas_content_candidate(
        cloud_file,
        source_sha256=canvas_content_sha256(cloud_file.content_body),
        scan_id=cloud_file.last_scan_id,
        producer_job_id="job-1",
        snapshot_sha256="b" * 64,
        candidate_sha256=canvas_content_sha256(cloud_file.remediated_body),
    )
    job = SimpleNamespace(
        id="job-1",
        status=CloudJobStatus.COMPLETED.value,
        execution_context={
            "version": 1,
            "department_id": "dept-1",
            "cloud_file_id": "file-1",
            "provider": "canvas",
            "provider_file_id": "page-1",
            "scan_id": "scan-1",
            "content_sha256": canvas_content_sha256("<p>source</p>"),
            "snapshot_sha256": "b" * 64,
        },
    )
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = job
    return db, cloud_file, job


def test_current_candidate_requires_completed_owned_job():
    db, cloud_file, _job = _current_graph()
    assert canvas_content_candidate_is_current(db, cloud_file) is True


@pytest.mark.parametrize("stale", ["source", "scan", "candidate", "job"])
def test_stale_source_scan_candidate_or_job_is_rejected(stale):
    db, cloud_file, job = _current_graph()
    if stale == "source":
        cloud_file.content_body = "<p>edited</p>"
    elif stale == "scan":
        cloud_file.last_scan_id = "scan-2"
    elif stale == "candidate":
        cloud_file.remediated_body = "<p>tampered</p>"
    else:
        job.status = CloudJobStatus.FAILED.value
        db.execute.return_value.scalar_one_or_none.return_value = None

    assert canvas_content_candidate_is_current(db, cloud_file) is False


def test_evidence_diagnostics_are_allowlisted_and_bounded():
    diagnostics = _allowlisted_diagnostics(
        {
            "canvas_content_remediation": {
                "job_id": "job-1",
                "scan_id": "scan-1",
                "status": "completed",
                "source_sha256": "a" * 64,
                "provider_exception": "must not persist",
            },
            "canvas_content_candidate": {
                "fingerprint": "b" * 64,
                "source_sha256": "a" * 64,
                "candidate_sha256": "c" * 64,
                "snapshot_sha256": "d" * 64,
                "provider_payload": {"secret": "must not persist"},
            },
            "unrelated": "/private/container/path",
        }
    )
    encoded = str(diagnostics).encode()

    assert b"secret" not in encoded
    assert b"private" not in encoded
    assert len(encoded) <= EVIDENCE_MAX_BYTES
    assert EVIDENCE_MAX_ROWS_PER_FILE == 20


@pytest.mark.asyncio
async def test_handler_revalidates_authority_before_and_after_work(monkeypatch):
    from src.jobs import canvas_content_job as module

    snapshot = _snapshot()
    snapshot["issues_sha256"] = module.hashlib.sha256(
        module._canonical_json(snapshot["issues"])
    ).hexdigest()
    snapshot["options_sha256"] = module.hashlib.sha256(
        module._canonical_json(snapshot["options"])
    ).hexdigest()
    payload, _ = _encode_snapshot(snapshot)
    cloud_file = SimpleNamespace(
        id="file-1",
        department_id="dept-1",
        content_body=snapshot["content_body"],
        remediated_body=None,
        writeback_status=None,
        has_remediated_version=False,
        remediation_origin=None,
        remediated_compliance_score=None,
        remediated_issues_fixed=None,
        remediated_issues_remaining=None,
        last_scan_id="scan-1",
        provider_metadata={},
    )
    job = SimpleNamespace(id="job-1")
    locks = MagicMock(side_effect=[(job, cloud_file), (job, cloud_file)])
    monkeypatch.setattr(module, "_lock_authority", locks)
    monkeypatch.setattr(
        module,
        "_remediate_snapshot",
        lambda _snapshot, _job_id: module._Candidate(
            "<p>candidate</p>", 1, 0, 0, 100.0
        ),
    )
    owned = AsyncMock()
    context = JobContext(
        job_id="job-1",
        job_type="canvas_content",
        payload=payload,
        claim_token="claim-1",
        worker_id="worker-1",
        attempt_count=1,
        report_progress=AsyncMock(return_value=True),
        assert_owned=owned,
    )
    db = MagicMock()

    result = await handle_canvas_content_job(context, db, MagicMock())

    assert isinstance(result, JobSuccess)
    assert locks.call_count == 2
    owned.assert_awaited_once()
    assert cloud_file.writeback_status == "pending_review"
    assert cloud_file.provider_metadata["canvas_content_candidate"]["status"] == (
        "completed"
    )
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_handler_failure_and_lost_authority_publish_nothing(monkeypatch):
    from src.jobs import canvas_content_job as module

    snapshot = _snapshot()
    snapshot["issues_sha256"] = module.hashlib.sha256(
        module._canonical_json(snapshot["issues"])
    ).hexdigest()
    snapshot["options_sha256"] = module.hashlib.sha256(
        module._canonical_json(snapshot["options"])
    ).hexdigest()
    payload, _ = _encode_snapshot(snapshot)
    monkeypatch.setattr(module, "_lock_authority", lambda *_args: None)
    context = JobContext(
        job_id="job-1",
        job_type="canvas_content",
        payload=payload,
        claim_token="claim-1",
        worker_id="worker-1",
        attempt_count=1,
        report_progress=AsyncMock(return_value=True),
    )
    db = MagicMock()

    result = await handle_canvas_content_job(context, db, MagicMock())

    assert isinstance(result, JobFailure)
    assert result.code == "canvas_content_stale_snapshot"
    db.commit.assert_not_called()


def _load_migration():
    spec = importlib.util.spec_from_file_location("canvas_queue_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_has_one_public_head_and_bounded_constraints(monkeypatch):
    migration = _load_migration()
    calls = []
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *items: calls.append(("table", name, items)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table, columns: calls.append(("index", name, table, columns)),
    )

    migration.upgrade()

    assert migration.revision == "20260825_canvas_queue"
    assert migration.down_revision == "20260824_task8_review"
    table_call = next(call for call in calls if call[0] == "table")
    constraints = " ".join(
        str(item.sqltext) for item in table_call[2] if hasattr(item, "sqltext")
    )
    assert "4096" in constraints
    assert "current" in constraints and "expired" in constraints


def test_migration_downgrade_drops_indexes_then_table(monkeypatch):
    migration = _load_migration()
    calls = []
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda name, table_name=None: calls.append(("index", name, table_name)),
    )
    monkeypatch.setattr(
        migration.op, "drop_table", lambda name: calls.append(("table", name))
    )

    migration.downgrade()

    assert calls[-1] == ("table", "canvas_content_remediation_evidence")


def test_migration_upgrades_and_downgrades_supported_sqlite(monkeypatch):
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE departments (id VARCHAR(36) PRIMARY KEY)")
        )
        connection.execute(
            sa.text(
                "CREATE TABLE cloud_files (id VARCHAR(36) PRIMARY KEY, "
                "department_id VARCHAR(36) NOT NULL)"
            )
        )
        connection.execute(sa.text("CREATE TABLE schema_sentinel (id INTEGER)"))
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "canvas_content_remediation_evidence" in inspector.get_table_names()
        assert "schema_sentinel" in inspector.get_table_names()
        checks = {
            item["name"]
            for item in inspector.get_check_constraints(
                "canvas_content_remediation_evidence"
            )
        }
        assert checks == {
            "ck_canvas_content_evidence_hashes",
            "ck_canvas_content_evidence_lifecycle",
            "ck_canvas_content_evidence_reason",
            "ck_canvas_content_evidence_size",
        }
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO canvas_content_remediation_evidence "
                    "(id, department_id, cloud_file_id, source_sha256, "
                    "candidate_sha256, quarantine_reason, diagnostics, "
                    "stored_bytes, lifecycle_state, expires_at) VALUES "
                    "('bad', 'dept-1', 'file-1', :source, :candidate, "
                    "'reason', '{}', 5000, 'current', CURRENT_TIMESTAMP)"
                ),
                {"source": "a" * 64, "candidate": "b" * 64},
            )
        savepoint.rollback()

        migration.downgrade()
        assert (
            "canvas_content_remediation_evidence"
            not in sa.inspect(connection).get_table_names()
        )
        assert "schema_sentinel" in sa.inspect(connection).get_table_names()


def test_queue_type_is_restart_discoverable_and_never_retries():
    from src.jobs.registry import EXECUTABLE_JOB_TYPES, build_default_registry

    assert "canvas_content" in EXECUTABLE_JOB_TYPES
    assert "canvas_content" in build_default_registry().job_types
    source = (ROOT / "src/jobs/canvas_content_job.py").read_text()
    assert "max_retries=0" in source
    assert "await asyncio.to_thread" in source
