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
