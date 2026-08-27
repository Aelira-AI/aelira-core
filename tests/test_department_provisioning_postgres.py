"""PostgreSQL proof that department provisioning and its audit are atomic."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.api import auth_routes
from src.db.models import AuditLog, Base, Department, User


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
        tables=[Department.__table__, User.__table__, AuditLog.__table__],
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
    monkeypatch.setattr(
        auth_routes,
        "get_email_service",
        lambda: SimpleNamespace(send_email=AsyncMock(return_value={"success": True})),
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
            assert len(departments) == 1
            assert len(audits) == 1
            assert audits[0].resource_id == department_id
            assert audits[0].action == "department_provision"
            assert audits[0].status == "success"


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
            assert db.query(AuditLog).count() == 0
