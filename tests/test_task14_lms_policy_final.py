"""Task 14 final slice: revisioned, readiness-gated admin LMS AI policy."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import AuditLog, Department, UserRole


def _principal(
    role=UserRole.ADMIN, *, auth_method="session", department_id="dept-1", **kwargs
):
    return AuthenticatedPrincipal(
        api_key=MagicMock() if auth_method == "api_key" else None,
        user_id="user-1",
        department_id=department_id,
        user_role=role,
        auth_method=auth_method,
        **kwargs,
    )


def _department(**overrides):
    values = dict(
        id="dept-1",
        lms_ai_enabled=False,
        lms_ai_provider=None,
        lms_ai_purposes=[],
        lms_ai_policy_revision=0,
        byok_provider=None,
        byok_api_key_encrypted=None,
        pilot_gemini_approved=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _db(department):
    db = MagicMock()
    query = db.query.return_value.filter.return_value
    query.first.return_value = department
    query.with_for_update.return_value.first.return_value = department
    return db


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_authenticated_principal, None)
    app.dependency_overrides.pop(get_db_dependency, None)


def test_revision_column_is_nonnull_and_defaults_to_zero():
    column = Department.__table__.c.lms_ai_policy_revision
    assert column.nullable is False
    assert column.default.arg == 0
    assert str(column.server_default.arg) == "0"


def test_update_contract_uses_strict_fields_and_forbids_secrets_and_legacy_purposes():
    from src.api.llm_providers import LMSAIPolicyUpdate

    valid = LMSAIPolicyUpdate.model_validate(
        {
            "enabled": True,
            "provider": "openai",
            "remediation_enabled": True,
            "alt_text_enabled": False,
            "expected_revision": 0,
        }
    )
    assert valid.expected_revision == 0
    for field, value in (
        ("enabled", 1),
        ("remediation_enabled", "true"),
        ("alt_text_enabled", 0),
        ("expected_revision", True),
        ("expected_revision", -1),
        ("api_key", "secret"),
        ("model", "gpt"),
        ("host", "http://evil"),
        ("pilot", True),
        ("purposes", ["remediation"]),
    ):
        payload = {
            "enabled": False,
            "provider": None,
            "remediation_enabled": False,
            "alt_text_enabled": False,
            "expected_revision": 0,
            field: value,
        }
        with pytest.raises(ValidationError):
            LMSAIPolicyUpdate.model_validate(payload)


def test_get_and_put_both_deny_faculty_before_department_lookup():
    client = TestClient(app)
    for method in ("get", "put"):
        db = _db(_department())
        app.dependency_overrides[get_authenticated_principal] = lambda: _principal(
            UserRole.FACULTY
        )
        app.dependency_overrides[get_db_dependency] = lambda: db
        kwargs = {}
        if method == "put":
            kwargs["json"] = {
                "enabled": False,
                "provider": None,
                "remediation_enabled": False,
                "alt_text_enabled": False,
                "expected_revision": 0,
            }
            kwargs["headers"] = {"Origin": "http://testserver"}
        response = getattr(client, method)("/llm/lms-policy", **kwargs)
        assert response.status_code == 403
        db.query.assert_not_called()


def test_cloud_readiness_decrypts_matching_byok_and_is_secret_free(caplog):
    from src.ai.lms_readiness import resolve_lms_ai_readiness

    plaintext = "plain-super-secret"
    calls = []
    dept = _department(
        byok_provider="openai", byok_api_key_encrypted="encrypted-secret"
    )
    readiness = resolve_lms_ai_readiness(
        dept,
        environment={},
        decrypt_api_key=lambda value: calls.append(value) or plaintext,
    )
    assert calls == ["encrypted-secret"]
    assert readiness["openai"].ready is True
    assert readiness["anthropic"].ready is False
    assert readiness["anthropic"].reason == "credential_provider_mismatch"
    assert "encrypted-secret" not in repr(readiness)
    assert plaintext not in repr(readiness)
    assert plaintext not in caplog.text

    pilot = _department(pilot_gemini_approved=True)
    readiness = resolve_lms_ai_readiness(
        pilot, environment={"GEMINI_API_KEY": "platform-secret"}
    )
    assert readiness["gemini"].ready is True
    assert readiness["gemini"].credential_source == "platform"
    assert "platform-secret" not in repr(readiness)


@pytest.mark.parametrize("decrypted", [None, "", b"not-text"])
def test_cloud_readiness_rejects_invalid_decrypted_byok(decrypted):
    from src.ai.lms_readiness import resolve_lms_ai_readiness

    readiness = resolve_lms_ai_readiness(
        _department(byok_provider="openai", byok_api_key_encrypted="ciphertext"),
        environment={},
        decrypt_api_key=lambda _value: decrypted,
    )
    assert readiness["openai"].ready is False
    assert readiness["openai"].reason == "credential_invalid"
    assert "ciphertext" not in repr(readiness)


def test_cloud_readiness_bounds_decrypt_exceptions_without_leaking(caplog):
    from src.ai.lms_readiness import resolve_lms_ai_readiness

    def fail(_value):
        raise RuntimeError("plaintext-must-not-leak")

    readiness = resolve_lms_ai_readiness(
        _department(byok_provider="openai", byok_api_key_encrypted="ciphertext"),
        environment={},
        decrypt_api_key=fail,
    )
    assert readiness["openai"].reason == "credential_invalid"
    assert "plaintext-must-not-leak" not in repr(readiness)
    assert "plaintext-must-not-leak" not in caplog.text


def test_stale_revision_returns_current_secret_free_policy_without_mutation_or_audit(
    monkeypatch,
):
    from src.api import llm_providers

    client = TestClient(app)
    dept = _department(lms_ai_policy_revision=3, byok_api_key_encrypted="must-not-leak")
    db = _db(dept)
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal()
    app.dependency_overrides[get_db_dependency] = lambda: db
    monkeypatch.setattr(llm_providers, "resolve_lms_ai_readiness", lambda *a, **k: {})

    response = client.put(
        "/llm/lms-policy",
        headers={"Origin": "http://testserver"},
        json={
            "enabled": False,
            "provider": None,
            "remediation_enabled": False,
            "alt_text_enabled": False,
            "expected_revision": 2,
        },
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "policy_revision_conflict"
    assert detail["reason"] == "stale_revision"
    assert detail["current"]["policy_revision"] == 3
    assert "must-not-leak" not in response.text
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_unready_provider_returns_typed_current_secret_free_policy(monkeypatch):
    from src.ai.lms_readiness import ProviderReadiness
    from src.api import llm_providers

    client = TestClient(app)
    dept = _department(byok_provider="openai", byok_api_key_encrypted="must-not-leak")
    db = _db(dept)
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal()
    app.dependency_overrides[get_db_dependency] = lambda: db
    monkeypatch.setattr(
        llm_providers,
        "resolve_lms_ai_readiness",
        lambda *args, **kwargs: {
            "openai": ProviderReadiness(
                False, "credential_invalid", "remote", "department_byok"
            )
        },
    )

    response = client.put(
        "/llm/lms-policy",
        headers={"Origin": "http://testserver"},
        json={
            "enabled": True,
            "provider": "openai",
            "remediation_enabled": True,
            "alt_text_enabled": False,
            "expected_revision": 0,
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert set(detail) == {"code", "reason", "current"}
    assert detail["code"] == "provider_not_ready"
    assert detail["reason"] == "credential_invalid"
    assert detail["current"]["policy_revision"] == 0
    assert "must-not-leak" not in response.text
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_ready_update_increments_revision_and_audits_allowlisted_transition(
    monkeypatch,
):
    from src.api import llm_providers
    from src.ai.lms_readiness import ProviderReadiness

    client = TestClient(app)
    dept = _department(byok_provider="openai", byok_api_key_encrypted="cipher")
    db = _db(dept)
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal(
        auth_method="api_key"
    )
    app.dependency_overrides[get_db_dependency] = lambda: db
    monkeypatch.setattr(
        llm_providers,
        "resolve_lms_ai_readiness",
        lambda *a, **k: {
            "openai": ProviderReadiness(True, "ready", "remote", "department_byok")
        },
    )

    response = client.put(
        "/llm/lms-policy",
        json={
            "enabled": True,
            "provider": "openai",
            "remediation_enabled": True,
            "alt_text_enabled": False,
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200
    assert response.json()["policy_revision"] == 1
    audit = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], AuditLog)
    )
    assert set(audit.details) == {
        "old",
        "new",
        "old_revision",
        "new_revision",
        "schema_version",
        "outcome",
    }
    assert "cipher" not in str(audit.details)


def test_openapi_policy_schemas_are_secret_free_and_document_concurrency():
    schema = app.openapi()
    operation = schema["paths"]["/llm/lms-policy"]["put"]
    request_schema = schema["components"]["schemas"]["LMSAIPolicyUpdate"]
    response_schema = schema["components"]["schemas"]["LMSAIPolicyResponse"]
    serialized = str(
        {"operation": operation, "request": request_schema, "response": response_schema}
    ).lower()
    assert "expected_revision" in serialized
    assert "policy_revision" in serialized
    assert "409" in operation["responses"]
    conflict_text = str(operation["responses"]["409"]).lower()
    assert "policy_revision_conflict" in conflict_text
    assert "provider_not_ready" in conflict_text
    assert "reason" in conflict_text
    assert "current" in conflict_text
    assert not any(
        name in request_schema["properties"]
        for name in ("api_key", "host", "model", "credentials")
    )
    assert not any(
        secret in conflict_text
        for secret in ("api_key", "cipher", "credential_hash", "credential_prefix")
    )
