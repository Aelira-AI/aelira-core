"""Exact authenticated account contracts; no live mail or account destruction."""

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import bcrypt
import pytest

from src.api import account_routes as routes
from src.db.models import AuditLog, DeletedEmail, Scan, ScanResult, ScanStatus, ScanType
from src.services import account_deletion_service as service_module
from tests import account_route_fixtures as fixtures

account_route = fixtures.account_route

pytestmark = pytest.mark.integration

ROUTES = [
    ("POST", "/account/deactivate", {"confirm": True}),
    ("POST", "/account/deletion/request", None),
    ("POST", "/account/deletion/confirm", {"code": "123456"}),
    ("POST", "/account/deletion/cancel", None),
    ("GET", "/account/deletion/status", None),
    ("GET", "/account/export", None),
]


def request(fixture, route):
    method, path, payload = route
    return fixture.client.request(method, path, json=payload)


def code(fixture, expired=False):
    fixture.user.deletion_confirmation_code_hash = bcrypt.hashpw(
        b"123456", bcrypt.gensalt()
    ).decode()
    fixture.user.deletion_confirmation_expires_at = datetime.now(
        timezone.utc
    ) + timedelta(minutes=-1 if expired else 15)
    fixture.db.commit()


def pending(fixture, expired=False):
    fixture.user.is_active = False
    fixture.user.deactivated_at = datetime.now(timezone.utc)
    fixture.user.deletion_requested_at = datetime.now(timezone.utc)
    fixture.user.deletion_scheduled_for = datetime.now(timezone.utc) + timedelta(
        days=-1 if expired else 20
    )
    fixture.db.add(
        DeletedEmail(
            id=str(uuid4()),
            email_hash=service_module.AccountDeletionService.hash_email(
                fixture.user.email
            ),
            deletion_type="gdpr_deleted",
        )
    )
    fixture.db.commit()


@pytest.mark.parametrize("route", ROUTES)
def test_account_requires_authentication(account_route, route):
    account_route.principal = None
    response = request(account_route, route)
    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication required. Provide 'Authorization: Bearer ***' header or login via dashboard."
    }
    assert response.headers["www-authenticate"] == "Bearer"
    account_route.mail.send_email.assert_not_called()


@pytest.mark.parametrize("route", ROUTES)
def test_account_missing_principal_user(account_route, route):
    account_route.principal = replace(account_route.principal, user_id=str(uuid4()))
    response = request(account_route, route)
    assert response.status_code == 404
    assert response.json() == {"detail": "User not found."}


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_account_database_failure_is_bounded(
    account_route, monkeypatch, route, failure
):
    monkeypatch.setattr(
        account_route.db,
        "query",
        Mock(side_effect=failure("private connection detail")),
    )
    response = request(account_route, route)
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to complete account operation. Please try again."
    }


@pytest.mark.parametrize(
    "route,method",
    list(
        zip(
            ROUTES,
            [
                "deactivate_account",
                "request_deletion_code",
                "confirm_deletion",
                "cancel_pending_deletion",
                "get_deletion_status",
                "export_user_data",
            ],
        )
    ),
)
def test_account_unexpected_value_error_is_bounded(
    account_route, monkeypatch, route, method
):
    monkeypatch.setattr(
        service_module.AccountDeletionService,
        method,
        Mock(side_effect=ValueError("private provider detail")),
    )
    response = request(account_route, route)
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to complete account operation. Please try again."
    }


