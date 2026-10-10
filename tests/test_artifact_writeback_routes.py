"""Explicit artifact writeback authorizes the whole batch before enqueueing."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.education import artifact_writeback_routes as routes


def _principal():
    return SimpleNamespace(
        department_id="department-1", auth_method="session", user_id="reviewer-1"
    )


def _item(index: int):
    return routes.SelectedArtifactWriteback(
        scan_id=f"scan-{index}", artifact_id=f"artifact-{index}"
    )


def _authority(item, provider="google"):
    cloud = SimpleNamespace(
        id=f"file-{item.scan_id}", provider=provider, credential_id="credential-1"
    )
    artifact = SimpleNamespace(id=item.artifact_id, remediation_job_id="job-1")
    return SimpleNamespace(id=item.scan_id), cloud, artifact


def test_batch_authorizes_every_selection_before_any_enqueue(monkeypatch):
    db = MagicMock()
    first, second = _item(1), _item(2)
    enqueue = MagicMock()
    monkeypatch.setattr(routes, "_queue_upload_job", enqueue)

    def authorize(_db, *, scan_id, artifact_id, principal):
        assert principal.department_id == "department-1"
        if scan_id == second.scan_id:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return _authority(first)

    monkeypatch.setattr(routes, "_managed_artifact_authority", authorize)
    with pytest.raises(HTTPException) as error:
        routes._queue_selected_writebacks(db, _principal(), [first, second])

    assert error.value.status_code == 404
    enqueue.assert_not_called()
    db.commit.assert_not_called()


def test_batch_rejects_unsupported_provider_before_any_enqueue(monkeypatch):
    db = MagicMock()
    first, second = _item(1), _item(2)
    enqueue = MagicMock()
    monkeypatch.setattr(routes, "_queue_upload_job", enqueue)
    monkeypatch.setattr(
        routes,
        "_managed_artifact_authority",
        lambda _db, *, scan_id, artifact_id, principal: _authority(
            first if scan_id == first.scan_id else second,
            "canvas" if scan_id == second.scan_id else "google",
        ),
    )

    with pytest.raises(HTTPException) as error:
        routes._queue_selected_writebacks(db, _principal(), [first, second])

    assert error.value.status_code == 501
    enqueue.assert_not_called()


def test_batch_queues_only_explicit_authorized_artifacts(monkeypatch):
    db = MagicMock()
    first, second = _item(1), _item(2)
    monkeypatch.setattr(
        routes,
        "_managed_artifact_authority",
        lambda _db, *, scan_id, artifact_id, principal: _authority(
            first if scan_id == first.scan_id else second
        ),
    )
    enqueue = MagicMock(side_effect=["upload-1", "upload-2"])
    monkeypatch.setattr(routes, "_queue_upload_job", enqueue)

    queued = routes._queue_selected_writebacks(db, _principal(), [first, second])

    assert queued == [
        {"artifact_id": "artifact-1", "job_id": "upload-1"},
        {"artifact_id": "artifact-2", "job_id": "upload-2"},
    ]
    assert [call.kwargs["artifact_id"] for call in enqueue.call_args_list] == [
        "artifact-1",
        "artifact-2",
    ]
    assert all(
        call.kwargs["requested_by_ref"] == "session:reviewer-1"
        for call in enqueue.call_args_list
    )
    db.commit.assert_called_once()
