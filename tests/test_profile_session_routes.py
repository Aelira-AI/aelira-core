"""HTTP profile/session contracts with PostgreSQL persistence.

Identity validation is a fixture boundary; routes, queries and commits are real.
No email service or external identity provider is contacted.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api import auth_routes as routes
from src.config.settings import get_settings
from src.db.database import get_db_dependency
from src.db.models import Department, User, UserRole, UserSession

pytestmark = pytest.mark.integration


@pytest.fixture
def profile_route(monkeypatch):
    engine = create_engine(get_settings().database_url)
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    department = Department(
        id=str(uuid4()),
        name="Route fixture",
        institution="Example University",
        contact_email="admin@example.edu",
    )
    db.add(department)
    db.flush()
    user = User(
        id=str(uuid4()),
        department_id=department.id,
        name="Example Faculty",
        email=f"{uuid4()}@example.edu",
        role=UserRole.FACULTY,
        email_scan_complete=True,
        timezone="UTC",
    )
    db.add(user)
    db.flush()
    current_jti = str(uuid4())
    identity = MagicMock()
    identity.validate_session.return_value = (user, {"jti": current_jti})
    monkeypatch.setattr(routes, "get_session_service", lambda: identity)
    application = FastAPI()
    application.include_router(routes.router)
    application.dependency_overrides[get_db_dependency] = lambda: db
    application.dependency_overrides[routes.get_required_api_key] = lambda: (
        None,
        user.id,
        department.id,
    )
    client = TestClient(application, raise_server_exceptions=False)
    client.cookies.set("aelira_access", "synthetic-session")
    try:
        yield SimpleNamespace(
            client=client,
            db=db,
            user=user,
            department=department,
            identity=identity,
            current_jti=current_jti,
        )
    finally:
        client.close()
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_profile_false_preference_round_trip(profile_route):
    fixture = profile_route
    response = fixture.client.patch(
        "/auth/profile", json={"email_notifications": False, "name": "Updated Name"}
    )
    assert response.status_code == 200
    assert response.json()["email_notifications"] is False
    fixture.db.expire_all()
    assert fixture.db.get(User, fixture.user.id).email_scan_complete is False
    response = fixture.client.get("/auth/profile")
    assert response.status_code == 200
    assert response.json()["email_notifications"] is False
    assert response.json()["name"] == "Updated Name"


@pytest.mark.parametrize("enabled", [True, False])
def test_profile_reads_persisted_preferences(profile_route, enabled):
    fixture = profile_route
    fixture.user.email_scan_complete = enabled
    fixture.db.commit()
    response = fixture.client.get("/auth/profile")
    assert response.status_code == 200
    assert response.json()["id"] == fixture.user.id
    assert response.json()["email_notifications"] is enabled


def test_email_preferences_partial_update_preserves_other_values(profile_route):
    fixture = profile_route
    fixture.user.email_critical_alerts = False
    fixture.db.commit()
    response = fixture.client.patch(
        "/auth/profile/email-preferences",
        json={
            "email_scan_complete": False,
            "weekly_summary_day": 6,
            "weekly_summary_hour": 23,
        },
    )
    assert response.status_code == 200
    assert response.json()["email_critical_alerts"] is False
    assert response.json()["email_scan_complete"] is False
    fixture.db.expire_all()
    assert fixture.db.get(User, fixture.user.id).weekly_summary_hour == 23
    assert (
        fixture.client.get("/auth/profile/email-preferences").json() == response.json()
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"weekly_summary_day": -1},
        {"weekly_summary_day": 7},
        {"weekly_summary_hour": -1},
        {"weekly_summary_hour": 24},
    ],
)
def test_email_preferences_validate_schedule_at_http_boundary(profile_route, payload):
    assert (
        profile_route.client.patch(
            "/auth/profile/email-preferences", json=payload
        ).status_code
        == 422
    )


def test_session_list_omits_other_users_records(profile_route):
    fixture = profile_route
    own = _session(fixture, current=True)
    other_user = User(
        id=str(uuid4()),
        department_id=fixture.department.id,
        email=f"{uuid4()}@example.edu",
        name="Other Faculty",
    )
    fixture.db.add(other_user)
    fixture.db.flush()
    other_session = UserSession(
        id=str(uuid4()),
        user_id=other_user.id,
        access_token_jti=str(uuid4()),
        refresh_token_hash=str(uuid4()),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    fixture.db.add(other_session)
    fixture.db.commit()
    response = fixture.client.get("/auth/sessions")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["sessions"]] == [own.id]
    response = fixture.client.delete(f"/auth/sessions/{other_session.id}")
    assert response.status_code == 404
    response = fixture.client.delete("/auth/sessions")
    assert response.status_code == 200
    assert response.json()["revoked_count"] == 0
    fixture.db.expire_all()
    assert fixture.db.get(UserSession, other_session.id).revoked_at is None


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"name": " "}, 400),
        ({"name": "x" * 101}, 400),
        ({"timezone": "x" * 51}, 400),
        ({"name": []}, 422),
    ],
)
def test_profile_invalid_input_does_not_persist(profile_route, payload, status):
    fixture = profile_route
    response = fixture.client.patch("/auth/profile", json=payload)
    assert response.status_code == status
    fixture.db.expire_all()
    assert fixture.db.get(User, fixture.user.id).name == "Example Faculty"


@pytest.mark.parametrize("method", ["get", "patch"])
def test_profile_missing_user_is_not_found(profile_route, method):
    fixture = profile_route
    fixture.db.delete(fixture.user)
    fixture.db.commit()
    response = fixture.client.request(
        method, "/auth/profile", json={} if method == "patch" else None
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


@pytest.mark.parametrize("method", ["get", "patch"])
def test_profile_database_failure_is_unsuccessful(profile_route, monkeypatch, method):
    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic database unavailable")

    monkeypatch.setattr(profile_route.db, "query", unavailable)
    response = profile_route.client.request(
        method, "/auth/profile", json={} if method == "patch" else None
    )
    assert response.status_code == 500
    assert "synthetic" not in response.text


def _session(fixture, *, current=False, expired=False):
    row = UserSession(
        id=str(uuid4()),
        user_id=fixture.user.id,
        access_token_jti=fixture.current_jti if current else str(uuid4()),
        refresh_token_hash=str(uuid4()),
        expires_at=datetime.now(timezone.utc) + timedelta(days=-1 if expired else 1),
        user_agent="Test client",
        ip_address="127.0.0.1",
    )
    fixture.db.add(row)
    fixture.db.commit()
    return row


def test_session_list_and_revoke_persist(profile_route):
    fixture = profile_route
    current = _session(fixture, current=True)
    other = _session(fixture)
    _session(fixture, expired=True)
    response = fixture.client.get("/auth/sessions")
    assert response.status_code == 200
    assert response.json()["total"] == 2
    by_id = {row["id"]: row for row in response.json()["sessions"]}
    assert by_id[current.id]["is_current"] is True
    assert by_id[other.id]["is_current"] is False
    response = fixture.client.delete(f"/auth/sessions/{other.id}")
    assert response.status_code == 200
    fixture.db.expire_all()
    assert fixture.db.get(UserSession, other.id).revoked_at is not None
    assert fixture.db.get(UserSession, current.id).revoked_at is None
    assert fixture.client.delete(f"/auth/sessions/{other.id}").status_code == 404


def test_revoke_other_sessions_preserves_current_and_is_idempotent(profile_route):
    fixture = profile_route
    current = _session(fixture, current=True)
    others = [_session(fixture), _session(fixture)]
    response = fixture.client.delete("/auth/sessions")
    assert response.status_code == 200
    assert response.json()["revoked_count"] == 2
    fixture.db.expire_all()
    assert fixture.db.get(UserSession, current.id).revoked_at is None
    assert all(fixture.db.get(UserSession, row.id).revoked_at for row in others)
    assert fixture.client.delete("/auth/sessions").json()["revoked_count"] == 0


def test_current_session_uses_logout_contract(profile_route):
    current = _session(profile_route, current=True)
    response = profile_route.client.delete(f"/auth/sessions/{current.id}")
    assert response.status_code == 400
    assert "Use logout" in response.json()["detail"]
    profile_route.db.expire_all()
    assert profile_route.db.get(UserSession, current.id).revoked_at is None


@pytest.mark.parametrize("path", ["/auth/sessions", "/auth/sessions/missing"])
def test_session_revocation_requires_valid_session(profile_route, path):
    profile_route.identity.validate_session.return_value = None
    response = profile_route.client.delete(path)
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/auth/sessions", "/auth/sessions/missing"])
def test_session_service_failure_is_unsuccessful(profile_route, path):
    profile_route.identity.validate_session.side_effect = RuntimeError(
        "synthetic identity unavailable"
    )
    response = profile_route.client.delete(path)
    assert response.status_code == 500
    assert "synthetic" not in response.text
