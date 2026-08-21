"""Durable route guardrails replacing the former unavailable-job contracts."""

import inspect
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


def test_blackboard_remediation_requires_auth_before_enqueue_or_provider(client, db):
    with (
        patch("src.api.blackboard_routes.enqueue_cloud_job") as enqueue,
        patch(
            "src.api.blackboard_routes._get_blackboard_client",
            new_callable=AsyncMock,
        ) as get_client,
    ):
        response = client.post(
            "/blackboard/remediate",
            json={
                "course_id": "course-1",
                "content_id": "content-1",
                "department_id": "dept-1",
            },
        )

    assert response.status_code == 401
    enqueue.assert_not_called()
    get_client.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_blackboard_remediate_still_rejects_cross_department_request(client, db):
    app.dependency_overrides[get_required_api_key] = lambda: (
        None,
        "user-1",
        "dept-1",
    )

    with (
        patch("src.api.blackboard_routes.enqueue_cloud_job") as enqueue,
        patch(
            "src.api.blackboard_routes._get_blackboard_client",
            new_callable=AsyncMock,
        ) as get_client,
    ):
        response = client.post(
            "/blackboard/remediate",
            json={
                "course_id": "course-1",
                "content_id": "content-1",
                "department_id": "other-dept",
            },
        )

    assert response.status_code == 403
    enqueue.assert_not_called()
    get_client.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_blackboard_remediation_rejects_untrusted_persisted_origin(client, db):
    app.dependency_overrides[get_required_api_key] = lambda: (
        None,
        "user-1",
        "dept-1",
    )
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        id="cred-1",
        department_id="dept-1",
        provider="blackboard",
        provider_metadata={},
        is_active=True,
    )

    with (
        patch("src.api.blackboard_routes.enqueue_cloud_job") as enqueue,
        patch(
            "src.api.blackboard_routes._get_blackboard_client",
            new_callable=AsyncMock,
        ) as get_client,
    ):
        response = client.post(
            "/blackboard/remediate",
            json={
                "course_id": "course-1",
                "content_id": "content-1",
                "department_id": "dept-1",
            },
        )

    assert response.status_code == 409
    enqueue.assert_not_called()
    get_client.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_provider_sync_requires_auth_before_enqueue(client, db):
    with patch("src.api.integration_routes.enqueue_cloud_job") as enqueue:
        response = client.post(
            "/integrations/sync",
            json={"provider": "google", "folder_ids": ["folder-1"]},
        )

    assert response.status_code == 401
    enqueue.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        (
            {"provider": "dropbox", "folder_ids": ["folder-1"]},
            "Unsupported sync provider",
        ),
        (
            {"provider": "google", "folder_ids": ["folder-1", "folder-1"]},
            "Duplicate sync folder",
        ),
    ],
)
def test_provider_sync_rejects_invalid_selection_before_enqueue(
    client, db, payload, detail
):
    app.dependency_overrides[get_current_api_key] = lambda: SimpleNamespace(
        department_id="dept-1"
    )

    with patch("src.api.integration_routes.enqueue_cloud_job") as enqueue:
        response = client.post("/integrations/sync", json=payload)

    assert response.status_code == 400
    assert response.json() == {"detail": detail}
    enqueue.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_provider_sync_rejects_unowned_folder_before_enqueue(client, db):
    app.dependency_overrides[get_current_api_key] = lambda: SimpleNamespace(
        department_id="dept-1"
    )
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        id="cred-1",
        department_id="dept-1",
        provider="google",
        is_active=True,
    )
    db.query.return_value.filter.return_value.all.return_value = []

    with patch("src.api.integration_routes.enqueue_cloud_job") as enqueue:
        response = client.post(
            "/integrations/sync",
            json={"provider": "google", "folder_ids": ["folder-other-tenant"]},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Sync folder not found"}
    enqueue.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_replacement_routes_enqueue_durably_without_inline_provider_http():
    from src.api.blackboard_routes import remediate_blackboard_file
    from src.api.integration_routes import trigger_sync

    for route in (remediate_blackboard_file, trigger_sync):
        source = inspect.getsource(route)
        assert "enqueue_cloud_job(" in source
        assert "BackgroundTasks" not in source
        assert "_get_blackboard_client(" not in source
        assert "httpx." not in source
        assert "requests." not in source