@pytest.mark.parametrize("failure", ["false", "unconfigured", "exception", "factory"])
def test_code_delivery_failure_never_claims_sent(account_route, monkeypatch, failure):
    monkeypatch.setattr(
        service_module.secrets, "randbelow", Mock(side_effect=[123456, 654321])
    )
    if failure == "false":
        account_route.mail.send_email.return_value = {
            "success": False,
            "error": "private mail detail",
        }
    elif failure == "unconfigured":
        account_route.mail.is_configured.return_value = False
    elif failure == "exception":
        account_route.mail.send_email.side_effect = RuntimeError("private mail detail")
    else:
        monkeypatch.setattr(
            service_module,
            "get_email_service",
            Mock(side_effect=ValueError("private mail detail")),
        )
    response = request(account_route, ROUTES[1])
    assert response.status_code == 502
    assert response.json() == {
        "detail": "Unable to deliver confirmation code. Please request a new code."
    }
    account_route.db.expire_all()
    assert account_route.user.is_active is True
    assert account_route.user.deletion_scheduled_for is None

    retained_hash = account_route.user.deletion_confirmation_code_hash
    retained_expiry = account_route.user.deletion_confirmation_expires_at
    assert bcrypt.checkpw(b"123456", retained_hash.encode())
    assert (
        timedelta(minutes=14)
        < retained_expiry - datetime.now(timezone.utc)
        <= timedelta(minutes=15)
    )
    assert account_route.other.deletion_confirmation_code_hash is None

    # A retry replaces the stored code only after the caller requests a new one.
    monkeypatch.setattr(service_module, "get_email_service", lambda: account_route.mail)
    account_route.mail.is_configured.return_value = True
    account_route.mail.send_email.side_effect = None
    account_route.mail.send_email.return_value = {"success": True}
    account_route.mail.send_email.reset_mock()
    retry = request(account_route, ROUTES[1])
    assert retry.status_code == 200
    assert retry.json()["message"] == "Confirmation code sent to your email."
    account_route.mail.send_email.assert_awaited_once()
    assert "654321" in account_route.mail.send_email.call_args.kwargs["text_content"]
    account_route.db.expire_all()
    assert account_route.user.deletion_confirmation_code_hash != retained_hash
    assert bcrypt.checkpw(
        b"654321", account_route.user.deletion_confirmation_code_hash.encode()
    )
    assert not bcrypt.checkpw(
        b"123456", account_route.user.deletion_confirmation_code_hash.encode()
    )
    assert account_route.user.deletion_confirmation_expires_at > retained_expiry
    assert (
        account_route.user.deletion_confirmation_expires_at.isoformat()
        == retry.json()["code_expires_at"]
    )


def test_code_request_sends_matching_code_without_exposing_hash(
    account_route, monkeypatch
):
    monkeypatch.setattr(service_module.secrets, "randbelow", lambda limit: 123456)
    response = request(account_route, ROUTES[1])
    assert response.status_code == 200
    assert set(response.json()) == {"message", "code_expires_at"}
    assert response.json()["message"] == "Confirmation code sent to your email."
    account_route.mail.send_email.assert_awaited_once()
    sent = account_route.mail.send_email.call_args.kwargs
    assert sent["to_emails"] == [account_route.user.email]
    assert sent["subject"] == "Aelira Account Deletion - Confirmation Code"
    assert "123456" in sent["text_content"] and "123456" in sent["html_content"]
    account_route.db.expire_all()
    assert bcrypt.checkpw(
        b"123456", account_route.user.deletion_confirmation_code_hash.encode()
    )
    expires = datetime.fromisoformat(response.json()["code_expires_at"])
    assert (
        timedelta(minutes=14)
        < expires - datetime.now(timezone.utc)
        <= timedelta(minutes=15)
    )
    assert account_route.other.deletion_confirmation_code_hash is None


def test_deactivation_is_scoped_and_persisted(account_route):
    fixture = account_route
    response = fixture.client.post(
        "/account/deactivate",
        json={"confirm": True, "reason": "leaving", "user_id": fixture.other.id},
    )
    assert response.status_code == 200
    assert response.json() == {
        "message": "Account deactivated successfully.",
        "sessions_revoked": 1,
        "keys_deactivated": 1,
    }
    fixture.db.expire_all()
    assert fixture.user.is_active is False and fixture.user.deactivated_at is not None
    assert (
        fixture.keys[0].is_active is False
        and fixture.sessions[0].revoked_at is not None
    )
    assert (
        fixture.other.is_active is True
        and fixture.keys[1].is_active is True
        and fixture.sessions[1].revoked_at is None
    )
    block = (
        fixture.db.query(DeletedEmail)
        .filter_by(
            email_hash=service_module.AccountDeletionService.hash_email(
                fixture.user.email
            )
        )
        .one()
    )
    assert block.deletion_type == "deactivated" and block.reason == "leaving"
    assert (
        timedelta(days=89)
        < block.cooldown_until - datetime.now(timezone.utc)
        <= timedelta(days=90)
    )
    audit = (
        fixture.db.query(AuditLog)
        .filter_by(user_id=fixture.user.id, action="account_deactivate")
        .one()
    )
    assert audit.details == {
        "reason": "leaving",
        "sessions_revoked": 1,
        "keys_deactivated": 1,
    }


