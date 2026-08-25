"""The Canvas review journey, driven end to end at the server layer.

Every defect this file guards against was found by hand the night before a
demo, because the tests that existed covered mechanics one call at a time:
authentication worked, listing worked, scanning worked, and the journey a
person actually walks was broken in four places at once.

So this test walks the journey instead of the parts. Real database, real
routes, real state transitions. Only the two edges of the system are
stubbed: the browser that runs axe-core, and Canvas itself. Everything
between them is the code a user drives.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudOAuthCredentials,
    CloudProvider,
    ContentWritebackLog,
    Scan,
    ScanResult,
    UserRole,
)
from src.education.canvas_content_scanner import CanvasContentScanner
from src.integrations.canvas.content_models import CanvasContentType
from src.jobs.canvas_content_job import _Candidate, handle_canvas_content_job
from src.jobs.contracts import JobContext, JobSuccess

pytestmark = pytest.mark.integration

DEPARTMENT_ID = "test-dept-456"
USER_ID = "test-user-123"
COURSE_ID = "journey-course-1"

# One image with no alternative text: a real axe-core rule, and the kind of
# thing a course page carries by the hundred.
ORIGINAL_BODY = '<p>Welcome</p><img src="chart.png">'


def _axe(violations, passes):
    return {"violations": violations, "passes": [{} for _ in range(passes)]}


def _image_alt_violation(nodes=1):
    return {
        "id": "image-alt",
        "impact": "critical",
        "description": "Images must have alternate text",
        "help": "Images must have alternative text",
        "tags": ["wcag2a", "wcag111"],
        "nodes": [{"html": '<img src="chart.png">'} for _ in range(nodes)],
    }


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.config.settings import get_settings

    engine = create_engine(get_settings().database_url)
    try:
        engine.connect().close()
    except Exception:
        pytest.skip("Journey test needs the database the CI test job provides")

    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def seeded(db):
    """A Canvas credential and one unscanned page, cleaned up afterwards."""
    credential = CloudOAuthCredentials(
        id=str(uuid.uuid4()),
        department_id=DEPARTMENT_ID,
        provider=CloudProvider.CANVAS.value,
        access_token="encrypted",
        refresh_token="encrypted-refresh",
        token_expires_at=datetime.now(timezone.utc),
        is_active=True,
        provider_metadata={"canvas_instance_url": "https://canvas.example.edu"},
    )
    db.add(credential)
    db.flush()

    page = CloudFile(
        id=str(uuid.uuid4()),
        department_id=DEPARTMENT_ID,
        credential_id=credential.id,
        provider=CloudProvider.CANVAS.value,
        provider_file_id="page-1",
        provider_parent_id=COURSE_ID,
        file_name="Welcome Page",
        file_type="page",
        mime_type="text/html",
        file_size_bytes=len(ORIGINAL_BODY),
        content_source=CanvasContentType.PAGE.value,
        content_body=ORIGINAL_BODY,
        content_updated_at=datetime.now(timezone.utc),
    )
    db.add(page)
    db.commit()

    yield credential, page

    db.query(ContentWritebackLog).filter(
        ContentWritebackLog.cloud_file_id == page.id
    ).delete()
    if page.last_scan_id:
        db.query(ScanResult).filter(ScanResult.scan_id == page.last_scan_id).delete()
        db.query(Scan).filter(Scan.id == page.last_scan_id).delete()
    db.query(CloudFile).filter(CloudFile.id == page.id).delete()
    db.query(CloudOAuthCredentials).filter(
        CloudOAuthCredentials.id == credential.id
    ).delete()
    db.commit()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_authenticated_principal] = (
        lambda: AuthenticatedPrincipal(
            api_key=None,
            user_id=USER_ID,
            department_id=DEPARTMENT_ID,
            user_role=UserRole.FACULTY,
            auth_method="session",
        )
    )
    app.dependency_overrides[get_db_dependency] = lambda: db
    with patch("src.api.canvas_content_routes.require_feature", new_callable=AsyncMock):
        yield TestClient(app)
    app.dependency_overrides.pop(get_authenticated_principal, None)
    app.dependency_overrides.pop(get_db_dependency, None)


@pytest.mark.asyncio
async def test_scan_remediate_approve_write_back(db, seeded, client):
    credential, page = seeded
    canvas = MagicMock()
    scanner = CanvasContentScanner(
        canvas_client=canvas,
        db=db,
        department_id=DEPARTMENT_ID,
        credential_id=credential.id,
    )

    # 1. Scan. The page has one failing rule out of ten checked.
    with patch.object(
        scanner,
        "_run_axe_scan",
        new=AsyncMock(return_value=_axe([_image_alt_violation()], passes=9)),
    ):
        scan = await scanner.scan_content_item(page)

    assert scan["scan_id"]
    db.refresh(page)
    assert page.last_scan_id == scan["scan_id"]
    assert page.last_compliance_score == 90.0

    stored = db.query(ScanResult).filter(ScanResult.scan_id == page.last_scan_id).one()
    assert stored.issues[0]["id"] == "image-alt"

    # 2. The course now reports that page, with its score.
    status = client.get(f"/canvas/content/courses/{COURSE_ID}/status")
    assert status.status_code == 200
    listed = [i for i in status.json()["items"] if i["cloud_file_id"] == page.id]
    assert len(listed) == 1
    assert listed[0]["compliance_score"] == 90.0

    # 3. Enqueue a durable immutable snapshot and execute it under a worker claim.
    queued = client.post(f"/canvas/content/{page.id}/remediate")
    assert queued.status_code == 202
    job = db.get(CloudJobQueue, queued.json()["job_id"])
    assert job is not None
    assert job.max_retries == 0
    claim_token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    job.status = CloudJobStatus.PROCESSING.value
    job.claim_token = claim_token
    job.worker_id = "journey-worker"
    job.claimed_at = now
    job.heartbeat_at = now
    job.lease_expires_at = now
    job.attempt_count = 1
    db.commit()
    context = JobContext(
        job_id=str(job.id),
        job_type="canvas_content",
        payload=job.payload,
        claim_token=claim_token,
        worker_id="journey-worker",
        attempt_count=1,
        report_progress=AsyncMock(return_value=True),
    )
    with patch(
        "src.jobs.canvas_content_job._remediate_snapshot",
        return_value=_Candidate(
            '<p>Welcome</p><img src="chart.png" alt="Chart">',
            1,
            0,
            0,
            100.0,
        ),
    ):
        remediated = await handle_canvas_content_job(context, db, MagicMock())

    assert isinstance(remediated, JobSuccess)
    job = db.get(CloudJobQueue, job.id)
    job.status = CloudJobStatus.COMPLETED.value
    job.progress = 100
    job.result_data = remediated.result
    job.claim_token = None
    job.worker_id = None
    job.claimed_at = None
    job.heartbeat_at = None
    job.lease_expires_at = None
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(page)
    assert page.remediated_body is not None
    assert page.writeback_status == "pending_review"
    assert remediated.result["verified"] is False
    assert page.remediated_compliance_score == 100.0
    assert page.remediated_issues_remaining == 0
    assert remediated.result["issues_introduced"] == 0

    # 4. The review view reports the split and where it came from.
    diff = client.get(f"/canvas/content/{page.id}/diff")
    assert diff.status_code == 200
    body = diff.json()
    assert body["original_html"] == ORIGINAL_BODY
    assert body["issues"][0]["id"] == "image-alt"
    assert body["issues_verified_by_rescan"] is False

    # 5. Approve.
    approve = client.post(f"/canvas/content/{page.id}/approve")
    assert approve.status_code == 200
    db.refresh(page)
    assert page.writeback_status == "approved"

    # 6. Write back. Canvas accepts the edit and reports no later change.
    with (
        patch.object(CanvasContentScanner, "_update_canvas_content", new=AsyncMock()),
        patch.object(
            CanvasContentScanner,
            "_get_canvas_updated_at",
            new=AsyncMock(return_value=page.content_updated_at),
        ),
        patch(
            "src.api.canvas_content_routes._get_canvas_client",
            new=AsyncMock(return_value=(credential, AsyncMock())),
        ),
    ):
        writeback = client.post(f"/canvas/content/{page.id}/writeback")

    assert writeback.status_code == 200
    assert writeback.json()["success"] is True

    db.refresh(page)
    assert page.writeback_status == "written_back"
    assert page.writeback_at is not None
    # What Canvas holds is now the remediated copy.
    assert page.content_body == page.remediated_body

    # 7. The write-back left an audit trail naming who approved it.
    log = (
        db.query(ContentWritebackLog)
        .filter(ContentWritebackLog.cloud_file_id == page.id)
        .one()
    )
    assert log.approved_by == USER_ID
    assert log.written_back_at is not None
    assert log.original_body == ORIGINAL_BODY
