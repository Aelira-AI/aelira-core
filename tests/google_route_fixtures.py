"""Google routes with real tenant rows and fail-closed provider adapters.

Identity is supplied; token encryption, account lookup and queue persistence are
real. Route commits release savepoints inside a rollback-only outer transaction.
No live Google transport, workers or browser are exercised.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api import google_routes as routes
from src.config.settings import get_settings
from src.db.database import get_db_dependency
from src.db.models import (
    APIKey,
    CloudFile,
    CloudJobQueue,
    CloudOAuthCredentials,
    Department,
    User,
)
from src.integrations.cloud_base import (
    CloudExportResult,
    CloudFileInfo,
    CloudFolderInfo,
)
from src.integrations.google_workspace.google_drive import GoogleDriveIntegration
from src.integrations.google_workspace.google_docs import GoogleDocsService
from src.integrations.google_workspace.google_slides import GoogleSlidesService
from src.integrations.google_workspace.google_sheets import GoogleSheetsService
from tests.conftest import require_disposable_postgres_url


@pytest.fixture
def google_route(monkeypatch):
    engine = create_engine(
        require_disposable_postgres_url(get_settings().database_url, destructive=False)
    )
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    # Other modules can leave committed rows in the shared suite database.
    # Exclude only those IDs, so unexpected new rows in any tenant still count.
    existing_ids = {
        model: tuple(row.id for row in db.query(model.id))
        for model in (CloudFile, CloudJobQueue)
    }

    def rows(model):
        return db.query(model).filter(model.id.not_in(existing_ids[model]))

    departments = [
        Department(
            id=str(uuid4()),
            name=name,
            institution="Example University",
            contact_email="admin@example.edu",
            tier="department",
        )
        for name in ("Google contracts", "Other department")
    ]
    db.add_all(departments)
    db.flush()
    users = [
        User(
            id=str(uuid4()),
            department_id=department.id,
            email=f"{uuid4()}@example.edu",
            name="Example Faculty",
        )
        for department in departments
    ]
    db.add_all(users)
    db.flush()
    key = APIKey(
        id=str(uuid4()),
        department_id=departments[0].id,
        user_id=users[0].id,
        key_hash=str(uuid4()),
        key_prefix="synthetic",
        name="Fixture",
    )
    manager = routes.get_token_manager()
    credentials = [
        CloudOAuthCredentials(
            id=str(uuid4()),
            department_id=department.id,
            provider="google",
            access_token=manager.encrypt_token(f"fixture-access-{index}"),
            refresh_token=manager.encrypt_token(f"fixture-refresh-{index}"),
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            provider_email=f"faculty{index}@example.edu",
            provider_name="Example Faculty",
        )
        for index, department in enumerate(departments)
    ]
    db.add_all(credentials + [key])
    db.flush()
    files = [
        CloudFile(
            id=str(uuid4()),
            department_id=credential.department_id,
            credential_id=credential.id,
            provider="google",
            provider_file_id=f"provider-{index}",
            file_name=f"File {index}.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            provider_version="v1",
            needs_rescan=True,
        )
        for index, credential in enumerate(credentials)
    ]
    db.add_all(files)
    db.commit()
    adapter = create_autospec(GoogleDriveIntegration, instance=True, spec_set=True)
    for name in (
        "list_files",
        "list_all_files",
        "list_folders",
        "get_file_info",
        "download_file",
        "upload_file",
    ):
        getattr(adapter, name).side_effect = AssertionError(
            f"Unexpected provider operation: {name}"
        )
    adapter.close.return_value = None
    factory = create_autospec(
        GoogleDriveIntegration, spec_set=True, return_value=adapter
    )
    monkeypatch.setattr(routes, "GoogleDriveIntegration", factory)
    exports = {}
    export_factories = {}
    for name, cls, method in [
        ("docs", GoogleDocsService, "export_to_docx"),
        ("slides", GoogleSlidesService, "export_to_pptx"),
        ("sheets", GoogleSheetsService, "export_to_xlsx"),
    ]:
        export = create_autospec(cls, instance=True, spec_set=True)
        getattr(export, method).return_value = b"PK\x00\xffsynthetic-office-bytes"
        export_factory = create_autospec(cls, spec_set=True, return_value=export)
        monkeypatch.setattr(routes, cls.__name__, export_factory)
        exports[name] = export
        export_factories[name] = export_factory
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db_dependency] = lambda: db
    app.dependency_overrides[routes.get_current_api_key] = lambda: key
    client = TestClient(app, raise_server_exceptions=False)
    fixture = SimpleNamespace(
        db=db,
        rows=rows,
        client=client,
        app=app,
        key=key,
        departments=departments,
        users=users,
        credential=credentials[0],
        other_credential=credentials[1],
        file=files[0],
        other_file=files[1],
        adapter=adapter,
        factory=factory,
        manager=manager,
        exports=exports,
        export_factories=export_factories,
    )
    try:
        yield fixture
    finally:
        client.close()
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def file_info(identifier="listed-doc", **kwargs):
    return CloudFileInfo(
        id=identifier,
        name="Lecture notes",
        mime_type="application/vnd.google-apps.document",
        parent_id="folder-1",
        version="v2",
        size_bytes=321,
        **kwargs,
    )


def allow_listing(fixture, files=None, token=None):
    fixture.adapter.list_files.side_effect = None
    fixture.adapter.list_files.return_value = (
        [file_info()] if files is None else files,
        token,
    )


def allow_folder(fixture, files=None):
    values = [file_info()] if files is None else files

    async def iterate(**kwargs):
        for value in values:
            yield value

    fixture.adapter.list_all_files.side_effect = iterate
    # The old implementation uses list_files and still gets strict DTOs.
    allow_listing(fixture, values)


def allow_download(fixture):
    async def download(file_id, local_path=None):
        assert local_path is not None, "Route must own the temporary download path"
        Path(local_path).write_bytes(b"PK\x00\xffdownload")
        return CloudExportResult(
            success=True, local_path=local_path, mime_type="application/pdf"
        )

    fixture.adapter.download_file.side_effect = download


def allow_folders(fixture):
    fixture.adapter.list_folders.side_effect = None
    fixture.adapter.list_folders.return_value = [
        CloudFolderInfo(
            id="folder-1",
            name="Teaching",
            parent_id="root",
            web_view_link="https://drive.example/folder-1",
        )
    ]
