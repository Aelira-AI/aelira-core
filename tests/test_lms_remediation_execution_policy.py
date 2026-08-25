"""Task 14 slice 3C1: execution-time LMS remediation policy enforcement."""

import asyncio
import ast
import importlib.util
import inspect
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.canvas_routes import CanvasRemediateRequest, remediate_canvas_file
from src.api.education._shared import RemediationOptions
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import (
    APIKey,
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudProvider,
    RemediationArtifact,
    RemediationOutcome,
    Scan,
    ScanFix,
    ScanResult,
    ScanStatus,
    ScanType,
    UserRole,
)

ROOT = Path(__file__).resolve().parents[1]
LMS_PROVIDERS = {
    CloudProvider.CANVAS.value,
    CloudProvider.BLACKBOARD.value,
    CloudProvider.MOODLE.value,
    CloudProvider.BRIGHTSPACE.value,
}


@pytest.fixture(autouse=True)
def _managed_artifact_service_stub():
    artifact = SimpleNamespace(
        id="66666666-6666-4666-8666-666666666666",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=19,
        sha256="a" * 64,
        expires_at=MagicMock(isoformat=MagicMock(return_value="2099-01-01T00:00:00Z")),
        review_status="pending",
        lifecycle_status="available",
    )
    service = MagicMock()
    service.claim_and_publish.return_value = artifact
    with patch(
        "src.api.education.remediation_routes.RemediationArtifactService.from_settings",
        return_value=service,
    ):
        yield service


def _load_migration():
    path = ROOT / "alembic/versions/2026_08_20_cloud_job_execution_context.py"
    spec = importlib.util.spec_from_file_location("cloud_job_execution_context", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_execution_context_model_and_migration_are_safe_non_null_defaults():
    migration = _load_migration()
    column = CloudJobQueue.__table__.c.execution_context

    assert migration.revision == "20260820_job_exec_context"
    assert migration.down_revision == "20260820_lms_ai_policy"
    assert len(migration.revision) <= 32
    assert column.nullable is False
    assert callable(column.default.arg)
    assert column.default.arg(None) == {}
    assert "{}" in str(column.server_default.arg)


def test_execution_context_sanitizer_uses_strict_allowlist_and_exact_types():
    from src.jobs.remediation_job import sanitize_execution_context

    raw = {
        "ai_requested": 1,
        "alt_text_requested": False,
        "requested_purposes": ["alt_text", "remediation", "alt_text", "evil"],
        "policy_version": "v1",
        "policy_provider": "gemini",
        "originating_route": "/canvas/remediate",
        "resource_id": "file-1",
        "course_id": "course-1",
        "api_key": "secret",
        "prompt": "secret prompt",
        "url": "https://secret.invalid",
        "content": "private",
        "nested": {"credential": "secret"},
    }

    context = sanitize_execution_context(raw)

    assert context == {
        "alt_text_requested": False,
        "requested_purposes": ["remediation", "alt_text"],
        "policy_version": "v1",
        "policy_provider": "gemini",
        "originating_route": "/canvas/remediate",
        "resource_id": "file-1",
        "course_id": "course-1",
    }
    assert not {"api_key", "prompt", "url", "content", "nested"} & context.keys()


def test_canvas_file_remediation_defaults_to_mechanical_only():
    request = CanvasRemediateRequest(file_id="file-1", course_id="course-1")

    assert request.use_ai is False
    assert request.generate_alt_text is False
    assert request.upload_back is False


def test_execution_context_sanitizer_ignores_malformed_unhashable_and_oversized_values():
    from src.jobs.remediation_job import sanitize_execution_context

    context = sanitize_execution_context(
        {
            "ai_requested": "false",
            "alt_text_requested": [],
            "upload_back": False,
            "requested_purposes": [
                {"unhashable": True},
                "remediation",
                7,
                "alt_text",
                "remediation",
            ],
            "course_id": "ok/ID_-." * 100,
            "resource_id": "bad\nvalue",
            "originating_route": "/" + "a" * 500,
        }
    )

    assert context["upload_back"] is False
    assert context["requested_purposes"] == ["remediation", "alt_text"]
    assert "ai_requested" not in context
    assert "alt_text_requested" not in context
    assert "resource_id" not in context
    assert len(context["course_id"]) <= 255
    assert len(context["originating_route"]) <= 255


def _principal(department_id="dept-1"):
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id=department_id,
        user_role=UserRole.FACULTY,
        auth_method="session",
    )


