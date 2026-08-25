"""Durable-queue contract for the Canvas remediation endpoint."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.api.canvas_routes import remediate_canvas_file
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import UserRole


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="u1",
        department_id="d1",
        user_role=UserRole.FACULTY,
        auth_method="session",
    )


def _db_with_credential_and_file():
    db = MagicMock()
    chain = MagicMock()
    chain.filter.return_value = chain
    cloud_file = SimpleNamespace(id="cloud-file-1", provider_version=None)
    chain.first.side_effect = [
        SimpleNamespace(id="cred-1"),
        cloud_file,
    ]
    db.query.return_value = chain
    db.cloud_file = cloud_file
    return db


@pytest.mark.asyncio
async def test_remediate_endpoint_enqueues_exact_durable_scan_and_remediation():
    request = MagicMock(
        file_id="f-1",
        course_id="101",
        department_id="d1",
        upload_back=False,
        use_ai=False,
        generate_alt_text=False,
    )
    canvas = AsyncMock()
    updated_at = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    canvas.list_course_files.return_value = [
        SimpleNamespace(id="f-1", updated_at=updated_at)
    ]
    db = _db_with_credential_and_file()
    enqueue = MagicMock(
        side_effect=[SimpleNamespace(id="scan-1"), SimpleNamespace(id="rem-1")]
    )

    with (
        patch("src.api.canvas_routes.require_feature", new=AsyncMock()),
        patch("src.api.canvas_routes.verify_department_access"),
        patch(
            "src.api.canvas_routes._get_canvas_client",
            new=AsyncMock(return_value=(SimpleNamespace(id="cred-1"), canvas)),
        ),
        patch("src.api.canvas_routes.enqueue_cloud_job", enqueue),
    ):
        response = await remediate_canvas_file(
            request=request,
            db=db,
            principal=_principal(),
        )

    assert response.success is True
    assert response.job_id == "rem-1"
    assert enqueue.call_args_list == [
        call(
            db,
            department_id="d1",
            job_type="scan",
            payload={
                "cloud_file_id": "cloud-file-1",
                "credential_id": "cred-1",
                "provider": "canvas",
                "provider_file_id": "f-1",
                "course_id": "101",
            },
            dedupe_key="scan:canvas:101:file:f-1:2026-03-01T10:00:00+00:00",
            provider="canvas",
            priority=1,
            cloud_file_id="cloud-file-1",
            credential_id="cred-1",
            execution_context={
                "originating_route": "/canvas/remediate",
                "resource_id": "f-1",
                "course_id": "101",
            },
        ),
        call(
            db,
            department_id="d1",
            job_type="remediate",
            payload={
                "cloud_file_id": "cloud-file-1",
                "credential_id": "cred-1",
                "provider": "canvas",
                "provider_file_id": "f-1",
                "course_id": "101",
                "scan_job_id": "scan-1",
                "ai_requested": False,
                "alt_text_requested": False,
                "upload_back": False,
            },
            dedupe_key=(
                "remediate:canvas:101:file:f-1:"
                "version=2026-03-01T10:00:00+00:00:ai=false:alt=false"
            ),
            depends_on_job_id="scan-1",
            provider="canvas",
            priority=2,
            cloud_file_id="cloud-file-1",
            credential_id="cred-1",
            execution_context={
                "ai_requested": False,
                "alt_text_requested": False,
                "requested_purposes": [],
                "policy_version": "1",
                "originating_route": "/canvas/remediate",
                "resource_id": "f-1",
                "course_id": "101",
            },
        ),
    ]
    assert db.cloud_file.provider_version == "2026-03-01T10:00:00+00:00"
    assert db.cloud_file.provider_modified_at == updated_at
    db.commit.assert_called_once_with()


@pytest.mark.asyncio
async def test_remediate_endpoint_sanitizes_enqueue_failure_and_rolls_back():
    sentinel = "/app/uploads/private.pdf token=secret"
    request = MagicMock(
        file_id="f-1",
        course_id="101",
        department_id="d1",
        upload_back=False,
        use_ai=False,
        generate_alt_text=False,
    )
    canvas = AsyncMock()
    canvas.list_course_files.side_effect = RuntimeError(sentinel)
    db = _db_with_credential_and_file()

    with (
        patch("src.api.canvas_routes.require_feature", new=AsyncMock()),
        patch("src.api.canvas_routes.verify_department_access"),
        patch(
            "src.api.canvas_routes._get_canvas_client",
            new=AsyncMock(return_value=(SimpleNamespace(id="cred-1"), canvas)),
        ),
        patch("src.api.canvas_routes.logger.error") as log_error,
    ):
        response = await remediate_canvas_file(
            request=request,
            db=db,
            principal=_principal(),
        )

    payload = response.model_dump()
    assert payload == {
        "success": False,
        "scan_id": None,
        "job_id": None,
        "message": "Unable to queue remediation. Please try again later.",
        "error_code": "remediation_queue_unavailable",
    }
    serialized = f"{payload!r} {log_error.call_args!r}"
    assert sentinel not in serialized
    assert "/app/uploads/private.pdf" not in serialized
    assert "token=secret" not in serialized
    assert log_error.call_args.kwargs["extra"] == {
        "operation": "canvas_remediation_enqueue",
        "exception_type": "RuntimeError",
    }
    db.rollback.assert_called_once_with()
    canvas.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_remediate_endpoint_rollback_failure_keeps_sanitized_response():
    request = MagicMock(
        file_id="f-1",
        course_id="101",
        department_id="d1",
        upload_back=False,
        use_ai=False,
        generate_alt_text=False,
    )
    canvas = AsyncMock()
    canvas.list_course_files.side_effect = RuntimeError("provider token=secret")
    db = _db_with_credential_and_file()
    db.rollback.side_effect = RuntimeError("/database/path password=secret")

    with (
        patch("src.api.canvas_routes.require_feature", new=AsyncMock()),
        patch("src.api.canvas_routes.verify_department_access"),
        patch(
            "src.api.canvas_routes._get_canvas_client",
            new=AsyncMock(return_value=(SimpleNamespace(id="cred-1"), canvas)),
        ),
        patch("src.api.canvas_routes.logger.error") as log_error,
    ):
        response = await remediate_canvas_file(
            request=request,
            db=db,
            principal=_principal(),
        )

    assert response.error_code == "remediation_queue_unavailable"
    assert "token=secret" not in repr(log_error.call_args_list)
    assert "password=secret" not in repr(log_error.call_args_list)
    assert [
        call.kwargs["extra"]["exception_type"] for call in log_error.call_args_list
    ] == ["RuntimeError", "RuntimeError"]
    canvas.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_remediation_dependency_is_persisted_before_endpoint_acknowledges():
    request = MagicMock(
        file_id="f-1",
        course_id="101",
        department_id="d1",
        upload_back=False,
        use_ai=False,
        generate_alt_text=False,
    )
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [SimpleNamespace(id="f-1")]
    db = _db_with_credential_and_file()
    enqueue = MagicMock(
        side_effect=[SimpleNamespace(id="scan-1"), SimpleNamespace(id="rem-1")]
    )

    with (
        patch("src.api.canvas_routes.require_feature", new=AsyncMock()),
        patch("src.api.canvas_routes.verify_department_access"),
        patch(
            "src.api.canvas_routes._get_canvas_client",
            new=AsyncMock(return_value=(SimpleNamespace(id="cred-1"), canvas)),
        ),
        patch("src.api.canvas_routes.enqueue_cloud_job", enqueue),
    ):
        response = await remediate_canvas_file(request, db, _principal())

    remediation_call = enqueue.call_args_list[1]
    assert remediation_call.kwargs["depends_on_job_id"] == "scan-1"
    assert remediation_call.kwargs["payload"]["scan_job_id"] == "scan-1"
    assert response.job_id == "rem-1"
    db.commit.assert_called_once_with()
