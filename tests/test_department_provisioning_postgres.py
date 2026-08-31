"""PostgreSQL proof that department provisioning and its audit are atomic."""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
from datetime import datetime, timedelta
import os
import uuid

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.api import auth_routes
from src.api import user_management
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import (
    AuditLog,
    Base,
    DeletedEmail,
    Department,
    InvitationPurpose,
    InvitationStatus,
    User,
    UserInvitation,
    UserRole,
)


def _postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url and os.getenv("CI", "").lower() == "true":
        database_url = os.getenv("DATABASE_URL")
    return (
        database_url or "postgresql://postgres:postgres@localhost:5432/aelira_test"
    ).replace("postgresql+asyncpg://", "postgresql://")


@contextmanager
def _isolated_database():
    database_url = _postgres_url()
    try:
        admin_engine = create_engine(database_url)
        with admin_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"disposable PostgreSQL unavailable: {exc}")

    schema = f"department_provision_{uuid.uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        database_url,
        isolation_level="READ COMMITTED",
        connect_args={"options": f"-csearch_path={schema}"},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Department.__table__,
            User.__table__,
            UserInvitation.__table__,
            AuditLog.__table__,
            DeletedEmail.__table__,
        ],
    )
    try:
        yield engine, sessionmaker(bind=engine)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/auth/departments",
            "raw_path": b"/auth/departments",
            "query_string": b"",
            "headers": [],
            "client": ("198.51.100.7", 43210),
            "server": ("testserver", 80),
        }
    )


def _payload() -> auth_routes.CreateDepartmentRequest:
    suffix = uuid.uuid4().hex
    return auth_routes.CreateDepartmentRequest(
        name=f"Department {suffix}",
        institution="Example University",
        contact_email=f"admin-{suffix}@example.edu",
        contact_name="Department Admin",
    )


def _allow_provisioning(monkeypatch):
    monkeypatch.setattr(
        auth_routes,
        "check_signup_abuse",
        AsyncMock(
            return_value=SimpleNamespace(allowed=True, recommended_action="allow")
        ),
    )
    email_service = SimpleNamespace(
        is_configured=lambda: True,
        send_admin_handoff_invitation=AsyncMock(return_value={"success": True}),
    )
    monkeypatch.setattr(
        auth_routes,
        "get_email_service",
        lambda: email_service,
    )
    return email_service


def _seed_provisioner(session_factory) -> AuthenticatedPrincipal:
    with session_factory() as db:
        operator_department = Department(
            id="operator-department",
            name="Operator Department",
            institution="Aelira",
            contact_email="operator@example.edu",
            contact_name="Operator",
        )
        db.add(operator_department)
        db.add(
            User(
                id="provisioner",
                email="provisioner@example.edu",
                department_id=operator_department.id,
                role=UserRole.SUPER_ADMIN,
                is_active=True,
            )
        )
        db.commit()
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="provisioner",
        department_id="operator-department",
        user_role=UserRole.SUPER_ADMIN,
        auth_method="session",
    )


@pytest.mark.integration
async def test_success_persists_department_and_audit_together(monkeypatch):
    _allow_provisioning(monkeypatch)
    with _isolated_database() as (_, session_factory):
        with session_factory() as db:
            response = await auth_routes.create_department(
                _payload(), _request(), None, db
            )
            department_id = response.id
        await asyncio.sleep(0)

        with session_factory() as db:
            departments = db.query(Department).all()
            audits = db.query(AuditLog).all()
            invitations = db.query(UserInvitation).all()
            assert len(departments) == 1
            assert len(invitations) == 1
            assert invitations[0].department_id == department_id
            assert invitations[0].purpose == (
                InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value
            )
            assert len(audits) == 2
            provisioning_audit = next(
                audit for audit in audits if audit.action == "department_provision"
            )
            assert provisioning_audit.resource_id == department_id
            assert provisioning_audit.status == "success"


