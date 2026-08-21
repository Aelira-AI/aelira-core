"""Task17B durable enqueue-only route contract tests."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

QUEUE_ROUTE_FILES = (
    "src/api/canvas_routes.py",
    "src/api/canvas_scan_routes.py",
    "src/api/canvas_content_routes.py",
    "src/api/google_routes.py",
    "src/api/microsoft_routes.py",
    "src/api/brightspace_routes.py",
    "src/api/webhook_routes.py",
    "src/api/blackboard_routes.py",
    "src/api/integration_routes.py",
)


@pytest.mark.parametrize("path", QUEUE_ROUTE_FILES)
def test_cloud_queue_routes_are_enqueue_only(path):
    source = Path(path).read_text()
    tree = ast.parse(source)

    assert "BackgroundTasks" not in source
    assert "background_tasks.add_task" not in source
    assert "handle_scan_job" not in source
    assert "handle_remediation_job" not in source
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CloudJobQueue"
        for node in ast.walk(tree)
    )
    assert "enqueue_cloud_job" in source


@pytest.mark.asyncio
async def test_blackboard_remediation_validates_exact_content_then_enqueues(
    monkeypatch,
):
    from src.api import blackboard_routes

    credential = SimpleNamespace(
        id="cred-1",
        department_id="dept-1",
        provider="blackboard",
        is_active=True,
        provider_metadata={"blackboard_instance_url": "https://lms.example.edu"},
    )
    cloud_file = SimpleNamespace(
        id="file-1",
        department_id="dept-1",
        credential_id="cred-1",
        provider="blackboard",
        provider_file_id="content-1",
        provider_parent_id="course-1",
        file_name="notes.docx",
        provider_version="version-1",
    )
    provider_client = AsyncMock(side_effect=AssertionError("provider call forbidden"))
    monkeypatch.setattr(blackboard_routes, "_get_blackboard_client", provider_client)
    monkeypatch.setattr(
        blackboard_routes,
        "require_persisted_blackboard_origin",
        MagicMock(return_value="https://lms.example.edu"),
    )
    scan_job = SimpleNamespace(id="scan-job-1", status="pending", progress=0)
    queued = SimpleNamespace(id="job-1", status="pending", progress=0)
    enqueue = MagicMock(side_effect=[scan_job, queued])
    monkeypatch.setattr(blackboard_routes, "enqueue_cloud_job", enqueue)
    db = MagicMock()
    credential_query = MagicMock()
    credential_query.filter.return_value.first.return_value = credential
    file_query = MagicMock()
    file_query.filter.return_value.first.return_value = cloud_file
    db.query.side_effect = lambda model: (
        credential_query
        if model is blackboard_routes.CloudOAuthCredentials
        else file_query
    )

    response = await blackboard_routes.remediate_blackboard_file(
        blackboard_routes.BlackboardRemediateRequest(
            course_id="course-1",
            content_id="content-1",
            department_id="dept-1",
            upload_as_new=True,
        ),
        db=db,
        api_key_info=(None, "user-1", "dept-1"),
    )

    assert response["job_id"] == "job-1"
    assert response["status"] == "pending"
    provider_client.assert_not_awaited()
    assert enqueue.call_count == 2
    remediation_call = enqueue.call_args_list[-1]
    assert remediation_call.kwargs["job_type"] == "remediate"
    assert remediation_call.kwargs["payload"]["course_id"] == "course-1"
    assert remediation_call.kwargs["depends_on_job_id"] == "scan-job-1"


@pytest.mark.asyncio
async def test_integration_sync_validates_selected_folders_then_enqueues(monkeypatch):
    from src.api import integration_routes

    credential = SimpleNamespace(
        id="cred-1", department_id="dept-1", provider="google", is_active=True
    )
    folder = SimpleNamespace(
        id="folder-1",
        department_id="dept-1",
        credential_id="cred-1",
        provider="google",
        is_active=True,
    )
    credential_query = MagicMock()
    credential_query.filter.return_value.first.return_value = credential
    folder_query = MagicMock()
    folder_query.filter.return_value.all.return_value = [folder]
    db = MagicMock()
    db.query.side_effect = lambda model: (
        credential_query
        if model is integration_routes.CloudOAuthCredentials
        else folder_query
    )
    queued = SimpleNamespace(id="sync-job-1", status="pending", progress=0)
    enqueue = MagicMock(return_value=queued)
    monkeypatch.setattr(integration_routes, "enqueue_cloud_job", enqueue)

    response = await integration_routes.trigger_sync(
        integration_routes.TriggerSyncRequest(
            provider="google", folder_ids=["folder-1"]
        ),
        api_key=SimpleNamespace(department_id="dept-1"),
        db=db,
    )

    assert response == {
        "job_id": "sync-job-1",
        "status": "pending",
        "progress": 0,
        "provider": "google",
        "folder_ids": ["folder-1"],
    }
    assert enqueue.call_args.kwargs["job_type"] == "sync"
    assert enqueue.call_args.kwargs["payload"]["folder_ids"] == ["folder-1"]


def test_blackboard_remediation_http_route_returns_durable_status(monkeypatch):
    from src.api import blackboard_routes

    credential = SimpleNamespace(
        id="cred-1",
        department_id="dept-1",
        provider="blackboard",
        is_active=True,
        provider_metadata={"blackboard_instance_url": "https://lms.example.edu"},
    )
    cloud_file = SimpleNamespace(
        id="file-1",
        department_id="dept-1",
        credential_id="cred-1",
        provider="blackboard",
        provider_file_id="content-1",
        provider_parent_id="course-1",
        provider_version="v1",
        file_hash=None,
    )
    credential_query = MagicMock()
    credential_query.filter.return_value.first.return_value = credential
    file_query = MagicMock()
    file_query.filter.return_value.first.return_value = cloud_file
    db = MagicMock()
    db.query.side_effect = lambda model: (
        credential_query
        if model is blackboard_routes.CloudOAuthCredentials
        else file_query
    )
    monkeypatch.setattr(
        blackboard_routes,
        "require_persisted_blackboard_origin",
        MagicMock(return_value="https://lms.example.edu"),
    )
    monkeypatch.setattr(
        blackboard_routes,
        "enqueue_cloud_job",
        MagicMock(
            side_effect=[
                SimpleNamespace(id="scan-job-1", status="pending", progress=0),
                SimpleNamespace(id="job-1", status="pending", progress=0),
            ]
        ),
    )
    app = FastAPI()
    app.include_router(blackboard_routes.router)
    app.dependency_overrides[blackboard_routes.get_db_dependency] = lambda: db
    app.dependency_overrides[blackboard_routes.get_required_api_key] = lambda: (
        None,
        "user-1",
        "dept-1",
    )

    response = TestClient(app).post(
        "/blackboard/remediate",
        json={
            "course_id": "course-1",
            "content_id": "content-1",
            "department_id": "dept-1",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "job_id": "job-1",
        "scan_job_id": "scan-job-1",
        "status": "pending",
        "progress": 0,
        "course_id": "course-1",
        "content_id": "content-1",
    }


def test_integration_sync_http_route_replaces_legacy_501(monkeypatch):
    from src.api import integration_routes

    credential = SimpleNamespace(
        id="cred-1", department_id="dept-1", provider="google", is_active=True
    )
    folder = SimpleNamespace(
        id="folder-1",
        department_id="dept-1",
        credential_id="cred-1",
        provider="google",
        is_active=True,
    )
    credential_query = MagicMock()
    credential_query.filter.return_value.first.return_value = credential
    folder_query = MagicMock()
    folder_query.filter.return_value.all.return_value = [folder]
    db = MagicMock()
    db.query.side_effect = lambda model: (
        credential_query
        if model is integration_routes.CloudOAuthCredentials
        else folder_query
    )
    monkeypatch.setattr(
        integration_routes,
        "enqueue_cloud_job",
        MagicMock(
            return_value=SimpleNamespace(id="sync-job-1", status="pending", progress=0)
        ),
    )
    app = FastAPI()
    app.include_router(integration_routes.router)
    app.dependency_overrides[integration_routes.get_db_dependency] = lambda: db
    app.dependency_overrides[integration_routes.get_current_api_key] = lambda: (
        SimpleNamespace(department_id="dept-1")
    )

    response = TestClient(app).post(
        "/integrations/sync",
        json={"provider": "google", "folder_ids": ["folder-1"]},
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == "sync-job-1"
    assert response.json()["status"] == "pending"
