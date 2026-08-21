"""CSRF enforcement covers cookie-authenticated dashboard mutations.

Regression: state-changing dashboard routes (/education, /auth/keys,
/auth/profile, /alerts, /analytics, /llm, /integrations, /admin, and the
Brightspace REST routes) used to sit in the CSRF exempt list on the
rationale that SameSite cookies were sufficient. That makes the control one
config flip (SESSION_COOKIE_SAMESITE=None) away from broadly unprotected.
They are now enforced: the SPA sends X-CSRF-Token, Bearer callers are
exempt by auth method.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from src.middleware.security import CSRFMiddleware


def _mw(enabled=True):
    return CSRFMiddleware(app=MagicMock(), enabled=enabled)


def _request(method, path, cookies=None, headers=None):
    req = MagicMock()
    req.method = method
    req.url.path = path
    req.cookies = cookies or {}
    req.headers = headers or {}
    return req


def _full_middleware_client():
    app = FastAPI()
    app.add_middleware(
        CSRFMiddleware,
        cookie_secure=False,
        cookie_httponly=False,
    )

    @app.post("/auth/keys")
    async def create_key(request: Request):
        principal = (
            "session"
            if request.cookies.get("aelira_access") == "valid-session"
            else "api_key"
        )
        return {"reached": True, "principal": principal}

    @app.delete("/auth/keys/{key_id}")
    async def delete_key(key_id: str, request: Request):
        principal = (
            "session"
            if request.cookies.get("aelira_access") == "valid-session"
            else "api_key"
        )
        return {"reached": True, "key_id": key_id, "principal": principal}

    return TestClient(app)


DASHBOARD_MUTATION_ROUTES = [
    "/education/scan",
    "/auth/keys",
    "/auth/profile",
    "/alerts/settings",
    "/analytics/snapshots",
    "/llm/providers",
    "/integrations/google",
    "/admin/users",
    "/brightspace/connect",
    "/brightspace/remediate",
]


@pytest.mark.parametrize("path", DASHBOARD_MUTATION_ROUTES)
def test_dashboard_mutation_is_not_exempt(path):
    # None of these may be treated as CSRF-exempt.
    assert _mw()._is_exempt(path) is False, path


@pytest.mark.asyncio
@pytest.mark.parametrize("path", DASHBOARD_MUTATION_ROUTES)
async def test_cookie_post_without_token_is_403(path):
    mw = _mw()
    call_next = AsyncMock()
    # Cookie-authenticated (session cookie present), no CSRF header.
    req = _request("POST", path, cookies={"aelira_access": "sess"})
    resp = await mw.dispatch(req, call_next)
    assert resp.status_code == 403
    call_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_cookie_post_with_matching_token_passes():
    mw = _mw()
    call_next = AsyncMock(return_value=MagicMock())
    req = _request(
        "POST",
        "/education/scan",
        cookies={"aelira_access": "sess", "csrf_token": "abc123"},
        headers={"X-CSRF-Token": "abc123"},
    )
    await mw.dispatch(req, call_next)
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_bearer_post_skips_csrf():
    # Bearer/API-key callers (e.g. the CLI) hit the same routes without a
    # CSRF token and must not be blocked.
    mw = _mw()
    call_next = AsyncMock(return_value=MagicMock())
    req = _request("POST", "/education/scan", headers={"Authorization": "Bearer k"})
    await mw.dispatch(req, call_next)
    call_next.assert_awaited_once()


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/auth/keys"), ("delete", "/auth/keys/key-1")],
)
def test_key_mutation_with_session_cookie_and_stale_bearer_requires_csrf(method, path):
    client = _full_middleware_client()

    response = getattr(client, method)(
        path,
        cookies={"aelira_access": "valid-session"},
        headers={"Authorization": "Bearer stale-or-invalid"},
        **({"json": {"name": "Automation"}} if method == "post" else {}),
    )

    assert response.status_code == 403
    assert response.json()["detail"].startswith("CSRF token missing")


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/auth/keys"), ("delete", "/auth/keys/key-1")],
)
def test_key_mutation_with_session_cookie_stale_bearer_and_matching_csrf_reaches_session_endpoint(
    method, path
):
    client = _full_middleware_client()
    cookies = {"aelira_access": "valid-session", "csrf_token": "matching-token"}
    headers = {
        "Authorization": "Bearer stale-or-invalid",
        "X-CSRF-Token": "matching-token",
    }

    response = getattr(client, method)(
        path,
        cookies=cookies,
        headers=headers,
        **({"json": {"name": "Automation"}} if method == "post" else {}),
    )

    assert response.status_code == 200
    assert response.json()["reached"] is True
    assert response.json()["principal"] == "session"


def test_key_mutation_with_valid_api_key_and_no_session_cookie_remains_csrf_exempt():
    client = _full_middleware_client()

    response = client.post(
        "/auth/keys",
        json={"name": "CLI"},
        headers={"Authorization": "Bearer valid-api-key"},
    )

    assert response.status_code == 200
    assert response.json() == {"reached": True, "principal": "api_key"}


def test_key_mutation_with_invalid_session_cookie_and_valid_api_key_fails_closed_without_csrf():
    """Cookie presence makes key CRUD CSRF-protected even with API-key fallback."""
    client = _full_middleware_client()

    response = client.post(
        "/auth/keys",
        json={"name": "CLI"},
        cookies={"aelira_access": "invalid-session"},
        headers={"Authorization": "Bearer valid-api-key"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_is_never_blocked():
    mw = _mw()
    call_next = AsyncMock(return_value=MagicMock())
    req = _request("GET", "/education/scan", cookies={"aelira_access": "s"})
    await mw.dispatch(req, call_next)
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_cookie_logout_requires_csrf_but_refresh_stays_exempt():
    mw = _mw()
    call_next = AsyncMock(return_value=MagicMock())
    logout = _request(
        "POST", "/auth/session/logout", cookies={"aelira_access": "session"}
    )
    response = await mw.dispatch(logout, call_next)
    assert response.status_code == 403
    call_next.assert_not_awaited()

    refresh_next = AsyncMock(return_value=MagicMock())
    refresh = _request(
        "POST", "/auth/session/refresh", cookies={"aelira_refresh": "refresh"}
    )
    await mw.dispatch(refresh, refresh_next)
    refresh_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_oauth_callback_stays_exempt():
    # External-party POSTs (OAuth callbacks) legitimately carry no CSRF token.
    mw = _mw()
    call_next = AsyncMock(return_value=MagicMock())
    req = _request("POST", "/auth/google/callback", cookies={})
    await mw.dispatch(req, call_next)
    call_next.assert_awaited_once()


def test_csrf_cookie_uses_configured_parent_domain_and_remains_readable():
    mw = CSRFMiddleware(
        app=MagicMock(),
        enabled=True,
        cookie_secure=True,
        cookie_httponly=False,
        cookie_samesite="Lax",
        cookie_domain=".example.com",
    )
    response = Response()

    mw._ensure_csrf_cookie(_request("GET", "/health"), response)

    cookie = response.headers["set-cookie"]
    assert "Domain=.example.com" in cookie
    assert "Secure" in cookie
    assert "SameSite=Lax" in cookie
    assert "HttpOnly" not in cookie


def test_csrf_cookie_remains_host_only_when_domain_is_unset():
    mw = CSRFMiddleware(
        app=MagicMock(),
        enabled=True,
        cookie_httponly=False,
        cookie_domain=None,
    )
    response = Response()

    mw._ensure_csrf_cookie(_request("GET", "/health"), response)

    assert "Domain=" not in response.headers["set-cookie"]
