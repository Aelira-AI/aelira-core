"""Atomic, replay-tolerant refresh-token rotation."""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import os
import uuid

import bcrypt
import jwt
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.auth.jwt_service import JWTService
from src.auth.session_service import SessionService
from src.config.settings import Settings
from src.db.models import Base, Department, User, UserRole, UserSession


class _Query:
    def __init__(self, db, model):
        self.db = db
        self.model = model

    def filter(self, *_args):
        return self

    def order_by(self, *columns):
        self.db.orderings.append((self.model, columns))
        return self

    def limit(self, value):
        self.db.limits.append((self.model, value))
        self._limit = value
        return self

    def with_for_update(self):
        self.db.locked_models.append(self.model)
        self._locked = True
        return self

    def first(self):
        rows = self.db.rows.get(self.model)
        return rows[0] if isinstance(rows, list) and rows else rows

    def all(self):
        rows = self.db.rows.get(self.model)
        result = (
            rows if isinstance(rows, list) else ([rows] if rows is not None else [])
        )
        result = result[: self._limit] if hasattr(self, "_limit") else result
        if getattr(self, "_locked", False):
            self.db.locked_row_counts.append((self.model, len(result)))
        return result


class _DB:
    def __init__(self, session, user):
        self.rows = {UserSession: session, User: user}
        self.locked_models = []
        self.locked_row_counts = []
        self.orderings = []
        self.limits = []
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        return _Query(self, model)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _service():
    service = SessionService()
    service.settings = SimpleNamespace(
        session_refresh_grace_seconds=10,
        session_legacy_refresh_candidate_limit=5,
        session_replay_encryption_key=Fernet.generate_key().decode(),
    )
    return service


def _user():
    return User(
        id="user-1",
        email="user@example.edu",
        department_id="department-1",
        role=UserRole.FACULTY,
        is_active=True,
    )


def _session(service, raw_token):
    return UserSession(
        id="session-1",
        user_id="user-1",
        refresh_token_hash=bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode(),
        access_token_jti="old-jti",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )


def _legacy_refresh_token(service, session_id="legacy-session"):
    token, raw_token, _expires_at = service.jwt_service.create_refresh_token(
        user_id="user-1", session_id=session_id
    )
    payload = service.jwt_service.decode_token(token)
    payload.pop("sid")
    legacy_token = jwt.encode(
        payload,
        service.jwt_service._private_key,
        algorithm=service.jwt_service.settings.jwt_algorithm,
    )
    return legacy_token, raw_token


def test_refresh_rotation_settings_are_safe_by_default():
    settings = Settings(
        database_url="postgresql://user:secret@localhost/test",
        jwt_secret="test-only-secret",
        env="test",
    )
    assert settings.session_refresh_grace_seconds == 10
    assert settings.session_legacy_refresh_candidate_limit == 5


def test_legacy_refresh_is_bounded_and_checks_previous_hashes(monkeypatch):
    service = _service()
    legacy_token, legacy_raw = _legacy_refresh_token(service)
    candidates = []
    for index in range(service.settings.session_legacy_refresh_candidate_limit):
        candidate = _session(service, f"current-{index}")
        candidate.id = f"session-{index}"
        previous_raw = legacy_raw if index == 4 else f"previous-{index}"
        candidate.previous_refresh_token_hash = bcrypt.hashpw(
            previous_raw.encode(), bcrypt.gensalt()
        ).decode()
        candidates.append(candidate)
    db = _DB(candidates, _user())
    real_checkpw = bcrypt.checkpw
    checks = []

    def counted_checkpw(raw, hashed):
        checks.append((raw, hashed))
        return real_checkpw(raw, hashed)

    monkeypatch.setattr("src.auth.session_service.bcrypt.checkpw", counted_checkpw)

    assert service.refresh_session(db, legacy_token) is None

    limit = service.settings.session_legacy_refresh_candidate_limit
    assert db.limits == [(UserSession, limit)]
    assert db.locked_row_counts == [(UserSession, limit)]
    assert db.orderings and db.orderings[0][0] is UserSession
    assert len(checks) <= 2 * limit
    assert candidates[-1].revoked_at is not None


