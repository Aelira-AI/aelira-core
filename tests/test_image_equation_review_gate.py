"""Durable image-equation evidence and mandatory human-review gate."""

from datetime import datetime, timezone
import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import Column, MetaData, String, Table, create_engine, select
from sqlalchemy.orm import Session

from src.education.remediation.base import FixedIssue, IssueCategory, IssueSeverity

HASH = "a" * 64
EVIDENCE = {
    "passed": True,
    "source_sha256": HASH,
    "rendered_sha256": "b" * 64,
    "mathml_sha256": "c" * 64,
    "renderer_version": "chromium-1",
    "comparator_version": "pixel-v1",
    "font_sha256": "d" * 64,
    "threshold_version": "printed-equation-v1",
    "ink_iou": 0.99,
    "pixel_similarity": 0.99,
    "required_ink_iou": 0.90,
    "required_pixel_similarity": 0.98,
}


def _fix(**overrides):
    values = {
        "issue_id": "equation-1",
        "category": IssueCategory.STRUCTURE,
        "severity": IssueSeverity.HIGH,
        "description": "Image equation requires accessible math",
        "fixed_content": "x squared",
        "fix_method": "ai_vision",
        "confidence": 0.55,
        "needs_review": True,
        "provider_used": "gemini",
        "model_used": "vision-model",
        "source_kind": "image_equation",
        "verification_evidence": EVIDENCE,
    }
    values.update(overrides)
    return FixedIssue(**values)


def test_fixed_issue_accepts_only_bounded_allowlisted_evidence():
    fix = _fix()

    assert fix.source_kind == "image_equation"
    assert fix.provider_used == "gemini"
    assert fix.verification_evidence.model_dump() == EVIDENCE

    with pytest.raises(ValidationError):
        _fix(source_kind="x" * 33)
    with pytest.raises(ValidationError):
        _fix(verification_evidence={**EVIDENCE, "provider_payload": "secret"})
    with pytest.raises(ValidationError):
        _fix(verification_evidence={**EVIDENCE, "source_sha256": "not-a-hash"})
    with pytest.raises(ValidationError):
        _fix(confidence=math.nan)

    from src.services.scan_fix_service import valid_image_equation_evidence

    assert not valid_image_equation_evidence(
        {
            **EVIDENCE,
            "threshold_version": "attacker-v1",
            "required_ink_iou": 0.0,
            "required_pixel_similarity": 0.0,
        }
    )


def test_shared_persistence_forces_image_equation_pending_and_preserves_evidence():
    from src.services.scan_fix_service import build_scan_fix

    forged = _fix(
        fix_method="rule",
        confidence=1.0,
        needs_review=False,
    )

    row = build_scan_fix("scan-1", forged)

    assert row.fix_method == "ai_vision"
    assert row.confidence == 0.55
    assert row.needs_review is True
    assert row.review_status == "pending"
    assert row.reviewed_by is None
    assert row.reviewed_at is None
    assert row.provider_used == "gemini"
    assert row.model_used == "vision-model"
    assert row.source_kind == "image_equation"
    assert row.verification_evidence == EVIDENCE
    assert (
        "\x00"
        not in build_scan_fix(
            "scan-1", _fix(description="safe\x00description")
        ).description
    )

    nan_forgery = _fix().model_copy(update={"confidence": math.nan})
    with pytest.raises(ValueError):
        build_scan_fix("scan-1", nan_forgery)


def test_review_gate_requires_exact_human_approval_for_every_image_equation_fix():
    from src.services.scan_fix_service import image_equation_review_blockers

    approved = SimpleNamespace(
        source_kind="image_equation",
        fix_method="ai_vision",
        confidence=0.55,
        needs_review=True,
        provider_used="gemini",
        model_used="vision-model",
        review_status="approved",
        reviewed_by="user-1",
        reviewed_at=datetime.now(timezone.utc),
        verification_evidence=EVIDENCE,
    )
    assert image_equation_review_blockers([approved]) == []

    for mutation in (
        {"review_status": "auto_approved"},
        {"review_status": "pending"},
        {"review_status": "rejected"},
        {"reviewed_by": None},
        {"reviewed_at": None},
        {"fix_method": "rule"},
        {"confidence": 1.0},
        {"confidence": math.nan},
        {"needs_review": False},
        {"provider_used": None},
        {"model_used": None},
        {"verification_evidence": {**EVIDENCE, "passed": False}},
        {"verification_evidence": {**EVIDENCE, "source_sha256": "forged"}},
    ):
        candidate = SimpleNamespace(**{**approved.__dict__, **mutation})
        assert image_equation_review_blockers([candidate])

    mixed = SimpleNamespace(**{**approved.__dict__, "review_status": "auto_approved"})
    assert image_equation_review_blockers([approved, mixed])


def test_image_equation_review_cannot_edit_stale_artifact_metadata():
    from src.services.scan_fix_service import validate_fix_review_action

    image_fix = SimpleNamespace(source_kind="image_equation")
    with pytest.raises(ValueError, match="cannot be edited"):
        validate_fix_review_action(image_fix, "edit")
    validate_fix_review_action(image_fix, "approve")


