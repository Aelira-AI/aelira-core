"""Task 14 slice 3C1: execution-time LMS remediation policy enforcement."""

import ast
import importlib.util
import inspect
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
    CloudProvider,
    Scan,
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
            self.rows = [row for row in self.rows if getattr(row, key) == expected]
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        return self.rows[: self.limit_value]

    def first(self):
        return self.rows[0] if self.rows else None


class CloudFileDB:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.commits = 0

    def query(self, model):
        assert model is CloudFile
        return CloudFileQuery(self.rows)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1


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
    return SimpleNamespace(
        success=True,
        original_file=str(path),
        output_file=str(path.with_name("fixed.docx")),
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
    ):
        response = await remediate_canvas_file(
            request=body,
            background_tasks=MagicMock(),
            db=db,
            principal=_principal(),
        )

    assert response.success is True
    jobs = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], CloudJobQueue)
    ]
    remediation = next(job for job in jobs if job.job_type == "remediate")
    assert remediation.execution_context == {
        "ai_requested": True,
        "alt_text_requested": True,
        "requested_purposes": ["remediation", "alt_text"],
        "policy_version": "1",
        "policy_provider": "gemini",
        "originating_route": "/canvas/remediate",
        "resource_id": "file-1",
        "course_id": "course-1",
    }
    assert "secret" not in repr(remediation.execution_context)
    assert "evil" not in repr(remediation.execution_context)


class HandlerDB:
    def __init__(self, *, cloud_file, credential, scan):
        self.values = {
            type(cloud_file): cloud_file,
            type(credential): credential,
            type(scan): scan,
        }

    def get(self, model, identifier, **kwargs):
        assert not kwargs or kwargs == {"populate_existing": True}
        value = self.values.get(model)
        return value if value is not None and value.id == identifier else None


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
        result_data={"scan_id": scan.id},
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
@pytest.mark.parametrize(
    "provider", [CloudProvider.GOOGLE.value, CloudProvider.MICROSOFT.value]
)
async def test_non_lms_route_shaped_job_uses_exact_last_scan_fallback(provider):
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(provider=provider)
    job.result_data = {"upload_as_new": True}
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
    job.result_data = {"scan_id": "scan-other", "upload_as_new": True}
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
async def test_document_alt_intent_never_binds_or_passes_alt_client():
    from src.jobs.remediation_job import handle_remediation_job

    job, db = _job_graph(
        context={"alt_text_requested": True, "requested_purposes": ["alt_text"]}
    )
    process = AsyncMock(return_value={"success": True, "fixed_count": 0})
    with (
        patch("src.jobs.remediation_job.LMSRemediationClient.bind_if_allowed") as bind,
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
    ):
        await handle_remediation_job(job, db, MagicMock())

    bind.assert_not_called()
    assert process.await_args.kwargs["alt_text_client"] is None


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
async def test_canvas_background_chain_marks_artifact_failure_failed_without_raw_error():
    from src.api.canvas_routes import _canvas_scan_then_remediate_task
    from src.jobs.remediation_job import RemediationJobFailed

    scan_job = SimpleNamespace(
        id="scan-job",
        status="pending",
        started_at=None,
        progress=0,
        progress_message=None,
        result_data=None,
        completed_at=None,
        error_message=None,
    )
    remediation_job = SimpleNamespace(
        id="remediation-job",
        status="pending",
        started_at=None,
        progress=0,
        progress_message=None,
        result_data=None,
        completed_at=None,
        error_message=None,
    )
    scan_query = MagicMock()
    scan_query.filter.return_value = scan_query
    scan_query.first.return_value = scan_job
    remediation_query = MagicMock()
    remediation_query.filter.return_value = remediation_query
    remediation_query.first.return_value = remediation_job
    db = MagicMock()
    db.query.side_effect = [scan_query, remediation_query]
    context = MagicMock()
    context.__enter__.return_value = db
    context.__exit__.return_value = False

    with (
        patch("src.db.database.get_db", return_value=context),
        patch(
            "src.jobs.cloud_scan_job.handle_scan_job",
            new=AsyncMock(return_value={"scan_id": "scan-1"}),
        ),
        patch(
            "src.jobs.remediation_job.handle_remediation_job",
            new=AsyncMock(
                side_effect=RemediationJobFailed("remediation_artifact_unavailable")
            ),
        ),
    ):
        await _canvas_scan_then_remediate_task("scan-job", "remediation-job")

    assert scan_job.status == "completed"
    assert remediation_job.status == "failed"
    assert remediation_job.progress == 10
    assert remediation_job.error_message == "remediation_artifact_unavailable"
    assert "secret" not in repr(remediation_job.progress_message)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["scan", "remediation"])
