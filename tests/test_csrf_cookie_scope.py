"""Regression coverage for independent CSRF and session cookie scope (#149)."""

from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pathlib import Path
import pytest
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response

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


def test_settings_exposes_independent_csrf_cookie_domain():
    settings = _settings(
        csrf_cookie_domain=".example.org",
        session_cookie_domain="",
    )

    assert settings.csrf_cookie_domain == ".example.org"
    assert settings.session_cookie_domain == ""


def test_cookie_domains_default_to_host_only():
    settings = _settings()

    assert settings.csrf_cookie_domain == ""
    assert settings.session_cookie_domain == ""


def test_session_cookie_domain_does_not_scope_csrf_cookie():
    settings = _settings(
        csrf_cookie_domain="",
        session_cookie_domain=".example.org",
    )

    assert settings.csrf_cookie_domain == ""
    assert settings.session_cookie_domain == ".example.org"


@pytest.mark.parametrize("field", ["csrf_cookie_domain", "session_cookie_domain"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_cookie_domain_values_are_host_only(field, value):
    assert getattr(_settings(**{field: value}), field) == ""


@pytest.mark.parametrize("field", ["csrf_cookie_domain", "session_cookie_domain"])
@pytest.mark.parametrize(
    "value",
    [
        "https://example.org",
        ".example.org:443",
        "example.org/path",
        "*.example.org",
        "localhost",
        "example..org",
        "-example.org",
        "example.org.",
    ],
)
def test_invalid_cookie_domains_fail_startup(field, value):
    with pytest.raises(ValueError, match=field.upper()):
        _settings(**{field: value})


def test_cookie_domains_are_trimmed_and_normalized_to_lowercase():
    settings = _settings(
        csrf_cookie_domain="  .Example.ORG  ",
        session_cookie_domain="Login.Example.ORG",
    )

    assert settings.csrf_cookie_domain == ".example.org"
    assert settings.session_cookie_domain == "login.example.org"


def test_csrf_cookie_domain_loads_from_environment(monkeypatch):
    monkeypatch.setenv("CSRF_COOKIE_DOMAIN", ".Example.ORG")

    assert _settings().csrf_cookie_domain == ".example.org"


def test_application_wires_csrf_domain_independently():
    main_source = (ROOT / "src/api/main.py").read_text()
    csrf_block = main_source.split("# CSRF Protection middleware", 1)[1].split(
        "# Prometheus metrics middleware", 1
    )[0]

    assert "settings.csrf_cookie_domain" in csrf_block
    assert "settings.session_cookie_domain" not in csrf_block


def test_self_hosting_contract_documents_independent_cookie_scope():
    env_example = (ROOT / ".env.example").read_text()
    self_hosting = (ROOT / "docs/deployment/self-hosting.md").read_text()
    session_row = next(
        line
        for line in self_hosting.splitlines()
        if line.startswith("| `SESSION_COOKIE_DOMAIN`")
    )
    csrf_row = next(
        line
        for line in self_hosting.splitlines()
        if line.startswith("| `CSRF_COOKIE_DOMAIN`")
    )

    assert "# SESSION_COOKIE_DOMAIN=" in env_example
    assert "# CSRF_COOKIE_DOMAIN=" in env_example
    assert "does **not** require a parent-domain session cookie" in session_row
    assert "`SESSION_COOKIE_DOMAIN` remains unset" in csrf_row
    assert "invalid values fail startup" in csrf_row
    assert "aelira.ai" not in session_row + csrf_row


def test_existing_host_only_csrf_token_is_promoted_without_rotation():
    middleware = CSRFMiddleware(
        app=FastAPI(),
        cookie_secure=True,
        cookie_httponly=False,
        cookie_samesite="Lax",
        cookie_domain=".example.org",
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [(b"cookie", b"csrf_token=existing-token")],
        }
    )
    response = Response()

    middleware._ensure_csrf_cookie(request, response)

    cookie = response.headers["set-cookie"]
    assert cookie.startswith("csrf_token=existing-token;")
    assert "Domain=.example.org" in cookie
    assert "Secure" in cookie
    assert "SameSite=Lax" in cookie
    assert "HttpOnly" not in cookie


def test_split_host_csrf_cookie_does_not_broaden_host_only_session_cookie():
    settings = _settings(
        env="production",
        cors_origins=["https://dashboard.example.org"],
        csrf_cookie_domain=".example.org",
        session_cookie_domain="",
    )
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )
    app.add_middleware(
        CSRFMiddleware,
        cookie_secure=True,
        cookie_httponly=False,
        cookie_samesite="Lax",
        cookie_domain=settings.csrf_cookie_domain,
    )

    @app.get("/bootstrap")
    async def bootstrap():
        response = JSONResponse({"ready": True})
        response.set_cookie(
            "aelira_access",
            "session-token",
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/cookies")
    async def cookies(request: Request):
        return dict(request.cookies)

    @app.post("/mutate")
    async def mutate():
        return {"reached": True}

    client = TestClient(app, base_url="https://api.example.org")
    preflight = client.options(
        "/mutate",
        headers={
            "Origin": "https://dashboard.example.org",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-CSRF-Token",
        },
    )
    bootstrap_response = client.get("/bootstrap")
    csrf_token = bootstrap_response.cookies["csrf_token"]
    set_cookie_headers = bootstrap_response.headers.get_list("set-cookie")
    csrf_cookie = next(
        header for header in set_cookie_headers if header.startswith("csrf_token=")
    )
    session_cookie = next(
        header for header in set_cookie_headers if header.startswith("aelira_access=")
    )

    mutation = client.post(
        "/mutate",
        headers={"X-CSRF-Token": csrf_token},
    )
    dashboard_cookies = client.get("https://dashboard.example.org/cookies").json()

    assert preflight.status_code == 200
    assert "x-csrf-token" in preflight.headers["access-control-allow-headers"].lower()
    assert "Domain=.example.org" in csrf_cookie
    assert "Path=/" in csrf_cookie
    assert "Secure" in csrf_cookie
    assert "SameSite=Lax" in csrf_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "Domain=" not in session_cookie
    assert "Path=/" in session_cookie
    assert "Secure" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert mutation.status_code == 200
    assert mutation.json() == {"reached": True}
    assert dashboard_cookies["csrf_token"] == csrf_token
    assert "aelira_access" not in dashboard_cookies
