"""Account HTTP contracts with rollback-only PostgreSQL and controlled mail.

Authenticated identity is substituted; the required-auth adapter remains active.
Route commits release savepoints within a per-test outer transaction. Reloads
prove persistence on that connection, not concurrent or cross-connection behavior.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api import account_routes as routes
from src.auth import dependencies
from src.auth.dependencies import AuthenticatedPrincipal
from src.config.settings import get_settings
from src.db.database import get_db_dependency
from src.db.models import APIKey, Department, User, UserRole, UserSession
from src.mailer.email_service import EmailService
from src.services import account_deletion_service as service_module
from tests.conftest import require_disposable_postgres_url


@pytest.fixture
def account_route(monkeypatch):
    engine = create_engine(
        require_disposable_postgres_url(get_settings().database_url, destructive=False)
    )
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    department = Department(
        id=str(uuid4()),
        name="Account contracts",
        institution="Example University",
        contact_email="admin@example.edu",
    )
    db.add(department)
    db.flush()
    users = [
        User(
            id=str(uuid4()),
            department_id=department.id,
            name=name,
            email=f"{uuid4()}@example.edu",
            role=UserRole.FACULTY,
        )
        for name in ("Account Owner", "Other Owner")
    ]
    db.add_all(users)
    db.flush()
    keys = [
        APIKey(
            id=str(uuid4()),
            user_id=user.id,
            department_id=department.id,
            name=f"{user.name} key",
            key_hash=str(uuid4()),
            key_prefix="synthetic",
        )
        for user in users
    ]
    sessions = [
        UserSession(
            id=str(uuid4()),
            user_id=user.id,
            access_token_jti=str(uuid4()),
            refresh_token_hash=str(uuid4()),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        for user in users
    ]
    db.add_all(keys + sessions)
    db.commit()
    mail = create_autospec(EmailService, instance=True, spec_set=True)
    mail.is_configured.return_value = True
    mail.send_email.return_value = {"success": True}
    monkeypatch.setattr(service_module, "get_email_service", lambda: mail)
    monkeypatch.setattr(get_settings(), "allow_mock_auth", False)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db_dependency] = lambda: db
    fixture = SimpleNamespace(
        db=db,
        user=users[0],
        other=users[1],
        department=department,
        keys=keys,
        sessions=sessions,
        mail=mail,
        app=app,
        principal=AuthenticatedPrincipal(
            api_key=None,
            user_id=users[0].id,
            department_id=department.id,
            user_role=UserRole.FACULTY,
            auth_method="session",
        ),
    )
    original = dependencies.get_authenticated_principal

    def principal(request, credentials, db):
        return (
            fixture.principal
            if fixture.principal is not None
            else original(request, credentials, db)
        )

    monkeypatch.setattr(dependencies, "get_authenticated_principal", principal)
    client = TestClient(app, raise_server_exceptions=False)
    fixture.client = client
    try:
        yield fixture
    finally:
        client.close()
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
