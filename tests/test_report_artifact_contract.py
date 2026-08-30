from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from pypdf import PdfReader

from src.auth.dependencies import AuthenticatedPrincipal
from src.auth.dependencies import get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import CloudJobQueue, CloudJobStatus, UserRole
from src.jobs.contracts import FailureKind, JobContext, JobFailure, JobSuccess
from src.jobs.report_job import handle_report_job

ROOT = Path(__file__).parents[1]


def _compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text())


def test_every_compose_service_shares_the_configured_report_artifact_root():
    for name in (
        "docker-compose.dev.yml",
        "docker-compose.prod.yml",
        "docker-compose.quickstart.yml",
    ):
        compose = _compose(name)
        api = compose["services"]["api"]
        worker = compose["services"]["worker"]
        environment = api["environment"]

        assert "upload_data:/app/uploads" in api["volumes"]
        assert "upload_data:/app/uploads" in worker["volumes"]
        assert "upload_data" in compose["volumes"]
        assert environment["REPORT_ARTIFACT_DIR"].endswith(
            "/app/uploads/report-artifacts}"
        )
        assert environment["REPORT_ARTIFACT_MAX_BYTES"].endswith("20971520}")


def test_environment_template_documents_report_artifact_limits():
    template = (ROOT / ".env.example").read_text()

    assert "REPORT_ARTIFACT_DIR=/app/uploads/report-artifacts" in template
    assert "REPORT_ARTIFACT_MAX_BYTES=20971520" in template
    assert "remediation and report artifacts" in template.lower()
    assert "all api and worker replicas" in template.lower()


def _principal(department_id: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id=department_id,
        user_role=UserRole.ADMIN,
        auth_method="session",
    )


def _context(job_id: str, payload: dict) -> JobContext:
    async def report_progress(_progress: int, _message: str | None = None) -> bool:
        return True

    return JobContext(
        job_id=job_id,
        job_type="report",
        payload=payload,
        claim_token="claim-1",
        worker_id="worker-1",
        attempt_count=1,
        report_progress=report_progress,
    )


class _Session:
    def __init__(self, job: CloudJobQueue):
        self.job = job

    def get(self, model, job_id):
        assert model is CloudJobQueue
        return self.job if self.job.id == job_id else None


@pytest.mark.asyncio
async def test_report_worker_generates_immutable_bounded_pdf(monkeypatch, tmp_path):
    from src.jobs import report_job

    monkeypatch.setattr(
        report_job,
        "get_settings",
        lambda: SimpleNamespace(
            report_artifact_dir=str(tmp_path), report_artifact_max_bytes=2_000_000
        ),
    )
    job = CloudJobQueue(
        id="11111111-1111-4111-8111-111111111111",
        department_id="dept-1",
        job_type="report",
        payload={},
    )
    payload = {
        "report_kind": "scan",
        "target": "https://example.test",
        "compliance_score": 82,
        "total_issues": 2,
        "severity_totals": {
            "critical": 0,
            "serious": 1,
            "moderate": 0,
            "minor": 1,
        },
        "issues": [
            {
                "impact": "serious",
                "description": "Image has no alternate text",
                "element": "img.hero",
                "fix": "Add meaningful alternate text",
                "rule": "image-alt",
            }
        ],
    }

    result = await handle_report_job(_context(job.id, payload), _Session(job), None)

    assert isinstance(result, JobSuccess)
    assert result.result["artifact_id"] == job.id
    assert result.result["content_type"] == "application/pdf"
    assert result.result["size_bytes"] > 4
    assert len(result.result["sha256"]) == 64
    artifact = Path(tmp_path, result.result["storage_key"])
    pdf = artifact.read_bytes()
    assert pdf.startswith(b"%PDF-")
    assert hashlib.sha256(pdf).hexdigest() == result.result["sha256"]
    assert len(pdf) == result.result["size_bytes"]
    text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages
    )
    assert "Accessibility Issues (2)" in text
    assert "showing first 1" in text


@pytest.mark.asyncio
async def test_report_worker_rejects_malformed_payload_without_exception_text(
    tmp_path, monkeypatch
):
    from src.jobs import report_job

    monkeypatch.setattr(
        report_job,
        "get_settings",
        lambda: SimpleNamespace(
            report_artifact_dir=str(tmp_path), report_artifact_max_bytes=2_000_000
        ),
    )
    job = CloudJobQueue(
        id="22222222-2222-4222-8222-222222222222",
        department_id="dept-1",
        job_type="report",
        payload={},
    )

    result = await handle_report_job(
        _context(
            job.id,
            {"report_kind": "scan", "target": "/private/server/path", "issues": "bad"},
        ),
        _Session(job),
        None,
    )

    assert isinstance(result, JobFailure)
    assert result.kind is FailureKind.DETERMINISTIC
    assert result.code == "report_payload_invalid"
    assert result.details == {}


