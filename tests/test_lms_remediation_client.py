"""Task 14 slice 3A: policy-bound, one-provider LMS AI execution."""

import ast
import asyncio
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.ai.providers.base import LLMResponse
from src.ai.providers.types import ProviderConfig, ProviderType
from src.db.models import AuditLogStatus, Department, User


class FakeSession:
    def __init__(self, department=None, *, users=(), fail_commit=False):
        self.department = department
        self.users = {user.id: user for user in users}
        self.fail_commit = fail_commit
        self.added = []
        self.closed = False

    def get(self, model, identifier):
        if (
            model is Department
            and self.department is not None
            and self.department.id == identifier
        ):
            return self.department
        if model is User:
            return self.users.get(identifier)
        return None

    def add(self, value):
        self.added.append(value)

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("database URL and secret must not escape")

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class MutatingSession(FakeSession):
    def __init__(self, department, mutate):
        super().__init__(department)
        self.mutate = mutate

    def get(self, model, identifier):
        if model is Department:
            self.mutate()
        return super().get(model, identifier)


class SessionSequence:
    def __init__(self, *sessions):
        self.sessions = list(sessions)
        self.created = []

    def __call__(self):
        session = self.sessions.pop(0)
        self.created.append(session)
        return session


class RecordingProvider:
    def __init__(self, *, response=None, initialize=True, barrier=None):
        self.response = response or LLMResponse.success_response(
            content="safe result",
            provider="gemini",
            model="safe-model",
            inference_time=0.1,
        )
        self.initialize_result = initialize
        self.barrier = barrier
        self.initialized = 0
        self.operations = []
        self.closed = 0

    async def initialize(self):
        self.initialized += 1
        return self.initialize_result

    async def close(self):
        self.closed += 1

    async def generate_text(self, **kwargs):
        self.operations.append(("text", kwargs))
        return self.response

    async def generate_code(self, **kwargs):
        self.operations.append(("code", kwargs))
        return self.response

    async def analyze_image(self, **kwargs):
        self.operations.append(("vision", kwargs))
        return self.response


