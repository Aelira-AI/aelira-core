"""Review mutation HTTP contracts with real PostgreSQL transactions and audits.

The real database dependency manages each request's commit failure cleanup.
Authentication is a trusted fixture; browser and login flows are out of scope.
"""

from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from src.api import review_routes
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db import database
from src.db.models import (
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
from src.services.scan_fix_service import (
    artifact_approval_review_digest,
    review_digest_for,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def review_http(monkeypatch):
    with database.engine.connect() as connection:
        outer = connection.begin()
        factory = sessionmaker(
            bind=connection, join_transaction_mode="create_savepoint"
        )
        try:
            department_id, user_id, scan_id = (str(uuid.uuid4()) for _ in range(3))
            fix_ids = {}
            with factory() as db:
                db.add(
                    Department(
                        id=department_id,
                        name="Review integration department",
                        institution="Example University",
                        contact_email="reviews@example.edu",
                    )
                )
                db.flush()
                db.add(
                    User(
                        id=user_id,
                        department_id=department_id,
                        email=f"review-{uuid.uuid4().hex}@example.edu",
                        role=UserRole.ADMIN,
                    )
                )
                db.flush()
                db.add(
                    Scan(
                        id=scan_id,
                        department_id=department_id,
                        user_id=user_id,
                        scan_type=ScanType.WORD,
                        file_name="review.docx",
                        status=ScanStatus.COMPLETED,
                        remediation_outcome=RemediationOutcome.COMPLETED.value,
                    )
                )
                db.flush()
                for name, category, confidence, status in (
                    ("high", "structure", 0.95, "pending"),
                    ("low", "structure", 0.5, "pending"),
                    ("text", "text", 0.9, "pending"),
                    ("unscored", "structure", None, "pending"),
                    ("terminal", "structure", 0.99, "approved"),
                ):
                    fix_ids[name] = str(uuid.uuid4())
                    fix = ScanFix(
                        id=fix_ids[name],
                        scan_id=scan_id,
                        issue_id=name,
                        occurrence_key=hashlib.sha256(name.encode()).hexdigest(),
                        category=category,
                        severity="medium",
                        description=f"Review {name} fix",
                        original_content="Original heading",
                        fixed_content="Proposed heading",
                        fix_method="rule",
                        confidence=confidence,
                        needs_review=status == "pending",
                        review_status=status,
                    )
                    fix.review_digest = review_digest_for(fix)
                    if status == "approved":
                        fix.approved_review_digest = fix.review_digest
                    db.add(fix)
                db.commit()
            # Preserve the actual get_db_dependency rollback/close behavior.
            monkeypatch.setattr(database, "SessionLocal", factory)
            actor = AuthenticatedPrincipal(
                api_key=None,
                user_id=user_id,
                department_id=department_id,
                user_role=UserRole.ADMIN,
                auth_method="session",
            )
            app = FastAPI()
            app.include_router(review_routes.router, prefix="/api")
            app.dependency_overrides[get_authenticated_principal] = lambda: actor
            with TestClient(app, raise_server_exceptions=False) as client:
                yield SimpleNamespace(
                    client=client,
                    factory=factory,
                    department_id=department_id,
                    user_id=user_id,
                    scan_id=scan_id,
                    fix_ids=fix_ids,
                    url=f"/api/reviews/{scan_id}",
                )
        finally:
            outer.rollback()


def _fix_request(case, body, name="high"):
    return case.client.post(f"{case.url}/fixes/{case.fix_ids[name]}", json=body)


def _audits(db, case):
    return db.query(ReviewAuditLog).filter(ReviewAuditLog.scan_id == case.scan_id).all()


@pytest.mark.parametrize("action", ["approve", "reject", "edit"])
def test_single_review_persists_decision_digest_and_audit(review_http, action):
    case = review_http
    body = {"action": action, "notes": "Reviewed against source"}
    if action == "edit":
        body["edited_content"] = "Corrected heading"
    response = _fix_request(case, body)
    assert response.status_code == 200
    status = "rejected" if action == "reject" else "approved"
    assert response.json() == {
        "status": "ok",
        "fix_id": case.fix_ids["high"],
        "review_status": status,
    }
    with case.factory() as db:
        fix = db.get(ScanFix, case.fix_ids["high"])
        assert fix.review_status == status
        assert fix.reviewed_by == case.user_id
        assert fix.reviewed_at is not None
        assert fix.review_notes == body["notes"]
        assert fix.fixed_content == (
            "Corrected heading" if action == "edit" else "Proposed heading"
        )
        if action == "reject":
            assert fix.approved_review_digest is None
        else:
            assert fix.approved_review_digest == review_digest_for(fix)
            assert fix.review_digest == fix.approved_review_digest
        audits = _audits(db, case)
        assert len(audits) == 1
        assert audits[0].action == f"fix_{action}"
        assert audits[0].fix_id == fix.id
        assert audits[0].user_id == case.user_id
        assert audits[0].details == {"notes": body["notes"], "edited": action == "edit"}
        assert db.get(ScanFix, case.fix_ids["low"]).review_status == "pending"


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize(
    "selection,expected",
    [
        ("all", {"high", "low", "text", "unscored"}),
        ("ids", {"high"}),
        ("confidence", {"high", "text"}),
        ("category", {"high", "low", "unscored"}),
        ("combined", {"high"}),
        ("none", set()),
    ],
)
def test_batch_review_applies_only_selected_pending_fixes(
    review_http, action, selection, expected
):
    case = review_http
    body = {"action": action, "notes": "Batch review"}
    if selection == "ids":
        body["fix_ids"] = [case.fix_ids["high"], case.fix_ids["terminal"]]
    if selection in {"confidence", "combined"}:
        body["min_confidence"] = 0.9
    if selection in {"category", "combined"}:
        body["category"] = "structure"
    if selection == "combined":
        body["fix_ids"] = list(case.fix_ids.values())
    if selection == "none":
        body["fix_ids"] = [str(uuid.uuid4())]
    response = case.client.post(f"{case.url}/batch", json=body)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "affected": len(expected)}
    with case.factory() as db:
        expected_ids = {case.fix_ids[name] for name in expected}
        for name, fix_id in case.fix_ids.items():
            fix = db.get(ScanFix, fix_id)
            if name in expected:
                assert fix.review_status == (
                    "approved" if action == "approve" else "rejected"
                )
                assert fix.reviewed_by == case.user_id
                assert fix.reviewed_at is not None
                assert fix.review_notes == "Batch review"
                assert fix.approved_review_digest == (
                    review_digest_for(fix) if action == "approve" else None
                )
            else:
                assert fix.review_status == (
                    "approved" if name == "terminal" else "pending"
                )
                assert fix.reviewed_by is None
        audits = _audits(db, case)
        per_fix = [row for row in audits if row.action == f"fix_batch_{action}"]
        assert {row.fix_id for row in per_fix} == expected_ids
        assert all(row.user_id == case.user_id for row in audits)
        summary = [row for row in audits if row.action == f"batch_{action}"]
        assert len(summary) == 1
        assert summary[0].details["count"] == len(expected)
        assert set(summary[0].details["fix_ids"]) == expected_ids
        assert len(audits) == len(expected) + 1


@pytest.mark.parametrize(
    "body,expected_status",
    [
        ({"action": "unknown"}, 422),
        ({"action": "edit"}, 400),
        ({"action": "edit", "edited_content": ""}, 400),
    ],
)
def test_invalid_single_review_leaves_fix_and_audit_unchanged(
    review_http, body, expected_status
):
    case = review_http
    response = _fix_request(case, body)
    assert response.status_code == expected_status
    with case.factory() as db:
        fix = db.get(ScanFix, case.fix_ids["high"])
        assert fix.review_status == "pending"
        assert fix.fixed_content == "Proposed heading"
        assert fix.reviewed_at is None
        assert _audits(db, case) == []


@pytest.mark.parametrize("missing", ["scan", "fix"])
def test_missing_review_resource_has_no_mutation(review_http, missing):
    case = review_http
    scan_id = str(uuid.uuid4()) if missing == "scan" else case.scan_id
    fix_id = str(uuid.uuid4()) if missing == "fix" else case.fix_ids["high"]
    response = case.client.post(
        f"/api/reviews/{scan_id}/fixes/{fix_id}", json={"action": "approve"}
    )
    assert response.status_code == 404
    assert response.json() == {
        "detail": "Scan not found" if missing == "scan" else "Fix not found"
    }
    with case.factory() as db:
        assert db.get(ScanFix, case.fix_ids["high"]).review_status == "pending"
        assert _audits(db, case) == []


@pytest.mark.parametrize("operation", ["single", "batch"])
def test_commit_failure_rolls_back_flushed_reviews_and_audits(review_http, operation):
    case = review_http
    flushed = []

    def fail_commit(session):
        session.flush()
        flushed.append(len(_audits(session, case)))
        raise RuntimeError("Synthetic commit failure")

    event.listen(case.factory, "before_commit", fail_commit)
    try:
        if operation == "single":
            response = _fix_request(case, {"action": "approve"})
        else:
            response = case.client.post(f"{case.url}/batch", json={"action": "approve"})
    finally:
        event.remove(case.factory, "before_commit", fail_commit)
    assert response.status_code == 500
    assert flushed == [1 if operation == "single" else 5]
    with case.factory() as db:
        for name, fix_id in case.fix_ids.items():
            fix = db.get(ScanFix, fix_id)
            assert fix.review_status == (
                "approved" if name == "terminal" else "pending"
            )
            assert fix.reviewed_by is None
        assert _audits(db, case) == []
    # The real dependency released the failed request; subsequent work succeeds.
    assert _fix_request(case, {"action": "approve"}).status_code == 200


@pytest.mark.parametrize("operation", ["single", "batch"])
def test_review_change_invalidates_current_artifact_approval(review_http, operation):
    case = review_http
    assert (
        case.client.post(f"{case.url}/batch", json={"action": "approve"}).status_code
        == 200
    )
    artifact_id = str(uuid.uuid4())
    checksum = hashlib.sha256(b"synthetic document metadata").hexdigest()
    with case.factory() as db:
        fixes = db.query(ScanFix).filter(ScanFix.scan_id == case.scan_id).all()
        digest = artifact_approval_review_digest(checksum, fixes)
        assert digest is not None
        # These routes invalidate persisted approval metadata without opening
        # artifact bytes; file verification belongs to the artifact route suite.
        db.add(
            RemediationArtifact(
                id=artifact_id,
                department_id=case.department_id,
                scan_id=case.scan_id,
                provider="local",
                scan_type="WORD",
                storage_backend="local",
                storage_key=f"{case.department_id}/{case.scan_id}/{artifact_id}/output.docx",
                filename="output.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=27,
                sha256=checksum,
                lifecycle_status="available",
                review_status="approved",
                approval_checksum=checksum,
                approval_review_digest=digest,
                approved_by_id=case.user_id,
                approved_by_ref=f"session:{case.user_id}",
                approved_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            )
        )
        db.flush()
        db.get(Scan, case.scan_id).current_remediation_artifact_id = artifact_id
        if operation == "batch":
            fix = db.get(ScanFix, case.fix_ids["high"])
            fix.review_status = "pending"
            fix.approved_review_digest = None
        db.commit()
    if operation == "single":
        response = _fix_request(case, {"action": "reject"})
    else:
        response = case.client.post(
            f"{case.url}/batch",
            json={"action": "reject", "fix_ids": [case.fix_ids["high"]]},
        )
    assert response.status_code == 200
    with case.factory() as db:
        artifact = db.get(RemediationArtifact, artifact_id)
        assert artifact.review_status == "pending"
        assert artifact.approval_checksum is None
        assert artifact.approval_review_digest is None
        assert artifact.approved_by_id is None
        assert artifact.approved_by_ref is None
        assert artifact.approved_at is None
        assert db.get(ScanFix, case.fix_ids["high"]).review_status == "rejected"
        invalidations = [
            row
            for row in _audits(db, case)
            if row.action == "artifact_approval_invalidated"
        ]
        assert len(invalidations) == 1
        assert invalidations[0].details == {
            "artifact_id": artifact_id,
            "reason": "fix_review_changed",
        }