@pytest.mark.integration
async def test_audit_insert_failure_rolls_back_department(monkeypatch):
    _allow_provisioning(monkeypatch)
    with _isolated_database() as (engine, session_factory):

        def fail_audit_insert(
            connection, cursor, statement, parameters, context, executemany
        ):
            if statement.lstrip().lower().startswith("insert into audit_logs"):
                raise RuntimeError("forced audit insert failure")

        event.listen(engine, "before_cursor_execute", fail_audit_insert)
        try:
            with session_factory() as db:
                with pytest.raises(HTTPException) as exc_info:
                    await auth_routes.create_department(
                        _payload(), _request(), None, db
                    )
                assert exc_info.value.status_code == 500
                assert exc_info.value.detail == "Department could not be created"
        finally:
            event.remove(engine, "before_cursor_execute", fail_audit_insert)

        with session_factory() as db:
            assert db.query(Department).count() == 0
            assert db.query(UserInvitation).count() == 0
            assert db.query(AuditLog).count() == 0


@pytest.mark.integration
def test_concurrent_exact_retry_creates_one_department_and_handoff(monkeypatch):
    email_service = _allow_provisioning(monkeypatch)
    payload = _payload()

    with _isolated_database() as (_, session_factory):
        principal = _seed_provisioner(session_factory)

        def provision():
            with session_factory() as db:
                return asyncio.run(
                    auth_routes.create_department(payload, _request(), principal, db)
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: provision(), range(2)))

        assert len({response.id for response in responses}) == 1
        with session_factory() as db:
            assert db.query(Department).count() == 2
            assert db.query(UserInvitation).count() == 1
            invitation = db.query(UserInvitation).one()
            assert invitation.email == str(payload.contact_email).lower()
            assert len(invitation.token) == 64
            emailed_tokens = [
                call.kwargs["accept_url"].split("#token=", 1)[1]
                for call in email_service.send_admin_handoff_invitation.await_args_list
            ]
            assert len(emailed_tokens) == 1
            assert invitation.token not in emailed_tokens
            assert invitation.token in {
                auth_routes._handoff_token_digest(token) for token in emailed_tokens
            }


@pytest.mark.integration
def test_concurrent_differing_duplicate_payload_fails_without_extra_artifacts(
    monkeypatch,
):
    _allow_provisioning(monkeypatch)
    first = _payload()
    second = first.model_copy(
        update={"contact_email": "different@example.edu", "contact_name": "Different"}
    )

    with _isolated_database() as (_, session_factory):

        def provision(payload):
            with session_factory() as db:
                try:
                    response = asyncio.run(
                        auth_routes.create_department(payload, _request(), None, db)
                    )
                    return "created", response.id
                except HTTPException as exc:
                    return "rejected", exc.status_code, exc.detail

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(provision, [first, second]))

        assert sorted(result[0] for result in results) == ["created", "rejected"]
        rejection = next(result for result in results if result[0] == "rejected")
        assert rejection[1:] == (
            409,
            "A department with this identity already exists",
        )
        with session_factory() as db:
            assert db.query(Department).count() == 1
            assert db.query(UserInvitation).count() == 1
            rejected_audits = (
                db.query(AuditLog).filter(AuditLog.status == "failure").all()
            )
            assert len(rejected_audits) == 1
            assert rejected_audits[0].details["reason"] == "duplicate_department"


@pytest.mark.integration
def test_existing_user_email_rejects_new_target_without_target_artifacts(monkeypatch):
    _allow_provisioning(monkeypatch)
    payload = _payload().model_copy(
        update={"first_admin_email": "existing@example.edu"}
    )

    with _isolated_database() as (_, session_factory):
        with session_factory() as db:
            source = Department(
                id="source",
                name="Existing Department",
                institution="Example University",
                contact_email="existing@example.edu",
            )
            db.add(source)
            db.add(
                User(
                    id="existing",
                    email="existing@example.edu",
                    department_id=source.id,
                    role=UserRole.ADMIN,
                )
            )
            db.commit()

        with session_factory() as db:
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    auth_routes.create_department(payload, _request(), None, db)
                )
            assert exc_info.value.status_code == 409
            assert exc_info.value.detail == "The administrator email is unavailable"

        with session_factory() as db:
            assert db.query(Department).count() == 1
            assert db.query(UserInvitation).count() == 0
            audit = db.query(AuditLog).one()
            assert audit.status == "failure"
            assert audit.details["reason"] == "admin_email_unavailable"


