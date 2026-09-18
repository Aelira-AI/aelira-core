"""HTTP integration for remediation enqueue, stored status and artifact mapping.

Positive paths use PostgreSQL and the real enqueue/artifact services. Completed
job state is an explicit fixture for receipt/download mapping, not evidence of
worker processing or an accessibility remediation result.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from test_managed_artifact_routes import artifact_http  # noqa: F401
from src.db.models import CloudJobQueue, Scan, ScanResult, ScanStatus, ScanType

pytestmark = pytest.mark.integration
OPTIONS = {
    "use_ai": False,
    "generate_alt_text": False,
    "latex_formats": ["html", "pdf", "tex"],
    "multimedia_format": "individual",
    "include_original_in_zip": True,
}


@pytest.fixture
def queue_http(artifact_http, tmp_path):  # noqa: F811 - imported pytest fixture
    case = artifact_http
    source = tmp_path / "source.docx"
    source.write_bytes(
        (Path(__file__).parent / "fixtures/document_stack/course.docx").read_bytes()
    )
    case.scan.storage_path = str(source)
    case.scan.file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    case.scan.result = ScanResult(compliance_score=60.0, issues=[])
    case.db.commit()
    return case


def _enqueue(case, scan_id=None, options=None):
    return case.client.post(
        f"/education/remediate/{scan_id or case.scan.id}",
        headers={"Prefer": "respond-async"},
        json=OPTIONS if options is None else options,
    )


def _jobs(case):
    return case.db.query(CloudJobQueue).filter(
        CloudJobQueue.department_id == case.scan.department_id,
        CloudJobQueue.job_type == "remediate",
    )


def _second_scan(case):
    scan = Scan(
        id=str(uuid.uuid4()),
        user_id=case.user.id,
        department_id=case.scan.department_id,
        scan_type=ScanType.WORD,
        file_name="second.docx",
        storage_path=case.scan.storage_path,
        status=ScanStatus.COMPLETED,
        result=ScanResult(compliance_score=65.0, issues=[]),
    )
    case.db.add(scan)
    case.db.commit()
    return scan


def test_enqueue_persists_job_and_returns_status_and_latest(queue_http):
    case = queue_http
    latest_url = f"/education/scans/{case.scan.id}/remediation/latest"
    empty = case.client.get(latest_url)
    assert empty.status_code == 200
    assert empty.json() is None
    response = _enqueue(case)
    assert response.status_code == 202
    contract = response.json()
    job = _jobs(case).one()
    assert contract == {
        "job_id": job.id,
        "scan_id": case.scan.id,
        "status": "pending",
        "status_url": f"/education/remediation/jobs/{job.id}",
    }
    assert job.payload == {
        "scan_id": case.scan.id,
        "requested_by_id": case.user.id,
        "options": OPTIONS,
    }
    canonical = json.dumps(OPTIONS, sort_keys=True, separators=(",", ":")).encode()
    assert (
        job.dedupe_key
        == f"remediate:{case.scan.id}:{hashlib.sha256(canonical).hexdigest()}"
    )
    assert job.provider == "local"
    assert job.max_retries == 0
    assert job.cloud_file_id is None
    assert job.credential_id is None
    assert job.execution_context == {
        "ai_requested": False,
        "alt_text_requested": False,
        "requested_purposes": [],
        "originating_route": "education_api",
    }
    status = case.client.get(contract["status_url"])
    latest = case.client.get(latest_url)
    assert status.status_code == latest.status_code == 200
    assert latest.json() == status.json()
    body = status.json()
    assert body["job_id"] == job.id
    assert body["scan_id"] == case.scan.id
    assert body["status"] == "pending"
    assert body["progress"] == 0
    assert body["original_score"] == 60.0
    assert body["score_verified"] is False
    assert body["remediated_score"] is None
    assert body["download_available"] is False
    assert body["download_url"] is None


def test_repeat_enqueue_reuses_active_job(queue_http):
    first = _enqueue(queue_http)
    second = _enqueue(queue_http)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert _jobs(queue_http).count() == 1


def test_completed_job_maps_to_approved_artifact_download(queue_http):
    case = queue_http
    response = _enqueue(case)
    assert response.status_code == 202
    status_url = response.json()["status_url"]
    job = _jobs(case).one()
    assert case.client.get(f"{status_url}/download").status_code == 404
    # Explicit stored completion fixture: no worker or model is run here.
    artifact_id = case.artifact.id
    job.status = "completed"
    job.completed_at = datetime.now(timezone.utc)
    job.progress = 100
    job.result_data = {"artifact_id": artifact_id, "download_available": True}
    case.db.commit()
    pending_review = case.client.get(status_url)
    assert pending_review.status_code == 200
    assert pending_review.json()["download_available"] is False
    assert case.client.get(f"{status_url}/download").status_code == 404
    assert case.client.post(f"{case.url}/approve").status_code == 200
    status = case.client.get(status_url)
    latest = case.client.get(f"/education/scans/{case.scan.id}/remediation/latest")
    assert status.status_code == latest.status_code == 200
    assert status.json() == latest.json()
    assert status.json()["status"] == "completed"
    assert status.json()["artifact_id"] == case.artifact.id
    assert status.json()["download_available"] is True
    assert status.json()["download_url"] == f"{status_url}/download"
    downloaded = case.client.get(status.json()["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content == case.payload
    assert downloaded.headers["etag"] == f'"{case.artifact.sha256}"'
    assert downloaded.headers["content-length"] == str(len(case.payload))


@pytest.mark.parametrize("missing", ["result", "source"])
def test_enqueue_requires_result_and_original_source(queue_http, missing):
    case = queue_http
    if missing == "result":
        case.db.delete(case.scan.result)
    else:
        case.scan.storage_path = None
    case.db.commit()
    case.db.expire_all()
    response = _enqueue(case)
    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == {
            "result": "Scan has no results to remediate",
            "source": "Original file not available",
        }[missing]
    )
    assert _jobs(case).count() == 0


@pytest.mark.parametrize(
    "options",
    [
        {**OPTIONS, "latex_formats": ["unknown"]},
        {**OPTIONS, "multimedia_format": "unknown"},
        {**OPTIONS, "use_ai": "unknown"},
    ],
    ids=["latex_format", "multimedia_format", "boolean"],
)
def test_enqueue_rejects_invalid_options(queue_http, options):
    response = _enqueue(queue_http, options=options)
    assert response.status_code == 422
    assert _jobs(queue_http).count() == 0


@pytest.mark.parametrize("operation", ["enqueue", "status", "latest", "download"])
def test_missing_remediation_record_returns_404(queue_http, operation):
    case = queue_http
    missing_id = str(uuid.uuid4())
    if operation == "enqueue":
        response = _enqueue(case, scan_id=missing_id)
    else:
        path = {
            "status": f"/education/remediation/jobs/{missing_id}",
            "latest": f"/education/scans/{missing_id}/remediation/latest",
            "download": f"/education/remediation/jobs/{missing_id}/download",
        }[operation]
        response = case.client.get(path)
    assert response.status_code == 404
    assert _jobs(case).count() == 0


def test_batch_enqueues_all_requested_scans(queue_http):
    case = queue_http
    second = _second_scan(case)
    scan_ids = [case.scan.id, second.id]
    response = case.client.post(
        "/education/remediate/batch", params={"use_ai": False}, json=scan_ids
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["total_scans"] == 2
    assert body["scans_queued"] == scan_ids
    jobs = _jobs(case).all()
    assert set(body["job_ids"]) == {job.id for job in jobs}
    assert {job.payload["scan_id"] for job in jobs} == set(scan_ids)
    assert all(job.payload["options"]["use_ai"] is False for job in jobs)
    assert all(job.status == "pending" for job in jobs)
    for scan_id in scan_ids:
        status = case.client.get(f"/education/scans/{scan_id}/remediation/latest")
        assert status.status_code == 200
        assert status.json()["job_id"] in body["job_ids"]


def test_batch_missing_scan_refuses_entire_request(queue_http):
    case = queue_http
    response = case.client.post(
        "/education/remediate/batch",
        params={"use_ai": False},
        json=[case.scan.id, str(uuid.uuid4())],
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Scan not found"}
    assert _jobs(case).count() == 0


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch_second_job"])
def test_queue_rejection_returns_409_and_rolls_back_all_jobs(queue_http, batch):
    case = queue_http
    scan_ids = [case.scan.id, _second_scan(case).id] if batch else [case.scan.id]
    attempted = []
    connection = case.db.get_bind()

    def reject(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith("insert into cloud_job_queue"):
            attempted.append(statement)
            if len(attempted) == (2 if batch else 1):
                raise IntegrityError(
                    statement, parameters, RuntimeError("fixture queue rejection")
                )

    event.listen(connection, "before_cursor_execute", reject)
    try:
        response = (
            case.client.post(
                "/education/remediate/batch", params={"use_ai": False}, json=scan_ids
            )
            if batch
            else _enqueue(case)
        )
    finally:
        event.remove(connection, "before_cursor_execute", reject)
    assert response.status_code == 409
    assert response.json() == {"detail": "Remediation could not be queued"}
    assert len(attempted) == (2 if batch else 1)
    assert _jobs(case).count() == 0
