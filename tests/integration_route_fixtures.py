"""PostgreSQL contracts with supplied identity and no live provider calls.

Commits release savepoints inside a rollback-only outer transaction. Reloads use
the same connection, not cross-connection commit visibility. The callback context
manager and request dependency retain rollback on failure. Authentication parsing,
provider transport and worker execution are outside this fixture's evidence.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api import integration_routes, webhook_routes
from src.config.settings import get_settings
from src.db.database import get_db_dependency
from src.db.models import (
    APIKey,
    CloudOAuthCredentials,
    CloudWebhookSubscription,
    Department,
    User,
)

PROVIDERS = ("google", "microsoft", "canvas", "blackboard", "moodle", "brightspace")
# One shared UTC reference keeps expected serialization exact without aging
# supported credentials/subscriptions into expired fixtures on future CI runs.
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def credential(case, provider, *, other=False, active=True, synced_at=NOW):
    row = CloudOAuthCredentials(
        id=str(uuid4()),
        department_id=case.other_department.id if other else case.department.id,
        provider=provider,
        access_token="fixture-unused-access",
        refresh_token="fixture-unused-refresh",
        token_expires_at=NOW + timedelta(days=1),
        provider_email=f"{provider}@example.edu",
        provider_name=f"{provider} Account",
        provider_metadata={
            "user_email": f"{provider}@example.edu",
            "user_name": f"{provider} Account",
        },
        is_active=active,
        last_sync_at=synced_at,
    )
    case.db.add(row)
    case.db.flush()
    return row


def subscription(case, credentials, *, active=True):
    row = CloudWebhookSubscription(
        id=str(uuid4()),
        department_id=credentials.department_id,
        credential_id=credentials.id,
        provider=credentials.provider,
        subscription_id=f"fixture-sub-{uuid4()}",
        provider_resource_id="fixture-resource",
        provider_channel_resource_id="fixture-channel-resource",
        expiration_time=NOW + timedelta(days=7),
        notification_url="https://example.test/webhooks",
        is_active=active,
    )
    case.db.add(row)
    case.db.flush()
    return row


@pytest.fixture
def integration_route(monkeypatch):
    engine = create_engine(get_settings().database_url)
    assert (
        engine.dialect.name == "postgresql"
    ), "Integration contracts require PostgreSQL"
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    department = Department(
        id=str(uuid4()),
        name="Integration contracts",
        institution="Example University",
        contact_email="primary@example.edu",
    )
    other_department = Department(
        id=str(uuid4()),
        name="Other contracts",
        institution="Example University",
        contact_email="other@example.edu",
    )
    db.add_all([department, other_department])
    db.flush()
    user = User(
        id=str(uuid4()),
        email=f"fixture-{uuid4()}@example.edu",
        department_id=department.id,
    )
    db.add(user)
    db.flush()
    key = APIKey(
        id=str(uuid4()),
        user_id=user.id,
        department_id=department.id,
        key_hash=f"fixture-only-{uuid4()}",
        key_prefix="fixture-only",
    )
    db.add(key)
    db.commit()
    case = SimpleNamespace(
        db=db,
        department=department,
        other_department=other_department,
        user=user,
        identity=key,
    )

    def database_dependency():
        try:
            yield db
        except Exception:
            db.rollback()
            raise

    @contextmanager
    def callback_database():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(webhook_routes, "get_db", callback_database)
    application = FastAPI()
    application.include_router(integration_routes.router)
    application.include_router(webhook_routes.router)
    application.dependency_overrides[get_db_dependency] = database_dependency
    application.dependency_overrides[integration_routes.get_current_api_key] = (
        lambda: case.identity
    )
    application.dependency_overrides[webhook_routes.get_api_key_or_mock] = lambda: (
        case.identity,
        case.user.id,
        case.department.id,
    )
    case.application = application
    case.client = TestClient(application, raise_server_exceptions=False)
    try:
        yield case
    finally:
        case.client.close()
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