def test_legacy_refresh_rotates_then_replays_exact_pair_once():
    service = _service()
    legacy_token, legacy_raw = _legacy_refresh_token(service)
    session = _session(service, legacy_raw)
    session.id = "legacy-session"
    db = _DB([session], _user())

    first = service.refresh_session(db, legacy_token)
    second = service.refresh_session(db, legacy_token)
    third = service.refresh_session(db, legacy_token)

    assert first is not None
    assert second == first
    assert third is None
    assert session.revoked_at is not None


def test_production_requires_valid_replay_encryption_key():
    with pytest.raises(ValueError, match="SESSION_REPLAY_ENCRYPTION_KEY"):
        Settings(
            database_url="postgresql://user:secret@localhost/test",
            jwt_secret="test-only-secret",
            env="production",
            allow_mock_auth=False,
            session_replay_encryption_key="not-a-fernet-key",
        )


def test_user_session_model_exposes_refresh_replay_state():
    assert UserSession.previous_refresh_token_hash.nullable is True
    assert UserSession.refresh_grace_expires_at.nullable is True
    assert UserSession.refresh_replay_used_at.nullable is True
    assert UserSession.refresh_replay_ciphertext.nullable is True


def test_refresh_jwt_is_bound_to_session_id():
    service = JWTService()

    token, _raw, _expires_at = service.create_refresh_token(
        user_id="user-1", session_id="session-1"
    )

    payload = service.verify_refresh_token(token)
    assert payload is not None
    assert payload["sid"] == "session-1"


def test_current_refresh_rotates_one_locked_row_once():
    service = _service()
    old_token, old_raw, _ = service.jwt_service.create_refresh_token(
        user_id="user-1", session_id="session-1"
    )
    session = _session(service, old_raw)
    old_hash = session.refresh_token_hash
    db = _DB(session, _user())

    result = service.refresh_session(db, old_token, "203.0.113.1", "pytest")

    assert result is not None
    access_token, refresh_token, _access_exp, refresh_exp = result
    assert db.locked_models == [UserSession]
    assert db.added == []
    assert db.commits == 1
    assert session.id == "session-1"
    assert session.previous_refresh_token_hash == old_hash
    assert session.refresh_token_hash != old_hash
    assert session.access_token_jti != "old-jti"
    assert session.expires_at == refresh_exp
    assert session.refresh_replay_ciphertext
    assert service.jwt_service.verify_access_token(access_token)["sid"] == "session-1"
    assert service.jwt_service.verify_refresh_token(refresh_token)["sid"] == "session-1"


def test_previous_refresh_replays_exact_pair_once_then_revokes(monkeypatch):
    service = _service()
    token_a, raw_a, _ = service.jwt_service.create_refresh_token(
        user_id="user-1", session_id="session-1"
    )
    session = _session(service, raw_a)
    db = _DB(session, _user())
    real_checkpw = bcrypt.checkpw
    checks = []

    def counted_checkpw(raw, hashed):
        checks.append((raw, hashed))
        return real_checkpw(raw, hashed)

    monkeypatch.setattr("src.auth.session_service.bcrypt.checkpw", counted_checkpw)

    first = service.refresh_session(db, token_a)
    first_count = len(checks)
    second = service.refresh_session(db, token_a)
    second_count = len(checks) - first_count
    current_hash_after_rotation = session.refresh_token_hash
    current_jti_after_rotation = session.access_token_jti
    third = service.refresh_session(db, token_a)
    third_count = len(checks) - first_count - second_count

    assert first is not None
    assert second == first
    assert third is None
    assert session.refresh_replay_used_at is not None
    assert session.refresh_token_hash == current_hash_after_rotation
    assert session.access_token_jti == current_jti_after_rotation
    assert session.revoked_at is not None
    assert (first_count, second_count, third_count) == (1, 2, 2)


def test_previous_refresh_after_grace_revokes_session():
    service = _service()
    token_a, raw_a, _ = service.jwt_service.create_refresh_token(
        user_id="user-1", session_id="session-1"
    )
    session = _session(service, raw_a)
    db = _DB(session, _user())
    assert service.refresh_session(db, token_a) is not None
    session.refresh_grace_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert service.refresh_session(db, token_a) is None
    assert session.revoked_at is not None


