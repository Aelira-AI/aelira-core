"""Real PostgreSQL alert routes with only identity and mail boundaries replaced.

Each test owns a connection and rollback-only outer transaction. Route commits
release Session savepoints; expire/reload assertions read PostgreSQL through that
same connection. These contracts do not prove cross-connection commit visibility
or concurrent behavior. Individual failure tests inject named database failures.
"""

from types import SimpleNamespace
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src import mailer
from src.api import alert_routes as routes
from src.config.settings import get_settings
from src.db.database import get_db_dependency
from src.db.models import Department


@pytest.fixture
def alert_route(monkeypatch):
    engine = create_engine(get_settings().database_url)
    assert engine.dialect.name == "postgresql", "Alert contracts require PostgreSQL"
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    department = Department(
        id=str(uuid4()),
        name="Faculty alert fixture",
        institution="Example University",
        contact_email="faculty@example.edu",
    )
    other_department = Department(
        id=str(uuid4()),
        name="Library alert fixture",
        institution="Example University",
        contact_email="library@example.edu",
    )
    db.add_all([department, other_department])
    db.flush()
    principal = SimpleNamespace(
        id=str(uuid4()), user_id=str(uuid4()), department_id=department.id
    )
    mail = create_autospec(mailer.EmailService, instance=True)
    for name in (
        "send_scan_complete",
        "send_critical_issues",
        "send_weekly_summary",
        "send_email",
    ):
        getattr(mail, name).return_value = {"success": True}
    monkeypatch.setattr(mailer, "get_email_service", lambda: mail)
    application = FastAPI()
    application.include_router(routes.router, prefix="/api")
    application.dependency_overrides[get_db_dependency] = lambda: db
    application.dependency_overrides[routes.get_current_api_key] = lambda: principal
    client = TestClient(application, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(
            client=client,
            db=db,
            department=department,
            other_department=other_department,
            principal=principal,
            application=application,
            mail=mail,
        )
    finally:
        client.close()
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
