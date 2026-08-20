"""Durable remediation outcomes and atomic generic route persistence."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect

from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import (
    CloudFile,
    CloudProvider,
    RemediationOutcome,
    Scan,
    ScanFix,
    ScanResult,
    ScanStatus,
    ScanType,
    UserRole,
)

ROOT = Path(__file__).resolve().parents[1]


def _load_migration():
    path = ROOT / "alembic/versions/2026_08_20_cloud_job_execution_context.py"
    spec = importlib.util.spec_from_file_location("job_execution_context", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scan_remediation_outcome_is_real_nullable_string_mapper_column():
    mapped = inspect(Scan).columns.remediation_outcome

    assert mapped.type.length == 32
    assert mapped.nullable is True
    assert mapped.index is None
    assert set(RemediationOutcome) == {
        RemediationOutcome.COMPLETED,
        RemediationOutcome.NO_OP,
        RemediationOutcome.MANUAL_REQUIRED,
        RemediationOutcome.ARTIFACT_UNAVAILABLE,
        RemediationOutcome.REMEDIATION_FAILED,
    }


def test_current_migration_adds_and_reverses_nullable_outcome_column(monkeypatch):
    migration = _load_migration()
    added = []
    dropped = []
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: added.append((table, column)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda table, column: dropped.append((table, column)),
    )

    migration.upgrade()
    migration.downgrade()

    assert migration.revision == "20260820_job_exec_context"
    assert len(migration.revision) <= 32
    assert [(table, column.name) for table, column in added] == [
        ("cloud_job_queue", "execution_context"),
        ("scans", "remediation_outcome"),
    ]
    outcome = added[1][1]
    assert outcome.type.length == 32
    assert outcome.nullable is True
    assert outcome.server_default is None
    assert dropped == [
        ("scans", "remediation_outcome"),
        ("cloud_job_queue", "execution_context"),
    ]


def test_postgresql_upgrade_exposes_outcome_to_reloaded_mapper():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "Set TEST_DATABASE_URL for PostgreSQL migration reload verification"
        )
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.skip("Remediation migration verification requires PostgreSQL")

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with Session(engine) as session:
        session.execute(select(Scan.remediation_outcome).limit(0))


def test_queued_worker_suppresses_success_notification_until_transactional_outbox():
    from src.jobs.remediation_job import process_remediation_job

    assert (
        "_send_remediation_notification"
        not in process_remediation_job.__code__.co_names
    )


class _CloudQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.limit_value = None

    def filter(self, *criteria):
        for criterion in criteria:
            key = criterion.left.key
            expected = criterion.right.value
            self.rows = [row for row in self.rows if getattr(row, key) == expected]
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        return self.rows[: self.limit_value]

    def first(self):
        return self.rows[0] if self.rows else None


class _TransactionDB:
    def __init__(self, cloud_file, *, fail_commit=False):
        self.cloud_file = cloud_file
        self.pending = []
        self.persisted = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = fail_commit

    def query(self, model):
        assert model is CloudFile
        return _CloudQuery([self.cloud_file] if self.cloud_file else [])

    def add(self, value):
        self.pending.append(value)

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.persisted.extend(self.pending)
        self.pending.clear()

    def rollback(self):
        self.rollbacks += 1
        self.pending.clear()


def _principal():
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method="session",
    )


def _cloud_file():
    return CloudFile(
        id="cloud-1",
        department_id="dept-1",
        credential_id="cred-1",
        provider=CloudProvider.CANVAS.value,
        provider_file_id="provider-1",
        provider_parent_id="course-1",
        file_name="file.docx",
        file_type="docx",
        last_scan_id="scan-1",
        has_remediated_version=False,
    )


def _scan(path, scan_type=ScanType.WORD):
    return SimpleNamespace(
        id="scan-1",
        department_id="dept-1",
        scan_type=scan_type,
        storage_path=str(path),
        file_name=path.name,
        status=ScanStatus.PROCESSING,
        remediation_outcome=None,
        result=SimpleNamespace(issues=[{"description": "heading"}]),
    )


_MISSING = object()


def _result(
    path,
    *,
    fixed_count=1,
    manual_count=0,
    failed_count=0,
    verification_passed=True,
):
    fixed = SimpleNamespace(
        issue_id="issue-1",
        category=SimpleNamespace(value="heading"),
        severity=SimpleNamespace(value="high"),
        description="Fixed heading",
        location=None,
        original_content=None,
        fixed_content=None,
        fix_method="mechanical",
        model_used=None,
        confidence=1.0,
        needs_review=False,
        wcag_criteria=None,
        page_number=None,
    )
    result = SimpleNamespace(
        success=True,
        original_file=str(path),
        output_file=str(path.with_name("fixed.docx")),
        total_issues=1,
        fixed_count=fixed_count,
        manual_count=manual_count,
        failed_count=failed_count,
        original_compliance_score=50.0,
        remediated_compliance_score=100.0,
        improvement=50.0,
        duration_seconds=0.01,
        fixed_issues=[fixed] if fixed_count else [],
        manual_issues=[],
        warnings=[],
    )
    if verification_passed is not _MISSING:
        result.verification_passed = verification_passed
    return result


async def _run_document_route(path, scan, db, audit_effect, *, result=None):
    from src.api.education.remediation_routes import remediate_scan

    remediator = MagicMock()
    remediator.remediate.return_value = result or _result(path)
    audit = MagicMock()
    audit.log_remediation_complete.side_effect = audit_effect
    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch("src.education.remediation.DocxRemediator", return_value=remediator),
        patch(
            "src.api.education.remediation_routes.get_provider_manager",
            return_value=object(),
        ),
        patch("src.security.audit_service.AuditService", return_value=audit),
    ):
        return await remediate_scan(
            "scan-1", MagicMock(), db=db, principal=_principal()
        )


@pytest.mark.asyncio
async def test_generic_audit_failure_rolls_back_fixes_and_restores_scan(tmp_path):
    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _scan(path)
    cloud_file = _cloud_file()
    db = _TransactionDB(cloud_file)

    with pytest.raises(HTTPException) as caught:
        await _run_document_route(
            path,
            scan,
            db,
            MagicMock(
                side_effect=HTTPException(status_code=418, detail="audit failed")
            ),
        )

    assert caught.value.status_code == 500
    assert caught.value.detail == "Remediation failed. Please try again."
    assert db.commits == 0
    assert db.rollbacks == 1
    assert db.pending == []
    assert db.persisted == []
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None
    assert cloud_file.has_remediated_version is False


@pytest.mark.asyncio
async def test_generic_cloud_mutation_failure_rolls_back_everything(tmp_path):
    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    path.with_name("fixed.docx").write_bytes(b"remediated document")
    scan = _scan(path)

    class FailingCloudFile:
        id = "cloud-1"
        department_id = "dept-1"
        credential_id = "cred-1"
        provider = CloudProvider.CANVAS.value
        provider_file_id = "provider-1"
        provider_parent_id = "course-1"
        file_name = "file.docx"
        file_type = "docx"
        last_scan_id = "scan-1"
        has_remediated_version = False

        def __setattr__(self, name, value):
            if name == "has_remediated_version" and value is True:
                raise RuntimeError("cloud mutation failed")
            super().__setattr__(name, value)

    cloud_file = FailingCloudFile()
    db = _TransactionDB(cloud_file)

    def add_audit(**kwargs):
        db.add(SimpleNamespace(kind="audit"))

    with pytest.raises(HTTPException) as caught:
        await _run_document_route(path, scan, db, add_audit)

    assert caught.value.status_code == 500
    assert db.commits == 0
    assert db.rollbacks == 1
    assert db.persisted == []
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None
    assert cloud_file.has_remediated_version is False


@pytest.mark.asyncio
async def test_generic_success_commits_fixes_audit_status_and_cloud_once(tmp_path):
    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    path.with_name("fixed.docx").write_bytes(b"remediated document")
    scan = _scan(path)
    cloud_file = _cloud_file()
    db = _TransactionDB(cloud_file)

    def add_audit(**kwargs):
        assert kwargs["commit"] is False
        db.add(SimpleNamespace(kind="audit"))

    result = await _run_document_route(path, scan, db, add_audit)

    assert result["success"] is True
    assert db.commits == 1
    assert db.rollbacks == 0
    assert len([row for row in db.persisted if isinstance(row, ScanFix)]) == 1
    assert (
        len([row for row in db.persisted if getattr(row, "kind", None) == "audit"]) == 1
    )
    assert scan.status == ScanStatus.COMPLETED
    assert scan.remediation_outcome == RemediationOutcome.COMPLETED.value
    assert cloud_file.has_remediated_version is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixed_count", "manual_count", "failed_count", "prior", "expected"),
    [
        pytest.param(0, 0, 1, False, False, id="failed"),
        pytest.param(0, 1, 0, False, False, id="manual"),
        pytest.param(0, 0, 0, False, False, id="no-op-new-row"),
        pytest.param(0, 0, 0, True, True, id="no-op-preserves-prior-artifact"),
        pytest.param(1, 0, 0, False, False, id="fixed-without-durable-artifact"),
    ],
)
async def test_generic_artifact_flag_reflects_terminal_outcome_and_durable_output(
    tmp_path, fixed_count, manual_count, failed_count, prior, expected
):
    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _scan(path)
    cloud_file = _cloud_file()
    cloud_file.has_remediated_version = prior
    db = _TransactionDB(cloud_file)

    await _run_document_route(
        path,
        scan,
        db,
        lambda **kwargs: None,
        result=_result(
            path,
            fixed_count=fixed_count,
            manual_count=manual_count,
            failed_count=failed_count,
        ),
    )

    assert cloud_file.has_remediated_version is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verification_passed", "prior", "expected"),
    [
        pytest.param(False, False, False, id="verification-false"),
        pytest.param(None, False, False, id="verification-none"),
        pytest.param(_MISSING, False, False, id="verification-missing"),
        pytest.param(True, False, True, id="verification-true"),
        pytest.param(False, True, True, id="failed-verification-preserves-prior"),
    ],
)
async def test_generic_artifact_promotion_requires_explicit_successful_verification(
    tmp_path, verification_passed, prior, expected
):
    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    path.with_name("fixed.docx").write_bytes(b"remediated document")
    scan = _scan(path)
    cloud_file = _cloud_file()
    cloud_file.has_remediated_version = prior
    db = _TransactionDB(cloud_file)

    await _run_document_route(
        path,
        scan,
        db,
        lambda **kwargs: None,
        result=_result(path, fixed_count=1, verification_passed=verification_passed),
    )

    assert scan.status == ScanStatus.COMPLETED
    assert cloud_file.has_remediated_version is expected


@pytest.mark.asyncio
async def test_generic_zero_issue_scan_persists_honest_noop_without_artifact():
    from src.api.education.remediation_routes import remediate_scan

    scan = Scan(
        id="scan-zero",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        status=ScanStatus.PROCESSING,
        file_name="empty.docx",
    )
    scan.result = ScanResult(id="result-zero", scan_id=scan.id, issues=[])
    cloud_file = _cloud_file()
    cloud_file.last_scan_id = scan.id
    db = _TransactionDB(cloud_file)

    with patch(
        "src.api.education.remediation_routes.ScanService.get_scan_with_result",
        return_value=scan,
    ):
        result = await remediate_scan(
            scan.id, MagicMock(), db=db, principal=_principal()
        )

    assert result == {
        "success": True,
        "message": "No issues to remediate",
        "fixed_count": 0,
        "manual_count": 0,
        "failed_count": 0,
        "artifact_required": False,
    }
    assert db.commits == 1
    assert scan.status == ScanStatus.COMPLETED
    assert scan.remediation_outcome == RemediationOutcome.NO_OP.value
    assert cloud_file.has_remediated_version is False


@pytest.mark.asyncio
async def test_generic_zero_issue_commit_failure_rolls_back_and_restores_scan():
    from src.api.education.remediation_routes import remediate_scan

    scan = Scan(
        id="scan-zero-fail",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        status=ScanStatus.PROCESSING,
        file_name="empty.docx",
    )
    scan.result = ScanResult(id="result-zero-fail", scan_id=scan.id, issues=[])
    db = _TransactionDB(None, fail_commit=True)

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(scan.id, MagicMock(), db=db, principal=_principal())

    assert caught.value.status_code == 500
    assert db.rollbacks == 1
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None


@pytest.mark.asyncio
async def test_image_commit_failure_rolls_back_and_restores_outcome(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "image.png"
    path.write_bytes(b"image")
    scan = _scan(path, scan_type=ScanType.IMAGE)
    cloud_file = _cloud_file()
    db = _TransactionDB(cloud_file, fail_commit=True)
    generator = MagicMock()
    generator.analyze_image_comprehensive = AsyncMock(
        return_value={
            "description": {"alt_text": "A chart"},
            "type_detection": {"is_decorative": False},
        }
    )
    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            return_value=object(),
        ),
        patch(
            "src.api.education.remediation_routes.ImageAltTextGenerator",
            return_value=generator,
        ),
    ):
        with pytest.raises(HTTPException) as caught:
            await remediate_scan(
                "scan-1",
                MagicMock(),
                use_ai=True,
                db=db,
                principal=_principal(),
            )

    assert caught.value.status_code == 500
    assert caught.value.detail == "Remediation failed. Please try again."
    assert db.commits == 1
    assert db.rollbacks == 1
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None
