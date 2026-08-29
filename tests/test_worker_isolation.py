"""Release-blocking contracts for issue #262 worker isolation."""

from __future__ import annotations

import ast
import asyncio
import os
import sys
import time
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudProvider,
    Scan,
    ScanStatus,
    UserRole,
)


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method="session",
    )


def _cloud_file(identifier: str = "cloud-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=identifier,
        department_id="dept-1",
        provider=CloudProvider.BRIGHTSPACE.value,
        credential_id="credential-1",
        provider_file_id="7",
        provider_parent_id="42",
        provider_metadata={"org_unit_id": 42, "url": "/safe/file.docx"},
        provider_version="version-1",
        content_source="file",
        content_body=None,
        content_updated_at=None,
        file_size_bytes=1,
        file_name="safe.docx",
        last_scan_id="scan-1",
    )


def _brightspace_credential() -> SimpleNamespace:
    return SimpleNamespace(
        id="credential-1",
        department_id="dept-1",
        provider=CloudProvider.BRIGHTSPACE.value,
        is_active=True,
    )


def _brightspace_job(cloud_file: SimpleNamespace) -> SimpleNamespace:
    from src.jobs.brightspace_content_job import _source_reference

    checker = AsyncMock()
    return SimpleNamespace(
        id="job-1",
        department_id="dept-1",
        provider=CloudProvider.BRIGHTSPACE.value,
        credential_id="credential-1",
        cloud_file_id=str(cloud_file.id),
        provider_file_id="7",
        status="processing",
        claim_token="claim-1",
        worker_id="worker-1",
        payload={
            "execution": "brightspace_content",
            "scan_id": "scan-1",
            "actor_id": "user-1",
            "options": {"use_ai": True, "generate_alt_text": False},
            "source": _source_reference(cloud_file),
            "provider_configuration": {
                "workspace_id": "dept-1",
                "configuration_id": "provider-config-1",
                "provider": "gemini",
                "configuration_revision": 3,
                "lms_policy_revision": 2,
            },
        },
        _assert_owned=checker,
    )


def _assert_complete_failure_envelope(
    result: dict, error_code: str, *, status: str = "failed"
) -> None:
    assert result == {
        "success": False,
        "status": status,
        "fixed_count": 0,
        "manual_count": 0,
        "failed_count": 1,
        "skipped_count": 0,
        "download_available": False,
        "ai_used": False,
        "external_ai_used": False,
        "providers": [],
        "purpose_decisions": {
            "remediation": "allowed_not_used",
        },
        "error_code": error_code,
    }


