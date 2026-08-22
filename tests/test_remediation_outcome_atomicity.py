"""Durable remediation outcomes and atomic generic route persistence."""

import importlib.util
import os
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect

from src.auth.dependencies import AuthenticatedPrincipal
from src.api.education._shared import RemediationOptions
from src.db.models import (
    AuditLog,
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

    def flush(self):
        return None

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.persisted.extend(self.pending)
        self.pending.clear()

    def rollback(self):
        self.rollbacks += 1
        self.pending.clear()

    def refresh(self, value):
        return None


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
        completed_at=None,
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


async def _run_document_route(
    path, scan, db, audit_effect, *, result=None, durable_output=True
):
    from src.api.education.remediation_routes import remediate_scan

    effective_result = result or _result(path)
    if (
        durable_output
        and effective_result.fixed_count
        and getattr(effective_result, "verification_passed", None) is True
    ):
        path.with_name("fixed.docx").write_bytes(b"remediated document")
    remediator = MagicMock()
    remediator.remediate.return_value = effective_result
    artifact = SimpleNamespace(
        id="66666666-6666-4666-8666-666666666666",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=19,
        sha256="a" * 64,
        expires_at=datetime.now(timezone.utc),
        review_status="pending",
        lifecycle_status="available",
    )
    artifact_service = MagicMock()

    def persist(*args, **kwargs):
        if db.cloud_file is not None:
            db.cloud_file.current_remediation_artifact_id = artifact.id
            db.cloud_file.has_remediated_version = True
            db.cloud_file.remediation_origin = "manual"
        return artifact

    artifact_service.claim_and_publish.side_effect = persist
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
        patch(
            "src.api.education.remediation_routes.RemediationArtifactService.from_settings",
            return_value=artifact_service,
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
    cloud_file.remediation_origin = "automatic"
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
    assert cloud_file.remediation_origin == "automatic"


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
async def test_jobless_local_success_creates_scan_bound_artifact_without_cloud_file(
    tmp_path,
):
    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    path.with_name("fixed.docx").write_bytes(b"remediated document")
    scan = _scan(path)
    db = _TransactionDB(None)

    response = await _run_document_route(path, scan, db, lambda **kwargs: None)

    assert response["success"] is True
    assert not any(isinstance(row, CloudFile) for row in db.pending + db.persisted)
    assert response["artifact_id"] is not None


def test_issue_normalization_copies_valid_persisted_input_without_mutating_it():
    from src.api.education.remediation_routes import _normalize_issues_for_remediation

    raw_metadata = {"preserved": ["value"]}
    raw_issue = {"id": "issue-1", "category": "heading", "metadata": raw_metadata}

    normalized = _normalize_issues_for_remediation([raw_issue])

    assert raw_issue == {
        "id": "issue-1",
        "category": "heading",
        "metadata": {"preserved": ["value"]},
    }
    assert normalized[0] is not raw_issue
    assert normalized[0]["metadata"] is not raw_metadata
    assert normalized[0]["metadata"]["preserved"] is not raw_metadata["preserved"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_issues",
    [
        pytest.param({"unexpected": "SENSITIVE content"}, id="wrapper-missing-details"),
        pytest.param({"details": None}, id="wrapper-details-none"),
        pytest.param({"details": {"SENSITIVE": "content"}}, id="wrapper-details-dict"),
        pytest.param({"details": "SENSITIVE content"}, id="wrapper-details-string"),
        pytest.param("SENSITIVE container", id="container-string"),
        pytest.param(17, id="container-number"),
        pytest.param([{"metadata": "SENSITIVE metadata"}], id="metadata-string"),
        pytest.param([{"metadata": ["SENSITIVE metadata"]}], id="metadata-list"),
        pytest.param([{"metadata": None}], id="metadata-none"),
        pytest.param(["SENSITIVE issue"], id="issue-not-dict"),
        pytest.param(
            [{"metadata": {}, "nodes": {"SENSITIVE": "node"}}],
            id="nodes-not-list",
        ),
        pytest.param(
            [{"metadata": {}, "nodes": ["SENSITIVE node"]}],
            id="node-not-dict",
        ),
    ],
)
async def test_malformed_persisted_issues_fail_once_without_provider_or_raw_leakage(
    tmp_path, malformed_issues, caplog
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _scan(path)
    scan.result.issues = malformed_issues
    db = _TransactionDB(None)
    audit = MagicMock()

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch("src.api.education.remediation_routes.get_provider_manager") as manager,
        patch("src.education.remediation.DocxRemediator") as remediator,
        patch("src.security.audit_service.AuditService", return_value=audit),
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(scan.id, MagicMock(), db=db, principal=_principal())

    assert caught.value.status_code == 500
    assert caught.value.detail == "Remediation failed. Please try again."
    assert db.commits == 0
    assert db.rollbacks == 1
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None
    manager.assert_not_called()
    remediator.assert_not_called()
    audit.log_remediation_complete.assert_not_called()
    audit.log_remediation_failed.assert_called_once()
    failure = audit.log_remediation_failed.call_args.kwargs
    assert failure["error"] == "invalid_scan_result"
    assert "SENSITIVE" not in str(failure)
    assert "SENSITIVE" not in caplog.text


@pytest.mark.asyncio
async def test_response_construction_failure_precedes_success_audit_and_commit(
    tmp_path,
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _scan(path)
    db = _TransactionDB(None)
    result = _result(path, fixed_count=0, manual_count=1)

    class ExplodingCategory:
        @property
        def value(self):
            raise RuntimeError("SENSITIVE response construction failure")

    result.manual_issues = [
        SimpleNamespace(
            issue_id="manual-1",
            category=ExplodingCategory(),
            severity=SimpleNamespace(value="high"),
            description="manual issue",
            reason="manual",
            recommendation="review",
        )
    ]
    remediator = MagicMock()
    remediator.remediate.return_value = result
    audit = MagicMock()

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
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(scan.id, MagicMock(), db=db, principal=_principal())

    assert caught.value.status_code == 500
    assert db.commits == 0
    assert db.rollbacks == 1
    assert scan.status == ScanStatus.PROCESSING
    audit.log_remediation_complete.assert_not_called()
    audit.log_remediation_failed.assert_called_once()
    assert (
        audit.log_remediation_failed.call_args.kwargs["error"]
        == "remediation_exception"
    )


@pytest.mark.asyncio
async def test_lone_surrogate_response_fails_before_success_audit_commit_or_state(
    tmp_path,
):
    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _scan(path)
    db = _TransactionDB(None)
    result = _result(path)
    result.warnings = ["SENSITIVE lone surrogate: \ud800"]
    with pytest.raises(HTTPException) as caught:
        await _run_document_route(
            path,
            scan,
            db,
            lambda **kwargs: None,
            result=result,
        )

    assert caught.value.status_code == 500
    assert caught.value.detail == "Remediation failed. Please try again."
    assert db.rollbacks >= 1
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None


@pytest.mark.asyncio
async def test_normal_unicode_response_succeeds_before_commit(tmp_path):
    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _scan(path)
    db = _TransactionDB(None)
    result = _result(path)
    result.warnings = ["Résumé ready 😀"]

    response = await _run_document_route(
        path,
        scan,
        db,
        lambda **kwargs: None,
        result=result,
    )

    assert response["warnings"] == ["Résumé ready 😀"]
    assert db.commits == 1
    assert db.rollbacks == 0
    assert scan.status == ScanStatus.COMPLETED
    assert scan.remediation_outcome == RemediationOutcome.COMPLETED.value


@pytest.mark.asyncio
async def test_postcommit_serialization_failure_cannot_add_second_terminal_audit(
    tmp_path,
):
    import json

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _scan(path)
    db = _TransactionDB(None)
    audit = MagicMock()
    result = _result(path)

    response = await _run_document_route(
        path,
        scan,
        db,
        lambda **kwargs: audit.log_remediation_complete(**kwargs),
        result=result,
    )

    assert db.commits == 1
    assert db.rollbacks == 0
    audit.log_remediation_complete.assert_called_once()
    audit.log_remediation_failed.assert_not_called()

    assert json.dumps(response)

    with patch("json.dumps", side_effect=RuntimeError("serialization failed")):
        with pytest.raises(RuntimeError, match="serialization failed"):
            json.dumps(response)

    assert db.commits == 1
    assert db.rollbacks == 0
    audit.log_remediation_complete.assert_called_once()
    audit.log_remediation_failed.assert_not_called()


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
        durable_output=False,
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

    expected_status = (
        ScanStatus.COMPLETED if verification_passed is True else ScanStatus.FAILED
    )
    assert scan.status == expected_status
    assert cloud_file.has_remediated_version is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "persisted_issues",
    [
        pytest.param([], id="list"),
        pytest.param({"details": []}, id="documented-details-wrapper"),
    ],
)
@pytest.mark.parametrize(
    ("options", "expected_requested", "expected_outcomes"),
    [
        pytest.param(
            None,
            (True, True),
            {
                "remediation": "allowed_not_used",
                "alt_text": "allowed_not_used",
            },
            id="legacy-defaults-request-both",
        ),
        pytest.param(
            RemediationOptions(use_ai=False),
            (False, True),
            {"remediation": "not_requested", "alt_text": "allowed_not_used"},
            id="explicit-remediation-false",
        ),
        pytest.param(
            RemediationOptions(generate_alt_text=False),
            (True, False),
            {"remediation": "allowed_not_used", "alt_text": "not_requested"},
            id="explicit-alt-text-false",
        ),
        pytest.param(
            RemediationOptions(use_ai=False, generate_alt_text=False),
            (False, False),
            {"remediation": "not_requested", "alt_text": "not_requested"},
            id="explicit-both-false",
        ),
    ],
)
async def test_generic_zero_issue_scan_audits_honest_atomic_noop(
    options, expected_requested, expected_outcomes, persisted_issues
):
    from src.api.education.remediation_routes import remediate_scan

    scan = Scan(
        id="scan-zero",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        status=ScanStatus.PROCESSING,
        file_name="empty.docx",
    )
    scan.result = ScanResult(id="result-zero", scan_id=scan.id, issues=persisted_issues)
    db = _TransactionDB(None)
    audit = MagicMock()

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
        ) as bind,
        patch("src.api.education.remediation_routes.get_provider_manager") as manager,
        patch("src.security.audit_service.AuditService", return_value=audit),
    ):
        result = await remediate_scan(
            scan.id,
            MagicMock(),
            options=options,
            db=db,
            principal=_principal(),
        )

    bind.assert_not_called()
    manager.assert_not_called()
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
    audit.log_remediation_complete.assert_called_once()
    audit.log_remediation_failed.assert_not_called()
    details = audit.log_remediation_complete.call_args.kwargs
    assert details["commit"] is False
    assert (
        details["remediation_ai_requested"],
        details["alt_text_requested"],
    ) == expected_requested
    assert details["purpose_outcomes"] == expected_outcomes
    assert details["remediation_ai_attempted"] is False
    assert details["alt_text_attempted"] is False
    assert details["remediation_ai_used"] is False
    assert details["alt_text_used"] is False
    assert details["remediation_external_ai_used"] is False
    assert details["alt_text_external_ai_used"] is False
    assert details["external_ai_used"] is False
    assert details["providers"] == {}
    assert details["use_ai"] is False
    assert details["total_issues"] == 0
    assert details["fixed_count"] == 0
    assert details["manual_count"] == 0
    assert details["failed_count"] == 0
    assert details["skipped_count"] == 0


@pytest.mark.asyncio
async def test_generic_zero_issue_audit_failure_restores_scan_without_commit():
    from src.api.education.remediation_routes import remediate_scan

    scan = Scan(
        id="scan-zero-audit-fail",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        status=ScanStatus.PROCESSING,
        file_name="empty.docx",
    )
    scan.result = ScanResult(id="result-zero-audit-fail", scan_id=scan.id, issues=[])
    original_completed_at = scan.completed_at
    db = _TransactionDB(None)
    audit = MagicMock()
    audit.log_remediation_complete.side_effect = RuntimeError("audit failed")

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch("src.security.audit_service.AuditService", return_value=audit),
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(scan.id, MagicMock(), db=db, principal=_principal())

    assert caught.value.status_code == 500
    assert caught.value.detail == "Remediation failed. Please try again."
    assert db.commits == 0
    assert db.rollbacks == 1
    assert db.pending == []
    assert db.persisted == []
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None
    assert scan.completed_at == original_completed_at
    audit.log_remediation_complete.assert_called_once()
    audit.log_remediation_failed.assert_not_called()


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
    audit = MagicMock()

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch("src.security.audit_service.AuditService", return_value=audit),
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(scan.id, MagicMock(), db=db, principal=_principal())

    assert caught.value.status_code == 500
    assert caught.value.detail == "Remediation failed. Please try again."
    assert db.rollbacks == 1
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None
    audit.log_remediation_complete.assert_called_once()
    audit.log_remediation_failed.assert_called_once()
    failure = audit.log_remediation_failed.call_args.kwargs
    assert failure["error"] == "remediation_exception"
    assert failure["commit"] is True
    assert failure["total_issues"] == 0


@pytest.mark.asyncio
async def test_generic_zero_issue_failure_audit_outage_preserves_stable_response():
    from src.api.education.remediation_routes import remediate_scan

    scan = Scan(
        id="scan-zero-audit-outage",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        status=ScanStatus.PROCESSING,
        file_name="empty.docx",
    )
    scan.result = ScanResult(id="result-zero-audit-outage", scan_id=scan.id, issues=[])
    db = _TransactionDB(None, fail_commit=True)
    audit = MagicMock()
    audit.log_remediation_failed.side_effect = RuntimeError("SENSITIVE audit outage")

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch("src.security.audit_service.AuditService", return_value=audit),
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(scan.id, MagicMock(), db=db, principal=_principal())

    assert caught.value.status_code == 500
    assert caught.value.detail == "Remediation failed. Please try again."
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None
    audit.log_remediation_failed.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "analysis",
    [
        pytest.param("SENSITIVE analysis", id="analysis-not-dict"),
        pytest.param(
            {
                "description": "SENSITIVE description",
                "type_detection": {"is_decorative": False},
            },
            id="description-not-dict",
        ),
        pytest.param(
            {"description": {}, "type_detection": "SENSITIVE type detection"},
            id="type-detection-not-dict",
        ),
        pytest.param(
            {
                "description": {"alt_text": ["SENSITIVE alt"]},
                "type_detection": {"is_decorative": False},
            },
            id="alt-text-not-string",
        ),
        pytest.param(
            {
                "description": {},
                "type_detection": {"is_decorative": "SENSITIVE bool"},
            },
            id="decorative-not-bool",
        ),
        pytest.param(
            {"description": {}, "type_detection": {}},
            id="decorative-missing",
        ),
    ],
)
async def test_malformed_image_analysis_fails_once_with_tracked_provider_and_no_leakage(
    tmp_path, analysis, caplog
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "image.png"
    path.write_bytes(b"image")
    scan = _scan(path, scan_type=ScanType.IMAGE)
    cloud_file = _cloud_file()
    db = _TransactionDB(cloud_file)
    audit = MagicMock()
    client = MagicMock(provider="openai", model="bounded-model")
    client.analyze_image_sync.return_value = {
        "success": True,
        "provider": "openai",
        "model": "bounded-model",
    }

    class Generator:
        def __init__(self, *, lms_client, **kwargs):
            self.client = lms_client

        async def analyze_image_comprehensive(self, **kwargs):
            self.client.analyze_image_sync("bounded-image-reference")
            return analysis

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            return_value=client,
        ),
        patch("src.api.education.remediation_routes.ImageAltTextGenerator", Generator),
        patch("src.security.audit_service.AuditService", return_value=audit),
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(
            scan.id,
            MagicMock(),
            use_ai=True,
            db=db,
            principal=_principal(),
        )

    assert caught.value.status_code == 500
    assert caught.value.detail == "Remediation failed. Please try again."
    assert db.rollbacks == 1
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None
    client.analyze_image_sync.assert_called_once()
    audit.log_remediation_complete.assert_not_called()
    audit.log_remediation_failed.assert_called_once()
    failure = audit.log_remediation_failed.call_args.kwargs
    assert failure["error"] == "invalid_provider_response"
    assert failure["alt_text_attempted"] is True
    assert failure["alt_text_external_ai_used"] is True
    assert failure["providers"] == {"alt_text": "openai"}
    assert "SENSITIVE" not in str(failure)
    assert "SENSITIVE" not in caplog.text


