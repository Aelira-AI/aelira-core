"""LMS HTML publication requires a receipt for the exact current candidate."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.services.lms_content_approval import (
    LMSContentApprovalError,
    approve_lms_content,
    current_lms_content_approval,
)


def _candidate():
    return SimpleNamespace(
        id="file-1",
        department_id="department-1",
        credential_id="credential-1",
        provider="canvas",
        provider_file_id="page-1",
        provider_parent_id="course-1",
        last_scan_id="scan-1",
        content_source="page",
        content_slug="intro",
        file_name="source.html",
        file_type="html",
        content_updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        content_body="<p>Original</p>",
        remediated_body="<p>Improved</p>",
        provider_metadata={"url": "https://canvas.example/page/1"},
        needs_rescan=False,
        current_remediation_artifact_id=None,
        has_remediated_version=True,
        remediated_file_id=None,
        remediated_issues_fixed=1,
        remediated_issues_remaining=0,
        writeback_status=None,
    )


def test_receipt_binds_actor_source_candidate_and_destination():
    original = _candidate()
    receipt = approve_lms_content(
        original, actor_id="reviewer-1", actor_ref="session:reviewer-1"
    )
    assert current_lms_content_approval(original) == receipt

    for field, value in (
        ("content_body", "<p>New source</p>"),
        ("remediated_body", "<p>Other candidate</p>"),
        ("credential_id", "credential-2"),
        ("department_id", "department-2"),
        ("provider_file_id", "page-2"),
        ("last_scan_id", "scan-2"),
    ):
        changed = deepcopy(original)
        setattr(changed, field, value)
        assert current_lms_content_approval(changed) is None


def test_worker_cannot_use_status_only_as_human_approval():
    candidate = _candidate()
    candidate.writeback_status = "approved"
    assert current_lms_content_approval(candidate) is None
    with pytest.raises(LMSContentApprovalError):
        approve_lms_content(candidate, actor_id="", actor_ref="worker")


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["current", "other_document", "local_source"])
async def test_brightspace_diff_identifies_only_current_public_cloud_scan(binding):
    from src.api.brightspace_routes import get_content_diff
    from src.db.models import Scan

    cloud = _candidate()
    cloud.provider = "brightspace"
    scan = SimpleNamespace(
        id=cloud.last_scan_id,
        department_id=cloud.department_id,
        scan_type="PDF",
        document_source="local" if binding == "local_source" else "cloud_file",
        document_id=("other-file" if binding == "other_document" else cloud.id),
    )
    db = MagicMock()
    scan_query = MagicMock()
    scan_query.filter.return_value.first.return_value = scan
    result_query = MagicMock()
    result_query.filter.return_value.first.return_value = None
    db.query.side_effect = lambda model: scan_query if model is Scan else result_query
    with patch(
        "src.api.brightspace_routes._get_authorized_cloud_file_or_404",
        return_value=cloud,
    ):
        payload = await get_content_diff(
            cloud.id, SimpleNamespace(department_id=cloud.department_id), db
        )
    assert payload["cloud_file_id"] == cloud.id
    assert payload["content_type"] == "html"
    assert payload["scan_id"] == (cloud.last_scan_id if binding == "current" else None)
    assert payload["scan_type"] == ("PDF" if binding == "current" else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_changed", [False, True])
async def test_brightspace_writeback_requires_unchanged_human_reviewed_source(
    source_changed,
):
    from src.api.brightspace_routes import _writeback_single

    cloud = _candidate()
    cloud.provider = "brightspace"
    cloud.provider_file_id = "7"
    cloud.provider_parent_id = "42"
    cloud.provider_metadata = {"url": "/content/source.html", "org_unit_id": 42}
    receipt = approve_lms_content(
        cloud, actor_id="reviewer-1", actor_ref="session:reviewer-1"
    )
    api = AsyncMock()
    api.credential_id = cloud.credential_id
    api.get_topic_file.return_value = (
        b"<p>New upstream</p>" if source_changed else cloud.content_body.encode(),
        "text/html",
    )
    with patch("src.api.brightspace_routes._get_cloud_file_or_404", return_value=cloud):
        if source_changed:
            with pytest.raises(HTTPException) as denied:
                await _writeback_single(api, cloud, 42, 7, db=MagicMock())
            assert denied.value.status_code == 409
            api.replace_topic_file.assert_not_awaited()
        else:
            await _writeback_single(api, cloud, 42, 7, db=MagicMock())
            api.replace_topic_file.assert_awaited_once_with(
                42, 7, b"<p>Improved</p>", "source.html"
            )
            assert cloud.content_body == "<p>Original</p>"
            assert current_lms_content_approval(cloud) == receipt
