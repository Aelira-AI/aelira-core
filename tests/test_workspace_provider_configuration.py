"""Workspace-scoped AI provider configuration security contracts (#258)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api import llm_providers
from src.ai.lms_readiness import resolve_lms_provider_config
from src.ai.workspace_provider_config import (
    provider_config_from_row,
    test_provider_row as run_provider_test,
)
from src.api.main import app
from src.auth.dependencies import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
    get_required_api_key,
)
from src.db.database import get_db_dependency
from src.db.models import (
    AuditLog,
    Department,
    DepartmentAIProviderConfig,
    UserRole,
)

PROVIDERS = {"ollama", "gemini", "openai", "anthropic", "xai"}
CANARY_CREDENTIAL = "CANARY_CREDENTIAL_MATERIAL"
OPAQUE_CIPHERTEXT = "opaque-encrypted-canary"


def _principal(
    role: UserRole = UserRole.ADMIN,
    *,
    auth_method: str = "session",
    department_id: str = "dept-1",
    **overrides,
) -> AuthenticatedPrincipal:
    values = {
        "api_key": MagicMock() if auth_method == "api_key" else None,
        "user_id": f"user-{department_id}",
        "department_id": department_id,
        "user_role": role,
        "auth_method": auth_method,
    }
    values.update(overrides)
    return AuthenticatedPrincipal(**values)


def _provider_row(
    provider: str,
    *,
    department_id: str = "dept-1",
    encrypted_key: str | None = OPAQUE_CIPHERTEXT,
    text_model: str | None = None,
    code_model: str | None = None,
    vision_model: str | None = None,
):
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=f"{department_id}-{provider}",
        department_id=department_id,
        provider=provider,
        api_key_encrypted=encrypted_key,
        text_model=text_model,
        code_model=code_model,
        vision_model=vision_model,
        configured_at=now,
        updated_at=now,
    )


def _department(
    *,
    department_id: str = "dept-1",
    revision: int = 0,
    primary: str | None = None,
    fallback: str | None = None,
    rows: list[object] | None = None,
):
    provider_rows = list(rows or [])
    return SimpleNamespace(
        id=department_id,
        ai_provider_config_revision=revision,
        ai_primary_provider=primary,
        ai_fallback_provider=fallback,
        provider_configs=provider_rows,
        ai_provider_configs=provider_rows,
        # Compatibility fields must not become an alternate authority.
        byok_provider=None,
        byok_api_key_encrypted=None,
        byok_configured_at=None,
        pilot_gemini_approved=False,
        tier="department",
    )


class _Query:
    def __init__(self, session: "_Session", model: object):
        self.session = session
        self.model = model
        self.criteria: list[object] = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def filter_by(self, **values):
        self.criteria.extend(values.items())
        return self

    def with_for_update(self):
        self.session.locked = True
        return self

    def _criterion(self, name: str):
        for criterion in self.criteria:
            if isinstance(criterion, tuple) and criterion[0] == name:
                return criterion[1]
            left = getattr(criterion, "left", None)
            if getattr(left, "name", None) == name:
                return getattr(getattr(criterion, "right", None), "value", None)
        return None

    def _is_department(self) -> bool:
        return (
            self.model is Department
            or getattr(self.model, "__tablename__", None) == "departments"
        )

    def _rows(self):
        if self._is_department():
            requested_id = self._criterion("id")
            if requested_id is not None and requested_id != self.session.department.id:
                return []
            return [self.session.department]
        rows = list(self.session.rows)
        provider = self._criterion("provider")
        department_id = self._criterion("department_id")
        if provider is not None:
            rows = [row for row in rows if getattr(row, "provider", None) == provider]
        if department_id is not None:
            rows = [
                row
                for row in rows
                if getattr(row, "department_id", None) == department_id
            ]
        return rows

    def first(self):
        rows = self._rows()
        return rows[0] if rows else None

    def one_or_none(self):
        return self.first()

    def all(self):
        return self._rows()

    def delete(self):
        targets = self._rows()
        self.session.rows[:] = [row for row in self.session.rows if row not in targets]
        return len(targets)


class _Session:
    """Small stateful session double at the public route persistence boundary."""

    def __init__(self, department):
        self.department = department
        self.rows = department.ai_provider_configs
        self.audits: list[AuditLog] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.locked = False

    def query(self, model):
        return _Query(self, model)

    def get(self, model, identity):
        if model is Department and identity == self.department.id:
            return self.department
        return None

    def add(self, value):
        if isinstance(value, AuditLog):
            self.audits.append(value)
            return
        if hasattr(value, "provider") and hasattr(value, "department_id"):
            if value not in self.rows:
                self.rows.append(value)

    def delete(self, value):
        if value in self.rows:
            self.rows.remove(value)

    def flush(self):
        return None

    def refresh(self, _value):
        return None

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


class _TrapDB:
    def query(self, _model):  # pragma: no cover - failure message is the contract
        raise AssertionError("authorization must precede database work")

    def get(self, _model, _identity):  # pragma: no cover
        raise AssertionError("authorization must precede database work")


@pytest.fixture(autouse=True)
def _clean_dependency_overrides():
    yield
    for dependency in (
        get_authenticated_principal,
        get_required_api_key,
        get_db_dependency,
    ):
        app.dependency_overrides.pop(dependency, None)


def _authorize(actor: AuthenticatedPrincipal, db: object) -> None:
    app.dependency_overrides[get_authenticated_principal] = lambda: actor
    # Legacy wrappers currently declare this adapter directly. Keeping the
    # override lets the test prove their authorization behavior during migration.
    app.dependency_overrides[get_required_api_key] = actor.as_legacy_tuple
    app.dependency_overrides[get_db_dependency] = lambda: db


def _forbid_singleton(monkeypatch):
    def forbidden():
        raise AssertionError("workspace routes must not use the global ProviderManager")

    monkeypatch.setattr(llm_providers, "get_provider_manager", forbidden)


@pytest.mark.parametrize(
    "actor",
    [
        _principal(UserRole.FACULTY, auth_method="session"),
        _principal(UserRole.FACULTY, auth_method="api_key"),
        _principal(UserRole.ADMIN, auth_method="mock"),
        _principal(UserRole.SUPER_ADMIN, auth_method="mock"),
        _principal(
            UserRole.ADMIN,
            auth_method="lti",
            lti_staff_role="Administrator",
            lti_account_wide=True,
            lti_platform="canvas",
        ),
        _principal(
            UserRole.FACULTY,
            auth_method="lti",
            lti_course_id="course-1",
            lti_staff_role="Instructor",
            lti_platform="canvas",
        ),
        _principal(
            UserRole.FACULTY,
            auth_method="lti",
            lti_course_id="course-1",
            lti_staff_role="TeachingAssistant",
            lti_platform="blackboard",
        ),
        _principal(
            UserRole.FACULTY,
            auth_method="lti",
            lti_course_id="course-1",
            lti_staff_role="ContentDeveloper",
            lti_platform="brightspace",
        ),
    ],
    ids=[
        "session-faculty",
        "api-key-faculty",
        "mock-admin",
        "mock-super-admin",
        "lti-account-admin",
        "lti-instructor",
        "lti-teaching-assistant",
        "lti-content-developer",
    ],
)
@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/llm/providers", {}),
        (
            "put",
            "/llm/providers/selection",
            {"json": {"expected_revision": 0, "primary": None, "fallback": None}},
        ),
        (
            "put",
            "/llm/providers/ollama",
            {"json": {"expected_revision": 0}},
        ),
    ],
)
def test_disallowed_principals_are_rejected_before_db_or_provider_work(
    monkeypatch, actor, method, path, kwargs
):
    _authorize(actor, _TrapDB())
    _forbid_singleton(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 403


def test_anonymous_is_rejected_before_db_or_provider_work(monkeypatch):
    def anonymous():
        raise HTTPException(status_code=401, detail="Authentication required")

    app.dependency_overrides[get_authenticated_principal] = anonymous
    app.dependency_overrides[get_required_api_key] = anonymous
    app.dependency_overrides[get_db_dependency] = lambda: _TrapDB()
    _forbid_singleton(monkeypatch)

    response = TestClient(app, raise_server_exceptions=False).get("/llm/providers")

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("role", "auth_method"),
    [
        (UserRole.ADMIN, "session"),
        (UserRole.SUPER_ADMIN, "session"),
        (UserRole.ADMIN, "api_key"),
        (UserRole.SUPER_ADMIN, "api_key"),
    ],
)
def test_admin_auth_methods_get_neutral_workspace_state(monkeypatch, role, auth_method):
    department = _department()
    _authorize(_principal(role, auth_method=auth_method), _Session(department))
    _forbid_singleton(monkeypatch)

    response = TestClient(app).get("/llm/providers")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["config_revision"] == 0
    assert payload["primary"] is None
    assert payload["fallback"] is None
    assert set(payload["providers"]) == PROVIDERS


def test_workspace_provider_schema_is_unique_constrained_and_neutral():
    provider_table = DepartmentAIProviderConfig.__table__
    department_table = Department.__table__
    provider_constraints = {
        constraint.name: str(getattr(constraint, "sqltext", ""))
        for constraint in provider_table.constraints
        if constraint.name
    }
    department_constraints = {
        constraint.name: str(getattr(constraint, "sqltext", ""))
        for constraint in department_table.constraints
        if constraint.name
    }

    assert (
        "uq_department_ai_provider_configs_department_provider" in provider_constraints
    )
    assert "ck_department_ai_provider_configs_provider" in provider_constraints
    credential_check = provider_constraints[
        "ck_department_ai_provider_configs_credential"
    ]
    assert "provider = 'ollama' AND api_key_encrypted IS NULL" in credential_check
    assert "provider <> 'ollama' AND api_key_encrypted IS NOT NULL" in credential_check
    assert "ck_departments_ai_primary_provider" in department_constraints
    assert "ck_departments_ai_fallback_provider" in department_constraints
    assert "ck_departments_ai_provider_selection_distinct" in department_constraints
    assert department_table.c.ai_primary_provider.default is None
    assert department_table.c.ai_fallback_provider.default is None
    revision = department_table.c.ai_provider_config_revision
    assert revision.nullable is False
    assert revision.default.arg == 0
    assert str(revision.server_default.arg) == "0"


def test_migration_moves_legacy_credentials_to_one_authoritative_store():
    source = (
        Path(__file__).parents[1]
        / "alembic/versions/2026_08_29_workspace_provider_configuration.py"
    ).read_text()

    assert "INSERT INTO {_TABLE}" in source
    assert "SET byok_provider = NULL" in source
    assert "byok_api_key_encrypted = NULL" in source
    assert "Restore the primary provider, or the sole configured provider" in source


@pytest.mark.parametrize(
    ("role", "auth_method"),
    [
        (UserRole.ADMIN, "session"),
        (UserRole.SUPER_ADMIN, "session"),
        (UserRole.ADMIN, "api_key"),
        (UserRole.SUPER_ADMIN, "api_key"),
    ],
)
def test_admin_auth_methods_can_atomically_keep_provider_selection_neutral(
    monkeypatch, role, auth_method
):
    session = _Session(_department())
    _authorize(_principal(role, auth_method=auth_method), session)
    _forbid_singleton(monkeypatch)

    response = TestClient(app).put(
        "/llm/providers/selection",
        json={"expected_revision": 0, "primary": None, "fallback": None},
    )

    assert response.status_code == 200
    assert response.json()["config_revision"] == 1
    assert response.json()["primary"] is None
    assert response.json()["fallback"] is None
    assert session.commit_count == 1


def test_get_is_durable_and_independent_of_singleton_state(monkeypatch):
    row = _provider_row("anthropic", text_model="workspace-model")
    department = _department(revision=7, primary="anthropic", rows=[row])
    _authorize(_principal(), _Session(department))
    _forbid_singleton(monkeypatch)

    payload = TestClient(app).get("/llm/providers").json()

    assert payload["config_revision"] == 7
    assert payload["primary"] == "anthropic"
    assert payload["fallback"] is None
    assert payload["providers"]["anthropic"]["configured"] is True
    assert payload["providers"]["anthropic"]["text_model"] == "workspace-model"


def test_two_departments_read_independent_persisted_rows(monkeypatch):
    _forbid_singleton(monkeypatch)
    client = TestClient(app)
    states = (
        (
            _principal(department_id="dept-a"),
            _department(
                department_id="dept-a",
                revision=2,
                primary="openai",
                rows=[_provider_row("openai", department_id="dept-a")],
            ),
            "openai",
        ),
        (
            _principal(department_id="dept-b"),
            _department(
                department_id="dept-b",
                revision=5,
                primary="ollama",
                rows=[
                    _provider_row("ollama", department_id="dept-b", encrypted_key=None)
                ],
            ),
            "ollama",
        ),
    )

    for actor, department, expected_primary in states:
        _authorize(actor, _Session(department))
        payload = client.get("/llm/providers").json()
        assert payload["primary"] == expected_primary
        assert payload["providers"][expected_primary]["configured"] is True
        other = "ollama" if expected_primary == "openai" else "openai"
        assert payload["providers"][other]["configured"] is False


def test_selection_uses_optimistic_revision_and_returns_safe_current_state(
    monkeypatch,
):
    row = _provider_row("openai")
    department = _department(revision=4, primary="openai", rows=[row])
    session = _Session(department)
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)

    response = TestClient(app).put(
        "/llm/providers/selection",
        json={"expected_revision": 3, "primary": None, "fallback": None},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "provider_config_revision_conflict"
    assert detail["current"]["config_revision"] == 4
    assert detail["current"]["primary"] == "openai"
    assert CANARY_CREDENTIAL not in response.text
    assert OPAQUE_CIPHERTEXT not in response.text
    assert session.commit_count == 0
    assert session.audits == []


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
@pytest.mark.parametrize("auth_method", ["session", "api_key"])
def test_all_supported_provider_identities_can_be_configured(
    monkeypatch, provider, auth_method
):
    department = _department()
    session = _Session(department)
    _authorize(_principal(auth_method=auth_method), session)
    _forbid_singleton(monkeypatch)
    monkeypatch.setattr(llm_providers, "is_encryption_configured", lambda: True)
    monkeypatch.setattr(
        llm_providers, "encrypt_api_key", lambda _value: OPAQUE_CIPHERTEXT
    )
    body = {"expected_revision": 0, "text_model": "chosen-model"}
    if provider != "ollama":
        body["api_key"] = CANARY_CREDENTIAL

    response = TestClient(app).put(f"/llm/providers/{provider}", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["config_revision"] == 1
    assert payload["providers"][provider]["configured"] is True
    assert CANARY_CREDENTIAL not in response.text
    assert OPAQUE_CIPHERTEXT not in response.text


def test_provider_update_is_atomic_and_audit_is_secret_free(monkeypatch):
    department = _department()
    session = _Session(department)
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)
    monkeypatch.setattr(llm_providers, "is_encryption_configured", lambda: True)
    monkeypatch.setattr(
        llm_providers, "encrypt_api_key", lambda _value: OPAQUE_CIPHERTEXT
    )

    response = TestClient(app).put(
        "/llm/providers/openai",
        json={
            "expected_revision": 0,
            "api_key": CANARY_CREDENTIAL,
            "text_model": "approved-model",
        },
    )

    assert response.status_code == 200
    assert session.locked is True
    assert session.commit_count == 1
    assert len(session.audits) == 1
    audit = session.audits[0]
    assert audit.action == "ai_provider_config_update"
    serialized_audit = json.dumps(audit.details, default=str)
    assert CANARY_CREDENTIAL not in serialized_audit
    assert OPAQUE_CIPHERTEXT not in serialized_audit
    assert "api_key" not in serialized_audit
    assert audit.department_id == "dept-1"


def test_key_replacement_preserves_existing_model_overrides(monkeypatch):
    row = _provider_row(
        "openai",
        text_model="text-choice",
        code_model="code-choice",
        vision_model="vision-choice",
    )
    session = _Session(_department(revision=3, rows=[row]))
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)
    monkeypatch.setattr(llm_providers, "is_encryption_configured", lambda: True)
    monkeypatch.setattr(
        llm_providers, "encrypt_api_key", lambda _value: "replacement-ciphertext"
    )

    response = TestClient(app).put(
        "/llm/providers/openai",
        json={"expected_revision": 3, "api_key": CANARY_CREDENTIAL},
    )

    assert response.status_code == 200
    assert row.api_key_encrypted == "replacement-ciphertext"
    assert row.text_model == "text-choice"
    assert row.code_model == "code-choice"
    assert row.vision_model == "vision-choice"


def test_selected_provider_cannot_be_deleted(monkeypatch):
    row = _provider_row("openai")
    session = _Session(_department(primary="openai", rows=[row]))
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)

    response = TestClient(app).delete(
        "/llm/providers/openai", params={"expected_revision": 0}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "provider_is_selected"
    assert row in session.rows
    assert session.commit_count == 0


def test_commit_failure_rolls_back_and_returns_bounded_error(monkeypatch):
    class FailingSession(_Session):
        def commit(self):
            raise RuntimeError(f"database leaked {CANARY_CREDENTIAL}")

    session = FailingSession(_department())
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)

    response = TestClient(app, raise_server_exceptions=False).put(
        "/llm/providers/ollama",
        json={"expected_revision": 0},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Provider configuration update failed"
    assert CANARY_CREDENTIAL not in response.text
    assert session.rollback_count == 1


def test_ollama_rejects_credentials_before_mutation(monkeypatch):
    session = _Session(_department())
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)

    response = TestClient(app).put(
        "/llm/providers/ollama",
        json={"expected_revision": 0, "api_key": CANARY_CREDENTIAL},
    )

    assert response.status_code == 422
    assert session.commit_count == 0
    assert session.audits == []


def test_rejected_api_key_is_never_reflected_in_validation_response(monkeypatch):
    session = _Session(_department())
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)
    secret = "secret-canary-" + ("x" * 4096)

    response = TestClient(app).put(
        "/llm/providers/openai",
        json={"expected_revision": 0, "api_key": secret},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid provider API key"
    assert secret not in response.text
    assert "secret-canary" not in response.text
    assert session.commit_count == 0


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "put",
            "/llm/providers/openai",
            {
                "expected_revision": 0,
                "api_key": {"token": "MALFORMED_SECRET_CANARY"},
            },
        ),
        (
            "post",
            "/llm/providers/add",
            {
                "provider": "openai",
                "api_key": ["MALFORMED_SECRET_CANARY"],
            },
        ),
    ],
)
def test_malformed_api_key_is_never_reflected_by_any_credential_route(
    monkeypatch, method, path, body
):
    session = _Session(_department())
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)

    response = TestClient(app, raise_server_exceptions=False).request(
        method, path, json=body
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid provider API key"
    assert "MALFORMED_SECRET_CANARY" not in response.text
    assert session.commit_count == 0


def test_workspace_rows_are_authoritative_over_retained_legacy_credentials():
    department = _department()
    department.byok_provider = "openai"
    department.byok_api_key_encrypted = "stale-legacy-ciphertext"

    config, readiness = resolve_lms_provider_config(
        department,
        "openai",
        decrypt_api_key=lambda value: f"decrypted:{value}",
    )

    assert config is None
    assert readiness.reason == "credentials_missing"


def test_workspace_row_drives_lms_credentials_and_models():
    row = _provider_row("openai", text_model="workspace-text-model")
    department = _department(rows=[row])
    department.byok_provider = "openai"
    department.byok_api_key_encrypted = "stale-legacy-ciphertext"

    config, readiness = resolve_lms_provider_config(
        department,
        "openai",
        decrypt_api_key=lambda value: f"decrypted:{value}",
    )

    assert readiness.ready is True
    assert config is not None
    assert config.api_key == f"decrypted:{OPAQUE_CIPHERTEXT}"
    assert config.text_model == "workspace-text-model"


def test_workspace_ollama_uses_operator_configured_host(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434")

    config = provider_config_from_row(
        _provider_row("ollama", encrypted_key=None),
        decryptor=lambda _value: "unused",
    )

    assert config.host == "http://ollama:11434"
    assert config.api_key is None


def test_cloud_provider_fails_closed_when_encryption_is_unavailable(monkeypatch):
    session = _Session(_department())
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)
    monkeypatch.setattr(llm_providers, "is_encryption_configured", lambda: False)

    response = TestClient(app).put(
        "/llm/providers/gemini",
        json={"expected_revision": 0, "api_key": CANARY_CREDENTIAL},
    )

    assert response.status_code == 503
    assert CANARY_CREDENTIAL not in response.text
    assert session.commit_count == 0
    assert session.audits == []


@pytest.mark.parametrize(
    "injected", [{"department_id": "dept-2"}, {"tenant_id": "dept-2"}]
)
def test_provider_update_forbids_cross_tenant_field_injection(monkeypatch, injected):
    session = _Session(_department())
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)

    response = TestClient(app).put(
        "/llm/providers/ollama",
        json={"expected_revision": 0, **injected},
    )

    assert response.status_code == 422
    assert session.commit_count == 0


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "post",
            "/llm/providers/add",
            {"provider": "openai", "api_key": CANARY_CREDENTIAL},
        ),
        (
            "post",
            "/llm/providers/primary",
            {"provider": "openai", "as_fallback": False},
        ),
        (
            "put",
            "/llm/providers/openai/models",
            {"text_model": "approved-model"},
        ),
        ("post", "/llm/byok/load", None),
        ("delete", "/llm/byok", None),
    ],
)
def test_legacy_mutations_are_tenant_safe_wrappers_without_singleton_mutation(
    monkeypatch, method, path, body
):
    row = _provider_row("openai")
    session = _Session(_department(primary="openai", rows=[row]))
    _authorize(_principal(), session)
    _forbid_singleton(monkeypatch)
    monkeypatch.setattr(llm_providers, "is_encryption_configured", lambda: True)
    monkeypatch.setattr(
        llm_providers, "encrypt_api_key", lambda _value: OPAQUE_CIPHERTEXT
    )

    response = TestClient(app, raise_server_exceptions=False).request(
        method.upper(), path, json=body
    )

    assert response.status_code == 200
    assert CANARY_CREDENTIAL not in response.text
    assert OPAQUE_CIPHERTEXT not in response.text


def test_provider_openapi_never_exposes_persisted_secret_fields():
    schema = app.openapi()
    provider_paths = {
        path: value
        for path, value in schema["paths"].items()
        if path.startswith("/llm/providers") or path.startswith("/llm/byok")
    }
    serialized = json.dumps(provider_paths)

    assert "api_key_encrypted" not in serialized
    assert "byok_api_key_encrypted" not in serialized
    assert OPAQUE_CIPHERTEXT not in serialized
    for model_name in ("ProviderConfigUpdate", "AddProviderRequest"):
        api_key_schema = schema["components"]["schemas"][model_name]["properties"][
            "api_key"
        ]
        assert api_key_schema["type"] == "string"
        assert api_key_schema["writeOnly"] is True


@pytest.mark.asyncio
async def test_provider_test_uses_one_fresh_instance_and_always_closes():
    events = []

    class Provider:
        async def initialize(self):
            events.append("initialize")
            return True

        async def generate_text(self, **_kwargs):
            events.append("generate")
            return SimpleNamespace(
                success=True,
                content=CANARY_CREDENTIAL,
                provider="openai",
                model="approved-model",
                inference_time=0.25,
            )

        async def close(self):
            events.append("close")

    result = await run_provider_test(
        _provider_row("openai", text_model="approved-model"),
        decryptor=lambda encrypted: (
            events.append(("decrypt", encrypted)) or CANARY_CREDENTIAL
        ),
        provider_factory=lambda provider_type, config: (
            events.append(("factory", provider_type.value, config.api_key))
            or Provider()
        ),
    )

    assert result.success is True
    assert result.provider == "openai"
    assert not hasattr(result, "content")
    assert events == [
        ("decrypt", OPAQUE_CIPHERTEXT),
        ("factory", "openai", CANARY_CREDENTIAL),
        "initialize",
        "generate",
        "close",
    ]


@pytest.mark.asyncio
async def test_provider_test_returns_bounded_error_and_closes_on_failure():
    closed = []

    class Provider:
        async def initialize(self):
            raise RuntimeError(f"upstream leaked {CANARY_CREDENTIAL}")

        async def close(self):
            closed.append(True)

    result = await run_provider_test(
        _provider_row("openai"),
        decryptor=lambda _encrypted: CANARY_CREDENTIAL,
        provider_factory=lambda _provider_type, _config: Provider(),
    )

    assert result.success is False
    assert result.error == "provider_test_failed"
    assert CANARY_CREDENTIAL not in json.dumps(result.__dict__)
    assert closed == [True]
