"""PostgreSQL proof that first-administrator bootstrap is atomic."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import MagicMock
import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.auth.session_service import SessionService
from src.db.models import Base, DeletedEmail, Department, User, UserRole


def _postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url and os.getenv("CI", "").lower() == "true":
        database_url = os.getenv("DATABASE_URL")
    return (
        database_url or "postgresql://postgres:postgres@localhost:5432/aelira_test"
    ).replace("postgresql+asyncpg://", "postgresql://")


def _service() -> SessionService:
    service = SessionService.__new__(SessionService)
    service.settings = SimpleNamespace(open_signup=False)
    service._notify_admins_new_signup = MagicMock()
    service._send_welcome_email = MagicMock()
    return service


def _seed_admin(session_factory) -> tuple[str, str]:
    suffix = uuid.uuid4().hex
    department_id = suffix
    email = f"admin-{suffix}@example.edu"
    with session_factory() as db:
        db.add(
            Department(
                id=department_id,
                name="Existing Department",
                institution="Example University",
                contact_email=email,
            )
        )
        user = User(
            email=email,
            name="Existing Admin",
            department_id=department_id,
            role=UserRole.ADMIN,
            email_verified=True,
        )
        db.add(user)
        db.commit()
        return user.id, email


@pytest.mark.integration
def test_application_database_enforces_read_committed_isolation():
    from src.db.database import DATABASE_ISOLATION_LEVEL, engine

    assert DATABASE_ISOLATION_LEVEL == "READ COMMITTED"
    try:
        with engine.connect() as connection:
            assert connection.get_isolation_level() == DATABASE_ISOLATION_LEVEL
    except Exception as exc:
        pytest.skip(f"disposable PostgreSQL unavailable: {exc}")


@contextmanager
def _isolated_database():
    database_url = _postgres_url()
    try:
        admin_engine = create_engine(database_url)
        with admin_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"disposable PostgreSQL unavailable: {exc}")

    schema = f"first_admin_{uuid.uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        database_url,
        pool_size=2,
        max_overflow=0,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    Base.metadata.create_all(
        engine,
        tables=[Department.__table__, User.__table__, DeletedEmail.__table__],
    )
    try:
        yield engine, sessionmaker(bind=engine)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("same_email", [False, True])
def test_concurrent_first_admin_bootstrap_creates_one_workspace(same_email):
    with _isolated_database() as (engine, session_factory):
        service = _service()
        count_barrier = Barrier(2)

        def synchronize_empty_observation(
            connection, cursor, statement, parameters, context, executemany
        ):
            normalized = " ".join(statement.lower().split())
            if (
                normalized.startswith("select count(*)")
                and "from users" in normalized
                and not connection.info.get("first_admin_count_seen")
            ):
                connection.info["first_admin_count_seen"] = True
                count_barrier.wait(timeout=10)

        event.listen(engine, "after_cursor_execute", synchronize_empty_observation)

        first_email = f"first-{uuid.uuid4().hex}@example.edu"
        second_email = (
            first_email if same_email else f"second-{uuid.uuid4().hex}@example.edu"
        )

        def bootstrap(email: str):
            with session_factory() as db:
                db.execute(text("SET LOCAL lock_timeout = '10s'"))
                try:
                    user, is_new = service.get_or_create_user_for_magic_link(db, email)
                    return "ok", is_new, user.id, user.email, user.role
                except ValueError as exc:
                    db.rollback()
                    return "closed", str(exc)
                except Exception as exc:
                    db.rollback()
                    return "error", type(exc).__name__, str(exc)

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(bootstrap, [first_email, second_email]))

            statuses = sorted(result[0] for result in results)
            if same_email:
                assert statuses == ["ok", "ok"], results
                assert sorted(result[1] for result in results) == [False, True]
                assert len({result[2] for result in results}) == 1
            else:
                assert statuses == ["closed", "ok"], results

            with session_factory() as db:
                users = db.query(User).all()
                departments = db.query(Department).all()
                assert len(users) == 1
                assert len(departments) == 1
                assert users[0].role == UserRole.ADMIN
                assert users[0].department_id == departments[0].id
        finally:
            event.remove(engine, "after_cursor_execute", synchronize_empty_observation)


@pytest.mark.integration
def test_bootstrap_user_insert_failure_rolls_back_department():
    with _isolated_database() as (engine, session_factory):
        service = _service()

        def fail_user_insert(
            connection, cursor, statement, parameters, context, executemany
        ):
            if statement.lstrip().lower().startswith("insert into users"):
                raise RuntimeError("forced user insert failure")

        event.listen(engine, "before_cursor_execute", fail_user_insert)
        try:
            with session_factory() as db:
                with pytest.raises(RuntimeError, match="forced user insert failure"):
                    service.get_or_create_user_for_magic_link(
                        db, f"failure-{uuid.uuid4().hex}@example.edu"
                    )
                db.rollback()
        finally:
            event.remove(engine, "before_cursor_execute", fail_user_insert)

        with session_factory() as db:
            assert db.query(User).count() == 0
            assert db.query(Department).count() == 0


@pytest.mark.integration
def test_populated_closed_deployment_rejects_unknown_user_without_inserts():
    with _isolated_database() as (_, session_factory):
        _seed_admin(session_factory)
        service = _service()

        with session_factory() as db:
            with pytest.raises(ValueError, match="Account provisioning is closed"):
                service.get_or_create_user_for_magic_link(
                    db, f"unknown-{uuid.uuid4().hex}@example.edu"
                )
            assert db.query(User).count() == 1
            assert db.query(Department).count() == 1


@pytest.mark.integration
def test_populated_open_signup_still_creates_faculty_workspace():
    with _isolated_database() as (_, session_factory):
        _seed_admin(session_factory)
        service = _service()
        service.settings.open_signup = True
        email = f"faculty-{uuid.uuid4().hex}@example.edu"

        with session_factory() as db:
            user, is_new = service.get_or_create_user_for_magic_link(db, email)
            assert is_new is True
            assert user.role == UserRole.FACULTY

        with session_factory() as db:
            assert db.query(User).count() == 2
            assert db.query(Department).count() == 2
            department = db.query(Department).filter_by(contact_email=email).one()
            assert department.tier == "individual"


@pytest.mark.integration
def test_existing_user_login_returns_same_account_without_duplicates():
    with _isolated_database() as (_, session_factory):
        user_id, email = _seed_admin(session_factory)
        service = _service()

        with session_factory() as db:
            user, is_new = service.get_or_create_user_for_magic_link(db, email)
            assert is_new is False
            assert user.id == user_id
            assert db.query(User).count() == 1
            assert db.query(Department).count() == 1
