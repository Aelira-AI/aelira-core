"""Admin user and statistics HTTP contracts against disposable PostgreSQL."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

import admin_route_fixtures as fixtures
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import (
    APIKey,
    AuditLog,
    AuditLogAction,
    AuditLogStatus,
    InvitationStatus,
    Scan,
    ScanResult,
    ScanStatus,
    ScanType,
    User,
    UserInvitation,
    UserRole,
)

pytestmark = pytest.mark.integration
admin_route = fixtures.admin_route
BASE = "/admin"


def reload_user(case, user_id):
    case.db.expire_all()
    return case.db.get(User, user_id)


def add_user(case, *, role=UserRole.FACULTY, active=True):
    user = User(
        id=str(uuid4()),
        email=f"member-{uuid4()}@example.edu",
        name="Fixture Member",
        department_id=case.department.id,
        role=role,
        is_active=active,
    )
    case.db.add(user)
    case.db.flush()
    return user


def add_scan(case, user, *, created_at=None, document_id=None, score=None):
    scan = Scan(
        id=str(uuid4()),
        user_id=user.id,
        department_id=user.department_id,
        scan_type=ScanType.PDF,
        status=ScanStatus.COMPLETED if score is not None else ScanStatus.PENDING,
        file_name="contract.pdf",
        document_id=document_id or str(uuid4()),
        created_at=created_at or datetime.now(timezone.utc),
    )
    case.db.add(scan)
    case.db.flush()
    if score is not None:
        case.db.add(
            ScanResult(
                scan_id=scan.id,
                compliance_score=score,
                high_issues=2,
                low_issues=1,
            )
        )
        case.db.flush()
    return scan


def test_users_list_serializes_active_department_users_and_scan_counts(admin_route):
    case = admin_route
    inactive = add_user(case, active=False)
    case.faculty.picture_url = "https://example.edu/faculty.png"
    case.faculty.last_login_at = datetime(2026, 1, 15, tzinfo=timezone.utc)
    add_scan(case, case.faculty)
    add_scan(case, case.faculty)
    add_scan(case, case.other_user)
    case.db.commit()
    response = case.client.get(f"{BASE}/users")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"success", "department_id", "users", "count"}
    assert data["success"] is True
    assert data["department_id"] == case.department.id
    assert data["count"] == 2
    rows = {row["id"]: row for row in data["users"]}
    assert set(rows) == {case.admin.id, case.faculty.id}
    assert inactive.id not in rows and case.other_user.id not in rows
    assert rows[case.admin.id]["scan_count"] == 0
    row = rows[case.faculty.id]
    assert set(row) == {
        "id",
        "email",
        "name",
        "picture_url",
        "role",
        "created_at",
        "last_login_at",
        "scan_count",
    }
    assert row["email"] == case.faculty.email
    assert row["name"] == case.faculty.name
    assert row["picture_url"] == case.faculty.picture_url
    assert row["role"] == "faculty"
    assert row["scan_count"] == 2
    assert datetime.fromisoformat(row["created_at"]) == case.faculty.created_at
    assert datetime.fromisoformat(row["last_login_at"]) == case.faculty.last_login_at


def test_role_update_persists_normalized_role_and_transactional_audit(admin_route):
    case = admin_route
    target_id = case.faculty.id
    response = case.client.patch(
        f"{BASE}/users/{target_id}/role", json={"role": "ADMIN"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "user_id": target_id,
        "email": case.faculty.email,
        "old_role": "faculty",
        "new_role": "admin",
    }
    assert reload_user(case, target_id).role == UserRole.ADMIN
    event = (
        case.db.query(AuditLog)
        .filter_by(
            department_id=case.department.id,
            action=AuditLogAction.USER_ROLE_CHANGE.value,
            resource_id=target_id,
        )
        .one()
    )
    assert event.user_id == case.admin.id
    assert event.resource_type == "user"
    assert event.status == AuditLogStatus.SUCCESS.value
    assert event.details == {"old_role": "faculty", "new_role": "admin"}


@pytest.mark.parametrize(
    "payload,status", [({"role": "unsupported"}, 400), ({}, 422), ({"role": None}, 422)]
)
def test_role_update_rejects_invalid_input_without_mutation(
    admin_route, payload, status
):
    case = admin_route
    target_id = case.faculty.id
    response = case.client.patch(f"{BASE}/users/{target_id}/role", json=payload)
    assert response.status_code == status
    assert reload_user(case, target_id).role == UserRole.FACULTY


@pytest.mark.parametrize("target", ["other_department", "missing", "inactive"])
def test_role_update_requires_active_department_member(admin_route, target):
    case = admin_route
    if target == "other_department":
        target_id = case.other_user.id
    elif target == "inactive":
        target_id = add_user(case, active=False).id
        case.db.commit()
    else:
        target_id = str(uuid4())
    response = case.client.patch(
        f"{BASE}/users/{target_id}/role", json={"role": "admin"}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}
    user = reload_user(case, target_id)
    if target == "missing":
        assert user is None
    else:
        assert user.role == UserRole.FACULTY


def test_role_update_commit_failure_rolls_back_role_and_audit(admin_route):
    """Only commit is fault-injected; assertions reload actual PostgreSQL rows."""
    case = admin_route
    target_id = case.faculty.id
    with patch.object(
        case.db, "commit", side_effect=SQLAlchemyError("injected commit failure")
    ):
        response = case.client.patch(
            f"{BASE}/users/{target_id}/role", json={"role": "admin"}
        )
    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to update user role"}
    assert reload_user(case, target_id).role == UserRole.FACULTY
    assert case.db.query(AuditLog).filter_by(resource_id=target_id).count() == 0


def test_delete_deactivates_user_and_preserves_account_record(admin_route):
    case = admin_route
    target_id = case.faculty.id
    case.faculty.lti_reauthorization_required = True
    case.db.commit()
    before = datetime.now(timezone.utc)
    response = case.client.delete(f"{BASE}/users/{target_id}")
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": f"User {case.faculty.email} has been removed",
    }
    user = reload_user(case, target_id)
    assert user.is_active is False
    assert user.lti_reauthorization_required is False
    assert before <= user.deactivated_at <= datetime.now(timezone.utc)
    assert user.role == UserRole.FACULTY
    listed = case.client.get(f"{BASE}/users").json()
    assert {row["id"] for row in listed["users"]} == {case.admin.id}


def test_delete_commit_failure_preserves_active_account(admin_route):
    """The commit boundary is fault-injected; account state reloads from PostgreSQL."""
    case = admin_route
    target_id = case.faculty.id
    case.faculty.lti_reauthorization_required = True
    case.db.commit()
    with patch.object(
        case.db, "commit", side_effect=SQLAlchemyError("injected commit failure")
    ):
        response = case.client.delete(f"{BASE}/users/{target_id}")
    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to remove user"}
    user = reload_user(case, target_id)
    assert user.is_active is True
    assert user.deactivated_at is None
    assert user.lti_reauthorization_required is True


@pytest.mark.parametrize(
    "target,status,detail",
    [
        ("self", 400, "Cannot remove yourself"),
        ("other_department", 404, "User not found"),
        ("missing", 404, "User not found"),
        ("admin", 403, "Only super admins can remove admins"),
    ],
)
def test_delete_conflicts_preserve_accounts(admin_route, target, status, detail):
    case = admin_route
    target_id = {
        "self": case.admin.id,
        "other_department": case.other_user.id,
        "missing": str(uuid4()),
    }.get(target)
    if target == "admin":
        target_id = add_user(case, role=UserRole.ADMIN).id
        case.db.commit()
    response = case.client.delete(f"{BASE}/users/{target_id}")
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    user = reload_user(case, target_id)
    if target == "missing":
        assert user is None
    else:
        assert user.is_active is True
        assert user.deactivated_at is None


def test_stats_without_scans_preserves_unknown_compliance_score(admin_route):
    case = admin_route
    response = case.client.get(f"{BASE}/stats")
    assert response.status_code == 200
    assert response.json()["stats"] == {
        "total_users": 2,
        "active_users": 0,
        "total_scans": 0,
        "historical_scan_count": 0,
        "enrolled_document_count": 0,
        "verified_document_count": 0,
        "unverified_document_count": 0,
        "scans_this_month": 0,
        "avg_compliance_score": None,
        "total_issues": 0,
        "pending_invitations": 0,
    }


@pytest.mark.parametrize(
    "path,detail",
    [
        ("users", "Unable to list users"),
        ("stats", "Unable to get department stats"),
    ],
)
def test_read_database_failure_returns_stable_error(admin_route, path, detail):
    """Query failure is substituted after proving the same real route succeeds."""
    case = admin_route
    assert case.client.get(f"{BASE}/{path}").status_code == 200
    with patch.object(
        case.db,
        "query",
        side_effect=SQLAlchemyError("fixture-only internal database error"),
    ):
        response = case.client.get(f"{BASE}/{path}")
    assert response.status_code == 500
    assert response.json() == {"detail": detail}
    assert case.client.get(f"{BASE}/{path}").status_code == 200


def test_stats_aggregates_real_current_documents_and_department_counts(admin_route):
    """ScanService and compliance projection are real; no stats service stub."""
    case = admin_route
    now = datetime.now(timezone.utc)
    case.admin.last_login_at = now
    case.faculty.last_login_at = now - timedelta(days=40)
    inactive = add_user(case, active=False)
    inactive.last_login_at = now
    document_id = str(uuid4())
    add_scan(
        case,
        case.faculty,
        created_at=now - timedelta(days=65),
        document_id=document_id,
        score=10,
    )
    add_scan(case, case.faculty, created_at=now, document_id=document_id, score=80)
    add_scan(case, case.faculty, created_at=now, score=100)
    add_scan(case, case.faculty, created_at=now)
    add_scan(case, case.other_user, created_at=now, score=1)
    for department_id, status in [
        (case.department.id, InvitationStatus.PENDING),
        (case.department.id, InvitationStatus.ACCEPTED),
        (case.other_department.id, InvitationStatus.PENDING),
    ]:
        case.db.add(
            UserInvitation(
                department_id=department_id,
                email=f"invite-{uuid4()}@example.edu",
                role=UserRole.FACULTY,
                token=str(uuid4()),
                invited_by=case.admin.id,
                status=status,
                expires_at=now + timedelta(days=7),
            )
        )
    case.db.commit()
    response = case.client.get(f"{BASE}/stats")
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "department_id": case.department.id,
        "department_name": case.department.name,
        "institution": case.department.institution,
        "tier": case.department.tier,
        "max_users": case.department.max_users,
        "stats": {
            "total_users": 2,
            "active_users": 1,
            "total_scans": 4,
            "historical_scan_count": 4,
            "enrolled_document_count": 3,
            "verified_document_count": 2,
            "unverified_document_count": 1,
            "scans_this_month": 3,
            "avg_compliance_score": 90.0,
            "total_issues": 6,
            "pending_invitations": 1,
        },
    }


@pytest.mark.parametrize(
    "identity", ["faculty_session", "lti_instructor", "lti_administrator"]
)
@pytest.mark.parametrize(
    "operation",
    ["list", "role", "delete", "stats", "invite", "invitations", "revoke", "resend"],
)
def test_admin_routes_reject_non_admitted_principals(admin_route, identity, operation):
    """Supported principal objects exercise the real guard; authentication is substituted."""
    case = admin_route
    kwargs = dict(
        api_key=None,
        user_id=case.faculty.id,
        department_id=case.department.id,
        user_role=UserRole.FACULTY,
        auth_method="session",
    )
    if identity == "lti_instructor":
        kwargs.update(
            auth_method="lti",
            lti_staff_role="Instructor",
            lti_course_id="course-fixture",
        )
    elif identity == "lti_administrator":
        kwargs.update(
            user_id=case.admin.id,
            user_role=UserRole.ADMIN,
            auth_method="lti",
            lti_staff_role="Administrator",
            lti_account_wide=True,
        )
    case.principal = AuthenticatedPrincipal(**kwargs)
    target_id = case.faculty.id
    if operation == "role":
        response = case.client.patch(
            f"{BASE}/users/{target_id}/role", json={"role": "admin"}
        )
    elif operation == "delete":
        response = case.client.delete(f"{BASE}/users/{target_id}")
    elif operation == "invite":
        response = case.client.post(
            f"{BASE}/users/invite", json={"email": "faculty-invite@example.edu"}
        )
    elif operation == "invitations":
        response = case.client.get(f"{BASE}/invitations")
    elif operation == "revoke":
        response = case.client.delete(f"{BASE}/invitations/fixture-invitation")
    elif operation == "resend":
        response = case.client.post(f"{BASE}/invitations/fixture-invitation/resend")
    else:
        response = case.client.get(
            f"{BASE}/{'users' if operation == 'list' else 'stats'}"
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "Admin access required"}
    user = reload_user(case, target_id)
    assert user.role == UserRole.FACULTY and user.is_active is True
    case.mail.send_faculty_invitation.assert_not_called()


def test_admin_api_key_principal_is_admitted_by_real_guard(admin_route):
    """Identity substitution does not validate API key parsing or verification."""
    case = admin_route
    key = APIKey(
        user_id=case.admin.id,
        department_id=case.department.id,
        key_hash=f"fixture-only-{uuid4()}",
        key_prefix="fixture-only",
    )
    case.db.add(key)
    case.db.commit()
    case.principal = replace(case.principal, api_key=key, auth_method="api_key")
    response = case.client.get(f"{BASE}/users")
    assert response.status_code == 200
    assert response.json()["department_id"] == case.department.id