@pytest.mark.parametrize("payload,status", [({"confirm": False}, 400), ({}, 422)])
def test_deactivation_requires_confirmation(account_route, payload, status):
    response = account_route.client.post("/account/deactivate", json=payload)
    assert response.status_code == status
    if status == 400:
        assert response.json() == {"detail": "You must confirm account deactivation."}
    account_route.db.expire_all()
    assert account_route.user.is_active and account_route.keys[0].is_active
    assert account_route.sessions[0].revoked_at is None


def test_confirmation_schedules_only_principal_and_revokes_credentials(account_route):
    code(account_route)
    response = request(account_route, ROUTES[2])
    assert response.status_code == 200
    assert (
        response.json()["message"]
        == "Account deletion scheduled. Your data will be permanently removed after 30 days."
    )
    assert set(response.json()) == {"message", "scheduled_for"}
    scheduled = datetime.fromisoformat(response.json()["scheduled_for"])
    assert (
        timedelta(days=29)
        < scheduled - datetime.now(timezone.utc)
        <= timedelta(days=30)
    )
    account_route.db.expire_all()
    assert account_route.user.deletion_scheduled_for == scheduled
    assert account_route.user.deletion_confirmation_code_hash is None
    assert account_route.user.deletion_confirmation_expires_at is None
    assert not account_route.user.is_active and not account_route.keys[0].is_active
    assert account_route.sessions[0].revoked_at is not None
    assert account_route.other.is_active and account_route.keys[1].is_active
    assert account_route.sessions[1].revoked_at is None
    block = (
        account_route.db.query(DeletedEmail)
        .filter_by(
            email_hash=service_module.AccountDeletionService.hash_email(
                account_route.user.email
            )
        )
        .one()
    )
    assert block.deletion_type == "gdpr_deleted" and block.cooldown_until is None
    account_route.mail.send_email.assert_awaited_once()