@pytest.mark.integration
def test_revoked_handoff_retry_is_rejected_and_audited(monkeypatch):
    _allow_provisioning(monkeypatch)
    payload = _payload()

    with _isolated_database() as (_, session_factory):
        principal = _seed_provisioner(session_factory)
        with session_factory() as db:
            asyncio.run(
                auth_routes.create_department(payload, _request(), principal, db)
            )
        with session_factory() as db:
            invitation = db.query(UserInvitation).one()
            invitation.status = InvitationStatus.REVOKED
            db.commit()
        with session_factory() as db:
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    auth_routes.create_department(payload, _request(), principal, db)
                )
            assert exc_info.value.status_code == 409
            assert exc_info.value.detail == "The administrator handoff has been revoked"

        with session_factory() as db:
            assert db.query(Department).count() == 2
            assert db.query(UserInvitation).count() == 1
            rejected = db.query(AuditLog).filter(AuditLog.status == "failure").one()
            assert rejected.details["reason"] == "handoff_revoked"


@pytest.mark.integration
def test_blocked_email_rejects_existing_department_retry(monkeypatch):
    _allow_provisioning(monkeypatch)
    payload = _payload()

    with _isolated_database() as (_, session_factory):
        principal = _seed_provisioner(session_factory)
        with session_factory() as db:
            asyncio.run(
                auth_routes.create_department(payload, _request(), principal, db)
            )
            db.add(
                DeletedEmail(
                    email_hash=auth_routes.AccountDeletionService.hash_email(
                        str(payload.contact_email)
                    ),
                    deletion_type="gdpr_deleted",
                    cooldown_until=None,
                )
            )
            db.commit()

        with session_factory() as db:
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    auth_routes.create_department(payload, _request(), principal, db)
                )
            assert exc_info.value.status_code == 409
            assert exc_info.value.detail == "The administrator email is unavailable"

        with session_factory() as db:
            invitation = db.query(UserInvitation).one()
            assert invitation.status == InvitationStatus.PENDING
            assert db.query(DeletedEmail).count() == 1


@pytest.mark.integration
def test_concurrent_handoff_acceptance_creates_one_admin_and_one_audit():
    raw_token = "example-handoff-token-with-sufficient-entropy-123456789"
    payload = user_management.AcceptInvitationRequest(
        token=raw_token,
        email="admin@example.edu",
        name="First Admin",
    )

    with _isolated_database() as (_, session_factory):
        with session_factory() as db:
            department = Department(
                id="target",
                name="Target Department",
                institution="Example University",
                contact_email="admin@example.edu",
                contact_name="First Admin",
            )
            db.add(department)
            db.add(
                UserInvitation(
                    id="handoff",
                    department_id=department.id,
                    email="admin@example.edu",
                    role=UserRole.ADMIN,
                    token=auth_routes._handoff_token_digest(raw_token),
                    purpose=InvitationPurpose.DEPARTMENT_ADMIN_HANDOFF.value,
                    status=InvitationStatus.PENDING,
                    delivery_queued_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(days=7),
                )
            )
            db.commit()

        def accept():
            with session_factory() as db:
                return asyncio.run(
                    user_management.accept_invitation(
                        payload,
                        Request(
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
                        ),
                        db,
                    )
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: accept(), range(2)))

        assert {result["outcome"] for result in results} == {
            "accepted",
            "already_accepted",
        }
        with session_factory() as db:
            assert db.query(User).count() == 1
            assert db.query(UserInvitation).one().status.value == "accepted"
            acceptance_audits = (
                db.query(AuditLog)
                .filter(AuditLog.action == "user_invite_accepted")
                .all()
            )
            assert len(acceptance_audits) == 1