def test_api_startup_has_no_queue_consumer_or_playwright_execution() -> None:
    root = Path(__file__).parents[1]
    source = (root / "src" / "api" / "main.py").read_text()
    tree = ast.parse(source)
    startup = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "startup_event"
    )
    called = {
        node.func.id
        for node in ast.walk(startup)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    forbidden = {
        "JobProcessor",
        "get_job_processor",
        "process_pending_jobs",
        "start_job_processor_background",
        "sync_playwright",
        "WebScanner",
    }

    assert not called & forbidden
    assert "src.jobs.job_processor" not in source


def test_mounted_api_handlers_have_no_browser_or_cpu_processor_entrypoint() -> None:
    from fastapi.routing import APIRoute

    from src.api.main import app

    forbidden_names = {
        "FocusOrderAnalyzer",
        "sync_playwright",
        "async_playwright",
        "MultimediaProcessor",
    }
    violations: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        endpoint = route.endpoint
        module = getattr(endpoint, "__module__", "")
        if not module.startswith("src.api"):
            continue
        names = set(endpoint.__code__.co_names)
        if names & forbidden_names:
            violations.append(f"{','.join(sorted(route.methods))} {route.path}")
    assert violations == []

    api_root = Path(__file__).parents[1] / "src" / "api"
    for path in api_root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert not imported & {
            "FocusOrderAnalyzer",
            "sync_playwright",
            "async_playwright",
        }


def test_success_result_rejects_recursive_decrypted_credential_sentinel() -> None:
    from src.jobs.contracts import JobSuccess

    sentinel = "decrypted-credential-sentinel"
    with pytest.raises(ValueError, match="credential_material_forbidden"):
        JobSuccess({"success": True, "nested": [{"access-token": sentinel}]})


def test_failure_details_remove_recursive_decrypted_credential_sentinel() -> None:
    from src.jobs.contracts import JobFailure

    sentinel = "decrypted-failure-sentinel"
    failure = JobFailure.retryable(
        "temporary",
        {"safe": "code", "nested": [{"client_secret": sentinel}]},
    )
    assert failure.details == {"safe": "code", "nested": [{}]}
    assert sentinel not in repr(failure.details)


def test_child_authority_invalidates_connection_when_unlock_is_not_proven() -> None:
    from src.jobs.execution_authority import ChildExecutionAuthority

    connection = MagicMock()
    connection.scalar.return_value = False
    authority = ChildExecutionAuthority(connection, 42)

    with pytest.raises(RuntimeError, match="unlock failed"):
        authority.close()

    connection.invalidate.assert_called_once_with()
    connection.close.assert_called_once_with()
    authority.close()
    connection.close.assert_called_once_with()


def test_finish_acknowledges_committed_cancellation_after_handler_teardown() -> None:
    from src.jobs.contracts import JobSuccess
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    class CancellationWon(JobProcessor):
        def _external_effect_state(self, _claim):
            return None

        def _fenced_update(self, _claim, _values):
            return False

        def _cancellation_requested(self, _claim):
            return True

        def _acknowledge_cancellation(self, _claim):
            self.acknowledged = True
            return True

    worker = CancellationWon(session_factory=MagicMock())
    claim = ClaimedJob("job-1", "scan", {}, "claim-1", "worker-1", 1, 1)

    assert worker._finish(claim, JobSuccess()) is True
    assert worker.acknowledged is True


@pytest.mark.asyncio
async def test_worker_handler_failure_log_does_not_emit_credential_sentinel(
    caplog,
) -> None:
    from src.jobs.job_processor import ClaimedJob, JobProcessor
    from src.jobs.registry import JobRegistry

    sentinel = "decrypted-log-sentinel"

    async def handler(_context, _db, _tokens):
        raise RuntimeError(sentinel)

    class Processor(JobProcessor):
        def _owns_claim(self, _claim):
            return True

        def _cancellation_requested(self, _claim):
            return False

        def _fenced_update(self, _claim, _values):
            return True

        def _record_outcome(self, *, completed):
            pass

    registry = JobRegistry()
    registry.register("scan", handler)
    processor = Processor(registry=registry, session_factory=MagicMock())
    processor._token_manager = MagicMock()
    claim = ClaimedJob("job-1", "scan", {}, "claim-1", processor.worker_id, 1, 1)
    with caplog.at_level("ERROR"):
        assert await processor.process_claim(claim) is True
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_brightspace_single_route_only_authorizes_and_enqueues() -> None:
    from src.api.brightspace_routes import (
        BrightspaceContentRemediateRequest,
        remediate_content,
    )

    cloud_file = _cloud_file()
    job = SimpleNamespace(id="job-1", status="pending", progress=0)
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = cloud_file
    db.query.return_value = query

    with (
        patch(
            "src.api.brightspace_routes._authorize_brightspace_files",
            new=AsyncMock(return_value=[cloud_file]),
        ),
        patch(
            "src.api.brightspace_routes.enqueue_brightspace_content_remediation",
            return_value=job,
        ) as enqueue,
        patch("src.api.brightspace_routes._bind_brightspace_clients") as bind,
        patch("src.api.brightspace_routes._remediate_file") as execute,
    ):
        result = await remediate_content(
            "cloud-1",
            request=BrightspaceContentRemediateRequest(use_ai=True),
            principal=_principal(),
            db=db,
        )

    assert result.job_id == "job-1"
    assert result.status == "pending"
    assert result.status_url.endswith("/remediation/jobs/job-1")
    enqueue.assert_called_once()
    bind.assert_not_called()
    execute.assert_not_called()
    db.commit.assert_called_once_with()


@pytest.mark.asyncio
async def test_brightspace_batch_route_enqueues_every_authorized_item() -> None:
    from src.api.brightspace_routes import (
        BrightspaceBatchRemediateRequest,
        batch_remediate_content,
    )

    cloud_files = [_cloud_file("cloud-1"), _cloud_file("cloud-2")]
    jobs = [
        SimpleNamespace(id="job-1", status="pending", progress=0),
        SimpleNamespace(id="job-2", status="pending", progress=0),
    ]
    db = MagicMock()

    with (
        patch(
            "src.api.brightspace_routes._authorize_brightspace_files",
            new=AsyncMock(return_value=cloud_files),
        ),
        patch(
            "src.api.brightspace_routes.enqueue_brightspace_content_remediation",
            side_effect=jobs,
        ) as enqueue,
        patch("src.api.brightspace_routes._bind_brightspace_clients") as bind,
        patch("src.api.brightspace_routes._remediate_file") as execute,
    ):
        result = await batch_remediate_content(
            BrightspaceBatchRemediateRequest(
                org_unit_id=42,
                cloud_file_ids=["cloud-1", "cloud-2"],
                generate_alt_text=True,
            ),
            principal=_principal(),
            db=db,
        )

    assert result.status == "queued"
    assert result.requested_count == 2
    assert [item.job_id for item in result.jobs] == ["job-1", "job-2"]
    assert enqueue.call_count == 2
    bind.assert_not_called()
    execute.assert_not_called()
    db.commit.assert_called_once_with()


def test_brightspace_enqueue_pins_opaque_provider_configuration_revision() -> None:
    from src.db.models import Department
    from src.jobs.brightspace_content_job import (
        enqueue_brightspace_content_remediation,
    )

    cloud_file = _cloud_file()
    department = SimpleNamespace(
        id="dept-1",
        ai_primary_provider="gemini",
        lms_ai_provider="gemini",
        ai_provider_config_revision=7,
        lms_ai_policy_revision=5,
    )
    configuration = SimpleNamespace(id="opaque-config-1", provider="gemini")
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = cloud_file
    db.get.side_effect = lambda model, *_args, **_kwargs: (
        department if model is Department else None
    )
    db.query.return_value.filter.return_value.first.return_value = configuration
    with patch(
        "src.jobs.brightspace_content_job.enqueue_cloud_job",
        return_value=SimpleNamespace(id="job-1"),
    ) as enqueue:
        enqueue_brightspace_content_remediation(
            db,
            cloud_file=cloud_file,
            actor_id="user-1",
            options={"use_ai": True, "generate_alt_text": False},
        )

    payload = enqueue.call_args.kwargs["payload"]
    assert payload["provider_configuration"] == {
        "workspace_id": "dept-1",
        "configuration_id": "opaque-config-1",
        "provider": "gemini",
        "configuration_revision": 7,
        "lms_policy_revision": 5,
    }
    assert "api_key" not in repr(payload)


@pytest.mark.asyncio
async def test_brightspace_batch_enqueue_is_all_or_none() -> None:
    from src.api.brightspace_routes import (
        BrightspaceBatchRemediateRequest,
        batch_remediate_content,
    )
    from src.services.job_enqueue_service import JobEnqueueError

    cloud_files = [_cloud_file("cloud-1"), _cloud_file("cloud-2")]
    db = MagicMock()
    with (
        patch(
            "src.api.brightspace_routes._authorize_brightspace_files",
            new=AsyncMock(return_value=cloud_files),
        ),
        patch(
            "src.api.brightspace_routes.enqueue_brightspace_content_remediation",
            side_effect=[
                SimpleNamespace(id="job-1"),
                JobEnqueueError("brightspace_remediation_scope_invalid"),
            ],
        ),
    ):
        with pytest.raises(Exception) as error:
            await batch_remediate_content(
                BrightspaceBatchRemediateRequest(
                    org_unit_id=42,
                    cloud_file_ids=["cloud-1", "cloud-2"],
                ),
                principal=_principal(),
                db=db,
            )

    assert getattr(error.value, "status_code", None) == 409
    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()


@pytest.mark.asyncio
async def test_brightspace_worker_rejects_source_drift_before_execution() -> None:
    from src.jobs.brightspace_content_job import (
        _execute_brightspace_content_remediation_job,
    )

    cloud_file = _cloud_file()
    job = _brightspace_job(cloud_file)
    cloud_file.provider_version = "changed-after-enqueue"
    credential = _brightspace_credential()
    db = MagicMock()
    db.get.side_effect = lambda model, *_args, **_kwargs: (
        cloud_file if model is CloudFile else credential
    )

    with (
        patch(
            "src.jobs.brightspace_content_job._provider_configuration_reference",
            return_value=job.payload["provider_configuration"],
        ),
        patch("src.api.brightspace_routes._bind_brightspace_clients") as bind,
    ):
        result = await _execute_brightspace_content_remediation_job(
            job, db, MagicMock()
        )

    _assert_complete_failure_envelope(result, "invalid_job_scope")
    bind.assert_not_called()


@pytest.mark.asyncio
async def test_brightspace_worker_rejects_credential_drift_before_execution() -> None:
    from src.jobs.brightspace_content_job import (
        _execute_brightspace_content_remediation_job,
    )

    cloud_file = _cloud_file()
    job = _brightspace_job(cloud_file)
    credential = _brightspace_credential()
    credential.is_active = False
    db = MagicMock()
    db.get.side_effect = lambda model, *_args, **_kwargs: (
        cloud_file if model is CloudFile else credential
    )

    with (
        patch(
            "src.jobs.brightspace_content_job._provider_configuration_reference",
            return_value=job.payload["provider_configuration"],
        ),
        patch("src.api.brightspace_routes._bind_brightspace_clients") as bind,
    ):
        result = await _execute_brightspace_content_remediation_job(
            job, db, MagicMock()
        )

    _assert_complete_failure_envelope(result, "invalid_job_scope")
    bind.assert_not_called()


@pytest.mark.asyncio
async def test_brightspace_worker_fences_commit_and_returns_bounded_result() -> None:
    from src.api.brightspace_routes import RemediationOutcome
    from src.jobs.brightspace_content_job import (
        _execute_brightspace_content_remediation_job,
    )

    cloud_file = _cloud_file()
    credential = _brightspace_credential()
    job = _brightspace_job(cloud_file)
    db = MagicMock()
    db.get.side_effect = lambda model, *_args, **_kwargs: (
        cloud_file if model is CloudFile else credential
    )
    db.execute.return_value.scalar_one_or_none.return_value = "job-1"
    api_client = SimpleNamespace(close=AsyncMock())

    async def execute(
        *_args,
        assert_owned,
        remediation_job_id,
        commit_changes,
        **_kwargs,
    ):
        assert remediation_job_id == "job-1"
        assert commit_changes is False
        await assert_owned()
        return RemediationOutcome(
            cloud_file_id="cloud-1",
            status="completed",
            fixed_count=3,
            manual_count=1,
            failed_count=0,
            skipped_count=2,
            has_remediated_version=True,
            artifact_id="artifact-1",
            providers=["sensitive-provider-detail"],
        )

    with (
        patch(
            "src.jobs.brightspace_content_job._provider_configuration_reference",
            return_value=job.payload["provider_configuration"],
        ),
        patch(
            "src.api.brightspace_routes._bind_brightspace_clients",
            return_value=(None, None, {"remediation": "not_requested"}),
        ),
        patch(
            "src.api.brightspace_routes._client_for_fresh_credential",
            new=AsyncMock(return_value=(credential, api_client)),
        ),
        patch("src.api.brightspace_routes._remediate_file", new=execute),
    ):
        result = await _execute_brightspace_content_remediation_job(
            job, db, MagicMock()
        )

    assert result == {
        "success": True,
        "status": "completed",
        "scan_id": "scan-1",
        "fixed_count": 3,
        "manual_count": 1,
        "failed_count": 0,
        "skipped_count": 2,
        "download_available": True,
        "ai_used": False,
        "external_ai_used": False,
        "providers": [],
        "purpose_decisions": {},
        "artifact_id": "artifact-1",
    }
    assert job._assert_owned.await_count >= 2
    api_client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_brightspace_worker_fence_rejects_source_drift_at_commit() -> None:
    from src.jobs.contracts import LostJobOwnership
    from src.jobs.brightspace_content_job import (
        _execute_brightspace_content_remediation_job,
    )

    cloud_file = _cloud_file()
    credential = _brightspace_credential()
    job = _brightspace_job(cloud_file)
    db = MagicMock()
    db.get.side_effect = lambda model, *_args, **_kwargs: (
        cloud_file if model is CloudFile else credential
    )
    api_client = SimpleNamespace(close=AsyncMock())

    async def execute(*_args, assert_owned, **_kwargs):
        cloud_file.provider_version = "changed-during-execution"
        await assert_owned()

    with (
        patch(
            "src.jobs.brightspace_content_job._provider_configuration_reference",
            return_value=job.payload["provider_configuration"],
        ),
        patch(
            "src.api.brightspace_routes._bind_brightspace_clients",
            return_value=(None, None, {}),
        ),
        patch(
            "src.api.brightspace_routes._client_for_fresh_credential",
            new=AsyncMock(return_value=(credential, api_client)),
        ),
        patch("src.api.brightspace_routes._remediate_file", new=execute),
    ):
        with pytest.raises(LostJobOwnership):
            await _execute_brightspace_content_remediation_job(job, db, MagicMock())

    api_client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_brightspace_worker_fence_rejects_provider_configuration_drift() -> None:
    from src.jobs.contracts import LostJobOwnership
    from src.jobs.brightspace_content_job import (
        _execute_brightspace_content_remediation_job,
    )

    cloud_file = _cloud_file()
    credential = _brightspace_credential()
    job = _brightspace_job(cloud_file)
    changed = {**job.payload["provider_configuration"], "configuration_revision": 4}
    db = MagicMock()
    db.get.side_effect = lambda model, *_args, **_kwargs: (
        cloud_file if model is CloudFile else credential
    )
    api_client = SimpleNamespace(close=AsyncMock())

    async def execute(*_args, assert_owned, **_kwargs):
        await assert_owned()

    with (
        patch(
            "src.jobs.brightspace_content_job._provider_configuration_reference",
            side_effect=[job.payload["provider_configuration"], changed],
        ),
        patch(
            "src.api.brightspace_routes._bind_brightspace_clients",
            return_value=(None, None, {}),
        ),
        patch(
            "src.api.brightspace_routes._client_for_fresh_credential",
            new=AsyncMock(return_value=(credential, api_client)),
        ),
        patch("src.api.brightspace_routes._remediate_file", new=execute),
    ):
        with pytest.raises(LostJobOwnership):
            await _execute_brightspace_content_remediation_job(job, db, MagicMock())


@pytest.mark.asyncio
async def test_brightspace_handler_cancellation_reaps_killable_process_group(
    tmp_path: Path,
) -> None:
    from src.jobs.brightspace_content_job import (
        handle_brightspace_content_remediation_job,
    )
    from src.jobs.local_scan_subprocess import _run_process

    started = tmp_path / "brightspace-started"
    late = tmp_path / "brightspace-late"
    job = _brightspace_job(_cloud_file())

    async def run_child(_job):
        code = (
            "import pathlib,time;"
            f"pathlib.Path({str(started)!r}).write_text('started');"
            "time.sleep(1);"
            f"pathlib.Path({str(late)!r}).write_text('late')"
        )
        await _run_process(
            (sys.executable, "-c", code),
            timeout_seconds=None,
            termination_grace_seconds=0.1,
        )
        return {"success": True}

    with patch(
        "src.jobs.brightspace_content_job._run_brightspace_subprocess",
        new=run_child,
    ):
        task = asyncio.create_task(
            handle_brightspace_content_remediation_job(job, MagicMock(), MagicMock())
        )
        for _ in range(200):
            if started.exists():
                break
            await asyncio.sleep(0.01)
        assert started.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.sleep(1.1)
    assert not late.exists()


def test_brightspace_domain_and_queue_terminal_commit_is_single_use() -> None:
    from src.jobs.brightspace_content_job import _commit_terminal_outcome
    from src.jobs.contracts import LostJobOwnership

    job = _brightspace_job(_cloud_file())
    job.completed_at = None
    job.progress = 20
    job.progress_message = "Remediating content"
    job.result_data = None
    job.error_message = None
    job.last_error_code = None
    job.last_error_retryable = None
    job.claimed_at = datetime.now(timezone.utc)
    job.heartbeat_at = datetime.now(timezone.utc)
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    job.updated_at = datetime.now(timezone.utc)
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.side_effect = [job, None]
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

    _commit_terminal_outcome(db, job, outcome)

    assert job.status == "completed"
    assert job.result_data == outcome
    assert job.claim_token is None
    assert job.worker_id is None
    db.commit.assert_called_once_with()
    with pytest.raises(LostJobOwnership):
        _commit_terminal_outcome(db, job, outcome)
    db.commit.assert_called_once_with()


@pytest.mark.asyncio
async def test_brightspace_parent_recovers_committed_outcome_after_response_loss() -> (
    None
):
    from src.jobs.brightspace_content_job import (
        handle_brightspace_content_remediation_job,
    )
    from src.jobs.remediation_job import RemediationJobHandledResult

    job = _brightspace_job(_cloud_file())
    terminal = SimpleNamespace(
        status="completed",
        claim_token=None,
        worker_id=None,
        result_data={
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
        },
    )
    db = MagicMock()
    db.get.return_value = terminal
    with patch(
        "src.jobs.brightspace_content_job._run_brightspace_subprocess",
        new=AsyncMock(
            return_value={"success": False, "error_code": "remediation_failed"}
        ),
    ):
        result = await handle_brightspace_content_remediation_job(job, db, MagicMock())

    assert isinstance(result, RemediationJobHandledResult)
    assert result["success"] is True
    assert result["fixed_count"] == 1
    db.expire_all.assert_called_once_with()


@pytest.mark.asyncio
async def test_brightspace_policy_failure_is_complete_public_envelope() -> None:
    from fastapi import HTTPException

    from src.jobs.brightspace_content_job import (
        _execute_brightspace_content_remediation_job,
    )

    cloud_file = _cloud_file()
    credential = _brightspace_credential()
    job = _brightspace_job(cloud_file)
    db = MagicMock()
    db.get.side_effect = lambda model, *_args, **_kwargs: (
        cloud_file if model is CloudFile else credential
    )
    with (
        patch(
            "src.jobs.brightspace_content_job._provider_configuration_reference",
            return_value=job.payload["provider_configuration"],
        ),
        patch(
            "src.api.brightspace_routes._bind_brightspace_clients",
            side_effect=HTTPException(status_code=403, detail="denied"),
        ),
    ):
        result = await _execute_brightspace_content_remediation_job(
            job, db, MagicMock()
        )

    assert result["status"] == "manual_required"
    assert result["manual_count"] == 1
    assert result["failed_count"] == 0
    assert result["purpose_decisions"] == {"remediation": "denied_at_dispatch"}
    assert result["error_code"] == "policy_not_permitted"
    for field in (
        "fixed_count",
        "skipped_count",
        "download_available",
        "ai_used",
        "external_ai_used",
        "providers",
    ):
        assert field in result


@pytest.mark.asyncio
async def test_brightspace_transport_failure_is_complete_public_envelope(
    monkeypatch,
) -> None:
    from src.jobs.brightspace_content_job import _run_brightspace_subprocess

    monkeypatch.setattr(
        "src.jobs.local_scan_subprocess._run_process",
        AsyncMock(return_value=1),
    )
    result = await _run_brightspace_subprocess(_brightspace_job(_cloud_file()))

    assert result == {
        "success": False,
        "status": "failed",
        "fixed_count": 0,
        "manual_count": 0,
        "failed_count": 1,
        "skipped_count": 0,
        "download_available": False,
        "ai_used": None,
        "external_ai_used": None,
        "providers": None,
        "purpose_decisions": None,
        "error_code": "remediation_failed",
    }


def test_brightspace_timeout_persists_complete_public_terminal_envelope() -> None:
    from src.jobs.contracts import JobFailure
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    job = _brightspace_job(_cloud_file())
    claim = ClaimedJob("job-1", "remediate", job.payload, "claim-1", "worker-1", 1, 0)
    values = JobProcessor()._finish_values(
        claim,
        JobFailure.retryable("job_execution_timeout"),
        external_effect_state=None,
    )

    assert values["status"] == "failed"
    assert values["last_error_code"] == "job_execution_timeout"
    envelope = values["result_data"]
    assert envelope["status"] == "failed"
    assert envelope["failed_count"] == 1
    assert envelope["download_available"] is False
    assert envelope["ai_used"] is None
    assert envelope["external_ai_used"] is None
    assert envelope["providers"] is None
    assert envelope["purpose_decisions"] is None


def test_public_job_result_preserves_explicit_unknown_usage_contract() -> None:
    from src.jobs.contracts import public_job_result

    assert public_job_result(
        {
            "status": "failed",
            "ai_used": None,
            "external_ai_used": None,
            "providers": None,
            "purpose_decisions": None,
        }
    ) == {
        "status": "failed",
        "ai_used": None,
        "external_ai_used": None,
        "providers": None,
        "purpose_decisions": None,
    }


@pytest.mark.asyncio
async def test_remediation_registry_dispatches_brightspace_execution() -> None:
    from src.jobs.remediation_job import handle_remediation_job

    job = _brightspace_job(_cloud_file())
    expected = {"success": True, "scan_id": "scan-1"}
    with patch(
        "src.jobs.brightspace_content_job.handle_brightspace_content_remediation_job",
        new=AsyncMock(return_value=expected),
    ) as handler:
        result = await handle_remediation_job(job, MagicMock(), MagicMock())

    assert result == expected
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_registry_reaches_specialized_brightspace_handler() -> None:
    from src.jobs.contracts import JobContext, JobSuccess
    from src.jobs.registry import build_default_registry

    job = _brightspace_job(_cloud_file())
    db = MagicMock()
    db.get.return_value = job
    context = JobContext(
        job_id="job-1",
        job_type="remediate",
        payload=job.payload,
        claim_token="claim-1",
        worker_id="worker-1",
        attempt_count=1,
        report_progress=AsyncMock(return_value=True),
        assert_owned=AsyncMock(),
        begin_external_effect=AsyncMock(),
    )
    registry_handler = build_default_registry().get("remediate")
    assert registry_handler is not None
    with patch(
        "src.jobs.brightspace_content_job.handle_brightspace_content_remediation_job",
        new=AsyncMock(return_value={"success": True, "scan_id": "scan-1"}),
    ) as handler:
        result = await registry_handler(context, db, MagicMock())

    assert isinstance(result, JobSuccess)
    assert result.result == {"success": True, "scan_id": "scan-1"}
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_brightspace_status_url_resolves_bounded_terminal_result() -> None:
    from src.api.brightspace_routes import get_brightspace_remediation_job

    cloud_file = _cloud_file()
    job = _brightspace_job(cloud_file)
    job.status = "completed"
    job.progress = 100
    job.result_data = {
        "fixed_count": 2,
        "manual_count": 0,
        "failed_count": 0,
        "artifact_id": "artifact-1",
        "status": "no_op",
        "ai_used": True,
        "external_ai_used": True,
        "providers": ["gemini"],
        "purpose_decisions": {"remediation": "used"},
        "internal_secret": "must-not-escape",
    }
    job.last_error_code = None
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = job
    db = MagicMock()
    db.query.return_value = query
    with patch(
        "src.api.brightspace_routes._get_authorized_cloud_file_or_404",
        return_value=cloud_file,
    ):
        result = await get_brightspace_remediation_job(
            "cloud-1", "job-1", principal=_principal(), db=db
        )

    assert result.status == "completed"
    assert result.fixed_count == 2
    assert result.artifact_id == "artifact-1"
    assert result.outcome_status == "no_op"
    assert result.model_dump()["outcome_status"] == "no_op"
    assert "result_status" not in result.model_dump()
    assert result.ai_used is True
    assert result.external_ai_used is True
    assert result.providers == ["gemini"]
    assert result.purpose_decisions == {"remediation": "used"}
    assert "secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_brightspace_status_failure_is_complete_client_contract() -> None:
    from src.api.brightspace_routes import get_brightspace_remediation_job

    cloud_file = _cloud_file()
    job = _brightspace_job(cloud_file)
    job.status = "failed"
    job.progress = 0
    job.result_data = {
        "status": "manual_required",
        "fixed_count": 0,
        "manual_count": 1,
        "failed_count": 0,
        "skipped_count": 0,
        "download_available": False,
        "ai_used": False,
        "external_ai_used": False,
        "providers": [],
        "purpose_decisions": {"remediation": "denied_at_dispatch"},
    }
    job.last_error_code = "policy_not_permitted"
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = job
    db = MagicMock()
    db.query.return_value = query
    with patch(
        "src.api.brightspace_routes._get_authorized_cloud_file_or_404",
        return_value=cloud_file,
    ):
        result = await get_brightspace_remediation_job(
            "cloud-1", "job-1", principal=_principal(), db=db
        )

    assert result.model_dump() == {
        "job_id": "job-1",
        "cloud_file_id": "cloud-1",
        "status": "failed",
        "status_url": "/brightspace/content/cloud-1/remediation/jobs/job-1",
        "progress": 0,
        "progress_message": "Failed",
        "error_code": "policy_not_permitted",
        "fixed_count": 0,
        "manual_count": 1,
        "failed_count": 0,
        "skipped_count": 0,
        "download_available": False,
        "outcome_status": "manual_required",
        "ai_used": False,
        "external_ai_used": False,
        "providers": [],
        "purpose_decisions": {"remediation": "denied_at_dispatch"},
        "artifact_id": None,
    }


@pytest.mark.asyncio
async def test_brightspace_status_preserves_unknown_failure_usage() -> None:
    from src.api.brightspace_routes import get_brightspace_remediation_job

    cloud_file = _cloud_file()
    job = _brightspace_job(cloud_file)
    job.status = "failed"
    job.progress = 0
    job.result_data = {
        "status": "failed",
        "fixed_count": 0,
        "manual_count": 0,
        "failed_count": 1,
        "skipped_count": 0,
        "download_available": False,
        "ai_used": None,
        "external_ai_used": None,
        "providers": None,
        "purpose_decisions": None,
    }
    job.last_error_code = "job_execution_timeout"
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = job
    db = MagicMock()
    db.query.return_value = query
    with patch(
        "src.api.brightspace_routes._get_authorized_cloud_file_or_404",
        return_value=cloud_file,
    ):
        result = await get_brightspace_remediation_job(
            "cloud-1", "job-1", principal=_principal(), db=db
        )

    wire = result.model_dump()
    assert wire["outcome_status"] == "failed"
    assert wire["ai_used"] is None
    assert wire["external_ai_used"] is None
    assert wire["providers"] is None
    assert wire["purpose_decisions"] is None


def test_self_hosting_documents_worker_operations_and_rollback() -> None:
    guide = (
        Path(__file__).parents[1] / "docs" / "deployment" / "self-hosting.md"
    ).read_text()

    for heading in (
        "## API and worker topology",
        "### Resource limits",
        "### Worker recovery",
        "### Worker rollback",
    ):
        assert heading in guide
    for command in (
        "python -m src.jobs.worker",
        "src.jobs.healthcheck",
        "/api/jobs/worker-status",
        "docker compose -f docker-compose.prod.yml stop worker",
    ):
        assert command in guide


def test_compose_workers_bound_resources_and_execution_time() -> None:
    import yaml

    root = Path(__file__).parents[1]
    for name in (
        "docker-compose.prod.yml",
        "docker-compose.quickstart.yml",
        "docker-compose.dev.yml",
    ):
        compose = yaml.safe_load((root / name).read_text())
        worker = compose["services"]["worker"]
        assert worker["cpus"] == "${JOB_WORKER_CPUS:-0.75}"
        assert worker["mem_limit"]
        assert worker["pids_limit"]
        assert worker["environment"]["JOB_WORKER_MAX_EXECUTION_SECONDS"]


def test_ci_executes_postgres_concurrency_suite_on_explicit_test_database() -> None:
    import yaml

    from conftest import require_disposable_postgres_url

    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    environment = workflow["jobs"]["test"]["steps"][-1]["env"]
    durable_url = environment["TEST_MIGRATION_DATABASE_URL"]

    assert durable_url == environment["DATABASE_URL"]
    assert environment["ALLOW_DESTRUCTIVE_MIGRATION_TESTS"] == "1"
    assert (
        require_disposable_postgres_url(
            durable_url,
            destructive=True,
            environment={"ALLOW_DESTRUCTIVE_MIGRATION_TESTS": "1"},
        )
        == durable_url
    )


def test_ci_runs_kernel_enforced_worker_saturation_gate() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text()
    gate = Path("scripts/verify_worker_cpu_isolation.py").read_text()

    assert "verify_worker_cpu_isolation.py --image" in workflow
    assert '"--cpus",\n                "0.75"' in gate
    assert '"--cpuset-cpus"' in gate
    assert '("/auth/health", 200)' in gate
    assert "ClaimedJob" in gate


def test_fixed_worker_id_restart_resets_lifetime_progress_counters() -> None:
    from src.jobs.job_processor import JobProcessor

    old_started = datetime.now(timezone.utc) - timedelta(hours=1)
    heartbeat = SimpleNamespace(
        status="running",
        started_at=old_started,
        heartbeat_at=old_started,
        stopped_at=None,
        jobs_claimed=10,
        jobs_completed=8,
        jobs_failed=2,
        metadata_json={"instance_id": "prior-process"},
    )
    db = MagicMock()
    db.get.return_value = heartbeat
    db.scalar.return_value = 1
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    processor = JobProcessor(worker_id="fixed-worker", session_factory=factory)

    processor._set_worker_state("running")

    assert heartbeat.started_at > old_started
    assert heartbeat.jobs_claimed == 0
    assert heartbeat.jobs_completed == 0
    assert heartbeat.jobs_failed == 0
    assert heartbeat.metadata_json["instance_id"] == processor.instance_id
    heartbeat.jobs_claimed = 1
    processor._set_worker_state("running")
    assert heartbeat.jobs_claimed == 1


def test_worker_health_state_requires_recent_progress_for_active_work() -> None:
    from src.api.job_worker_routes import _worker_health_state

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=2)
    assert (
        _worker_health_state(
            live_workers=0,
            runnable_pending=1,
            processing_count=0,
            expired_processing=0,
            stalled_processing=0,
            latest_progress=None,
            cutoff=cutoff,
        )
        == "worker_unavailable"
    )
    assert (
        _worker_health_state(
            live_workers=1,
            runnable_pending=0,
            processing_count=0,
            expired_processing=0,
            stalled_processing=0,
            latest_progress=None,
            cutoff=cutoff,
        )
        == "healthy_idle"
    )
    assert (
        _worker_health_state(
            live_workers=1,
            runnable_pending=0,
            processing_count=1,
            expired_processing=0,
            stalled_processing=0,
            latest_progress=now,
            cutoff=cutoff,
        )
        == "healthy_processing"
    )
    assert (
        _worker_health_state(
            live_workers=1,
            runnable_pending=1,
            processing_count=0,
            expired_processing=0,
            stalled_processing=0,
            latest_progress=cutoff - timedelta(seconds=1),
            cutoff=cutoff,
        )
        == "stuck_runnable_backlog"
    )
    assert (
        _worker_health_state(
            live_workers=1,
            runnable_pending=1,
            processing_count=0,
            expired_processing=0,
            stalled_processing=0,
            latest_progress=now,
            cutoff=cutoff,
        )
        == "healthy_advancing"
    )
    assert (
        _worker_health_state(
            live_workers=1,
            runnable_pending=0,
            processing_count=1,
            expired_processing=0,
            stalled_processing=0,
            latest_progress=cutoff - timedelta(seconds=1),
            cutoff=cutoff,
        )
        == "healthy_processing"
    )
    assert (
        _worker_health_state(
            live_workers=1,
            runnable_pending=0,
            processing_count=1,
            expired_processing=0,
            stalled_processing=1,
            latest_progress=now,
            cutoff=cutoff,
        )
        == "stuck_processing"
    )
    assert (
        _worker_health_state(
            live_workers=1,
            runnable_pending=0,
            processing_count=1,
            expired_processing=1,
            stalled_processing=0,
            latest_progress=now,
            cutoff=cutoff,
        )
        == "expired_lease"
    )


