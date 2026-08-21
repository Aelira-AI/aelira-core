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
from src.db.models import CloudOAuthCredentials, UserRole

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
