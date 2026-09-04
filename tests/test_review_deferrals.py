"""Controlled review-deferral contract for issue #307."""

from datetime import datetime, timedelta, timezone
from inspect import signature
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.review_routes import (
    DeferralAction,
    FixAction,
    BatchAction,
    _fix_summary,
    batch_review,
    deferral_lifecycle,
    defer_fix,
    get_document_review,
    get_auth,
    review_fix,
    revoke_fix_deferral,
)
from src.db.models import ReviewAuditLog, ScanFix

FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _fix(**overrides):
    values = {
        "id": "fix-one",
        "scan_id": "scan-one",
        "review_status": "pending",
        "deferral_status": None,
        "deferral_owner": None,
        "deferral_reason": None,
        "deferral_expires_at": None,
        "deferral_created_at": None,
        "deferral_updated_at": None,
        "deferral_closed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("stored_status", "expires_at", "expected"),
    [
        (None, None, None),
        ("active", FUTURE, "active"),
        ("active", datetime(2020, 1, 1, tzinfo=timezone.utc), "expired"),
        ("revoked", FUTURE, "revoked"),
        ("resolved", FUTURE, "resolved"),
    ],
)
def test_deferral_lifecycle_is_exact(stored_status, expires_at, expected):
    assert (
        deferral_lifecycle(
            _fix(deferral_status=stored_status, deferral_expires_at=expires_at),
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        == expected
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"owner": " ", "reason": "planned work", "expires_at": FUTURE}, "owner"),
        ({"owner": "Team A", "reason": " ", "expires_at": FUTURE}, "reason"),
        (
            {
                "owner": "Team A",
                "reason": "planned work",
                "expires_at": datetime(2020, 1, 1, tzinfo=timezone.utc),
            },
            "future",
        ),
        (
            {
                "owner": "Team A",
                "reason": "planned work",
                "expires_at": datetime(2099, 1, 1),
            },
            "timezone",
        ),
    ],
)
def test_deferral_creation_requires_accountability(payload, message):
    with pytest.raises(ValidationError, match=message):
        DeferralAction(**payload)


def _route_db(*, department_id="dept-one"):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        id="scan-one", department_id=department_id
    )
    return db


def test_authorized_reviewer_creates_deferral_without_resolving_finding():
    db = _route_db()
    fix = _fix()
    graph = SimpleNamespace(fixes=[fix])

    with patch("src.api.review_routes.lock_scan_review_graph", return_value=graph):
        response = defer_fix(
            "scan-one",
            "fix-one",
            DeferralAction(
                owner="Accessibility team",
                reason="Needs source-author confirmation",
                expires_at=FUTURE,
            ),
            db=db,
            auth_result=("key-one", "user-one", "dept-one"),
        )

    assert fix.review_status == "pending"
    assert response.deferral.lifecycle == "active"
    assert response.deferral.owner == "Accessibility team"
    logs = [call.args[0] for call in db.add.call_args_list]
    audit = next(log for log in logs if isinstance(log, ReviewAuditLog))
    assert audit.action == "fix_deferral_created"
    assert audit.user_id == "user-one"
    assert audit.details["actor_id"] == "user-one"
    db.commit.assert_called_once()