def test_container_healthcheck_fails_live_worker_with_stalled_runnable_backlog() -> (
    None
):
    from src.jobs import healthcheck

    now = datetime.now(timezone.utc)
    heartbeat = SimpleNamespace(
        status="running",
        heartbeat_at=now,
        metadata_json={
            "progress_watermark_at": (now - timedelta(minutes=3)).isoformat()
        },
    )
    db = MagicMock()
    db.get.return_value = heartbeat
    db.scalar.return_value = 1
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = SimpleNamespace(id="job-1")
    query.all.return_value = []
    db.query.return_value = query
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = db
    with (
        patch.dict(os.environ, {"JOB_WORKER_ID": "worker-1"}),
        patch("src.jobs.healthcheck.SessionLocal", session_factory),
        pytest.raises(SystemExit) as exit_status,
    ):
        healthcheck.main()
    assert exit_status.value.code == 1


def test_container_healthcheck_accepts_long_job_with_current_lease() -> None:
    from src.jobs import healthcheck

    now = datetime.now(timezone.utc)
    heartbeat = SimpleNamespace(
        status="running",
        heartbeat_at=now,
        metadata_json={
            "progress_watermark_at": (now - timedelta(minutes=10)).isoformat()
        },
    )
    active = SimpleNamespace(
        id="job-1",
        claimed_at=now - timedelta(minutes=10),
        lease_expires_at=now + timedelta(seconds=90),
    )
    db = MagicMock()
    db.get.return_value = heartbeat
    db.scalar.return_value = 0
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = None
    query.all.return_value = [active]
    db.query.return_value = query
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = db
    with (
        patch.dict(os.environ, {"JOB_WORKER_ID": "worker-1"}),
        patch("src.jobs.healthcheck.SessionLocal", session_factory),
        pytest.raises(SystemExit) as exit_status,
    ):
        healthcheck.main()
    assert exit_status.value.code == 0


