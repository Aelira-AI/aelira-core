"""Representative route compatibility for keyless normal sessions."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api import alert_routes, auth_routes, google_routes, integration_routes
from src.api.auth_routes import SessionAccessIdentity
from src.auth.auth_service import AuthService
from src.db.models import APIKey


def _identity() -> SessionAccessIdentity:
    return SessionAccessIdentity.from_validated_session(
        user_id="session-user",
        department_id="session-dept",
        payload={"sub": "session-user", "jti": "trusted-session-jti"},
    )


def _query_db(*, first=None, all_rows=None):
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.first.return_value = first
    query.all.return_value = all_rows or []
    db = MagicMock()
    db.query.return_value = query
    return db


@pytest.mark.asyncio
async def test_integration_status_accepts_keyless_session_without_api_key_row(
    monkeypatch,
):
    db = _query_db()
    create = MagicMock()
    monkeypatch.setattr(AuthService, "create_api_key", create)

    result = await integration_routes.get_integration_status(cast(Any, _identity()), db)

    assert result["google"]["connected"] is False
    assert result["microsoft"]["connected"] is False
    create.assert_not_called()
    assert not any(
        call.args and call.args[0] is APIKey for call in db.query.call_args_list
    )


@pytest.mark.asyncio
async def test_alert_settings_accepts_keyless_session_without_api_key_row(monkeypatch):
    now = datetime.now(timezone.utc)
    settings = SimpleNamespace(
        id="alerts-1",
        department_id="session-dept",
        alert_on_scan_complete=True,
        alert_on_critical_issues=True,
        alert_weekly_summary=False,
        email_addresses=[],
        weekly_summary_day=0,
        weekly_summary_hour=9,
        created_at=now,
        updated_at=now,
    )
    db = _query_db(first=settings)
    create = MagicMock()
    monkeypatch.setattr(AuthService, "create_api_key", create)

    result = await alert_routes.get_alert_settings(cast(Any, _identity()), db)

    assert result.department_id == "session-dept"
    db.add.assert_not_called()
    create.assert_not_called()


@pytest.mark.asyncio
async def test_cloud_status_accepts_keyless_session_without_api_key_fk(monkeypatch):
    now = datetime.now(timezone.utc)
    credential = SimpleNamespace(
        id="credential-1",
        provider="google",
        provider_email="user@example.edu",
        provider_name="Example User",
        is_active=True,
        last_sync_at=None,
        created_at=now,
    )
    db = _query_db(first=credential)
    feature_check = AsyncMock(return_value=None)
    create = MagicMock()
    monkeypatch.setattr(google_routes, "require_feature", feature_check)
    monkeypatch.setattr(AuthService, "create_api_key", create)

    result = await google_routes.google_status(cast(Any, _identity()), db)

    assert result.id == "credential-1"
    feature_check.assert_awaited_once_with(
        db, "session-dept", "cloud_integration", "Google Workspace Integration"
    )
    db.add.assert_not_called()
    create.assert_not_called()


@pytest.mark.asyncio
async def test_moved_user_session_routes_use_current_tenant_not_old_active_key(
    monkeypatch,
):
    from starlette.requests import Request

    user = MagicMock(id="session-user", department_id="current-dept")
    session_service = MagicMock()
    session_service.validate_session.return_value = (
        user,
        {"sub": user.id, "jti": "trusted-session-jti"},
    )
    monkeypatch.setattr(auth_routes, "get_session_service", lambda: session_service)
    auth_db = MagicMock()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/google/status",
            "headers": [(b"cookie", b"aelira_access=valid-session")],
        }
    )

    identity = auth_routes.get_current_api_key(request, credentials=None, db=auth_db)

    route_db = _query_db(
        first=SimpleNamespace(
            id="current-credential",
            provider="google",
            provider_email="current@example.edu",
            provider_name="Current User",
            is_active=True,
            last_sync_at=None,
            created_at=datetime.now(timezone.utc),
        )
    )
    feature_check = AsyncMock(return_value=None)
    monkeypatch.setattr(google_routes, "require_feature", feature_check)
    result = await google_routes.google_status(cast(Any, identity), route_db)

    assert result.id == "current-credential"
    feature_check.assert_awaited_once_with(
        route_db, "current-dept", "cloud_integration", "Google Workspace Integration"
    )
    auth_db.query.assert_not_called()
