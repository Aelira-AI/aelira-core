"""
Tests for src/api/auth_routes.py (issue #35).

Covers the API-key management, magic-link, and session/logout flows named
in the issue: happy paths, auth/authz enforcement, input validation, and
handler-level error mapping (service failure -> the specific 4xx/5xx the
handler itself returns, not an unhandled exception).

Mocking strategy (mirrors tests/test_readiness.py's dependency_overrides
pattern, extended for auth_routes' extra collaborators):

- The DB dependency is overridden via app.dependency_overrides[get_db_dependency]
  with a MagicMock session (`_db_with`) that never touches Postgres. Per-model
  query results are supplied via `db.query.side_effect`, keyed by the
  SQLAlchemy model class, matching what each handler actually queries.
- auth_routes.py imports its collaborators by name (`from ..x import y`),
  which binds a new name in auth_routes's own module namespace. Patching the
  origin module (`x.y`) would not be visible there, so collaborators used
  directly by auth_routes handlers (get_session_service, get_jwt_service,
  get_email_service, get_quota_status, check_signup_abuse, log_signup) are
  monkeypatched on `src.api.auth_routes` itself.
- AuthService.validate_api_key / create_api_key / list_api_keys /
  revoke_api_key and RateLimiter.check_rate_limit are patched on the class
  objects (src.auth.auth_service.AuthService / RateLimiter). Both
  auth_routes.py and auth/dependencies.py reference the *same* class object,
  so patching the class covers get_current_api_key (Bearer-only) and
  get_required_api_key (Bearer + session-cookie) identically.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.main import app
from src.db.database import get_db_dependency
from src.db.models import (
    APIKey,
    DeletedEmail,
    Department,
    User,
    UserRole,
    UserSession,
)
from src.auth.dependencies import AuthenticatedPrincipal
from src.auth.auth_service import AuthService, RateLimiter
import src.api.auth_routes as auth_routes
from src.security.abuse_detector import AbuseCheckResult

# ==================== Shared fixtures / helpers ====================


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Each test starts from a clean dependency_overrides state (test_readiness.py pattern)."""
    app.dependency_overrides.pop(get_db_dependency, None)
    yield
    app.dependency_overrides.pop(get_db_dependency, None)


def _db_with(mapping=None):
    """A MagicMock DB session whose db.query(Model) returns a chained mock
    reflecting `mapping[Model]` (a dict with any of "first"/"all"/"count").

    Any model not named in `mapping` yields .first() -> None, .all() -> [].
    db.refresh(obj) backfills obj.created_at with now() when it is still
    None, standing in for the server_default the real Postgres column would
    have assigned on commit.
    """
    mapping = mapping or {}

    def _query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.filter_by.return_value = q
        q.order_by.return_value = q
        spec = mapping.get(model, {})
        q.first.return_value = spec.get("first")
        q.all.return_value = spec.get("all", [])
        q.count.return_value = spec.get("count", len(spec.get("all", [])))
        return q

    db = MagicMock()
    db.query.side_effect = _query

    def _refresh(obj):
        # SQLAlchemy Column(default=...) / server_default only fire on a real
        # flush against an engine; a handful of handlers (e.g. create_department,
        # L459-582) rely on that to populate `id`/`created_at` before building
        # their response, so a mocked db.refresh() has to backfill them too or
        # the response model's required fields come back None.
        if getattr(obj, "id", None) is None:
            try:
                obj.id = str(uuid.uuid4())
            except Exception:
                pass
        if getattr(obj, "created_at", None) is None:
            try:
                obj.created_at = datetime.now(timezone.utc)
            except Exception:
                pass

    db.refresh.side_effect = _refresh
    return db


@pytest.fixture
def mock_db():
    """Default empty DB (every query -> None/[]), installed as the override."""
    db = _db_with()
    app.dependency_overrides[get_db_dependency] = lambda: db
    return db


def _use_db(db):
    app.dependency_overrides[get_db_dependency] = lambda: db


def _fake_api_key(**overrides):
    key = MagicMock()
    key.id = overrides.get("id", "key-1")
    key.user_id = overrides.get("user_id", "user-1")
    key.department_id = overrides.get("department_id", "dept-1")
    key.name = overrides.get("name", "Test Key")
    key.key_prefix = overrides.get("key_prefix", "aelira_live_ab")
    key.rate_limit_per_hour = overrides.get("rate_limit_per_hour", 100)
    key.created_at = overrides.get("created_at", datetime.now(timezone.utc))
    key.last_used_at = overrides.get("last_used_at", None)
    key.expires_at = overrides.get("expires_at", None)
    key.is_active = overrides.get("is_active", True)
    key.user = overrides.get(
        "user",
        MagicMock(
            spec=User,
            id=key.user_id,
            department_id=key.department_id,
            role="faculty",
            is_active=True,
        ),
    )
    return key


@pytest.fixture
def valid_api_key(monkeypatch):
    """Bearer 'valid_test_key' authenticates as this fake key wherever
    get_current_api_key / get_required_api_key run (auth_routes.py L160-258,
    auth/dependencies.py L36-91 both call AuthService.validate_api_key +
    RateLimiter.check_rate_limit)."""
    key = _fake_api_key()
    monkeypatch.setattr(
        AuthService,
        "validate_api_key",
        staticmethod(lambda db, token: key if token == "valid_test_key" else None),
    )
    monkeypatch.setattr(
        RateLimiter, "check_rate_limit", staticmethod(lambda *a, **k: (True, {}))
    )
    return key


AUTH_HEADERS = {"Authorization": "Bearer valid_test_key"}
EDU_EMAIL = "prof@stanford.edu"


def _allow_rate_limit(monkeypatch):
    monkeypatch.setattr(
        RateLimiter, "check_rate_limit", staticmethod(lambda *a, **k: (True, {}))
    )


# ==================== API key management (L264-453) ====================