@pytest.mark.parametrize("queue_status", ["pending", "processing"])
def test_api_timeout_never_overrides_queue_owned_scan_lifecycle(
    queue_status: str,
) -> None:
    from src.jobs.scan_timeout import fail_stale_scans

    old = datetime.now(timezone.utc) - timedelta(hours=2)
    scan = SimpleNamespace(
        id="scan-1",
        status=ScanStatus.PROCESSING,
        created_at=old,
        completed_at=None,
        error_message=None,
        progress_message=None,
    )
    queue_job = SimpleNamespace(
        status=queue_status,
        payload={"scan_id": "scan-1", "scan_kind": "local_pdf"},
        lease_expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=5)
            if queue_status == "processing"
            else None
        ),
    )
    scan_query = MagicMock()
    scan_query.filter.return_value = scan_query
    scan_query.order_by.return_value = scan_query
    scan_query.limit.return_value = scan_query
    scan_query.all.return_value = [scan]
    db = MagicMock()
    db.scalars.return_value = scan_query
    db.scalar.return_value = str(queue_job.payload["scan_id"])
    context = MagicMock()
    context.__enter__.return_value = db
    with patch("src.jobs.scan_timeout.get_db", return_value=context):
        assert fail_stale_scans() == 0

    assert scan.status is ScanStatus.PROCESSING
    db.commit.assert_not_called()


