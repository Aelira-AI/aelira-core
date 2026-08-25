"""CORS contracts for dashboard remediation requests."""

from fastapi.testclient import TestClient

from src.api.main import app


def test_async_remediation_preflight_allows_prefer_header():
    origin = "http://localhost:5173"
    response = TestClient(app).options(
        "/education/remediate/scan-1",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,prefer",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    allowed_headers = {
        header.strip().lower()
        for header in response.headers["access-control-allow-headers"].split(",")
    }
    assert {"authorization", "content-type", "prefer"} <= allowed_headers
