"""HTTP integration contracts for local artifacts, PostgreSQL, and real files.

Authentication is supplied as a trusted dependency override. These tests cover
the mounted router and persistence, not login, middleware, or browser journeys.
"""

from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace
import uuid
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from src.api.education import remediation_routes
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import engine, get_db_dependency
from src.db.models import (
    CloudFile,
    CloudOAuthCredentials,
    Department,
    RemediationArtifact,
    RemediationOutcome,
    ReviewAuditLog,
    Scan,
    ScanFix,
    ScanStatus,
    ScanType,
    User,
    UserRole,
)
from src.services.remediation_artifact_service import RemediationArtifactService
from src.services.scan_fix_service import review_digest_for

pytestmark = pytest.mark.integration
OPERATIONS = ("metadata", "download", "approve", "reject")


@pytest.fixture
def artifact_http(tmp_path, monkeypatch):
    """Keep route commits real while rolling back all fixture data afterward."""
    with engine.connect() as connection:
        transaction = connection.begin()
        db = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            department = Department(
                id=str(uuid.uuid4()),
                name="Artifact integration department",
                institution="Example University",
                contact_email="artifacts@example.edu",
            )
            db.add(department)
            db.flush()
            user = User(
                id=str(uuid.uuid4()),
                department_id=department.id,
                email=f"artifact-{uuid.uuid4().hex}@example.edu",
                role=UserRole.ADMIN,
            )
            db.add(user)
            db.flush()
            scan = Scan(
                id=str(uuid.uuid4()),
                user_id=user.id,
                department_id=department.id,
                scan_type=ScanType.WORD,
                file_name="source.docx",
                status=ScanStatus.COMPLETED,
                remediation_outcome=RemediationOutcome.COMPLETED.value,
            )
            db.add(scan)
            db.flush()
            fix = ScanFix(
                scan_id=scan.id,
                issue_id="heading-1",
                occurrence_key=hashlib.sha256(b"heading-1").hexdigest(),
                category="structure",
                severity="high",
                description="Heading level repaired",
                original_content="Title",
                fixed_content="Title",
                fix_method="rule",
                confidence=1.0,
                needs_review=False,
                review_status="auto_approved",
            )
            fix.review_digest = review_digest_for(fix)
            fix.approved_review_digest = fix.review_digest
            db.add(fix)
            service = RemediationArtifactService(
                root=tmp_path / "artifacts",
                max_bytes=1024 * 1024,
                retention_days=30,
                approved_retention_days=30,
                written_retention_days=7,
                staging_grace_seconds=3600,
            )
            artifact_id = str(uuid.uuid4())
            storage_key = f"{department.id}/{scan.id}/{artifact_id}/{uuid.uuid4()}.docx"
            path = service.root / storage_key
            path.parent.mkdir(parents=True)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr(
                    "word/document.xml", "<document><p>Title</p></document>"
                )
            payload = path.read_bytes()
            artifact = RemediationArtifact(
                id=artifact_id,
                department_id=department.id,
                scan_id=scan.id,
                created_by_id=user.id,
                provider="local",
                scan_type="WORD",
                storage_backend="local",
                storage_key=storage_key,
                filename="Reviewed document.docx",
                mime_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                lifecycle_status="available",
                review_status="pending",
                provider_result={"requires_approval": True},
                created_at=datetime.now(timezone.utc) - timedelta(days=2),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            db.add(artifact)
            db.flush()
            scan.current_remediation_artifact_id = artifact.id
            db.commit()

            principal = AuthenticatedPrincipal(
                api_key=None,
                user_id=user.id,
                department_id=department.id,
                user_role=UserRole.ADMIN,
                auth_method="session",
            )
            app = FastAPI()
            app.include_router(remediation_routes.router, prefix="/education")

            def database():
                yield db

            app.dependency_overrides[get_db_dependency] = database
            app.dependency_overrides[get_authenticated_principal] = lambda: principal
            monkeypatch.setattr(
                RemediationArtifactService,
                "from_settings",
                classmethod(lambda cls: service),
            )
            with TestClient(app) as client:
                yield SimpleNamespace(
                    app=app,
                    client=client,
                    db=db,
                    scan=scan,
                    artifact=artifact,
                    fix=fix,
                    user=user,
                    path=path,
                    payload=payload,
                    url=f"/education/scans/{scan.id}/artifacts/{artifact.id}",
                )
        finally:
            db.close()
            transaction.rollback()


def _request(case, operation, url=None):
    url = case.url if url is None else url
    if operation == "metadata":
        return case.client.get(url)
    if operation == "download":
        return case.client.get(f"{url}/download")
    return case.client.post(f"{url}/{operation}")


def _audit(case, action):
    return (
        case.db.query(ReviewAuditLog)
        .filter(ReviewAuditLog.scan_id == case.scan.id, ReviewAuditLog.action == action)
        .all()
    )


def test_current_artifact_metadata_matches_persisted_file(artifact_http):
    case = artifact_http
    response = _request(case, "metadata")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == case.artifact.id
    assert body["scan_id"] == case.scan.id
    assert body["sha256"] == hashlib.sha256(case.payload).hexdigest()
    assert body["size_bytes"] == len(case.payload)
    assert body["filename"] == "Reviewed document.docx"
    assert body["mime_type"] == case.artifact.mime_type
    assert body["availability"] == "available"
    assert body["can_approve"] is True
    assert body["approval_blockers"] == []
    assert "storage_key" not in body


def test_approval_commits_review_audit_and_downloads_exact_bytes(artifact_http):
    case = artifact_http
    response = _request(case, "approve")
    assert response.status_code == 200
    assert response.json() == {
        "id": case.artifact.id,
        "review_status": "approved",
        "sha256": case.artifact.sha256,
    }
    case.db.expire_all()
    assert case.artifact.approved_by_id == case.user.id
    assert case.artifact.approval_checksum == case.artifact.sha256
    assert case.artifact.approval_review_digest
    assert case.artifact.approved_at is not None
    audits = _audit(case, "artifact_approved")
    assert len(audits) == 1
    assert audits[0].user_id == case.user.id
    assert audits[0].details["artifact_id"] == case.artifact.id
    downloaded = _request(case, "download")
    assert downloaded.status_code == 200
    assert downloaded.content == case.payload
    assert downloaded.headers["content-length"] == str(len(case.payload))
    assert downloaded.headers["content-type"] == case.artifact.mime_type
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''Reviewed%20document.docx"
    )
    assert _request(case, "approve").status_code == 200
    assert len(_audit(case, "artifact_approved")) == 1