def test_api_timeout_reconciles_terminal_queue_scan_disagreement() -> None:
    from src.jobs.scan_timeout import fail_stale_scans

    old = datetime.now(timezone.utc) - timedelta(hours=2)
    scan = SimpleNamespace(
        id="scan-1",
        status=ScanStatus.PROCESSING,
        created_at=old,
        completed_at=None,
        error_message=None,
        progress_message=None,
        file_name="large.pdf",
        user_id="user-1",
        department_id="dept-1",
    )
    terminal_job = SimpleNamespace(status="completed", last_error_code=None)
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = [scan]
    db = MagicMock()
    db.scalars.return_value = query
    db.scalar.side_effect = [None, terminal_job]
    context = MagicMock()
    context.__enter__.return_value = db

    with patch("src.jobs.scan_timeout.get_db", return_value=context):
        assert fail_stale_scans() == 1

    assert scan.status is ScanStatus.FAILED
    assert scan.error_message == "scan_queue_terminal_disagreement"
    assert scan.progress_message == "Scan failed"
    db.commit.assert_called_once_with()


def test_scan_timeout_query_is_bounded_and_excludes_only_active_queue_owners() -> None:
    from sqlalchemy.dialects import postgresql

    from src.jobs.scan_timeout import build_stale_scan_query

    query = build_stale_scan_query(datetime.now(timezone.utc), limit=100)
    sql = str(
        query.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()

    assert "NOT (EXISTS" in sql
    assert "CLOUD_JOB_QUEUE.STATUS IN ('PENDING', 'PROCESSING')" in sql
    assert "CLOUD_JOB_QUEUE.PAYLOAD ->> 'SCAN_ID'" in sql
    assert "LIMIT 100" in sql
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql


def test_worker_reaper_owns_expired_local_scan_terminal_transition() -> None:
    from src.jobs.job_processor import JobProcessor

    scan = SimpleNamespace(
        id="scan-1",
        status=ScanStatus.PROCESSING,
        completed_at=None,
        error_message=None,
        progress_message=None,
    )
    job = SimpleNamespace(
        id="job-1",
        job_type="scan",
        department_id="dept-1",
        payload={"scan_id": "scan-1", "scan_kind": "local_pdf"},
        external_effect_state=None,
        attempt_count=1,
        max_retries=1,
        scheduled_for=datetime.now(timezone.utc),
        status="processing",
        completed_at=None,
        error_message=None,
        last_error_code=None,
        last_error_retryable=None,
        result_data=None,
        progress=10,
        progress_message=None,
        claim_token="claim-1",
        worker_id="dead-worker",
        claimed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [job]
    db.scalar.side_effect = [job, scan]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    worker = JobProcessor(worker_id="new-worker", session_factory=factory)

    with patch(
        "src.jobs.execution_authority.try_acquire_recovery_lock",
        return_value=True,
    ):
        assert worker.reap_stale_jobs() == 1
    assert job.status == "failed"
    assert job.claim_token is None
    assert scan.status is ScanStatus.FAILED
    assert scan.error_message == "job_lease_expired"
    db.commit.assert_called_once_with()


def test_cancellation_ack_preserves_scan_until_every_processing_job_stops() -> None:
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    scan = SimpleNamespace(id="scan-1", result=None)
    job = SimpleNamespace(
        id="job-1",
        department_id="dept-1",
        status="processing",
        completed_at=None,
        progress=10,
        progress_message=None,
        result_data=None,
        error_message="scan_cancel_requested",
        last_error_code="scan_cancel_requested",
        last_error_retryable=None,
        claim_token="claim-1",
        worker_id="worker-1",
        claimed_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    db = MagicMock()
    db.scalar.side_effect = [job, scan, "sibling-job"]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    worker = JobProcessor(worker_id="worker-1", session_factory=factory)
    claim = ClaimedJob(
        "job-1",
        "scan",
        {"scan_id": "scan-1"},
        "claim-1",
        "worker-1",
        1,
        1,
    )

    assert worker._acknowledge_cancellation(claim) is True
    assert job.status == "failed"
    assert job.last_error_code == "scan_cancelled"
    assert job.claim_token is None
    db.delete.assert_not_called()


def test_last_cancellation_ack_cleans_artifacts_after_handler_teardown() -> None:
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    scan = SimpleNamespace(id="scan-1", result=None)
    job = SimpleNamespace(
        id="job-1",
        department_id="dept-1",
        status="processing",
        completed_at=None,
        progress=10,
        progress_message=None,
        result_data=None,
        error_message="scan_cancel_requested",
        last_error_code="scan_cancel_requested",
        last_error_retryable=None,
        claim_token="claim-1",
        worker_id="worker-1",
        claimed_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    db = MagicMock()
    db.scalar.side_effect = [job, scan, None]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    worker = JobProcessor(worker_id="worker-1", session_factory=factory)
    claim = ClaimedJob(
        "job-1",
        "scan",
        {"scan_id": "scan-1"},
        "claim-1",
        "worker-1",
        1,
        1,
    )
    with patch(
        "src.services.remediation_artifact_service.RemediationArtifactService.from_settings"
    ) as artifact_service:
        assert worker._acknowledge_cancellation(claim) is True

    artifact_service.return_value.delete_for_scan.assert_called_once_with(
        db, department_id="dept-1", scan_id="scan-1"
    )
    db.delete.assert_called_once_with(scan)


def test_cancellation_ack_never_deletes_foreign_tenant_scan_or_artifacts() -> None:
    from src.jobs.job_processor import ClaimedJob, JobProcessor

    foreign_scan = SimpleNamespace(
        id="foreign-scan",
        department_id="dept-b",
        result=SimpleNamespace(id="foreign-result"),
    )
    job = SimpleNamespace(
        id="job-1",
        department_id="dept-a",
        status="processing",
        completed_at=None,
        progress=10,
        progress_message=None,
        result_data=None,
        error_message="scan_cancel_requested",
        last_error_code="scan_cancel_requested",
        last_error_retryable=None,
        claim_token="claim-1",
        worker_id="worker-1",
        claimed_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    db = MagicMock()
    db.scalar.side_effect = [job, None]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    worker = JobProcessor(worker_id="worker-1", session_factory=factory)
    claim = ClaimedJob(
        "job-1",
        "scan",
        {"scan_id": "foreign-scan"},
        "claim-1",
        "worker-1",
        1,
        1,
    )

    with patch(
        "src.services.remediation_artifact_service.RemediationArtifactService.from_settings"
    ) as artifact_service:
        assert worker._acknowledge_cancellation(claim) is True

    assert job.last_error_code == "scan_cancelled"
    db.delete.assert_not_called()
    artifact_service.assert_not_called()


def test_reaper_keeps_expired_cancellation_nonterminal_without_reap_proof() -> None:
    from src.jobs.job_processor import JobProcessor

    scan = SimpleNamespace(id="scan-1", result=None)
    job = SimpleNamespace(
        id="job-1",
        job_type="scan",
        department_id="dept-1",
        payload={"scan_id": "scan-1", "scan_kind": "local_pdf"},
        external_effect_state=None,
        attempt_count=1,
        max_retries=1,
        scheduled_for=datetime.now(timezone.utc),
        status="processing",
        completed_at=None,
        error_message="scan_cancel_requested",
        last_error_code="scan_cancel_requested",
        last_error_retryable=None,
        result_data=None,
        progress=10,
        progress_message=None,
        claim_token="claim-1",
        worker_id="dead-worker",
        claimed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [job]
    db.get.return_value = scan
    db.scalar.return_value = None
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    worker = JobProcessor(worker_id="new-worker", session_factory=factory)

    with patch(
        "src.jobs.execution_authority.try_acquire_recovery_lock",
        return_value=False,
    ):
        assert worker.reap_stale_jobs() == 0
    assert job.status == "processing"
    assert job.last_error_code == "scan_cancel_requested"
    assert job.claim_token == "claim-1"
    db.delete.assert_not_called()
    db.commit.assert_called_once_with()


def test_reaper_commits_cancellation_before_releasing_recovery_authority() -> None:
    from src.jobs.job_processor import JobProcessor

    job = SimpleNamespace(
        id="job-1",
        job_type="scan",
        department_id="dept-1",
        payload={"scan_kind": "local_pdf"},
        external_effect_state=None,
        attempt_count=1,
        max_retries=1,
        scheduled_for=datetime.now(timezone.utc),
        status="processing",
        completed_at=None,
        error_message="scan_cancel_requested",
        last_error_code="scan_cancel_requested",
        last_error_retryable=None,
        result_data=None,
        progress=10,
        progress_message=None,
        claim_token="claim-1",
        worker_id="dead-worker",
        claimed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [job]
    db.scalar.return_value = job
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    worker = JobProcessor(worker_id="new-worker", session_factory=factory)

    with patch(
        "src.jobs.execution_authority.try_acquire_recovery_lock",
        return_value=True,
    ):
        assert worker.reap_stale_jobs() == 1

    assert db.commit.call_count == 1
    assert job.status == "failed"
    assert job.last_error_code == "scan_cancelled"


def test_reaper_requires_child_authority_for_specialized_brightspace_jobs() -> None:
    from src.jobs.job_processor import JobProcessor

    assert JobProcessor._uses_child_execution_authority(
        SimpleNamespace(
            job_type="remediate",
            payload={"execution": "brightspace_content"},
        )
    )
    assert not JobProcessor._uses_child_execution_authority(
        SimpleNamespace(job_type="remediate", payload={})
    )


@pytest.mark.asyncio
async def test_scan_deletion_fences_pending_and_claimed_queue_jobs() -> None:
    from src.api.education.scan_history_routes import cancel_scan

    scan = SimpleNamespace(
        id="scan-1",
        status=ScanStatus.PROCESSING,
        result=None,
        department_id="dept-1",
        user_id="user-1",
    )
    pending = SimpleNamespace(
        payload={"scan_id": "scan-1"}, status="pending", result_data=None
    )
    claimed = SimpleNamespace(
        payload={"scan_id": "scan-1"},
        status="processing",
        result_data=None,
        claim_token="claim-1",
        worker_id="worker-1",
        claimed_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    scan_query = MagicMock()
    scan_query.filter.return_value = scan_query
    scan_query.first.return_value = scan
    jobs_query = MagicMock()
    jobs_query.filter.return_value = jobs_query
    jobs_query.with_for_update.return_value = jobs_query
    jobs_query.all.return_value = [pending, claimed]
    db = MagicMock()
    db.query.side_effect = [scan_query, jobs_query]
    with (
        patch("src.api.education.scan_history_routes.authorize_scan_access"),
        patch(
            "src.api.education.scan_history_routes.RemediationArtifactService.from_settings"
        ) as artifact_service,
    ):
        result = await cancel_scan("scan-1", db=db, principal=_principal())

    assert result["success"] is True
    artifact_service.return_value.delete_for_scan.assert_not_called()
    assert pending.status == "failed"
    assert pending.last_error_code == "scan_cancelled"
    assert claimed.status == "processing"
    assert claimed.last_error_code == "scan_cancel_requested"
    assert claimed.claim_token == "claim-1"
    assert claimed.worker_id == "worker-1"
    db.delete.assert_not_called()
    db.commit.assert_called_once_with()


@pytest.mark.asyncio
async def test_pending_only_scan_cancellation_cleans_up_without_worker_ack() -> None:
    from src.api.education.scan_history_routes import cancel_scan

    scan = SimpleNamespace(
        id="scan-1",
        status=ScanStatus.PENDING,
        result=None,
        department_id="dept-1",
        user_id="user-1",
    )
    pending = SimpleNamespace(
        payload={"scan_id": "scan-1"}, status="pending", result_data=None
    )
    scan_query = MagicMock()
    scan_query.filter.return_value = scan_query
    scan_query.first.return_value = scan
    jobs_query = MagicMock()
    jobs_query.filter.return_value = jobs_query
    jobs_query.with_for_update.return_value = jobs_query
    jobs_query.all.return_value = [pending]
    db = MagicMock()
    db.query.side_effect = [scan_query, jobs_query]
    with (
        patch("src.api.education.scan_history_routes.authorize_scan_access"),
        patch(
            "src.api.education.scan_history_routes.RemediationArtifactService.from_settings"
        ) as artifact_service,
    ):
        await cancel_scan("scan-1", db=db, principal=_principal())

    artifact_service.return_value.delete_for_scan.assert_called_once_with(
        db, department_id="dept-1", scan_id="scan-1"
    )
    assert pending.status == "failed"
    assert pending.last_error_code == "scan_cancelled"
    db.delete.assert_called_once_with(scan)


@pytest.mark.asyncio
async def test_real_cancellation_waits_for_child_reap_before_terminal_state(
    tmp_path: Path,
) -> None:
    pytest.skip("real database race is covered by the PostgreSQL integration matrix")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.db.models import Base, Department, WorkerHeartbeat
    from src.jobs.contracts import JobSuccess
    from src.jobs.job_processor import ClaimedJob, JobProcessor
    from src.jobs.local_scan_subprocess import _run_process
    from src.jobs.registry import JobRegistry

    database = tmp_path / "cancel-race.sqlite3"
    engine = create_engine(
        f"sqlite:///{database}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add(Department(id="dept-1", name="Test", institution="Test"))
        db.add(
            Scan(
                id="scan-1",
                department_id="dept-1",
                file_name="large.pdf",
                status=ScanStatus.PROCESSING,
            )
        )
        db.add(
            CloudJobQueue(
                id="job-1",
                department_id="dept-1",
                job_type="scan",
                payload={"scan_id": "scan-1"},
                dedupe_key="scan-1",
                status="processing",
                claim_token="claim-1",
                worker_id="worker-1",
                claimed_at=now,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=30),
                attempt_count=1,
                max_retries=1,
            )
        )
        db.add(
            WorkerHeartbeat(
                worker_id="worker-1",
                status="running",
                started_at=now,
                heartbeat_at=now,
                metadata_json={},
            )
        )
        db.commit()

    started = tmp_path / "race-started"
    late = tmp_path / "race-late"

    async def handler(_context, _db, _token_manager):
        code = (
            "import pathlib,time;"
            f"pathlib.Path({str(started)!r}).write_text('started');"
            "time.sleep(1);"
            f"pathlib.Path({str(late)!r}).write_text('late')"
        )
        await _run_process(
            (sys.executable, "-c", code),
            timeout_seconds=None,
            termination_grace_seconds=0.1,
        )
        return JobSuccess({"success": True, "scan_id": "scan-1"})

    registry = JobRegistry()
    registry.register("scan", handler)
    processor = JobProcessor(
        worker_id="worker-1",
        heartbeat_interval=0.01,
        lease_seconds=30,
        max_execution_seconds=5,
        session_factory=factory,
        registry=registry,
    )
    processor._token_manager = MagicMock()
    claim = ClaimedJob(
        "job-1", "scan", {"scan_id": "scan-1"}, "claim-1", "worker-1", 1, 1
    )
    task = asyncio.create_task(processor.process_claim(claim))
    for _ in range(200):
        if started.exists():
            break
        await asyncio.sleep(0.01)
    assert started.exists()

    with factory() as db:
        job = db.get(CloudJobQueue, "job-1")
        job.last_error_code = "scan_cancel_requested"
        job.error_message = "scan_cancel_requested"
        db.commit()
    await asyncio.sleep(0)
    with factory() as db:
        still_running = db.get(CloudJobQueue, "job-1")
        assert still_running.status == "processing"
        assert still_running.claim_token == "claim-1"

    assert await asyncio.wait_for(task, timeout=3) is True
    await asyncio.sleep(1.1)
    assert not late.exists()
    with factory() as db:
        terminal = db.get(CloudJobQueue, "job-1")
        assert terminal.status == "failed"
        assert terminal.last_error_code == "scan_cancelled"
        assert terminal.claim_token is None
        assert db.get(Scan, "scan-1") is None
    engine.dispose()


@pytest.mark.asyncio
async def test_killable_local_scan_process_cannot_write_after_cancellation(
    tmp_path: Path,
) -> None:
    from src.jobs.local_scan_subprocess import _run_process

    started = tmp_path / "started"
    late_effect = tmp_path / "late-effect"
    code = (
        "import pathlib,time;"
        f"pathlib.Path({str(started)!r}).write_text('started');"
        "time.sleep(1);"
        f"pathlib.Path({str(late_effect)!r}).write_text('too late')"
    )
    task = asyncio.create_task(
        _run_process(
            (sys.executable, "-c", code),
            timeout_seconds=10,
            termination_grace_seconds=0.1,
        )
    )
    for _ in range(100):
        if started.exists():
            break
        await asyncio.sleep(0.01)
    assert started.exists()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.1)

    assert not late_effect.exists()


@pytest.mark.asyncio
async def test_cpu_saturated_worker_process_leaves_api_probes_responsive(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.jobs.contracts import JobSuccess
    from src.jobs.job_processor import ClaimedJob, JobProcessor
    from src.jobs.local_scan_subprocess import _run_process
    from src.jobs.registry import JobRegistry

    started = tmp_path / "cpu-started"

    async def representative_scan(_context, _db, _tokens):
        nice = ("nice", "-n", "10") if sys.platform != "win32" else ()
        await _run_process(
            (
                *nice,
                sys.executable,
                "-c",
                "import pathlib;"
                f"pathlib.Path({str(started)!r}).write_text('started');"
                "value=0\nwhile True:\n value=(value+1)%1000003",
            ),
            timeout_seconds=None,
            termination_grace_seconds=0.1,
        )
        return JobSuccess()

    class ProbeProcessor(JobProcessor):
        def _owns_claim(self, _claim):
            return True

        def _cancellation_requested(self, _claim):
            return False

        def _fenced_update(self, _claim, _values):
            return True

        def _record_outcome(self, *, completed):
            pass

    registry = JobRegistry()
    registry.register("scan", representative_scan)
    processor = ProbeProcessor(
        registry=registry,
        session_factory=MagicMock(),
        max_execution_seconds=1,
        heartbeat_interval=60,
    )
    processor._token_manager = MagicMock()
    claim = ClaimedJob("job-1", "scan", {}, "claim-1", processor.worker_id, 1, 1)
    queued_worker = asyncio.create_task(processor.process_claim(claim))
    for _ in range(100):
        if started.exists():
            break
        await asyncio.sleep(0.01)
    assert started.exists()
    client = TestClient(app)
    probes = (
        ("/health", 200),
        ("/auth/health", 200),
        ("/api/jobs/worker-status", 401),
        ("/definitely-unrelated", 404),
    )
    for path, expected_status in probes:
        before = time.perf_counter()
        response = await asyncio.to_thread(client.get, path)
        elapsed = time.perf_counter() - before
        assert response.status_code == expected_status
        assert elapsed < 2.0
    assert await asyncio.wait_for(queued_worker, timeout=3) is True


@pytest.mark.skipif(sys.platform != "linux", reason="Linux CPU-affinity contract")
def test_separate_api_process_stays_responsive_at_worker_point_75_cpu(
    tmp_path: Path,
) -> None:
    import socket
    import urllib.error
    import urllib.request

    cpu = min(os.sched_getaffinity(0))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    started = tmp_path / "representative-worker-started"
    api = subprocess.Popen(
        (
            sys.executable,
            "-m",
            "uvicorn",
            "src.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "error",
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=lambda: os.sched_setaffinity(0, {cpu}),
    )
    worker_code = f"""
import asyncio,time,pathlib
from unittest.mock import MagicMock
from src.jobs.contracts import JobSuccess
from src.jobs.job_processor import ClaimedJob, JobProcessor
from src.jobs.local_scan_subprocess import _run_process
from src.jobs.registry import JobRegistry
async def heavy(_context, _db, _tokens):
    code = '''import pathlib,time
pathlib.Path({str(started)!r}).write_text("started")
end=time.monotonic()+3
while time.monotonic()<end:
    tick=time.monotonic()
    while time.monotonic()-tick<0.075:
        pass
    time.sleep(max(0,0.1-(time.monotonic()-tick)))'''
    await _run_process(({sys.executable!r}, '-c', code), timeout_seconds=5)
    return JobSuccess()
class Probe(JobProcessor):
    def _owns_claim(self, _claim): return True
    def _cancellation_requested(self, _claim): return False
    def _fenced_update(self, _claim, _values): return True
    def _finish(self, _claim, _result): return True
    def _record_outcome(self, *, completed): pass
registry=JobRegistry(); registry.register('scan', heavy)
worker=Probe(registry=registry, session_factory=MagicMock(), heartbeat_interval=60)
worker._token_manager=MagicMock()
claim=ClaimedJob('heavy-job','scan',{{}},'claim','worker',1,1)
raise SystemExit(0 if asyncio.run(worker.process_claim(claim)) else 1)
"""
    worker = None

    def response_code(path: str, timeout: float = 1.5) -> int:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=timeout
            ) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        for _ in range(200):
            if api.poll() is not None:
                pytest.fail("separate API process exited before readiness")
            try:
                if response_code("/health", timeout=0.2) == 200:
                    break
            except (OSError, TimeoutError):
                pass
            time.sleep(0.025)
        else:
            pytest.fail("separate API process did not become ready")
        worker = subprocess.Popen(
            (sys.executable, "-c", worker_code),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=lambda: (os.sched_setaffinity(0, {cpu}), os.nice(10)),
        )
        for _ in range(200):
            if started.exists():
                break
            if worker.poll() is not None:
                stdout, stderr = worker.communicate(timeout=1)
                pytest.fail(
                    "representative queued worker exited before saturation: "
                    f"{(stdout + stderr)[-2000:]}"
                )
            time.sleep(0.01)
        assert started.exists()
        for path, expected in (
            ("/health", 200),
            ("/auth/health", 200),
            ("/api/jobs/worker-status", 401),
            ("/definitely-unrelated", 404),
        ):
            before = time.perf_counter()
            assert response_code(path) == expected
            assert time.perf_counter() - before < 1.5
        assert worker.wait(timeout=8) == 0
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=3)
        if api.poll() is None:
            api.terminate()
            api.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux worker-container contract")
def test_local_scan_child_dies_with_hard_killed_worker_parent(tmp_path: Path) -> None:
    started = tmp_path / "parent-death-started"
    late_effect = tmp_path / "parent-death-late"
    grandchild = (
        "import pathlib,sys,time;"
        f"pathlib.Path({str(started)!r}).write_text('started');"
        "time.sleep(1);"
        f"pathlib.Path({str(late_effect)!r}).write_text('too late')"
    )
    child = (
        "import os,subprocess,sys,time;"
        "from src.jobs.local_scan_subprocess import _bind_parent_death;"
        "_bind_parent_death(int(sys.argv[1]));"
        f"subprocess.Popen([sys.executable,'-c',{grandchild!r}]);"
        "time.sleep(10)"
    )
    parent = (
        "import os,subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r},str(os.getpid())],start_new_session=True);"
        "time.sleep(10)"
    )
    owner = subprocess.Popen((sys.executable, "-c", parent))
    try:
        for _ in range(100):
            if started.exists():
                break
            time.sleep(0.01)
        assert started.exists()
        owner.kill()
        owner.wait(timeout=2)
        time.sleep(1.1)
        assert not late_effect.exists()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=2)
