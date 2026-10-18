"""LTI principal compatibility and course scoping for Brightspace navigation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.brightspace_routes import (
    BrightspaceBatchContentRequest,
    BrightspaceCourseActionRequest,
    get_brightspace_content_status,
    get_content_diff,
    list_brightspace_course_files,
    list_brightspace_courses,
)
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import (
    CloudFile,
    CloudOAuthCredentials,
    Scan,
    ScanResult,
    ScanStatus,
    ScanType,
    UserRole,
)

pytestmark = pytest.mark.integration


def test_course_actions_require_a_strict_integer_org_unit_id():
    with pytest.raises(ValidationError):
        BrightspaceCourseActionRequest(org_unit_id="101")


def test_batch_content_actions_validate_and_dedupe_object_ids():
    request = BrightspaceBatchContentRequest(
        cloud_file_ids=["cloud-1", "cloud-1", "cloud-2"]
    )
    assert request.cloud_file_ids == ["cloud-1", "cloud-2"]

    with pytest.raises(ValidationError):
        BrightspaceBatchContentRequest(cloud_file_ids=["../foreign"])


def _principal(
    *,
    admin: bool = False,
    course_id: str = "101",
    platform: str = "brightspace",
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="lti-user",
        department_id="dept-1",
        user_role=UserRole.ADMIN if admin else UserRole.FACULTY,
        auth_method="lti",
        lti_course_id=None if admin else course_id,
        lti_staff_role="Administrator" if admin else "Instructor",
        lti_account_wide=admin,
        lti_platform=platform,
    )


def _credential() -> MagicMock:
    credential = MagicMock(spec=CloudOAuthCredentials)
    credential.id = "credential-1"
    credential.department_id = "dept-1"
    credential.provider_metadata = {
        "brightspace_instance_url": "https://lms.example.edu"
    }
    return credential


def _db_with_credential() -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _credential()
    return db


@pytest.mark.asyncio
async def test_course_lti_staff_list_only_their_launch_course():
    courses = [
        SimpleNamespace(
            OrgUnitId=101,
            Name="Launch course",
            Code="LTI-101",
            StartDate=None,
            EndDate=None,
            IsActive=True,
        ),
        SimpleNamespace(
            OrgUnitId=202,
            Name="Other course",
            Code="LTI-202",
            StartDate=None,
            EndDate=None,
            IsActive=True,
        ),
    ]
    api_client = MagicMock()
    api_client.get_my_enrollments = AsyncMock(return_value=courses)
    api_client.close = AsyncMock()

    with (
        patch(
            "src.api.brightspace_routes._ensure_valid_token",
            new=AsyncMock(return_value="token"),
        ),
        patch(
            "src.api.brightspace_routes.BrightspaceAPIClient", return_value=api_client
        ),
    ):
        result = await list_brightspace_courses(
            principal=_principal(),
            db=_db_with_credential(),
        )

    assert [course["org_unit_id"] for course in result] == [101]


@pytest.mark.asyncio
async def test_account_wide_lti_admin_can_list_permitted_courses():
    courses = [
        SimpleNamespace(
            OrgUnitId=101,
            Name="First",
            Code="ONE",
            StartDate=None,
            EndDate=None,
            IsActive=True,
        ),
        SimpleNamespace(
            OrgUnitId=202,
            Name="Second",
            Code="TWO",
            StartDate=None,
            EndDate=None,
            IsActive=True,
        ),
    ]
    api_client = MagicMock()
    api_client.get_my_enrollments = AsyncMock(return_value=courses)
    api_client.close = AsyncMock()

    with (
        patch(
            "src.api.brightspace_routes._ensure_valid_token",
            new=AsyncMock(return_value="token"),
        ),
        patch(
            "src.api.brightspace_routes.BrightspaceAPIClient", return_value=api_client
        ),
    ):
        result = await list_brightspace_courses(
            principal=_principal(admin=True),
            db=_db_with_credential(),
        )

    assert [course["org_unit_id"] for course in result] == [101, 202]


@pytest.mark.asyncio
async def test_course_lti_staff_cannot_open_another_brightspace_course():
    db = MagicMock()

    with pytest.raises(HTTPException) as denied:
        await list_brightspace_course_files(
            org_unit_id=202,
            principal=_principal(course_id="101"),
            db=db,
        )

    assert denied.value.status_code == 403
    db.query.assert_not_called()


@pytest.mark.asyncio
async def test_canvas_lti_token_cannot_enter_brightspace_even_when_course_matches():
    db = MagicMock()

    with pytest.raises(HTTPException) as denied:
        await list_brightspace_course_files(
            org_unit_id=101,
            principal=_principal(course_id="101", platform="canvas"),
            db=db,
        )

    assert denied.value.status_code == 403
    db.query.assert_not_called()


@pytest.mark.asyncio
async def test_course_lti_staff_can_read_their_brightspace_content_status():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []

    result = await get_brightspace_content_status(
        org_unit_id=101,
        principal=_principal(course_id="101"),
        db=db,
    )

    assert result["org_unit_id"] == 101
    assert result["items"] == []


@pytest.mark.asyncio
async def test_lti_brightspace_status_returns_persisted_remediation_origin():
    cloud_file = MagicMock()
    cloud_file.id = "cf-1"
    cloud_file.provider_file_id = "42"
    cloud_file.file_name = "Topic"
    cloud_file.file_type = "html"
    cloud_file.last_compliance_score = None
    cloud_file.last_scan_id = None
    cloud_file.provider_metadata = {}
    cloud_file.writeback_status = "pending_review"
    cloud_file.last_scanned_at = None
    cloud_file.has_remediated_version = True
    cloud_file.remediation_origin = "automatic"
    cloud_file.needs_rescan = False
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [cloud_file]

    result = await get_brightspace_content_status(
        org_unit_id=101,
        principal=_principal(course_id="101"),
        db=db,
    )

    assert result["items"][0]["remediation_origin"] == "automatic"


@pytest.mark.asyncio
async def test_brightspace_mixed_assessment_does_not_report_complete_course_average():
    files = []
    scans = []
    results = []
    for file_id, file_type, score, issue_count in [
        ("pdf", "pdf", 100.0, 0),
        ("video", "mp4", None, 1),
    ]:
        scan_id = f"scan-{file_id}"
        files.append(
            SimpleNamespace(
                id=file_id,
                department_id="dept-1",
                provider_file_id=file_id,
                file_name=f"{file_id}.{file_type}",
                file_type=file_type,
                mime_type="video/mp4" if file_type == "mp4" else "application/pdf",
                last_compliance_score=score,
                last_scan_id=scan_id,
                provider_metadata={},
                writeback_status=None,
                last_scanned_at=None,
                has_remediated_version=False,
                remediation_origin=None,
                needs_rescan=False,
            )
        )
        scans.append(
            SimpleNamespace(
                id=scan_id,
                department_id="dept-1",
                document_id=file_id,
                scan_type=ScanType.VIDEO if file_type == "mp4" else ScanType.PDF,
                status=ScanStatus.COMPLETED,
            )
        )
        results.append(
            SimpleNamespace(
                scan_id=scan_id,
                compliance_score=score,
                issues=[{"severity": "critical", "rule": "WCAG 1.2.2"}] * issue_count,
                critical_issues=issue_count,
                high_issues=0,
                medium_issues=0,
                low_issues=0,
                structure=(
                    {
                        "media_type": "video",
                        "flashing_assessment": {
                            "state": "unassessed",
                            "reason": "detector_unavailable",
                            "version": "unassessed-v1",
                        },
                    }
                    if file_type == "mp4"
                    else None
                ),
            )
        )
    db = MagicMock()
    records = {CloudFile: files, Scan: scans, ScanResult: results}

    def query(model):
        response = MagicMock()
        response.filter.return_value.all.return_value = records[model]
        return response

    db.query.side_effect = query

    with patch(
        "src.api.brightspace_routes._brightspace_approval_eligibility",
        return_value=SimpleNamespace(eligible=False),
    ):
        response = await get_brightspace_content_status(
            org_unit_id=101, principal=_principal(), db=db
        )

    assert response["total_items"] == 2
    assert response["scanned_items"] == 2
    assert response["average_compliance"] is None
    items = {item["cloud_file_id"]: item for item in response["items"]}
    assert items["pdf"]["compliance_score"] == 100
    assert items["video"]["compliance_score"] is None
    assert items["video"]["issue_count"] == 1
    assert items["video"]["scan_status"] == ScanStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_course_lti_staff_cannot_enumerate_another_course_item():
    cloud_file = MagicMock()
    cloud_file.id = "cf-other"
    cloud_file.department_id = "dept-1"
    cloud_file.provider = "brightspace"
    cloud_file.provider_parent_id = "202"
    cloud_file.provider_file_id = "42"
    cloud_file.credential_id = "credential-1"
    cloud_file.provider_metadata = {"org_unit_id": 202}
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = cloud_file

    with pytest.raises(HTTPException) as denied:
        await get_content_diff(
            cloud_file_id="cf-other",
            principal=_principal(course_id="101"),
            db=db,
        )

    assert denied.value.status_code == 404
