"""Tenant ownership contracts for issue collaboration mutations."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.db.models import AuditLog, IssueStatus
from src.education.issue_tracking_service import IssueTrackingService

DEPARTMENT = "department-one"
OTHER_DEPARTMENT = "department-two"


def _issue(issue_id="issue-one", *, department_id=DEPARTMENT):
    return SimpleNamespace(
        id=issue_id,
        department_id=department_id,
        status=IssueStatus.OPEN,
        updated_at=None,
        resolved_by=None,
        resolved_at=None,
        resolution_notes=None,
        resolution_method=None,
        assigned_to=None,
        assigned_by=None,
        assigned_at=None,
        notes=None,
    )


def _query_returning(value):
    query = MagicMock()
    query.filter.return_value.first.return_value = value
    return query


def _filter_text(query):
    return " ".join(str(expression) for expression in query.filter.call_args.args)


def test_status_update_queries_issue_inside_department():
    issue = _issue()
    issue_query = _query_returning(issue)
    user_query = _query_returning(SimpleNamespace(id="actor-one"))
    db = MagicMock()
    db.query.side_effect = [issue_query, user_query]

    IssueTrackingService.update_issue_status(
        db,
        issue_id=issue.id,
        department_id=DEPARTMENT,
        new_status="resolved",
        user_id="actor-one",
    )

    assert "issue_tracking.id" in _filter_text(issue_query)
    assert "issue_tracking.department_id" in _filter_text(issue_query)
    assert "users.department_id" in _filter_text(user_query)
    assert any(isinstance(call.args[0], AuditLog) for call in db.add.call_args_list)
    db.commit.assert_called_once()


def test_cross_department_status_update_has_no_side_effect():
    db = MagicMock()
    db.query.return_value = _query_returning(None)

    with pytest.raises(ValueError, match="Issue not found"):
        IssueTrackingService.update_issue_status(
            db,
            issue_id="other-issue",
            department_id=DEPARTMENT,
            new_status="resolved",
            user_id="actor-one",
        )

    db.commit.assert_not_called()


def test_assignment_rejects_user_outside_department_without_mutation():
    issue = _issue()
    issue_query = _query_returning(issue)
    user_query = _query_returning(None)
    db = MagicMock()
    db.query.side_effect = [issue_query, user_query]

    with pytest.raises(ValueError, match="Assignee not found"):
        IssueTrackingService.assign_issue(
            db,
            issue_id=issue.id,
            department_id=DEPARTMENT,
            assigned_to="other-user",
            assigned_by="actor-one",
        )

    assert issue.assigned_to is None
    assert "users.department_id" in _filter_text(user_query)
    db.commit.assert_not_called()


def test_note_scopes_issue_and_actor_to_department():
    issue = _issue()
    issue_query = _query_returning(issue)
    user_query = _query_returning(SimpleNamespace(name="Member"))
    db = MagicMock()
    db.query.side_effect = [issue_query, user_query]

    IssueTrackingService.add_issue_note(
        db,
        issue_id=issue.id,
        department_id=DEPARTMENT,
        note="Reviewed",
        user_id="actor-one",
    )

    assert "issue_tracking.department_id" in _filter_text(issue_query)
    assert "users.department_id" in _filter_text(user_query)
    assert "Member: Reviewed" in issue.notes
    db.commit.assert_called_once()


def test_auto_fix_mutation_queries_issue_inside_department():
    issue = _issue()
    query = _query_returning(issue)
    db = MagicMock()
    db.query.return_value = query

    IssueTrackingService.mark_auto_fixed(
        db,
        issue_id=issue.id,
        department_id=DEPARTMENT,
        fix_result='{"verified": true}',
    )

    assert "issue_tracking.department_id" in _filter_text(query)
    assert issue.auto_fix_applied is True
    db.commit.assert_called_once()


def test_mixed_department_bulk_update_is_atomic():
    count_query = MagicMock()
    count_query.filter.return_value.scalar.return_value = 1
    db = MagicMock()
    db.query.return_value = count_query

    with pytest.raises(ValueError, match="One or more issues not found"):
        IssueTrackingService.bulk_update_status(
            db,
            issue_ids=["issue-one", "issue-other"],
            department_id=DEPARTMENT,
            new_status="resolved",
            user_id="actor-one",
        )

    count_query.filter.return_value.update.assert_not_called()
    db.commit.assert_not_called()


def test_bulk_update_deduplicates_ids_and_scopes_update():
    user_query = _query_returning(SimpleNamespace(id="actor-one"))
    count_query = MagicMock()
    count_query.filter.return_value.scalar.return_value = 2
    update_query = MagicMock()
    update_query.filter.return_value.update.return_value = 2
    db = MagicMock()
    db.query.side_effect = [user_query, count_query, update_query]

    count = IssueTrackingService.bulk_update_status(
        db,
        issue_ids=["issue-one", "issue-two", "issue-one"],
        department_id=DEPARTMENT,
        new_status="resolved",
        user_id="actor-one",
    )

    assert count == 2
    update_filter = " ".join(
        str(expression) for expression in update_query.filter.call_args.args
    )
    assert "issue_tracking.department_id" in update_filter
    db.commit.assert_called_once()


def test_bulk_update_rolls_back_if_concurrent_change_reduces_update_count():
    user_query = _query_returning(SimpleNamespace(id="actor-one"))
    count_query = MagicMock()
    count_query.filter.return_value.scalar.return_value = 2
    update_query = MagicMock()
    update_query.filter.return_value.update.return_value = 1
    db = MagicMock()
    db.query.side_effect = [user_query, count_query, update_query]

    with pytest.raises(ValueError, match="One or more issues not found"):
        IssueTrackingService.bulk_update_status(
            db,
            issue_ids=["issue-one", "issue-two"],
            department_id=DEPARTMENT,
            new_status="resolved",
            user_id="actor-one",
        )

    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_status_update_rejects_actor_outside_department():
    issue = _issue()
    db = MagicMock()
    db.query.side_effect = [_query_returning(issue), _query_returning(None)]

    with pytest.raises(ValueError, match="User not found"):
        IssueTrackingService.update_issue_status(
            db,
            issue_id=issue.id,
            department_id=DEPARTMENT,
            new_status="resolved",
            user_id="foreign-actor",
        )

    assert issue.status == IssueStatus.OPEN
    db.commit.assert_not_called()


def test_assignment_rejects_assigner_outside_department():
    issue = _issue()
    db = MagicMock()
    db.query.side_effect = [
        _query_returning(issue),
        _query_returning(SimpleNamespace(id="member-one")),
        _query_returning(None),
    ]

    with pytest.raises(ValueError, match="Assignee not found"):
        IssueTrackingService.assign_issue(
            db,
            issue_id=issue.id,
            department_id=DEPARTMENT,
            assigned_to="member-one",
            assigned_by="foreign-actor",
        )

    assert issue.assigned_to is None
    db.commit.assert_not_called()


def test_create_issue_rejects_scan_outside_department():
    db = MagicMock()
    db.query.return_value = _query_returning(None)

    with pytest.raises(ValueError, match="Scan not found"):
        IssueTrackingService.create_tracked_issue(
            db,
            scan_id="foreign-scan",
            department_id=DEPARTMENT,
            issue_type="missing_alt",
            severity="high",
            description="Image lacks alternative text",
        )

    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_create_issue_scopes_existing_lookup_to_scan_department():
    scan_query = _query_returning(SimpleNamespace(id="scan-one"))
    existing_query = _query_returning(SimpleNamespace(id="issue-one"))
    db = MagicMock()
    db.query.side_effect = [scan_query, existing_query]

    issue = IssueTrackingService.create_tracked_issue(
        db,
        scan_id="scan-one",
        department_id=DEPARTMENT,
        issue_type="missing_alt",
        severity="high",
        description="Image lacks alternative text",
    )

    assert issue.id == "issue-one"
    assert "scans.department_id" in _filter_text(scan_query)
    assert "issue_tracking.department_id" in _filter_text(existing_query)
    db.commit.assert_not_called()