class CloudFileQuery:
    """Small query fake that applies the real CloudFile equality predicates."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.limit_value = None

    def filter(self, *criteria):
        for criterion in criteria:
            key = criterion.left.key
            expected = criterion.right.value
            self.rows = [row for row in self.rows if getattr(row, key, row) == expected]
        return self

    def options(self, *args):
        return self

    def order_by(self, *args):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        return self

    def scalar(self):
        return self.rows[0] if self.rows else None

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        return self.rows[: self.limit_value]

    def first(self):
        return self.rows[0] if self.rows else None

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class CloudFileDB:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.commits = 0

    def query(self, model):
        if model is Scan.id:
            return CloudFileQuery(["scan-1"])
        if model is Scan:
            return CloudFileQuery(
                [
                    SimpleNamespace(
                        id="scan-1",
                        department_id="dept-1",
                        current_remediation_artifact_id=None,
                    )
                ]
            )
        if model in {ScanFix, RemediationArtifact}:
            return CloudFileQuery([])
        assert model is CloudFile
        return CloudFileQuery(self.rows)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def flush(self):
        pass

    def rollback(self):
        pass


def _cloud_file(*, id="cloud-1", scan_id="scan-1", course_id="course-1"):
    return CloudFile(
        id=id,
        department_id="dept-1",
        credential_id="cred-1",
        provider=CloudProvider.CANVAS.value,
        provider_file_id=f"provider-{id}",
        provider_parent_id=course_id,
        file_name="file.docx",
        file_type="docx",
        last_scan_id=scan_id,
    )


def _route_scan(path, *, issues=None):
    return SimpleNamespace(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        file_name="file.docx",
        status=ScanStatus.PROCESSING,
        remediation_outcome=None,
        result=SimpleNamespace(
            issues=issues if issues is not None else [{"description": "heading"}]
        ),
    )


def _route_result(path):
    path.with_name("fixed.docx").write_bytes(b"remediated document")
    return SimpleNamespace(
        success=True,
        original_file=str(path),
        output_file=str(path.with_name("fixed.docx")),
        verification_passed=True,
        total_issues=1,
        fixed_count=0,
        manual_count=1,
        failed_count=0,
        original_compliance_score=50.0,
        remediated_compliance_score=50.0,
        improvement=0.0,
        duration_seconds=0.01,
        fixed_issues=[],
        manual_issues=[],
        warnings=[],
    )


def _successful_route_result(path):
    result = _route_result(path)
    result.fixed_count = 1
    result.manual_count = 0
    return result


def _principal_for(auth_method):
    if auth_method == "lti":
        return AuthenticatedPrincipal(
            api_key=None,
            user_id="user-1",
            department_id="dept-1",
            user_role=UserRole.ADMIN,
            auth_method="lti",
            lti_staff_role="Administrator",
            lti_account_wide=True,
        )
    api_key = None
    if auth_method == "api_key":
        api_key = APIKey(
            id="key-1",
            key_hash="hash",
            key_prefix="aelira_test",
            user_id="user-1",
            department_id="dept-1",
        )
    return AuthenticatedPrincipal(
        api_key=api_key,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method=auth_method,
    )


@pytest.mark.asyncio
async def test_canvas_queue_context_is_built_from_trusted_fields_not_body_injection():
    credential = SimpleNamespace(id="cred-1")
    cloud_file = SimpleNamespace(
        id="cloud-1",
        department_id="dept-1",
        credential_id="cred-1",
        provider=CloudProvider.CANVAS.value,
        provider_version=None,
    )
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.first.side_effect = [credential, cloud_file]
    db = MagicMock()
    db.query.return_value = chain
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [SimpleNamespace(id="file-1")]
    body = CanvasRemediateRequest(
        file_id="file-1",
        course_id="course-1",
        use_ai=True,
        generate_alt_text=True,
    )
    # Pydantic must not allow arbitrary body data to flow into the job context.
    object.__setattr__(body, "api_key", "secret")
    object.__setattr__(body, "requested_purposes", ["evil"])

    with (
        patch("src.api.canvas_routes.require_feature", new=AsyncMock()),
        patch("src.api.canvas_routes.verify_department_access"),
        patch("src.api.canvas_routes.require_lti_course_access"),
        patch(
            "src.api.canvas_routes._get_canvas_client",
            new=AsyncMock(return_value=(credential, canvas)),
        ),
        patch(
            "src.api.canvas_routes.LMSRemediationClient.bind_if_allowed",
            return_value=SimpleNamespace(provider="gemini"),
        ),
        patch(
            "src.api.canvas_routes.enqueue_cloud_job",
            side_effect=[
                SimpleNamespace(id="scan-job-1"),
                SimpleNamespace(id="remediation-job-1"),
            ],
        ) as enqueue,
    ):
        response = await remediate_canvas_file(
            request=body,
            db=db,
            principal=_principal(),
        )

    assert response.success is True
    assert enqueue.call_count == 2
    scan_call, remediation_call = enqueue.call_args_list
    assert scan_call.kwargs["department_id"] == "dept-1"
    assert scan_call.kwargs["credential_id"] == "cred-1"
    assert scan_call.kwargs["payload"] == {
        "cloud_file_id": "cloud-1",
        "credential_id": "cred-1",
        "provider": "canvas",
        "provider_file_id": "file-1",
        "course_id": "course-1",
    }
    assert scan_call.kwargs["dedupe_key"] == (
        "scan:canvas:course-1:file:file-1:current"
    )
    assert remediation_call.kwargs["depends_on_job_id"] == "scan-job-1"
    assert remediation_call.kwargs["payload"]["scan_job_id"] == "scan-job-1"
    assert remediation_call.kwargs["execution_context"] == {
        "ai_requested": True,
        "alt_text_requested": True,
        "requested_purposes": ["remediation", "alt_text"],
        "policy_version": "1",
        "policy_provider": "gemini",
        "originating_route": "/canvas/remediate",
        "resource_id": "file-1",
        "course_id": "course-1",
    }
    assert "secret" not in repr(remediation_call.kwargs)
    assert "evil" not in repr(remediation_call.kwargs)


class HandlerDB:
    def __init__(self, *, cloud_file, credential, scan):
        self.values = {
            type(cloud_file): cloud_file,
            type(credential): credential,
            type(scan): scan,
        }
        self.commits = 0
        self.rollbacks = 0

    def get(self, model, identifier, **kwargs):
        assert not kwargs or kwargs == {"populate_existing": True}
        value = self.values.get(model)
        return value if value is not None and value.id == identifier else None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    @property
    def no_autoflush(self):
        return nullcontext()


class FreshCredentialHandlerDB(HandlerDB):
    """Session fake that exposes stale identity-map credential state."""

    def __init__(self, *, cloud_file, credential, fresh_credential, scan):
        super().__init__(cloud_file=cloud_file, credential=credential, scan=scan)
        self.fresh_credential = fresh_credential
        self.credential_gets = []

    def get(self, model, identifier, **kwargs):
        from src.db.models import CloudOAuthCredentials

        if model is CloudOAuthCredentials:
            self.credential_gets.append((identifier, kwargs))
            if kwargs == {"populate_existing": True}:
                return self.fresh_credential
        return super().get(model, identifier, **kwargs)


def _job_graph(
    *,
    provider="canvas",
    mismatch=None,
    context=None,
    last_scan_id="scan-1",
    active=True,
    scan_type=ScanType.WORD,
):
    from src.db.models import CloudFile, CloudOAuthCredentials, Scan

    department_id = "dept-1"
    cloud_file = CloudFile(
        id="cloud-1",
        department_id="other" if mismatch == "file" else department_id,
        credential_id="cred-1",
        provider=provider,
        provider_file_id="remote-1",
        file_name="file.docx",
        file_type="docx",
        last_scan_id=last_scan_id,
    )
    credential = CloudOAuthCredentials(
        id="cred-1",
        department_id="other" if mismatch == "credential" else department_id,
        provider=provider,
        access_token="x",
        refresh_token="x",
        token_expires_at=None,
        is_active=active,
    )
    scan = Scan(
        id="scan-1",
        department_id="other" if mismatch == "scan" else department_id,
        scan_type=scan_type,
        file_name="file.docx",
    )
    job = SimpleNamespace(
        id="job-1",
        department_id=department_id,
        cloud_file_id=cloud_file.id,
        credential_id=credential.id,
        provider=provider,
        payload={"scan_id": scan.id},
        result_data=None,
        execution_context=context or {},
    )
    return job, HandlerDB(cloud_file=cloud_file, credential=credential, scan=scan)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["file", "credential", "scan"])
async def test_worker_rejects_cross_tenant_graph_before_policy_or_processing(mismatch):
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(mismatch=mismatch, context={"ai_requested": True})
    with (
        patch("src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed") as bind,
        patch(
            "src.jobs.remediation_job.process_remediation_job", new=AsyncMock()
        ) as process,
        pytest.raises(Exception) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert type(caught.value).__name__ == "RemediationJobFailed"
    assert str(caught.value) == "invalid_job_scope"
    bind.assert_not_called()
    process.assert_not_called()


async def _assert_worker_rejects_unbound_scan(last_scan_id):
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(
        last_scan_id=last_scan_id,
        context={"ai_requested": True, "requested_purposes": ["remediation"]},
    )
    token_manager = MagicMock()
    with (
        patch("src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed") as bind,
        patch(
            "src.jobs.remediation_job.process_remediation_job", new=AsyncMock()
        ) as process,
        pytest.raises(Exception) as caught,
    ):
        await handle_remediation_job(job, db, token_manager)

    assert type(caught.value).__name__ == "RemediationJobFailed"
    assert str(caught.value) == "invalid_job_scope"
    bind.assert_not_called()
    process.assert_not_called()
    token_manager.assert_not_called()


@pytest.mark.asyncio
async def test_worker_rejects_same_tenant_wrong_scan_binding():
    await _assert_worker_rejects_unbound_scan("scan-other")


@pytest.mark.asyncio
async def test_worker_rejects_missing_last_scan_binding():
    await _assert_worker_rejects_unbound_scan(None)


@pytest.mark.asyncio
async def test_deterministic_lms_job_never_binds_and_injects_no_clients():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(context={})
    process = AsyncMock(return_value={"success": True})
    with (
        patch("src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed") as bind,
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
    ):
        result = await handle_remediation_job(job, db, MagicMock())

    assert result["success"] is True
    bind.assert_not_called()
    assert process.await_args.kwargs["ai_client"] is None
    assert process.await_args.kwargs["alt_text_client"] is None


@pytest.mark.asyncio
async def test_remediation_handler_returns_sanitized_result_for_worker_terminalization():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(context={})
    process = AsyncMock(
        return_value={
            "success": True,
            "scan_id": "scan-1",
            "artifact_id": "artifact-1",
            "file_path": "/must/not/serialize",
        }
    )

    with patch("src.jobs.remediation_job.process_remediation_job", new=process):
        result = await handle_remediation_job(job, db, MagicMock())

    assert process.await_args.kwargs["defer_final_commit"] is True
    assert db.commits == 1
    assert db.rollbacks == 0
    assert result["artifact_id"] == "artifact-1"
    assert "file_path" not in result
    assert job.result_data is None
    assert not hasattr(job, "status")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "error_code", "outcome"),
    [
        (provider, error_code, outcome)
        for provider in ("canvas", "blackboard", "google", "microsoft")
        for error_code, outcome in (
            ("manual_required", RemediationOutcome.MANUAL_REQUIRED.value),
            ("remediation_failed", RemediationOutcome.REMEDIATION_FAILED.value),
            (
                "remediation_artifact_unavailable",
                RemediationOutcome.ARTIFACT_UNAVAILABLE.value,
            ),
        )
    ],
)
async def test_remediation_failure_commits_domain_state_for_worker_terminalization(
    provider, error_code, outcome
):
    from src.jobs.remediation_job import RemediationJobFailed, handle_remediation_job

    job, db = _job_graph(provider=provider, context={})
    scan = db.values[Scan]

    async def process(*args, **kwargs):
        scan.status = ScanStatus.FAILED
        scan.remediation_outcome = outcome
        return {
            "success": False,
            "error": error_code,
            "scan_id": scan.id,
            "fixed_count": 2,
            "manual_count": 3,
            "failed_count": 1,
            "skipped_count": 4,
            "total_issues": 10,
            "file_path": "/must/not/serialize",
            "artifact_id": "must-not-survive-failure",
        }

    with (
        patch(
            "src.jobs.remediation_job.process_remediation_job",
            new=AsyncMock(side_effect=process),
        ),
        pytest.raises(RemediationJobFailed) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert caught.value.code == error_code
    assert caught.value.terminal_state_committed is False
    assert db.commits == 1
    assert db.rollbacks == 0
    assert scan.status == ScanStatus.FAILED
    assert scan.remediation_outcome == outcome
    assert job.result_data is None
    assert not hasattr(job, "status")


@pytest.mark.asyncio
async def test_remediation_failure_commit_error_is_uncommitted_for_outer_retry():
    from src.jobs.remediation_job import RemediationJobFailed, handle_remediation_job

    job, db = _job_graph(context={})
    scan = db.values[Scan]
    scan.status = ScanStatus.PROCESSING
    scan.remediation_outcome = None
    job.status = CloudJobStatus.PROCESSING.value
    job.progress = 10
    job.progress_message = "Remediating..."
    db.commit = MagicMock(side_effect=RuntimeError("commit unavailable"))

    async def process(*args, **kwargs):
        scan.status = ScanStatus.FAILED
        scan.remediation_outcome = RemediationOutcome.MANUAL_REQUIRED.value
        return {"success": False, "error": "manual_required", "manual_count": 1}

    with (
        patch(
            "src.jobs.remediation_job.process_remediation_job",
            new=AsyncMock(side_effect=process),
        ),
        pytest.raises(RemediationJobFailed) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert caught.value.code == "manual_required"
    assert caught.value.terminal_state_committed is False
    assert db.commit.call_count == 1
    assert db.rollbacks == 1
    assert scan.status == ScanStatus.PROCESSING
    assert scan.remediation_outcome is None
    assert job.status == CloudJobStatus.PROCESSING.value
    assert job.progress == 10
    assert job.progress_message == "Remediating..."


@pytest.mark.asyncio
async def test_remediation_completion_commit_failure_restores_processing_and_aborts_artifact():
    from src.jobs.remediation_job import (
        RemediationProcessingResult,
        RetryableRemediationJobError,
        handle_remediation_job,
    )
    from src.services.remediation_artifact_service import ArtifactPublicationResult

    job, db = _job_graph(context={})
    job.status = "processing"
    job.progress = 10
    job.progress_message = "Remediating..."
    job.completed_at = None
    job.error_message = None
    db.commit = MagicMock(side_effect=RuntimeError("commit failed"))
    artifact_service = MagicMock()
    artifact = SimpleNamespace(id="artifact-1")
    publication = ArtifactPublicationResult(
        artifact=artifact,
        artifact_id="artifact-1",
        publication_token="a" * 64,
    )
    process = AsyncMock(
        return_value=RemediationProcessingResult(
            {
                "success": True,
                "scan_id": "scan-1",
                "artifact_id": "artifact-1",
            },
            artifact_publication=publication,
        )
    )

    with (
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=artifact_service,
        ),
        pytest.raises(RetryableRemediationJobError) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert caught.value.code == "remediation_completion_retryable"
    assert caught.value.artifact_id == "artifact-1"
    assert caught.value.cleanup_complete is True
    assert "a" * 64 not in repr(caught.value)
    assert job.status == "processing"
    assert job.progress == 10
    assert job.completed_at is None
    assert db.rollbacks >= 1
    artifact_service.abort_staging.assert_called_once_with(
        db,
        artifact_id="artifact-1",
        publication_token="a" * 64,
    )


@pytest.mark.asyncio
async def test_remediation_completion_checkpoint_cancellation_aborts_exact_artifact():
    from src.jobs.remediation_job import (
        RemediationProcessingResult,
        handle_remediation_job,
    )
    from src.services.remediation_artifact_service import ArtifactPublicationResult

    job, db = _job_graph(context={})
    job.status = CloudJobStatus.PROCESSING.value
    job.progress = 10
    job.progress_message = "Remediating..."
    job.completed_at = None
    job.error_message = None
    job._assert_owned = AsyncMock(side_effect=asyncio.CancelledError)
    artifact_service = MagicMock()
    artifact = SimpleNamespace(id="artifact-cancelled")
    publication = ArtifactPublicationResult(
        artifact=artifact,
        artifact_id="artifact-cancelled",
        publication_token="b" * 64,
    )
    process = AsyncMock(
        return_value=RemediationProcessingResult(
            {
                "success": True,
                "scan_id": "scan-1",
                "artifact_id": "artifact-cancelled",
            },
            artifact_publication=publication,
        )
    )

    with (
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=artifact_service,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await handle_remediation_job(job, db, MagicMock())

    process.assert_awaited_once()
    job._assert_owned.assert_awaited_once_with()
    artifact_service.abort_staging.assert_called_once_with(
        db,
        artifact_id=publication.artifact_id,
        publication_token=publication.publication_token,
    )
    assert db.rollbacks == 1
    assert db.commits == 0
    assert job.status == CloudJobStatus.PROCESSING.value
    assert job.progress == 10
    assert job.progress_message == "Remediating..."
    assert job.completed_at is None
    assert job.error_message is None
    assert job.result_data is None


@pytest.mark.asyncio
async def test_remediation_completion_cancellation_cleanup_failure_stays_pending(
    caplog,
):
    from src.jobs.remediation_job import (
        RemediationProcessingResult,
        handle_remediation_job,
    )
    from src.services.remediation_artifact_service import ArtifactPublicationResult

    job, db = _job_graph(context={})
    job.status = CloudJobStatus.PROCESSING.value
    job.progress = 10
    job.completed_at = None
    job._assert_owned = AsyncMock(side_effect=asyncio.CancelledError)
    artifact_service = MagicMock()
    artifact_service.abort_staging.side_effect = OSError("cleanup unavailable")
    publication = ArtifactPublicationResult(
        artifact=SimpleNamespace(id="artifact-pending"),
        artifact_id="artifact-pending",
        publication_token="d" * 64,
    )
    process = AsyncMock(
        return_value=RemediationProcessingResult(
            {
                "success": True,
                "scan_id": "scan-1",
                "artifact_id": "artifact-pending",
            },
            artifact_publication=publication,
        )
    )

    with (
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=artifact_service,
        ),
        caplog.at_level("WARNING", logger="src.jobs.remediation_job"),
        pytest.raises(asyncio.CancelledError),
    ):
        await handle_remediation_job(job, db, MagicMock())

    artifact_service.abort_staging.assert_called_once_with(
        db,
        artifact_id=publication.artifact_id,
        publication_token=publication.publication_token,
    )
    pending = [
        record
        for record in caplog.records
        if getattr(record, "publication_cleanup_pending", False) is True
    ]
    assert len(pending) == 1
    assert pending[0].artifact_id == publication.artifact_id
    assert db.rollbacks == 2
    assert db.commits == 0
    assert job.status == CloudJobStatus.PROCESSING.value
    assert job.progress == 10
    assert job.completed_at is None
    assert job.result_data is None


@pytest.mark.asyncio
async def test_remediation_completion_fence_failure_aborts_exact_artifact_for_retry():
    from src.jobs.remediation_job import (
        RemediationCompletionCommitFailed,
        RemediationProcessingResult,
        handle_remediation_job,
    )
    from src.services.remediation_artifact_service import ArtifactPublicationResult

    job, db = _job_graph(context={})
    job.status = CloudJobStatus.PROCESSING.value
    job.progress = 10
    job.completed_at = None
    artifact_service = MagicMock()
    artifact = SimpleNamespace(id="artifact-fence")
    publication = ArtifactPublicationResult(
        artifact=artifact,
        artifact_id="artifact-fence",
        publication_token="c" * 64,
    )
    process = AsyncMock(
        return_value=RemediationProcessingResult(
            {
                "success": True,
                "scan_id": "scan-1",
                "artifact_id": "artifact-fence",
            },
            artifact_publication=publication,
        )
    )

    with (
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=artifact_service,
        ),
        patch(
            "src.jobs.remediation_job._fence_claim_for_handler_commit",
            side_effect=RuntimeError("claim fence unavailable"),
        ),
        pytest.raises(RemediationCompletionCommitFailed) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert caught.value.artifact_id == publication.artifact_id
    assert caught.value.cleanup_complete is True
    artifact_service.abort_staging.assert_called_once_with(
        db,
        artifact_id=publication.artifact_id,
        publication_token=publication.publication_token,
    )
    assert db.rollbacks == 1
    assert db.commits == 0
    assert job.status == CloudJobStatus.PROCESSING.value
    assert job.progress == 10
    assert job.completed_at is None
    assert job.result_data is None


@pytest.mark.asyncio
async def test_job_processor_cannot_second_commit_handler_owned_completion():
    from src.jobs.job_processor import JobProcessor
    from src.jobs.remediation_job import RemediationJobHandledResult

    job = SimpleNamespace(
        id="job-owned",
        job_type="remediate",
        status="pending",
        started_at=None,
        completed_at=None,
        progress=0,
        result_data=None,
        error_message=None,
        retry_count=0,
        max_retries=3,
    )
    db = MagicMock()
    db.commit.side_effect = [None, RuntimeError("forbidden second commit")]
    processor = JobProcessor()
    processor._token_manager = MagicMock()
    processor.register_handler(
        "remediate",
        AsyncMock(return_value=RemediationJobHandledResult(success=True)),
    )

    await processor._process_job(job, db)

    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_job_processor_does_not_touch_handler_committed_terminal_failure():
    from src.jobs.job_processor import JobProcessor
    from src.jobs.remediation_job import RemediationJobFailed

    job = SimpleNamespace(
        id="job-terminal-owned",
        job_type="remediate",
        status="pending",
        started_at=None,
        completed_at=None,
        progress=0,
        progress_message=None,
        result_data=None,
        error_message=None,
        retry_count=0,
        max_retries=3,
    )
    db = MagicMock()

    async def committed_failure(*args):
        job.status = CloudJobStatus.FAILED.value
        job.progress = 100
        job.error_message = "manual_required"
        job.result_data = {"success": False, "error": "manual_required"}
        raise RemediationJobFailed("manual_required", terminal_state_committed=True)

    processor = JobProcessor()
    processor._token_manager = MagicMock()
    processor.register_handler("remediate", committed_failure)

    await processor._process_job(job, db)

    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    db.refresh.assert_not_called()
    assert job.status == CloudJobStatus.FAILED.value
    assert job.progress == 100
    assert job.retry_count == 0
    assert job.result_data == {"success": False, "error": "manual_required"}


@pytest.mark.asyncio
async def test_job_processor_retries_uncommitted_terminal_commit_failure():
    from src.jobs.job_processor import JobProcessor
    from src.jobs.remediation_job import RemediationJobFailed

    job = SimpleNamespace(
        id="job-commit-retry",
        job_type="remediate",
        status="pending",
        started_at=None,
        completed_at=None,
        progress=0,
        progress_message=None,
        result_data=None,
        error_message=None,
        retry_count=0,
        max_retries=3,
    )
    try:
        raise RemediationJobFailed(
            "manual_required", terminal_state_committed=False
        ) from RuntimeError("commit unavailable")
    except RemediationJobFailed as failure:
        commit_failure = failure

    db = MagicMock()
    processor = JobProcessor()
    processor._token_manager = MagicMock()
    processor.register_handler("remediate", AsyncMock(side_effect=commit_failure))

    await processor._process_job(job, db)

    assert db.commit.call_count == 2
    db.rollback.assert_called_once()
    assert job.status == CloudJobStatus.PENDING.value
    assert job.progress == 0
    assert job.retry_count == 1
    assert job.error_message == "manual_required"
    assert job.completed_at is None


def test_provider_routes_delegate_remediation_to_the_durable_worker():
    from src.api import canvas_routes, google_routes, microsoft_routes

    routes = (
        (canvas_routes, "_canvas_scan_then_remediate_task"),
        (google_routes, "_remediate_file_task"),
        (microsoft_routes, "_remediate_file_task"),
    )
    for module, legacy_wrapper in routes:
        assert not hasattr(module, legacy_wrapper)
        endpoint = (
            module.remediate_canvas_file
            if module is canvas_routes
            else module.remediate_file
        )
        source = inspect.getsource(endpoint)
        assert "enqueue_cloud_job" in source
        assert "handle_remediation_job" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider", [CloudProvider.GOOGLE.value, CloudProvider.MICROSOFT.value]
)
async def test_non_lms_route_shaped_job_uses_exact_last_scan_fallback(provider):
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(provider=provider)
    job.payload = {"upload_as_new": True}
    process = AsyncMock(return_value={"success": True, "scan_id": "scan-1"})
    with patch("src.jobs.remediation_job.process_remediation_job", new=process):
        result = await handle_remediation_job(job, db, MagicMock())

    assert result["success"] is True
    assert process.await_args.args[0]["scan_id"] == "scan-1"
    assert process.await_args.kwargs["lms_policy_authoritative"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider", [CloudProvider.GOOGLE.value, CloudProvider.MICROSOFT.value]
)
async def test_non_lms_route_shaped_job_rejects_wrong_explicit_scan(provider):
    from src.jobs.remediation_job import RemediationJobFailed, handle_remediation_job

    job, db = _job_graph(provider=provider)
    job.payload = {"scan_id": "scan-other", "upload_as_new": True}
    with (
        patch(
            "src.jobs.remediation_job.process_remediation_job", new=AsyncMock()
        ) as process,
        pytest.raises(RemediationJobFailed, match="invalid_job_scope"),
    ):
        await handle_remediation_job(job, db, MagicMock())

    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_rejects_all_null_tenant_relation_graph():
    from src.db.models import CloudFile, CloudOAuthCredentials, Scan
    from src.jobs.remediation_job import RemediationJobFailed, handle_remediation_job

    job, db = _job_graph(provider=CloudProvider.GOOGLE.value)
    job.department_id = None
    db.values[CloudFile].department_id = None
    db.values[CloudOAuthCredentials].department_id = None
    db.values[Scan].department_id = None
    with (
        patch(
            "src.jobs.remediation_job.process_remediation_job", new=AsyncMock()
        ) as process,
        pytest.raises(RemediationJobFailed, match="invalid_job_scope"),
    ):
        await handle_remediation_job(job, db, MagicMock())

    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_rechecks_requested_purpose_and_uses_current_binding_only():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(
        context={"ai_requested": True, "requested_purposes": ["remediation"]}
    )
    current_client = SimpleNamespace(provider="openai")
    process = AsyncMock(return_value={"success": True})
    with (
        patch(
            "src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed",
            return_value=current_client,
        ) as bind,
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
    ):
        await handle_remediation_job(job, db, MagicMock())

    bind.assert_called_once_with(
        department_id="dept-1",
        purpose="remediation",
        job_id="job-1",
        scan_id="scan-1",
        cloud_file_id="cloud-1",
    )
    assert process.await_args.kwargs["ai_client"] is current_client


@pytest.mark.asyncio
async def test_document_alt_intent_binds_and_passes_alt_client():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(
        context={"alt_text_requested": True, "requested_purposes": ["alt_text"]}
    )
    current_client = SimpleNamespace(provider="openai")
    process = AsyncMock(return_value={"success": True, "fixed_count": 0})
    with (
        patch(
            "src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed",
            return_value=current_client,
        ) as bind,
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
    ):
        await handle_remediation_job(job, db, MagicMock())

    bind.assert_called_once_with(
        department_id="dept-1",
        purpose="alt_text",
        job_id="job-1",
        scan_id="scan-1",
        cloud_file_id="cloud-1",
    )
    assert process.await_args.kwargs["alt_text_client"] is current_client


@pytest.mark.asyncio
async def test_policy_disabled_after_enqueue_fails_without_stale_provider():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(
        context={
            "ai_requested": True,
            "requested_purposes": ["remediation"],
            "policy_provider": "gemini",
        }
    )
    process = AsyncMock(return_value={"success": True})
    with (
        patch(
            "src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed",
            return_value=None,
        ),
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
        pytest.raises(Exception) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert type(caught.value).__name__ == "RemediationJobFailed"
    assert str(caught.value) == "policy_not_permitted"
    process.assert_not_called()


@pytest.mark.asyncio
async def test_early_policy_failure_commits_domain_state_for_worker_terminalization():
    from src.jobs.remediation_job import RemediationJobFailed, handle_remediation_job

    job, db = _job_graph(
        context={
            "ai_requested": True,
            "requested_purposes": ["remediation"],
        }
    )
    scan = db.values[Scan]
    old_completed_at = object()
    scan.status = ScanStatus.COMPLETED
    scan.remediation_outcome = RemediationOutcome.COMPLETED.value
    scan.completed_at = old_completed_at

    with (
        patch(
            "src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed",
            return_value=None,
        ),
        pytest.raises(RemediationJobFailed) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert caught.value.code == "policy_not_permitted"
    assert caught.value.terminal_state_committed is False
    assert db.commits == 1
    assert scan.status == ScanStatus.FAILED
    assert scan.remediation_outcome == RemediationOutcome.REMEDIATION_FAILED.value
    assert scan.completed_at is not old_completed_at
    assert job.result_data is None
    assert not hasattr(job, "status")


@pytest.mark.asyncio
async def test_job_processor_sanitizes_untyped_failure_and_marks_failed():
    from src.jobs.job_processor import JobProcessor

    job = SimpleNamespace(
        id="job-1",
        job_type="remediate",
        status="pending",
        started_at=None,
        completed_at=None,
        progress=0,
        result_data=None,
        error_message=None,
        retry_count=0,
        max_retries=1,
    )
    db = MagicMock()
    processor = JobProcessor()
    processor.register_handler(
        "remediate", AsyncMock(side_effect=RuntimeError("secret provider traceback"))
    )

    await processor._process_job(job, db)

    assert job.status == "failed"
    assert job.error_message == "job_processing_failed"
    assert "secret" not in repr(job.error_message)
    assert job.progress < 100


@pytest.mark.asyncio
async def test_job_processor_rolls_back_and_refreshes_before_failure_state_commit():
    from src.jobs.job_processor import JobProcessor

    events = []

    class TrackingJob(SimpleNamespace):
        def __setattr__(self, name, value):
            if name in {"status", "retry_count", "error_message", "completed_at"}:
                events.append(("set", name, value))
            super().__setattr__(name, value)

    class RollbackDB:
        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def refresh(self, value):
            assert value is job
            events.append("refresh")

    job = TrackingJob(
        id="job-rollback",
        job_type="remediate",
        status="pending",
        started_at=None,
        completed_at=None,
        progress=0,
        result_data=None,
        error_message=None,
        retry_count=0,
        max_retries=1,
    )
    db = RollbackDB()
    processor = JobProcessor()
    processor._token_manager = MagicMock()
    processor.register_handler("remediate", AsyncMock(side_effect=RuntimeError("boom")))
    events.clear()

    await processor._process_job(job, db)

    rollback_index = events.index("rollback")
    assert events[rollback_index + 1] == "refresh"
    assert rollback_index < next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[:2] == ("set", "retry_count")
    )
    assert events[-1] == "commit"
    assert job.status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_code",
    [
        "invalid_job_scope",
        "unsupported_lms_remediation",
        "remediation_artifact_unavailable",
        "manual_required",
        "alt_text_manual_required",
        "policy_not_permitted",
        "download_failed",
        "remediation_failed",
    ],
)
async def test_job_processor_typed_deterministic_failure_is_terminal_before_retry_limit(
    failure_code,
):
    from src.jobs.job_processor import JobProcessor
    from src.jobs.remediation_job import RemediationJobFailed

    job = SimpleNamespace(
        id="job-terminal",
        job_type="remediate",
        status="pending",
        started_at=None,
        completed_at=None,
        progress=0,
        result_data=None,
        error_message=None,
        retry_count=0,
        max_retries=3,
    )
    db = MagicMock()
    processor = JobProcessor()
    processor._token_manager = MagicMock()
    processor.register_handler(
        "remediate", AsyncMock(side_effect=RemediationJobFailed(failure_code))
    )

    await processor._process_job(job, db)

    assert job.status == "failed"
    assert job.error_message == failure_code
    assert job.retry_count == 1
    assert job.completed_at is not None
    assert job.status != "pending"


@pytest.mark.asyncio
async def test_job_processor_unexpected_failure_remains_retryable():
    from src.jobs.job_processor import JobProcessor

    job = SimpleNamespace(
        id="job-retryable",
        job_type="remediate",
        status="pending",
        started_at=None,
        completed_at=None,
        progress=25,
        result_data=None,
        error_message=None,
        retry_count=0,
        max_retries=3,
    )
    db = MagicMock()
    processor = JobProcessor()
    processor._token_manager = MagicMock()
    handler = AsyncMock(side_effect=RuntimeError("transient provider error"))
    processor.register_handler("remediate", handler)

    await processor._process_job(job, db)

    handler.assert_awaited_once()

    assert job.status == "pending"
    assert job.error_message == "job_processing_failed"
    assert job.retry_count == 1
    assert job.completed_at is None
    assert job.progress == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retry_count", "expected_status"),
    [(0, CloudJobStatus.PENDING.value), (2, CloudJobStatus.FAILED.value)],
)
async def test_job_processor_publication_retry_is_immediate_and_bounded(
    retry_count, expected_status
):
    from src.jobs.job_processor import JobProcessor
    from src.jobs.remediation_job import RetryableRemediationJobError

    job = SimpleNamespace(
        id="job-publication-retry",
        job_type="remediate",
        status="pending",
        started_at=None,
        completed_at=None,
        progress=25,
        progress_message="Publishing /private/path",
        result_data={"scan_id": "scan-1"},
        error_message=None,
        retry_count=retry_count,
        max_retries=3,
    )
    db = MagicMock()
    processor = JobProcessor()
    processor._token_manager = MagicMock()
    processor.register_handler(
        "remediate",
        AsyncMock(
            side_effect=RetryableRemediationJobError(
                "remediation_artifact_retryable",
                artifact_id="artifact-1",
                cleanup_complete=False,
            )
        ),
    )

    await processor._process_job(job, db)

    db.rollback.assert_called_once()
    assert db.commit.call_count == 2
    assert job.status == expected_status
    assert job.retry_count == retry_count + 1
    assert job.error_message == "remediation_artifact_retryable"
    assert "/private/path" not in repr(job.progress_message)
    assert job.result_data == {
        "scan_id": "scan-1",
        "artifact_id": "artifact-1",
        "publication_cleanup_pending": True,
    }
    if expected_status == CloudJobStatus.PENDING.value:
        assert job.progress == 0
        assert job.completed_at is None
    else:
        assert job.completed_at is not None


@pytest.mark.asyncio
async def test_retryable_artifact_failure_becomes_typed_durable_worker_outcome():
    from src.jobs.contracts import FailureKind, JobFailure
    from src.jobs.registry import adapt_legacy_handler
    from src.jobs.remediation_job import RetryableRemediationJobError

    legacy = AsyncMock(
        side_effect=RetryableRemediationJobError(
            "remediation_artifact_retryable",
            artifact_id="artifact-1",
            cleanup_complete=False,
        )
    )
    handler = adapt_legacy_handler(legacy)
    context = SimpleNamespace(job_id="job-1", assert_owned=AsyncMock())
    job = SimpleNamespace(id="job-1")
    db = MagicMock()
    db.get.return_value = job

    result = await handler(context, db, MagicMock())

    assert isinstance(result, JobFailure)
    assert result.kind is FailureKind.RETRYABLE
    assert result.code == "remediation_artifact_retryable"
    assert result.details == {
        "artifact_id": "artifact-1",
        "publication_cleanup_pending": True,
    }
    assert job._assert_owned is context.assert_owned


@pytest.mark.asyncio
async def test_worker_requires_exactly_active_queued_credential_before_policy():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(
        active=False,
        context={"ai_requested": True, "requested_purposes": ["remediation"]},
    )
    with (
        patch("src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed") as bind,
        patch(
            "src.jobs.remediation_job.process_remediation_job", new=AsyncMock()
        ) as process,
        pytest.raises(Exception) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert type(caught.value).__name__ == "RemediationJobFailed"
    assert str(caught.value) == "invalid_job_scope"
    bind.assert_not_called()
    process.assert_not_called()


@pytest.mark.asyncio
async def test_worker_forces_fresh_credential_before_initial_binding_checks():
    from src.db.models import CloudOAuthCredentials, Scan
    from src.jobs.remediation_job import handle_remediation_job

    job, ordinary_db = _job_graph(
        context={"ai_requested": True, "requested_purposes": ["remediation"]}
    )
    stale_active = ordinary_db.values[CloudOAuthCredentials]
    fresh_revoked_mismatch = SimpleNamespace(
        id=stale_active.id,
        department_id="other-department",
        provider=CloudProvider.BLACKBOARD.value,
        is_active=False,
    )
    db = FreshCredentialHandlerDB(
        cloud_file=ordinary_db.values[CloudFile],
        credential=stale_active,
        fresh_credential=fresh_revoked_mismatch,
        scan=ordinary_db.values[Scan],
    )
    token_manager = MagicMock()

    with (
        patch("src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed") as bind,
        patch(
            "src.jobs.remediation_job.process_remediation_job", new=AsyncMock()
        ) as process,
        patch(
            "src.jobs.remediation_job._download_cloud_file", new=AsyncMock()
        ) as download,
        pytest.raises(Exception) as caught,
    ):
        await handle_remediation_job(job, db, token_manager)

    assert type(caught.value).__name__ == "RemediationJobFailed"
    assert str(caught.value) == "invalid_job_scope"
    assert db.credential_gets == [(job.credential_id, {"populate_existing": True})]
    bind.assert_not_called()
    process.assert_not_awaited()
    token_manager.refresh_if_expired.assert_not_called()
    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_uses_exact_validated_credential_and_never_uploads():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(context={})
    exact_credential = db.values[
        next(model for model in db.values if model.__name__ == "CloudOAuthCredentials")
    ]
    process = AsyncMock(return_value={"success": True, "fixed_count": 0})
    with patch("src.jobs.remediation_job.process_remediation_job", new=process):
        await handle_remediation_job(job, db, MagicMock())

    assert process.await_args.kwargs["credential"] is exact_credential
    assert process.await_args.args[0]["upload_to_cloud"] is False


@pytest.mark.asyncio
async def test_same_job_retry_leaves_retained_artifact_cleanup_to_maintenance():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(context={})
    job.result_data = {
        "scan_id": "scan-1",
        "artifact_id": "artifact-1",
        "publication_cleanup_pending": True,
    }
    events = []
    service = MagicMock()
    service.abort_staging_for_job.side_effect = lambda *args, **kwargs: events.append(
        "cleanup"
    )

    async def process(*args, **kwargs):
        events.append("process")
        return {"success": True, "fixed_count": 0, "scan_id": "scan-1"}

    with (
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.jobs.remediation_job.process_remediation_job",
            new=AsyncMock(side_effect=process),
        ),
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert events == ["process"]
    service.abort_staging_for_job.assert_not_called()
    assert job.result_data == {
        "scan_id": "scan-1",
        "artifact_id": "artifact-1",
        "publication_cleanup_pending": True,
    }


@pytest.mark.asyncio
async def test_worker_translates_structured_failure_to_sanitized_typed_exception():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(context={})
    process = AsyncMock(
        return_value={
            "success": False,
            "error": "remediation_artifact_unavailable",
            "debug": "secret raw provider failure",
        }
    )
    with (
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
        pytest.raises(Exception) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert type(caught.value).__name__ == "RemediationJobFailed"
    assert str(caught.value) == "remediation_artifact_unavailable"
    assert "secret" not in repr(caught.value)


@pytest.mark.asyncio
async def test_queued_lms_image_fails_before_alt_provider_or_processing():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(
        scan_type=ScanType.IMAGE,
        context={"alt_text_requested": True, "requested_purposes": ["alt_text"]},
    )
    with (
        patch("src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed") as bind,
        patch(
            "src.jobs.remediation_job.process_remediation_job", new=AsyncMock()
        ) as process,
        pytest.raises(Exception) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert str(caught.value) == "remediation_artifact_unavailable"
    bind.assert_not_called()
    process.assert_not_called()


def test_top_level_document_remediator_receives_exact_bound_client():
    from src.jobs.remediation_job import _get_remediator_for_scan_type

    client = object()
    constructor = MagicMock(return_value=object())
    with patch("src.education.remediation.docx_remediator.DocxRemediator", constructor):
        _get_remediator_for_scan_type(
            "DOCX",
            "/tmp/file.docx",
            [],
            True,
            ai_client=client,
            allow_legacy_nested_ai=False,
        )

    assert constructor.call_args.kwargs["ai_client"] is client
    config = constructor.call_args.kwargs["config"]
    assert config.use_ai is True
    assert config.allow_legacy_nested_ai is False


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["moodle", "brightspace"])
async def test_stale_unsupported_lms_jobs_fail_closed_before_download(provider):
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(provider=provider)
    with (
        patch(
            "src.jobs.remediation_job.process_remediation_job", new=AsyncMock()
        ) as process,
        pytest.raises(Exception) as caught,
    ):
        await handle_remediation_job(job, db, MagicMock())

    assert type(caught.value).__name__ == "RemediationJobFailed"
    assert str(caught.value) == "unsupported_lms_remediation"
    process.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["canvas", "blackboard", "google", "microsoft"])
async def test_supported_provider_matrix_reaches_artifact_worker(provider):
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(provider=provider)
    process_result = {
        "success": True,
        "artifact_id": "artifact-1",
        "fixed_count": 1,
    }
    with patch(
        "src.jobs.remediation_job.process_remediation_job",
        new=AsyncMock(return_value=process_result),
    ) as process:
        result = await handle_remediation_job(job, db, MagicMock())

    assert result == process_result
    job_data = process.await_args.args[0]
    assert job_data["job_id"] == job.id
    assert job_data["provider"] == provider


def test_lms_worker_has_no_global_manager_or_legacy_image_transport():
    import src.jobs.remediation_job as module

    tree = ast.parse(inspect.getsource(module))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    source = inspect.getsource(module)
    assert "get_provider_manager" not in names
    assert "allow_legacy_transport=True" not in source
    assert "ImageAltTextGenerator(" not in inspect.getsource(
        module.handle_remediation_job
    )
    assert not hasattr(module, "_queue_upload_job")
    assert "job_data=" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "query_use_ai", "expects_ai"),
    [
        pytest.param(None, None, False, id="no-options"),
        pytest.param(RemediationOptions(), None, False, id="empty-options"),
        pytest.param(
            RemediationOptions(latex_formats=["pdf"]),
            None,
            False,
            id="options-omits-use-ai",
        ),
        pytest.param(
            RemediationOptions(use_ai=True), None, True, id="explicit-body-true"
        ),
        pytest.param(None, True, True, id="explicit-query-true"),
        pytest.param(
            RemediationOptions(use_ai=False), None, False, id="explicit-body-false"
        ),
        pytest.param(None, False, False, id="explicit-query-false"),
        pytest.param(
            RemediationOptions(use_ai=True),
            False,
            True,
            id="body-true-precedes-query-false",
        ),
        pytest.param(
            RemediationOptions(use_ai=False),
            True,
            False,
            id="body-false-precedes-query-true",
        ),
    ],
)
async def test_generic_lms_route_requires_explicit_ai_intent(
    options, query_use_ai, expects_ai
):
    from src.api.education.remediation_routes import remediate_scan

    scan = SimpleNamespace(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        storage_path=None,
        file_name="file.docx",
        status=ScanStatus.PROCESSING,
        remediation_outcome=None,
        completed_at=None,
        result=SimpleNamespace(issues=[]),
    )
    cloud_file = _cloud_file()
    db = CloudFileDB([cloud_file])
    client = object()
    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            return_value=client,
        ) as bind,
        patch("src.api.education.remediation_routes.get_provider_manager") as manager,
    ):
        result = await remediate_scan(
            "scan-1",
            MagicMock(),
            options=options,
            use_ai=query_use_ai,
            db=db,
            principal=_principal(),
        )

    assert result["message"] == "No issues to remediate"
    assert scan.status == ScanStatus.COMPLETED
    assert scan.remediation_outcome == "no_op"
    assert bind.called is expects_ai
    manager.assert_not_called()


def test_generic_non_lms_omitted_ai_intent_preserves_legacy_true():
    from src.api.education.remediation_routes import _effective_remediation_use_ai

    assert _effective_remediation_use_ai(None, None, lms_backed=False) is True
    assert (
        _effective_remediation_use_ai(RemediationOptions(), None, lms_backed=False)
        is True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "expected_alt"),
    [
        pytest.param(None, True, id="omitted-preserves-legacy-generation"),
        pytest.param(
            RemediationOptions(generate_alt_text=False),
            False,
            id="explicit-false-disables",
        ),
        pytest.param(
            RemediationOptions(generate_alt_text=True),
            True,
            id="explicit-true-enables",
        ),
    ],
)
async def test_generic_non_lms_alt_intent_controls_remediator_config(
    tmp_path, options, expected_alt
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path, issues=[{"type": "alt_text", "description": "missing"}])
    db = CloudFileDB([])
    remediator = MagicMock()
    remediator.remediate.return_value = _route_result(path)
    manager = MagicMock()

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.education.remediation.DocxRemediator", return_value=remediator
        ) as ctor,
        patch(
            "src.api.education.remediation_routes.get_provider_manager",
            return_value=manager,
        ),
        patch("src.security.audit_service.AuditService"),
    ):
        await remediate_scan(
            "scan-1",
            MagicMock(),
            options=options,
            db=db,
            principal=_principal(),
        )

    kwargs = ctor.call_args.kwargs
    assert kwargs["config"].fix_alt_text is expected_alt
    assert kwargs["ai_client"].client is manager
    if expected_alt:
        assert kwargs["alt_text_client"].client is manager
    else:
        assert kwargs["alt_text_client"] is None


def test_generic_lms_alt_text_requires_separate_explicit_body_intent():
    from src.api.education.remediation_routes import _effective_generate_alt_text

    assert _effective_generate_alt_text(None, lms_backed=True) is False
    assert _effective_generate_alt_text(RemediationOptions(), lms_backed=True) is False
    assert (
        _effective_generate_alt_text(RemediationOptions(use_ai=True), lms_backed=True)
        is False
    )
    assert (
        _effective_generate_alt_text(
            RemediationOptions(generate_alt_text=True), lms_backed=True
        )
        is True
    )
    assert _effective_generate_alt_text(None, lms_backed=False) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("lms_backed", [False, True])
async def test_generic_false_remediator_result_emits_one_atomic_terminal_failure_audit(
    tmp_path, lms_backed
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path)
    db = CloudFileDB([_cloud_file()] if lms_backed else [])
    remediator = MagicMock()
    failed_result = _route_result(path)
    failed_result.success = False
    remediator.remediate.return_value = failed_result

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
            "src.security.audit_service.AuditService", return_value=MagicMock()
        ) as audit_cls,
    ):
        result = await remediate_scan(
            "scan-1", MagicMock(), db=db, principal=_principal()
        )

    assert result["success"] is False
    audit = audit_cls.return_value
    audit.log_remediation_complete.assert_not_called()
    audit.log_remediation_failed.assert_called_once()
    details = audit.log_remediation_failed.call_args.kwargs
    assert details["commit"] is False
    assert details["error"] == "remediation_failed"
    assert details["remediation_ai_requested"] is (not lms_backed)
    assert details["alt_text_requested"] is (not lms_backed)
    assert "SENSITIVE" not in str(details)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_generic_lms_request_true_denied_is_stable_403_before_provider(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = SimpleNamespace(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        file_name="file.docx",
        result=SimpleNamespace(issues=[{"description": "heading"}]),
    )
    cloud_file = _cloud_file()
    db = CloudFileDB([cloud_file])
    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            return_value=None,
        ) as bind,
        patch("src.api.education.remediation_routes.get_provider_manager") as manager,
    ):
        with pytest.raises(HTTPException) as caught:
            await remediate_scan(
                "scan-1",
                MagicMock(),
                use_ai=True,
                db=db,
                principal=_principal(),
            )

    assert caught.value.status_code == 403
    bind.assert_called_once()
    manager.assert_not_called()


@pytest.mark.asyncio
async def test_generic_lms_image_injects_alt_text_client_without_legacy(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "image.png"
    path.write_bytes(b"image")
    scan = Scan(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.IMAGE,
        storage_path=str(path),
        file_name="image.png",
        status=ScanStatus.PROCESSING,
        metadata={"preserved": True},
    )
    scan.result = ScanResult(
        id="result-success",
        scan_id=scan.id,
        issues=[{"description": "missing alt"}],
    )
    cloud_file = _cloud_file()
    client = object()
    generator = MagicMock()
    generator.analyze_image_comprehensive = AsyncMock(
        return_value={
            "description": {"alt_text": "A chart"},
            "type_detection": {"is_decorative": False},
        }
    )
    db = CloudFileDB([cloud_file])
    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            return_value=client,
        ) as bind,
        patch(
            "src.api.education.remediation_routes.ImageAltTextGenerator",
            return_value=generator,
        ) as generator_class,
        patch("src.api.education.remediation_routes.get_provider_manager") as manager,
    ):
        result = await remediate_scan(
            "scan-1",
            MagicMock(),
            use_ai=True,
            db=db,
            principal=_principal(),
        )

    assert result["remediated_alt_text"] == "A chart"
    assert scan.status == ScanStatus.COMPLETED
    assert scan.metadata == {"preserved": True}
    assert scan.remediation_outcome == "completed"
    assert not hasattr(scan, "remediation_status")
    assert bind.call_args.kwargs["purpose"] == "alt_text"
    assert generator_class.call_args.kwargs["lms_client"].client is client
    assert generator_class.call_args.kwargs["allow_legacy_transport"] is False
    manager.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_method", ["session", "api_key", "lti"])
async def test_generic_lms_omitted_intent_uses_no_ai_or_global_manager(
    tmp_path, auth_method
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path)
    db = CloudFileDB([_cloud_file()])
    remediator = MagicMock()
    remediator.remediate.return_value = _route_result(path)

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed"
        ) as bind,
        patch(
            "src.api.education.remediation_routes.get_provider_manager"
        ) as global_manager,
        patch(
            "src.education.remediation.DocxRemediator", return_value=remediator
        ) as cls,
        patch("src.security.audit_service.AuditService"),
    ):
        result = await remediate_scan(
            "scan-1",
            MagicMock(),
            db=db,
            principal=_principal_for(auth_method),
        )

    assert result["success"] is True
    assert cls.call_args.kwargs["config"].use_ai is False
    assert cls.call_args.kwargs["ai_client"] is None
    bind.assert_not_called()
    global_manager.assert_not_called()


@pytest.mark.asyncio
async def test_generic_lms_explicit_true_is_policy_gated_with_exact_client(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(
        path,
        issues=[
            {"id": "alt", "category": "image_description"},
            {"id": "heading", "category": "heading"},
        ],
    )
    cloud_file = _cloud_file(id="exact-cloud")
    db = CloudFileDB([cloud_file])
    client = object()
    remediator = MagicMock()
    remediator.remediate.return_value = _route_result(path)
    remediator.remediate.return_value.manual_count = 0

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            return_value=client,
        ) as bind,
        patch(
            "src.api.education.remediation_routes.get_provider_manager"
        ) as global_manager,
        patch(
            "src.education.remediation.DocxRemediator", return_value=remediator
        ) as cls,
        patch("src.security.audit_service.AuditService"),
    ):
        result = await remediate_scan(
            "scan-1",
            MagicMock(),
            use_ai=True,
            db=db,
            principal=_principal(),
        )

    assert bind.call_args.kwargs["cloud_file_id"] == "exact-cloud"
    assert cls.call_args.kwargs["config"].use_ai is True
    assert cls.call_args.kwargs["config"].fix_alt_text is False
    assert [item["id"] for item in cls.call_args.kwargs["issues"]] == ["heading"]
    assert result["manual_count"] == 1
    assert cls.call_args.kwargs["ai_client"].client is client
    global_manager.assert_not_called()


@pytest.mark.asyncio
async def test_direct_lms_partition_materializes_node_manuals_and_preserves_total(
    tmp_path,
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    issues = [
        {
            "id": "duplicate",
            "category": "heading",
            "metadata": {"axe_rule_id": "image-alt"},
            "nodes": [{"target": ["#one"]}, {"target": ["#two"]}],
        },
        {"id": "duplicate", "category": "heading"},
    ]
    scan = _route_scan(path, issues=issues)
    db = CloudFileDB([_cloud_file()])
    remediation_client = MagicMock()
    remediator = MagicMock()
    route_result = _route_result(path)
    route_result.total_issues = 1
    route_result.manual_count = 0
    route_result.failed_count = 1
    route_result.manual_issues = []
    remediator.remediate.return_value = route_result

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            return_value=remediation_client,
        ),
        patch(
            "src.education.remediation.DocxRemediator", return_value=remediator
        ) as cls,
        patch("src.security.audit_service.AuditService"),
    ):
        result = await remediate_scan(
            "scan-1",
            MagicMock(),
            options=RemediationOptions(use_ai=True, generate_alt_text=False),
            db=db,
            principal=_principal(),
        )

    assert [item["id"] for item in cls.call_args.kwargs["issues"]] == ["duplicate"]
    remediation_client.analyze_image_sync.assert_not_called()
    assert result["total_issues"] == 3
    assert result["manual_count"] == 2
    assert [item["id"] for item in result["manual_issues"]] == [
        "duplicate:node:0",
        "duplicate:node:1",
    ]
    assert (
        result["fixed_count"]
        + result["manual_count"]
        + result["failed_count"]
        + result["skipped_count"]
        == result["total_issues"]
    )
    assert route_result.total_issues == 3
    assert (
        route_result.fixed_count
        + route_result.manual_count
        + route_result.failed_count
        + getattr(route_result, "skipped_count", 0)
        == route_result.total_issues
    )


@pytest.mark.asyncio
async def test_direct_lms_alt_only_audit_records_actual_purpose_usage(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path, issues=[{"id": "alt", "type": "image-alt"}])
    db = CloudFileDB([_cloud_file()])
    alt_client = MagicMock(provider="gemini")
    alt_client.analyze_image_sync.return_value = {
        "success": True,
        "content": "safe result",
        "ai_used": True,
        "external_ai_used": True,
        "provider": "gemini",
        "purpose_outcome": "used",
    }
    route_result = _route_result(path)
    route_result.fixed_count = 1
    route_result.manual_count = 0
    route_result.manual_issues = []
    remediator = MagicMock()

    def run_remediation():
        tracked = remediator_class.call_args.kwargs["alt_text_client"]
        tracked.analyze_image_sync(b"image")
        return route_result

    remediator.remediate.side_effect = run_remediation
    audit = MagicMock()
    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            return_value=alt_client,
        ) as bind,
        patch(
            "src.education.remediation.DocxRemediator", return_value=remediator
        ) as remediator_class,
        patch("src.security.audit_service.AuditService", return_value=audit),
    ):
        await remediate_scan(
            "scan-1",
            MagicMock(),
            options=RemediationOptions(use_ai=False, generate_alt_text=True),
            db=db,
            principal=_principal(),
        )

    assert [call.kwargs["purpose"] for call in bind.call_args_list] == ["alt_text"]
    details = audit.log_remediation_complete.call_args.kwargs
    assert details["remediation_ai_requested"] is False
    assert details["alt_text_requested"] is True
    assert details["remediation_ai_used"] is False
    assert details["alt_text_used"] is True
    assert details["use_ai"] is True
    assert details["external_ai_used"] is True
    assert details["providers"] == {"alt_text": "gemini"}
    assert details["purpose_outcomes"] == {
        "remediation": "not_requested",
        "alt_text": "used",
    }
    assert details["failed_count"] == 0
    assert details["skipped_count"] == 0


@pytest.mark.asyncio
async def test_direct_lms_requested_but_unused_audit_does_not_claim_ai_use(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path, issues=[{"id": "heading", "type": "heading"}])
    db = CloudFileDB([_cloud_file()])
    client = MagicMock(provider="ollama")
    remediator = MagicMock()
    remediator.remediate.return_value = _successful_route_result(path)
    audit = MagicMock()

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            return_value=client,
        ),
        patch("src.education.remediation.DocxRemediator", return_value=remediator),
        patch("src.security.audit_service.AuditService", return_value=audit),
    ):
        await remediate_scan(
            "scan-1",
            MagicMock(),
            options=RemediationOptions(use_ai=True, generate_alt_text=False),
            db=db,
            principal=_principal(),
        )

    details = audit.log_remediation_complete.call_args.kwargs
    assert details["remediation_ai_requested"] is True
    assert details["remediation_ai_used"] is False
    assert details["alt_text_requested"] is False
    assert details["alt_text_used"] is False
    assert details["use_ai"] is False
    assert details["external_ai_used"] is False
    assert details["providers"] == {}
    assert details["purpose_outcomes"]["remediation"] == "allowed_not_used"


class LegacyProviderManager:
    """Real legacy manager shape: no LMS usage metadata in responses."""

    def __init__(self, provider, *, success=True):
        self.provider = provider
        self.success = success
        self.calls = []

    def _result(self, method):
        self.calls.append(method)
        if self.success:
            return {
                "success": True,
                "content": "safe generated result",
                "provider": self.provider,
            }
        return {
            "success": False,
            "error": "provider_call_failed",
            "provider": self.provider,
        }

    def generate_text_sync(self, *args, **kwargs):
        return self._result("generate_text_sync")

    def generate_code_sync(self, *args, **kwargs):
        return self._result("generate_code_sync")

    def analyze_image_sync(self, *args, **kwargs):
        return self._result("analyze_image_sync")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "success", "expected_used", "expected_external", "expected_outcome"),
    [
        pytest.param("gemini", True, True, True, "used", id="gemini-success"),
        pytest.param("ollama", True, True, False, "used", id="ollama-success"),
        pytest.param(
            "gemini", False, False, True, "attempted_failed", id="provider-failure"
        ),
    ],
)
async def test_generic_legacy_manager_call_through_audits_actual_remediation_use(
    tmp_path,
    provider,
    success,
    expected_used,
    expected_external,
    expected_outcome,
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path, issues=[{"id": "heading", "type": "heading"}])
    db = CloudFileDB([])
    manager = LegacyProviderManager(provider, success=success)
    remediator = MagicMock()

    def run_remediation():
        tracked = remediator_class.call_args.kwargs["ai_client"]
        tracked.generate_text_sync("safe prompt")
        return _successful_route_result(path)

    remediator.remediate.side_effect = run_remediation
    audit = MagicMock()
    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.get_provider_manager",
            return_value=manager,
        ),
        patch(
            "src.education.remediation.DocxRemediator", return_value=remediator
        ) as remediator_class,
        patch("src.security.audit_service.AuditService", return_value=audit),
    ):
        await remediate_scan(
            "scan-1",
            MagicMock(),
            options=RemediationOptions(use_ai=True, generate_alt_text=False),
            db=db,
            principal=_principal(),
        )

    assert manager.calls == ["generate_text_sync"]
    details = audit.log_remediation_complete.call_args.kwargs
    assert details["use_ai"] is expected_used
    assert details["remediation_ai_used"] is expected_used
    assert details["external_ai_used"] is expected_external
    assert details["remediation_external_ai_used"] is expected_external
    assert details["providers"] == {"remediation": provider}
    assert details["purpose_outcomes"]["remediation"] == expected_outcome


@pytest.mark.asyncio
async def test_generic_legacy_manager_alt_only_call_through_audits_alt_purpose(
    tmp_path,
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path, issues=[{"id": "alt", "type": "image-alt"}])
    db = CloudFileDB([])
    manager = LegacyProviderManager("gemini")
    remediator = MagicMock()

    def run_remediation():
        tracked = remediator_class.call_args.kwargs["alt_text_client"]
        tracked.analyze_image_sync(b"safe image")
        return _successful_route_result(path)

    remediator.remediate.side_effect = run_remediation
    audit = MagicMock()
    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.get_provider_manager",
            return_value=manager,
        ),
        patch(
            "src.education.remediation.DocxRemediator", return_value=remediator
        ) as remediator_class,
        patch("src.security.audit_service.AuditService", return_value=audit),
    ):
        await remediate_scan(
            "scan-1",
            MagicMock(),
            options=RemediationOptions(use_ai=False, generate_alt_text=True),
            db=db,
            principal=_principal(),
        )

    assert manager.calls == ["analyze_image_sync"]
    details = audit.log_remediation_complete.call_args.kwargs
    assert details["use_ai"] is True
    assert details["remediation_ai_used"] is False
    assert details["alt_text_used"] is True
    assert details["external_ai_used"] is True
    assert details["providers"] == {"alt_text": "gemini"}
    assert details["purpose_outcomes"] == {
        "remediation": "not_requested",
        "alt_text": "used",
    }


@pytest.mark.asyncio
async def test_explicit_lms_denial_metadata_remains_authoritative_in_audit(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path, issues=[{"id": "heading", "type": "heading"}])
    db = CloudFileDB([_cloud_file()])
    client = MagicMock(provider="gemini")
    client.generate_text_sync.return_value = {
        "success": False,
        "error": "policy_denied",
        "ai_used": False,
        "external_ai_used": False,
        "provider": "gemini",
        "purpose_outcome": "denied_at_dispatch",
    }
    remediator = MagicMock()

    def run_remediation():
        tracked = remediator_class.call_args.kwargs["ai_client"]
        tracked.generate_text_sync("safe prompt")
        return _successful_route_result(path)

    remediator.remediate.side_effect = run_remediation
    audit = MagicMock()
    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.LMSRemediationClient.bind_if_allowed",
            return_value=client,
        ),
        patch(
            "src.education.remediation.DocxRemediator", return_value=remediator
        ) as remediator_class,
        patch("src.security.audit_service.AuditService", return_value=audit),
    ):
        await remediate_scan(
            "scan-1",
            MagicMock(),
            options=RemediationOptions(use_ai=True, generate_alt_text=False),
            db=db,
            principal=_principal(),
        )

    details = audit.log_remediation_complete.call_args.kwargs
    assert details["use_ai"] is False
    assert details["remediation_ai_used"] is False
    assert details["external_ai_used"] is False
    assert details["providers"] == {"remediation": "gemini"}
    assert details["purpose_outcomes"]["remediation"] == "denied_at_dispatch"


def test_usage_tracker_records_generation_exception_then_reraises_without_payload():
    from src.api.education.remediation_routes import _PurposeUsageTracker

    class ExplodingManager:
        provider = "gemini"

        def generate_code_sync(self, payload):
            raise RuntimeError(payload)

    tracker = _PurposeUsageTracker(
        ExplodingManager(), requested=True, authoritative=False
    )
    with pytest.raises(RuntimeError, match="SENSITIVE"):
        tracker.generate_code_sync("SENSITIVE")

    assert tracker.ai_used is False
    assert tracker.call_attempted is True
    assert tracker.external_ai_used is True
    assert tracker.provider_used == "gemini"
    assert tracker.outcome == "attempted_failed"


@pytest.mark.parametrize(
    (
        "result",
        "expected_attempted",
        "expected_used",
        "expected_external",
        "expected_outcome",
    ),
    [
        pytest.param(
            {
                "success": True,
                "ai_used": False,
                "external_ai_used": False,
                "purpose_outcome": "denied_at_dispatch",
                "provider": "ollama",
            },
            True,
            True,
            True,
            "used",
            id="success-cannot-understate-use",
        ),
        pytest.param(
            {
                "success": False,
                "ai_used": True,
                "external_ai_used": False,
                "purpose_outcome": "used",
                "provider": "ollama",
            },
            True,
            False,
            True,
            "attempted_failed",
            id="failure-cannot-claim-success-or-locality",
        ),
        pytest.param(
            {
                "success": False,
                "ai_used": False,
                "external_ai_used": False,
                "purpose_outcome": "denied_at_dispatch",
                "error": "provider_call_failed",
            },
            False,
            False,
            False,
            "denied_at_dispatch",
            id="trusted-stable-error-does-not-invalidate-denial",
        ),
    ],
)
def test_bound_tracker_derives_one_coherent_state_from_trusted_client(
    result,
    expected_attempted,
    expected_used,
    expected_external,
    expected_outcome,
):
    from src.api.education.remediation_routes import _PurposeUsageTracker

    client = MagicMock(provider="gemini")
    client.generate_text_sync.return_value = result
    tracker = _PurposeUsageTracker(
        client,
        requested=True,
        authoritative=True,
        trusted_lms_metadata=True,
    )

    tracker.generate_text_sync("safe")

    assert tracker.call_attempted is expected_attempted
    assert tracker.ai_used is expected_used
    assert tracker.external_ai_used is expected_external
    assert tracker.provider_used == "gemini"
    assert tracker.outcome == expected_outcome


def test_bound_gemini_result_cannot_spoof_ollama_provider_or_model():
    from src.api.education.remediation_routes import _PurposeUsageTracker

    client = MagicMock(provider="gemini")
    client.generate_text_sync.return_value = {
        "success": True,
        "provider": "ollama",
        "model": "bad\x00model",
        "external_ai_used": False,
    }
    tracker = _PurposeUsageTracker(
        client,
        requested=True,
        authoritative=True,
        trusted_lms_metadata=True,
    )

    tracker.generate_text_sync("safe")

    assert tracker.provider_used == "gemini"
    assert tracker.model_used is None
    assert tracker.external_ai_used is True


@pytest.mark.parametrize("provider", ["unknown-vendor", "gemini\x00", ["ollama"]])
def test_legacy_unknown_provider_is_omitted_and_external_use_is_conservative(provider):
    from src.api.education.remediation_routes import _PurposeUsageTracker

    client = MagicMock(spec=["generate_text_sync"])
    client.generate_text_sync.return_value = {
        "success": False,
        "provider": provider,
        "external_ai_used": False,
    }
    tracker = _PurposeUsageTracker(client, requested=True, authoritative=False)

    tracker.generate_text_sync("safe")

    assert tracker.call_attempted is True
    assert tracker.provider_used is None
    assert tracker.external_ai_used is True
    assert tracker.outcome == "attempted_failed"


@pytest.mark.parametrize(
    "error",
    [
        "credentials_unavailable",
        "provider_changed",
        "policy_resolution_failed",
        "audit_write_failed",
        "purpose_operation_mismatch",
        "policy_not_permitted",
        "policy_denied",
    ],
)
def test_trusted_coherent_denial_tuple_is_no_call_for_any_stable_error(error):
    from src.api.education.remediation_routes import _PurposeUsageTracker

    client = MagicMock(provider="gemini")
    client.generate_text_sync.return_value = {
        "success": False,
        "error": error,
        "ai_used": False,
        "external_ai_used": False,
        # Bound-client identity is authoritative; response relabeling is ignored.
        "provider": "ollama",
        "purpose_outcome": "denied_at_dispatch",
    }
    tracker = _PurposeUsageTracker(
        client,
        requested=True,
        authoritative=True,
        trusted_lms_metadata=True,
    )

    tracker.generate_text_sync("safe")

    assert tracker.call_attempted is False
    assert tracker.ai_used is False
    assert tracker.external_ai_used is False
    assert tracker.provider_used == "gemini"
    assert tracker.outcome == "denied_at_dispatch"


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(
            {
                "success": False,
                "ai_used": True,
                "external_ai_used": False,
                "purpose_outcome": "denied_at_dispatch",
            },
            id="ai-used-contradicts-denial",
        ),
        pytest.param(
            {
                "success": False,
                "ai_used": False,
                "external_ai_used": True,
                "purpose_outcome": "denied_at_dispatch",
            },
            id="external-use-contradicts-denial",
        ),
        pytest.param(
            {
                "success": False,
                "ai_used": False,
                "external_ai_used": False,
            },
            id="missing-denial-outcome",
        ),
    ],
)
def test_trusted_incoherent_denial_tuple_remains_conservative(result):
    from src.api.education.remediation_routes import _PurposeUsageTracker

    client = MagicMock(provider="gemini")
    client.generate_text_sync.return_value = result
    tracker = _PurposeUsageTracker(
        client,
        requested=True,
        authoritative=True,
        trusted_lms_metadata=True,
    )

    tracker.generate_text_sync("safe")

    assert tracker.call_attempted is True
    assert tracker.ai_used is False
    assert tracker.external_ai_used is True
    assert tracker.provider_used == "gemini"
    assert tracker.outcome == "attempted_failed"


def test_trusted_denial_with_unknown_bound_provider_remains_conservative():
    from src.api.education.remediation_routes import _PurposeUsageTracker

    client = MagicMock(provider="unknown-vendor")
    client.generate_text_sync.return_value = {
        "success": False,
        "ai_used": False,
        "external_ai_used": False,
        "purpose_outcome": "denied_at_dispatch",
    }
    tracker = _PurposeUsageTracker(
        client,
        requested=True,
        authoritative=True,
        trusted_lms_metadata=True,
    )

    tracker.generate_text_sync("safe")

    assert tracker.call_attempted is True
    assert tracker.external_ai_used is True
    assert tracker.provider_used is None
    assert tracker.outcome == "attempted_failed"


def test_trusted_denial_requires_client_provider_to_remain_bound():
    from src.api.education.remediation_routes import _PurposeUsageTracker

    class ProviderMutatingClient:
        provider = "gemini"

        def generate_text_sync(self, _prompt):
            self.provider = "ollama"
            return {
                "success": False,
                "ai_used": False,
                "external_ai_used": False,
                "purpose_outcome": "denied_at_dispatch",
            }

    tracker = _PurposeUsageTracker(
        ProviderMutatingClient(),
        requested=True,
        authoritative=True,
        trusted_lms_metadata=True,
    )

    tracker.generate_text_sync("safe")

    assert tracker.call_attempted is True
    assert tracker.external_ai_used is True
    assert tracker.outcome == "attempted_failed"


@pytest.mark.parametrize(
    ("authoritative", "trusted_lms_metadata"),
    [
        pytest.param(False, True, id="non-authoritative-tracker"),
        pytest.param(True, False, id="legacy-untrusted-response"),
    ],
)
def test_denial_tuple_requires_authoritative_trusted_tracker(
    authoritative, trusted_lms_metadata
):
    from src.api.education.remediation_routes import _PurposeUsageTracker

    client = MagicMock(provider="gemini")
    client.generate_text_sync.return_value = {
        "success": False,
        "ai_used": False,
        "external_ai_used": False,
        "purpose_outcome": "denied_at_dispatch",
    }
    tracker = _PurposeUsageTracker(
        client,
        requested=True,
        authoritative=authoritative,
        trusted_lms_metadata=trusted_lms_metadata,
    )

    tracker.generate_text_sync("safe")

    assert tracker.call_attempted is True
    assert tracker.external_ai_used is True
    assert tracker.outcome == "attempted_failed"


@pytest.mark.parametrize(
    ("client", "requested", "authoritative", "expected_outcome"),
    [
        pytest.param(
            None,
            True,
            False,
            "allowed_not_used",
            id="legacy-absence-is-an-unused-allowance",
        ),
        pytest.param(
            None,
            True,
            True,
            "denied_at_dispatch",
            id="authoritative-lms-absence-is-denial",
        ),
        pytest.param(
            None,
            False,
            True,
            "not_requested",
            id="authoritative-absence-without-intent",
        ),
        pytest.param(
            object(),
            True,
            True,
            "allowed_not_used",
            id="authoritative-client-allowed-but-unused",
        ),
    ],
)
def test_usage_tracker_no_call_outcome_requires_explicit_authority(
    client, requested, authoritative, expected_outcome
):
    from src.api.education.remediation_routes import _PurposeUsageTracker

    tracker = _PurposeUsageTracker(
        client, requested=requested, authoritative=authoritative
    )

    assert tracker.call_attempted is False
    assert tracker.ai_used is False
    assert tracker.provider_used is None
    assert tracker.outcome == expected_outcome


@pytest.mark.asyncio
async def test_generic_standalone_scan_retains_legacy_global_manager(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path)
    db = CloudFileDB([])
    manager = object()
    remediator = MagicMock()
    remediator.remediate.return_value = _route_result(path)

    with (
        patch(
            "src.api.education.remediation_routes.ScanService.get_scan_with_result",
            return_value=scan,
        ),
        patch(
            "src.api.education.remediation_routes.get_provider_manager",
            return_value=manager,
        ) as global_manager,
        patch(
            "src.education.remediation.DocxRemediator", return_value=remediator
        ) as cls,
        patch("src.security.audit_service.AuditService"),
    ):
        await remediate_scan("scan-1", MagicMock(), db=db, principal=_principal())

    global_manager.assert_called_once_with()
    assert cls.call_args.kwargs["config"].use_ai is True
    assert cls.call_args.kwargs["ai_client"].client is manager


@pytest.mark.asyncio
async def test_generic_ambiguous_exact_scan_links_fail_closed(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path, issues=[])
    db = CloudFileDB([_cloud_file(id="one"), _cloud_file(id="two")])

    with patch(
        "src.api.education.remediation_routes.ScanService.get_scan_with_result",
        return_value=scan,
    ):
        with pytest.raises(HTTPException) as caught:
            await remediate_scan("scan-1", MagicMock(), db=db, principal=_principal())

    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_generic_course_scoped_mismatched_link_is_denied(tmp_path):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = _route_scan(path, issues=[])
    db = CloudFileDB([_cloud_file(course_id="other-course")])
    principal = AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method="lti",
        lti_course_id="course-1",
        lti_staff_role="Instructor",
    )

    with patch(
        "src.api.education.remediation_routes.ScanService.get_scan_with_result",
        return_value=scan,
    ):
        with pytest.raises(HTTPException) as caught:
            await remediate_scan("scan-1", MagicMock(), db=db, principal=principal)

    assert caught.value.status_code == 404


def test_authoritative_document_alt_partition_uses_canonical_aliases():
    from src.jobs.remediation_job import _partition_authoritative_document_issues

    issues = [
        {"id": "a", "category": "alt_text"},
        {"id": "b", "type": "alternative_text"},
        {"id": "c", "category": "image"},
        {"id": "d", "issue_type": "image_of_text"},
        {"id": "e", "type": "image_description"},
        {"id": "f", "category": "heading"},
    ]
    automatic, manual = _partition_authoritative_document_issues(issues)
    assert [issue["id"] for issue in automatic] == ["f"]
    assert [issue["id"] for issue in manual] == ["a", "b", "c", "d", "e"]


class _ProcessQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args):
        return self

    def options(self, *args):
        return self

    def order_by(self, *args):
        return self

    def populate_existing(self):
        return self

    def first(self):
        return self.value

    def one_or_none(self):
        return self.value

    def all(self):
        return [] if self.value is None else [self.value]

    def with_for_update(self):
        return self

    def scalar(self):
        return self.value

    def delete(self):
        return 0


class _ProcessDB:
    def __init__(self, scan, scan_result, cloud_file=None):
        self.scan = scan
        self.scan_result = scan_result
        self.cloud_file = cloud_file
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.queried_models = []

    def query(self, model):
        if model is Scan.id:
            self.queried_models.append("Scan.id")
            return _ProcessQuery(self.scan.id)
        self.queried_models.append(model.__name__)
        values = {
            "Scan": self.scan,
            "ScanResult": self.scan_result,
            "ScanFix": None,
            "RemediationArtifact": None,
            "CloudFile": self.cloud_file,
        }
        return _ProcessQuery(values.get(model.__name__))

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _worker_remediation_result(
    path, *, fixed_count=0, success=True, verification_passed=False
):
    return SimpleNamespace(
        success=success,
        verification_passed=verification_passed,
        fixed_count=fixed_count,
        manual_count=0,
        failed_count=0,
        skipped_count=0,
        output_file=str(path.with_name("fixed.docx")),
        fixed_issues=[],
        failed_issues=[],
        improvement=1.0 if fixed_count else 0.0,
        remediated_compliance_score=100.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("authoritative", [False, True])
async def test_process_false_remediator_result_is_fatal_before_side_effects(
    tmp_path, authoritative
):
    from src.jobs.remediation_job import process_remediation_job

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = Scan(
        id="scan-false",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        metadata={"preserved": True},
        status=ScanStatus.PROCESSING,
        file_name="file.docx",
    )
    db = _ProcessDB(scan, SimpleNamespace(issues=[{"category": "heading"}]))
    remediator = MagicMock()
    remediator.remediate.return_value = _worker_remediation_result(path, success=False)
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job._send_remediation_notification", new=AsyncMock()
        ) as notification,
    ):
        result = await process_remediation_job(
            {
                "scan_id": scan.id,
                "department_id": "dept-1",
                "file_path": str(path),
            },
            db,
            lms_policy_authoritative=authoritative,
        )

    assert result == {
        "success": False,
        "error": "remediation_failed",
        "scan_id": scan.id,
    }
    assert scan.status == ScanStatus.PROCESSING
    assert scan.metadata == {"preserved": True}
    assert db.commits == 0
    assert db.added == []
    assert "ScanFix" not in db.queried_models
    notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_broad_exception_rolls_back_before_structured_failure(tmp_path):
    from src.jobs.remediation_job import process_remediation_job

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = Scan(
        id="scan-error",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        status=ScanStatus.PROCESSING,
        file_name="file.docx",
    )
    db = _ProcessDB(scan, SimpleNamespace(issues=[{"category": "heading"}]))
    remediator = MagicMock()
    remediator.remediate.side_effect = RuntimeError("provider exploded")
    with patch(
        "src.jobs.remediation_job._get_remediator_for_scan_type",
        return_value=remediator,
    ):
        result = await process_remediation_job(
            {
                "scan_id": scan.id,
                "department_id": "dept-1",
                "file_path": str(path),
            },
            db,
        )

    assert result["error"] == "remediation_failed"
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_authoritative_document_keeps_embedded_alt_manual_and_fails_truthfully(
    tmp_path,
):
    from src.jobs.remediation_job import process_remediation_job

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    issues = [
        {"id": "alt", "category": "image_of_text"},
        {"id": "heading", "category": "heading"},
    ]
    scan = Scan(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        metadata={"preserved": True},
        status=ScanStatus.COMPLETED,
        completed_at=None,
        file_name="file.docx",
    )
    db = _ProcessDB(scan, SimpleNamespace(issues=issues))
    remediator = MagicMock()
    remediator.remediate.return_value = _worker_remediation_result(path)
    remediation_client = MagicMock()
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ) as get_remediator,
        patch(
            "src.jobs.remediation_job._send_remediation_notification", new=AsyncMock()
        ),
    ):
        result = await process_remediation_job(
            {"scan_id": "scan-1", "department_id": "dept-1", "file_path": str(path)},
            db,
            ai_client=remediation_client,
            lms_policy_authoritative=True,
        )

    assert get_remediator.call_args.kwargs["issues"] == [issues[1]]
    assert get_remediator.call_args.kwargs["allow_embedded_alt"] is False
    remediation_client.analyze_image_sync.assert_not_called()
    assert result["success"] is False
    assert result["error"] == "manual_required"
    assert result["manual_count"] == 1
    assert "output_file" not in result
    assert scan.status == ScanStatus.FAILED
    assert scan.metadata == {"preserved": True}
    assert scan.remediation_outcome == "manual_required"


@pytest.mark.asyncio
async def test_authoritative_mechanical_noop_without_remaining_issues_is_honest_success(
    tmp_path,
):
    from src.jobs.remediation_job import process_remediation_job

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = SimpleNamespace(
        id="scan-1",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        metadata={},
        status=None,
        completed_at=None,
        file_name="file.docx",
    )
    db = _ProcessDB(scan, SimpleNamespace(issues=[{"category": "heading"}]))
    remediator = MagicMock()
    remediator.remediate.return_value = _worker_remediation_result(path)
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job._send_remediation_notification",
            new=AsyncMock(),
        ),
    ):
        result = await process_remediation_job(
            {
                "scan_id": "scan-1",
                "department_id": "dept-1",
                "file_path": str(path),
            },
            db,
            lms_policy_authoritative=True,
        )

    assert result["success"] is True
    assert result["artifact_required"] is False
    assert result["manual_count"] == 0
    assert "output_file" not in result
    assert scan.status == ScanStatus.COMPLETED
    assert scan.remediation_outcome == "no_op"


@pytest.mark.asyncio
async def test_worker_zero_issue_scan_is_durable_noop_without_remediator_or_notification():
    from src.jobs.remediation_job import process_remediation_job

    scan = Scan(
        id="scan-zero",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        status=ScanStatus.PROCESSING,
        file_name="empty.docx",
    )
    db = _ProcessDB(scan, SimpleNamespace(issues=[]))
    with (
        patch("src.jobs.remediation_job._get_remediator_for_scan_type") as remediator,
        patch(
            "src.jobs.remediation_job._send_remediation_notification", new=AsyncMock()
        ) as notification,
    ):
        result = await process_remediation_job(
            {"scan_id": scan.id, "department_id": "dept-1"}, db
        )

    assert result == {
        "success": True,
        "fixed_count": 0,
        "manual_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "scan_id": scan.id,
        "artifact_required": False,
    }
    assert scan.status == ScanStatus.COMPLETED
    assert scan.remediation_outcome == "no_op"
    assert db.commits == 1
    assert db.added == []
    remediator.assert_not_called()
    notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_authoritative_document_fixes_fail_when_artifact_cannot_persist(tmp_path):
    from src.jobs.remediation_job import process_remediation_job

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = SimpleNamespace(
        id="scan-1",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        metadata={},
        status=None,
        completed_at=None,
        file_name="file.docx",
    )
    db = _ProcessDB(scan, SimpleNamespace(issues=[{"category": "heading"}]))
    remediator = MagicMock()
    remediator.remediate.return_value = _worker_remediation_result(path, fixed_count=1)
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job._send_remediation_notification", new=AsyncMock()
        ) as notification,
    ):
        result = await process_remediation_job(
            {"scan_id": "scan-1", "department_id": "dept-1", "file_path": str(path)},
            db,
            lms_policy_authoritative=True,
        )

    assert result["success"] is False
    assert result["error"] == "remediation_artifact_unavailable"
    assert "output_file" not in result
    assert scan.status == ScanStatus.FAILED
    assert scan.metadata == {}
    assert scan.remediation_outcome == "artifact_unavailable"
    assert db.commits == 1
    assert db.added == []
    assert "ScanFix" not in db.queried_models
    notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_persists_verified_output_before_temp_cleanup(tmp_path):
    from src.jobs.remediation_job import process_remediation_job

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    fixed = path.with_name("fixed.docx")
    fixed.write_bytes(b"verified remediated document")
    scan = Scan(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        status=ScanStatus.PROCESSING,
        file_name="file.docx",
    )
    cloud_file = SimpleNamespace(id="cloud-1", provider=CloudProvider.CANVAS.value)
    db = _ProcessDB(
        scan,
        SimpleNamespace(issues=[{"category": "heading"}]),
        cloud_file,
    )
    remediator = MagicMock()
    remediator.remediate.return_value = _worker_remediation_result(
        path, fixed_count=1, verification_passed=True
    )
    artifact = SimpleNamespace(
        id="artifact-1",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=28,
        sha256="a" * 64,
        expires_at=MagicMock(isoformat=MagicMock(return_value="2099-01-01T00:00:00Z")),
        review_status="pending",
    )
    service = MagicMock()
    published_source = None

    def publish(*args, **kwargs):
        nonlocal published_source
        published_source = Path(kwargs["source_path"])
        assert published_source.is_file()
        assert published_source.read_bytes() == fixed.read_bytes()
        cloud_file.current_remediation_artifact_id = artifact.id
        cloud_file.has_remediated_version = True
        return artifact

    service.claim_and_publish.side_effect = publish
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(return_value={"success": True, "local_path": str(path)}),
        ),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
    ):
        result = await process_remediation_job(
            {
                "job_id": "job-1",
                "scan_id": scan.id,
                "cloud_file_id": cloud_file.id,
                "department_id": scan.department_id,
                "file_path": str(path),
            },
            db,
            lms_policy_authoritative=True,
        )

    assert result["success"] is True, result
    assert result["artifact_id"] == artifact.id
    assert "output_file" not in result
    assert published_source is not None
    assert not published_source.exists()
    assert scan.remediation_outcome == RemediationOutcome.COMPLETED.value
    assert scan.metadata is Scan.metadata
    assert result["fixed_count"] == 1
    assert result["manual_count"] == 0
    assert result["failed_count"] == 0


@pytest.mark.asyncio
async def test_unowned_artifact_claim_is_retryable_without_scan_mutation_or_cleanup(
    tmp_path,
):
    from src.jobs.remediation_job import (
        RetryableRemediationJobError,
        process_remediation_job,
    )
    from src.services.remediation_artifact_service import ArtifactInProgressError

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    fixed = path.with_name("fixed.docx")
    fixed.write_bytes(b"verified remediated document")
    scan = Scan(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        metadata={},
        status=ScanStatus.PROCESSING,
        file_name="file.docx",
    )
    cloud_file = SimpleNamespace(id="cloud-1", provider=CloudProvider.CANVAS.value)
    db = _ProcessDB(
        scan,
        SimpleNamespace(issues=[{"category": "heading"}]),
        cloud_file,
    )
    remediator = MagicMock()
    remediator.remediate.return_value = _worker_remediation_result(
        path, fixed_count=1, verification_passed=True
    )
    service = MagicMock()
    service.claim_and_publish.side_effect = ArtifactInProgressError("unowned")
    original_scan_state = (
        scan.status,
        scan.remediation_outcome,
        scan.completed_at,
        scan.metadata,
    )

    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(return_value={"success": True, "local_path": str(path)}),
        ),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        pytest.raises(RetryableRemediationJobError) as caught,
    ):
        await process_remediation_job(
            {
                "job_id": "job-1",
                "scan_id": scan.id,
                "cloud_file_id": cloud_file.id,
                "department_id": scan.department_id,
                "file_path": str(path),
            },
            db,
            lms_policy_authoritative=True,
        )

    assert caught.value.code == "remediation_artifact_retryable"
    assert caught.value.artifact_id is None
    assert caught.value.cleanup_complete is True
    assert (
        scan.status,
        scan.remediation_outcome,
        scan.completed_at,
        scan.metadata,
    ) == original_scan_state
    service.abort_staging.assert_not_called()
    assert "unowned" not in repr(caught.value)


def test_overlapping_duplicate_requeues_then_cannot_overwrite_original_success():
    from src.jobs.remediation_job import (
        RetryableRemediationJobError,
        transition_retryable_remediation_job,
    )

    job = SimpleNamespace(
        id="job-1",
        status=CloudJobStatus.PROCESSING.value,
        progress=10,
        progress_message="Remediating...",
        result_data={"scan_id": "scan-1"},
        completed_at=None,
        error_message=None,
        retry_count=0,
        max_retries=3,
    )
    scan = SimpleNamespace(status=ScanStatus.PROCESSING, remediation_outcome=None)
    artifact = SimpleNamespace(lifecycle_status="staging", published_at=None)
    db = MagicMock()
    failure = RetryableRemediationJobError("remediation_artifact_retryable")

    transition_retryable_remediation_job(job, db, failure)

    assert job.status == CloudJobStatus.PENDING.value
    assert job.retry_count == 1
    assert artifact.lifecycle_status == "staging"
    assert scan.status == ScanStatus.PROCESSING

    job.status = CloudJobStatus.PROCESSING.value

    def refresh_original_winner(value):
        artifact.lifecycle_status = "available"
        artifact.published_at = "original-published"
        scan.status = ScanStatus.COMPLETED
        scan.remediation_outcome = RemediationOutcome.COMPLETED.value
        value.status = CloudJobStatus.COMPLETED.value
        value.progress = 100
        value.progress_message = "Remediation complete"
        value.result_data = {
            "success": True,
            "scan_id": "scan-1",
            "artifact_id": "artifact-1",
        }
        value.completed_at = "original-completed"
        value.error_message = None

    db.refresh.side_effect = refresh_original_winner

    transition_retryable_remediation_job(job, db, failure)

    assert job.status == CloudJobStatus.COMPLETED.value
    assert job.retry_count == 1
    assert job.result_data["artifact_id"] == "artifact-1"
    assert job.completed_at == "original-completed"
    assert artifact.lifecycle_status == "available"
    assert artifact.published_at == "original-published"
    assert scan.status == ScanStatus.COMPLETED
    assert scan.remediation_outcome == RemediationOutcome.COMPLETED.value
    db.commit.assert_called_once()


def test_max_retry_failure_only_mutates_duplicate_job_not_available_authority():
    from src.jobs.remediation_job import (
        RetryableRemediationJobError,
        transition_retryable_remediation_job,
    )

    job = SimpleNamespace(
        status=CloudJobStatus.PROCESSING.value,
        progress=10,
        progress_message="Remediating...",
        result_data={"scan_id": "scan-1"},
        completed_at=None,
        error_message=None,
        retry_count=2,
        max_retries=3,
    )
    scan = SimpleNamespace(
        status=ScanStatus.COMPLETED,
        remediation_outcome=RemediationOutcome.COMPLETED.value,
    )
    artifact = SimpleNamespace(
        lifecycle_status="available",
        published_at="original-published",
    )
    db = MagicMock()

    transition_retryable_remediation_job(
        job, db, RetryableRemediationJobError("remediation_artifact_retryable")
    )

    assert job.status == CloudJobStatus.FAILED.value
    assert job.retry_count == 3
    assert artifact.lifecycle_status == "available"
    assert artifact.published_at == "original-published"
    assert scan.status == ScanStatus.COMPLETED
    assert scan.remediation_outcome == RemediationOutcome.COMPLETED.value
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_authoritative_manual_outcome_suppresses_success_side_effects(tmp_path):
    from src.jobs.remediation_job import process_remediation_job

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = SimpleNamespace(
        id="scan-manual",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        metadata={},
        status=None,
        completed_at=None,
        file_name="file.docx",
    )
    db = _ProcessDB(scan, SimpleNamespace(issues=[{"category": "image_of_text"}]))
    remediator = MagicMock()
    remediator.remediate.return_value = _worker_remediation_result(path)
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job._send_remediation_notification", new=AsyncMock()
        ) as notification,
    ):
        result = await process_remediation_job(
            {
                "scan_id": "scan-manual",
                "department_id": "dept-1",
                "file_path": str(path),
            },
            db,
            lms_policy_authoritative=True,
        )

    assert result["success"] is False
    assert result["error"] == "manual_required"
    assert scan.status == ScanStatus.FAILED
    assert scan.metadata == {}
    assert scan.remediation_outcome == "manual_required"
    assert db.commits == 1
    assert db.added == []
    assert "ScanFix" not in db.queried_models
    notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_revoked_credential_is_not_replaced_by_active_sibling():
    from src.db.models import CloudOAuthCredentials
    from src.jobs.remediation_job import _download_cloud_file

    cloud_file = SimpleNamespace(
        id="cloud-1",
        department_id="dept-1",
        credential_id="cred-a",
        provider=CloudProvider.CANVAS.value,
        provider_file_id="remote-1",
        file_name="file.docx",
    )
    revoked = SimpleNamespace(
        id="cred-a",
        department_id="dept-1",
        provider=CloudProvider.CANVAS.value,
        is_active=False,
        provider_metadata={"canvas_instance_url": "https://canvas.example"},
    )
    active_sibling = SimpleNamespace(
        id="cred-b",
        department_id="dept-1",
        provider=CloudProvider.CANVAS.value,
        is_active=True,
    )
    cloud_query = MagicMock()
    cloud_query.filter.return_value = cloud_query
    cloud_query.first.return_value = cloud_file
    sibling_query = MagicMock()
    sibling_query.filter.return_value = sibling_query
    sibling_query.first.return_value = active_sibling
    db = MagicMock()
    db.get.side_effect = lambda model, identifier, **kwargs: (
        cloud_file
        if model is CloudFile
        else revoked if model is CloudOAuthCredentials else None
    )
    db.query.side_effect = lambda model: (
        cloud_query if model is CloudFile else sibling_query
    )
    token_manager = MagicMock()

    result = await _download_cloud_file(
        "cloud-1",
        "dept-1",
        db,
        credential=revoked,
        token_manager=token_manager,
        require_exact_credential=True,
    )

    assert result == {"success": False, "error": "invalid_job_scope"}
    db.query.assert_called_once_with(CloudFile)
    sibling_query.first.assert_not_called()
    token_manager.refresh_if_expired.assert_not_called()


@pytest.mark.asyncio
async def test_exact_credential_forces_fresh_round_trip_before_token_or_download():
    from src.db.models import CloudOAuthCredentials
    from src.jobs.remediation_job import _download_cloud_file

    cloud_file = SimpleNamespace(
        id="cloud-1",
        department_id="dept-1",
        credential_id="cred-a",
        provider=CloudProvider.CANVAS.value,
        provider_file_id="remote-1",
        file_name="file.docx",
    )
    stale_active = SimpleNamespace(
        id="cred-a",
        department_id="dept-1",
        provider=CloudProvider.CANVAS.value,
        is_active=True,
        provider_metadata={"canvas_instance_url": "https://canvas.example"},
    )
    fresh_revoked = SimpleNamespace(
        id="cred-a",
        department_id="dept-1",
        provider=CloudProvider.CANVAS.value,
        is_active=False,
        provider_metadata={"canvas_instance_url": "https://canvas.example"},
    )
    cloud_query = MagicMock()
    cloud_query.filter.return_value = cloud_query
    cloud_query.first.return_value = cloud_file
    db = MagicMock()

    def get(model, identifier, **kwargs):
        if model is CloudOAuthCredentials:
            return (
                fresh_revoked
                if kwargs.get("populate_existing") is True
                else stale_active
            )
        return None

    db.get.side_effect = get
    db.query.return_value = cloud_query
    token_manager = MagicMock()

    with patch("src.integrations.canvas.CanvasAPIClient") as canvas_client:
        result = await _download_cloud_file(
            "cloud-1",
            "dept-1",
            db,
            credential=stale_active,
            token_manager=token_manager,
            require_exact_credential=True,
        )

    assert result == {"success": False, "error": "invalid_job_scope"}
    db.get.assert_called_once_with(
        CloudOAuthCredentials, "cred-a", populate_existing=True
    )
    token_manager.refresh_if_expired.assert_not_called()
    canvas_client.assert_not_called()


@pytest.mark.asyncio
async def test_generic_image_without_valid_alt_or_explicit_decorative_is_manual(
    tmp_path,
):
    from src.api.education.remediation_routes import remediate_scan

    path = tmp_path / "image.png"
    path.write_bytes(b"image")
    scan = Scan(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.IMAGE,
        storage_path=str(path),
        file_name="image.png",
        status=ScanStatus.COMPLETED,
        metadata={"preserved": True},
    )
    scan.result = ScanResult(
        id="result-1",
        scan_id=scan.id,
        issues=[{"description": "missing alt"}],
    )
    generator = MagicMock()
    generator.analyze_image_comprehensive = AsyncMock(
        return_value={
            "description": {"alt_text": "   "},
            "type_detection": {"is_decorative": False},
        }
    )
    db = CloudFileDB([_cloud_file()])
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
        result = await remediate_scan(
            "scan-1", MagicMock(), use_ai=True, db=db, principal=_principal()
        )

    assert result == {
        "success": False,
        "message": "manual_required",
        "fixed_count": 0,
        "manual_count": 1,
        "remediated_alt_text": "",
        "is_decorative": False,
    }
    assert scan.status == ScanStatus.FAILED
    assert scan.metadata == {"preserved": True}
    assert scan.remediation_outcome == "manual_required"
    assert not hasattr(scan, "remediation_status")


@pytest.mark.asyncio
async def test_canvas_authorizes_course_file_before_policy_oracle():
    credential = SimpleNamespace(id="cred-1")
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.first.return_value = credential
    db = MagicMock()
    db.query.return_value = chain
    canvas = AsyncMock()
    canvas.list_course_files.return_value = []
    request = CanvasRemediateRequest(
        file_id="hidden-file", course_id="course-1", use_ai=True, upload_back=True
    )
    with (
        patch("src.api.canvas_routes.require_feature", new=AsyncMock()),
        patch("src.api.canvas_routes.verify_department_access"),
        patch("src.api.canvas_routes.require_lti_course_access"),
        patch(
            "src.api.canvas_routes._get_canvas_client",
            new=AsyncMock(return_value=(credential, canvas)),
        ),
        patch("src.api.canvas_routes.LMSRemediationClient.bind_if_allowed") as bind,
    ):
        with pytest.raises(HTTPException) as caught:
            await remediate_canvas_file(
                request=request,
                db=db,
                principal=_principal(),
            )

    assert caught.value.status_code == 404
    bind.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_canvas_explicit_upload_back_fails_closed_before_enqueue():
    credential = SimpleNamespace(id="cred-1")
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.first.return_value = credential
    db = MagicMock()
    db.query.return_value = chain
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [SimpleNamespace(id="file-1")]
    with (
        patch("src.api.canvas_routes.require_feature", new=AsyncMock()),
        patch("src.api.canvas_routes.verify_department_access"),
        patch("src.api.canvas_routes.require_lti_course_access"),
        patch(
            "src.api.canvas_routes._get_canvas_client",
            new=AsyncMock(return_value=(credential, canvas)),
        ),
        patch("src.api.canvas_routes.LMSRemediationClient.bind_if_allowed") as bind,
    ):
        with pytest.raises(HTTPException) as caught:
            await remediate_canvas_file(
                request=CanvasRemediateRequest(
                    file_id="file-1", course_id="course-1", upload_back=True
                ),
                db=db,
                principal=_principal(),
            )

    assert caught.value.status_code == 400
    assert caught.value.detail == "automatic_canvas_writeback_unsupported"
    bind.assert_not_called()
    db.add.assert_not_called()
