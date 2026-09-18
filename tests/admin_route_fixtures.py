"""Admin HTTP contracts using PostgreSQL and the real admin authorization guard.

Only authenticated identity and outbound mail are substituted. Each test owns a
rollback-only outer transaction; route commits release Session savepoints. Reload
assertions read PostgreSQL on that connection, not cross-connection visibility.
Credential parsing, delivery, concurrency, and production wiring are out of scope.
"""

from types import SimpleNamespace
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api import user_management as routes
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.config.settings import get_settings
from src.db.database import get_db_dependency
from src.db.models import Department, User, UserRole
from src.mailer.email_service import EmailService


@pytest.fixture
def admin_route(monkeypatch):
    engine = create_engine(get_settings().database_url)
    assert engine.dialect.name == "postgresql", "Admin contracts require PostgreSQL"
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    department = Department(
        id=str(uuid4()),
        name="Admin contracts department",
        institution="Example University",
        contact_email="admin@example.edu",
        max_users=20,
    )
    other_department = Department(
        id=str(uuid4()),
        name="Other contracts department",
        institution="Example University",
        contact_email="other@example.edu",
        max_users=20,
    )
    db.add_all([department, other_department])
    db.flush()
    admin = User(
        id=str(uuid4()),
        email=f"admin-{uuid4()}@example.edu",
        name="Department Admin",
        department_id=department.id,
        role=UserRole.ADMIN,
    )
    faculty = User(
        id=str(uuid4()),
        email=f"faculty-{uuid4()}@example.edu",
        name="Department Faculty",
        department_id=department.id,
        role=UserRole.FACULTY,
    )
    other_user = User(
        id=str(uuid4()),
        email=f"other-{uuid4()}@example.edu",
        name="Other Faculty",
        department_id=other_department.id,
        role=UserRole.FACULTY,
    )
    db.add_all([admin, faculty, other_user])
    db.commit()
    mail = create_autospec(EmailService, instance=True, spec_set=True)
    mail.send_faculty_invitation.return_value = {"success": True}
    monkeypatch.setattr(routes, "get_email_service", lambda: mail)
    application = FastAPI()
    application.include_router(routes.router)
    application.include_router(routes.accept_router)
    case = SimpleNamespace(
        db=db,
        department=department,
        other_department=other_department,
        admin=admin,
        faculty=faculty,
        other_user=other_user,
        principal=AuthenticatedPrincipal(
            api_key=None,
            user_id=admin.id,
            department_id=department.id,
            user_role=UserRole.ADMIN,
            auth_method="session",
        ),
        application=application,
        mail=mail,
    )
    application.dependency_overrides[get_db_dependency] = lambda: db
    application.dependency_overrides[get_authenticated_principal] = (
        lambda: case.principal
    )
    client = TestClient(application, raise_server_exceptions=False)
    case.client = client
    try:
        yield case
    finally:
        client.close()
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