def test_rejection_commits_audit_and_blocks_approval_and_gated_download(artifact_http):
    case = artifact_http
    response = _request(case, "reject")
    assert response.status_code == 200
    assert response.json()["review_status"] == "rejected"
    case.db.expire_all()
    assert case.artifact.rejected_by_id == case.user.id
    assert case.artifact.rejected_at is not None
    assert case.artifact.approval_checksum is None
    assert len(_audit(case, "artifact_rejected")) == 1
    assert _request(case, "reject").status_code == 200
    assert len(_audit(case, "artifact_rejected")) == 1
    assert _request(case, "approve").status_code == 404
    assert _request(case, "download").status_code == 404
    metadata = _request(case, "metadata").json()
    assert metadata["can_approve"] is False
    assert "review_rejected" in metadata["approval_blockers"]


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("missing", ["scan", "artifact", "invalid_identifier"])
def test_missing_or_invalid_artifact_identifiers_return_not_found(
    artifact_http, operation, missing
):
    case = artifact_http
    scan_id = str(uuid.uuid4()) if missing == "scan" else case.scan.id
    artifact_id = case.artifact.id if missing == "scan" else str(uuid.uuid4())
    if missing == "invalid_identifier":
        artifact_id = "not-an-artifact-id"
    response = _request(
        case, operation, f"/education/scans/{scan_id}/artifacts/{artifact_id}"
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Artifact not found"}
    assert case.artifact.review_status == "pending"


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("state", ["expired", "superseded", "not_current"])
def test_unavailable_candidate_cannot_be_read_or_reviewed(
    artifact_http, operation, state
):
    case = artifact_http
    if operation == "download":
        # Begin with a downloadable candidate so the unavailable state, rather
        # than pending review, is the reason this request must be refused.
        assert _request(case, "approve").status_code == 200
        assert _request(case, "download").status_code == 200
    if state == "expired":
        case.artifact.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    elif state == "superseded":
        case.artifact.lifecycle_status = "superseded"
    else:
        case.scan.current_remediation_artifact_id = None
    case.db.commit()
    response = _request(case, operation)
    assert response.status_code == 404
    assert response.json() == {"detail": "Artifact unavailable"}
    case.db.expire_all()
    assert case.artifact.review_status == (
        "approved" if operation == "download" else "pending"
    )
    assert len(_audit(case, "artifact_approved")) == (operation == "download")
    assert _audit(case, "artifact_rejected") == []


@pytest.mark.parametrize("operation", OPERATIONS)
def test_missing_stored_bytes_return_conflict_without_review_mutation(
    artifact_http, operation
):
    case = artifact_http
    # A normal storage-loss condition; retain the durable artifact metadata.
    case.path.rename(case.path.with_suffix(".unavailable"))
    if operation == "download":
        case.artifact.provider_result = {}
        case.db.commit()
    response = _request(case, operation)
    assert response.status_code == 409
    assert response.json() == {"detail": "Artifact unavailable"}
    case.db.expire_all()
    assert case.artifact.review_status == "pending"
    assert _audit(case, "artifact_approved") == []
    assert _audit(case, "artifact_rejected") == []


def test_pending_review_blocks_gated_download(artifact_http):
    response = _request(artifact_http, "download")
    assert response.status_code == 404
    assert response.json() == {"detail": "Artifact unavailable"}
    assert artifact_http.artifact.review_status == "pending"


def test_ungated_candidate_download_preserves_pending_review(artifact_http):
    case = artifact_http
    case.artifact.provider_result = {}
    case.db.commit()
    response = _request(case, "download")
    assert response.status_code == 200
    assert response.content == case.payload
    case.db.expire_all()
    assert case.artifact.review_status == "pending"
    assert _audit(case, "artifact_approved") == []


@pytest.mark.parametrize(
    "blocker", ["fixes_pending_review", "scan_not_completed", "verification_not_passed"]
)
def test_metadata_explains_approval_blocker_and_approval_refuses_it(
    artifact_http, blocker
):
    case = artifact_http
    if blocker == "fixes_pending_review":
        case.fix.review_status = "pending"
        case.fix.approved_review_digest = None
    elif blocker == "scan_not_completed":
        case.scan.status = ScanStatus.PROCESSING
    else:
        case.scan.remediation_outcome = RemediationOutcome.MANUAL_REQUIRED.value
    case.db.commit()
    metadata = _request(case, "metadata")
    assert metadata.status_code == 200
    assert blocker in metadata.json()["approval_blockers"]
    assert metadata.json()["can_approve"] is False
    response = _request(case, "approve")
    assert response.status_code == 404
    case.db.expire_all()
    assert case.artifact.review_status == "pending"
    assert _audit(case, "artifact_approved") == []


def test_changed_fix_review_invalidates_previous_approval_durably(artifact_http):
    case = artifact_http
    assert _request(case, "approve").status_code == 200
    case.fix.review_status = "pending"
    case.fix.approved_review_digest = None
    case.db.commit()
    response = _request(case, "download")
    assert response.status_code == 404
    assert response.json() == {"detail": "Artifact unavailable"}
    case.db.expire_all()
    assert case.artifact.review_status == "pending"
    assert case.artifact.approval_checksum is None
    assert case.artifact.approval_review_digest is None
    assert case.artifact.approved_at is None
    assert len(_audit(case, "artifact_approval_invalidated")) == 1


@pytest.mark.parametrize("operation", OPERATIONS)
def test_artifact_operations_require_the_principals_department(
    artifact_http, operation
):
    case = artifact_http
    other_department = Department(
        id=str(uuid.uuid4()),
        name="Other department",
        institution="Example University",
        contact_email="other@example.edu",
    )
    case.db.add(other_department)
    case.db.flush()
    other_user = User(
        id=str(uuid.uuid4()),
        department_id=other_department.id,
        email=f"other-{uuid.uuid4().hex}@example.edu",
        role=UserRole.ADMIN,
    )
    case.db.add(other_user)
    case.db.commit()
    actor = AuthenticatedPrincipal(
        api_key=None,
        user_id=other_user.id,
        department_id=other_department.id,
        user_role=UserRole.ADMIN,
        auth_method="session",
    )
    case.app.dependency_overrides[get_authenticated_principal] = lambda: actor
    response = _request(case, operation)
    assert response.status_code == 403
    assert response.json() == {"detail": "Access denied"}
    case.db.expire_all()
    assert case.artifact.review_status == "pending"
    assert _audit(case, "artifact_approved") == []
    assert _audit(case, "artifact_rejected") == []


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("course_matches", [True, False])
def test_canvas_course_staff_artifact_scope(artifact_http, operation, course_matches):
    case = artifact_http
    credential = CloudOAuthCredentials(
        id=str(uuid.uuid4()),
        department_id=case.scan.department_id,
        provider="canvas",
        access_token="unused-test-placeholder",
        refresh_token="unused-test-placeholder",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    case.db.add(credential)
    case.db.flush()
    cloud = CloudFile(
        id=str(uuid.uuid4()),
        department_id=case.scan.department_id,
        credential_id=credential.id,
        provider="canvas",
        provider_file_id="document-1",
        provider_parent_id="course-a",
        file_name="source.docx",
        file_type="docx",
        last_scan_id=case.scan.id,
        current_remediation_artifact_id=case.artifact.id,
    )
    case.db.add(cloud)
    case.db.flush()
    case.artifact.provider = "canvas"
    case.artifact.cloud_file_id = cloud.id
    case.artifact.provider_result = {}
    case.scan.document_source = "canvas"
    case.scan.document_id = cloud.id
    case.user.role = UserRole.FACULTY
    case.db.commit()
    actor = AuthenticatedPrincipal(
        api_key=None,
        user_id=case.user.id,
        department_id=case.scan.department_id,
        user_role=UserRole.FACULTY,
        auth_method="lti",
        lti_platform="canvas",
        lti_staff_role="Instructor",
        lti_course_id="course-a" if course_matches else "course-b",
    )
    case.app.dependency_overrides[get_authenticated_principal] = lambda: actor
    response = _request(case, operation)
    case.db.expire_all()
    if not course_matches:
        assert response.status_code == 404
        assert response.json() == {"detail": "Scan not found"}
        assert case.artifact.review_status == "pending"
        assert _audit(case, "artifact_approved") == []
        assert _audit(case, "artifact_rejected") == []
        return
    assert response.status_code == 200
    if operation == "download":
        assert response.content == case.payload
    elif operation == "metadata":
        assert response.json()["id"] == case.artifact.id
    else:
        status = "approved" if operation == "approve" else "rejected"
        assert case.artifact.review_status == status
        assert cloud.writeback_status == status
        assert len(_audit(case, f"artifact_{status}")) == 1
