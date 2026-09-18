# ruff: noqa: F811

"""Server-side Microsoft journeys with PostgreSQL and controlled Graph HTTP.

Historical module name retained; these are not live-provider or worker E2E tests.
"""

from src.db.models import CloudFile, CloudJobQueue, CloudOAuthCredentials
from tests.test_microsoft_file_routes import microsoft_route  # noqa: F401


def test_browse_enqueue_poll_and_disconnect(microsoft_route):
    f = microsoft_route
    response = f.client.get("/microsoft/onedrive/files")
    assert response.status_code == 200
    file_id = response.json()["files"][0]["id"]
    response = f.client.post("/microsoft/scan/file", json={"file_id": file_id})
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert f.client.get("/microsoft/jobs/" + job_id).json()["status"] == "pending"
    assert f.db.get(CloudJobQueue, job_id).cloud_file_id == file_id
    response = f.client.delete("/microsoft/disconnect")
    assert response.status_code == 200
    assert f.db.query(CloudFile).count() == 0
    assert f.db.query(CloudJobQueue).count() == 0
    assert f.db.query(CloudOAuthCredentials).count() == 0
    assert f.client.get("/microsoft/status").status_code == 404


def test_connected_account_and_status_use_persisted_identity(microsoft_route):
    f = microsoft_route
    f.credential.provider_email = "faculty@example.test"
    f.credential.provider_name = "Example Faculty"
    f.db.commit()
    response = f.client.get("/microsoft/account")
    assert response.status_code == 200
    assert response.json()["email"] == "faculty@example.test"
    assert response.json()["name"] == "Example Faculty"
    response = f.client.get("/microsoft/status")
    assert response.status_code == 200
    assert response.json()["id"] == f.credential.id
    assert response.json()["is_active"] is True
    assert not f.requests
