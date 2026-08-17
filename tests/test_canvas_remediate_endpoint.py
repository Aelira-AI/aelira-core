"""The Canvas remediate endpoint must run the work it queues.

Nothing polls CloudJobQueue in this application, so an endpoint that writes
job rows and returns success is reporting work that will never happen. This
endpoint did exactly that: it created a scan row and a remediation row, took
a BackgroundTasks parameter, and never used it.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.canvas_routes import (
    _canvas_scan_then_remediate_task,
    remediate_canvas_file,
)
from src.db.models import CloudJobStatus


def _db_with_credential_and_file():
    db = MagicMock()
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.first.return_value = MagicMock(id="cred-1")
    db.query.return_value = chain
    return db


@pytest.mark.asyncio
async def test_remediate_endpoint_fires_the_background_task():
    background_tasks = MagicMock()
    request = MagicMock(file_id="f-1", course_id="101", department_id="d1")

    with (
        patch("src.api.canvas_routes.require_feature", new=AsyncMock()),
        patch("src.api.canvas_routes.verify_department_access"),
    ):
        response = await remediate_canvas_file(
            request=request,
            background_tasks=background_tasks,
            db=_db_with_credential_and_file(),
            api_key_info=(None, "u1", "d1"),
        )

    assert response.success is True
    background_tasks.add_task.assert_called_once()
    fired, *ids = background_tasks.add_task.call_args[0]
    assert fired is _canvas_scan_then_remediate_task
    assert len(ids) == 2 and all(ids)


@pytest.mark.asyncio
async def test_a_failed_scan_fails_its_remediation_instead_of_stranding_it():
    scan_job, remediation_job = MagicMock(), MagicMock()
    db = MagicMock()
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.first.side_effect = [scan_job, remediation_job]
    db.query.return_value = chain

    @contextmanager
    def fake_db():
        yield db

    with (
        patch("src.db.database.get_db", fake_db),
        patch(
            "src.jobs.cloud_scan_job.handle_scan_job",
            new=AsyncMock(side_effect=RuntimeError("download refused")),
        ),
    ):
        await _canvas_scan_then_remediate_task("scan-1", "rem-1")

    assert scan_job.status == CloudJobStatus.FAILED.value
    assert remediation_job.status == CloudJobStatus.FAILED.value
    assert "scan" in remediation_job.progress_message.lower()
