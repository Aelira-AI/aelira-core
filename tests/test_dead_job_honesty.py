"""Truthful interim responses for routes without a durable worker."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.auth_routes import get_current_api_key
from src.api.main import app
from src.auth import get_required_api_key
from src.db.database import get_db_dependency


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db_dependency] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db_dependency, None)
    app.dependency_overrides.pop(get_required_api_key, None)
    app.dependency_overrides.pop(get_current_api_key, None)


def test_blackboard_remediate_is_explicitly_unavailable_without_writing_jobs(
    client, db
):
    app.dependency_overrides[get_required_api_key] = lambda: (
        None,
        "user-1",
        "dept-1",
    )

    with patch(
        "src.api.blackboard_routes._get_blackboard_client", new_callable=AsyncMock
    ) as get_client:
        response = client.post(
            "/blackboard/remediate",
            json={
                "course_id": "course-1",
                "content_id": "content-1",
                "department_id": "dept-1",
            },
        )

    assert response.status_code == 501
    assert response.json() == {
        "detail": "Blackboard remediation execution is not available in this release."
    }
    get_client.assert_not_awaited()
    db.query.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_blackboard_remediate_still_rejects_cross_department_request(client, db):
    app.dependency_overrides[get_required_api_key] = lambda: (
        None,
        "user-1",
        "dept-1",
    )

    response = client.post(
        "/blackboard/remediate",
        json={
            "course_id": "course-1",
            "content_id": "content-1",
            "department_id": "other-dept",
        },
    )

    assert response.status_code == 403
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_provider_sync_is_explicitly_unavailable_without_writing_jobs(client, db):
    app.dependency_overrides[get_current_api_key] = lambda: SimpleNamespace(
        department_id="dept-1"
    )

    response = client.post("/integrations/sync")

    assert response.status_code == 501
    assert response.json() == {
        "detail": "Cloud provider sync execution is not available in this release."
    }
    db.query.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()