async def test_canvas_background_failure_rolls_back_before_terminal_state_mutation(
    failure_phase,
):
    from src.api.canvas_routes import _canvas_scan_then_remediate_task

    events = []

    class TrackingJob(SimpleNamespace):
        def __setattr__(self, name, value):
            if name in {"status", "error_message", "completed_at"}:
                events.append(("set", self.id, name, value))
            super().__setattr__(name, value)

    class RollbackOnlyDB:
        def __init__(self, scan_job, remediation_job):
            self.jobs = [scan_job, remediation_job]

        def query(self, model):
            value = self.jobs.pop(0)
            query = MagicMock()
            query.filter.return_value = query
            query.first.return_value = value
            return query

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

    def job(job_id):
        return TrackingJob(
            id=job_id,
            status="pending",
            started_at=None,
            progress=0,
            progress_message=None,
            result_data=None,
            completed_at=None,
            error_message=None,
        )

    scan_job = job("scan-job")
    remediation_job = job("remediation-job")
    events.clear()
    db = RollbackOnlyDB(scan_job, remediation_job)
    context = MagicMock()
    context.__enter__.return_value = db
    context.__exit__.return_value = False
    scan_handler = AsyncMock(
        side_effect=(RuntimeError("scan failed") if failure_phase == "scan" else None),
        return_value={"scan_id": "scan-1"},
    )
    remediation_handler = AsyncMock(
        side_effect=(
            RuntimeError("remediation failed")
            if failure_phase == "remediation"
            else None
        ),
        return_value={"success": True, "fixed_count": 0},
    )

    with (
        patch("src.db.database.get_db", return_value=context),
        patch("src.jobs.cloud_scan_job.handle_scan_job", new=scan_handler),
        patch(
            "src.jobs.remediation_job.handle_remediation_job",
            new=remediation_handler,
        ),
    ):
        await _canvas_scan_then_remediate_task("scan-job", "remediation-job")

    rollback_index = events.index("rollback")
    terminal_job_ids = (
        {"scan-job", "remediation-job"}
        if failure_phase == "scan"
        else {"remediation-job"}
    )
    first_terminal_mutation = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple)
        and event[0] == "set"
        and event[1] in terminal_job_ids
        and event[2] == "error_message"
    )
    assert rollback_index < first_terminal_mutation
    assert events[-1] == "commit"


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
@pytest.mark.parametrize("provider", ["blackboard", "moodle"])
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
@pytest.mark.parametrize("lms_backed", [False, True])
async def test_generic_false_remediator_result_is_fatal_before_side_effects(
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
        patch("src.security.audit_service.AuditService") as audit,
    ):
        result = await remediate_scan(
            "scan-1", MagicMock(), db=db, principal=_principal()
        )

    assert result == {
        "success": False,
        "message": "remediation_failed",
        "scan_id": "scan-1",
    }
    assert db.added == []
    assert db.commits == 0
    audit.assert_not_called()


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
    assert generator_class.call_args.kwargs == {
        "lms_client": client,
        "allow_legacy_transport": False,
    }
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
    assert cls.call_args.kwargs["ai_client"] is client
    global_manager.assert_not_called()


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
    assert cls.call_args.kwargs["ai_client"] is manager


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

    def first(self):
        return self.value

    def delete(self):
        return 0


class _ProcessDB:
    def __init__(self, scan, scan_result):
        self.scan = scan
        self.scan_result = scan_result
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.queried_models = []

    def query(self, model):
        self.queried_models.append(model.__name__)
        values = {"Scan": self.scan, "ScanResult": self.scan_result, "ScanFix": None}
        return _ProcessQuery(values.get(model.__name__))

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _worker_remediation_result(path, *, fixed_count=0, success=True):
    return SimpleNamespace(
        success=success,
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
                background_tasks=MagicMock(),
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
                background_tasks=MagicMock(),
                db=db,
                principal=_principal(),
            )

    assert caught.value.status_code == 400
    assert caught.value.detail == "automatic_canvas_writeback_unsupported"
    bind.assert_not_called()
    db.add.assert_not_called()
