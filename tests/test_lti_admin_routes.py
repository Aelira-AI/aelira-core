"""LTI registration CRUD contracts against isolated PostgreSQL records.

Authentication is replaced only at FastAPI's dependency boundary. These tests
exercise the production router, serialization and department-scoped SQL queries;
they do not exercise a live LMS or claim administrator-only authorization.
"""

from datetime import datetime, timezone
import os
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from conftest import require_disposable_postgres_url
from src.api.auth_routes import SessionAccessIdentity, get_current_api_key
from src.api.main import app
from src.db.database import get_db_dependency
from src.db.models import APIKey, Base, Department, LTIPlatform, LTIRegistration

PATH = "/integrations/lti/registrations"
DEPARTMENT = "lti-contract-department"
OTHER_DEPARTMENT = "lti-contract-other-department"
PAYLOAD = {
    "platform": "canvas",
    "platform_name": "Example Canvas",
    "issuer": "https://canvas.example.edu",
    "client_id": "example-client",
    "deployment_id": "example-deployment",
}


@pytest.fixture(scope="module")
def lti_engine():
    """Use a unique schema, without swallowing unavailable database failures."""
    database_url = require_disposable_postgres_url(
        os.environ["DATABASE_URL"], destructive=False
    )
    admin_engine = create_engine(database_url)
    schema = f"lti_contract_{uuid.uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        database_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    try:
        Base.metadata.create_all(
            engine, tables=[Department.__table__, LTIRegistration.__table__]
        )
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def db(lti_engine):
    """Allow handler commits while rolling each test back at its boundary."""
    with lti_engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            bind=connection, join_transaction_mode="create_savepoint"
        ) as session:
            session.add_all(
                Department(
                    id=department_id,
                    name="Example Department",
                    institution="Example University",
                    contact_email="admin@example.edu",
                )
                for department_id in (DEPARTMENT, OTHER_DEPARTMENT)
            )
            session.commit()
            yield session
        transaction.rollback()


@pytest.fixture
def client(db):
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db_dependency] = lambda: db
    # Missing-auth cases deliberately use the real authentication dependency.
    app.dependency_overrides.pop(get_current_api_key, None)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture
def authenticated(client):
    app.dependency_overrides[get_current_api_key] = lambda: SessionAccessIdentity(
        id="session_contract", user_id="example-admin", department_id=DEPARTMENT
    )
    return client