class TestCreateApiKey:
    """POST /auth/keys (L264-314)."""

    def test_requires_auth(self, client, mock_db):
        # L254-258: no Bearer/cookie -> 401.
        response = client.post("/auth/keys", json={"name": "My Key"})
        assert response.status_code == 401

    def test_missing_name_is_422(self, client, mock_db, valid_api_key):
        # L50: CreateAPIKeyRequest.name has no default -> required field.
        response = client.post("/auth/keys", json={}, headers=AUTH_HEADERS)
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "payload",
        [
            {"name": ""},
            {"name": "x" * 101},
            {"name": "key", "rate_limit_per_hour": 0},
            {"name": "key", "expires_days": 0},
            {"name": "key", "unexpected": "secret"},
        ],
    )
    def test_rejects_unbounded_or_extra_input(
        self, client, mock_db, valid_api_key, payload
    ):
        response = client.post("/auth/keys", json=payload, headers=AUTH_HEADERS)
        assert response.status_code == 422

    def test_happy_path_returns_full_key_once(
        self, client, mock_db, valid_api_key, monkeypatch
    ):
        created = _fake_api_key(id="key-2", name="My Key")
        monkeypatch.setattr(
            AuthService,
            "create_api_key",
            staticmethod(lambda **kw: (created, "aelira_live_fullkey123")),
        )
        response = client.post(
            "/auth/keys", json={"name": "My Key"}, headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        body = response.json()
        # L312: full_key returned once.
        assert body["full_key"] == "aelira_live_fullkey123"
        # L302-311: response is built from the created APIKey's fields.
        assert body["api_key"]["id"] == "key-2"
        assert body["api_key"]["name"] == "My Key"

    def test_session_with_no_existing_keys_creates_only_requested_visible_key(
        self, client, mock_db, monkeypatch
    ):
        from src.auth import dependencies
        from src.auth.dependencies import AuthenticatedPrincipal
        from src.db.models import UserRole

        principal = AuthenticatedPrincipal(
            api_key=None,
            user_id="session-user",
            department_id="session-dept",
            user_role=UserRole.FACULTY,
            auth_method="session",
        )
        monkeypatch.setattr(
            dependencies,
            "resolve_access_token",
            MagicMock(return_value=SimpleNamespace(principal=principal)),
        )
        created = _fake_api_key(
            id="visible-key",
            user_id="session-user",
            department_id="session-dept",
            name="Automation",
        )
        create = MagicMock(return_value=(created, "aelira_live_visible_once"))
        monkeypatch.setattr(AuthService, "create_api_key", create)
        client.cookies.set("aelira_access", "valid-session")

        response = client.post(
            "/auth/keys",
            json={"name": "Automation"},
            headers={"Authorization": "Bearer retired-local-key"},
        )

        assert response.status_code == 200
        assert response.json()["full_key"] == "aelira_live_visible_once"
        assert response.json()["api_key"]["id"] == "visible-key"
        create.assert_called_once()
        assert create.call_args.kwargs["name"] == "Automation"
        assert create.call_args.kwargs["user_id"] == "session-user"


class TestListApiKeys:
    """GET /auth/keys (L317-344)."""

    def test_requires_auth(self, client, mock_db):
        response = client.get("/auth/keys")
        assert response.status_code == 401

    def test_happy_path(self, client, mock_db, valid_api_key, monkeypatch):
        keys = [_fake_api_key(id="key-a"), _fake_api_key(id="key-b")]
        monkeypatch.setattr(
            AuthService,
            "list_api_keys",
            staticmethod(lambda db, user_id, department_id: keys),
        )
        response = client.get("/auth/keys", headers=AUTH_HEADERS)
        assert response.status_code == 200
        # L332-344: one APIKeyResponse per key returned by AuthService.list_api_keys.
        ids = [k["id"] for k in response.json()]
        assert ids == ["key-a", "key-b"]

    def test_valid_session_lists_keys_despite_retired_bearer(
        self, client, mock_db, monkeypatch
    ):
        from src.auth import dependencies
        from src.auth.dependencies import AuthenticatedPrincipal
        from src.db.models import UserRole

        principal = AuthenticatedPrincipal(
            api_key=None,
            user_id="session-user",
            department_id="session-dept",
            user_role=UserRole.FACULTY,
            auth_method="session",
        )
        monkeypatch.setattr(
            dependencies,
            "resolve_access_token",
            MagicMock(return_value=SimpleNamespace(principal=principal)),
        )
        listed = MagicMock(return_value=[])
        monkeypatch.setattr(AuthService, "list_api_keys", listed)
        client.cookies.set("aelira_access", "valid-session")

        response = client.get(
            "/auth/keys",
            headers={"Authorization": "Bearer retired-local-key"},
        )

        assert response.status_code == 200
        assert response.json() == []
        listed.assert_called_once_with(mock_db, "session-user", "session-dept")


class TestRevokeApiKey:
    """DELETE /auth/keys/{key_id} (L347-377)."""

    def test_requires_auth(self, client, mock_db):
        response = client.delete("/auth/keys/some-key-id")
        assert response.status_code == 401

    def test_not_found_returns_404(self, client, mock_db, valid_api_key, monkeypatch):
        # L361-367: AuthService.revoke_api_key returning False -> 404.
        monkeypatch.setattr(
            AuthService,
            "revoke_api_key",
            staticmethod(lambda db, kid, uid, department_id, commit=True: False),
        )
        response = client.delete("/auth/keys/missing-key", headers=AUTH_HEADERS)
        assert response.status_code == 404

    def test_happy_path(self, client, mock_db, valid_api_key, monkeypatch):
        monkeypatch.setattr(
            AuthService,
            "revoke_api_key",
            staticmethod(lambda db, kid, uid, department_id, commit=True: True),
        )
        response = client.delete("/auth/keys/key-1", headers=AUTH_HEADERS)
        assert response.status_code == 200
        # L377: success message echoes the key id.
        assert response.json() == {
            "success": True,
            "message": "API key revoked",
            "revoked_current_key": True,
        }


class TestValidateApiKey:
    """GET /auth/keys/validate (L436-453)."""

    def test_requires_auth(self, client, mock_db):
        response = client.get("/auth/keys/validate")
        assert response.status_code == 401

    def test_happy_path(self, client, mock_db, valid_api_key):
        response = client.get("/auth/keys/validate", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        # L443-452: valid=True plus the authenticated key's own fields.
        assert body["valid"] is True
        assert body["api_key"]["id"] == valid_api_key.id
        assert body["api_key"]["user_id"] == valid_api_key.user_id


class TestGetDepartment:
    """GET /auth/departments/{department_id} (L585-618)."""

    def test_requires_auth(self, client, mock_db):
        response = client.get("/auth/departments/dept-1")
        assert response.status_code == 401

    def test_forbidden_for_other_department(self, client, mock_db, valid_api_key):
        # L597-601: current_key.department_id ("dept-1") != requested id -> 403.
        response = client.get("/auth/departments/some-other-dept", headers=AUTH_HEADERS)
        assert response.status_code == 403

    def test_not_found(self, client, valid_api_key):
        # L605-608: matching department_id but no row -> 404.
        _use_db(_db_with({Department: {"first": None}}))
        response = client.get("/auth/departments/dept-1", headers=AUTH_HEADERS)
        assert response.status_code == 404

    def test_happy_path(self, client, valid_api_key):
        dept = MagicMock(
            id="dept-1",
            institution="Stanford",
            contact_email="cs@stanford.edu",
            tier="trial",
            max_users=5,
            created_at=datetime.now(timezone.utc),
        )
        dept.name = "CS"  # name= is reserved by the Mock constructor
        _use_db(_db_with({Department: {"first": dept}}))
        response = client.get("/auth/departments/dept-1", headers=AUTH_HEADERS)
        assert response.status_code == 200
        # L610-618: response built straight from the department row.
        assert response.json()["id"] == "dept-1"
        assert response.json()["institution"] == "Stanford"


class TestQuota:
    """GET /auth/quota (L894-918)."""

    def test_requires_auth(self, client, mock_db):
        response = client.get("/auth/quota")
        assert response.status_code == 401

    def test_department_not_found_is_404(
        self, client, mock_db, valid_api_key, monkeypatch
    ):
        # L912-916: get_quota_status returning an "error" key -> 404.
        monkeypatch.setattr(
            auth_routes,
            "get_quota_status",
            lambda db, dept_id: {"error": "Department not found"},
        )
        response = client.get("/auth/quota", headers=AUTH_HEADERS)
        assert response.status_code == 404
        assert response.json()["detail"] == "Department not found"

    def test_happy_path_returns_quota_dict_verbatim(
        self, client, mock_db, valid_api_key, monkeypatch
    ):
        quota = {"tier": "trial", "unlimited": False, "scans": {"used": 1, "limit": 10}}
        monkeypatch.setattr(auth_routes, "get_quota_status", lambda db, dept_id: quota)
        response = client.get("/auth/quota", headers=AUTH_HEADERS)
        # L918: returns quota_info unchanged.
        assert response.status_code == 200
        assert response.json() == quota


class TestAuthHealth:
    """GET /auth/health (L2039-2054) — public."""

    def test_public_health_check(self, client, mock_db):
        response = client.get("/auth/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["service"] == "authentication"
        assert "magic-link-login" in body["features"]


# ==================== Department signup (L459-582) ====================


class TestDepartmentCreationPolicy:
    """POST /auth/departments provisioning policy (issue #75)."""

    @staticmethod
    def _principal(role, auth_method="session"):
        kwargs = {}
        if auth_method == "lti":
            kwargs = {
                "lti_staff_role": "Administrator",
                "lti_account_wide": True,
                "lti_platform": "canvas",
            }
        return AuthenticatedPrincipal(
            api_key=MagicMock(spec=APIKey) if auth_method == "api_key" else None,
            user_id="provisioner",
            department_id="existing-dept",
            user_role=role,
            auth_method=auth_method,
            **kwargs,
        )

    @staticmethod
    def _allow_handler(monkeypatch):
        monkeypatch.setattr(
            auth_routes,
            "check_signup_abuse",
            AsyncMock(
                return_value=AbuseCheckResult(
                    allowed=True, signals=[], recommended_action="allow"
                )
            ),
        )
        email_service = MagicMock()
        email_service.send_email = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(auth_routes, "get_email_service", lambda: email_service)

    def test_closed_mode_rejects_anonymous_before_mutation(self, client):
        db = _db_with()
        _use_db(db)
        client.cookies.set("csrf_token", "matching-csrf")

        response = client.post(
            "/auth/departments",
            headers={"X-CSRF-Token": "matching-csrf"},
            json={
                "name": "CS",
                "institution": "Example University",
                "contact_email": "cs@example.edu",
                "contact_name": "Admin",
            },
        )

        assert response.status_code == 401
        db.add.assert_not_called()
        db.commit.assert_not_called()

    @pytest.mark.parametrize("role", [UserRole.FACULTY])
    def test_closed_mode_rejects_every_non_admin_role(self, role):
        with pytest.raises(HTTPException) as exc_info:
            auth_routes._enforce_department_creation_principal(self._principal(role))
        assert exc_info.value.status_code == 403

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.SUPER_ADMIN])
    @pytest.mark.parametrize("auth_method", ["session", "api_key"])
    def test_normal_admin_principals_are_allowed(self, role, auth_method):
        principal = self._principal(role, auth_method)
        assert (
            auth_routes._enforce_department_creation_principal(principal) is principal
        )

    def test_lti_admin_cannot_create_cross_department_workspace(self):
        with pytest.raises(HTTPException) as exc_info:
            auth_routes._enforce_department_creation_principal(
                self._principal(UserRole.ADMIN, "lti")
            )
        assert exc_info.value.status_code == 403

    def test_lti_admin_route_is_rejected_before_mutation(self, client, monkeypatch):
        principal = self._principal(UserRole.ADMIN, "lti")
        monkeypatch.setattr(
            auth_routes, "get_authenticated_principal", lambda *args: principal
        )
        db = _db_with()
        _use_db(db)
        client.cookies.set("aelira_access", "lti-session")
        client.cookies.set("csrf_token", "matching-csrf")

        response = client.post(
            "/auth/departments",
            headers={"X-CSRF-Token": "matching-csrf"},
            json={
                "name": "CS",
                "institution": "Example University",
                "contact_email": "cs@example.edu",
                "contact_name": "Admin",
            },
        )

        assert response.status_code == 403
        db.add.assert_not_called()
        db.commit.assert_not_called()

    @pytest.mark.parametrize("auth_method", ["session", "api_key"])
    def test_admin_authentication_reaches_department_handler(
        self, client, monkeypatch, auth_method
    ):
        self._allow_handler(monkeypatch)
        principal = self._principal(UserRole.ADMIN, auth_method)
        monkeypatch.setattr(
            auth_routes, "get_authenticated_principal", lambda *args: principal
        )
        _use_db(_db_with({Department: {"first": None}}))
        headers = {}
        if auth_method == "session":
            client.cookies.set("aelira_access", "valid-session")
            client.cookies.set("csrf_token", "matching-csrf")
            headers["X-CSRF-Token"] = "matching-csrf"
        else:
            headers["Authorization"] = "Bearer valid-admin-key"

        response = client.post(
            "/auth/departments",
            headers=headers,
            json={
                "name": "CS",
                "institution": "Example University",
                "contact_email": "cs@example.edu",
                "contact_name": "Admin",
            },
        )

        assert response.status_code == 200

    def test_explicit_public_mode_skips_authentication(self, client, monkeypatch):
        self._allow_handler(monkeypatch)
        monkeypatch.setattr(
            auth_routes,
            "get_settings",
            lambda: SimpleNamespace(allow_public_department_creation=True),
        )
        authenticate = MagicMock()
        monkeypatch.setattr(auth_routes, "get_authenticated_principal", authenticate)
        _use_db(_db_with({Department: {"first": None}}))
        client.cookies.set("csrf_token", "matching-csrf")

        response = client.post(
            "/auth/departments",
            headers={"X-CSRF-Token": "matching-csrf"},
            json={
                "name": "Open Lab",
                "institution": "Example University",
                "contact_email": "lab@example.edu",
                "contact_name": "Researcher",
            },
        )

        assert response.status_code == 200
        authenticate.assert_not_called()

    @pytest.mark.parametrize("authorization_header", ["Bearer invalid-key", "Bearer "])
    def test_explicit_public_mode_rejects_invalid_bearer_before_mutation(
        self, client, monkeypatch, authorization_header
    ):
        self._allow_handler(monkeypatch)
        monkeypatch.setattr(
            auth_routes,
            "get_settings",
            lambda: SimpleNamespace(allow_public_department_creation=True),
        )
        db = _db_with()
        _use_db(db)

        response = client.post(
            "/auth/departments",
            headers={"Authorization": authorization_header},
            json={
                "name": "Open Lab",
                "institution": "Example University",
                "contact_email": "lab@example.edu",
                "contact_name": "Researcher",
            },
        )

        assert response.status_code in {401, 403}
        db.add.assert_not_called()
        db.commit.assert_not_called()


class TestCreateDepartment:
    """POST /auth/departments handler behavior in explicit public mode."""

    @pytest.fixture(autouse=True)
    def _enable_public_department_creation(self, monkeypatch):
        monkeypatch.setattr(
            auth_routes,
            "get_settings",
            lambda: SimpleNamespace(allow_public_department_creation=True),
        )

    def test_invalid_payload_is_422(self, client, mock_db):
        # L75: contact_email: EmailStr -> a malformed address fails validation.
        response = client.post(
            "/auth/departments",
            json={
                "name": "CS",
                "institution": "Stanford",
                "contact_email": "not-an-email",
                "contact_name": "Prof X",
            },
        )
        assert response.status_code == 422

    def test_abuse_blocked_is_429(self, client, monkeypatch):
        # L503-511: abuse_result.recommended_action == "block" -> 429.
        # (create_department has no separate RateLimiter check, unlike
        # magic-link/request -- only check_signup_abuse gates it.)
        monkeypatch.setattr(
            auth_routes,
            "check_signup_abuse",
            AsyncMock(
                return_value=AbuseCheckResult(
                    allowed=False, signals=[], recommended_action="block"
                )
            ),
        )
        _use_db(_db_with())
        response = client.post(
            "/auth/departments",
            json={
                "name": "CS",
                "institution": "Stanford",
                "contact_email": "cs@stanford.edu",
                "contact_name": "Prof X",
            },
        )
        assert response.status_code == 429

    def test_already_exists_is_400(self, client, monkeypatch):
        # L513-526: an existing Department row with the same name+institution -> 400.
        monkeypatch.setattr(
            auth_routes,
            "check_signup_abuse",
            AsyncMock(
                return_value=AbuseCheckResult(
                    allowed=True, signals=[], recommended_action="allow"
                )
            ),
        )
        existing = MagicMock()
        _use_db(_db_with({Department: {"first": existing}}))
        response = client.post(
            "/auth/departments",
            json={
                "name": "CS",
                "institution": "Stanford",
                "contact_email": "cs@stanford.edu",
                "contact_name": "Prof X",
            },
        )
        assert response.status_code == 400

    def test_happy_path(self, client, monkeypatch):
        monkeypatch.setattr(
            auth_routes,
            "check_signup_abuse",
            AsyncMock(
                return_value=AbuseCheckResult(
                    allowed=True, signals=[], recommended_action="allow"
                )
            ),
        )
        # L547-572: unlike magic-link/request and signup_individual, this
        # handler does NOT gate on email_service.is_configured() -- it calls
        # send_email() directly inside a bare try/except, so send_email must
        # itself be awaitable (asyncio.create_task requires a coroutine).
        email_service = MagicMock()
        email_service.send_email = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(auth_routes, "get_email_service", lambda: email_service)
        _use_db(_db_with({Department: {"first": None}}))
        response = client.post(
            "/auth/departments",
            json={
                "name": "CS",
                "institution": "Stanford",
                "contact_email": "cs@stanford.edu",
                "contact_name": "Prof X",
                "tier": "trial",
            },
        )
        # L574-582: DepartmentResponse built from the newly-created row;
        # db.refresh (mocked) backfills id/created_at so the response validates.
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "CS"
        assert body["max_users"] == 5  # L535: trial tier -> 5


# ==================== Individual faculty signup (L647-891) ====================


class TestMagicLinkRequest:
    """POST /auth/magic-link/request (L977-1136) — public."""

    def test_missing_email_is_422(self, client, mock_db):
        # L927: email: EmailStr has no default -> required field.
        response = client.post("/auth/magic-link/request", json={})
        assert response.status_code == 422

    def test_email_rate_limited_is_429(self, client, monkeypatch):
        # L1005-1013: RateLimiter fails the 5/hour email check -> 429.
        monkeypatch.setattr(
            RateLimiter, "check_rate_limit", staticmethod(lambda *a, **k: (False, {}))
        )
        _use_db(_db_with())
        response = client.post("/auth/magic-link/request", json={"email": EDU_EMAIL})
        assert response.status_code == 429
        assert "Too many magic link requests" in response.json()["detail"]

    def test_ip_rate_limited_is_429(self, client, monkeypatch):
        # L1015-1023: email check (limit=5) passes, IP check (limit=10) fails -> 429.
        def fake_rate_limit(key, limit, *a, **k):
            return (limit != 10, {})

        monkeypatch.setattr(
            RateLimiter, "check_rate_limit", staticmethod(fake_rate_limit)
        )
        _use_db(_db_with())
        response = client.post("/auth/magic-link/request", json={"email": EDU_EMAIL})
        assert response.status_code == 429
        assert "Too many requests from this location" in response.json()["detail"]

    def test_blocked_email_returns_generic_success(self, client, monkeypatch):
        # L1025-1037: is_email_blocked True -> same generic response used for
        # the happy path, to avoid leaking which emails exist (anti-enumeration).
        _allow_rate_limit(monkeypatch)
        blocked_record = MagicMock(cooldown_until=None)
        _use_db(_db_with({DeletedEmail: {"first": blocked_record}}))
        response = client.post("/auth/magic-link/request", json={"email": EDU_EMAIL})
        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "message": "If an account exists with this email, you will receive a login link.",
        }

    def test_deactivated_account_returns_generic_success(self, client, monkeypatch):
        # L1039-1047: existing_user.is_active is False -> same generic response.
        _allow_rate_limit(monkeypatch)
        inactive_user = MagicMock(is_active=False)
        _use_db(
            _db_with({DeletedEmail: {"first": None}, User: {"first": inactive_user}})
        )
        response = client.post("/auth/magic-link/request", json={"email": EDU_EMAIL})
        assert response.status_code == 200
        assert response.json()["message"] == (
            "If an account exists with this email, you will receive a login link."
        )

    def test_new_signup_blocked_by_abuse_detector_is_429(self, client, monkeypatch):
        # L1049-1087: existing_user is None -> abuse check runs; "block" -> 429.
        _allow_rate_limit(monkeypatch)
        monkeypatch.setattr(
            auth_routes,
            "check_signup_abuse",
            AsyncMock(
                return_value=AbuseCheckResult(
                    allowed=False, signals=[], recommended_action="block"
                )
            ),
        )
        monkeypatch.setattr(auth_routes, "log_signup", lambda **kw: None)
        _use_db(_db_with({DeletedEmail: {"first": None}, User: {"first": None}}))
        response = client.post("/auth/magic-link/request", json={"email": EDU_EMAIL})
        assert response.status_code == 429
        assert response.json()["detail"]["error"] == "signup_blocked"

    def test_new_signup_challenge_required_is_403(self, client, monkeypatch):
        # L1072-1080: recommended_action == "challenge" -> 403 with challenge details.
        _allow_rate_limit(monkeypatch)
        monkeypatch.setattr(
            auth_routes,
            "check_signup_abuse",
            AsyncMock(
                return_value=AbuseCheckResult(
                    allowed=False,
                    signals=[],
                    recommended_action="challenge",
                    challenge_type="phone",
                )
            ),
        )
        monkeypatch.setattr(auth_routes, "log_signup", lambda **kw: None)
        _use_db(_db_with({DeletedEmail: {"first": None}, User: {"first": None}}))
        response = client.post("/auth/magic-link/request", json={"email": EDU_EMAIL})
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "verification_required"
        assert response.json()["detail"]["challenge_type"] == "phone"

    def test_happy_path_new_signup(self, client, monkeypatch):
        # L1089-1136: allowed signup -> magic link created, generic success returned.
        _allow_rate_limit(monkeypatch)
        monkeypatch.setattr(
            auth_routes,
            "check_signup_abuse",
            AsyncMock(
                return_value=AbuseCheckResult(
                    allowed=True, signals=[], recommended_action="allow"
                )
            ),
        )
        monkeypatch.setattr(auth_routes, "log_signup", lambda **kw: None)
        session_service = MagicMock()
        session_service.create_magic_link.return_value = "tok-123"
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        email_service = MagicMock()
        email_service.is_configured.return_value = False
        monkeypatch.setattr(auth_routes, "get_email_service", lambda: email_service)
        _use_db(_db_with({DeletedEmail: {"first": None}, User: {"first": None}}))
        response = client.post(
            "/auth/magic-link/request",
            json={"email": EDU_EMAIL, "name": "Prof X", "institution": "Stanford"},
        )
        assert response.status_code == 200
        # L1133-1136: always the generic anti-enumeration success message.
        assert response.json() == {
            "success": True,
            "message": "If an account exists with this email, you will receive a login link.",
        }
        assert session_service.create_magic_link.called

    @pytest.mark.parametrize(
        ("requested_next", "expected_next"),
        [
            (
                "/canvas/courses/42/content?tab=files",
                "/canvas/courses/42/content?tab=files",
            ),
            ("//evil.example", "/dashboard"),
            ("https://evil.example/steal", "/dashboard"),
            ("/\\\\evil.example", "/dashboard"),
            ("\\\\evil.example", "/dashboard"),
        ],
    )
    def test_generated_link_carries_only_safe_next(
        self, client, monkeypatch, requested_next, expected_next
    ):
        from urllib.parse import parse_qs, urlparse

        _allow_rate_limit(monkeypatch)
        existing_user = MagicMock(is_active=True)
        session_service = MagicMock()
        session_service.create_magic_link.return_value = "tok-123"
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        email_service = MagicMock()
        email_service.is_configured.return_value = True
        email_service.send_magic_link = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(auth_routes, "get_email_service", lambda: email_service)
        _use_db(
            _db_with({DeletedEmail: {"first": None}, User: {"first": existing_user}})
        )

        response = client.post(
            "/auth/magic-link/request",
            json={"email": EDU_EMAIL, "next": requested_next},
        )

        assert response.status_code == 200
        email_service.send_magic_link.assert_awaited_once()
        sent_url = email_service.send_magic_link.await_args.kwargs["magic_link_url"]
        assert parse_qs(urlparse(sent_url).query)["next"] == [expected_next]

    def test_unknown_email_closed_signup_returns_generic_success(
        self, client, monkeypatch
    ):
        # Provisioning is closed by default: an unknown email on a deployment
        # that already has users gets the generic anti-enumeration response
        # and no magic link is created.
        _allow_rate_limit(monkeypatch)
        session_service = MagicMock()
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        _use_db(
            _db_with({DeletedEmail: {"first": None}, User: {"first": None, "count": 3}})
        )
        response = client.post("/auth/magic-link/request", json={"email": EDU_EMAIL})
        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "message": "If an account exists with this email, you will receive a login link.",
        }
        assert not session_service.create_magic_link.called


