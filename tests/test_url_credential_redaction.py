"""Tests for redact_url_credentials.

Service URLs carry credentials inline. The Redis client logged its connection
URL verbatim on every startup, which wrote the password to stdout, shipped it
to Loki and attached it to Sentry breadcrumbs.
"""

import logging

import pytest

from src.utils.security import redact_url_credentials

# Deliberately not shaped like a real credential. An earlier version of this
# file used a fragment of the actual Redis password, which is how a secret
# scanner ends up flagging your own test suite.
SECRET = "not-a-real-password-placeholder"


@pytest.mark.parametrize(
    "url",
    [
        f"redis://:{SECRET}@aelira-backend-redis:6379/0",
        f"redis://default:{SECRET}@aelira-backend-redis:6379/0",
        f"postgresql://aelira:{SECRET}@aelira-db:5432/aelira",
        f"amqp://user:{SECRET}@broker:5672/vhost",
        f"https://user:{SECRET}@example.com/path?x=1",
    ],
)
def test_password_never_survives(url):
    """Regression: the credential must not appear in the redacted output."""
    redacted = redact_url_credentials(url)
    assert SECRET not in redacted


def test_diagnostic_shape_is_kept():
    """Redaction is useless if it hides what you need to debug a connection."""
    redacted = redact_url_credentials(f"redis://:{SECRET}@aelira-backend-redis:6379/0")
    assert redacted == "redis://:***@aelira-backend-redis:6379/0"


def test_username_is_preserved():
    redacted = redact_url_credentials(f"postgresql://aelira:{SECRET}@db:5432/aelira")
    assert redacted == "postgresql://aelira:***@db:5432/aelira"


@pytest.mark.parametrize(
    "url",
    [
        "redis://aelira-backend-redis:6379/0",
        "postgresql://db:5432/aelira",
        "http://localhost:8000/health",
    ],
)
def test_urls_without_a_password_are_untouched(url):
    assert redact_url_credentials(url) == url


@pytest.mark.parametrize("url", ["", None])
def test_empty_input_is_safe(url):
    assert redact_url_credentials(url) == ""


def test_unparseable_input_does_not_fall_back_to_the_original():
    """A malformed URL must not leak by being passed through verbatim."""
    result = redact_url_credentials(f"redis://:{SECRET}@[unclosed")
    assert SECRET not in result


def test_redis_client_logs_a_redacted_url(monkeypatch, caplog):
    """End to end: connecting must not write the password to the log."""
    from src.auth import redis_rate_limiter as rrl

    url = f"redis://:{SECRET}@aelira-backend-redis:6379/0"

    class _FakeClient:
        def ping(self):
            return True

    monkeypatch.setattr(rrl, "_redis_client", None)
    monkeypatch.setattr(rrl._settings, "redis_enabled", True, raising=False)
    monkeypatch.setattr(rrl._settings, "redis_url", url, raising=False)
    monkeypatch.setattr(rrl.redis, "from_url", lambda *a, **k: _FakeClient())

    with caplog.at_level(logging.DEBUG):
        rrl.get_redis_client()

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert SECRET not in logged
    assert "aelira-backend-redis:6379" in logged, "host detail should survive"

    monkeypatch.setattr(rrl, "_redis_client", None)
