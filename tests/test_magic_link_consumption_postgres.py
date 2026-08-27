"""PostgreSQL proof that one-time magic links have one verification winner."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock
import os
import uuid

import bcrypt
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.auth.session_service import SessionService
from src.db.models import Base, MagicLink

EXPECTED_ISOLATION_LEVEL = "READ COMMITTED"


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

    schema = f"magic_link_{uuid.uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        database_url,
        isolation_level=EXPECTED_ISOLATION_LEVEL,
        pool_size=2,
        max_overflow=0,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    Base.metadata.create_all(engine, tables=[MagicLink.__table__])
    try:
        yield engine, sessionmaker(bind=engine)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _insert_link(
    session_factory,
    *,
    token: str,
    expired: bool = False,
    token_hash: str | None = None,
):
    email = f"person-{uuid.uuid4().hex}@example.edu"
    link_id = str(uuid.uuid4())
    with session_factory() as db:
        db.add(
            MagicLink(
                id=link_id,
                email=email,
                token_hash=token_hash
                or bcrypt.hashpw(
                    token.encode("utf-8"), bcrypt.gensalt(rounds=4)
                ).decode("utf-8"),
                expires_at=datetime.now(timezone.utc)
                + (timedelta(minutes=-1) if expired else timedelta(minutes=5)),
            )
        )
        db.commit()
    return link_id, email


@pytest.mark.integration
def test_concurrent_verification_consumes_magic_link_once():
    with _isolated_database() as (engine, session_factory):
        service = SessionService.__new__(SessionService)
        token = f"token-{uuid.uuid4().hex}"
        link_id, email = _insert_link(session_factory, token=token)
        update_barrier = Barrier(2)
        arrivals_lock = Lock()
        update_backend_pids = set()

        def synchronize_claim_updates(
            connection, cursor, statement, parameters, context, executemany
        ):
            normalized = " ".join(statement.lower().split())
            if (
                normalized.startswith("update magic_links")
                and "magic_links.used_at is null" in normalized
                and not connection.info.get("magic_link_claim_arrived")
            ):
                connection.info["magic_link_claim_arrived"] = True
                with arrivals_lock:
                    update_backend_pids.add(connection.info["magic_link_backend_pid"])
                update_barrier.wait(timeout=10)

        event.listen(engine, "before_cursor_execute", synchronize_claim_updates)

        def verify_once():
            with session_factory() as db:
                connection = db.connection()
                backend_pid = connection.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar_one()
                connection.info["magic_link_backend_pid"] = backend_pid
                isolation_level = connection.get_isolation_level()
                db.execute(text("SET LOCAL lock_timeout = '10s'"))
                link = service.verify_magic_link(db, email, token)
                return link.id if link else None, backend_pid, isolation_level

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _index: verify_once(), range(2)))
        finally:
            event.remove(engine, "before_cursor_execute", synchronize_claim_updates)

        claims = [result[0] for result in results]
        backend_pids = {result[1] for result in results}
        isolation_levels = {result[2] for result in results}
        assert claims.count(link_id) == 1
        assert claims.count(None) == 1
        assert backend_pids == update_backend_pids
        assert len(backend_pids) == 2
        assert isolation_levels == {EXPECTED_ISOLATION_LEVEL}

        with session_factory() as db:
            row = db.get(MagicLink, link_id)
            assert row.used_at is not None
            assert service.verify_magic_link(db, email, token) is None


@pytest.mark.integration
def test_wrong_and_expired_tokens_remain_unused():
    with _isolated_database() as (_, session_factory):
        service = SessionService.__new__(SessionService)
        valid_token = f"token-{uuid.uuid4().hex}"
        valid_id, valid_email = _insert_link(session_factory, token=valid_token)
        expired_token = f"token-{uuid.uuid4().hex}"
        expired_id, expired_email = _insert_link(
            session_factory, token=expired_token, expired=True
        )

        with session_factory() as db:
            assert service.verify_magic_link(db, valid_email, "wrong-token") is None
            assert service.verify_magic_link(db, expired_email, expired_token) is None
            assert db.get(MagicLink, valid_id).used_at is None
            assert db.get(MagicLink, expired_id).used_at is None


@pytest.mark.integration
def test_malformed_stored_hash_is_treated_as_invalid():
    with _isolated_database() as (_, session_factory):
        service = SessionService.__new__(SessionService)
        link_id, email = _insert_link(
            session_factory, token="token", token_hash="malformed-hash"
        )

        with session_factory() as db:
            assert service.verify_magic_link(db, email, "token") is None
            assert db.get(MagicLink, link_id).used_at is None


@pytest.mark.integration
def test_unexpected_bcrypt_error_propagates(monkeypatch):
    with _isolated_database() as (_, session_factory):
        service = SessionService.__new__(SessionService)
        link_id, email = _insert_link(session_factory, token="token")

        def fail_verification(*_args):
            raise RuntimeError("forced verifier failure")

        monkeypatch.setattr(bcrypt, "checkpw", fail_verification)
        with session_factory() as db:
            with pytest.raises(RuntimeError, match="forced verifier failure"):
                service.verify_magic_link(db, email, "token")

        with session_factory() as db:
            assert db.get(MagicLink, link_id).used_at is None


@pytest.mark.integration
def test_scanner_check_does_not_consume_valid_magic_link():
    with _isolated_database() as (_, session_factory):
        service = SessionService.__new__(SessionService)
        token = f"token-{uuid.uuid4().hex}"
        link_id, email = _insert_link(session_factory, token=token)

        with session_factory() as db:
            assert service.check_magic_link(db, email, token) is True
            assert db.get(MagicLink, link_id).used_at is None
