"""Unreported remediation confidence stays unknown across review boundaries."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.education.remediation.base import (
    BaseRemediator,
    FixedIssue,
    IssueCategory,
    IssueSeverity,
    RemediationConfig,
    RemediationIssue,
    RemediationResult,
)
from src.services.scan_fix_service import build_scan_fix, review_digest_for


def _generic_fix(**overrides):
    return FixedIssue(
        **{
            "issue_id": "generic-fix",
            "category": IssueCategory.ALT_TEXT,
            "severity": IssueSeverity.HIGH,
            "description": "Missing description",
            "fixed_content": "A chart of enrollment",
            "fix_method": "ai_generated",
            **overrides,
        }
    )


def test_omitted_confidence_is_unknown_and_requires_review():
    fix = _generic_fix()
    assert fix.confidence is None
    assert fix.needs_review is True
    row = build_scan_fix("scan-one", fix)
    assert row.confidence is None
    assert row.needs_review is True
    assert row.review_status == "pending"
    assert row.review_digest == review_digest_for(row)
    assert row.approved_review_digest is None


def test_unknown_confidence_survives_orm_persistence_and_review_serialization():
    import sqlalchemy as sa
    from sqlalchemy.orm import Session
    from src.api.review_routes import _fix_summary
    from src.db.models import ScanFix

    metadata = sa.MetaData()
    for name in ("scans", "users"):
        sa.Table(name, metadata, sa.Column("id", sa.String, primary_key=True))
    ScanFix.__table__.to_metadata(metadata)
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with Session(engine) as db:
        row = build_scan_fix("scan-one", _generic_fix())
        db.add(row)
        db.commit()
        db.expire_all()
        stored = db.get(ScanFix, row.id)
        assert stored.confidence is None
        assert stored.review_status == "pending"
        assert _fix_summary(stored).model_dump(mode="json")["confidence"] is None
        assert review_digest_for(stored) == stored.review_digest
    engine.dispose()


def test_generic_success_does_not_invent_a_confidence_score():
    # Exercise the actual generic processing path without invoking a provider.
    remediator = MagicMock(spec=BaseRemediator)
    remediator.config = RemediationConfig()
    remediator.result = RemediationResult(
        original_file="input.html", document_type="html"
    )
    remediator._is_category_enabled.return_value = True
    remediator.can_auto_fix.return_value = True
    remediator._generate_fix.return_value = "A chart of enrollment"
    remediator.apply_fix.return_value = True
    remediator._get_fix_method.return_value = "ai_generated"
    remediator._add_fixed_issue.side_effect = (
        lambda *args, **kwargs: BaseRemediator._add_fixed_issue(
            remediator, *args, **kwargs
        )
    )
    issue = RemediationIssue(
        category=IssueCategory.ALT_TEXT,
        severity=IssueSeverity.HIGH,
        description="Missing description",
    )
    BaseRemediator._process_issue(remediator, issue, object())
    assert remediator.result.fixed_issues[0].confidence is None
    assert remediator.result.fixed_issues[0].needs_review is True


@pytest.mark.parametrize("confidence", [0.0, 0.65, 1.0])
def test_explicit_confidence_survives_without_defaulting(confidence):
    row = build_scan_fix("scan-one", _generic_fix(confidence=confidence))
    assert row.confidence == confidence


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.1, 1.1])
def test_unknown_does_not_make_invalid_numbers_valid(confidence):
    with pytest.raises(ValidationError):
        _generic_fix(confidence=confidence)


def test_unknown_visual_confidence_still_requires_evidence_and_human_approval():
    from src.services.scan_fix_service import (
        bind_fix_review_decision,
        image_equation_review_blockers,
    )
    from tests.test_image_equation_review_gate import _fix

    row = build_scan_fix("scan-one", _fix(confidence=None))
    assert row.confidence is None
    assert row.needs_review is True
    assert "image_equation_not_human_approved" in image_equation_review_blockers([row])
    bind_fix_review_decision(row, "approve")
    row.review_status = "approved"
    row.reviewed_by = "reviewer"
    row.reviewed_at = datetime.now(timezone.utc)
    assert image_equation_review_blockers([row]) == []
    row.verification_evidence = None
    assert image_equation_review_blockers([row])


def test_numeric_batch_threshold_excludes_unknown_but_includes_zero(monkeypatch):
    from src.api import review_routes as routes
    from tests.test_review_queue_contract import SESSION_PRINCIPAL

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        id="scan-one", department_id="dept-one"
    )
    fixes = [
        SimpleNamespace(id="unknown", confidence=None, review_status="pending"),
        SimpleNamespace(id="zero", confidence=0.0, review_status="pending"),
        SimpleNamespace(id="scored", confidence=0.8, review_status="pending"),
    ]
    monkeypatch.setattr(routes, "authorize_scan_access", lambda *args: None)
    monkeypatch.setattr(
        routes,
        "lock_scan_review_graph",
        lambda *args: SimpleNamespace(
            fixes=fixes,
            cloud_files=(),
            artifacts=(),
            scan=SimpleNamespace(current_remediation_artifact_id=None),
        ),
    )
    applied = MagicMock()
    monkeypatch.setattr(routes, "apply_authenticated_batch_review", applied)
    monkeypatch.setattr(routes, "_resolve_fix_deferral", lambda *args, **kwargs: None)
    response = routes.batch_review(
        "scan-one",
        routes.BatchAction(action="approve", min_confidence=0.0),
        db,
        SESSION_PRINCIPAL,
    )
    assert response.affected == 2
    assert [fix.id for fix in applied.call_args.kwargs["fixes"]] == ["zero", "scored"]


def test_audit_exports_preserve_unknown_and_zero():
    from src.education.reports.compliance_report import AuditReportGenerator
    from tests.test_audit_export import _make_department, _make_fix, _make_scan

    unknown = _make_fix(fix_id="unknown")
    unknown.confidence = None
    zero = _make_fix(fix_id="zero", confidence=0.0)
    arguments = dict(
        scan=_make_scan(),
        fixes=[unknown, zero],
        audit_entries=[],
        matterhorn_results=[],
        department=_make_department(),
    )
    report = AuditReportGenerator.generate_json(**arguments)
    assert [fix["confidence"] for fix in report["machine_observations"]] == [None, 0.0]
    assert AuditReportGenerator.generate_csv(**arguments)
    assert AuditReportGenerator.generate_pdf(**arguments).startswith(b"%PDF")