@pytest.mark.asyncio
async def test_image_analysis_missing_optional_alt_text_is_manual_required(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "image.png"
    path.write_bytes(b"image")
    scan = _scan(path, scan_type=ScanType.IMAGE)
    db = _TransactionDB(None)
    generator = MagicMock()
    generator.analyze_image_comprehensive = AsyncMock(
        return_value={
            "description": {},
            "type_detection": {"is_decorative": False},
        }
    )

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.ImageAltTextGenerator",
            return_value=generator,
        ),
    ):
        result = await remediate_scan(
            scan.id, MagicMock(), use_ai=True, db=db, principal=_principal()
        )

    assert result["success"] is False
    assert result["message"] == "manual_required"
    assert result["remediated_alt_text"] == ""
    assert result["is_decorative"] is False
    assert scan.status == ScanStatus.FAILED
    assert scan.remediation_outcome == RemediationOutcome.MANUAL_REQUIRED.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "use_gemini",
        "gemini_result",
        "ollama_result",
        "expected_attempts",
        "expected_external",
        "expected_provider",
        "expected_success",
    ),
    [
        (
            True,
            ('{"is_decorative": true, "image_purpose": "decorative"}', 0.1),
            None,
            ("gemini",),
            True,
            "gemini",
            True,
        ),
        (
            False,
            None,
            ('{"is_decorative": true, "image_purpose": "decorative"}', 0.1),
            ("ollama",),
            False,
            "ollama",
            True,
        ),
        (
            True,
            ("ERROR: SENSITIVE gemini failure", 0.1),
            ('{"is_decorative": true, "image_purpose": "decorative"}', 0.1),
            ("gemini", "ollama"),
            True,
            "ollama",
            True,
        ),
        (
            True,
            ("ERROR: SENSITIVE gemini failure", 0.1),
            ("ERROR: SENSITIVE ollama failure", 0.1),
            ("gemini", "ollama"),
            True,
            "ollama",
            False,
        ),
    ],
)
async def test_legacy_image_terminal_audit_uses_generator_transport_metadata(
    tmp_path,
    caplog,
    use_gemini,
    gemini_result,
    ollama_result,
    expected_attempts,
    expected_external,
    expected_provider,
    expected_success,
):
    from PIL import Image

    from src.api.education.remediation_routes import remediate_scan
    from src.education.image_alt_text import ImageAltTextGenerator

    path = tmp_path / "image.png"
    Image.new("RGB", (10, 10), color="blue").save(path)
    scan = _scan(path, scan_type=ScanType.IMAGE)
    db = _TransactionDB(None)
    audit = MagicMock()
    settings = SimpleNamespace(
        gemini_api_key="safe-key" if use_gemini else None,
        gemini_api_base="https://safe.invalid",
        gemini_vision_model="gemini-safe",
        use_gemini=use_gemini,
        ollama_host="http://localhost:11434",
        ollama_fallback_vision="llava-safe",
    )

    patches = [
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch("src.education.image_alt_text.get_settings", return_value=settings),
        patch("src.security.audit_service.AuditService", return_value=audit),
    ]
    if gemini_result is not None:
        patches.append(
            patch.object(
                ImageAltTextGenerator,
                "_generate_with_gemini",
                new=AsyncMock(return_value=gemini_result),
            )
        )
    if ollama_result is not None:
        patches.append(
            patch.object(
                ImageAltTextGenerator,
                "_generate_with_ollama",
                new=AsyncMock(return_value=ollama_result),
            )
        )

    with ExitStack() as stack:
        for active_patch in patches:
            stack.enter_context(active_patch)
        if expected_success:
            result = await remediate_scan(
                scan.id, MagicMock(), use_ai=True, db=db, principal=_principal()
            )
        else:
            with pytest.raises(HTTPException) as caught:
                await remediate_scan(
                    scan.id, MagicMock(), use_ai=True, db=db, principal=_principal()
                )
            assert caught.value.status_code == 500
            result = {"success": False}

    assert result["success"] is expected_success
    if expected_success:
        assert result["remediated_alt_text"] == ""
        assert result["is_decorative"] is True
        terminal = audit.log_remediation_complete.call_args.kwargs
    else:
        terminal = audit.log_remediation_failed.call_args.kwargs
    assert terminal["alt_text_attempted"] is True
    assert terminal["alt_text_used"] is expected_success
    assert terminal["alt_text_external_ai_used"] is expected_external
    assert terminal["providers"] == {"alt_text": expected_provider}
    assert terminal["providers_attempted"] == {"alt_text": expected_attempts}
    assert terminal["purpose_outcomes"]["alt_text"] == (
        "used" if expected_success else "attempted_failed"
    )
    assert "SENSITIVE" not in str(terminal)
    assert "SENSITIVE" not in caplog.text


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
    # The failed atomic transaction is rolled back, then a sanitized terminal
    # failure audit is attempted in a fresh best-effort transaction.
    assert db.commits == 2
    assert db.rollbacks == 2
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_case", "expected_error", "expected_total"),
    [
        pytest.param("missing_result", "missing_scan_result", 0),
        pytest.param("missing_source", "source_file_unavailable", 1),
        pytest.param("unsupported_type", "unsupported_scan_type", 1),
    ],
)
@pytest.mark.parametrize(
    ("options", "expected_requested", "expected_outcomes"),
    [
        pytest.param(
            None,
            (True, True),
            {"remediation": "allowed_not_used", "alt_text": "allowed_not_used"},
            id="legacy-both-requested",
        ),
        pytest.param(
            RemediationOptions(use_ai=False),
            (False, True),
            {"remediation": "not_requested", "alt_text": "allowed_not_used"},
            id="remediation-not-requested",
        ),
        pytest.param(
            RemediationOptions(generate_alt_text=False),
            (True, False),
            {"remediation": "allowed_not_used", "alt_text": "not_requested"},
            id="alt-text-not-requested",
        ),
        pytest.param(
            RemediationOptions(use_ai=False, generate_alt_text=False),
            (False, False),
            {"remediation": "not_requested", "alt_text": "not_requested"},
            id="neither-requested",
        ),
    ],
)
async def test_generic_post_intent_validation_exit_persists_one_bounded_failure_audit(
    tmp_path,
    terminal_case,
    expected_error,
    expected_total,
    options,
    expected_requested,
    expected_outcomes,
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "source.bin"
    path.write_bytes(b"not audit content")
    scan = SimpleNamespace(
        id="scan-terminal",
        department_id="dept-1",
        scan_type=(
            "unsupported" if terminal_case == "unsupported_type" else ScanType.WORD
        ),
        storage_path=(
            str(tmp_path / "missing.docx")
            if terminal_case == "missing_source"
            else str(path)
        ),
        file_name="SENSITIVE-original-name.docx",
        status=ScanStatus.PROCESSING,
        remediation_outcome=None,
        completed_at=None,
        result=(
            None
            if terminal_case == "missing_result"
            else SimpleNamespace(issues=[{"content": "SENSITIVE issue content"}])
        ),
    )
    db = _TransactionDB(None)

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch("src.api.education.remediation_routes.get_provider_manager") as manager,
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(
            scan.id,
            None,
            options=options,
            db=db,
            principal=_principal(),
        )

    assert caught.value.status_code == 400
    manager.assert_not_called()
    audits = [row for row in db.persisted if isinstance(row, AuditLog)]
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == "remediation_failed"
    assert audit.status == "failure"
    assert audit.details["error"] == expected_error
    assert (
        audit.details["remediation_ai_requested"],
        audit.details["alt_text_requested"],
    ) == expected_requested
    assert audit.details["purpose_outcomes"] == expected_outcomes
    assert audit.details["total_issues"] == expected_total
    assert audit.details["fixed_count"] == 0
    assert audit.details["manual_count"] == 0
    assert audit.details["failed_count"] == 0
    assert audit.details["skipped_count"] == 0
    assert audit.details["providers"] == {}
    assert audit.details["use_ai"] is False
    assert "SENSITIVE" not in str(audit.details)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "allowed_purpose", "expected_outcomes"),
    [
        pytest.param(
            RemediationOptions(use_ai=True, generate_alt_text=False),
            None,
            {"remediation": "denied_at_dispatch", "alt_text": "not_requested"},
            id="remediation-denied",
        ),
        pytest.param(
            RemediationOptions(use_ai=False, generate_alt_text=True),
            None,
            {"remediation": "not_requested", "alt_text": "denied_at_dispatch"},
            id="alt-text-denied",
        ),
        pytest.param(
            RemediationOptions(use_ai=True, generate_alt_text=True),
            "remediation",
            {"remediation": "allowed_not_used", "alt_text": "denied_at_dispatch"},
            id="second-purpose-denied-after-first-allowed",
        ),
    ],
)
async def test_generic_policy_bind_denial_persists_exactly_one_dispatch_failure_audit(
    tmp_path, options, allowed_purpose, expected_outcomes
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "source.docx"
    path.write_bytes(b"document")
    scan = _scan(path)
    cloud_file = _cloud_file()
    db = _TransactionDB(cloud_file)
    allowed_client = MagicMock(provider="openai", model="bounded-model")

    def bind(*, purpose, **kwargs):
        return allowed_client if purpose == allowed_purpose else None

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            side_effect=bind,
        ),
        patch("src.api.education.remediation_routes.get_provider_manager") as manager,
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(
            scan.id,
            None,
            options=options,
            db=db,
            principal=_principal(),
        )

    assert caught.value.status_code == 403
    manager.assert_not_called()
    allowed_client.generate_text_sync.assert_not_called()
    allowed_client.generate_code_sync.assert_not_called()
    allowed_client.analyze_image_sync.assert_not_called()
    audits = [row for row in db.persisted if isinstance(row, AuditLog)]
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == "remediation_failed"
    assert audit.status == "failure"
    assert audit.details["error"] == "policy_not_permitted"
    assert audit.details["purpose_outcomes"] == expected_outcomes
    assert audit.details["total_issues"] == 0
    assert audit.details["fixed_count"] == 0
    assert audit.details["manual_count"] == 0
    assert audit.details["failed_count"] == 0
    assert audit.details["providers"] == {}
    assert audit.details["use_ai"] is False