@pytest.mark.parametrize(
    "value",
    ["000000", "", "12345", "x" * 1000],
    ids=["wrong", "empty", "short", "long"],
)
def test_invalid_code_is_harmless(account_route, value):
    code(account_route)
    response = account_route.client.post(
        "/account/deletion/confirm", json={"code": value}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid confirmation code."}
    account_route.db.expire_all()
    assert account_route.user.is_active and account_route.keys[0].is_active
    assert account_route.sessions[0].revoked_at is None
    assert account_route.user.deletion_scheduled_for is None


def test_expired_code_cleared_without_scheduling(account_route):
    code(account_route, expired=True)
    response = request(account_route, ROUTES[2])
    assert response.status_code == 400
    assert response.json() == {
        "detail": "Confirmation code has expired. Please request a new one."
    }
    account_route.db.expire_all()
    assert account_route.user.deletion_confirmation_code_hash is None
    assert account_route.user.deletion_confirmation_expires_at is None
    assert (
        account_route.user.is_active
        and account_route.user.deletion_scheduled_for is None
    )


@pytest.mark.parametrize(
    "route,detail",
    [
        (
            ROUTES[2],
            "No deletion request pending. Please request a new confirmation code.",
        ),
        (ROUTES[3], "No pending deletion to cancel."),
    ],
)
def test_missing_lifecycle_state(account_route, route, detail):
    response = request(account_route, route)
    assert response.status_code == 400
    assert response.json() == {"detail": detail}


def test_expired_cancellation_leaves_schedule(account_route):
    pending(account_route, expired=True)
    scheduled = account_route.user.deletion_scheduled_for
    response = request(account_route, ROUTES[3])
    assert response.status_code == 400
    assert response.json() == {
        "detail": "Deletion grace period has expired and cannot be cancelled."
    }
    account_route.db.expire_all()
    assert (
        account_route.user.deletion_scheduled_for == scheduled
        and not account_route.user.is_active
    )


def test_cancellation_persists_and_preserves_other_account(account_route):
    pending(account_route)
    account_route.keys[0].is_active = False
    account_route.sessions[0].revoked_at = datetime.now(timezone.utc)
    account_route.db.commit()
    response = request(account_route, ROUTES[3])
    assert response.status_code == 200
    assert response.json() == {
        "message": "Account deletion cancelled. Your account has been reactivated."
    }
    account_route.db.expire_all()
    assert account_route.user.is_active and account_route.user.deactivated_at is None
    assert (
        account_route.user.deletion_requested_at is None
        and account_route.user.deletion_scheduled_for is None
    )
    assert (
        account_route.db.query(DeletedEmail)
        .filter_by(
            email_hash=service_module.AccountDeletionService.hash_email(
                account_route.user.email
            )
        )
        .count()
        == 0
    )
    assert account_route.other.is_active
    assert not account_route.keys[0].is_active
    assert account_route.sessions[0].revoked_at is not None


@pytest.mark.parametrize("state", ["none", "pending", "expired"])
def test_status_exact_contract(account_route, state):
    if state != "none":
        pending(account_route, expired=state == "expired")
    response = request(account_route, ROUTES[4])
    assert response.status_code == 200
    if state == "none":
        assert response.json() == {
            "deletion_pending": False,
            "scheduled_for": None,
            "days_remaining": None,
            "can_cancel": False,
        }
    else:
        assert response.json() == {
            "deletion_pending": True,
            "scheduled_for": account_route.user.deletion_scheduled_for.isoformat(),
            "days_remaining": 19 if state == "pending" else 0,
            "can_cancel": state == "pending",
        }


def test_export_is_principal_scoped_and_omits_credentials(account_route):
    fixture = account_route
    scans = [
        Scan(
            id=str(uuid4()),
            user_id=user.id,
            department_id=user.department_id,
            scan_type=ScanType.PDF,
            status=ScanStatus.COMPLETED,
            file_name=f"{user.name}.pdf",
            pages=2,
        )
        for user in (fixture.user, fixture.other)
    ]
    fixture.db.add_all(scans)
    fixture.db.flush()
    fixture.db.add_all(
        [
            ScanResult(
                id=str(uuid4()), scan_id=scan.id, compliance_score=80, critical_issues=1
            )
            for scan in scans
        ]
    )
    fixture.db.add_all(
        [
            AuditLog(
                id=str(uuid4()),
                user_id=user.id,
                department_id=user.department_id,
                action="login_success",
                status="success",
                resource_type="user",
                details={"private": "not exported"},
            )
            for user in (fixture.user, fixture.other)
        ]
    )
    fixture.db.commit()
    response = fixture.client.get(
        "/account/export", params={"user_id": fixture.other.id}
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "exported_at",
        "user",
        "department",
        "preferences",
        "scans",
        "api_keys",
        "audit_logs",
    }
    assert (
        data["user"]["id"] == fixture.user.id
        and data["user"]["email"] == fixture.user.email
    )
    assert [scan["id"] for scan in data["scans"]] == [scans[0].id]
    assert data["scans"][0]["result"] == {
        "compliance_score": 80,
        "wcag_level": "AA",
        "critical_issues": 1,
        "high_issues": 0,
        "medium_issues": 0,
        "low_issues": 0,
    }
    assert [key["id"] for key in data["api_keys"]] == [fixture.keys[0].id]
    assert len(data["audit_logs"]) == 1
    assert data["audit_logs"][0]["action"] == "login_success"
    for secret in [
        fixture.other.id,
        fixture.other.email,
        scans[1].id,
        fixture.keys[0].key_hash,
        fixture.sessions[0].refresh_token_hash,
        "not exported",
    ]:
        assert secret not in response.text
    assert (
        fixture.db.query(AuditLog)
        .filter_by(user_id=fixture.user.id, action="account_data_export")
        .count()
        == 1
    )


def _assert_lifecycle_rollback(fixture, original_hash, original_expiry):
    fixture.db.expire_all()
    assert fixture.user.is_active and fixture.keys[0].is_active
    assert fixture.sessions[0].revoked_at is None
    assert fixture.user.deactivated_at is None
    assert fixture.user.deletion_requested_at is None
    assert fixture.user.deletion_scheduled_for is None
    assert fixture.user.deletion_confirmation_code_hash == original_hash
    assert fixture.user.deletion_confirmation_expires_at == original_expiry
    assert (
        fixture.db.query(DeletedEmail)
        .filter_by(
            email_hash=service_module.AccountDeletionService.hash_email(
                fixture.user.email
            )
        )
        .count()
        == 0
    )
    assert fixture.db.query(AuditLog).filter_by(user_id=fixture.user.id).count() == 0


@pytest.mark.parametrize("route", [ROUTES[0], ROUTES[2]])
def test_lifecycle_commit_failure_rolls_back_credentials_and_state(
    account_route, monkeypatch, route
):
    if route == ROUTES[2]:
        code(account_route)
    original_hash = account_route.user.deletion_confirmation_code_hash
    original_expiry = account_route.user.deletion_confirmation_expires_at
    monkeypatch.setattr(
        account_route.db,
        "commit",
        Mock(side_effect=RuntimeError("private commit detail")),
    )
    response = request(account_route, route)
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to complete account operation. Please try again."
    }
    _assert_lifecycle_rollback(account_route, original_hash, original_expiry)


