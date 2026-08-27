"""Configuration and documentation contract for self-host provisioning (#75)."""

from pathlib import Path

from cryptography.fernet import Fernet

from src.config.settings import Settings
from src.middleware.security import CSRFMiddleware

ROOT = Path(__file__).resolve().parents[1]


def _settings(**overrides):
    values = {
        "database_url": "postgresql://user:pass@localhost:5432/aelira",
        "jwt_secret": "a-real-secret-that-is-not-a-placeholder-12345",
        "jwt_algorithm": "HS256",
        "session_replay_encryption_key": Fernet.generate_key().decode(),
        "smtp_host": "smtp.example.com",
    }
    values.update(overrides)
    return Settings(**values)


def test_department_creation_is_closed_by_default_and_can_be_opted_in():
    assert _settings().allow_public_department_creation is False
    assert (
        _settings(
            allow_public_department_creation=True
        ).allow_public_department_creation
        is True
    )


def test_department_creation_is_not_csrf_exempt():
    assert not any(
        "/auth/departments".startswith(path) for path in CSRFMiddleware.EXEMPT_PATHS
    )


def test_shipped_configuration_documents_explicit_public_opt_in():
    env_example = (ROOT / ".env.example").read_text()
    assert "ALLOW_PUBLIC_DEPARTMENT_CREATION=false" in env_example
    assert "first" in env_example.lower() and "admin" in env_example.lower()


def test_self_hosting_guide_documents_provisioning_sequence():
    guide = (ROOT / "docs/deployment/self-hosting.md").read_text().lower()
    assert "allow_public_department_creation" in guide
    assert "first" in guide and "administrator" in guide
    assert "post /auth/departments" in guide