def department(**overrides):
    values = {
        "id": "dept-1",
        "lms_ai_enabled": True,
        "lms_ai_provider": "gemini",
        "lms_ai_purposes": ["remediation"],
        "byok_provider": "gemini",
        "byok_api_key_encrypted": "encrypted",
        "pilot_gemini_approved": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def build_client(
    dept,
    *,
    provider="gemini",
    purpose="remediation",
    decrypt=lambda value: "department-key",
    provider_factory=None,
    audit_sessions=None,
    environment=None,
    **ids,
):
    from src.ai.lms_remediation_client import LMSRemediationClient

    policy_session = FakeSession(dept)
    audits = audit_sessions or [FakeSession(dept), FakeSession(dept)]
    if len(audits) == 2:
        sessions = SessionSequence(
            policy_session, audits[0], FakeSession(dept), audits[1]
        )
    else:
        sessions = SessionSequence(policy_session, *audits)
    created = []

    def default_factory(provider_type, config):
        instance = RecordingProvider(
            response=LLMResponse.success_response(
                content="safe result",
                provider=provider,
                model="safe-model",
                inference_time=0.1,
            )
        )
        created.append((provider_type, config, instance))
        return instance

    client = LMSRemediationClient(
        department_id="dept-1",
        provider=provider,
        purpose=purpose,
        session_factory=sessions,
        provider_factory=provider_factory or default_factory,
        decrypt_api_key=decrypt,
        environment={} if environment is None else environment,
        **ids,
    )
    return client, sessions, created


def build_dispatch_mutation_client(
    dept, mutate, *, provider="gemini", environment=None, decrypt=lambda _: "key"
):
    from src.ai.lms_remediation_client import LMSRemediationClient

    sessions = SessionSequence(
        FakeSession(dept),
        FakeSession(dept),
        MutatingSession(dept, mutate),
        FakeSession(dept),
    )
    created = []

    def factory(provider_type, config):
        created.append((provider_type, config))
        return RecordingProvider(
            response=LLMResponse.success_response(
                content="safe result",
                provider=provider,
                model="safe-model",
                inference_time=0.1,
            )
        )

    client = LMSRemediationClient(
        department_id="dept-1",
        provider=provider,
        purpose="remediation",
        session_factory=sessions,
        provider_factory=factory,
        decrypt_api_key=decrypt,
        environment=environment,
    )
    return client, sessions, created


@pytest.mark.parametrize(
    "overrides",
    [
        {"lms_ai_enabled": False},
        {"lms_ai_provider": "bogus"},
        {"lms_ai_purposes": ["remediation", "remediation"]},
        {"lms_ai_purposes": ["alt_text"]},
    ],
)
def test_bind_if_allowed_fails_closed_without_provider_or_credentials(overrides):
    from src.ai.lms_remediation_client import LMSRemediationClient

    decrypt = MagicMock(side_effect=AssertionError("must not decrypt"))
    provider_factory = MagicMock(side_effect=AssertionError("must not construct"))
    session = FakeSession(department(**overrides))

    client = LMSRemediationClient.bind_if_allowed(
        department_id="dept-1",
        purpose="remediation",
        actor_id="user-1",
        cloud_file_id="file-1",
        session_factory=lambda: session,
        provider_factory=provider_factory,
        decrypt_api_key=decrypt,
        environment={},
    )

    assert client is None
    assert session.closed is True
    decrypt.assert_not_called()
    provider_factory.assert_not_called()


def test_bind_if_allowed_returns_purpose_bound_client_from_fresh_policy():
    from src.ai.lms_remediation_client import LMSRemediationClient

    session = FakeSession(
        department(lms_ai_provider="ollama", lms_ai_purposes=["alt_text"])
    )
    client = LMSRemediationClient.bind_if_allowed(
        department_id="dept-1",
        purpose="alt_text",
        actor_id="user-1",
        cloud_file_id="file-1",
        session_factory=lambda: session,
        environment={"OLLAMA_HOST": "http://127.0.0.1:11434"},
    )

    assert client is not None
    assert client.department_id == "dept-1"
    assert client.provider == "ollama"
    assert client.purpose == "alt_text"
    assert client.actor_id == "user-1"
    assert client.cloud_file_id == "file-1"
    assert session.closed is True


def test_import_and_denied_execution_do_not_load_provider_implementations():
    script = """
import sys
from types import SimpleNamespace

from src.ai.lms_remediation_client import LMSRemediationClient

class Session:
    def __init__(self, department=None):
        self.department = department
    def get(self, model, identifier):
        del model
        if self.department is not None and self.department.id == identifier:
            return self.department
        return None
    def add(self, value):
        del value
    def commit(self):
        pass
    def close(self):
        pass

department = SimpleNamespace(id="dept-1", lms_ai_enabled=False)
sessions = iter((Session(department), Session()))
client = LMSRemediationClient(
    department_id="dept-1",
    provider="gemini",
    purpose="remediation",
    session_factory=lambda: next(sessions),
)
assert client.generate_text_sync("secret")["error"] == "policy_denied"
provider_modules = {
    "src.ai.providers.gemini_provider",
    "src.ai.providers.ollama_provider",
    "src.ai.providers.openai_provider",
    "src.ai.providers.anthropic_provider",
    "src.ai.providers.xai_provider",
}
loaded = sorted(provider_modules.intersection(sys.modules))
assert not loaded, loaded
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "overrides,error_code",
    [
        ({"lms_ai_enabled": False}, "policy_denied"),
        ({"lms_ai_provider": "bogus"}, "policy_denied"),
        ({"lms_ai_purposes": ["remediation", "remediation"]}, "policy_denied"),
        ({"lms_ai_provider": "openai"}, "provider_changed"),
    ],
)
def test_denied_or_changed_policy_does_not_decrypt_construct_or_call(
    overrides, error_code
):
    decrypt = MagicMock(side_effect=AssertionError("must not decrypt"))
    factory = MagicMock(side_effect=AssertionError("must not construct"))
    client, sessions, _ = build_client(
        department(**overrides), decrypt=decrypt, provider_factory=factory
    )

    result = client.generate_text_sync("secret prompt")

    assert result["success"] is False
    assert result["error"] == error_code
    decrypt.assert_not_called()
    factory.assert_not_called()
    assert len(sessions.created) == 2
    audit = sessions.created[1].added[0]
    assert audit.details["call_made"] is False
    assert audit.details["error_code"] == error_code


@pytest.mark.parametrize(
    "overrides,error_code",
    [
        ({"byok_provider": "openai"}, "credential_provider_mismatch"),
        ({"byok_api_key_encrypted": None}, "credentials_unavailable"),
    ],
)
def test_cloud_byok_must_match_and_exist(overrides, error_code):
    factory = MagicMock()
    decrypt = MagicMock(return_value="key")
    client, _, _ = build_client(
        department(**overrides), decrypt=decrypt, provider_factory=factory
    )

    result = client.generate_text_sync("prompt")

    assert result["error"] == error_code
    factory.assert_not_called()
    if error_code == "credential_provider_mismatch":
        decrypt.assert_not_called()


def test_byok_decryption_failure_denies_without_platform_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "platform-secret")
    factory = MagicMock()
    client, _, _ = build_client(
        department(),
        decrypt=MagicMock(side_effect=RuntimeError("raw decrypt error")),
        provider_factory=factory,
        environment={"GEMINI_API_KEY": "platform-secret"},
    )

    result = client.generate_text_sync("prompt")

    assert result["error"] == "credential_decryption_failed"
    assert "raw" not in str(result)
    factory.assert_not_called()


def test_gemini_platform_credential_requires_explicit_entitlement():
    denied, _, denied_created = build_client(
        department(byok_provider=None, byok_api_key_encrypted=None),
        environment={"GEMINI_API_KEY": "platform-key"},
    )
    allowed, sessions, allowed_created = build_client(
        department(
            byok_provider=None,
            byok_api_key_encrypted=None,
            pilot_gemini_approved=True,
        ),
        environment={"GEMINI_API_KEY": "platform-key"},
    )

    assert denied.generate_text_sync("prompt")["error"] == "credentials_unavailable"
    result = allowed.generate_text_sync("prompt")

    assert result["success"] is True
    assert not denied_created
    assert allowed_created[0][1].api_key == "platform-key"
    assert allowed_created[0][1].api_base == (
        ProviderConfig.default_for_provider(ProviderType.GEMINI).api_base
    )
    assert sessions.created[1].added[0].details["credential_source"] == "platform"


def test_non_gemini_environment_key_never_authorizes_cloud_provider():
    client, _, created = build_client(
        department(
            lms_ai_provider="openai",
            byok_provider=None,
            byok_api_key_encrypted=None,
        ),
        provider="openai",
        environment={"OPENAI_API_KEY": "ambient-key"},
    )

    assert client.generate_text_sync("prompt")["error"] == "credentials_unavailable"
    assert not created


@pytest.mark.parametrize(
    "host",
    [
        "https://ollama.example.edu:11434",
        "http://10.0.0.8:11434",
        "http://user:pass@localhost:11434",
        "http://localhost.evil.test:11434",
        "not-a-url",
    ],
)
def test_ollama_rejects_public_private_ambiguous_or_credentialed_hosts(host):
    client, _, created = build_client(
        department(
            lms_ai_provider="ollama",
            byok_provider=None,
            byok_api_key_encrypted=None,
        ),
        provider="ollama",
        environment={"OLLAMA_HOST": host},
    )

    assert client.generate_text_sync("prompt")["error"] == "ollama_host_not_local"
    assert not created


@pytest.mark.parametrize(
    "host", ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"]
)
def test_ollama_accepts_loopback_and_passes_exact_host_to_real_config(host):
    client, _, created = build_client(
        department(
            lms_ai_provider="ollama",
            byok_provider=None,
            byok_api_key_encrypted=None,
        ),
        provider="ollama",
        environment={"OLLAMA_HOST": host},
    )

    result = client.generate_text_sync("prompt")

    assert result["success"] is True
    assert result["ai_used"] is True
    assert result["external_ai_used"] is False
    assert created[0][0] is ProviderType.OLLAMA
    expected_host = host.replace("localhost", "127.0.0.1")
    assert created[0][1].host == expected_host
    assert created[0][1].api_key is None


@pytest.mark.parametrize(
    "injected,ambient",
    [
        ({"OLLAMA_API_KEY": "injected-secret"}, None),
        ({}, "ambient-secret"),
        ({"OLLAMA_API_KEY": ""}, "ambient-secret"),
    ],
)
def test_ollama_rejects_any_injected_or_ambient_api_key(monkeypatch, injected, ambient):
    if ambient is None:
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OLLAMA_API_KEY", ambient)
    client, _, created = build_client(
        department(
            lms_ai_provider="ollama",
            byok_provider=None,
            byok_api_key_encrypted=None,
        ),
        provider="ollama",
        environment={"OLLAMA_HOST": "http://localhost:11434", **injected},
    )

    result = client.generate_text_sync("prompt")

    assert result["error"] == "ollama_credentials_forbidden"
    assert not created


def test_compatibility_methods_use_one_fresh_provider_and_close_it():
    client, _, created = build_client(
        department(),
        audit_sessions=[
            FakeSession(),
            FakeSession(department()),
            FakeSession(),
            FakeSession(department()),
            FakeSession(),
            FakeSession(department()),
            FakeSession(),
        ],
    )

    text = client.generate_text_sync("text")
    code = client.generate_code_sync("code", language="python")

    assert text["success"] and code["success"]
    assert [entry[2].operations[0][0] for entry in created] == ["text", "code"]
    assert all(entry[2].initialized == 1 for entry in created)
    assert all(entry[2].closed == 1 for entry in created)
    assert len({id(entry[2]) for entry in created}) == 2


def test_remediation_purpose_rejects_vision_without_provider_call():
    client, _, created = build_client(department(), purpose="remediation")

    result = client.analyze_image_sync(b"image bytes", "describe")

    assert result["success"] is False
    assert result["error"] == "purpose_operation_mismatch"
    assert result["ai_used"] is False
    assert result["external_ai_used"] is False
    assert result["purpose_outcome"] == "denied_at_dispatch"
    assert created == []


def test_provider_lifecycle_uses_one_event_loop_and_one_coroutine_runner(monkeypatch):
    import src.ai.lms_remediation_client as client_module

    class LoopBoundProvider(RecordingProvider):
        def __init__(self):
            super().__init__()
            self.loop = None

        async def initialize(self):
            self.loop = asyncio.get_running_loop()
            return await super().initialize()

        async def generate_text(self, **kwargs):
            assert asyncio.get_running_loop() is self.loop
            return await super().generate_text(**kwargs)

        async def close(self):
            assert asyncio.get_running_loop() is self.loop
            await super().close()

    provider = LoopBoundProvider()
    original_runner = client_module._run_coroutine
    runner = MagicMock(side_effect=original_runner)
    monkeypatch.setattr(client_module, "_run_coroutine", runner)
    client, _, _ = build_client(department(), provider_factory=lambda *_: provider)

    assert client.generate_text_sync("prompt")["success"] is True
    assert provider.closed == 1
    assert runner.call_count == 1


def test_provider_failure_is_sanitized_closed_and_never_falls_back():
    provider = RecordingProvider(
        response=LLMResponse.error_response(
            "raw URL https://secret.invalid?key=abc",
            "gemini",
            "model-x",
        )
    )
    factory = MagicMock(return_value=provider)
    client, sessions, _ = build_client(department(), provider_factory=factory)

    result = client.generate_text_sync("prompt")

    assert result["success"] is False
    assert result["error"] == "provider_call_failed"
    assert result["purpose_outcome"] == "attempted_failed"
    assert "secret" not in str(result)
    assert factory.call_count == 1
    assert provider.closed == 1
    assert sessions.created[-1].added[0].details["call_made"] is True


def test_provider_exception_audits_that_the_generation_call_was_made():
    class RaisingProvider(RecordingProvider):
        async def generate_text(self, **kwargs):
            del kwargs
            raise RuntimeError("raw provider URL and key")

    provider = RaisingProvider()
    client, sessions, _ = build_client(
        department(), provider_factory=lambda *_: provider
    )

    result = client.generate_text_sync("secret")

    assert result["error"] == "provider_call_failed"
    assert sessions.created[-1].added[0].details["call_made"] is True
    assert provider.closed == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "openai"},
        {"content": None},
        {"inference_time": -0.1},
        {"inference_time": float("nan")},
        {"inference_time": float("inf")},
        {"inference_time": True},
        {"model": "unsafe model"},
    ],
)
def test_malformed_success_response_is_rejected_without_content_or_success_audit(
    overrides,
):
    values = {
        "success": True,
        "content": "must not escape",
        "provider": "gemini",
        "model": "safe-model",
        "inference_time": 0.1,
    }
    values.update(overrides)
    response = LLMResponse(**values)
    client, sessions, _ = build_client(
        department(), provider_factory=lambda *_: RecordingProvider(response=response)
    )

    result = client.generate_text_sync("prompt")

    assert result["error"] == "invalid_provider_response"
    assert "content" not in result
    post_audit = sessions.created[-1].added[0]
    assert post_audit.details["outcome"] == "failure"
    assert post_audit.details["error_code"] == "invalid_provider_response"
    assert post_audit.status == AuditLogStatus.FAILURE.value


def test_provider_close_failure_discards_success_and_audits_failure():
    sensitive_marker = "SENSITIVE-CLOSE-MARKER"

    class CloseFailureProvider(RecordingProvider):
        async def close(self):
            self.closed += 1
            raise RuntimeError(sensitive_marker)

    provider = CloseFailureProvider()
    client, sessions, _ = build_client(
        department(), provider_factory=lambda *_: provider
    )

    result = client.generate_text_sync("prompt")

    assert result["success"] is False
    assert result["error"] == "provider_close_failed"
    assert "content" not in result
    assert sensitive_marker not in repr(result)
    post_audit = sessions.created[-1].added[0]
    assert post_audit.details["outcome"] == "failure"
    assert post_audit.status == AuditLogStatus.FAILURE.value
    assert post_audit.details["call_made"] is True
    assert post_audit.details["error_code"] == "provider_close_failed"
    assert sensitive_marker not in repr(post_audit.details)
    assert provider.closed == 1


@pytest.mark.parametrize("failure_mode", ["returned_false", "raised"])
def test_initialization_attempt_is_conservatively_audited(failure_mode):
    class InitializationFailureProvider(RecordingProvider):
        async def initialize(self):
            self.initialized += 1
            if failure_mode == "raised":
                raise RuntimeError("raw initialization URL and secret")
            return False

    provider = InitializationFailureProvider()
    client, sessions, _ = build_client(
        department(), provider_factory=lambda *_: provider
    )

    result = client.generate_text_sync("secret")

    assert result["error"] == "provider_initialization_failed"
    assert "raw initialization" not in str(result)
    assert sessions.created[1].added[0].details["call_made"] is False
    post_audit = sessions.created[-1].added[0].details
    assert post_audit["call_made"] is True
    assert post_audit["error_code"] == "provider_initialization_failed"
    assert "raw initialization" not in repr(post_audit)
    assert provider.operations == []
    assert provider.closed == 1


def test_revocation_after_client_construction_is_observed_on_each_call():
    current = department()
    policy_sessions = [FakeSession(current), FakeSession()]

    def session_factory():
        return policy_sessions.pop(0)

    from src.ai.lms_remediation_client import LMSRemediationClient

    factory = MagicMock()
    client = LMSRemediationClient(
        department_id="dept-1",
        provider="gemini",
        purpose="remediation",
        session_factory=session_factory,
        provider_factory=factory,
        decrypt_api_key=lambda value: "key",
        environment={},
    )
    current.lms_ai_enabled = False

    result = client.generate_text_sync("prompt")

    assert result["error"] == "policy_denied"
    factory.assert_not_called()


def test_credential_snapshot_is_copied_before_policy_session_closes():
    class DetachingDepartment:
        detached = False

        def __init__(self):
            self.id = "dept-1"
            self.lms_ai_enabled = True
            self.lms_ai_provider = "gemini"
            self.lms_ai_purposes = ["remediation"]
            self.byok_provider = "gemini"
            self.byok_api_key_encrypted = "encrypted"
            self.pilot_gemini_approved = False

        def __getattribute__(self, name):
            if name not in {
                "detached",
                "__dict__",
                "__class__",
            } and object.__getattribute__(self, "detached"):
                raise RuntimeError("detached ORM access")
            return object.__getattribute__(self, name)

    guarded = DetachingDepartment()
    current = department()

    class DetachingSession(FakeSession):
        def close(self):
            super().close()
            guarded.detached = True

    sessions = SessionSequence(
        DetachingSession(guarded),
        FakeSession(current),
        FakeSession(current),
        FakeSession(current),
    )
    from src.ai.lms_remediation_client import LMSRemediationClient

    client = LMSRemediationClient(
        department_id="dept-1",
        provider="gemini",
        purpose="remediation",
        session_factory=sessions,
        provider_factory=lambda *_: RecordingProvider(),
        decrypt_api_key=lambda _: "key",
        environment={},
    )

    assert client.generate_text_sync("prompt")["success"] is True


@pytest.mark.parametrize(
    "changed,error_code",
    [
        ({"lms_ai_enabled": False}, "policy_denied"),
        ({"lms_ai_provider": "openai"}, "provider_changed"),
    ],
)
def test_second_recheck_denies_revocation_or_provider_change(changed, error_code):
    initial = department()
    revised = department(**changed)
    sessions = SessionSequence(
        FakeSession(initial),
        FakeSession(initial),
        FakeSession(revised),
        FakeSession(revised),
    )
    factory = MagicMock(side_effect=AssertionError("must not construct"))
    from src.ai.lms_remediation_client import LMSRemediationClient

    client = LMSRemediationClient(
        department_id="dept-1",
        provider="gemini",
        purpose="remediation",
        session_factory=sessions,
        provider_factory=factory,
        decrypt_api_key=lambda _: "key",
        environment={},
    )

    result = client.generate_text_sync("prompt")

    assert result["error"] == error_code
    assert result["ai_used"] is False
    assert result["external_ai_used"] is False
    assert result["purpose_outcome"] == "denied_at_dispatch"
    factory.assert_not_called()
    final_audit = sessions.created[-1].added[0]
    assert final_audit.details["call_made"] is False
    assert final_audit.details["error_code"] == error_code


def test_dispatch_recheck_uses_current_byok_snapshot_after_rotation():
    initial = department()
    revised = department(byok_api_key_encrypted="replacement")
    sessions = SessionSequence(
        FakeSession(initial),
        FakeSession(initial),
        FakeSession(revised),
        FakeSession(revised),
    )
    created = []

    def factory(_, config):
        created.append(config)
        return RecordingProvider()

    from src.ai.lms_remediation_client import LMSRemediationClient

    client = LMSRemediationClient(
        department_id="dept-1",
        provider="gemini",
        purpose="remediation",
        session_factory=sessions,
        provider_factory=factory,
        decrypt_api_key=lambda encrypted: f"key-for-{encrypted}",
        environment={},
    )

    result = client.generate_text_sync("prompt")

    assert result["success"] is True
    assert result["ai_used"] is True
    assert result["external_ai_used"] is True
    assert result["purpose_outcome"] == "used"
    assert created[0].api_key == "key-for-replacement"
    assert sessions.created[-1].added[0].details["credential_source"] == (
        "department_byok"
    )


def test_dispatch_recheck_denies_new_ambient_ollama_api_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    environment = {"OLLAMA_HOST": "http://localhost:11434"}
    client, sessions, created = build_dispatch_mutation_client(
        department(
            lms_ai_provider="ollama",
            byok_provider=None,
            byok_api_key_encrypted=None,
        ),
        lambda: monkeypatch.setenv("OLLAMA_API_KEY", "late-secret"),
        provider="ollama",
        environment=environment,
    )

    result = client.generate_text_sync("prompt")

    assert result["error"] == "ollama_credentials_forbidden"
    assert not created
    denied_audit = sessions.created[-1].added[0].details
    assert denied_audit["call_made"] is False
    assert denied_audit["error_code"] == "ollama_credentials_forbidden"


def test_dispatch_recheck_uses_rotated_local_ollama_host():
    environment = {"OLLAMA_HOST": "http://localhost:11434"}
    client, sessions, created = build_dispatch_mutation_client(
        department(
            lms_ai_provider="ollama",
            byok_provider=None,
            byok_api_key_encrypted=None,
        ),
        lambda: environment.update(OLLAMA_HOST="http://[::1]:11435"),
        provider="ollama",
        environment=environment,
    )

    result = client.generate_text_sync("prompt")

    assert result["success"] is True
    assert created[0][1].host == "http://[::1]:11435"
    assert sessions.created[-1].added[0].details["credential_source"] == "local"


def test_dispatch_recheck_denies_ollama_host_that_becomes_nonlocal():
    environment = {"OLLAMA_HOST": "http://localhost:11434"}
    client, sessions, created = build_dispatch_mutation_client(
        department(
            lms_ai_provider="ollama",
            byok_provider=None,
            byok_api_key_encrypted=None,
        ),
        lambda: environment.update(OLLAMA_HOST="https://ollama.example.edu"),
        provider="ollama",
        environment=environment,
    )

    result = client.generate_text_sync("prompt")

    assert result["error"] == "ollama_host_not_local"
    assert not created
    denied_audit = sessions.created[-1].added[0].details
    assert denied_audit["call_made"] is False
    assert denied_audit["error_code"] == "ollama_host_not_local"


@pytest.mark.parametrize("replacement", [None, "platform-key-2"])
def test_dispatch_recheck_uses_current_gemini_platform_key_or_denies(replacement):
    environment = {"GEMINI_API_KEY": "platform-key-1"}

    def mutate():
        if replacement is None:
            environment.pop("GEMINI_API_KEY")
        else:
            environment["GEMINI_API_KEY"] = replacement

    client, sessions, created = build_dispatch_mutation_client(
        department(
            byok_provider=None,
            byok_api_key_encrypted=None,
            pilot_gemini_approved=True,
        ),
        mutate,
        environment=environment,
    )

    result = client.generate_text_sync("prompt")

    if replacement is None:
        assert result["error"] == "credentials_unavailable"
        assert not created
        denied_audit = sessions.created[-1].added[0].details
        assert denied_audit["call_made"] is False
        assert denied_audit["error_code"] == "credentials_unavailable"
    else:
        assert result["success"] is True
        assert created[0][1].api_key == replacement
        assert sessions.created[-1].added[0].details["credential_source"] == "platform"


def test_dispatch_recheck_uses_second_byok_decryption_result():
    decrypt = MagicMock(side_effect=["department-key-1", "department-key-2"])
    client, sessions, created = build_dispatch_mutation_client(
        department(), lambda: None, environment={}, decrypt=decrypt
    )

    result = client.generate_text_sync("prompt")

    assert result["success"] is True
    assert decrypt.call_count == 2
    assert created[0][1].api_key == "department-key-2"
    assert sessions.created[-1].added[0].details["credential_source"] == (
        "department_byok"
    )


def test_dispatch_recheck_denies_second_byok_decryption_failure():
    decrypt = MagicMock(side_effect=["department-key-1", RuntimeError("late failure")])
    client, sessions, created = build_dispatch_mutation_client(
        department(), lambda: None, environment={}, decrypt=decrypt
    )

    result = client.generate_text_sync("prompt")

    assert result["error"] == "credential_decryption_failed"
    assert decrypt.call_count == 2
    assert not created
    denied_audit = sessions.created[-1].added[0].details
    assert denied_audit["call_made"] is False
    assert denied_audit["error_code"] == "credential_decryption_failed"


def test_dispatch_recheck_denies_current_byok_provider_mismatch():
    current = department()
    decrypt = MagicMock(return_value="department-key")
    client, sessions, created = build_dispatch_mutation_client(
        current,
        lambda: setattr(current, "byok_provider", "openai"),
        environment={},
        decrypt=decrypt,
    )

    result = client.generate_text_sync("prompt")

    assert result["error"] == "credential_provider_mismatch"
    decrypt.assert_called_once_with("encrypted")
    assert not created
    denied_audit = sessions.created[-1].added[0].details
    assert denied_audit["call_made"] is False
    assert denied_audit["error_code"] == "credential_provider_mismatch"


def test_pre_call_audit_failure_fails_closed_and_post_call_failure_masks_success():
    pre_client, _, pre_created = build_client(
        department(), audit_sessions=[FakeSession(fail_commit=True)]
    )
    post_client, _, post_created = build_client(
        department(),
        audit_sessions=[FakeSession(), FakeSession(fail_commit=True)],
    )

    pre_result = pre_client.generate_text_sync("prompt")
    post_result = post_client.generate_text_sync("prompt")

    assert pre_result["error"] == "audit_write_failed"
    assert not pre_created
    assert post_result["error"] == "post_call_audit_failed"
    assert post_created[0][2].operations
    assert post_created[0][2].closed == 1


def test_audit_session_creation_failure_is_stable_and_prevents_provider_creation():
    from src.ai.lms_remediation_client import LMSRemediationClient

    calls = 0

    def session_factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeSession(department())
        raise RuntimeError("raw audit database URL")

    factory = MagicMock()
    client = LMSRemediationClient(
        department_id="dept-1",
        provider="gemini",
        purpose="remediation",
        session_factory=session_factory,
        provider_factory=factory,
        decrypt_api_key=lambda value: "key",
        environment={},
    )

    result = client.generate_text_sync("secret")

    assert result["error"] == "audit_write_failed"
    assert "database" not in str(result)
    factory.assert_not_called()


def test_missing_department_audit_keeps_attempted_id_only_in_details():
    client, sessions, _ = build_client(None, actor_id="attempted-actor")

    result = client.generate_text_sync("prompt")

    assert result["error"] == "department_not_found"
    audit = sessions.created[1].added[0]
    assert audit.department_id is None
    assert audit.user_id is None
    assert audit.details["department_id"] == "dept-1"
    assert audit.details["actor_id"] == "attempted-actor"


def test_cross_department_actor_is_not_attached_to_audit_foreign_key():
    resolved_department = department(lms_ai_enabled=False)
    other_actor = SimpleNamespace(id="actor-2", department_id="dept-2")
    audit_session = FakeSession(resolved_department, users=[other_actor])
    client, sessions, _ = build_client(
        resolved_department,
        actor_id="actor-2",
        audit_sessions=[audit_session],
    )

    assert client.generate_text_sync("prompt")["error"] == "policy_denied"

    audit = sessions.created[1].added[0]
    assert audit.department_id == "dept-1"
    assert audit.user_id is None
    assert audit.details["actor_id"] == "actor-2"


def test_audit_metadata_is_allowlisted_and_contains_no_payload_or_raw_error():
    raw_prompt = "TOP SECRET PROMPT"
    raw_response = "TOP SECRET RESPONSE"
    raw_image = b"TOP SECRET IMAGE"
    response = LLMResponse.error_response(raw_response, "gemini", "safe-model")
    provider = RecordingProvider(response=response)
    client, sessions, _ = build_client(
        department(lms_ai_purposes=["alt_text"]),
        purpose="alt_text",
        provider_factory=lambda *_: provider,
        actor_id="actor-1",
        job_id="job-1",
        scan_id="scan-1",
        cloud_file_id="file-1",
    )

    client.analyze_image_sync(raw_image, raw_prompt)

    details = sessions.created[-1].added[0].details
    assert set(details) == {
        "department_id",
        "actor_id",
        "job_id",
        "scan_id",
        "cloud_file_id",
        "purpose",
        "operation",
        "provider",
        "locality",
        "credential_source",
        "policy_version",
        "policy_reason",
        "call_made",
        "outcome",
        "error_code",
        "model",
    }
    serialized = repr(details)
    assert raw_prompt not in serialized
    assert raw_response not in serialized
    assert raw_image.decode() not in serialized
    assert "safe-model" in serialized


@pytest.mark.parametrize(
    "unsafe_model",
    [
        "safe-model\nSENSITIVE-MARKER",
        "model\x00SENSITIVE-MARKER",
        "content-like model identifier with spaces",
        "m" * 129,
    ],
)
def test_unsafe_provider_model_makes_success_response_invalid(
    unsafe_model,
):
    generated_content = "generated content\nSENSITIVE-MARKER"
    response = LLMResponse.success_response(
        content=generated_content,
        provider="gemini",
        model=unsafe_model,
        inference_time=0.1,
    )
    client, sessions, _ = build_client(
        department(), provider_factory=lambda *_: RecordingProvider(response=response)
    )

    result = client.generate_text_sync("prompt")

    assert result["success"] is False
    assert result["error"] == "invalid_provider_response"
    assert "content" not in result
    assert result["model"] == ""
    assert sessions.created[-1].added[0].details["model"] is None
    assert unsafe_model not in repr(sessions.created[-1].added[0].details)


def test_binding_is_immutable_and_invalid_purpose_rejected_at_construction():
    client, _, _ = build_client(department())
    with pytest.raises(FrozenInstanceError):
        client.provider = "openai"

    from src.ai.lms_remediation_client import LMSRemediationClient

    with pytest.raises(ValueError, match="purpose"):
        LMSRemediationClient(department_id="dept-1", provider="gemini", purpose="other")


def test_concurrent_calls_construct_independent_providers():
    sessions = []
    created = []

    def session_factory():
        # Any session may become a policy or audit session under interleaving.
        session = FakeSession(department())
        sessions.append(session)
        return session

    def factory(provider_type, config):
        del provider_type, config
        instance = RecordingProvider()
        created.append(instance)
        return instance

    from src.ai.lms_remediation_client import LMSRemediationClient

    client = LMSRemediationClient(
        department_id="dept-1",
        provider="gemini",
        purpose="remediation",
        session_factory=session_factory,
        provider_factory=factory,
        decrypt_api_key=lambda value: "key",
        environment={},
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(client.generate_text_sync, [f"p{i}" for i in range(4)]))

    assert all(result["success"] for result in results)
    assert len(created) == 4
    assert len({id(provider) for provider in created}) == 4
    assert all(provider.closed == 1 for provider in created)


def test_client_ast_has_no_manager_cache_fallback_or_provider_singleton_references():
    path = Path(__file__).parents[1] / "src/ai/lms_remediation_client.py"
    tree = ast.parse(path.read_text())
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    source = path.read_text().lower()

    assert "get_provider_manager" not in names
    assert "cache" not in names
    assert "fallback" not in source
    assert "provider_manager" not in source


def test_ollama_provider_uses_one_host_bound_client_for_full_lifecycle(monkeypatch):
    from src.ai.providers.ollama_provider import OllamaProvider

    configured_host = "http://127.0.0.1:11555"
    models = [
        "gemma3:4b",
        "qwen2.5-coder:7b",
        "qwen2.5vl:3b",
        "nomic-embed-text",
    ]
    fake_client = MagicMock()
    fake_client.list.return_value = {
        "models": [{"name": model_name} for model_name in models]
    }
    fake_client.chat.return_value = {"message": {"content": "host-bound result"}}
    fake_client.embeddings.return_value = {"embedding": [0.1, 0.2, 0.3]}
    fake_ollama = SimpleNamespace(
        Client=MagicMock(return_value=fake_client),
        list=MagicMock(side_effect=AssertionError("module-global list forbidden")),
        chat=MagicMock(side_effect=AssertionError("module-global chat forbidden")),
        generate=MagicMock(
            side_effect=AssertionError("module-global generate forbidden")
        ),
        embeddings=MagicMock(
            side_effect=AssertionError("module-global embeddings forbidden")
        ),
    )
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama)
    config = ProviderConfig.default_for_provider(ProviderType.OLLAMA)
    config.host = configured_host
    provider = OllamaProvider(config)

    initialized = asyncio.run(provider.initialize())
    generated = asyncio.run(provider.generate_text("prompt"))
    embedded = asyncio.run(provider.generate_embedding("text"))
    health = provider.health_check()
    available = provider.is_available
    asyncio.run(provider.close())

    assert initialized is True
    assert generated.success is True
    assert generated.content == "host-bound result"
    assert embedded.success is True
    assert embedded.metadata == {"embedding": [0.1, 0.2, 0.3], "dimensions": 3}
    assert health["status"] == "healthy"
    assert available is True
    fake_ollama.Client.assert_called_once_with(
        host=configured_host, follow_redirects=False, timeout=120
    )
    assert provider._client is None
    fake_client.chat.assert_called_once()
    fake_client.embeddings.assert_called_once_with(
        model="nomic-embed-text", prompt="text"
    )
    assert fake_client.list.call_count == 3
    fake_client.close.assert_called_once_with()
    fake_ollama.list.assert_not_called()
    fake_ollama.chat.assert_not_called()
    fake_ollama.generate.assert_not_called()
    fake_ollama.embeddings.assert_not_called()


@pytest.mark.parametrize(
    "configured,expected", [(0, 1), (-5, 1), (121, 120), (float("inf"), 120)]
)
def test_ollama_client_timeout_is_finite_and_bounded(monkeypatch, configured, expected):
    from src.ai.providers.ollama_provider import OllamaProvider

    fake_ollama = SimpleNamespace(Client=MagicMock(return_value=MagicMock()))
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama)
    config = ProviderConfig.default_for_provider(ProviderType.OLLAMA)
    config.host = "http://127.0.0.1:11434"
    config.timeout = configured

    provider = OllamaProvider(config)
    assert provider._client is None
    provider._get_client()

    fake_ollama.Client.assert_called_once_with(
        host=config.host, follow_redirects=False, timeout=expected
    )
