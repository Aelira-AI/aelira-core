"""The first token response must be usable by a fresh browser session."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.api.main import get_csrf_token_endpoint
from src.middleware.security import CSRFMiddleware, get_csrf_token


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(CSRFMiddleware, cookie_secure=True, cookie_httponly=False)
    app.add_api_route("/api/csrf-token", get_csrf_token_endpoint)

    @app.post("/form")
    async def submit_form():
        return {"accepted": True}

    return TestClient(app, base_url="https://testserver")


def test_existing_cookie_token_can_submit_form(client):
    # Positive control: the established session path already works.
    client.get("/api/csrf-token")
    response = client.get("/api/csrf-token")
    token = response.json()["csrf_token"]

    assert bool(token == client.cookies["csrf_token"])
    assert client.post("/form", headers={"X-CSRF-Token": token}).status_code == 200


def test_first_token_response_can_submit_form_without_retry(client):
    response = client.get("/api/csrf-token")
    token = response.json()["csrf_token"]

    mutation = client.post("/form", headers={"X-CSRF-Token": token})

    assert mutation.status_code == 200
    assert mutation.json() == {"accepted": True}
    assert bool(token == response.cookies["csrf_token"])
    assert bool(client.get("/api/csrf-token").json()["csrf_token"] == token)


def test_separate_clients_receive_separate_tokens(client):
    other_client = TestClient(client.app, base_url="https://testserver")

    first = client.get("/api/csrf-token").json()["csrf_token"]
    second = other_client.get("/api/csrf-token").json()["csrf_token"]

    assert bool(first != second)
    assert bool(first == client.cookies["csrf_token"])
    assert bool(second == other_client.cookies["csrf_token"])


def test_token_helper_reuses_token_within_request():
    request = Request({"type": "http", "headers": []})
    token = get_csrf_token(request)

    assert bool(get_csrf_token(request) == token)


def test_configured_cookie_and_token_length_share_endpoint_token():
    app = FastAPI()
    app.add_middleware(
        CSRFMiddleware,
        cookie_name="custom_csrf",
        token_length=48,
        cookie_domain=".example.org",
    )
    app.add_api_route("/api/csrf-token", get_csrf_token_endpoint)
    client = TestClient(app, base_url="https://api.example.org")

    response = client.get("/api/csrf-token")
    token = response.json()["csrf_token"]

    assert len(token) == 64
    assert bool(token == response.cookies["custom_csrf"])
    assert bool(client.get("/api/csrf-token").json()["csrf_token"] == token)