@pytest.mark.parametrize("route", [ROUTES[0], ROUTES[2]])
def test_lifecycle_audit_failure_rolls_back_before_commit(
    account_route, monkeypatch, route
):
    if route == ROUTES[2]:
        code(account_route)
    original_hash = account_route.user.deletion_confirmation_code_hash
    original_expiry = account_route.user.deletion_confirmation_expires_at
    monkeypatch.setattr(
        service_module.AuditService,
        "log_action",
        Mock(side_effect=RuntimeError("private audit detail")),
    )
    response = request(account_route, route)
    assert response.status_code == 500
    _assert_lifecycle_rollback(account_route, original_hash, original_expiry)


@pytest.mark.parametrize("route", ROUTES)
def test_account_service_factory_failure_is_bounded(account_route, monkeypatch, route):
    monkeypatch.setattr(
        routes,
        "get_account_deletion_service",
        Mock(side_effect=RuntimeError("private service setup")),
    )
    response = request(account_route, route)
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to complete account operation. Please try again."
    }


@pytest.mark.parametrize("failure", ["false", "exception", "unconfigured"])
def test_optional_schedule_notification_failure_preserves_success(
    account_route, failure
):
    code(account_route)
    if failure == "false":
        account_route.mail.send_email.return_value = {"success": False}
    elif failure == "exception":
        account_route.mail.send_email.side_effect = RuntimeError(
            "private notification failure"
        )
    else:
        account_route.mail.is_configured.return_value = False
    response = request(account_route, ROUTES[2])
    assert response.status_code == 200
    account_route.db.expire_all()
    assert (
        account_route.user.deletion_scheduled_for.isoformat()
        == response.json()["scheduled_for"]
    )
    assert not account_route.user.is_active


def test_request_commit_failure_does_not_send_code(account_route, monkeypatch):
    monkeypatch.setattr(
        account_route.db,
        "commit",
        Mock(side_effect=RuntimeError("private commit failure")),
    )
    response = request(account_route, ROUTES[1])
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to complete account operation. Please try again."
    }
    account_route.mail.send_email.assert_not_called()
    account_route.db.expire_all()
    assert account_route.user.deletion_confirmation_code_hash is None
    assert account_route.user.deletion_confirmation_expires_at is None


@pytest.mark.parametrize("failure", ["commit", "audit"])
def test_cancel_failure_preserves_pending_account(account_route, monkeypatch, failure):
    pending(account_route)
    scheduled = account_route.user.deletion_scheduled_for
    if failure == "commit":
        monkeypatch.setattr(
            account_route.db,
            "commit",
            Mock(side_effect=RuntimeError("private commit failure")),
        )
    else:
        monkeypatch.setattr(
            service_module.AuditService,
            "log_action",
            Mock(side_effect=RuntimeError("private audit failure")),
        )
    response = request(account_route, ROUTES[3])
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to complete account operation. Please try again."
    }
    account_route.db.expire_all()
    assert not account_route.user.is_active
    assert account_route.user.deletion_scheduled_for == scheduled
    assert (
        account_route.db.query(DeletedEmail)
        .filter_by(
            email_hash=service_module.AccountDeletionService.hash_email(
                account_route.user.email
            )
        )
        .count()
        == 1
    )


@pytest.mark.parametrize("payload", [{}, {"code": None}, {"code": 123456}])
def test_confirmation_payload_validation(account_route, payload):
    response = account_route.client.post("/account/deletion/confirm", json=payload)
    assert response.status_code == 422
    account_route.db.expire_all()
    assert (
        account_route.user.is_active
        and account_route.user.deletion_scheduled_for is None
    )
