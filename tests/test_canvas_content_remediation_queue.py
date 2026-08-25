"""Durability and provenance contracts for queued Canvas stored HTML."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
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
    MAX_ENCODED_SNAPSHOT_CHARS,
    MAX_OUTPUT_BYTES,
    MAX_QUEUE_PAYLOAD_BYTES,
    MAX_SNAPSHOT_BYTES,
    _decode_snapshot,
    _dependency_matches_canvas_content,
    _encode_snapshot,
    _snapshot_material,
    _snapshot_is_valid,
    handle_canvas_content_job,
)
from src.jobs.contracts import JobContext, JobFailure, JobSuccess
from src.services.canvas_content_provenance import (
    EVIDENCE_MAX_BYTES,
    EVIDENCE_MAX_ROWS_PER_FILE,
    _allowlisted_diagnostics,
    _archive_candidate,
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
        "credential_id": "credential-1",
        "cloud_file_id": "file-1",
        "provider": "canvas",
        "provider_file_id": "page-1",
        "provider_parent_id": "course-1",
        "content_source": "page",
        "content_slug": "page-slug",
        "content_updated_at": "2026-08-25T09:00:00+00:00",
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


def test_oversized_encoded_snapshot_is_rejected_before_base64_allocation(monkeypatch):
    from src.jobs import canvas_content_job as module

    decode = MagicMock(side_effect=AssertionError("decode must not run"))
    monkeypatch.setattr(module.base64, "b64decode", decode)

    with pytest.raises(ValueError, match="invalid_job_payload"):
        _decode_snapshot(
            {
                "version": 1,
                "snapshot": "A" * (MAX_ENCODED_SNAPSHOT_CHARS + 1),
                "snapshot_sha256": "a" * 64,
            }
        )

    decode.assert_not_called()


def test_candidate_fingerprint_binds_every_authority_dimension():
    values = {
        "department_id": "dept-1",
        "credential_id": "credential-1",
        "cloud_file_id": "file-1",
        "provider_file_id": "page-1",
        "provider_parent_id": "course-1",
        "content_source": "page",
        "content_slug": "page-slug",
        "content_updated_at": "2026-08-25T09:00:00+00:00",
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
        credential_id="credential-1",
        provider="canvas",
        provider_file_id="page-1",
        provider_parent_id="course-1",
        content_source="page",
        content_slug="page-slug",
        content_updated_at=datetime(2026, 8, 25, 9, tzinfo=timezone.utc),
        content_body="<p>source</p>",
        remediated_body="<p>candidate</p>",
        last_scan_id="scan-1",
        provider_metadata={},
    )
    publish_canvas_content_candidate(
        cloud_file,
        credential_id=cloud_file.credential_id,
        provider_file_id=cloud_file.provider_file_id,
        provider_parent_id=cloud_file.provider_parent_id,
        content_source=cloud_file.content_source,
        content_slug=cloud_file.content_slug,
        content_updated_at=cloud_file.content_updated_at.isoformat(),
        source_sha256=canvas_content_sha256(cloud_file.content_body),
        scan_id=cloud_file.last_scan_id,
        producer_job_id="job-1",
        snapshot_sha256="b" * 64,
        candidate_sha256=canvas_content_sha256(cloud_file.remediated_body),
    )
    job = SimpleNamespace(
        id="job-1",
        department_id="dept-1",
        job_type="canvas_content",
        cloud_file_id="file-1",
        provider="canvas",
        credential_id="credential-1",
        provider_file_id="page-1",
        status=CloudJobStatus.COMPLETED.value,
        max_retries=0,
        execution_context={
            "version": 1,
            "department_id": "dept-1",
            "credential_id": "credential-1",
            "cloud_file_id": "file-1",
            "provider": "canvas",
            "provider_file_id": "page-1",
            "provider_parent_id": "course-1",
            "content_source": "page",
            "content_slug": "page-slug",
            "content_updated_at": "2026-08-25T09:00:00+00:00",
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


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("credential_id", "credential-2"),
        ("provider_file_id", "page-2"),
        ("provider_parent_id", "course-2"),
        ("content_source", "assignment"),
        ("content_slug", "changed-slug"),
        ("content_updated_at", datetime(2026, 8, 26, 9, tzinfo=timezone.utc)),
    ],
)
def test_current_candidate_rejects_canvas_target_drift(field, replacement):
    db, cloud_file, _job = _current_graph()
    setattr(cloud_file, field, replacement)

    assert canvas_content_candidate_is_current(db, cloud_file) is False


def test_snapshot_validation_rejects_canvas_target_tampering():
    snapshot = _snapshot()
    from src.jobs import canvas_content_job as module

    snapshot["issues_sha256"] = module.hashlib.sha256(
        module._canonical_json(snapshot["issues"])
    ).hexdigest()
    snapshot["options_sha256"] = module.hashlib.sha256(
        module._canonical_json(snapshot["options"])
    ).hexdigest()
    assert _snapshot_is_valid(snapshot) is True

    snapshot["content_source"] = "file"
    assert _snapshot_is_valid(snapshot) is False


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("version", 2),
        ("last_compliance_score", float("nan")),
        ("last_compliance_score", -1.0),
        ("last_compliance_score", 101.0),
    ],
)
def test_snapshot_validation_rejects_unsupported_or_unbounded_values(
    field, replacement
):
    from src.jobs import canvas_content_job as module

    snapshot = _snapshot()
    snapshot["issues_sha256"] = module.hashlib.sha256(
        module._canonical_json(snapshot["issues"])
    ).hexdigest()
    snapshot["options_sha256"] = module.hashlib.sha256(
        module._canonical_json(snapshot["options"])
    ).hexdigest()
    snapshot[field] = replacement

    assert _snapshot_is_valid(snapshot) is False


def test_snapshot_validation_rejects_noncanonical_json_without_raising():
    snapshot = _snapshot()
    snapshot["options"] = {"use_ai": float("nan")}

    assert _snapshot_is_valid(snapshot) is False


def test_active_dedupe_binds_the_complete_immutable_snapshot(monkeypatch):
    from src.jobs import canvas_content_job as module

    cloud_file = SimpleNamespace(
        id="file-1",
        department_id="dept-1",
        credential_id="credential-1",
        provider_file_id="page-1",
        provider_parent_id="course-1",
        content_source="page",
        content_slug="page-slug",
        content_updated_at=datetime(2026, 8, 25, 9, tzinfo=timezone.utc),
        last_scan_id="scan-1",
        content_body="<p>source</p>",
    )
    monkeypatch.setattr(
        module,
        "_scan_evidence",
        lambda _db, _cloud_file: ([{"id": "image-alt"}], 75.0),
    )
    _, _, baseline = _snapshot_material(MagicMock(), cloud_file, {"use_ai": False})

    for field, replacement in (
        ("credential_id", "credential-2"),
        ("provider_file_id", "page-2"),
        ("provider_parent_id", "course-2"),
        ("content_source", "assignment"),
        ("content_slug", "other-slug"),
        ("content_updated_at", datetime(2026, 8, 25, 10, tzinfo=timezone.utc)),
    ):
        original = getattr(cloud_file, field)
        setattr(cloud_file, field, replacement)
        _, _, changed = _snapshot_material(MagicMock(), cloud_file, {"use_ai": False})
        setattr(cloud_file, field, original)
        assert changed != baseline


def test_dependency_must_own_the_exact_canvas_scan_and_options():
    cloud_file = SimpleNamespace(
        id="file-1",
        department_id="dept-1",
        credential_id="credential-1",
        provider_file_id="page-1",
        provider_parent_id="course-1",
        content_source="page",
        last_scan_id="scan-1",
    )
    dependency = SimpleNamespace(
        payload={
            "scan_kind": "canvas_content",
            "cloud_file_id": "file-1",
            "credential_id": "credential-1",
            "provider": "canvas",
            "provider_file_id": "page-1",
            "course_id": "course-1",
            "content_source": "page",
            "scan_options": {"use_ai": False},
        }
    )
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = dependency

    assert (
        _dependency_matches_canvas_content(
            db,
            dependency_id="scan-job-1",
            source_scan_id="scan-1",
            cloud_file=cloud_file,
            options={"use_ai": False},
        )
        is True
    )

    dependency.payload["course_id"] = "course-2"
    assert (
        _dependency_matches_canvas_content(
            db,
            dependency_id="scan-job-1",
            source_scan_id="scan-1",
            cloud_file=cloud_file,
            options={"use_ai": False},
        )
        is False
    )

    dependency.payload["course_id"] = "course-1"
    assert (
        _dependency_matches_canvas_content(
            db,
            dependency_id="scan-job-1",
            source_scan_id="scan-2",
            cloud_file=cloud_file,
            options={"use_ai": False},
        )
        is False
    )


def test_output_limit_is_rechecked_after_html_normalization(monkeypatch):
    from src.education.remediation import html_remediator as html_module
    from src.jobs import canvas_content_job as module

    class FakeRemediator:
        def __init__(self, file_path, *_args, **_kwargs):
            self.file_path = file_path

        def remediate(self):
            return SimpleNamespace(
                success=True,
                output_file=self.file_path,
                fixed_count=0,
                manual_count=1,
                failed_count=0,
                remediated_compliance_score=75.0,
            )

    snapshot = _snapshot()
    monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", 1024)
    monkeypatch.setattr(module, "_sanitize_html", lambda _body: "x" * 1025)
    monkeypatch.setattr(html_module, "HtmlRemediator", FakeRemediator)

    with pytest.raises(ValueError, match="canvas_content_invalid_output"):
        module._remediate_snapshot(snapshot, "job-1")


@pytest.mark.parametrize(
    "result_values",
    [
        {"fixed_count": True},
        {"manual_count": 1.5},
        {"failed_count": "0"},
        {"remediated_compliance_score": float("inf")},
        {"remediated_compliance_score": 101.0},
    ],
)
def test_remediator_result_scalars_are_exact_and_bounded(monkeypatch, result_values):
    from src.education.remediation import html_remediator as html_module
    from src.jobs import canvas_content_job as module

    class FakeRemediator:
        def __init__(self, file_path, *_args, **_kwargs):
            self.file_path = file_path

        def remediate(self):
            values = {
                "success": True,
                "output_file": self.file_path,
                "fixed_count": 0,
                "manual_count": 1,
                "failed_count": 0,
                "remediated_compliance_score": 75.0,
                **result_values,
            }
            return SimpleNamespace(**values)

    monkeypatch.setattr(html_module, "HtmlRemediator", FakeRemediator)

    with pytest.raises(ValueError, match="canvas_content_invalid_output"):
        module._remediate_snapshot(_snapshot(), "job-1")


def test_public_job_shape_allowlists_scalar_result_fields():
    from src.api.canvas_content_routes import _content_remediation_job_shape

    job = SimpleNamespace(
        id="job-1",
        status="completed",
        progress=500,
        created_at=None,
        updated_at=None,
        started_at=None,
        completed_at=None,
        last_error_code="unsafe-provider-exception",
        result_data={
            "fixed_count": "unsafe-provider-output",
            "manual_count": -1,
            "failed_count": 1,
            "remediated_compliance_score": float("inf"),
            "verified": "yes",
            "issues_remaining": 999999,
            "provider_payload": {"secret": "must not escape"},
        },
    )

    public = _content_remediation_job_shape(job, "file-1")

    assert public.progress == 100
    assert public.error_code is None
    assert public.fixed_count is None
    assert public.manual_count is None
    assert public.failed_count == 1
    assert public.remediated_score is None
    assert public.verified is None
    assert public.issues_remaining is None
    assert "provider_payload" not in public.model_dump()


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
            "unrelated": "unsafe-diagnostic-value",
        }
    )
    encoded = str(diagnostics).encode()

    assert b"secret" not in encoded
    assert b"unsafe" not in encoded
    assert len(encoded) <= EVIDENCE_MAX_BYTES
    assert EVIDENCE_MAX_ROWS_PER_FILE == 20


class _EvidenceDb:
    def __init__(self):
        self.rows = {}

    def get(self, _model, evidence_id):
        return self.rows.get(evidence_id)

    def add(self, row):
        self.rows[row.id] = row

    def flush(self):
        return None

    def scalars(self, _query):
        return []


def _evidence_metadata(
    *,
    source_sha256="a" * 64,
    scan_id="scan-1",
    producer_job_id="job-1",
    snapshot_sha256="d" * 64,
):
    return {
        "canvas_content_remediation": {
            "job_id": producer_job_id,
            "scan_id": scan_id,
            "status": "completed",
            "source_sha256": source_sha256,
        },
        "canvas_content_candidate": {
            "fingerprint": "b" * 64,
            "source_sha256": source_sha256,
            "candidate_sha256": "c" * 64,
            "snapshot_sha256": snapshot_sha256,
            "scan_id": scan_id,
            "producer_job_id": producer_job_id,
        },
    }


def test_exact_duplicate_canvas_evidence_archival_is_idempotent():
    db = _EvidenceDb()
    cloud_file = SimpleNamespace(id="file-1", department_id="dept-1")
    metadata = _evidence_metadata()

    _archive_candidate(db, cloud_file, metadata, "source_changed")
    evidence_id = next(iter(db.rows))
    _archive_candidate(db, cloud_file, metadata, "source_changed")

    assert list(db.rows) == [evidence_id]


@pytest.mark.parametrize(
    ("dimension", "replacement"),
    [
        ("source_sha256", "e" * 64),
        ("scan_id", "scan-2"),
        ("producer_job_id", "job-2"),
        ("department_id", "dept-2"),
        ("snapshot_sha256", "f" * 64),
    ],
)
def test_canvas_evidence_identity_binds_persisted_provenance(dimension, replacement):
    db = _EvidenceDb()
    cloud_file = SimpleNamespace(id="file-1", department_id="dept-1")
    _archive_candidate(db, cloud_file, _evidence_metadata(), "source_changed")

    metadata_changes = {}
    if dimension == "department_id":
        cloud_file.department_id = replacement
    else:
        metadata_changes[dimension] = replacement
    _archive_candidate(
        db,
        cloud_file,
        _evidence_metadata(**metadata_changes),
        "source_changed",
    )

    assert len(db.rows) == 2


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

        invalid_hash_savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO canvas_content_remediation_evidence "
                    "(id, department_id, cloud_file_id, source_sha256, "
                    "candidate_sha256, quarantine_reason, diagnostics, "
                    "stored_bytes, lifecycle_state, expires_at) VALUES "
                    "('bad-hash', 'dept-1', 'file-1', :source, :candidate, "
                    "'reason', '{}', 2, 'current', CURRENT_TIMESTAMP)"
                ),
                {"source": "z" * 64, "candidate": "b" * 64},
            )
        invalid_hash_savepoint.rollback()

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
