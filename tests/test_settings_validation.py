"""
Tests for startup validation of critical environment variables (#39).

These tests instantiate src.config.settings.Settings directly with explicit
kwargs so they never depend on the process environment or a real database —
kwargs always win over both env vars and the .env file in pydantic-settings,
so passing them explicitly keeps these tests hermetic regardless of what
ambient env vars (DATABASE_URL, JWT_SECRET, ...) happen to be set in the
runner.
"""

import logging

import pytest

from src.config.settings import Settings


def _base_kwargs(**overrides):
    """A minimal set of kwargs that should always pass validation.

    Individual tests override one field at a time to isolate the behaviour
    under test.
    """
    kwargs = {
        "database_url": "postgresql://user:pass@localhost:5432/aelira",
        "jwt_secret": "a-real-secret-that-is-not-a-placeholder-12345",
        "jwt_algorithm": "HS256",
        "smtp_host": "smtp.example.com",
    }
    kwargs.update(overrides)
    return kwargs


class TestValidConfig:
    def test_valid_config_passes(self):
        """A fully-populated, sane config should construct without error."""
        settings = Settings(**_base_kwargs())

        assert settings.database_url == "postgresql://user:pass@localhost:5432/aelira"
        assert settings.jwt_secret == "a-real-secret-that-is-not-a-placeholder-12345"
        assert settings.smtp_host == "smtp.example.com"

    def test_valid_config_passes_with_asyncpg_scheme(self):
        """postgresql+asyncpg:// is an explicitly supported scheme."""
        settings = Settings(
            **_base_kwargs(
                database_url="postgresql+asyncpg://user:pass@localhost:5432/aelira"
            )
        )
        assert settings.database_url.startswith("postgresql+asyncpg://")


class TestDatabaseUrlValidation:
    def test_missing_database_url_fails(self):
        """An empty DATABASE_URL must fail fast at startup, not first query."""
        with pytest.raises(ValueError, match="DATABASE_URL must be set"):
            Settings(**_base_kwargs(database_url=""))

    def test_bad_database_url_scheme_fails(self):
        """A non-Postgres scheme (e.g. mysql://, sqlite://) must fail fast."""
        with pytest.raises(ValueError, match="DATABASE_URL must start with"):
            Settings(**_base_kwargs(database_url="mysql://user:pass@localhost/db"))

    def test_database_url_without_scheme_separator_fails(self):
        """A malformed value with no '://' at all still fails cleanly."""
        with pytest.raises(ValueError, match="DATABASE_URL must start with"):
            Settings(**_base_kwargs(database_url="not-a-url-at-all"))

    def test_unsafe_default_database_url_fails(self):
        """Known-unsafe default passwords must still be rejected (pre-existing check)."""
        with pytest.raises(ValueError, match="Unsafe DATABASE_URL"):
            Settings(
                **_base_kwargs(
                    database_url="postgresql://user:change_me@localhost:5432/db"
                )
            )


class TestJwtSecretValidation:
    def test_missing_jwt_secret_fails_for_hs256(self):
        """Empty JWT_SECRET under HS256 (the default) must fail fast."""
        with pytest.raises(ValueError, match="JWT_SECRET must be set"):
            Settings(**_base_kwargs(jwt_secret=""))

    def test_missing_jwt_secret_ok_for_rs256(self):
        """RS256 deployments sign with key files and legitimately leave
        JWT_SECRET empty (see src/auth/jwt_service.py) — must not fail."""
        settings = Settings(**_base_kwargs(jwt_secret="", jwt_algorithm="RS256"))
        assert settings.jwt_secret == ""
        assert settings.jwt_algorithm == "RS256"

    @pytest.mark.parametrize(
        "placeholder",
        ["your-jwt-secret-here", "quickstart-insecure-change-me"],
    )
    def test_placeholder_jwt_secret_warns_but_passes(self, placeholder, caplog):
        """Known placeholders (incl. the quickstart's deliberate default)
        must WARN, never hard-fail — hard-failing on
        quickstart-insecure-change-me would break the zero-config quickstart."""
        with caplog.at_level(logging.WARNING):
            settings = Settings(**_base_kwargs(jwt_secret=placeholder))

        assert settings.jwt_secret == placeholder
        assert any("placeholder" in record.message.lower() for record in caplog.records)

    def test_real_jwt_secret_does_not_warn(self, caplog):
        """A real secret should not trigger the placeholder warning."""
        with caplog.at_level(logging.WARNING):
            Settings(**_base_kwargs(jwt_secret="a-genuinely-random-secret-value"))

        assert not any(
            "placeholder" in record.message.lower() for record in caplog.records
        )


class TestSmtpHostValidation:
    def test_missing_smtp_warns_but_passes(self, caplog):
        """SMTP_HOST unset should warn (email won't work) but not fail startup."""
        with caplog.at_level(logging.WARNING):
            settings = Settings(**_base_kwargs(smtp_host=""))

        assert settings.smtp_host == ""
        assert any("SMTP_HOST" in record.message for record in caplog.records)

    def test_configured_smtp_does_not_warn(self, caplog):
        with caplog.at_level(logging.WARNING):
            Settings(**_base_kwargs(smtp_host="smtp.example.com"))

        assert not any("SMTP_HOST" in record.message for record in caplog.records)


class TestCanvasOAuthAllowlistValidation:
    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_deployed_environment_without_canvas_oauth_does_not_require_allowlist(
        self, env, monkeypatch
    ):
        monkeypatch.delenv("CANVAS_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("CANVAS_OAUTH_CLIENT_SECRET", raising=False)

        settings = Settings(**_base_kwargs(env=env, canvas_oauth_allowed_origins=""))

        assert settings.canvas_oauth_allowed_origins == ""

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_deployed_environment_with_canvas_oauth_requires_allowlist(
        self, env, monkeypatch
    ):
        with monkeypatch.context() as context:
            context.setenv("CANVAS_OAUTH_CLIENT_ID", "client-id")
            context.setenv("CANVAS_OAUTH_CLIENT_SECRET", "client-secret")
            with pytest.raises(ValueError, match="CANVAS_OAUTH_ALLOWED_ORIGINS"):
                Settings(
                    **_base_kwargs(
                        env=env,
                        canvas_oauth_allowed_origins="",
                    )
                )

    @pytest.mark.parametrize("env", ["development", "test"])
    def test_local_and_test_environments_do_not_require_canvas_allowlist(self, env):
        settings = Settings(
            **_base_kwargs(
                env=env,
                canvas_oauth_allowed_origins="",
            )
        )

        assert settings.canvas_oauth_allowed_origins == ""
