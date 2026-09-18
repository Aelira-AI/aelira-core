"""Current provider job routes, persisted queue metadata and claim ordering.

HTTP tests exercise production routers with a trusted identity fixture and real
PostgreSQL queries. They seed job states rather than claiming worker execution.
Worker completion, cancellation and retries are covered by the required worker
lane; no generic job mutation REST surface is part of this contract.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from conftest import require_disposable_postgres_url
from src.api import google_routes, integration_routes, microsoft_routes
from src.api.auth_routes import SessionAccessIdentity, get_current_api_key
from src.config.settings import get_settings
from src.db.database import get_db_dependency
from src.db.models import CloudJobQueue, Department
from src.jobs.job_processor import build_claim_query
from src.services.job_enqueue_service import JobEnqueueError, enqueue_cloud_job

pytestmark = pytest.mark.integration


@pytest.fixture
def queue_routes():
    url = require_disposable_postgres_url(
        get_settings().database_url, destructive=False
    )
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        db = Session(bind=connection, join_transaction_mode="create_savepoint")
        own, other = str(uuid4()), str(uuid4())
        db.add_all(
            Department(
                id=identity,
                name="Queue route fixture",
                institution="Example University",
                contact_email="queue@example.edu",
            )
            for identity in (own, other)
        )
        db.commit()
        app = FastAPI()
        for router in (
            google_routes.router,
            microsoft_routes.router,
            integration_routes.router,
        ):
            app.include_router(router)
        app.dependency_overrides[get_db_dependency] = lambda: db
        app.dependency_overrides[get_current_api_key] = lambda: SessionAccessIdentity(
            id="synthetic-session", user_id="queue-user", department_id=own
        )
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                yield SimpleNamespace(
                    client=client, app=app, db=db, own=own, other=other
                )
        finally:
            db.close()
            transaction.rollback()
    engine.dispose()


def _job(case, *, provider="google", state="pending", department=None, age=0):
    now = datetime.now(timezone.utc) - timedelta(minutes=age)
    job = CloudJobQueue(
        id=str(uuid4()),
        department_id=department or case.own,
        provider=provider,
        job_type="scan",
        payload={},
        dedupe_key=str(uuid4()),
        status=state,
        progress=45 if state == "processing" else (100 if state == "completed" else 0),
        progress_message="Scanning document" if state == "processing" else None,
        result_data={"files_processed": 1} if state == "completed" else None,
        error_message="scan_failed" if state == "failed" else None,
        created_at=now,
        completed_at=now if state in {"completed", "failed"} else None,
        claim_token=str(uuid4()) if state == "processing" else None,
        worker_id="fixture-worker" if state == "processing" else None,
        claimed_at=now if state == "processing" else None,
        heartbeat_at=now if state == "processing" else None,
        lease_expires_at=now + timedelta(minutes=5) if state == "processing" else None,
    )
    case.db.add(job)
    case.db.commit()
    return job


@pytest.mark.parametrize("provider", ["google", "microsoft"])
@pytest.mark.parametrize("state", ["pending", "processing", "completed", "failed"])
def test_provider_job_status_returns_persisted_progress_and_result(
    queue_routes, provider, state
):
    case = queue_routes
    job = _job(case, provider=provider, state=state)
    response = case.client.get(f"/{provider}/jobs/{job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job.id
    assert body["status"] == state
    assert body["progress"] == job.progress
    assert body["progress_message"] == job.progress_message
    assert body["result_data"] == job.result_data
    assert body["error_message"] == job.error_message
    assert datetime.fromisoformat(body["created_at"]) == job.created_at
    assert bool(body["completed_at"]) == (state in {"completed", "failed"})


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_provider_job_list_filters_department_provider_status_and_limit(
    queue_routes, provider
):
    case = queue_routes
    older = _job(case, provider=provider, age=2)
    newer = _job(case, provider=provider, age=1)
    completed = _job(case, provider=provider, state="completed")
    _job(case, provider=provider, department=case.other)
    _job(case, provider="microsoft" if provider == "google" else "google")
    response = case.client.get(f"/{provider}/jobs")
    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()] == [
        completed.id,
        newer.id,
        older.id,
    ]
    response = case.client.get(
        f"/{provider}/jobs", params={"status": "pending", "limit": 1}
    )
    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()] == [newer.id]
    assert (
        case.client.get(f"/{provider}/jobs", params={"status": "failed"}).json() == []
    )


@pytest.mark.parametrize("provider", ["google", "microsoft"])
@pytest.mark.parametrize("missing", [False, True], ids=["other-department", "missing"])
def test_provider_job_status_hides_unavailable_record(queue_routes, provider, missing):
    case = queue_routes
    job_id = (
        str(uuid4())
        if missing
        else _job(case, provider=provider, department=case.other).id
    )
    response = case.client.get(f"/{provider}/jobs/{job_id}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


@pytest.mark.parametrize("provider", ["google", "microsoft"])
@pytest.mark.parametrize("limit", [0, 101, "unknown"])
def test_provider_job_list_validates_limit(queue_routes, provider, limit):
    response = queue_routes.client.get(f"/{provider}/jobs", params={"limit": limit})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "limit"]


@pytest.mark.parametrize("provider", ["google", "microsoft"])
@pytest.mark.parametrize("suffix", ["", "/missing"])
def test_provider_job_routes_require_authentication(queue_routes, provider, suffix):
    queue_routes.app.dependency_overrides.pop(get_current_api_key)
    response = queue_routes.client.get(f"/{provider}/jobs{suffix}")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("provider", ["google", "microsoft"])
@pytest.mark.parametrize("suffix", ["", "/missing"])
def test_provider_job_query_failure_is_not_a_success(
    queue_routes, monkeypatch, provider, suffix
):
    def unavailable(*args):
        raise RuntimeError("synthetic queue query unavailable")

    monkeypatch.setattr(queue_routes.db, "query", unavailable)
    response = queue_routes.client.get(f"/{provider}/jobs{suffix}")
    assert response.status_code == 500
    assert "synthetic" not in response.text


def test_integration_metrics_count_only_authenticated_department_jobs(queue_routes):
    case = queue_routes
    for state in ("pending", "processing", "completed", "failed"):
        _job(case, state=state)
        _job(case, state=state, department=case.other)
    response = case.client.get("/integrations/metrics")
    assert response.status_code == 200
    data = response.json()
    assert {
        key: data[key]
        for key in ("total_jobs", "pending_jobs", "completed_jobs", "failed_jobs")
    } == {"total_jobs": 4, "pending_jobs": 1, "completed_jobs": 1, "failed_jobs": 1}


def test_enqueue_priority_and_claim_query_follow_durable_contract(queue_routes):
    case = queue_routes
    jobs = []
    for name, options in (
        ("default", {}),
        ("urgent", {"priority": 1}),
        ("later", {"priority": 10}),
    ):
        job = enqueue_cloud_job(
            case.db,
            department_id=case.own,
            job_type="scan",
            payload={},
            dedupe_key=name,
            **options,
        )
        jobs.append(job)
    case.db.commit()
    assert [job.priority for job in jobs] == [5, 1, 10]
    query = build_claim_query({"scan"}, limit=3).where(
        CloudJobQueue.department_id == case.own
    )
    assert [job.id for job in case.db.scalars(query)] == [
        jobs[1].id,
        jobs[0].id,
        jobs[2].id,
    ]
    # This executes the real selection SQL; claim ownership and worker execution
    # are separately exercised by the required PostgreSQL worker profile.


@pytest.mark.parametrize("priority", [-1, 101, True, "urgent"])
def test_enqueue_rejects_invalid_priority_without_storing_work(queue_routes, priority):
    case = queue_routes
    with pytest.raises(JobEnqueueError, match="priority_invalid"):
        enqueue_cloud_job(
            case.db,
            department_id=case.own,
            job_type="scan",
            payload={},
            dedupe_key="invalid",
            priority=priority,
        )
    assert case.db.query(CloudJobQueue).filter_by(department_id=case.own).count() == 0
