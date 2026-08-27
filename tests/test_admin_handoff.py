"""Focused security contracts for first-administrator handoff."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import hashlib
import logging
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import CheckConstraint, MetaData, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.api.user_management import (
    AcceptInvitationRequest,
    accept_invitation,
    get_admin_api_key,
)
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import (
    APIKey,
    AuditLog,
    DeletedEmail,
    Department,
    InvitationPurpose,
    InvitationStatus,
    User,
    UserInvitation,
    UserRole,
)
from src.mailer.email_service import EmailService
from src.services.account_deletion_service import AccountDeletionService


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/auth/accept-invitation",
            "raw_path": b"/auth/accept-invitation",
            "query_string": b"",
            "headers": [],
            "client": ("198.51.100.8", 43210),
            "server": ("testserver", 443),
        }
    )


@pytest.fixture
def handoff_db():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    for table in [
        Department.__table__,
        User.__table__,
        UserInvitation.__table__,
        AuditLog.__table__,
        DeletedEmail.__table__,
    ]:
        table.to_metadata(metadata)
    department_table = metadata.tables["departments"]
    for constraint in list(department_table.constraints):
        if isinstance(constraint, CheckConstraint) and constraint.name.startswith(
            "ck_departments_lms_ai_"
        ):
            department_table.constraints.remove(constraint)
    department_table.c.lms_ai_purposes.server_default = None
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        yield db
    engine.dispose()


def _department(db, *, department_id: str, email: str) -> Department:
    department = Department(
        id=department_id,
        name=f"Department {department_id}",
        institution="Example University",
        contact_email=email,
        contact_name="Admin",
        tier="department",
    )
    db.add(department)
    db.commit()
    return department


def _handoff(db, *, department_id: str, email: str, raw_token: str):
    invitation = UserInvitation(
        id=f"invite-{department_id}",
        department_id=department_id,
        email=email,
        role=UserRole.ADMIN,
        token=hashlib.sha256(raw_token.encode()).hexdigest(),
        purpose=InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value,
        status=InvitationStatus.PENDING,
        delivery_queued_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(invitation)
    db.commit()
    return invitation


def test_handoff_acceptance_is_normalized_atomic_and_replay_idempotent(handoff_db):
    raw_token = "handoff-token-with-sufficient-entropy-123456789"
    _department(handoff_db, department_id="target", email="admin@example.edu")
    invitation = _handoff(
        handoff_db,
        department_id="target",
        email="admin@example.edu",
        raw_token=raw_token,
    )

    first = asyncio.run(
        accept_invitation(
            AcceptInvitationRequest(
                token=raw_token,
                email="ADMIN@EXAMPLE.EDU",
                name="First Admin",
            ),
            _request(),
            handoff_db,
        )
    )
    replay = asyncio.run(
        accept_invitation(
            AcceptInvitationRequest(token=raw_token, email="admin@example.edu"),
            _request(),
            handoff_db,
        )
    )

    assert first["outcome"] == "accepted"
    assert first["login_required"] is True
    assert replay["outcome"] == "already_accepted"
    assert "email" not in replay
    assert "department_id" not in replay
    assert "user_id" not in replay
    assert handoff_db.query(User).count() == 1
    user = handoff_db.query(User).one()
    assert user.email == "admin@example.edu"
    assert user.department_id == "target"
    assert user.role == UserRole.ADMIN
    assert invitation.status == InvitationStatus.ACCEPTED
    audits = handoff_db.query(AuditLog).all()
    assert len(audits) == 1
    assert audits[0].details == {
        "outcome": "accepted",
        "purpose": InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value,
    }


def test_handoff_digest_cannot_be_submitted_as_the_bearer(handoff_db):
    raw_token = "handoff-token-with-sufficient-entropy-123456789"
    _department(handoff_db, department_id="target", email="admin@example.edu")
    invitation = _handoff(
        handoff_db,
        department_id="target",
        email="admin@example.edu",
        raw_token=raw_token,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            accept_invitation(
                AcceptInvitationRequest(
                    token=invitation.token,
                    email="admin@example.edu",
                ),
                _request(),
                handoff_db,
            )
        )

    assert exc_info.value.status_code == 404
    assert handoff_db.query(User).count() == 0
    assert invitation.status == InvitationStatus.PENDING


@pytest.mark.parametrize(
    ("email", "role", "token"),
    [
        ("ADMIN@example.edu", UserRole.ADMIN, "a" * 64),
        ("admin@example.edu", UserRole.FACULTY, "a" * 64),
        ("admin@example.edu", UserRole.ADMIN, "raw-token"),
    ],
)
def test_handoff_database_contract_rejects_invalid_security_fields(
    handoff_db, email, role, token
):
    _department(handoff_db, department_id="target", email="admin@example.edu")
    handoff_db.add(
        UserInvitation(
            id="invalid-handoff",
            department_id="target",
            email=email,
            role=role,
            token=token,
            purpose=InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value,
            status=InvitationStatus.PENDING,
            delivery_queued_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
    )

    with pytest.raises(IntegrityError):
        handoff_db.commit()

    handoff_db.rollback()


def test_handoff_email_is_globally_unique_across_departments(handoff_db):
    _department(handoff_db, department_id="first", email="admin@example.edu")
    _department(handoff_db, department_id="second", email="admin@example.edu")
    _handoff(
        handoff_db,
        department_id="first",
        email="admin@example.edu",
        raw_token="first-handoff-token-with-sufficient-entropy-12345",
    )

    with pytest.raises(IntegrityError):
        _handoff(
            handoff_db,
            department_id="second",
            email="admin@example.edu",
            raw_token="second-handoff-token-with-sufficient-entropy-1234",
        )

    handoff_db.rollback()


def test_deleted_email_block_prevents_handoff_acceptance(handoff_db):
    raw_token = "handoff-token-with-sufficient-entropy-123456789"
    _department(handoff_db, department_id="target", email="admin@example.edu")
    invitation = _handoff(
        handoff_db,
        department_id="target",
        email="admin@example.edu",
        raw_token=raw_token,
    )
    handoff_db.add(
        DeletedEmail(
            email_hash=hashlib.sha256(b"admin@example.edu").hexdigest(),
            deletion_type="gdpr_deleted",
            cooldown_until=None,
        )
    )
    handoff_db.commit()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            accept_invitation(
                AcceptInvitationRequest(token=raw_token, email="admin@example.edu"),
                _request(),
                handoff_db,
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invitation could not be accepted"
    assert handoff_db.query(User).count() == 0
    assert invitation.status == InvitationStatus.PENDING
    assert handoff_db.query(AuditLog).one().details["outcome"] == "email_blocked"


def test_expired_deletion_cleanup_can_preserve_surrounding_transaction_lock():
    record = MagicMock(
        cooldown_until=datetime.now().astimezone() - timedelta(seconds=1)
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = record

    blocked, reason = AccountDeletionService.is_email_blocked(
        db,
        "admin@example.edu",
        commit_expired_cleanup=False,
    )

    assert blocked is False
    assert reason is None
    db.delete.assert_called_once_with(record)
    db.flush.assert_called_once_with()
    db.commit.assert_not_called()


def test_handoff_never_moves_or_promotes_another_department_user(handoff_db):
    raw_token = "handoff-token-with-sufficient-entropy-123456789"
    _department(handoff_db, department_id="source", email="admin@example.edu")
    _department(handoff_db, department_id="target", email="admin@example.edu")
    existing = User(
        id="existing-user",
        email="admin@example.edu",
        department_id="source",
        role=UserRole.FACULTY,
        is_active=True,
    )
    handoff_db.add(existing)
    handoff_db.commit()
    invitation = _handoff(
        handoff_db,
        department_id="target",
        email="admin@example.edu",
        raw_token=raw_token,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            accept_invitation(
                AcceptInvitationRequest(token=raw_token, email="admin@example.edu"),
                _request(),
                handoff_db,
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invitation could not be accepted"
    assert existing.department_id == "source"
    assert existing.role == UserRole.FACULTY
    assert invitation.status == InvitationStatus.PENDING
    audit = handoff_db.query(AuditLog).one()
    assert audit.status == "failure"
    assert audit.details["outcome"] == "email_bound_to_other_department"


def test_expired_handoff_becomes_terminal_with_bounded_audit(handoff_db):
    raw_token = "handoff-token-with-sufficient-entropy-123456789"
    _department(handoff_db, department_id="target", email="admin@example.edu")
    invitation = _handoff(
        handoff_db,
        department_id="target",
        email="admin@example.edu",
        raw_token=raw_token,
    )
    invitation.expires_at = datetime.utcnow() - timedelta(seconds=1)
    handoff_db.commit()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            accept_invitation(
                AcceptInvitationRequest(token=raw_token, email="admin@example.edu"),
                _request(),
                handoff_db,
            )
        )

    assert exc_info.value.status_code == 400
    assert invitation.status == InvitationStatus.EXPIRED
    audit = handoff_db.query(AuditLog).one()
    assert audit.status == "failure"
    assert audit.details == {
        "outcome": "expired",
        "purpose": InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value,
    }


def test_legacy_member_invitation_still_accepts_raw_token(handoff_db):
    raw_token = "legacy-member-token-with-sufficient-entropy-12345"
    _department(handoff_db, department_id="target", email="admin@example.edu")
    invitation = UserInvitation(
        id="legacy-invite",
        department_id="target",
        email="faculty@example.edu",
        role=UserRole.FACULTY,
        token=raw_token,
        purpose=InvitationPurpose.MEMBER.value,
        status=InvitationStatus.PENDING,
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    handoff_db.add(invitation)
    handoff_db.commit()

    result = asyncio.run(
        accept_invitation(
            AcceptInvitationRequest(token=raw_token, email="faculty@example.edu"),
            _request(),
            handoff_db,
        )
    )

    assert result["outcome"] == "accepted"
    assert handoff_db.query(User).one().role == UserRole.FACULTY


def test_admin_dependency_accepts_normal_session_and_rejects_lti_or_faculty():
    admin = AuthenticatedPrincipal(
        api_key=None,
        user_id="admin",
        department_id="target",
        user_role=UserRole.ADMIN,
        auth_method="session",
    )
    assert get_admin_api_key(admin) == (None, "admin", "target", UserRole.ADMIN)

    faculty = AuthenticatedPrincipal(
        api_key=None,
        user_id="faculty",
        department_id="target",
        user_role=UserRole.FACULTY,
        auth_method="session",
    )
    with pytest.raises(HTTPException) as faculty_error:
        get_admin_api_key(faculty)
    assert faculty_error.value.status_code == 403

    lti_admin = AuthenticatedPrincipal(
        api_key=None,
        user_id="lti-admin",
        department_id="target",
        user_role=UserRole.ADMIN,
        auth_method="lti",
        lti_staff_role="Administrator",
        lti_account_wide=True,
    )
    with pytest.raises(HTTPException) as lti_error:
        get_admin_api_key(lti_admin)
    assert lti_error.value.status_code == 403


def test_admin_dependency_preserves_api_key_identity():
    api_key = APIKey(
        id="key-id",
        key_hash="hash",
        key_prefix="aelira_test_prefix",
        user_id="admin",
        department_id="target",
    )
    principal = AuthenticatedPrincipal(
        api_key=api_key,
        user_id="admin",
        department_id="target",
        user_role=UserRole.ADMIN,
        auth_method="api_key",
    )
    assert get_admin_api_key(principal)[0] is api_key


def test_smtp_success_log_contains_count_not_recipient(monkeypatch, caplog):
    class FakeSMTP:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            return None

        def sendmail(self, *args):
            return None

    monkeypatch.setattr("src.mailer.email_service.smtplib.SMTP", FakeSMTP)
    service = EmailService(
        smtp_host="mail.example.test",
        smtp_port=25,
        smtp_user="",
        smtp_password="",
    )
    recipient = "first-admin@example.edu"

    with caplog.at_level(logging.INFO, logger="src.mailer.email_service"):
        result = asyncio.run(
            service._send_via_smtp(
                [recipient],
                "Subject",
                "<p>Body</p>",
                "Body",
                "noreply@example.test",
                "Aelira",
            )
        )

    assert result == {"success": True}
    assert "recipient_count=1" in caplog.text
    assert recipient not in caplog.text


def test_sendgrid_success_log_contains_count_not_recipient(monkeypatch, caplog):
    class FakeResponse:
        status_code = 202
        headers = {"X-Message-Id": "bounded-message-id"}
        text = ""

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("src.mailer.email_service.httpx.AsyncClient", FakeAsyncClient)
    service = EmailService(sendgrid_api_key="test-key")
    recipient = "first-admin@example.edu"

    with caplog.at_level(logging.INFO, logger="src.mailer.email_service"):
        result = asyncio.run(
            service._send_via_sendgrid(
                [recipient],
                "Subject",
                "<p>Body</p>",
                "Body",
                "noreply@example.test",
                "Aelira",
                None,
            )
        )

    assert result == {"success": True, "message_id": "bounded-message-id"}
    assert "recipient_count=1" in caplog.text
    assert recipient not in caplog.text


def test_sendgrid_rejection_does_not_expose_response_body(monkeypatch, caplog):
    canary = "private-sendgrid-response-canary"

    class FakeResponse:
        status_code = 400
        headers = {}
        text = canary

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("src.mailer.email_service.httpx.AsyncClient", FakeAsyncClient)
    service = EmailService(sendgrid_api_key="test-key")

    with caplog.at_level(logging.ERROR, logger="src.mailer.email_service"):
        result = asyncio.run(
            service._send_via_sendgrid(
                ["admin@example.edu"],
                "Subject",
                "<p>Body</p>",
                "Body",
                "noreply@example.test",
                "Aelira",
                None,
            )
        )

    assert result == {"success": False, "error": "Email delivery failed"}
    assert canary not in caplog.text
    assert canary not in str(result)


@pytest.mark.parametrize("transport", ["smtp", "sendgrid"])
def test_mail_transport_failures_do_not_expose_provider_details(
    transport, monkeypatch, caplog
):
    canary = "private-provider-detail-canary"
    service = EmailService(
        sendgrid_api_key="test-key",
        smtp_host="mail.example.test",
        smtp_port=25,
        smtp_user="",
        smtp_password="",
    )

    if transport == "smtp":

        class FailingSMTP:
            def __init__(self, *args):
                raise RuntimeError(canary)

        monkeypatch.setattr("src.mailer.email_service.smtplib.SMTP", FailingSMTP)
        coroutine = service._send_via_smtp(
            ["admin@example.edu"],
            "Subject",
            "<p>Body</p>",
            "Body",
            "noreply@example.test",
            "Aelira",
        )
    else:

        class FailingAsyncClient:
            async def __aenter__(self):
                raise RuntimeError(canary)

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(
            "src.mailer.email_service.httpx.AsyncClient", FailingAsyncClient
        )
        coroutine = service._send_via_sendgrid(
            ["admin@example.edu"],
            "Subject",
            "<p>Body</p>",
            "Body",
            "noreply@example.test",
            "Aelira",
            None,
        )

    with caplog.at_level(logging.ERROR, logger="src.mailer.email_service"):
        result = asyncio.run(coroutine)

    assert result == {"success": False, "error": "Email delivery failed"}
    assert canary not in caplog.text
    assert canary not in str(result)
