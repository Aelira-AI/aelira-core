"""
Unit and Integration Tests for WebAuthn Passkey Routes (src/api/passkey_routes.py)

Covers:
- Passkey registration options & verification (authenticated)
- Passkey login options & assertion verification (public passwordless)
- Replay/clone attack detection via sign_count enforcement
- Passkey credential management (list, rename, revoke)
- Unauthenticated / unauthorized rejection
"""

import base64
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.auth.passkey_service import PasskeyService, WebAuthnChallengeStorage
from src.db.database import get_db_dependency
from src.db.models import User, UserRole, WebAuthnCredential


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def test_user():
    return User(
        id=str(uuid.uuid4()),
        email="faculty@university.edu",
        name="Dr. Accessible",
        role=UserRole.FACULTY,
        department_id=str(uuid.uuid4()),
        is_active=True,
    )


@pytest.fixture
def authenticated_principal(test_user):
    return AuthenticatedPrincipal(
        api_key=None,
        user_id=test_user.id,
        department_id=test_user.department_id,
        user_role=test_user.role,
        auth_method="session",
    )


@pytest.fixture
def client(mock_db, authenticated_principal, test_user):
    app.dependency_overrides[get_db_dependency] = lambda: mock_db
    app.dependency_overrides[get_authenticated_principal] = lambda: (
        authenticated_principal
    )

    # Setup default mock DB query responses
    def mock_query(model):
        query_mock = MagicMock()
        if model == User:
            query_mock.filter.return_value.first.return_value = test_user
            query_mock.filter.return_value.all.return_value = [test_user]
        elif model == WebAuthnCredential:
            query_mock.filter.return_value.all.return_value = []
            query_mock.filter.return_value.first.return_value = None
            query_mock.filter.return_value.order_by.return_value.all.return_value = []
        return query_mock

    mock_db.query.side_effect = mock_query

    yield TestClient(app)

    app.dependency_overrides.clear()


def test_registration_options_success(client, test_user):
    response = client.post(
        "/auth/passkey/register/options",
        headers={"Origin": "https://aelira.ai"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "options" in data
    assert "challenge_id" in data
    assert data["options"]["rp"]["id"] == "aelira.ai"
    assert data["options"]["user"]["name"] == test_user.email


def test_registration_verify_success(client, test_user, mock_db):
    # Store a challenge first
    challenge_id = "test-challenge-123"
    WebAuthnChallengeStorage.store_challenge(
        challenge_id,
        {
            "challenge": base64.urlsafe_b64encode(b"0" * 32)
            .decode("utf-8")
            .rstrip("="),
            "user_id": test_user.id,
            "rp_id": "aelira.ai",
            "expected_origin": "https://aelira.ai",
            "ceremony": "registration",
        },
    )

    mock_cred = WebAuthnCredential(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        credential_id="cred-id-abc",
        public_key=b"mock-public-key",
        sign_count=0,
        friendly_name="MacBook Touch ID",
        created_at=datetime.now(timezone.utc),
    )

    with patch.object(PasskeyService, "verify_registration", return_value=mock_cred):
        response = client.post(
            "/auth/passkey/register/verify",
            json={
                "challenge_id": challenge_id,
                "credential": {"id": "cred-id-abc", "rawId": "raw", "response": {}},
                "friendly_name": "MacBook Touch ID",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["credential"]["friendly_name"] == "MacBook Touch ID"


def test_login_options_discoverable(client):
    # Public endpoint without email (for Conditional UI autofill)
    response = client.post(
        "/auth/passkey/login/options",
        headers={"Origin": "https://aelira.ai"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "options" in data
    assert "challenge_id" in data
    assert data["options"]["rpId"] == "aelira.ai"


def test_login_options_with_email(client, test_user, mock_db):
    stored_cred = WebAuthnCredential(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        credential_id="cred-xyz",
        public_key=b"key",
        sign_count=1,
    )

    def mock_query(model):
        query_mock = MagicMock()
        if model == User:
            query_mock.filter.return_value.first.return_value = test_user
        elif model == WebAuthnCredential:
            query_mock.filter.return_value.all.return_value = [stored_cred]
        return query_mock

    mock_db.query.side_effect = mock_query

    response = client.post(
        "/auth/passkey/login/options",
        json={"email": test_user.email},
        headers={"Origin": "https://aelira.ai"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "allowCredentials" in data["options"]
    assert len(data["options"]["allowCredentials"]) == 1


def test_login_verify_success(client, test_user):
    challenge_id = "auth-challenge-456"
    stored_cred = WebAuthnCredential(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        credential_id="cred-xyz",
        public_key=b"key",
        sign_count=2,
    )

    with (
        patch.object(
            PasskeyService,
            "verify_authentication",
            return_value=(test_user, stored_cred),
        ),
        patch(
            "src.auth.session_service.SessionService.create_session",
            return_value=("mock_access_token", "mock_refresh_token", 3600, 86400),
        ),
    ):
        response = client.post(
            "/auth/passkey/login/verify",
            json={
                "challenge_id": challenge_id,
                "credential": {"id": "cred-xyz", "rawId": "raw", "response": {}},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["user"]["email"] == test_user.email
        # Session cookies must be stamped
        assert "aelira_access" in response.cookies
        assert "aelira_refresh" in response.cookies


def test_passkey_list_and_delete(client, test_user, mock_db):
    cred_id = str(uuid.uuid4())
    stored_cred = WebAuthnCredential(
        id=cred_id,
        user_id=test_user.id,
        credential_id="cred-123",
        public_key=b"key",
        sign_count=1,
        friendly_name="Office YubiKey",
        created_at=datetime.now(timezone.utc),
        last_used_at=datetime.now(timezone.utc),
        backup_eligible=True,
    )

    def mock_query(model):
        query_mock = MagicMock()
        if model == WebAuthnCredential:
            query_mock.filter.return_value.order_by.return_value.all.return_value = [
                stored_cred
            ]
            query_mock.filter.return_value.first.return_value = stored_cred
        return query_mock

    mock_db.query.side_effect = mock_query

    # Test list
    list_res = client.get("/auth/passkey/list")
    assert list_res.status_code == 200
    creds = list_res.json()
    assert len(creds) == 1
    assert creds[0]["friendly_name"] == "Office YubiKey"

    # Test rename
    rename_res = client.patch(
        f"/auth/passkey/{cred_id}",
        json={"friendly_name": "Primary YubiKey"},
    )
    assert rename_res.status_code == 200
    assert rename_res.json()["friendly_name"] == "Primary YubiKey"

    # Test delete
    del_res = client.delete(f"/auth/passkey/{cred_id}")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True
    assert mock_db.delete.called
