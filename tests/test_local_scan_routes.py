"""HTTP API enqueue integration for local PDF and Office uploads.

Real fixtures cross document validation, local file storage and the PostgreSQL
Scan/CloudJobQueue enqueue boundary. Processing and remediation belong to the
worker suites; a successful acknowledgement here means durable pending work.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from conftest import require_disposable_postgres_url
from src.api.education._shared import get_api_key_or_mock
from src.api.main import app
from src.db import database
from src.db.models import (
    Base,
    CloudJobQueue,
    Department,
    Scan,
    ScanStatus,
    ScanType,
    SecurityScanResult,
    User,
    UserRole,
)
from src.utils import file_storage

FIXTURES = Path(__file__).parent / "fixtures" / "document_stack"
DEPARTMENT = "scan-contract-department"
USER = "scan-contract-user"


@dataclass(frozen=True)
class UploadCase:
    route: str
    filename: str
    scan_type: ScanType
    default_options: dict
    wrong_extension_message: str

    @property
    def path(self):
        return f"/education/{self.route}/scan"

    def upload(self):
        return {"file": (self.filename, (FIXTURES / self.filename).read_bytes())}


CASES = [
    UploadCase(
        "pdf",
        "metadata.pdf",
        ScanType.PDF,
        {"generate_alt_text": False, "enhance_descriptions": True},
        "File must be a PDF",
    ),
    UploadCase(
        "powerpoint",
        "course.pptx",
        ScanType.POWERPOINT,
        {"generate_alt_text": False, "validate_alt_text": False},
        "File must be a PowerPoint (.pptx)",
    ),
    UploadCase(
        "word",
        "course.docx",
        ScanType.WORD,
        {"generate_alt_text": False, "validate_alt_text": False},
        "File must be a Word document (.docx)",
    ),
    UploadCase(
        "excel",
        "course.xlsx",
        ScanType.EXCEL,
        {"generate_chart_descriptions": False, "generate_alt_text": False},
        "File must be an Excel spreadsheet (.xlsx)",
    ),
]


@pytest.fixture(params=CASES, ids=lambda case: case.route)
def upload_case(request):
    return request.param


@pytest.fixture(scope="module")
def scan_engine():
    database_url = require_disposable_postgres_url(
        os.environ["DATABASE_URL"], destructive=False
    )
    admin_engine = create_engine(database_url)
    schema = f"scan_contract_{uuid.uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        database_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    try:
        # The queue and scans have foreign keys to the wider managed-artifact
        # graph. Retain the production metadata instead of weakening constraints.
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def session_factory(scan_engine):
    with scan_engine.connect() as connection:
        transaction = connection.begin()
        factory = sessionmaker(
            bind=connection, join_transaction_mode="create_savepoint"
        )
        with factory() as db:
            db.add(
                Department(
                    id=DEPARTMENT,
                    name="Example Department",
                    institution="Example University",
                    contact_email="admin@example.edu",
                    tier="department",
                )
            )
            db.flush()
            db.add(
                User(
                    id=USER,
                    email="admin@example.edu",
                    name="Example Admin",
                    department_id=DEPARTMENT,
                    role=UserRole.ADMIN,
                )
            )
            db.commit()
        try:
            yield factory
        finally:
            transaction.rollback()


@pytest.fixture
def client(session_factory, monkeypatch, tmp_path):
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.pop(database.get_db_dependency, None)
    app.dependency_overrides[get_api_key_or_mock] = lambda: (None, USER, DEPARTMENT)
    # Keep the actual request dependency and its exception rollback semantics.
    monkeypatch.setattr(database, "SessionLocal", session_factory)
    monkeypatch.setattr(file_storage, "UPLOAD_BASE_DIR", tmp_path)
    test_client = TestClient(app, raise_server_exceptions=False)
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def assert_no_scan_or_job(session_factory):
    with session_factory() as db:
        assert db.query(Scan).count() == 0
        assert db.query(CloudJobQueue).count() == 0


@pytest.mark.parametrize("use_defaults", [True, False], ids=["defaults", "explicit"])
def test_upload_persists_scan_and_bound_queue_input(
    client, session_factory, tmp_path, upload_case, use_defaults
):
    options = (
        upload_case.default_options
        if use_defaults
        else {key: not value for key, value in upload_case.default_options.items()}
    )
    response = client.post(
        upload_case.path,
        files=upload_case.upload(),
        params={} if use_defaults else options,
    )
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["status"] == "PROCESSING"
    assert result["progress"] == 0
    with session_factory() as db:
        scan = db.query(Scan).one()
        job = db.query(CloudJobQueue).one()
        assert scan.id == result["scan_id"]
        assert scan.status == ScanStatus.PROCESSING
        assert scan.scan_type == upload_case.scan_type
        assert scan.user_id == USER
        assert scan.department_id == DEPARTMENT
        assert scan.document_source == "standalone"
        assert scan.document_id
        assert scan.file_name == upload_case.filename
        original = (FIXTURES / upload_case.filename).read_bytes()
        assert scan.file_size_bytes == len(original)
        expected_path = tmp_path / DEPARTMENT / scan.id / upload_case.filename
        assert Path(scan.storage_path) == expected_path
        assert expected_path.read_bytes() == original
        assert job.department_id == DEPARTMENT
        assert job.job_type == "scan"
        assert job.status == "pending"
        assert job.dedupe_key == f"local-scan:{scan.id}"
        assert job.payload == {
            "scan_kind": f"local_{upload_case.route}",
            "scan_id": scan.id,
            "options": options,
            "input_sha256": hashlib.sha256(original).hexdigest(),
        }
        validation = db.query(SecurityScanResult).one()
        assert validation.department_id == DEPARTMENT
        assert validation.file_hash == job.payload["input_sha256"]
        assert validation.was_blocked is False


@pytest.mark.parametrize("body_kind", ["missing", "text_field"])
def test_upload_requires_file_body(client, session_factory, upload_case, body_kind):
    body = (
        {}
        if body_kind == "missing"
        else {"data": {"file": "ordinary text instead of upload"}}
    )
    response = client.post(upload_case.path, **body)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "file"]
    assert_no_scan_or_job(session_factory)


def test_upload_rejects_wrong_extension(client, session_factory, tmp_path, upload_case):
    response = client.post(
        upload_case.path, files={"file": ("notes.txt", b"Ordinary course notes")}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": upload_case.wrong_extension_message}
    assert_no_scan_or_job(session_factory)
    assert list(tmp_path.iterdir()) == []


def test_upload_rejects_invalid_boolean_option(client, session_factory, upload_case):
    response = client.post(
        upload_case.path,
        files=upload_case.upload(),
        params={"generate_alt_text": "unknown"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "generate_alt_text"]
    assert_no_scan_or_job(session_factory)


def test_storage_failure_rolls_back_scan_without_acknowledgement(
    client, session_factory, monkeypatch, upload_case
):
    attempted = []

    def unavailable(directory):
        attempted.append(directory)
        raise OSError("fixture storage unavailable")

    monkeypatch.setattr(file_storage, "ensure_storage_dir", unavailable)
    response = client.post(upload_case.path, files=upload_case.upload())
    assert response.status_code == 500
    assert len(attempted) == 1
    assert_no_scan_or_job(session_factory)


def test_queue_write_failure_rolls_back_scan_without_acknowledgement(
    client, session_factory, scan_engine, upload_case
):
    attempted = []

    def unavailable(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith("insert into cloud_job_queue"):
            attempted.append(statement)
            raise RuntimeError("fixture queue write unavailable")

    event.listen(scan_engine, "before_cursor_execute", unavailable)
    try:
        response = client.post(upload_case.path, files=upload_case.upload())
    finally:
        event.remove(scan_engine, "before_cursor_execute", unavailable)
    assert response.status_code == 500
    assert len(attempted) == 1
    assert_no_scan_or_job(session_factory)


def test_upload_requires_authentication(
    client, session_factory, monkeypatch, tmp_path, upload_case
):
    from src.config.settings import get_settings

    app.dependency_overrides.pop(get_api_key_or_mock)
    monkeypatch.setattr(get_settings(), "allow_mock_auth", False)
    response = client.post(upload_case.path, files=upload_case.upload())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"].startswith("Authentication required.")
    assert_no_scan_or_job(session_factory)
    assert list(tmp_path.iterdir()) == []


def test_upload_rejects_exhausted_workspace_quota(
    client, session_factory, monkeypatch, tmp_path, upload_case
):
    from datetime import datetime, timedelta, timezone

    from src.config.settings import TIER_QUOTAS

    # A self-hosting operator can configure finite capacity; this is a
    # synthetic administrative limit, not a paid/free product distinction.
    monkeypatch.setitem(
        TIER_QUOTAS,
        "contract_capacity",
        {**TIER_QUOTAS["department"], "scans_per_month": 1},
    )
    reset_at = datetime.now(timezone.utc) + timedelta(days=1)
    with session_factory() as db:
        department = db.get(Department, DEPARTMENT)
        department.tier = "contract_capacity"
        department.scans_this_month = 1
        department.quota_reset_at = reset_at
        db.commit()

    response = client.post(upload_case.path, files=upload_case.upload())
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["error"] == "quota_exceeded"
    assert detail["remaining"]["scans"] == 0
    assert detail["resets_at"] == reset_at.isoformat()
    assert_no_scan_or_job(session_factory)
    with session_factory() as db:
        assert db.get(Department, DEPARTMENT).scans_this_month == 1
        assert db.query(SecurityScanResult).count() == 0
    assert list(tmp_path.iterdir()) == []
