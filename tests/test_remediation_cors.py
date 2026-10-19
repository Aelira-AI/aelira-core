"""CORS contracts for dashboard requests, including CSRF refusals."""

import pytest
from fastapi.testclient import TestClient
from starlette.middleware import Middleware

from src.api.education.multimedia_routes import get_api_key_or_mock
from src.api.main import app, settings
from src.db.database import get_db_dependency
from src.middleware.security import CSRFMiddleware, SecurityHeadersMiddleware
from src.monitoring.metrics import REQUEST_COUNT


@pytest.fixture
def csrf_client(monkeypatch):
    """Exercise the registered application stack with production CSRF enforcement."""
    assert settings.cors_origins
    csrf_layers = [
        layer for layer in app.user_middleware if layer.cls is CSRFMiddleware
    ]
    assert len(csrf_layers) == 1
    security_layers = [
        layer for layer in app.user_middleware if layer.cls is SecurityHeadersMiddleware
    ]
    assert len(security_layers) == 1

    layers = []
    for layer in app.user_middleware:
        if layer.cls is CSRFMiddleware:
            layers.append(Middleware(layer.cls, **{**layer.kwargs, "enabled": True}))
        elif layer.cls is SecurityHeadersMiddleware:
            layers.append(
                Middleware(layer.cls, **{**layer.kwargs, "enable_hsts": True})
            )
        else:
            layers.append(layer)
    monkeypatch.setattr(app, "user_middleware", layers)
    monkeypatch.setattr(app, "middleware_stack", None)
    monkeypatch.setattr(
        app,
        "dependency_overrides",
        {
            **app.dependency_overrides,
            get_db_dependency: lambda: None,
            get_api_key_or_mock: lambda: (None, "user", "department"),
        },
    )
    client = TestClient(app, base_url="https://testserver")
    yield client
    client.close()


def _options_count(status_code):
    labels = {
        "method": "OPTIONS",
        "endpoint": "/__other__",
        "status_code": str(status_code),
    }
    return sum(
        sample.value
        for metric in REQUEST_COUNT.collect()
        for sample in metric.samples
        if sample.name == "aelira_http_requests_total" and sample.labels == labels
    )


@pytest.mark.parametrize("approved", [True, False])
def test_preflight_retains_security_headers_and_metrics(csrf_client, approved):
    origin = settings.cors_origins[0] if approved else "https://unapproved.example.org"
    status_code = 200 if approved else 400
    before = _options_count(status_code)

    response = csrf_client.options(
        "/education/multimedia/scan",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )

    assert response.status_code == status_code
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in response.headers
    assert response.headers["strict-transport-security"] == (
        f"max-age={settings.hsts_max_age}; includeSubDomains; preload"
    )
    assert _options_count(status_code) == before + 1


@pytest.mark.parametrize(
    ("header_token", "detail"),
    [
        (None, "CSRF token missing. Include X-CSRF-Token header."),
        ("wrong-token", "CSRF token invalid."),
    ],
)
def test_allowed_origin_can_read_csrf_refusal(csrf_client, header_token, detail):
    origin = settings.cors_origins[0]
    token = csrf_client.get("/api/csrf-token", headers={"Origin": origin}).json()[
        "csrf_token"
    ]
    assert csrf_client.cookies["csrf_token"] == token
    csrf_client.cookies.set("aelira_access", "session")
    headers = {"Origin": origin}
    if header_token is not None:
        headers["X-CSRF-Token"] = header_token

    response = csrf_client.post("/education/multimedia/scan", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": detail}
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"


def test_matching_csrf_token_reaches_multimedia_route_validation(csrf_client):
    origin = settings.cors_origins[0]
    token = csrf_client.get("/api/csrf-token", headers={"Origin": origin}).json()[
        "csrf_token"
    ]
    csrf_client.cookies.set("aelira_access", "session")

    response = csrf_client.post(
        "/education/multimedia/scan",
        headers={"Origin": origin, "X-CSRF-Token": token},
    )

    assert response.status_code == 422
    assert any(error["loc"][-1] == "file" for error in response.json()["detail"])
    assert response.headers["access-control-allow-origin"] == origin


def test_unapproved_origin_cannot_read_csrf_refusal(csrf_client):
    origin = "https://unapproved.example.org"
    csrf_client.cookies.set("aelira_access", "session")

    response = csrf_client.post(
        "/education/multimedia/scan", headers={"Origin": origin}
    )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_unapproved_origin_preflight_is_rejected(csrf_client):
    response = csrf_client.options(
        "/education/multimedia/scan",
        headers={
            "Origin": "https://unapproved.example.org",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_async_remediation_preflight_allows_prefer_header():
    origin = settings.cors_origins[0]
    response = TestClient(app).options(
        "/education/remediate/scan-1",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,prefer,x-csrf-token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    allowed_headers = {
        header.strip().lower()
        for header in response.headers["access-control-allow-headers"].split(",")
    }
    assert {
        "authorization",
        "content-type",
        "prefer",
        "x-csrf-token",
    } <= allowed_headers
