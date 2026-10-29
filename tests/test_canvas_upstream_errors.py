"""Upstream Canvas failures on the listing routes become handled responses."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import UserRole
from src.integrations.canvas.canvas_api import CanvasAPIClient

DEPT = "dept-1"
COURSE = "course-1"
SECRET_BODY = "upstream-secret-body-9f3a"
SECRET_TOKEN = "canvas-token-7c1d"
SECRET_HOST = "canvas-internal.example.test"

ROUTES = [
    pytest.param(f"/canvas/courses?department_id={DEPT}", id="courses"),
    pytest.param(f"/canvas/courses/{COURSE}/files?department_id={DEPT}", id="files"),
    pytest.param(
        f"/canvas/courses/{COURSE}/folders?department_id={DEPT}", id="folders"
    ),
]


def _canvas_client(handler):
    """Build a real CanvasAPIClient whose HTTP transport is mocked."""
    canvas = CanvasAPIClient("http://localhost", SECRET_TOKEN)
    canvas._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return canvas


def _principal():
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id=DEPT,
        user_role=UserRole.ADMIN,
        auth_method="session",
    )


@pytest.fixture
def client():
    app.dependency_overrides[get_db_dependency] = lambda: MagicMock()
    app.dependency_overrides[get_authenticated_principal] = _principal
    yield TestClient(app)
    app.dependency_overrides.pop(get_db_dependency, None)
    app.dependency_overrides.pop(get_authenticated_principal, None)


def _get(client, path, handler):
    canvas = _canvas_client(handler)
    with (
        patch(
            "src.api.canvas_routes._get_canvas_client",
            new=AsyncMock(return_value=(MagicMock(), canvas)),
        ),
        patch("src.api.canvas_routes.require_feature", new=AsyncMock()),
    ):
        return client.get(path)


def _assert_no_upstream_detail(response):
    text = response.text
    for leaked in (SECRET_BODY, SECRET_TOKEN, SECRET_HOST, "localhost", "Traceback"):
        assert leaked not in text


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("upstream_status", [500, 502, 503])
def test_upstream_5xx_becomes_502(client, path, upstream_status):
    def handler(request):
        return httpx.Response(upstream_status, text=SECRET_BODY)

    response = _get(client, path, handler)

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Canvas returned an error. Please try again shortly."
    }
    _assert_no_upstream_detail(response)


@pytest.mark.parametrize("path", ROUTES)
def test_upstream_401_uses_the_reconnect_contract(client, path):
    def handler(request):
        return httpx.Response(401, text=SECRET_BODY)

    response = _get(client, path, handler)

    assert response.status_code == 409
    assert "reconnect your Canvas account" in response.json()["detail"]
    _assert_no_upstream_detail(response)


def test_upstream_403_on_course_listing_passes_through_as_403(client):
    def handler(request):
        return httpx.Response(403, text=SECRET_BODY)

    response = _get(client, f"/canvas/courses?department_id={DEPT}", handler)

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Canvas did not allow the connected account to list courses."
    }
    _assert_no_upstream_detail(response)


@pytest.mark.parametrize(
    "path",
    [
        f"/canvas/courses/{COURSE}/files?department_id={DEPT}",
        f"/canvas/courses/{COURSE}/folders?department_id={DEPT}",
    ],
    ids=["files", "folders"],
)
def test_upstream_403_on_course_routes_passes_through_as_403(client, path):
    def handler(request):
        return httpx.Response(403, text=SECRET_BODY)

    with patch("src.integrations.canvas.canvas_api.asyncio.sleep", new=AsyncMock()):
        response = _get(client, path, handler)

    assert response.status_code == 403
    assert response.json() == {
        "detail": (
            "Canvas did not allow this request for the connected account. "
            "Check that your Canvas role can access this course."
        )
    }
    _assert_no_upstream_detail(response)


def test_upstream_429_on_course_listing_becomes_503(client):
    def handler(request):
        return httpx.Response(429, text=SECRET_BODY)

    response = _get(client, f"/canvas/courses?department_id={DEPT}", handler)

    assert response.status_code == 503
    _assert_no_upstream_detail(response)


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError(f"connect failed to {SECRET_HOST} {SECRET_TOKEN}"),
        httpx.ReadTimeout(f"timed out talking to {SECRET_HOST}"),
        httpx.RemoteProtocolError(f"peer closed {SECRET_HOST}"),
    ],
    ids=["connect-error", "timeout", "protocol-error"],
)
def test_transport_failure_becomes_503(client, path, error):
    def handler(request):
        raise error

    response = _get(client, path, handler)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Canvas is unavailable right now. Please try again shortly."
    }
    _assert_no_upstream_detail(response)


def test_upstream_failure_is_logged_without_body_or_token(client, caplog):
    def handler(request):
        return httpx.Response(500, text=SECRET_BODY)

    with caplog.at_level(logging.WARNING, logger="src.api.canvas_routes"):
        response = _get(client, f"/canvas/courses?department_id={DEPT}", handler)

    assert response.status_code == 502
    records = [r for r in caplog.records if r.name == "src.api.canvas_routes"]
    assert records, "expected a warning log for the upstream failure"
    assert records[0].levelno == logging.WARNING
    assert records[0].upstream_status == 500
    logged = " ".join(r.getMessage() for r in records)
    assert SECRET_BODY not in logged
    assert SECRET_TOKEN not in logged


@pytest.mark.parametrize("path", ROUTES)
def test_successful_listing_is_unchanged(client, path):
    def handler(request):
        return httpx.Response(200, json=[])

    response = _get(client, path, handler)

    assert response.status_code == 200
    assert response.json() == []
