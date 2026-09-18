"""Ordinary administrator invitation contracts with real PostgreSQL rows.

Identity is supplied before the actual administrator dependency. Mail is a
signature-checked substitute; provider acceptance and inbox receipt are not tested.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
import admin_route_fixtures as fixtures

from src.api import user_management as routes
from src.config.settings import get_settings
from src.db.models import (
    AuditLog,
    AuditLogAction,
    InvitationStatus,
    UserInvitation,
    UserRole,
)

admin_route = fixtures.admin_route
pytestmark = pytest.mark.integration


def invitation(case, *, status=InvitationStatus.PENDING, expired=False, other=False):
    row = UserInvitation(
        id=str(uuid4()),
        department_id=(case.other_department.id if other else case.department.id),
        email=f"{uuid4()}@example.edu",
        role=UserRole.FACULTY,
        token=str(uuid4()),
        invited_by=case.admin.id,
        status=status,
        expires_at=datetime.now(timezone.utc) + timedelta(days=-1 if expired else 2),
    )
    case.db.add(row)
    case.db.commit()
    return row


def audits(case, resource_id):
    case.db.expire_all()
    return case.db.query(AuditLog).filter_by(resource_id=resource_id).all()


@pytest.mark.parametrize("role", ["faculty", "admin"])
def test_invite_persists_and_sends_current_contract(admin_route, monkeypatch, role):
    case = admin_route
    monkeypatch.setattr(get_settings(), "dashboard_url", "https://campus.example.edu")
    email = f"{uuid4()}@example.edu"
    response = case.client.post(
        "/admin/users/invite", json={"email": email, "role": role}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    case.db.expire_all()
    saved = case.db.get(UserInvitation, body["invitation_id"])
    assert saved.email == body["email"] == email
    assert saved.role.value == body["role"] == role
    assert saved.department_id == case.department.id
    assert saved.invited_by == case.admin.id
    assert saved.status == InvitationStatus.PENDING
    assert (
        timedelta(days=6, hours=23)
        < saved.expires_at - datetime.now(timezone.utc)
        <= timedelta(days=7)
    )
    sender = case.mail.send_faculty_invitation
    sender.assert_awaited_once()
    args = sender.await_args.kwargs
    assert args["to_email"] == email
    assert args["role"] == role
    assert args["department_name"] == case.department.name
    assert args["inviter_name"] == case.admin.name
    assert args["inviter_email"] == case.admin.email
    link = urlparse(args["accept_url"])
    assert link.netloc == "campus.example.edu"
    assert link.path == "/accept-invitation"
    assert parse_qs(link.query)["token"] == [saved.token]
    events = audits(case, saved.id)
    assert len(events) == 1
    assert events[0].action == AuditLogAction.USER_INVITE_SENT.value
    assert events[0].status == "success"
    assert events[0].user_id == case.admin.id
    assert events[0].department_id == case.department.id
    assert saved.token not in str(events[0].details)


@pytest.mark.parametrize("failure", ["result", "exception", "factory"])
@pytest.mark.parametrize("resend", [False, True])
def test_invitation_delivery_failure_is_visible_and_retryable(
    admin_route, monkeypatch, failure, resend
):
    case = admin_route
    first = case.client.post(
        "/admin/users/invite", json={"email": f"{uuid4()}@example.edu"}
    )
    assert first.status_code == 200
    assert first.json()["success"] is True
    sender = case.mail.send_faculty_invitation
    sender.assert_awaited_once()
    if failure == "result":
        sender.return_value = {"success": False, "error": "fixture mail rejection"}
    elif failure == "exception":
        sender.side_effect = RuntimeError("fixture mail rejection")
    else:

        def unavailable():
            raise RuntimeError("fixture mail rejection")

        monkeypatch.setattr(routes, "get_email_service", unavailable)
    if resend:
        invitation_id = first.json()["invitation_id"]
        failed_email = first.json()["email"]
        response = case.client.post(f"/admin/invitations/{invitation_id}/resend")
    else:
        failed_email = f"{uuid4()}@example.edu"
        response = case.client.post("/admin/users/invite", json={"email": failed_email})
    assert response.status_code == 502
    assert "fixture mail rejection" not in response.text
    assert response.json() == {
        "detail": "Invitation saved, but email delivery failed. Refresh invitations and retry."
    }
    refreshed = case.client.get("/admin/invitations", params={"status": "pending"})
    assert refreshed.status_code == 200
    failed_id = next(
        row["id"]
        for row in refreshed.json()["invitations"]
        if row["email"] == failed_email
    )
    case.db.expire_all()
    saved = case.db.get(UserInvitation, failed_id)
    assert saved is not None
    assert saved.status == InvitationStatus.PENDING
    events = audits(case, failed_id)
    assert sum(event.status == "failure" for event in events) == 1
    assert sum(event.status == "success" for event in events) == (1 if resend else 0)
    sender.side_effect = None
    sender.return_value = {"success": True}
    monkeypatch.setattr(routes, "get_email_service", lambda: case.mail)
    recovered = case.client.post(f"/admin/invitations/{failed_id}/resend")
    assert recovered.status_code == 200
    assert recovered.json()["success"] is True


@pytest.mark.parametrize("status", [InvitationStatus.PENDING, InvitationStatus.EXPIRED])
def test_resend_rotates_persisted_invitation_and_awaits_mail(admin_route, status):
    case = admin_route
    row = invitation(case, status=status, expired=status == InvitationStatus.EXPIRED)
    old_token = row.token
    old_expiry = row.expires_at
    response = case.client.post(f"/admin/invitations/{row.id}/resend")
    assert response.status_code == 200
    assert response.json()["success"] is True
    case.db.expire_all()
    assert row.token != old_token
    assert row.expires_at > old_expiry
    assert row.status == InvitationStatus.PENDING
    case.mail.send_faculty_invitation.assert_awaited_once()
    assert parse_qs(
        urlparse(
            case.mail.send_faculty_invitation.await_args.kwargs["accept_url"]
        ).query
    )["token"] == [row.token]
    assert [event.status for event in audits(case, row.id)] == ["success"]


def test_list_filters_effective_expiry_and_department(admin_route):
    case = admin_route
    pending = invitation(case)
    expired = invitation(case, expired=True)
    foreign = invitation(case, other=True, expired=True)
    accepted = invitation(case, status=InvitationStatus.ACCEPTED)
    response = case.client.get("/admin/invitations", params={"status": "pending"})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["invitations"]] == [pending.id]
    response = case.client.get("/admin/invitations", params={"status": "expired"})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["invitations"]] == [expired.id]
    response = case.client.get("/admin/invitations")
    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert {row["id"] for row in response.json()["invitations"]} == {
        pending.id,
        expired.id,
        accepted.id,
    }
    assert all("token" not in row for row in response.json()["invitations"])
    pending_view = next(
        row for row in response.json()["invitations"] if row["id"] == pending.id
    )
    assert pending_view == {
        "id": pending.id,
        "email": pending.email,
        "role": "faculty",
        "status": "pending",
        "invited_by_name": case.admin.name,
        "created_at": pending.created_at.isoformat(),
        "expires_at": pending.expires_at.isoformat(),
        "accepted_at": None,
    }
    case.db.expire_all()
    assert expired.status == InvitationStatus.EXPIRED
    assert foreign.status == InvitationStatus.PENDING


def test_revoke_persists_and_refuses_second_revoke(admin_route):
    case = admin_route
    row = invitation(case)
    response = case.client.delete(f"/admin/invitations/{row.id}")
    assert response.status_code == 200
    assert response.json()["success"] is True
    case.db.expire_all()
    assert row.status == InvitationStatus.REVOKED
    assert row.revoked_at is not None
    assert case.client.delete(f"/admin/invitations/{row.id}").status_code == 400
    case.mail.send_faculty_invitation.assert_not_awaited()


@pytest.mark.parametrize("operation", ["revoke", "resend"])
@pytest.mark.parametrize("kind", ["missing", "other_department", "accepted", "revoked"])
def test_invitation_lifecycle_refusals(admin_route, operation, kind):
    case = admin_route
    row = (
        None
        if kind == "missing"
        else invitation(
            case,
            other=kind == "other_department",
            status={
                "accepted": InvitationStatus.ACCEPTED,
                "revoked": InvitationStatus.REVOKED,
            }.get(kind, InvitationStatus.PENDING),
        )
    )
    row_id = row.id if row else "missing"
    previous = (row.status, row.token, row.expires_at) if row else None
    response = (
        case.client.delete(f"/admin/invitations/{row_id}")
        if operation == "revoke"
        else case.client.post(f"/admin/invitations/{row_id}/resend")
    )
    assert response.status_code == (
        404 if kind in {"missing", "other_department"} else 400
    )
    if row:
        case.db.expire_all()
        assert (row.status, row.token, row.expires_at) == previous
    case.mail.send_faculty_invitation.assert_not_awaited()


@pytest.mark.parametrize(
    "kind,status",
    [
        ("email", 422),
        ("role", 400),
        ("member", 400),
        ("foreign_member", 400),
        ("pending", 400),
        ("capacity", 403),
    ],
)
def test_invite_ordinary_input_and_conflicts(admin_route, kind, status):
    case = admin_route
    payload = {"email": f"{uuid4()}@example.edu", "role": "faculty"}
    if kind == "email":
        payload["email"] = "invalid"
    elif kind == "role":
        payload["role"] = "invalid"
    elif kind == "member":
        payload["email"] = case.faculty.email
    elif kind == "foreign_member":
        payload["email"] = case.other_user.email
    elif kind == "pending":
        payload["email"] = invitation(case).email
    elif kind == "capacity":
        case.department.max_users = 1
        case.db.commit()
    before = case.db.query(UserInvitation).count()
    response = case.client.post("/admin/users/invite", json=payload)
    assert response.status_code == status
    case.db.expire_all()
    assert case.db.query(UserInvitation).count() == before
    case.mail.send_faculty_invitation.assert_not_awaited()


def test_invalid_invitation_status_is_400(admin_route):
    assert (
        admin_route.client.get(
            "/admin/invitations", params={"status": "invalid"}
        ).status_code
        == 400
    )


def test_invitation_list_database_failure_after_success(admin_route, monkeypatch):
    case = admin_route
    invitation(case)
    assert case.client.get("/admin/invitations").status_code == 200
    original_query = case.db.query

    def unavailable(*args, **kwargs):
        raise RuntimeError("fixture invitation database unavailable")

    monkeypatch.setattr(case.db, "query", unavailable)
    response = case.client.get("/admin/invitations")
    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to list invitations"}
    monkeypatch.setattr(case.db, "query", original_query)
    assert case.client.get("/admin/invitations").json()["count"] == 1


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/admin/users", None),
        ("GET", "/admin/invitations", None),
        ("GET", "/admin/stats", None),
        ("POST", "/admin/users/invite", {"email": "faculty@example.edu"}),
        ("PATCH", "/admin/users/fixture/role", {"role": "faculty"}),
        ("DELETE", "/admin/users/fixture", None),
        ("DELETE", "/admin/invitations/fixture", None),
        ("POST", "/admin/invitations/fixture/resend", None),
    ],
)
def test_admin_routes_require_authenticated_identity(
    admin_route, method, path, payload
):
    case = admin_route
    case.application.dependency_overrides.pop(routes.get_authenticated_principal)
    response = case.client.request(method, path, json=payload)
    assert response.status_code == 401
    case.mail.send_faculty_invitation.assert_not_called()


def test_expired_pending_invitations_do_not_inflate_stats(admin_route):
    case = admin_route
    invitation(case)
    invitation(case, expired=True)
    response = case.client.get("/admin/stats")
    assert response.status_code == 200
    assert response.json()["stats"]["pending_invitations"] == 1


@pytest.mark.parametrize("same_email", [False, True])
def test_expired_pending_invitation_does_not_block_new_invite(admin_route, same_email):
    case = admin_route
    expired = invitation(case, expired=True)
    case.department.max_users = 3  # Two active members, one available place.
    case.db.commit()
    response = case.client.post(
        "/admin/users/invite",
        json={"email": expired.email if same_email else f"{uuid4()}@example.edu"},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    case.db.expire_all()
    assert expired.status == InvitationStatus.EXPIRED


@pytest.mark.parametrize("operation", ["invite", "resend", "revoke"])
def test_invitation_commit_failure_preserves_previous_state(
    admin_route, monkeypatch, operation
):
    case = admin_route
    row = invitation(case)
    # Establish a working mutation path before injecting a database failure.
    good = case.client.post(
        "/admin/users/invite", json={"email": f"{uuid4()}@example.edu"}
    )
    assert good.status_code == 200
    previous = (row.status, row.token, row.expires_at)
    count = case.db.query(UserInvitation).count()
    case.mail.reset_mock()
    original_commit = case.db.commit

    def fail_commit():
        case.db.flush()
        raise RuntimeError("fixture database unavailable")

    monkeypatch.setattr(case.db, "commit", fail_commit)
    if operation == "invite":
        response = case.client.post(
            "/admin/users/invite", json={"email": f"{uuid4()}@example.edu"}
        )
    elif operation == "resend":
        response = case.client.post(f"/admin/invitations/{row.id}/resend")
    else:
        response = case.client.delete(f"/admin/invitations/{row.id}")
    assert response.status_code == 500
    assert "fixture database unavailable" not in response.text
    monkeypatch.setattr(case.db, "commit", original_commit)
    case.db.expire_all()
    assert case.db.query(UserInvitation).count() == count
    assert (row.status, row.token, row.expires_at) == previous
    case.mail.send_faculty_invitation.assert_not_awaited()
