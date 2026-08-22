"""Brightspace approval eligibility and truthful accounting regressions."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _artifact(cloud_file_id: str = "cf-artifact", **overrides):
    values = {
        "id": "artifact-1",
        "cloud_file_id": cloud_file_id,
        "department_id": "dept-1",
        "provider": "brightspace",
        "lifecycle_status": "available",
        "review_status": "pending",
        "cleanup_claimed_at": None,
        "written_back_at": None,
        "published_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _cloud_file(cloud_file_id: str = "cf-artifact", **overrides):
    artifact = overrides.pop("artifact", _artifact(cloud_file_id))
    values = {
        "id": cloud_file_id,
        "department_id": "dept-1",
        "provider": "brightspace",
        "provider_parent_id": "42",
        "remediated_body": None,
        "has_remediated_version": True,
        "remediation_origin": None,
        "current_remediation_artifact_id": getattr(artifact, "id", None),
        "current_remediation_artifact": artifact,
        "writeback_status": "remediated",
        "provider_file_id": "1",
        "credential_id": "credential-1",
        "file_name": "document.pdf",
        "file_type": "file",
        "last_compliance_score": 80.0,
        "last_scan_id": None,
        "provider_metadata": {
            "url": "document.pdf",
            "module_path": "Module",
            "org_unit_id": 42,
        },
        "last_scanned_at": None,
        "needs_rescan": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _principal():
    from src.auth.dependencies import AuthenticatedPrincipal
    from src.db.models import UserRole

    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method="session",
    )


def test_artifact_backed_document_is_approval_eligible_without_html_body():
    from src.api.brightspace_routes import _brightspace_approval_eligibility

    result = _brightspace_approval_eligibility(_cloud_file())

    assert result.eligible is True
    assert result.authority == "artifact"
    assert result.reason is None


def test_html_body_is_approval_eligible_without_artifact():
    from src.api.brightspace_routes import _brightspace_approval_eligibility

    result = _brightspace_approval_eligibility(
        _cloud_file(
            artifact=None,
            current_remediation_artifact_id=None,
            remediated_body="<p>fixed</p>",
        )
    )

    assert result.eligible is True
    assert result.authority == "html"


def test_has_remediated_flag_without_durable_authority_is_ineligible():
    from src.api.brightspace_routes import _brightspace_approval_eligibility

    result = _brightspace_approval_eligibility(
        _cloud_file(artifact=None, current_remediation_artifact_id=None)
    )

    assert result.eligible is False
    assert result.reason == "no_durable_remediation_authority"


def test_mismatched_or_expired_artifact_is_ineligible():
    from src.api.brightspace_routes import _brightspace_approval_eligibility

    mismatched = _cloud_file(artifact=_artifact(cloud_file_id="other"))
    expired = _cloud_file(
        artifact=_artifact(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )

    assert _brightspace_approval_eligibility(mismatched).eligible is False
    assert _brightspace_approval_eligibility(expired).eligible is False


@pytest.mark.asyncio
async def test_status_dto_exposes_server_computed_approval_eligibility():
    from src.api.brightspace_routes import get_brightspace_content_status

    eligible = _cloud_file("eligible")
    ineligible = _cloud_file(
        "ineligible", artifact=None, current_remediation_artifact_id=None
    )
    db = MagicMock()
    cloud_query = MagicMock()
    cloud_query.filter.return_value.all.return_value = [eligible, ineligible]
    db.query.return_value = cloud_query
    result = await get_brightspace_content_status(42, principal=_principal(), db=db)

    assert [item["approval_eligible"] for item in result["items"]] == [True, False]


@pytest.mark.asyncio
async def test_batch_approve_reports_mixed_artifact_and_ineligible_counts():
    from src.api.brightspace_routes import (
        BrightspaceBatchContentRequest,
        batch_approve_content,
    )

    ready = _cloud_file("ready")
    ineligible = _cloud_file(
        "ineligible", artifact=None, current_remediation_artifact_id=None
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [ready, ineligible]
    service = MagicMock()

    with patch(
        "src.api.brightspace_routes.RemediationArtifactService.from_settings",
        return_value=service,
    ):
        result = await batch_approve_content(
            BrightspaceBatchContentRequest(cloud_file_ids=["ready", "ineligible"]),
            principal=_principal(),
            db=db,
        )

    assert result["requested_count"] == 2
    assert result["approved_count"] == 1
    assert result["skipped_count"] == 1
    assert result["failed_count"] == 0
    assert result["outcomes"] == [
        {"cloud_file_id": "ready", "status": "approved", "reason": None},
        {
            "cloud_file_id": "ineligible",
            "status": "skipped",
            "reason": "no_durable_remediation_authority",
        },
    ]
    service.approve.assert_called_once_with(
        db,
        artifact_id="artifact-1",
        approved_by_id="user-1",
        approved_by_ref="session:user-1",
    )


@pytest.mark.asyncio
async def test_batch_approve_reports_all_ineligible_without_false_success():
    from src.api.brightspace_routes import (
        BrightspaceBatchContentRequest,
        batch_approve_content,
    )

    missing = _cloud_file(
        "missing", artifact=None, current_remediation_artifact_id=None
    )
    terminal = _cloud_file("terminal", writeback_status="approved")
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [missing, terminal]

    result = await batch_approve_content(
        BrightspaceBatchContentRequest(cloud_file_ids=["missing", "terminal"]),
        principal=_principal(),
        db=db,
    )

    assert result["requested_count"] == 2
    assert result["approved_count"] == 0
    assert result["skipped_count"] == 2
    assert result["failed_count"] == 0
    assert result["errors"] == [
        "missing: no_durable_remediation_authority",
        "terminal: already_terminal",
    ]


@pytest.mark.asyncio
async def test_single_artifact_approval_uses_managed_artifact_service():
    from src.api.brightspace_routes import approve_content

    ready = _cloud_file("ready")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ready
    service = MagicMock()

    with patch(
        "src.api.brightspace_routes.RemediationArtifactService.from_settings",
        return_value=service,
    ):
        result = await approve_content("ready", principal=_principal(), db=db)

    assert result == {"success": True, "message": "Content approved"}
    service.approve.assert_called_once_with(
        db,
        artifact_id="artifact-1",
        approved_by_id="user-1",
        approved_by_ref="session:user-1",
    )


@pytest.mark.asyncio
async def test_single_artifact_rejection_uses_managed_artifact_service():
    from src.api.brightspace_routes import reject_content

    ready = _cloud_file("ready")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ready
    service = MagicMock()

    with patch(
        "src.api.brightspace_routes.RemediationArtifactService.from_settings",
        return_value=service,
    ):
        result = await reject_content("ready", principal=_principal(), db=db)

    assert result == {"success": True, "message": "Content rejected"}
    service.reject.assert_called_once_with(
        db,
        artifact_id="artifact-1",
        rejected_by_id="user-1",
        rejected_by_ref="session:user-1",
    )
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_single_html_rejection_clears_remediation_authority():
    from src.api.brightspace_routes import reject_content

    html_item = _cloud_file(
        "html",
        artifact=None,
        current_remediation_artifact_id=None,
        current_remediation_artifact=None,
        remediated_body="<p>Fixed</p>",
        remediation_origin="manual",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = html_item
    service = MagicMock()

    with patch(
        "src.api.brightspace_routes.RemediationArtifactService.from_settings",
        return_value=service,
    ):
        result = await reject_content("html", principal=_principal(), db=db)

    assert result == {"success": True, "message": "Content rejected"}
    service.reject.assert_not_called()
    assert html_item.writeback_status == "rejected"
    assert html_item.has_remediated_version is False
    assert html_item.remediation_origin is None
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_single_artifact_rejection_failure_rolls_back_with_stable_409():
    from fastapi import HTTPException
    from src.api.brightspace_routes import ArtifactAuthorizationError, reject_content

    ready = _cloud_file("ready")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ready
    service = MagicMock()
    service.reject.side_effect = ArtifactAuthorizationError("sensitive invalid state")

    with (
        patch(
            "src.api.brightspace_routes.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        pytest.raises(HTTPException) as denied,
    ):
        await reject_content("ready", principal=_principal(), db=db)

    assert denied.value.status_code == 409
    assert denied.value.detail == "artifact_rejection_validation_failed"
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_batch_approve_reports_artifact_validation_failure():
    from src.api.brightspace_routes import (
        ArtifactAuthorizationError,
        BrightspaceBatchContentRequest,
        batch_approve_content,
    )

    ready = _cloud_file("ready")
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [ready]
    service = MagicMock()
    service.approve.side_effect = ArtifactAuthorizationError("invalid")

    with patch(
        "src.api.brightspace_routes.RemediationArtifactService.from_settings",
        return_value=service,
    ):
        result = await batch_approve_content(
            BrightspaceBatchContentRequest(cloud_file_ids=["ready"]),
            principal=_principal(),
            db=db,
        )

    assert result["requested_count"] == 1
    assert result["approved_count"] == 0
    assert result["skipped_count"] == 0
    assert result["failed_count"] == 1
    assert result["outcomes"] == [
        {
            "cloud_file_id": "ready",
            "status": "failed",
            "reason": "artifact_approval_validation_failed",
        }
    ]
