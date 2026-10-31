"""Tests for Authenticator App (TOTP) MFA engine and API routes (#907)."""

from __future__ import annotations

import time
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker, Session

from src.api.main import app
from src.auth.totp_service import TOTPEngine
from src.auth.dependencies import get_authenticated_principal, AuthenticatedPrincipal
from src.db.database import get_db_dependency
from src.db.models import User, UserRole, TOTPCredential, UserSession


@pytest.fixture
def test_db():
    """Create clean in-memory SQLite database session with User and TOTPCredential tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine)
    UserSession.__table__.create(engine)
    TOTPCredential.__table__.create(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def test_user(test_db: Session) -> User:
    """Create an active test user for TOTP authentication tests."""
    user = User(
        id=str(uuid.uuid4()),
        email="faculty.totp@university.edu",
        name="Faculty TOTP User",
        role=UserRole.FACULTY,
        department_id=str(uuid.uuid4()),
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def test_totp_engine_generate_and_verify() -> None:
    """Verify standard RFC 6238 TOTP generation and verification."""
    secret = TOTPEngine.generate_secret()
    assert len(secret) >= 26
    assert secret.isalnum()

    # Code at current time
    now = time.time()
    code = TOTPEngine.generate_code(secret, for_time=now)
    assert len(code) == 6
    assert code.isdigit()

    # Verify at same timestamp
    assert TOTPEngine.verify_code(secret, code, window=1, for_time=now)

    # Verify with 1 time-step drift (30 seconds earlier or later)
    assert TOTPEngine.verify_code(secret, code, window=1, for_time=now + 28)
    assert TOTPEngine.verify_code(secret, code, window=1, for_time=now - 28)

    # Fails with 2 time-steps drift (65 seconds later)
    assert not TOTPEngine.verify_code(secret, code, window=1, for_time=now + 65)

    # Fails for wrong code
    assert not TOTPEngine.verify_code(
        secret, "000000" if code != "000000" else "111111", for_time=now
    )


def test_totp_engine_backup_codes() -> None:
    """Verify single-use backup recovery code generation, verification, and consumption."""
    plain_codes, hashed_codes = TOTPEngine.generate_backup_codes(count=8)
    assert len(plain_codes) == 8
    assert len(hashed_codes) == 8

    # Verify and consume the first code
    first_code = plain_codes[0]
    matched, remaining = TOTPEngine.verify_and_consume_backup_code(
        hashed_codes, first_code
    )
    assert matched is True
    assert len(remaining) == 7

    # Attempting to reuse the consumed code fails
    matched_again, still_remaining = TOTPEngine.verify_and_consume_backup_code(
        remaining, first_code
    )
    assert matched_again is False
    assert len(still_remaining) == 7

    # Invalid code fails
    matched_invalid, _ = TOTPEngine.verify_and_consume_backup_code(
        remaining, "invalid-code"
    )
    assert matched_invalid is False


def test_totp_enroll_setup_and_verify(test_db: Session, test_user: User) -> None:
    """Verify end-to-end enrollment setup and confirmation ceremony."""
    # Override authenticated principal
    principal = AuthenticatedPrincipal(
        api_key=None,
        user_id=test_user.id,
        department_id=test_user.department_id,
        user_role=test_user.role,
        auth_method="session",
    )
    app.dependency_overrides[get_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_db_dependency] = lambda: test_db

    try:
        client = TestClient(app)

        # 1. Setup enrollment
        setup_resp = client.post("/auth/totp/enroll/setup")
        assert setup_resp.status_code == 200
        setup_data = setup_resp.json()
        assert setup_data["success"] is True
        assert "secret" in setup_data
        assert "otpauth_uri" in setup_data
        secret = setup_data["secret"]

        # 2. Confirm enrollment with invalid code fails
        fail_resp = client.post("/auth/totp/enroll/verify", json={"code": "000000"})
        assert fail_resp.status_code == 400

        # 3. Confirm enrollment with valid 6-digit code
        valid_code = TOTPEngine.generate_code(secret)
        confirm_resp = client.post(
            "/auth/totp/enroll/verify", json={"code": valid_code}
        )
        assert confirm_resp.status_code == 200
        confirm_data = confirm_resp.json()
        assert confirm_data["success"] is True
        assert confirm_data["status"] == "confirmed"
        assert len(confirm_data["backup_codes"]) == 8

        # 4. Check status
        status_resp = client.get("/auth/totp/status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["enrolled"] is True
        assert status_data["remaining_backup_codes"] == 8

        # 5. Verify login with 6-digit TOTP code
        login_code = TOTPEngine.generate_code(secret)
        login_resp = client.post(
            "/auth/totp/login/verify",
            json={"email": test_user.email, "code": login_code},
        )
        assert login_resp.status_code == 200
        assert "aelira_access" in login_resp.cookies
        assert "aelira_refresh" in login_resp.cookies
        assert login_resp.json()["method"] == "totp"

        # 6. Verify login with single-use backup code
        backup_code = confirm_data["backup_codes"][0]
        backup_login_resp = client.post(
            "/auth/totp/login/verify",
            json={"email": test_user.email, "code": backup_code},
        )
        assert backup_login_resp.status_code == 200
        assert backup_login_resp.json()["method"] == "backup_code"

        # Reusing the same backup code fails
        reuse_resp = client.post(
            "/auth/totp/login/verify",
            json={"email": test_user.email, "code": backup_code},
        )
        assert reuse_resp.status_code == 401

        # 7. Revoke TOTP MFA
        revoke_code = TOTPEngine.generate_code(secret)
        revoke_resp = client.request(
            "DELETE", "/auth/totp/revoke", json={"code": revoke_code}
        )
        assert revoke_resp.status_code == 200

        # Status is now enrolled = False
        post_revoke_status = client.get("/auth/totp/status")
        assert post_revoke_status.json()["enrolled"] is False

    finally:
        app.dependency_overrides.clear()


def test_totp_routes_do_not_generate_api_keys() -> None:
    """Security invariant: login paths must never mint or return API keys."""
    import inspect
    from src.api import totp_routes

    source = inspect.getsource(totp_routes)
    assert "create_api_key" not in source
    assert (
        "api_key" not in source.lower() or "for programmatic access" in source.lower()
    )
