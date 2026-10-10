"""HTTP enqueue, fresh LMS policy, durable worker and public failure receipt.

PostgreSQL and queue transitions are real. Authentication is a trusted router
dependency override. Synthetic zero-issue scans make the allowed path a genuine
no-op; this does not exercise Canvas transport or model inference.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import sessionmaker

from src.ai.lms_remediation_client import LMSRemediationClient
from src.api.education import remediation_routes
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import engine, get_db_dependency
from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudOAuthCredentials,
    Department,
    Scan,
    ScanResult,
    ScanStatus,
    ScanType,
    User,
    UserRole,
    WorkerHeartbeat,
)
from src.jobs.job_processor import JobProcessor
from src.jobs.registry import JobRegistry, adapt_legacy_handler
from src.jobs.remediation_job import handle_remediation_job, process_remediation_job

pytestmark = pytest.mark.integration


@pytest.fixture
def policy_queue_http():
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    department_id, user_id, credential_id, scan_id, cloud_id = (
        str(uuid.uuid4()) for _ in range(5)
    )
    worker_id = f"policy-test-{uuid.uuid4()}"
    with factory() as db:
        db.add(
            Department(
                id=department_id,
                name="Policy queue fixture",
                institution="Example University",
                contact_email="policy@example.edu",
            )
        )
        db.flush()
        db.add(
            User(
                id=user_id,
                department_id=department_id,
                email=f"policy-{user_id}@example.edu",
                role=UserRole.ADMIN,
            )
        )
        db.add(
            CloudOAuthCredentials(
                id=credential_id,
                department_id=department_id,
                provider="canvas",
                access_token="example_synthetic-never-decrypted",
                refresh_token="example_synthetic-never-decrypted",
                token_expires_at=datetime.now(timezone.utc),
                is_active=True,
            )
        )
        db.flush()
        db.add(
            Scan(
                id=scan_id,
                department_id=department_id,
                user_id=user_id,
                scan_type=ScanType.WORD,
                file_name="policy-fixture.docx",
                status=ScanStatus.COMPLETED,
                document_source="cloud_file",
                document_id=cloud_id,
                result=ScanResult(compliance_score=100.0, issues=[]),
            )
        )
        db.flush()
        db.add(
            CloudFile(
                id=cloud_id,
                department_id=department_id,
                credential_id=credential_id,
                provider="canvas",
                provider_file_id="synthetic-file",
                provider_parent_id="synthetic-course",
                file_name="policy-fixture.docx",
                file_type="docx",
                last_scan_id=scan_id,
            )
        )
        db.commit()

    principal = AuthenticatedPrincipal(
        api_key=None,
        user_id=user_id,
        department_id=department_id,
        user_role=UserRole.ADMIN,
        auth_method="session",
    )
    app = FastAPI()
    app.include_router(remediation_routes.router, prefix="/education")

    def database():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db_dependency] = database
    app.dependency_overrides[get_authenticated_principal] = lambda: principal
    registry = JobRegistry()
    registry.register("remediate", adapt_legacy_handler(handle_remediation_job))
    worker = JobProcessor(
        session_factory=factory, registry=registry, worker_id=worker_id
    )
    worker._token_manager = MagicMock()
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                factory=factory,
                client=client,
                department_id=department_id,
                scan_id=scan_id,
                worker=worker,
            )
    finally:
        with factory() as db:
            db.query(WorkerHeartbeat).filter_by(worker_id=worker_id).delete()
            db.query(CloudJobQueue).filter_by(department_id=department_id).delete()
            db.query(CloudFile).filter_by(department_id=department_id).delete()
            db.query(ScanResult).filter_by(scan_id=scan_id).delete()
            db.query(Scan).filter_by(id=scan_id).delete()
            db.query(CloudOAuthCredentials).filter_by(id=credential_id).delete()
            db.query(User).filter_by(id=user_id).delete()
            db.query(Department).filter_by(id=department_id).delete()
            db.commit()


def set_policy(case, purposes):
    with case.factory() as db:
        department = db.get(Department, case.department_id)
        department.lms_ai_enabled = bool(purposes)
        department.lms_ai_provider = "ollama" if purposes else None
        department.lms_ai_purposes = purposes
        db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["remediation", "alt_text"])
@pytest.mark.parametrize(
    "policy", ["disabled", "other_purpose", "revoked_after_enqueue", "allowed"]
)
async def test_http_queue_worker_persists_policy_outcome(
    policy_queue_http, purpose, policy
):
    case = policy_queue_http
    opposite = "alt_text" if purpose == "remediation" else "remediation"
    purposes = (
        [purpose]
        if policy in {"allowed", "revoked_after_enqueue"}
        else [opposite] if policy == "other_purpose" else []
    )
    set_policy(case, purposes)
    response = case.client.post(
        f"/education/remediate/{case.scan_id}",
        headers={"Prefer": "respond-async"},
        json={
            "use_ai": purpose == "remediation",
            "generate_alt_text": purpose == "alt_text",
        },
    )
    assert response.status_code == 202
    receipt = response.json()
    if policy == "revoked_after_enqueue":
        set_policy(case, [])

    # Preserve real policy/session logic but replace the provider constructor
    # with a fail-fast sentinel: no test may contact an inference service.
    real_bind = LMSRemediationClient.bind_if_allowed
    provider = MagicMock(side_effect=AssertionError("unexpected provider dispatch"))
    process = AsyncMock(wraps=process_remediation_job)
    with (
        patch.object(
            LMSRemediationClient,
            "bind_if_allowed",
            side_effect=lambda **kwargs: real_bind(**kwargs, provider_factory=provider),
        ) as binding,
        patch("src.jobs.remediation_job.process_remediation_job", new=process),
    ):
        [claim] = case.worker.claim_batch(limit=1)
        assert claim.job_id == receipt["job_id"]
        assert await case.worker.process_claim(claim) is True
    provider.assert_not_called()
    case.worker._token_manager.refresh_if_expired.assert_not_called()
    binding.assert_called_once()
    assert binding.call_args.kwargs["purpose"] == purpose

    status = case.client.get(receipt["status_url"])
    latest = case.client.get(f"/education/scans/{case.scan_id}/remediation/latest")
    assert status.status_code == latest.status_code == 200
    assert status.json() == latest.json()
    public = status.json()
    assert public["download_available"] is False
    assert public["download_url"] is None
    with case.factory() as db:
        job = db.get(CloudJobQueue, receipt["job_id"])
        scan = db.get(Scan, case.scan_id)
        assert job.claim_token is None
        assert job.attempt_count == 1
        if policy == "allowed":
            process.assert_awaited_once()
            assert public["status"] == job.status == "completed"
            assert public["error_code"] is None
            assert scan.remediation_outcome == "no_op"
            bound = process.await_args.kwargs[
                "ai_client" if purpose == "remediation" else "alt_text_client"
            ]
            assert bound.purpose == purpose
            assert bound.provider == "ollama"
        else:
            process.assert_not_awaited()
            assert public["status"] == job.status == "failed"
            assert public["error_code"] == job.last_error_code == "policy_not_permitted"
            assert job.last_error_retryable is False
            assert scan.status == ScanStatus.FAILED
            assert scan.remediation_outcome == "remediation_failed"
    assert case.worker.claim_batch(limit=1) == []