def test_token_older_than_immediately_previous_revokes_session():
    service = _service()
    token_a, raw_a, _ = service.jwt_service.create_refresh_token(
        user_id="user-1", session_id="session-1"
    )
    session = _session(service, raw_a)
    db = _DB(session, _user())
    first = service.refresh_session(db, token_a)
    assert first is not None
    token_b = first[1]
    assert service.refresh_session(db, token_b) is not None

    assert service.refresh_session(db, token_a) is None
    assert session.revoked_at is not None


@pytest.mark.parametrize("state", ["revoked", "expired", "inactive"])
def test_unusable_session_or_user_cannot_refresh(state):
    service = _service()
    token, raw, _ = service.jwt_service.create_refresh_token(
        user_id="user-1", session_id="session-1"
    )
    session = _session(service, raw)
    user = _user()
    if state == "revoked":
        session.revoked_at = datetime.now(timezone.utc)
    elif state == "expired":
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    else:
        user.is_active = False
    db = _DB(session, user)

    assert service.refresh_session(db, token) is None
    if state == "inactive":
        assert session.revoked_at is not None


def test_encryption_error_rolls_back_without_rotating_row(caplog):
    service = _service()
    token, raw, _ = service.jwt_service.create_refresh_token(
        user_id="user-1", session_id="session-1"
    )
    session = _session(service, raw)
    old_hash = session.refresh_token_hash
    db = _DB(session, _user())
    failure_detail = "LOG_CANARY_REFRESH_FAILURE"
    service._replay_cipher = SimpleNamespace(
        encrypt=lambda _value: (_ for _ in ()).throw(RuntimeError(failure_detail))
    )

    with caplog.at_level("DEBUG"):
        assert service.refresh_session(db, token) is None
    assert db.rollbacks == 1
    assert db.commits == 0
    assert session.refresh_token_hash == old_hash
    assert failure_detail not in caplog.text
    assert token not in caplog.text
    assert "RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for real PostgreSQL lock verification",
)
def test_concurrent_refreshes_serialize_and_return_identical_pair():
    database_url = os.environ["TEST_DATABASE_URL"]
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    service = SessionService()
    barrier = Barrier(2)
    suffix = uuid.uuid4().hex[:12]
    department_id = f"dept-{suffix}"
    user_id = f"user-{suffix}"
    session_id = f"sess-{suffix}"
    token_a, raw_a, refresh_exp = service.jwt_service.create_refresh_token(
        user_id=user_id, session_id=session_id
    )

    with session_factory() as setup_db:
        setup_db.add(
            Department(
                id=department_id,
                name="Refresh Lock Test",
                institution="Test",
                contact_email=f"{suffix}@example.edu",
            )
        )
        setup_db.add(
            User(
                id=user_id,
                email=f"{suffix}@example.edu",
                name="Refresh Lock Test",
                department_id=department_id,
                role=UserRole.FACULTY,
                is_active=True,
            )
        )
        setup_db.add(
            UserSession(
                id=session_id,
                user_id=user_id,
                refresh_token_hash=bcrypt.hashpw(
                    raw_a.encode(), bcrypt.gensalt(rounds=12)
                ).decode(),
                access_token_jti="initial-jti",
                expires_at=refresh_exp,
            )
        )
        setup_db.commit()

    def refresh_once():
        with session_factory() as db:
            barrier.wait(timeout=10)
            return service.refresh_session(db, token_a)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: refresh_once(), range(2)))

        assert results[0] is not None
        assert results[1] == results[0]
        with session_factory() as verify_db:
            row = (
                verify_db.query(UserSession).filter(UserSession.id == session_id).one()
            )
            assert row.refresh_replay_used_at is not None
            assert row.revoked_at is None
            assert service.refresh_session(verify_db, token_a) is None
            assert row.revoked_at is not None
    finally:
        with session_factory() as cleanup_db:
            cleanup_db.query(UserSession).filter(UserSession.id == session_id).delete()
            cleanup_db.query(User).filter(User.id == user_id).delete()
            cleanup_db.query(Department).filter(Department.id == department_id).delete()
            cleanup_db.commit()
        engine.dispose()
