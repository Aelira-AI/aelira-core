"""Human approval must survive enqueue, materialization, and the effect fence."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs import remediation_job, upload_job
from src.jobs.contracts import LostJobOwnership
from src.jobs.job_processor import ClaimedJob, JobProcessor
from src.services.remediation_artifact_service import (
    ArtifactAuthorizationError,
    RemediationArtifactService,
)


def _approved_graph(provider="google"):
    artifact = SimpleNamespace(
        id="artifact-1",
        department_id="department-1",
        cloud_file_id="file-1",
        scan_id="scan-1",
        remediation_job_id="remediation-1",
        provider=provider,
        lifecycle_status="available",
        review_status="approved",
        sha256="a" * 64,
        approval_checksum="a" * 64,
        approval_review_digest="b" * 64,
        approved_by_ref="session:reviewer-1",
        approved_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        written_back_at=None,
    )
    cloud = SimpleNamespace(
        id=artifact.cloud_file_id,
        department_id=artifact.department_id,
        credential_id="credential-1",
        provider=provider,
        provider_file_id="remote-1",
        provider_parent_id="folder-1",
        provider_version="version-1",
        provider_modified_at=None,
        file_name="source.pdf",
        last_scan_id=artifact.scan_id,
        needs_rescan=False,
        current_remediation_artifact_id=artifact.id,
    )
    scan = SimpleNamespace(
        id=artifact.scan_id,
        department_id=artifact.department_id,
        document_source="cloud_file",
        document_id=cloud.id,
    )
    job = SimpleNamespace(
        id="upload-1",
        job_type="upload",
        department_id=artifact.department_id,
        cloud_file_id=cloud.id,
        credential_id=cloud.credential_id,
        provider=provider,
        payload={
            "artifact_id": artifact.id,
            "scan_id": artifact.scan_id,
            **upload_job.upload_approval_snapshot(artifact),
            **upload_job.upload_source_snapshot(cloud, scan),
        },
        external_effect_state=None,
        scan=scan,
    )
    return artifact, cloud, job


def _processor(monkeypatch, artifact, cloud, job):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.scalar.return_value = job
    db.get.side_effect = lambda model, *_args, **_kwargs: (
        artifact
        if model.__name__ == "RemediationArtifact"
        else job.scan if model.__name__ == "Scan" else cloud
    )
    service = MagicMock()
    service.lock_current.return_value = (None, job.scan, cloud, None, artifact)
    monkeypatch.setattr(RemediationArtifactService, "from_settings", lambda: service)
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    processor = JobProcessor(session_factory=factory, registry=MagicMock())
    claim = ClaimedJob(job.id, "upload", job.payload, "claim-1", "worker-1", 1, 3)
    return processor, claim, db, service


@pytest.mark.parametrize("provider", ("google", "microsoft", "blackboard"))
@pytest.mark.parametrize("remediation_job_id", ["remediation-1", None])
def test_explicit_current_approval_passes_effect_fence(
    monkeypatch, provider, remediation_job_id
):
    artifact, cloud, job = _approved_graph(provider)
    artifact.remediation_job_id = remediation_job_id
    processor, claim, db, service = _processor(monkeypatch, artifact, cloud, job)
    events = []
    service._lock_authority_order.side_effect = lambda *a, **k: events.append("parents")
    db.scalar.side_effect = lambda statement: (
        events.append("queue" if statement._for_update_arg is not None else "discover")
        or job
    )
    service.resolve_record.side_effect = lambda *a, **k: events.append("approval")

    token = processor._begin_external_effect_sync(claim)

    assert job.external_effect_state == "requesting"
    assert job.external_effect_token == token
    assert events == ["discover", "parents", "queue", "approval"]
    assert (
        service._lock_authority_order.call_args.kwargs["remediation_job_id"]
        == remediation_job_id
    )
    assert service.resolve_record.call_args.kwargs["require_approved"] is True
    assert (
        service.resolve_record.call_args.kwargs["approval_checksum"] == artifact.sha256
    )
    db.commit.assert_called_once()


@pytest.mark.parametrize(
    "change",
    (
        "pending",
        "rejected",
        "actor",
        "missing_actor",
        "approval_time",
        "missing_approval_time",
        "checksum",
        "review_digest",
        "foreign_tenant",
        "foreign_credential",
        "foreign_provider",
        "superseded",
        "missing_snapshot",
        "stale_review",
        "needs_rescan",
        "source_version",
        "destination_rebind",
        "scan_rebind",
    ),
)
def test_effect_fence_refuses_unapproved_or_changed_authority(monkeypatch, change):
    artifact, cloud, job = _approved_graph()
    processor, claim, db, service = _processor(monkeypatch, artifact, cloud, job)
    if change in {"pending", "rejected"}:
        artifact.review_status = change
    elif change == "actor":
        artifact.approved_by_ref = "session:someone-else"
    elif change == "missing_actor":
        artifact.approved_by_ref = None
    elif change == "approval_time":
        artifact.approved_at = datetime(2026, 10, 11, tzinfo=timezone.utc)
    elif change == "missing_approval_time":
        artifact.approved_at = None
    elif change == "checksum":
        artifact.sha256 = "c" * 64
    elif change == "review_digest":
        artifact.approval_review_digest = "c" * 64
    elif change == "foreign_tenant":
        artifact.department_id = "another-department"
    elif change == "foreign_credential":
        cloud.credential_id = "another-credential"
    elif change == "foreign_provider":
        artifact.provider = "microsoft"
    elif change == "superseded":
        cloud.current_remediation_artifact_id = "artifact-new"
    elif change == "needs_rescan":
        cloud.needs_rescan = True
    elif change == "source_version":
        cloud.provider_version = "version-2"
    elif change == "destination_rebind":
        cloud.provider_parent_id = "folder-2"
    elif change == "scan_rebind":
        job.scan.document_id = "another-file"
    elif change == "missing_snapshot":
        job.payload.pop("approval_review_digest")
    else:
        service.resolve_record.side_effect = ArtifactAuthorizationError(
            "review changed"
        )

    with pytest.raises(LostJobOwnership):
        processor._begin_external_effect_sync(claim)

    assert job.external_effect_state is None
    db.commit.assert_not_called()


def test_effect_fence_refuses_another_same_artifact_provider_effect(monkeypatch):
    artifact, cloud, job = _approved_graph()
    processor, claim, db, _service = _processor(monkeypatch, artifact, cloud, job)
    db.query.return_value.filter.return_value.first.return_value = ("other-upload",)

    with pytest.raises(LostJobOwnership):
        processor._begin_external_effect_sync(claim)

    assert job.external_effect_state is None
    db.commit.assert_not_called()


@pytest.mark.parametrize("review_status", ("pending", "rejected"))
def test_enqueue_refuses_unapproved_artifact(monkeypatch, review_status):
    artifact, cloud, job = _approved_graph()
    _, _, db, _ = _processor(monkeypatch, artifact, cloud, job)
    artifact.review_status = review_status
    enqueue = MagicMock()
    monkeypatch.setattr(remediation_job, "enqueue_cloud_job", enqueue)

    with pytest.raises(ArtifactAuthorizationError):
        remediation_job._queue_upload_job(
            cloud_file_id=cloud.id,
            department_id=cloud.department_id,
            provider=cloud.provider,
            db=db,
            artifact_id=artifact.id,
            remediation_job_id=artifact.remediation_job_id,
            scan_id=artifact.scan_id,
            credential_id=cloud.credential_id,
            create_new_version=True,
            requested_by_ref="session:writer-1",
        )

    enqueue.assert_not_called()


@pytest.mark.parametrize("mismatch", (None, "missing", "extra", "edited", "duplicate"))
def test_reviewed_output_requires_exact_selected_contents(mismatch):
    rows = [SimpleNamespace(id="fix-1", issue_id="alt-1", fixed_content="Reviewed alt")]
    applied = [SimpleNamespace(issue_id="alt-1", fixed_content="Reviewed alt")]
    if mismatch == "missing":
        applied = []
    elif mismatch == "extra":
        applied.append(
            SimpleNamespace(issue_id="alt-2", fixed_content="Unreviewed alt")
        )
    elif mismatch == "edited":
        applied[0].fixed_content = "Old unreviewed alt"
    elif mismatch == "duplicate":
        applied.append(applied[0])

    assert remediation_job._reviewed_output_matches(rows, applied) is (mismatch is None)


def test_explicit_retry_refuses_unresolved_prior_provider_effect(monkeypatch):
    artifact, cloud, job = _approved_graph()
    _, _, db, _ = _processor(monkeypatch, artifact, cloud, job)
    db.query.return_value.filter.return_value.first.return_value = ("prior-upload",)
    enqueue = MagicMock()
    monkeypatch.setattr(remediation_job, "enqueue_cloud_job", enqueue)

    with pytest.raises(ValueError, match="writeback_reconciliation_required"):
        remediation_job._queue_upload_job(
            cloud_file_id=cloud.id,
            department_id=cloud.department_id,
            provider=cloud.provider,
            db=db,
            artifact_id=artifact.id,
            remediation_job_id=artifact.remediation_job_id,
            scan_id=artifact.scan_id,
            credential_id=cloud.credential_id,
            create_new_version=True,
            requested_by_ref="session:writer-1",
        )
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    "change", ["same", "actor", "review_digest", "approved_at", "mode"]
)
def test_completed_writeback_dedupes_exact_approval_and_filename_mode(
    monkeypatch, change
):
    import hashlib
    import json

    artifact, cloud, job = _approved_graph()
    _, _, db, _ = _processor(monkeypatch, artifact, cloud, job)
    original_approval_key = hashlib.sha256(
        json.dumps(
            {
                **upload_job.upload_approval_snapshot(artifact),
                **upload_job.upload_source_snapshot(cloud, job.scan),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    previous_key = (
        f"upload:{cloud.provider}:{cloud.id}:{artifact.id}:{original_approval_key}:1"
    )
    if change == "actor":
        artifact.approved_by_ref = "session:new-reviewer"
    elif change == "review_digest":
        artifact.approval_review_digest = "c" * 64
    elif change == "approved_at":
        artifact.approved_at = datetime(2026, 10, 11, tzinfo=timezone.utc)

    query = db.query.return_value

    def find_existing():
        params = {
            key: value
            for condition in query.filter.call_args.args
            for key, value in condition.compile().params.items()
        }
        return (
            ("confirmed-upload",)
            if params.get("dedupe_key_1") == previous_key
            else None
        )

    query.filter.return_value.first.side_effect = find_existing
    enqueue = MagicMock(return_value=SimpleNamespace(id="new-upload"))
    monkeypatch.setattr(remediation_job, "enqueue_cloud_job", enqueue)
    result = remediation_job._queue_upload_job(
        cloud_file_id=cloud.id,
        department_id=cloud.department_id,
        provider=cloud.provider,
        db=db,
        artifact_id=artifact.id,
        remediation_job_id=artifact.remediation_job_id,
        scan_id=artifact.scan_id,
        credential_id=cloud.credential_id,
        create_new_version=change != "mode",
        requested_by_ref="session:writer-1",
    )
    if change == "same":
        assert result == "confirmed-upload"
        enqueue.assert_not_called()
    else:
        assert result == "new-upload"
        enqueue.assert_called_once()
        assert enqueue.call_args.kwargs["dedupe_key"] != previous_key


def test_written_artifact_rejects_second_upload_mode_before_enqueue(monkeypatch):
    artifact, cloud, job = _approved_graph()
    artifact.written_back_at = datetime(2026, 10, 11, tzinfo=timezone.utc)
    _, _, db, _ = _processor(monkeypatch, artifact, cloud, job)
    db.query.return_value.filter.return_value.first.return_value = None
    enqueue = MagicMock()
    monkeypatch.setattr(remediation_job, "enqueue_cloud_job", enqueue)

    with pytest.raises(ValueError, match="writeback_already_completed"):
        remediation_job._queue_upload_job(
            cloud_file_id=cloud.id,
            department_id=cloud.department_id,
            provider=cloud.provider,
            db=db,
            artifact_id=artifact.id,
            remediation_job_id=artifact.remediation_job_id,
            scan_id=artifact.scan_id,
            credential_id=cloud.credential_id,
            create_new_version=False,
            requested_by_ref="session:writer-1",
        )
    enqueue.assert_not_called()


@pytest.mark.parametrize("same_mode", [True, False])
def test_active_upload_reuses_only_same_approval_and_mode(monkeypatch, same_mode):
    import hashlib
    import json

    artifact, cloud, job = _approved_graph()
    _, _, db, _ = _processor(monkeypatch, artifact, cloud, job)
    approval = {
        **upload_job.upload_approval_snapshot(artifact),
        **upload_job.upload_source_snapshot(cloud, job.scan),
    }
    digest = hashlib.sha256(json.dumps(approval, sort_keys=True).encode()).hexdigest()
    active_key = f"upload:google:{cloud.id}:{artifact.id}:{digest}:1"
    db.query.return_value.filter.return_value.first.side_effect = [
        None,
        None,
        ("active-upload", active_key),
    ]
    enqueue = MagicMock()
    monkeypatch.setattr(remediation_job, "enqueue_cloud_job", enqueue)

    request = dict(
        cloud_file_id=cloud.id,
        department_id=cloud.department_id,
        provider=cloud.provider,
        db=db,
        artifact_id=artifact.id,
        remediation_job_id=artifact.remediation_job_id,
        scan_id=artifact.scan_id,
        credential_id=cloud.credential_id,
        create_new_version=same_mode,
        requested_by_ref="session:writer-1",
    )
    if same_mode:
        assert remediation_job._queue_upload_job(**request) == "active-upload"
    else:
        with pytest.raises(ValueError, match="writeback_reconciliation_required"):
            remediation_job._queue_upload_job(**request)
    enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_upload_handler_forwards_complete_source_authority(monkeypatch):
    _artifact, cloud, job = _approved_graph()
    job.payload.update(
        {
            "cloud_file_id": cloud.id,
            "department_id": cloud.department_id,
            "provider": cloud.provider,
        }
    )
    process = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(upload_job, "process_upload_job", process)

    await upload_job.handle_upload_job(job, MagicMock(), None)

    forwarded = process.await_args.args[0]
    assert all(
        forwarded[key] == value
        for key, value in upload_job.upload_source_snapshot(cloud, job.scan).items()
    )
    assert forwarded["credential_id"] == cloud.credential_id