def test_changing_deferral_appends_old_and_new_state():
    db = _route_db()
    fix = _fix(
        deferral_status="active",
        deferral_owner="Team A",
        deferral_reason="Original reason",
        deferral_expires_at=FUTURE - timedelta(days=1),
        deferral_created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    with patch(
        "src.api.review_routes.lock_scan_review_graph",
        return_value=SimpleNamespace(fixes=[fix]),
    ):
        defer_fix(
            "scan-one",
            "fix-one",
            DeferralAction(
                owner="Team B",
                reason="Updated reason",
                expires_at=FUTURE,
            ),
            db=db,
            auth_result=("key-one", "user-one", "dept-one"),
        )

    logs = [call.args[0] for call in db.add.call_args_list]
    audit = next(log for log in logs if isinstance(log, ReviewAuditLog))
    assert audit.action == "fix_deferral_updated"
    assert audit.details["previous"]["owner"] == "Team A"
    assert audit.details["current"]["owner"] == "Team B"


def test_revoke_preserves_expired_deferral_history():
    db = _route_db()
    fix = _fix(
        deferral_status="active",
        deferral_owner="Team A",
        deferral_reason="Awaiting replacement",
        deferral_expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        deferral_created_at=datetime(2019, 1, 1, tzinfo=timezone.utc),
    )

    with patch(
        "src.api.review_routes.lock_scan_review_graph",
        return_value=SimpleNamespace(fixes=[fix]),
    ):
        response = revoke_fix_deferral(
            "scan-one",
            "fix-one",
            db=db,
            auth_result=("key-one", "user-one", "dept-one"),
        )

    assert response.deferral.lifecycle == "revoked"
    logs = [call.args[0] for call in db.add.call_args_list]
    audit = next(log for log in logs if isinstance(log, ReviewAuditLog))
    assert audit.action == "fix_deferral_revoked"
    assert audit.details["previous"]["lifecycle"] == "expired"


@pytest.mark.parametrize("route", ["create", "revoke"])
def test_cross_tenant_deferral_writes_are_hidden(route):
    db = _route_db(department_id="dept-two")
    with pytest.raises(HTTPException) as exc:
        if route == "create":
            defer_fix(
                "scan-one",
                "fix-one",
                DeferralAction(owner="Team A", reason="Reason", expires_at=FUTURE),
                db=db,
                auth_result=("key-one", "user-one", "dept-one"),
            )
        else:
            revoke_fix_deferral(
                "scan-one",
                "fix-one",
                db=db,
                auth_result=("key-one", "user-one", "dept-one"),
            )
    assert exc.value.status_code == 404


def test_deferral_routes_use_the_canonical_review_authentication_dependency():
    for endpoint in (defer_fix, revoke_fix_deferral):
        dependency = signature(endpoint).parameters["auth_result"].default
        assert dependency.dependency is get_auth


def test_resolved_finding_cannot_be_deferred():
    db = _route_db()
    with (
        patch(
            "src.api.review_routes.lock_scan_review_graph",
            return_value=SimpleNamespace(fixes=[_fix(review_status="approved")]),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        defer_fix(
            "scan-one",
            "fix-one",
            DeferralAction(owner="Team A", reason="Reason", expires_at=FUTURE),
            db=db,
            auth_result=("key-one", "user-one", "dept-one"),
        )
    assert exc.value.status_code == 409


def test_document_read_hides_cross_tenant_deferral_data():
    db = _route_db(department_id="dept-two")
    with pytest.raises(HTTPException) as exc:
        get_document_review(
            "scan-one",
            db=db,
            auth_result=("key-one", "user-one", "dept-one"),
        )
    assert exc.value.status_code == 404


def test_fix_summary_exposes_active_deferral_without_changing_review_status():
    fix = ScanFix(
        id="fix-one",
        scan_id="scan-one",
        issue_id="issue-one",
        occurrence_key="occurrence-one",
        category="images",
        severity="serious",
        description="Missing alt text",
        fixed_content="Recorded proposal",
        fix_method="rule",
        confidence=0.7,
        needs_review=True,
        review_status="pending",
        deferral_status="active",
        deferral_owner="Accessibility team",
        deferral_reason="Awaiting source-author confirmation",
        deferral_expires_at=FUTURE,
        deferral_created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        deferral_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    summary = _fix_summary(fix).model_dump(mode="json")
    assert summary["review_status"] == "pending"
    assert summary["deferral"]["lifecycle"] == "active"


def test_individual_review_resolves_deferral_with_separate_audit_event():
    db = _route_db()
    fix = _fix(
        deferral_status="active",
        deferral_owner="Team A",
        deferral_reason="Awaiting replacement",
        deferral_expires_at=FUTURE,
        deferral_created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        deferral_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fixed_content="Recorded proposal",
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
    )
    graph = SimpleNamespace(fixes=[fix])
    with (
        patch("src.api.review_routes.lock_scan_review_graph", return_value=graph),
        patch("src.api.review_routes.validate_fix_review_action"),
        patch("src.api.review_routes.bind_fix_review_decision"),
        patch("src.api.review_routes.invalidate_current_artifact_approvals"),
    ):
        response = review_fix(
            "scan-one",
            "fix-one",
            FixAction(action="approve"),
            db=db,
            auth_result=("key-one", "user-one", "dept-one"),
        )

    assert response.review_status == "approved"
    assert fix.deferral_status == "resolved"
    actions = [
        call.args[0].action
        for call in db.add.call_args_list
        if isinstance(call.args[0], ReviewAuditLog)
    ]
    assert actions == ["fix_deferral_resolved", "fix_approve"]


def test_batch_review_resolves_each_selected_deferral():
    db = _route_db()
    fixes = [
        _fix(
            id=f"fix-{index}",
            deferral_status="active",
            deferral_owner=f"Team {index}",
            deferral_reason="Planned work",
            deferral_expires_at=FUTURE,
            deferral_created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            deferral_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.5,
            category="images",
        )
        for index in range(2)
    ]
    graph = SimpleNamespace(fixes=fixes)
    with (
        patch("src.api.review_routes.lock_scan_review_graph", return_value=graph),
        patch("src.api.review_routes.apply_authenticated_batch_review"),
        patch("src.api.review_routes.invalidate_current_artifact_approvals"),
    ):
        response = batch_review(
            "scan-one",
            BatchAction(action="approve"),
            db=db,
            auth_result=("key-one", "user-one", "dept-one"),
        )

    assert response.affected == 2
    assert [fix.deferral_status for fix in fixes] == ["resolved", "resolved"]
    actions = [
        call.args[0].action
        for call in db.add.call_args_list
        if isinstance(call.args[0], ReviewAuditLog)
    ]
    assert actions == [
        "fix_deferral_resolved",
        "fix_deferral_resolved",
        "batch_approve",
    ]
