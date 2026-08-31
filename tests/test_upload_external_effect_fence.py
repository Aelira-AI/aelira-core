"""Durable fence coverage for non-idempotent upload side effects."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import CheckConstraint

PROVIDERS = ("google", "microsoft", "blackboard")


def test_upload_external_effect_columns_and_constraints_are_dedicated():
    from src.db.models import CloudJobQueue

    columns = CloudJobQueue.__table__.columns
    assert columns.external_effect_state.nullable is True
    assert columns.external_effect_token.type.length == 36
    assert columns.external_effect_token.nullable is True
    assert columns.external_effect_started_at.nullable is True
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in CloudJobQueue.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert (
        "external_effect_state IN ('requesting', 'confirmed', 'indeterminate')"
        in checks["ck_cloud_job_queue_external_effect_state"]
    )
    pair = checks["ck_cloud_job_queue_external_effect_pair"]
    assert "external_effect_state IS NULL" in pair
    assert "external_effect_token IS NULL" in pair
    assert "external_effect_started_at IS NULL" in pair
    scope = checks["ck_cloud_job_queue_external_effect_owned"]
    assert "'upload'" in scope
    assert "'weekly_summary'" in scope


@pytest.mark.asyncio
async def test_job_context_exposes_external_effect_checkpoint():
    from src.jobs.contracts import JobContext

    begin = AsyncMock(return_value="11111111-1111-4111-8111-111111111111")
    claim_marker = "claim-1"
    context = JobContext(
        job_id="job-1",
        job_type="upload",
        payload={},
        claim_token=claim_marker,
        worker_id="worker-1",
        attempt_count=1,
        report_progress=AsyncMock(),
        begin_external_effect=begin,
    )

    assert await context.begin_external_effect() == begin.return_value
    begin.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_all_upload_providers_checkpoint_immediately_before_provider_call(
    provider, monkeypatch, tmp_path
):
    from src.jobs import upload_job

    path = tmp_path / "approved.docx"
    path.write_bytes(b"approved bytes")
    effect_marker = "22222222-2222-4222-8222-222222222222"
    events: list[tuple[str, str | None]] = []

    async def begin():
        events.append(("checkpoint", None))
        return effect_marker

    async def provider_call(**kwargs):
        events.append(("provider", kwargs.get("external_effect_token")))
        return {
            "success": True,
            "uploaded": True,
            "new_file_id": "remote-1",
            "new_file_name": "approved_remediated.docx",
            "provider": provider,
        }

    for name in PROVIDERS:
        monkeypatch.setattr(upload_job, f"_upload_to_{name}", AsyncMock())
    selected = AsyncMock(side_effect=provider_call)
    monkeypatch.setattr(upload_job, f"_upload_to_{provider}", selected)

    cloud_file = SimpleNamespace(
        id="file-1",
        credential_id="credential-1",
        provider_parent_id="folder-1",
        file_name="approved.docx",
        metadata={"course_id": "course-1"},
        has_remediated_version=False,
        remediated_file_id=None,
    )
    credential = SimpleNamespace(
        id="credential-1",
        provider_metadata={"blackboard_instance_url": "https://example.test"},
        canvas_instance_url="https://example.test",
    )
    query = MagicMock()
    query.filter.return_value.first.side_effect = [cloud_file, credential]
    db = MagicMock()
    db.query.return_value = query
    monkeypatch.setattr(
        upload_job.OAuthTokenManager,
        "refresh_if_expired",
        AsyncMock(return_value="access-token"),
    )
    result = await upload_job._process_upload_path(
        {
            "id": "job-1",
            "file_path": str(path),
            "cloud_file_id": "file-1",
            "department_id": "department-1",
            "provider": provider,
        },
        db,
        assert_owned=AsyncMock(),
        begin_external_effect=begin,
    )

    assert result["success"] is True
    assert events == [("checkpoint", None), ("provider", effect_marker)]
    selected.assert_awaited_once()


@pytest.mark.asyncio
async def test_canvas_generic_upload_is_rejected_before_external_effect(tmp_path):
    from src.jobs import upload_job

    path = tmp_path / "approved.pdf"
    path.write_bytes(b"approved bytes")
    begin = AsyncMock(return_value="effect-token")
    cloud_file = SimpleNamespace(
        id="file-1",
        credential_id="credential-1",
        provider_parent_id="course-1",
        file_name="approved.pdf",
    )
    query = MagicMock()
    query.filter.return_value.first.return_value = cloud_file
    db = MagicMock()
    db.query.return_value = query

    result = await upload_job._process_upload_path(
        {
            "id": "job-1",
            "file_path": str(path),
            "cloud_file_id": "file-1",
            "department_id": "department-1",
            "provider": "canvas",
        },
        db,
        assert_owned=AsyncMock(),
        begin_external_effect=begin,
    )

    assert result == {
        "success": False,
        "uploaded": False,
        "error": "Unsupported provider: canvas",
    }
    begin.assert_not_awaited()
    assert not hasattr(upload_job, "_upload_to_canvas")


def test_pre_request_upload_failure_remains_retryable():
    from src.jobs.contracts import JobFailure
    from src.jobs.job_processor import ClaimedJob, JobProcessor
    from src.jobs.registry import JobRegistry

    class CapturingProcessor(JobProcessor):
        def _external_effect_state(self, claim):
            return None

        def _fenced_update(self, claim, values):
            self.values = values
            return True

    worker = CapturingProcessor(registry=JobRegistry())
    claim = ClaimedJob("job-1", "upload", {}, "claim-1", worker.worker_id, 1, 3)
    assert worker._finish(
        claim, JobFailure.retryable("artifact_temporarily_unavailable")
    )
    assert worker.values["status"] == "pending"