class TestMagicLinkCheck:
    """GET /auth/magic-link/check (L1139-1158) — public."""

    def test_valid_token(self, client, mock_db, monkeypatch):
        session_service = MagicMock()
        session_service.check_magic_link.return_value = True
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        response = client.get(
            "/auth/magic-link/check", params={"email": EDU_EMAIL, "token": "tok"}
        )
        assert response.status_code == 200
        # L1158: returns {"valid": <bool>} straight from check_magic_link.
        assert response.json() == {"valid": True}

    def test_invalid_token(self, client, mock_db, monkeypatch):
        session_service = MagicMock()
        session_service.check_magic_link.return_value = False
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        response = client.get(
            "/auth/magic-link/check", params={"email": EDU_EMAIL, "token": "bad"}
        )
        assert response.status_code == 200
        assert response.json() == {"valid": False}

    def test_missing_params_is_422(self, client, mock_db):
        # email/token are required query params (no defaults) at L1141-1142.
        response = client.get("/auth/magic-link/check")
        assert response.status_code == 422


class TestMagicLinkVerify:
    """POST /auth/magic-link/verify (L1161-1263) — public."""

    def test_missing_token_is_422(self, client, mock_db):
        # L965-966: MagicLinkVerifyRequest requires both email and token.
        response = client.post("/auth/magic-link/verify", json={"email": EDU_EMAIL})
        assert response.status_code == 422

    def test_invalid_token_is_400(self, client, mock_db, monkeypatch):
        # L1188-1193: verify_magic_link returning None -> 400.
        session_service = MagicMock()
        session_service.verify_magic_link.return_value = None
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        response = client.post(
            "/auth/magic-link/verify", json={"email": EDU_EMAIL, "token": "bad"}
        )
        assert response.status_code == 400

    def test_blocked_account_is_403(self, client, mock_db, monkeypatch):
        # L1196-1209: get_or_create_user_for_magic_link raising ValueError -> 403.
        session_service = MagicMock()
        session_service.verify_magic_link.return_value = MagicMock(
            signup_name=None, signup_institution=None
        )
        session_service.get_or_create_user_for_magic_link.side_effect = ValueError(
            "This account has been deactivated."
        )
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        response = client.post(
            "/auth/magic-link/verify", json={"email": EDU_EMAIL, "token": "tok"}
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "This account has been deactivated."

    def test_happy_path_sets_session_cookies(self, client, mock_db, monkeypatch):
        # L1211-1263: valid link -> session created, cookies set, success body.
        # NB: name= is reserved by the Mock constructor; set it as an attribute.
        fake_user = MagicMock(id="user-9", email=EDU_EMAIL, role=None)
        fake_user.name = "Prof X"
        session_service = MagicMock()
        session_service.verify_magic_link.return_value = MagicMock(
            signup_name="Prof X", signup_institution="Stanford"
        )
        session_service.get_or_create_user_for_magic_link.return_value = (
            fake_user,
            True,
        )
        session_service.create_session.return_value = (
            "access-tok",
            "refresh-tok",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
        )
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        response = client.post(
            "/auth/magic-link/verify", json={"email": EDU_EMAIL, "token": "tok"}
        )
        assert response.status_code == 200
        body = response.json()
        # L1224-1233: success body with user info and is_new_user flag.
        assert body["success"] is True
        assert body["user"]["id"] == "user-9"
        assert body["is_new_user"] is True
        assert (
            body["user"]["role"] == "faculty"
        )  # L1230: None role -> "faculty" default
        # L1249-1260: both session cookies set.
        assert "aelira_access" in response.cookies
        assert "aelira_refresh" in response.cookies


# ==================== Session management (L1269-1461, L1829-2034) ====================


class TestSessionValidate:
    """GET /auth/session/validate (L1269-1338) — public, but requires a token."""

    def test_no_token_is_401(self, client, mock_db):
        # L1284-1294: no cookie, no Authorization header -> 401.
        response = client.get("/auth/session/validate")
        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"

    def test_both_paths_invalid_is_401(self, client, mock_db, monkeypatch):
        # L1296-1307: DB-backed session invalid, JWT fallback also invalid -> 401.
        session_service = MagicMock()
        session_service.validate_session.return_value = None
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        jwt_service = MagicMock()
        jwt_service.verify_access_token.return_value = None
        monkeypatch.setattr(auth_routes, "get_jwt_service", lambda: jwt_service)
        response = client.get(
            "/auth/session/validate", headers={"Authorization": "Bearer bad.jwt"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Session expired or invalid"

    def test_revoked_normal_jwt_has_no_direct_validation_fallback(
        self, client, monkeypatch
    ):
        session_service = MagicMock()
        session_service.validate_session.return_value = None
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        jwt_service = MagicMock()
        jwt_service.verify_access_token.return_value = {
            "type": "access",
            "sub": "user-9",
            "department_id": "dept-1",
            "exp": 12345,
        }
        monkeypatch.setattr(auth_routes, "get_jwt_service", lambda: jwt_service)
        fake_user = MagicMock(id="user-9", department_id="dept-1", is_active=True)
        _use_db(_db_with({User: {"first": fake_user}}))

        response = client.get(
            "/auth/session/validate", headers={"Authorization": "Bearer revoked.jwt"}
        )

        assert response.status_code == 401

    def test_canonical_lti_v2_token_bypasses_session_rows(self, client, monkeypatch):
        from src.auth.dependencies import AuthenticatedPrincipal
        from src.db.models import UserRole
        import src.auth.dependencies as dependencies

        fake_user = MagicMock(
            id="lti-user",
            email=EDU_EMAIL,
            department_id="dept-1",
            role=UserRole.FACULTY,
            email_verified=True,
            is_active=True,
        )
        fake_user.name = "LTI Instructor"
        principal = AuthenticatedPrincipal(
            api_key=None,
            user_id="lti-user",
            department_id="dept-1",
            user_role=UserRole.FACULTY,
            auth_method="lti",
            lti_course_id="course-1",
            lti_staff_role="Instructor",
            lti_account_wide=False,
        )
        monkeypatch.setattr(
            dependencies, "_principal_from_lti_payload", lambda payload, db: principal
        )
        jwt_service = MagicMock()
        jwt_service.verify_access_token.return_value = {
            "type": "access",
            "sub": "lti-user",
            "lti_launch": True,
            "lti_authz_version": 2,
            "exp": 12345,
        }
        monkeypatch.setattr(auth_routes, "get_jwt_service", lambda: jwt_service)
        session_service = MagicMock()
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        dept = MagicMock(id="dept-1", tier="trial")
        dept.name = "CS"
        _use_db(_db_with({User: {"first": fake_user}, Department: {"first": dept}}))

        response = client.get(
            "/auth/session/validate", headers={"Authorization": "Bearer lti-v2"}
        )

        assert response.status_code == 200
        session_service.validate_session.assert_not_called()
        assert response.json()["auth_method"] == "lti"

    def test_happy_path_db_backed_session(self, client, monkeypatch):
        # L1296-1300, L1318-1338: DB-backed session resolves user + department.
        fake_user = MagicMock(
            id="user-9",
            email=EDU_EMAIL,
            role=None,
            department_id="dept-1",
            email_verified=True,
        )
        fake_user.name = "Prof X"  # name= is reserved by the Mock constructor
        session_service = MagicMock()
        session_service.validate_session.return_value = (fake_user, {"exp": 12345})
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        jwt_service = MagicMock()
        jwt_service.verify_access_token.return_value = {"type": "access", "exp": 12345}
        monkeypatch.setattr(auth_routes, "get_jwt_service", lambda: jwt_service)
        dept = MagicMock(id="dept-1", tier="trial")
        dept.name = "CS"  # name= is reserved by the Mock constructor
        _use_db(_db_with({Department: {"first": dept}}))
        client.cookies.set("aelira_access", "tok")
        response = client.get("/auth/session/validate")
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["user"]["id"] == "user-9"
        assert body["department"]["id"] == "dept-1"
        assert body["expires_at"] == 12345
        assert body["auth_method"] == "session"

    def test_validated_session_recovers_from_stale_bearer_and_creates_one_requested_key(
        self, client, mock_db, monkeypatch
    ):
        """Exercise the complete browser recovery sequence at the HTTP boundary."""
        from src.auth import dependencies
        from src.auth.dependencies import AuthenticatedPrincipal
        from src.db.models import UserRole

        fake_user = MagicMock(
            id="session-user",
            email=EDU_EMAIL,
            department_id="session-dept",
            role=UserRole.FACULTY,
            email_verified=True,
            is_active=True,
        )
        fake_user.name = "Session User"
        principal = AuthenticatedPrincipal(
            api_key=None,
            user_id="session-user",
            department_id="session-dept",
            user_role=UserRole.FACULTY,
            auth_method="session",
        )
        resolved = SimpleNamespace(
            principal=principal, user=fake_user, payload={"exp": 12345}
        )
        resolve = MagicMock(return_value=resolved)
        monkeypatch.setattr(auth_routes, "resolve_access_token", resolve)
        monkeypatch.setattr(dependencies, "resolve_access_token", resolve)
        dept = MagicMock(id="session-dept", tier="trial")
        dept.name = "Accessibility"
        mock_db.query.return_value.filter.return_value.first.return_value = dept
        listed = MagicMock(return_value=[])
        monkeypatch.setattr(AuthService, "list_api_keys", listed)
        created = _fake_api_key(
            id="requested-key",
            user_id="session-user",
            department_id="session-dept",
            name="Automation",
        )
        create = MagicMock(return_value=(created, "aelira_live_visible_once"))
        monkeypatch.setattr(AuthService, "create_api_key", create)
        client.cookies.set("aelira_access", "valid-session")
        client.cookies.set("csrf_token", "matching-csrf")
        stale_headers = {"Authorization": "Bearer retired-dashboard-key"}

        validation = client.get("/auth/session/validate", headers=stale_headers)
        keys = client.get("/auth/keys", headers=stale_headers)
        creation = client.post(
            "/auth/keys",
            json={"name": "Automation"},
            headers={**stale_headers, "X-CSRF-Token": "matching-csrf"},
        )

        assert validation.status_code == 200
        assert validation.json()["auth_method"] == "session"
        assert keys.status_code == 200
        assert keys.json() == []
        assert creation.status_code == 200
        assert creation.json()["full_key"] == "aelira_live_visible_once"
        assert creation.json()["api_key"]["id"] == "requested-key"
        listed.assert_called_once_with(mock_db, "session-user", "session-dept")
        create.assert_called_once()
        assert create.call_args.kwargs["name"] == "Automation"


class TestSessionRefresh:
    """POST /auth/session/refresh (L1341-1411) — public."""

    def test_no_refresh_cookie_is_401(self, client, mock_db):
        response = client.post("/auth/session/refresh")
        assert response.status_code == 401
        assert response.json()["detail"] == "Refresh token required"
        cleared = "\n".join(response.headers.get_list("set-cookie")).lower()
        assert "aelira_access=" in cleared
        assert "aelira_refresh=" in cleared
        assert "path=/" in cleared
        assert "httponly" in cleared
        assert "secure" in cleared
        assert "samesite=lax" in cleared

    def test_invalid_refresh_token_is_401(self, client, mock_db, monkeypatch):
        # L1369-1380: session_service.refresh_session returning None -> 401.
        session_service = MagicMock()
        session_service.refresh_session.return_value = None
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        client.cookies.set("aelira_refresh", "bad")
        response = client.post("/auth/session/refresh")
        assert response.status_code == 401
        assert "Invalid or expired refresh token" in response.json()["detail"]

    def test_happy_path_rotates_tokens(self, client, mock_db, monkeypatch):
        session_service = MagicMock()
        session_service.refresh_session.return_value = (
            "new-access",
            "new-refresh",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
        )
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        client.cookies.set("aelira_refresh", "good")
        response = client.post("/auth/session/refresh")
        assert response.status_code == 200
        # L1385: fixed success body.
        assert response.json() == {"success": True, "message": "Session refreshed"}
        # L1397-1408: rotated cookies set on the response.
        assert "aelira_access" in response.cookies
        assert "aelira_refresh" in response.cookies


class TestSessionLogout:
    """POST /auth/session/logout (L1414-1461) — public."""

    def test_no_cookie_still_returns_success(self, client, mock_db):
        # L1428-1444: access_token is None -> skip revoke, still return success
        # and clear cookies (idempotent logout).
        response = client.post("/auth/session/logout")
        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "Logged out"}

    def test_with_cookie_revokes_session(self, client, mock_db, monkeypatch):
        jwt_service = MagicMock()
        jwt_service.decode_token.return_value = {
            "type": "access",
            "sub": "user-9",
            "jti": "jti-9",
        }
        monkeypatch.setattr(auth_routes, "get_jwt_service", lambda: jwt_service)
        session_service = MagicMock()
        session_service.revoke_session.return_value = True
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        client.cookies.set("aelira_access", "tok")
        response = client.post("/auth/session/logout")
        assert response.status_code == 200
        # L1435-1437: revoke_session called with the token-derived user id.
        session_service.revoke_session.assert_called_once()
        assert session_service.revoke_session.call_args.args[1] == "user-9"

    def test_expired_access_or_refresh_sid_revokes_exact_session(
        self, client, mock_db, monkeypatch
    ):
        jwt_service = MagicMock()
        jwt_service.decode_token.side_effect = [
            None,
            {"type": "refresh", "sub": "user-9", "sid": "sess-9"},
        ]
        monkeypatch.setattr(auth_routes, "get_jwt_service", lambda: jwt_service)
        session_service = MagicMock()
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        client.cookies.set("aelira_access", "expired-access")
        client.cookies.set("aelira_refresh", "refresh-with-sid")

        response = client.post("/auth/session/logout")

        assert response.status_code == 200
        session_service.revoke_session.assert_called_once_with(
            mock_db, "user-9", session_id="sess-9"
        )
        cleared = "\n".join(response.headers.get_list("set-cookie")).lower()
        assert "aelira_access=" in cleared
        assert "aelira_refresh=" in cleared

    def test_legacy_access_falls_back_to_refresh_sid(
        self, client, mock_db, monkeypatch
    ):
        jwt_service = MagicMock()
        jwt_service.decode_token.side_effect = [
            {"type": "access", "sub": "user-9", "jti": "legacy-jti"},
            {"type": "refresh", "sub": "user-9", "sid": "sess-9"},
        ]
        monkeypatch.setattr(auth_routes, "get_jwt_service", lambda: jwt_service)
        session_service = MagicMock()
        monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
        client.cookies.set("aelira_access", "legacy-access")
        client.cookies.set("aelira_refresh", "refresh-with-sid")

        response = client.post("/auth/session/logout")

        assert response.status_code == 200
        assert jwt_service.decode_token.call_count == 2
        session_service.revoke_session.assert_called_once_with(
            mock_db, "user-9", session_id="sess-9"
        )


class TestListSessions:
    """GET /auth/sessions (L1829-1894) — requires get_required_api_key."""

    def test_requires_auth(self, client, mock_db):
        # dependencies.py L159-164: no Bearer/cookie -> 401.
        response = client.get("/auth/sessions")
        assert response.status_code == 401

    def test_happy_path(self, client, valid_api_key):
        fake_user = MagicMock(id="user-1")
        sess = MagicMock(
            id="sess-1",
            ip_address="1.2.3.4",
            user_agent="pytest",
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            access_token_jti="jti-1",
        )
        _use_db(_db_with({User: {"first": fake_user}, UserSession: {"all": [sess]}}))
        response = client.get("/auth/sessions", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        # L1891-1894: one SessionListItem per active session; no cookie -> no
        # current_jti match, so is_current is False (L1887).
        assert body["total"] == 1
        assert body["sessions"][0]["id"] == "sess-1"
        assert body["sessions"][0]["is_current"] is False


class TestRevokeSessionById:
    """DELETE /auth/sessions/{session_id} (L1897-1964) — manual cookie-only auth."""

    def test_no_cookie_is_401(self, client, mock_db):
        # L1913-1919: no aelira_access cookie -> 401 "Not authenticated".
        response = client.delete("/auth/sessions/sess-1")
        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"


class TestRevokeAllOtherSessions:
    """DELETE /auth/sessions (L1967-2033) — manual cookie-only auth."""

    def test_no_cookie_is_401(self, client, mock_db):
        # L1983-1989: no aelira_access cookie -> 401 "Not authenticated".
        response = client.delete("/auth/sessions")
        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"


class TestGetProfile:
    """GET /auth/profile (L1489-1520) — requires get_required_api_key."""

    def test_requires_auth(self, client, mock_db):
        response = client.get("/auth/profile")
        assert response.status_code == 401

    def test_user_not_found_is_404(self, client, valid_api_key):
        # L1502-1507: authenticated but the user row is missing -> 404.
        _use_db(_db_with({User: {"first": None}}))
        response = client.get("/auth/profile", headers=AUTH_HEADERS)
        assert response.status_code == 404


class TestGetEmailPreferences:
    """GET /auth/profile/email-preferences (L1624-1678) — requires auth."""

    def test_requires_auth(self, client, mock_db):
        response = client.get("/auth/profile/email-preferences")
        assert response.status_code == 401

    def test_invalid_day_is_422(self, client, mock_db, valid_api_key):
        # L1620: weekly_summary_day constrained to 0-6.
        response = client.patch(
            "/auth/profile/email-preferences",
            json={"weekly_summary_day": 7},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422
