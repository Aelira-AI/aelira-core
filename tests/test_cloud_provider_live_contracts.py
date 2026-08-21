"""Live HTTP-seam coverage for cloud provider DTO and job contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.db.models import CloudFile, CloudProvider
from src.integrations.cloud_base import CloudAuthError
from src.integrations.google_workspace.google_drive import GoogleDriveIntegration
from src.integrations.microsoft_365.onedrive import OneDriveIntegration
from src.jobs.cloud_scan_job import CloudScanJob
from src.jobs.cloud_sync_job import CloudSyncJob

GOOGLE_FILE = {
    "id": "google-file-1",
    "name": "Lesson.docx",
    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "size": "17",
    "modifiedTime": "2026-08-20T01:02:03Z",
    "parents": ["google-folder-1"],
    "version": "7",
}
GOOGLE_FOLDER = {
    "id": "google-folder-1",
    "name": "Course",
    "mimeType": "application/vnd.google-apps.folder",
    "parents": ["selected-root"],
}
MICROSOFT_FILE = {
    "id": "ms-file-1",
    "name": "Slides.pptx",
    "size": 23,
    "file": {
        "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    },
    "parentReference": {"id": "ms-folder-1", "path": "/drive/root:/Course"},
    "lastModifiedDateTime": "2026-08-20T04:05:06Z",
    "eTag": "etag-9",
}
MICROSOFT_FOLDER = {
    "id": "ms-folder-1",
    "name": "Course",
    "size": 0,
    "folder": {"childCount": 1},
    "parentReference": {"id": "selected-root", "path": "/drive/root:"},
}


def _async_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _credential(provider: str):
    return SimpleNamespace(
        id=f"{provider}-credential",
        department_id="department-1",
        provider=provider,
        last_sync_at=None,
    )


def _sync_db():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


@pytest.mark.asyncio
async def test_google_list_files_returns_canonical_cloud_file_info():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"files": [GOOGLE_FILE]}, request=request)

    integration = GoogleDriveIntegration(
        access_token="token", credential_id="google-credential"
    )
    integration._client = _async_client(handler)
    try:
        files, next_token = await integration.list_files(folder_id="selected-root")
    finally:
        await integration.close()

    assert next_token is None
    assert len(files) == 1
    info = files[0]
    assert (
        info.id,
        info.name,
        info.mime_type,
        info.size_bytes,
        info.modified_at,
        info.parent_id,
        info.path,
        info.is_folder,
    ) == (
        "google-file-1",
        "Lesson.docx",
        GOOGLE_FILE["mimeType"],
        17,
        datetime(2026, 8, 20, 1, 2, 3, tzinfo=timezone.utc),
        "google-folder-1",
        None,
        False,
    )


@pytest.mark.asyncio
async def test_onedrive_list_files_returns_canonical_folder_and_file_dtos():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"value": [MICROSOFT_FOLDER, MICROSOFT_FILE]},
            request=request,
        )

    integration = OneDriveIntegration(
        access_token="token", credential_id="microsoft-credential"
    )
    integration._http_client = _async_client(handler)
    try:
        items, next_token = await integration.list_files(folder_id="selected-root")
    finally:
        await integration.close()

    assert next_token is None
    assert [(item.id, item.name, item.is_folder) for item in items] == [
        ("ms-folder-1", "Course", True),
        ("ms-file-1", "Slides.pptx", False),
    ]
    file_info = items[1]
    assert file_info.size_bytes == 23
    assert file_info.modified_at == datetime(2026, 8, 20, 4, 5, 6, tzinfo=timezone.utc)
    assert file_info.parent_id == "ms-folder-1"
    assert file_info.path == "/drive/root:/Course/Slides.pptx"


@pytest.mark.parametrize(
    ("integration_type", "client_attribute"),
    [
        (GoogleDriveIntegration, "_client"),
        (OneDriveIntegration, "_http_client"),
    ],
)
@pytest.mark.asyncio
async def test_provider_auth_failures_are_typed(integration_type, client_attribute):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "expired"}, request=request)

    integration = integration_type(access_token="token", credential_id="credential")
    setattr(integration, client_attribute, _async_client(handler))
    try:
        with pytest.raises(CloudAuthError):
            await integration.list_files()
    finally:
        await integration.close()


@pytest.mark.parametrize(
    ("provider", "integration_type", "file_payload", "expected_id"),
    [
        (
            CloudProvider.GOOGLE.value,
            GoogleDriveIntegration,
            GOOGLE_FILE,
            "google-file-1",
        ),
        (
            CloudProvider.MICROSOFT.value,
            OneDriveIntegration,
            MICROSOFT_FILE,
            "ms-file-1",
        ),
    ],
)
@pytest.mark.asyncio
async def test_sync_job_uses_actual_provider_dto_and_persists_canonical_fields(
    monkeypatch, provider, integration_type, file_payload, expected_id
):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = (
            {"files": [file_payload]}
            if provider == CloudProvider.GOOGLE.value
            else {"value": [file_payload]}
        )
        return httpx.Response(200, json=payload, request=request)

    clients = []

    async def get_client(self):
        client = _async_client(handler)
        clients.append(client)
        return client

    monkeypatch.setattr(integration_type, "_get_client", get_client)
    credential = _credential(provider)
    token_manager = SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token"))
    db = _sync_db()
    sync = CloudSyncJob(credential, token_manager)
    monkeypatch.setattr(sync, "_enqueue_scan", lambda *_args, **_kwargs: False)

    try:
        result = await sync.run(db, folder_id="selected-root", recursive=False)
    finally:
        for client in clients:
            await client.aclose()

    persisted = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], CloudFile)
    )
    assert result["files_discovered"] == 1
    assert persisted.provider_file_id == expected_id
    assert persisted.provider_parent_id == (
        "google-folder-1" if provider == CloudProvider.GOOGLE.value else "ms-folder-1"
    )
    assert persisted.file_size_bytes == (17 if provider == "google" else 23)
    assert persisted.provider_modified_at is not None
    assert persisted.mime_type == file_payload.get(
        "mimeType", file_payload.get("file", {}).get("mimeType")
    )


@pytest.mark.asyncio
async def test_google_sync_normalizes_recursive_folder_traversal(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q", "")
        if "mimeType = 'application/vnd.google-apps.folder'" in query:
            folders = [GOOGLE_FOLDER] if "'selected-root' in parents" in query else []
            return httpx.Response(200, json={"files": folders}, request=request)
        files = [GOOGLE_FILE] if "'google-folder-1' in parents" in query else []
        return httpx.Response(200, json={"files": files}, request=request)

    clients = []

    async def get_client(self):
        client = _async_client(handler)
        clients.append(client)
        return client

    monkeypatch.setattr(GoogleDriveIntegration, "_get_client", get_client)
    credential = _credential(CloudProvider.GOOGLE.value)
    token_manager = SimpleNamespace(refresh_if_expired=AsyncMock(return_value="token"))
    db = _sync_db()
    sync = CloudSyncJob(credential, token_manager)
    monkeypatch.setattr(sync, "_enqueue_scan", lambda *_args, **_kwargs: False)

    try:
        result = await sync.run(db, folder_id="selected-root", recursive=True)
    finally:
        for client in clients:
            await client.aclose()

    persisted = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], CloudFile)
    ]
    assert result["files_discovered"] == 1
    assert [row.provider_file_id for row in persisted] == ["google-file-1"]
    assert persisted[0].provider_parent_id == "google-folder-1"


@pytest.mark.asyncio
async def test_google_scan_setup_uses_exact_live_client_constructor(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"document bytes", request=request)
        return httpx.Response(200, json=GOOGLE_FILE, request=request)

    client = _async_client(handler)

    async def get_client(self):
        return client

    credential = _credential(CloudProvider.GOOGLE.value)
    cloud_file = SimpleNamespace(provider_file_id="google-file-1")
    job = CloudScanJob(credential, cloud_file, MagicMock())
    original = GoogleDriveIntegration._get_client
    GoogleDriveIntegration._get_client = get_client
    try:
        result = await job._download_google("token", str(tmp_path / "Lesson.docx"))
    finally:
        GoogleDriveIntegration._get_client = original
        await client.aclose()

    assert result["success"] is True
    assert result["local_path"] == str(tmp_path / "Lesson.docx")
    assert result["mime_type"] == GOOGLE_FILE["mimeType"]