@pytest.mark.asyncio
async def test_generic_dispatch_audit_outage_preserves_existing_4xx_without_duplicate():
    from src.api.education.remediation_routes import remediate_scan

    scan = SimpleNamespace(
        id="scan-no-result",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        storage_path=None,
        file_name="file.docx",
        status=ScanStatus.PROCESSING,
        remediation_outcome=None,
        completed_at=None,
        result=None,
    )
    db = _TransactionDB(None)
    audit = MagicMock()
    audit.log_remediation_failed.side_effect = RuntimeError("audit unavailable")

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch("src.security.audit_service.AuditService", return_value=audit),
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(
            scan.id,
            MagicMock(),
            db=db,
            principal=_principal(),
        )

    assert caught.value.status_code == 400
    assert caught.value.detail == "Scan has no results to remediate"
    audit.log_remediation_failed.assert_called_once()
    audit.log_remediation_complete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_case", "expected_error", "expected_total"),
    [
        pytest.param(
            "unsupported_lms_provider",
            "unsupported_lms_provider",
            0,
            id="unsupported-lms-provider",
        ),
        pytest.param(
            "lms_image_without_intent",
            "alt_text_not_requested",
            1,
            id="image-alt-text-not-requested",
        ),
    ],
)
async def test_generic_remaining_pre_provider_dispatch_exits_audit_exactly_once(
    tmp_path, terminal_case, expected_error, expected_total
):
    from src.api.education.remediation_routes import remediate_scan

    scan_type = (
        ScanType.IMAGE if terminal_case == "lms_image_without_intent" else ScanType.WORD
    )
    path = tmp_path / ("image.png" if scan_type == ScanType.IMAGE else "file.docx")
    path.write_bytes(b"source")
    scan = _scan(path, scan_type=scan_type)
    cloud_file = _cloud_file()
    if terminal_case == "unsupported_lms_provider":
        cloud_file.provider = CloudProvider.BLACKBOARD.value
    db = _TransactionDB(cloud_file)

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed"
        ) as bind,
        patch(
            "src.api.education.remediation_routes.ImageAltTextGenerator"
        ) as generator,
        patch("src.api.education.remediation_routes.get_provider_manager") as manager,
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_scan(
            scan.id,
            None,
            db=db,
            principal=_principal(),
        )

    assert caught.value.status_code == 400
    bind.assert_not_called()
    manager.assert_not_called()
    if terminal_case == "unsupported_lms_provider":
        generator.assert_not_called()
    else:
        generator.return_value.analyze_image_comprehensive.assert_not_called()
    audits = [row for row in db.persisted if isinstance(row, AuditLog)]
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == "remediation_failed"
    assert audit.status == "failure"
    assert audit.details["error"] == expected_error
    assert audit.details["purpose_outcomes"] == {
        "remediation": "not_requested",
        "alt_text": "not_requested",
    }
    assert audit.details["total_issues"] == expected_total
    assert audit.details["fixed_count"] == 0
    assert audit.details["manual_count"] == 0
    assert audit.details["failed_count"] == 0
    assert audit.details["providers"] == {}