def test_artifact_service_and_metadata_share_image_equation_blockers():
    from src.api.education.remediation_routes import _artifact_review_blockers
    from src.db.models import RemediationOutcome, Scan, ScanFix, ScanStatus
    from src.services.remediation_artifact_service import (
        ArtifactAuthorizationError,
        RemediationArtifactService,
    )

    scan = SimpleNamespace(
        id="scan-1",
        status=ScanStatus.COMPLETED,
        remediation_outcome=RemediationOutcome.COMPLETED.value,
    )
    artifact = SimpleNamespace(scan_id=scan.id, review_status="pending")
    forged = SimpleNamespace(
        source_kind="image_equation",
        review_status="auto_approved",
        reviewed_by=None,
        reviewed_at=None,
        verification_evidence=EVIDENCE,
    )
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.with_for_update.return_value = chain
        chain.populate_existing.return_value = chain
        if model is Scan:
            chain.one_or_none.return_value = scan
        elif model is ScanFix:
            chain.all.return_value = [forged]
        return chain

    db.query.side_effect = query
    blockers = _artifact_review_blockers(db, scan, artifact)
    assert "image_equation_not_human_approved" in blockers

    service = RemediationArtifactService.__new__(RemediationArtifactService)
    with pytest.raises(ArtifactAuthorizationError, match="human review"):
        service._require_approvable_review(db, artifact)


def test_authenticated_batch_review_records_actor_time_and_each_fix():
    from src.db.models import ReviewAuditLog
    from src.services.scan_fix_service import apply_authenticated_batch_review

    first = SimpleNamespace(id="fix-1", review_status="pending")
    second = SimpleNamespace(id="fix-2", review_status="pending")
    db = MagicMock()
    now = datetime.now(timezone.utc)

    apply_authenticated_batch_review(
        db,
        scan_id="scan-1",
        fixes=[first, second],
        action="approve",
        user_id="user-1",
        reviewed_at=now,
        notes="checked",
    )

    assert first.review_status == second.review_status == "approved"
    assert first.reviewed_by == second.reviewed_by == "user-1"
    assert first.reviewed_at == second.reviewed_at == now
    records = [call.args[0] for call in db.add.call_args_list]
    assert len(records) == 2
    assert all(isinstance(record, ReviewAuditLog) for record in records)
    assert {record.fix_id for record in records} == {"fix-1", "fix-2"}
    assert all(record.user_id == "user-1" for record in records)


def test_durable_evidence_and_per_fix_audit_survive_session_restart():
    from src.db.models import ReviewAuditLog, ScanFix
    from src.services.scan_fix_service import (
        apply_authenticated_batch_review,
        image_equation_review_blockers,
        persist_scan_fixes,
    )

    engine = create_engine("sqlite://")
    metadata = MetaData()
    Table("scans", metadata, Column("id", String(36), primary_key=True))
    Table("users", metadata, Column("id", String(36), primary_key=True))
    ScanFix.__table__.to_metadata(metadata)
    ReviewAuditLog.__table__.to_metadata(metadata)
    metadata.create_all(engine)

    with Session(engine) as session:
        row = persist_scan_fixes(session, "scan-1", [_fix()])[0]
        row_id = row.id
        session.commit()

    with Session(engine) as session:
        row = session.get(ScanFix, row_id)
        assert row is not None
        assert row.verification_evidence == EVIDENCE
        assert image_equation_review_blockers([row])
        apply_authenticated_batch_review(
            session,
            scan_id="scan-1",
            fixes=[row],
            action="approve",
            user_id="user-1",
            reviewed_at=datetime.now(timezone.utc),
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(ScanFix, row_id)
        assert row is not None
        assert image_equation_review_blockers([row]) == []
        audits = session.scalars(select(ReviewAuditLog)).all()
        assert len(audits) == 1
        assert audits[0].fix_id == row_id
        assert audits[0].user_id == "user-1"

        retried = persist_scan_fixes(session, "scan-1", [_fix()])[0]
        session.flush()
        assert retried.id == row_id
        assert retried.review_status == "approved"
        assert retried.reviewed_by == "user-1"
        assert session.scalars(select(ReviewAuditLog)).all()[0].fix_id == row_id

        changed = persist_scan_fixes(
            session, "scan-1", [_fix(fixed_content="changed equation text")]
        )[0]
        session.flush()
        assert changed.id == row_id
        assert changed.review_status == "pending"
        assert changed.reviewed_by is None
        assert "fix_replaced" in {
            audit.action for audit in session.scalars(select(ReviewAuditLog)).all()
        }


def test_direct_and_queued_paths_use_shared_persistence_service():
    from src.api.education.remediation_routes import remediate_scan
    from src.jobs.remediation_job import process_remediation_job

    assert "persist_scan_fixes" in remediate_scan.__code__.co_names
    assert "persist_scan_fixes" in process_remediation_job.__code__.co_names


def test_generic_fixes_keep_existing_auto_approval_behavior():
    from src.services.scan_fix_service import (
        build_scan_fix,
        image_equation_review_blockers,
    )

    generic = FixedIssue(
        issue_id="language-1",
        category=IssueCategory.LANGUAGE,
        severity=IssueSeverity.LOW,
        description="Missing language",
        fixed_content="en-AU",
        fix_method="rule",
    )
    row = build_scan_fix("scan-1", generic)

    assert row.review_status == "auto_approved"
    assert image_equation_review_blockers([row]) == []