@pytest.mark.asyncio
async def test_report_routes_are_tenant_scoped_and_sanitize_failed_jobs(monkeypatch):
    from fastapi import HTTPException
    from src.api.education import report_routes

    foreign = CloudJobQueue(
        id="33333333-3333-4333-8333-333333333333",
        department_id="dept-2",
        job_type="report",
        payload={},
        status=CloudJobStatus.FAILED.value,
        last_error_code="report_generation_failed",
        error_message="/private/server/path: secret details",
    )

    class Query:
        def filter(self, *args):
            return self

        def first(self):
            return None

    db = SimpleNamespace(query=lambda _model: Query())
    with pytest.raises(HTTPException) as caught:
        await report_routes.get_report_status(
            foreign.id, db=db, principal=_principal("dept-1")
        )
    assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as download_caught:
        await report_routes.download_report(
            foreign.id, db=db, principal=_principal("dept-1")
        )
    assert download_caught.value.status_code == 404

    own = foreign
    own.department_id = "dept-1"
    monkeypatch.setattr(report_routes, "_get_report_job", lambda *_args, **_kwargs: own)
    status = await report_routes.get_report_status(
        own.id, db=db, principal=_principal("dept-1")
    )
    assert status["status"] == "failed"
    assert status["error_code"] == "report_generation_failed"
    assert "/private/" not in str(status)


@pytest.mark.asyncio
async def test_report_request_queues_one_scoped_job_and_returns_its_status_url():
    from src.api.education.report_routes import ReportRequest, create_report

    added = []

    class DB:
        def add(self, value):
            added.append(value)

        def commit(self):
            return None

        def rollback(self):
            raise AssertionError("rollback not expected")

    response = await create_report(
        ReportRequest(
            report_kind="scan",
            target="fixture.html",
            compliance_score=100,
            issues=[],
        ),
        db=DB(),
        principal=_principal("dept-1"),
    )

    assert len(added) == 1
    assert added[0].department_id == "dept-1"
    assert added[0].job_type == "report"
    assert added[0].status == CloudJobStatus.PENDING.value
    assert response["job_id"] == added[0].id
    assert response["status_url"] == f"/education/reports/{added[0].id}"


def test_authenticated_report_route_is_registered_and_validates_totals():
    from src.api.main import app

    added = []

    class DB:
        def add(self, value):
            added.append(value)

        def commit(self):
            return None

        def rollback(self):
            raise AssertionError("rollback not expected")

    app.dependency_overrides[get_db_dependency] = lambda: DB()
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal("dept-1")
    try:
        client = TestClient(app)
        accepted = client.post(
            "/education/reports",
            json={
                "report_kind": "scan",
                "target": "fixture.html",
                "compliance_score": 50,
                "issues": [],
                "total_issues": 2,
                "severity_totals": {
                    "critical": 0,
                    "serious": 1,
                    "moderate": 0,
                    "minor": 1,
                },
            },
        )
        rejected = client.post(
            "/education/reports",
            json={
                "report_kind": "scan",
                "target": "fixture.html",
                "compliance_score": 50,
                "issues": [],
                "total_issues": 2,
                "severity_totals": {
                    "critical": 0,
                    "serious": 0,
                    "moderate": 0,
                    "minor": 1,
                },
            },
        )
        undersized_total = client.post(
            "/education/reports",
            json={
                "report_kind": "scan",
                "target": "fixture.html",
                "compliance_score": 50,
                "issues": [{"impact": "serious"}],
                "total_issues": 0,
            },
        )
    finally:
        app.dependency_overrides.pop(get_db_dependency, None)
        app.dependency_overrides.pop(get_authenticated_principal, None)

    assert accepted.status_code == 202
    assert accepted.json()["status_url"].startswith("/education/reports/")
    assert len(added) == 1
    assert rejected.status_code == 422
    assert undersized_total.status_code == 422


@pytest.mark.asyncio
async def test_completed_report_download_rechecks_identity_and_returns_headers(
    monkeypatch,
):
    from src.api.education import report_routes

    pdf = b"%PDF-verified"
    digest = hashlib.sha256(pdf).hexdigest()
    job = CloudJobQueue(
        id="44444444-4444-4444-8444-444444444444",
        department_id="dept-1",
        job_type="report",
        payload={},
        status=CloudJobStatus.COMPLETED.value,
        result_data={
            "artifact_id": "44444444-4444-4444-8444-444444444444",
            "content_type": "application/pdf",
            "filename": "aelira-accessibility-report.pdf",
            "sha256": digest,
            "size_bytes": len(pdf),
            "storage_key": "safe/report.pdf",
        },
    )
    monkeypatch.setattr(report_routes, "_get_report_job", lambda *_args, **_kwargs: job)
    monkeypatch.setattr(
        report_routes, "read_report_artifact", lambda *_args, **_kwargs: pdf
    )

    response = await report_routes.download_report(
        job.id, db=SimpleNamespace(), principal=_principal("dept-1")
    )

    assert response.body == pdf
    assert response.media_type == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-length"] == str(len(pdf))
    assert response.headers["x-artifact-id"] == job.id
    assert response.headers["x-checksum-sha256"] == digest