@pytest.fixture
def registration(db):
    row = LTIRegistration(
        id="example-registration",
        department_id=DEPARTMENT,
        platform=LTIPlatform.CANVAS,
        platform_name=PAYLOAD["platform_name"],
        issuer=PAYLOAD["issuer"],
        client_id=PAYLOAD["client_id"],
        deployment_id=PAYLOAD["deployment_id"],
        is_active=True,
        launch_count=10,
        last_launch_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def other_registration(db):
    row = LTIRegistration(
        id="other-registration",
        department_id=OTHER_DEPARTMENT,
        platform=LTIPlatform.BLACKBOARD,
        platform_name="Other Blackboard",
        issuer="https://blackboard.example.edu",
        client_id="other-client",
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    db.add(row)
    db.commit()
    return row


class TestListLTIRegistrations:
    def test_list_missing_auth(self, client):
        response = client.get(PATH)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_list_empty(self, authenticated):
        response = authenticated.get(PATH)
        assert response.status_code == 200
        assert response.json() == {"registrations": [], "total": 0}

    def test_list_with_registrations(self, authenticated, db, registration):
        newer = LTIRegistration(
            department_id=DEPARTMENT,
            platform=LTIPlatform.BLACKBOARD,
            issuer="https://blackboard.example.edu",
            client_id="second-client",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        db.add(newer)
        db.commit()
        response = authenticated.get(PATH)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert [item["id"] for item in data["registrations"]] == [
            newer.id,
            registration.id,
        ]
        assert data["registrations"][0]["last_launch_at"] is None
        assert data["registrations"][0]["platform"] == "blackboard"
        assert data["registrations"][1]["client_id"] == PAYLOAD["client_id"]


class TestCreateLTIRegistration:
    def test_create_missing_auth(self, client):
        assert client.post(PATH, json=PAYLOAD).status_code == 401

    def test_create_success(self, authenticated, db):
        payload = {
            **PAYLOAD,
            "auth_login_url": "https://canvas.example.edu/login",
            "auth_token_url": "https://canvas.example.edu/token",
            "jwks_url": "https://canvas.example.edu/keys",
        }
        response = authenticated.post(PATH, json=payload)
        assert response.status_code == 200
        data = response.json()
        row = db.get(LTIRegistration, data["id"])
        assert row is not None
        assert row.department_id == DEPARTMENT
        for field in payload:
            actual = getattr(row, field)
            assert (
                actual.value if isinstance(actual, LTIPlatform) else actual
            ) == payload[field]
        assert row.is_active is True
        assert row.launch_count == 0
        assert data["created_at"] == row.created_at.isoformat()
        assert data["deployment_id"] == PAYLOAD["deployment_id"]
        assert data["message"] == "LTI registration created successfully"

    def test_create_invalid_platform(self, authenticated, db):
        response = authenticated.post(PATH, json={**PAYLOAD, "platform": "unknown"})
        assert response.status_code == 400
        assert response.json()["detail"].startswith("Invalid platform: unknown.")
        assert db.query(LTIRegistration).count() == 0

    def test_create_duplicate_same_department(self, authenticated, db, registration):
        response = authenticated.post(PATH, json=PAYLOAD)
        assert response.status_code == 409
        assert (
            response.json()["detail"]
            == "LTI registration already exists for this client_id"
        )
        assert db.query(LTIRegistration).count() == 1

    def test_create_duplicate_different_department(
        self, authenticated, db, other_registration
    ):
        response = authenticated.post(
            PATH,
            json={
                **PAYLOAD,
                "issuer": other_registration.issuer,
                "client_id": other_registration.client_id,
            },
        )
        assert response.status_code == 409
        assert (
            response.json()["detail"]
            == "This LTI client_id is already registered by another department"
        )
        assert db.query(LTIRegistration).count() == 1

    @pytest.mark.parametrize(
        "platform", ["canvas", "blackboard", "moodle", "brightspace"]
    )
    def test_create_all_platforms(self, authenticated, db, platform):
        response = authenticated.post(
            PATH,
            json={
                "platform": platform,
                "issuer": f"https://{platform}.example.edu",
                "client_id": "client",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["platform"] == platform
        assert data["platform_name"] == f"{platform.title()} LMS"
        assert data["deployment_id"] is None
        assert db.get(LTIRegistration, data["id"]).platform == LTIPlatform(platform)


class TestUpdateLTIRegistration:
    def test_update_missing_auth(self, client):
        assert (
            client.patch(
                f"{PATH}/example-registration", params={"is_active": False}
            ).status_code
            == 401
        )

    @pytest.mark.parametrize("active", [False, True])
    def test_update_deactivate(self, authenticated, db, registration, active):
        registration.is_active = not active
        db.commit()
        response = authenticated.patch(
            f"{PATH}/{registration.id}", params={"is_active": active}
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is active
        db.expire_all()
        assert db.get(LTIRegistration, registration.id).is_active is active

    def test_update_rename(self, authenticated, db, registration):
        response = authenticated.patch(
            f"{PATH}/{registration.id}", params={"platform_name": "Renamed Canvas"}
        )
        assert response.status_code == 200
        assert response.json()["platform_name"] == "Renamed Canvas"
        db.expire_all()
        assert (
            db.get(LTIRegistration, registration.id).platform_name == "Renamed Canvas"
        )

    def test_update_not_found(self, authenticated):
        response = authenticated.patch(f"{PATH}/missing", params={"is_active": False})
        assert response.status_code == 404
        assert response.json()["detail"] == "LTI registration not found"

    def test_update_wrong_department(self, authenticated, db, other_registration):
        response = authenticated.patch(
            f"{PATH}/{other_registration.id}", params={"is_active": False}
        )
        assert response.status_code == 404
        db.expire_all()
        assert db.get(LTIRegistration, other_registration.id).is_active is True

    def test_update_invalid_boolean(self, authenticated, db, registration):
        response = authenticated.patch(
            f"{PATH}/{registration.id}", params={"is_active": "unknown"}
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["query", "is_active"]
        db.expire_all()
        assert db.get(LTIRegistration, registration.id).is_active is True

    def test_update_without_changes(self, authenticated, db, registration):
        response = authenticated.patch(f"{PATH}/{registration.id}")
        assert response.status_code == 200
        assert response.json()["platform_name"] == PAYLOAD["platform_name"]
        assert response.json()["is_active"] is True
        db.expire_all()
        assert (
            db.get(LTIRegistration, registration.id).platform_name
            == PAYLOAD["platform_name"]
        )


class TestDeleteLTIRegistration:
    def test_delete_missing_auth(self, client):
        assert client.delete(f"{PATH}/example-registration").status_code == 401

    def test_delete_success(self, authenticated, db, registration):
        registration_id = registration.id
        response = authenticated.delete(f"{PATH}/{registration_id}")
        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "message": "LTI registration deleted successfully",
            "platform": "canvas",
            "client_id": PAYLOAD["client_id"],
        }
        assert db.get(LTIRegistration, registration_id) is None

    def test_delete_not_found(self, authenticated):
        response = authenticated.delete(f"{PATH}/missing")
        assert response.status_code == 404
        assert response.json()["detail"] == "LTI registration not found"

    def test_delete_wrong_department(self, authenticated, db, other_registration):
        response = authenticated.delete(f"{PATH}/{other_registration.id}")
        assert response.status_code == 404
        assert db.get(LTIRegistration, other_registration.id) is not None


class TestLTIAdminSecurity:
    def test_cannot_access_other_department_registrations(
        self, authenticated, registration, other_registration
    ):
        response = authenticated.get(PATH)
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert [row["id"] for row in response.json()["registrations"]] == [
            registration.id
        ]

    def test_registration_includes_launch_stats(self, authenticated, registration):
        response = authenticated.get(PATH)
        assert response.status_code == 200
        row = response.json()["registrations"][0]
        assert row["launch_count"] == 10
        assert row["last_launch_at"] == registration.last_launch_at.isoformat()
        assert row["created_at"] == registration.created_at.isoformat()

    @pytest.mark.parametrize("missing", ["issuer", "client_id"])
    def test_create_requires_issuer_and_client_id(self, authenticated, db, missing):
        payload = {key: value for key, value in PAYLOAD.items() if key != missing}
        response = authenticated.post(PATH, json=payload)
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", missing]
        assert db.query(LTIRegistration).count() == 0

    def test_platform_is_case_insensitive(self, authenticated, db):
        response = authenticated.post(PATH, json={**PAYLOAD, "platform": "CANVAS"})
        assert response.status_code == 200
        assert response.json()["platform"] == "canvas"
        assert (
            db.get(LTIRegistration, response.json()["id"]).platform
            == LTIPlatform.CANVAS
        )

    def test_api_key_identity_uses_its_department(
        self, authenticated, registration, other_registration
    ):
        app.dependency_overrides[get_current_api_key] = lambda: APIKey(
            id="other-example-key",
            user_id="other-example-user",
            department_id=OTHER_DEPARTMENT,
        )
        response = authenticated.get(PATH)
        assert response.status_code == 200
        assert [row["id"] for row in response.json()["registrations"]] == [
            other_registration.id
        ]

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_dependency_failure_is_returned(self, authenticated, db, method):
        def unavailable():
            raise HTTPException(
                status_code=503, detail="Registration storage unavailable"
            )

        app.dependency_overrides[get_db_dependency] = unavailable
        path = PATH if method in {"get", "post"} else f"{PATH}/example-registration"
        response = authenticated.request(
            method, path, **({"json": PAYLOAD} if method == "post" else {})
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "Registration storage unavailable"}
        assert db.query(LTIRegistration).count() == 0
