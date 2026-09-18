"""Authenticated Microsoft HTTP contracts with real PostgreSQL and mock Graph transport.

Only trusted identity validation, entitlement and provider HTTP are supplied seams.
Routes, token decryption, adapter DTOs, files, durable jobs and subscriptions are real.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api import microsoft_routes as routes
from src.api.auth_routes import SessionAccessIdentity
from src.config.settings import get_settings
from src.db.database import get_db_dependency
from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudOAuthCredentials,
    CloudWebhookSubscription,
    Department,
    Scan,
    ScanType,
    ScanStatus,
)
from src.integrations.microsoft_365.onedrive import OneDriveIntegration

pytestmark = pytest.mark.integration
FILE = {
    "id": "file-1",
    "name": "Lesson.pdf",
    "size": 9,
    "file": {"mimeType": "application/pdf"},
    "eTag": "v1",
    "webUrl": "https://example.test/Lesson.pdf",
}
FOLDER = {
    "id": "folder-1",
    "name": "Course",
    "folder": {"childCount": 1},
    "webUrl": "https://example.test/Course",
}
SUB = {
    "resource": "/me/drive/root",
    "notification_url": "https://example.test/webhooks/microsoft",
    "change_types": ["updated"],
}


@pytest.fixture
def microsoft_route(monkeypatch):
    engine = create_engine(get_settings().database_url)
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    # Ignore pre-existing suite rows while retaining every new row, even if a
    # broken route writes it under another department.
    existing_ids = {
        model: tuple(row.id for row in db.query(model.id))
        for model in (
            CloudFile,
            CloudJobQueue,
            CloudOAuthCredentials,
            CloudWebhookSubscription,
        )
    }

    def rows(model):
        return db.query(model).filter(model.id.not_in(existing_ids[model]))

    department = Department(
        id=str(uuid4()),
        name="Microsoft route",
        institution="Example University",
        contact_email="admin@example.test",
    )
    db.add(department)
    db.flush()
    manager = routes.get_token_manager()
    credential = CloudOAuthCredentials(
        id=str(uuid4()),
        department_id=department.id,
        provider="microsoft",
        access_token=manager.encrypt_token("synthetic-access"),
        refresh_token=manager.encrypt_token("synthetic-refresh"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        is_active=True,
    )
    db.add(credential)
    db.commit()
    fixture = SimpleNamespace(
        db=db,
        rows=rows,
        department=department,
        credential=credential,
        requests=[],
        integrations=[],
        failure=None,
    )

    def handler(request):
        fixture.requests.append(request)
        if fixture.failure:
            return httpx.Response(
                fixture.failure, json={"error": "provider diagnostic"}
            )
        path = request.url.path
        if request.method == "POST" and path.endswith("/subscriptions"):
            return httpx.Response(
                201,
                json={
                    "id": "sub-1",
                    "resource": "/me/drive/root",
                    "expirationDateTime": (
                        datetime.now(timezone.utc) + timedelta(days=2)
                    ).isoformat(),
                },
            )
        if request.method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "id": "sub-1",
                    "expirationDateTime": (
                        datetime.now(timezone.utc) + timedelta(days=2)
                    ).isoformat(),
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/content"):
            return httpx.Response(
                200, content=b"%PDF-test", headers={"content-type": "application/pdf"}
            )
        if path.endswith("/items/file-1"):
            return httpx.Response(200, json=FILE)
        if path.endswith("/followedSites"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "site-1",
                            "name": "Campus",
                            "displayName": "Campus",
                            "webUrl": "https://example.test/site",
                        }
                    ]
                },
            )
        if path.endswith("/me/drive"):
            return httpx.Response(200, json={"id": "drive-1", "name": "Documents"})
        if path.endswith("/drives"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "drive-1",
                            "name": "Documents",
                            "driveType": "documentLibrary",
                            "webUrl": "https://example.test/drive",
                        }
                    ]
                },
            )
        if path.endswith("/children") or "/search(" in path:
            return httpx.Response(200, json={"value": [FOLDER, FILE]})
        raise AssertionError(f"Unexpected Graph request: {request.method} {path}")

    transport = httpx.MockTransport(handler)

    def integration(*args, **kwargs):
        item = OneDriveIntegration(*args, **kwargs)
        item._http_client = httpx.AsyncClient(transport=transport)
        fixture.integrations.append(item)
        return item

    monkeypatch.setattr(routes, "OneDriveIntegration", integration)

    async def entitlement(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "require_feature", entitlement)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db_dependency] = lambda: db
    app.dependency_overrides[routes.get_current_api_key] = (
        lambda: SessionAccessIdentity(
            id="session-test", user_id="test-user-123", department_id=department.id
        )
    )
    fixture.app = app
    fixture.client = TestClient(app, raise_server_exceptions=False)
    try:
        yield fixture
    finally:
        fixture.client.close()
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.parametrize("target", ["onedrive", "sharepoint"])
def test_local_path_upload_is_explicitly_unsupported(microsoft_route, target, tmp_path):
    file = tmp_path / "fixture.pdf"
    file.write_bytes(b"%PDF-test")
    response = microsoft_route.client.post(
        f"/microsoft/{target}/upload",
        json={"file_path": str(file), "site_id": "site-1", "drive_id": "drive-1"},
    )
    assert response.status_code == 501
    assert not microsoft_route.requests


def test_download_returns_binary_and_cleans_owned_storage(microsoft_route):
    response = microsoft_route.client.get("/microsoft/onedrive/files/file-1/content")
    assert response.status_code == 200
    assert response.content == b"%PDF-test"
    assert response.headers["content-type"] == "application/pdf"
    assert all(
        not Path(item._temp_dir).exists() for item in microsoft_route.integrations
    )


def test_file_listing_tracks_files_not_folders(microsoft_route):
    response = microsoft_route.client.get("/microsoft/onedrive/files")
    assert response.status_code == 200
    assert [item["provider_file_id"] for item in response.json()["files"]] == ["file-1"]
    rows = microsoft_route.rows(CloudFile).all()
    assert len(rows) == 1
    assert rows[0].credential_id == microsoft_route.credential.id
    assert microsoft_route.credential.last_sync_at is not None


def test_subscription_round_trip_is_persisted_and_scoped(microsoft_route):
    f = microsoft_route
    response = f.client.post("/microsoft/subscriptions", json=SUB)
    assert response.status_code == 200
    row = f.rows(CloudWebhookSubscription).one()
    assert row.subscription_id == response.json()["subscription_id"] == "sub-1"
    assert row.department_id == f.department.id
    assert row.credential_id == f.credential.id
    assert row.is_active
    assert (
        datetime.now(timezone.utc)
        < row.expiration_time
        < datetime.now(timezone.utc) + timedelta(days=3)
    )
    assert f.client.get("/microsoft/subscriptions").json()["count"] == 1
    assert f.client.patch("/microsoft/subscriptions/sub-1").status_code == 200
    assert f.client.delete("/microsoft/subscriptions/sub-1").status_code == 200
    f.db.refresh(row)
    assert not row.is_active
    assert f.client.get("/microsoft/subscriptions").json() == {
        "subscriptions": [],
        "count": 0,
    }


READ_ROUTES = [
    ("/drives", "id", "drive-1"),
    ("/sites", "id", "site-1"),
    ("/onedrive/folders", "folders", None),
    ("/onedrive/folders/root/children", "items", None),
    ("/onedrive/files/file-1", "id", "file-1"),
    ("/sharepoint/sites/site-1/drives", "drives", None),
    ("/sharepoint/drives/drive-1/items", "items", None),
    ("/sharepoint/search?query=Lesson", "results", None),
]


@pytest.mark.parametrize("path,key,value", READ_ROUTES)
def test_authorized_browse_shapes_and_adapter_cleanup(
    microsoft_route, path, key, value
):
    f = microsoft_route
    response = f.client.get("/microsoft" + path)
    assert response.status_code == 200
    data = response.json()
    if isinstance(data, list):
        assert data[0][key] == value
    elif value is not None:
        assert data[key] == value
    else:
        assert data[key][0]["id"] in {"folder-1", "drive-1"}
    assert f.requests
    assert all(not Path(item._temp_dir).exists() for item in f.integrations)


@pytest.mark.parametrize(
    "path", [item[0] for item in READ_ROUTES] + ["/onedrive/files"]
)
def test_graph_failures_have_bounded_responses(microsoft_route, path):
    f = microsoft_route
    f.failure = 429
    response = f.client.get("/microsoft" + path)
    assert response.status_code == 503
    assert response.json() == {"detail": "Microsoft operation unavailable"}
    assert not f.rows(CloudFile).all()
    assert all(not Path(item._temp_dir).exists() for item in f.integrations)


@pytest.mark.parametrize(
    "path", ["/onedrive/files/file-1", "/onedrive/folders/root/children"]
)
def test_graph_missing_object_is_404(microsoft_route, path):
    microsoft_route.failure = 404
    assert microsoft_route.client.get("/microsoft" + path).status_code == 404


def tracked_file(f, *, provider="microsoft", scanned=True):
    scan_id = None
    if scanned:
        scan = Scan(
            id=str(uuid4()),
            department_id=f.department.id,
            scan_type=ScanType.PDF,
            status=ScanStatus.COMPLETED,
            file_name="Lesson.pdf",
        )
        f.db.add(scan)
        f.db.flush()
        scan_id = scan.id
    row = CloudFile(
        id=str(uuid4()),
        department_id=f.department.id,
        credential_id=f.credential.id,
        provider=provider,
        provider_file_id="file-1",
        file_name="Lesson.pdf",
        file_type="pdf",
        provider_version="v1",
        last_scan_id=scan_id,
    )
    f.db.add(row)
    f.db.commit()
    return row


@pytest.mark.parametrize(
    "action", ["scan/file", "remediate", "scan/sharepoint/file/file-1", "scan/folder"]
)
def test_queue_routes_return_real_persisted_job_ids(microsoft_route, action):
    f = microsoft_route
    row = tracked_file(f)
    response = f.client.post(
        "/microsoft/" + action, json={"file_id": row.id, "folder_id": "folder-1"}
    )
    assert response.status_code == 200
    data = response.json()
    ids = data["job_ids"] if "job_ids" in data else [data["job_id"]]
    assert len(ids) == 1
    job = f.db.get(CloudJobQueue, ids[0])
    assert job and job.department_id == f.department.id
    assert job.credential_id == f.credential.id
    assert job.cloud_file_id == row.id
    assert job.payload["cloud_file_id"] == row.id
    assert job.provider == "microsoft"
    assert job.status == "pending"
    response = f.client.get("/microsoft/jobs/" + job.id)
    assert response.status_code == 200
    assert response.json()["job_id"] == job.id
    response = f.client.get("/microsoft/jobs?status=pending&limit=1")
    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()] == [job.id]


@pytest.mark.parametrize("action", ["scan/file", "remediate"])
@pytest.mark.parametrize("condition", ["missing", "other_provider", "other_department"])
def test_file_queue_rejects_unavailable_scope(microsoft_route, action, condition):
    f = microsoft_route
    row = tracked_file(
        f, provider="google" if condition == "other_provider" else "microsoft"
    )
    if condition == "other_department":
        f.app.dependency_overrides[routes.get_current_api_key] = (
            lambda: SessionAccessIdentity(
                id="session-other",
                user_id="test-user-123",
                department_id="test-dept-456",
            )
        )
    file_id = "missing" if condition == "missing" else row.id
    assert (
        f.client.post("/microsoft/" + action, json={"file_id": file_id}).status_code
        == 404
    )
    assert f.rows(CloudJobQueue).count() == 0


def test_remediation_requires_scan(microsoft_route):
    row = tracked_file(microsoft_route, scanned=False)
    response = microsoft_route.client.post(
        "/microsoft/remediate", json={"file_id": row.id}
    )
    assert response.status_code == 400
    assert microsoft_route.rows(CloudJobQueue).count() == 0


@pytest.mark.parametrize(
    "action", ["scan/file", "remediate", "scan/sharepoint/file/file-1", "scan/folder"]
)
def test_queue_failure_rolls_back_and_does_not_return_success(
    microsoft_route, monkeypatch, action
):
    from sqlalchemy.exc import OperationalError

    f = microsoft_route
    row = tracked_file(f)
    original = routes.enqueue_cloud_job

    def fail_after_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise OperationalError(
            "statement", {}, Exception("private database diagnostic")
        )

    monkeypatch.setattr(routes, "enqueue_cloud_job", fail_after_insert)
    response = f.client.post(
        "/microsoft/" + action, json={"file_id": row.id, "folder_id": "folder-1"}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Microsoft operation unavailable"}
    assert f.rows(CloudJobQueue).count() == 0


@pytest.mark.parametrize(
    "path,payload",
    [
        ("scan/file", {}),
        ("scan/file", {"file_id": ""}),
        ("scan/folder", {}),
        ("remediate", {}),
        ("onedrive/upload", {}),
        ("sharepoint/upload", {"file_path": "fixture.pdf"}),
        ("subscriptions", {}),
        ("subscriptions", {**SUB, "change_types": ["created"]}),
        ("subscriptions", {**SUB, "resource": "/unsupported"}),
        ("subscriptions", {**SUB, "notification_url": "http://example.test/hook"}),
    ],
)
def test_invalid_bodies_are_rejected_before_provider_requests(
    microsoft_route, path, payload
):
    assert (
        microsoft_route.client.post("/microsoft/" + path, json=payload).status_code
        == 422
    )
    assert not microsoft_route.requests


@pytest.mark.parametrize(
    "path",
    [
        "/onedrive/files?page_size=0",
        "/onedrive/files?page_size=101",
        "/jobs?limit=0",
        "/jobs?limit=101",
        "/sharepoint/search",
        "/sharepoint/search?query=",
    ],
)
def test_invalid_queries_are_rejected(microsoft_route, path):
    assert microsoft_route.client.get("/microsoft" + path).status_code == 422
    assert not microsoft_route.requests


@pytest.mark.parametrize(
    "path",
    [
        "/drives",
        "/sites",
        "/onedrive/files",
        "/onedrive/folders",
        "/onedrive/files/file-1",
        "/onedrive/files/file-1/content",
        "/sharepoint/drives/drive-1/items",
        "/subscriptions",
        "/account",
    ],
)
def test_other_department_cannot_use_credential(microsoft_route, path):
    f = microsoft_route
    f.app.dependency_overrides[routes.get_current_api_key] = (
        lambda: SessionAccessIdentity(
            id="session-other", user_id="test-user-123", department_id="test-dept-456"
        )
    )
    assert f.client.get("/microsoft" + path).status_code == 404
    assert not f.requests


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("get", "/onedrive/files", None),
        ("post", "/scan/folder", {"folder_id": "folder-1"}),
        ("post", "/subscriptions", SUB),
        ("patch", "/subscriptions/sub-1", None),
        ("delete", "/subscriptions/sub-1", None),
    ],
)
def test_missing_authentication_is_rejected(microsoft_route, method, path, payload):
    def denied():
        raise HTTPException(401, "Authentication required")

    microsoft_route.app.dependency_overrides[routes.get_current_api_key] = denied
    assert (
        microsoft_route.client.request(
            method, "/microsoft" + path, json=payload
        ).status_code
        == 401
    )
    assert not microsoft_route.requests


def test_entitlement_denial_does_not_call_provider(microsoft_route, monkeypatch):
    async def denied(*args, **kwargs):
        raise HTTPException(403, "Feature not available")

    monkeypatch.setattr(routes, "require_feature", denied)
    assert microsoft_route.client.get("/microsoft/drives").status_code == 403
    assert not microsoft_route.requests


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_subscription_unknown_or_other_department_is_404(microsoft_route, method):
    f = microsoft_route
    assert (
        f.client.request(method, "/microsoft/subscriptions/missing").status_code == 404
    )
    assert f.client.post("/microsoft/subscriptions", json=SUB).status_code == 200
    f.requests.clear()
    f.app.dependency_overrides[routes.get_current_api_key] = (
        lambda: SessionAccessIdentity(
            id="session-other", user_id="test-user-123", department_id="test-dept-456"
        )
    )
    assert f.client.request(method, "/microsoft/subscriptions/sub-1").status_code == 404
    assert not f.requests


@pytest.mark.parametrize("failure", [429, 500])
def test_subscription_create_failure_leaves_nonactive_intent_and_blocks_retry(
    microsoft_route, failure
):
    f = microsoft_route
    f.failure = failure
    response = f.client.post("/microsoft/subscriptions", json=SUB)
    assert response.status_code == 503
    row = f.rows(CloudWebhookSubscription).one()
    assert not row.is_active
    assert row.renewal_status == "requesting"
    assert f.client.post("/microsoft/subscriptions", json=SUB).status_code == 409
    assert len(f.requests) == 1


@pytest.mark.parametrize("method,expected", [("patch", 503), ("delete", 502)])
def test_subscription_mutation_failure_preserves_local_state(
    microsoft_route, method, expected
):
    f = microsoft_route
    assert f.client.post("/microsoft/subscriptions", json=SUB).status_code == 200
    row = f.rows(CloudWebhookSubscription).one()
    expiration = row.expiration_time
    f.failure = 500
    assert (
        f.client.request(method, "/microsoft/subscriptions/sub-1").status_code
        == expected
    )
    f.db.refresh(row)
    assert row.is_active and row.expiration_time == expiration


@pytest.mark.parametrize("after_provider", [False, True])
def test_subscription_commit_failure_is_not_false_success(
    microsoft_route, monkeypatch, after_provider
):
    from sqlalchemy.exc import OperationalError

    f = microsoft_route
    commit = f.db.commit
    calls = 0

    def fail_commit():
        nonlocal calls
        calls += 1
        if calls == (2 if after_provider else 1):
            raise OperationalError("commit", {}, Exception("private diagnostic"))
        commit()

    monkeypatch.setattr(f.db, "commit", fail_commit)
    response = f.client.post("/microsoft/subscriptions", json=SUB)
    assert response.status_code == 503
    rows = f.rows(CloudWebhookSubscription).all()
    if after_provider:
        assert len(f.requests) == 1
        assert len(rows) == 1 and not rows[0].is_active
        assert rows[0].renewal_status == "requesting"
        assert f.client.post("/microsoft/subscriptions", json=SUB).status_code == 409
    else:
        assert not f.requests and not rows


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_subscription_post_provider_commit_failure_requires_reconciliation(
    microsoft_route, monkeypatch, method
):
    from sqlalchemy.exc import OperationalError

    f = microsoft_route
    assert f.client.post("/microsoft/subscriptions", json=SUB).status_code == 200
    commit = f.db.commit
    calls = 0

    def fail_commit():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OperationalError("commit", {}, Exception("private diagnostic"))
        commit()

    monkeypatch.setattr(f.db, "commit", fail_commit)
    response = f.client.request(method, "/microsoft/subscriptions/sub-1")
    assert response.status_code == 503
    row = f.rows(CloudWebhookSubscription).one()
    assert row.is_active and row.renewal_status == "requesting"
    request_count = len(f.requests)
    assert f.client.request(method, "/microsoft/subscriptions/sub-1").status_code == 409
    assert len(f.requests) == request_count


@pytest.mark.parametrize("path", ["/onedrive/files", "/scan/folder"])
def test_file_and_folder_commit_failure_is_atomic(microsoft_route, monkeypatch, path):
    from sqlalchemy.exc import OperationalError

    f = microsoft_route

    def fail_commit():
        raise OperationalError("commit", {}, Exception("private diagnostic"))

    monkeypatch.setattr(f.db, "commit", fail_commit)
    response = (
        f.client.get("/microsoft" + path)
        if path == "/onedrive/files"
        else f.client.post("/microsoft" + path, json={"folder_id": "folder-1"})
    )
    assert response.status_code == 503
    assert f.rows(CloudFile).count() == 0
    assert f.rows(CloudJobQueue).count() == 0
    f.db.refresh(f.credential)
    assert f.credential.last_sync_at is None


@pytest.mark.parametrize("path", ["files", "folders"])
@pytest.mark.parametrize("invalid_refresh", [False, True])
def test_expired_credential_refresh_is_persisted_or_deactivated(
    microsoft_route, monkeypatch, invalid_refresh, path
):
    from unittest.mock import AsyncMock

    f = microsoft_route
    f.credential.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    f.db.commit()
    manager = routes.get_token_manager()
    refresh = (
        AsyncMock(side_effect=RuntimeError("provider unavailable"))
        if invalid_refresh
        else AsyncMock(
            return_value=(
                "new-access",
                "new-refresh",
                datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
    )
    monkeypatch.setattr(manager, "refresh_microsoft_token", refresh)
    monkeypatch.setattr(routes, "get_token_manager", lambda: manager)
    response = f.client.get("/microsoft/onedrive/" + path)
    assert response.status_code == (401 if invalid_refresh else 200)
    f.db.refresh(f.credential)
    if invalid_refresh:
        assert not f.credential.is_active
        assert not f.requests
    else:
        assert manager.decrypt_token(f.credential.access_token) == "new-access"
        assert manager.decrypt_token(f.credential.refresh_token) == "new-refresh"
    refresh.assert_awaited_once()


def test_job_scope_filters_other_provider_and_department(microsoft_route):
    f = microsoft_route
    job = CloudJobQueue(
        id=str(uuid4()),
        department_id=f.department.id,
        provider="google",
        job_type="scan",
        payload={},
        status="pending",
        priority=5,
    )
    f.db.add(job)
    f.db.commit()
    assert f.client.get("/microsoft/jobs/" + job.id).status_code == 404
    assert f.client.get("/microsoft/jobs").json() == []
    job.provider = "microsoft"
    f.db.commit()
    f.app.dependency_overrides[routes.get_current_api_key] = (
        lambda: SessionAccessIdentity(
            id="session-other", user_id="test-user-123", department_id="test-dept-456"
        )
    )
    assert f.client.get("/microsoft/jobs/" + job.id).status_code == 404
    assert f.client.get("/microsoft/jobs").json() == []


def test_download_failure_cleans_owned_storage(microsoft_route):
    f = microsoft_route
    f.failure = 500
    response = f.client.get("/microsoft/onedrive/files/file-1/content")
    assert response.status_code == 502
    assert response.json() == {"detail": "Microsoft download unavailable"}
    assert all(not Path(item._temp_dir).exists() for item in f.integrations)


@pytest.mark.asyncio
async def test_worker_does_not_retry_unresolved_route_subscription(microsoft_route):
    from unittest.mock import AsyncMock
    from src.jobs.webhook_refresh_job import handle_webhook_refresh_job

    f = microsoft_route
    assert f.client.post("/microsoft/subscriptions", json=SUB).status_code == 200
    row = f.rows(CloudWebhookSubscription).one()
    row.renewal_status = "requesting"
    row.pending_renewal_channel_id = str(uuid4())
    row.pending_renewal_started_at = datetime.now(timezone.utc)
    f.db.commit()
    manager = SimpleNamespace(
        refresh_if_expired=AsyncMock(side_effect=AssertionError("must not retry"))
    )
    job = SimpleNamespace(
        payload={"subscription_id": row.id},
        department_id=f.department.id,
        credential_id=f.credential.id,
        provider="microsoft",
    )
    result = await handle_webhook_refresh_job(job, f.db, manager)
    assert result.code == "webhook_provider_outcome_indeterminate"
    manager.refresh_if_expired.assert_not_awaited()


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_expired_refresh_commits_before_subscription_row_lock(
    microsoft_route, monkeypatch, method
):
    from sqlalchemy import event

    f = microsoft_route
    assert f.client.post("/microsoft/subscriptions", json=SUB).status_code == 200
    f.credential.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    f.db.commit()
    observed = []
    connection = f.db.get_bind()

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        if "cloud_webhook_subscriptions" in statement and "FOR UPDATE" in statement:
            observed.append("lock")

    event.listen(connection, "before_cursor_execute", record_sql)
    manager = routes.get_token_manager()

    async def refresh(*args, **kwargs):
        observed.append("refresh")
        return (
            "new-access",
            "new-refresh",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )

    monkeypatch.setattr(manager, "refresh_microsoft_token", refresh)
    monkeypatch.setattr(routes, "get_token_manager", lambda: manager)
    commit = f.db.commit

    def record_commit():
        observed.append("commit")
        commit()

    monkeypatch.setattr(f.db, "commit", record_commit)
    try:
        response = f.client.request(method, "/microsoft/subscriptions/sub-1")
    finally:
        event.remove(connection, "before_cursor_execute", record_sql)
    assert response.status_code == 200
    assert observed == ["refresh", "commit", "lock", "commit", "commit"]


@pytest.mark.parametrize(
    "condition", ["missing", "other_department", "other_provider", "old_credential"]
)
def test_sharepoint_scan_requires_current_scoped_tracking(microsoft_route, condition):
    f = microsoft_route
    row = tracked_file(
        f, provider="google" if condition == "other_provider" else "microsoft"
    )
    if condition == "other_department":
        row.department_id = "test-dept-456"
    if condition == "old_credential":
        stale = CloudOAuthCredentials(
            id=str(uuid4()),
            department_id=f.department.id,
            provider="microsoft",
            access_token=f.credential.access_token,
            refresh_token=f.credential.refresh_token,
            token_expires_at=f.credential.token_expires_at,
            is_active=False,
        )
        f.db.add(stale)
        f.db.flush()
        row.credential_id = stale.id
    f.db.commit()
    file_id = "missing" if condition == "missing" else row.provider_file_id
    response = f.client.post("/microsoft/scan/sharepoint/file/" + file_id)
    assert response.status_code == 404
    assert f.rows(CloudJobQueue).count() == 0
    assert not f.requests


@pytest.mark.asyncio
async def test_sharepoint_scan_hands_persisted_file_to_worker_handler(
    microsoft_route, monkeypatch
):
    from src.jobs import cloud_scan_job

    f = microsoft_route
    row = tracked_file(f)
    response = f.client.post("/microsoft/scan/sharepoint/file/" + row.provider_file_id)
    assert response.status_code == 200
    job = f.db.get(CloudJobQueue, response.json()["job_id"])
    seen = []

    async def controlled_scan(self, db):
        seen.append((self.cloud_file.id, self.credential.id, db))
        return {"success": True, "scan_id": row.last_scan_id}

    monkeypatch.setattr(cloud_scan_job.CloudScanJob, "run", controlled_scan)
    result = await cloud_scan_job.handle_scan_job(job, f.db, routes.get_token_manager())
    assert result == {"success": True, "scan_id": row.last_scan_id}
    assert seen == [(row.id, f.credential.id, f.db)]
    assert not f.requests


@pytest.mark.parametrize("path", ["/jobs", "/jobs/missing"])
def test_unexpected_job_query_failure_is_bounded_500(
    microsoft_route, monkeypatch, path
):
    def fail_query(*args, **kwargs):
        raise RuntimeError("private database diagnostic")

    monkeypatch.setattr(microsoft_route.db, "query", fail_query)
    response = microsoft_route.client.get("/microsoft" + path)
    assert response.status_code == 500
    assert response.json() == {"detail": "Microsoft operation unavailable"}


@pytest.mark.parametrize(
    "failure,expected", [("provider", 502), ("connection", 503), ("timeout", 503)]
)
def test_typed_provider_failures_keep_provider_status(
    microsoft_route, monkeypatch, failure, expected
):
    from src.integrations.cloud_base import CloudIntegrationError

    failures = {
        "provider": CloudIntegrationError,
        "connection": httpx.ConnectError,
        "timeout": httpx.ReadTimeout,
    }

    async def fail_integration(*args, **kwargs):
        raise failures[failure]("private provider diagnostic")

    monkeypatch.setattr(routes, "get_microsoft_integration", fail_integration)
    response = microsoft_route.client.get("/microsoft/onedrive/files")
    assert response.status_code == expected
    assert response.json() == {"detail": "Microsoft operation unavailable"}
