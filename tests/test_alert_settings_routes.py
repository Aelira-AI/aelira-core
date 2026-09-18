"""Exact alert settings HTTP contracts, backed by disposable PostgreSQL."""

from datetime import datetime

import pytest

import alert_route_fixtures as fixtures
from src.api import alert_routes as routes
from src.db.models import EmailAlertSettings

pytestmark = pytest.mark.integration
alert_route = fixtures.alert_route

BASE = "/api/alerts"
FLAGS = (
    "alert_on_scan_complete",
    "alert_on_critical_issues",
    "alert_weekly_summary",
)


def reload_settings(fixture, department_id=None):
    """Discard ORM state so every assertion reads the stored JSON and flags."""
    department_id = department_id or fixture.department.id
    fixture.db.expire_all()
    return (
        fixture.db.query(EmailAlertSettings)
        .filter_by(department_id=department_id)
        .one()
    )


def test_settings_get_creates_one_persisted_default_record(alert_route):
    fixture = alert_route
    response = fixture.client.get(f"{BASE}/settings")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "id",
        "department_id",
        *FLAGS,
        "email_addresses",
        "weekly_summary_day",
        "weekly_summary_hour",
        "created_at",
        "updated_at",
    }
    assert data["department_id"] == fixture.department.id
    assert all(data[flag] is True for flag in FLAGS)
    assert data["email_addresses"] == []
    assert data["weekly_summary_day"] == 0
    assert data["weekly_summary_hour"] == 9
    assert datetime.fromisoformat(data["created_at"])
    row = reload_settings(fixture)
    assert row.id == data["id"]
    assert row.email_addresses == []
    assert fixture.client.get(f"{BASE}/settings").json() == data
    assert (
        fixture.db.query(EmailAlertSettings)
        .filter_by(department_id=fixture.department.id)
        .count()
        == 1
    )


@pytest.mark.parametrize("day,hour", [(0, 0), (6, 23)])
def test_settings_put_persists_false_flags_and_schedule_boundaries(
    alert_route, day, hour
):
    fixture = alert_route
    payload = {
        **dict.fromkeys(FLAGS, False),
        "weekly_summary_day": day,
        "weekly_summary_hour": hour,
        "email_addresses": ["faculty@example.edu"],
    }
    response = fixture.client.put(f"{BASE}/settings", json=payload)
    assert response.status_code == 200
    assert {key: response.json()[key] for key in payload} == payload
    assert datetime.fromisoformat(response.json()["updated_at"])
    row = reload_settings(fixture)
    assert {key: getattr(row, key) for key in payload} == payload
    reread = fixture.client.get(f"{BASE}/settings")
    assert reread.status_code == 200
    assert reread.json() == response.json()


def test_partial_settings_update_preserves_omitted_values_and_clears_recipients(
    alert_route,
):
    fixture = alert_route
    initial = {
        "alert_on_scan_complete": False,
        "alert_on_critical_issues": False,
        "alert_weekly_summary": True,
        "weekly_summary_day": 5,
        "weekly_summary_hour": 17,
        "email_addresses": ["faculty@example.edu"],
    }
    assert fixture.client.put(f"{BASE}/settings", json=initial).status_code == 200
    response = fixture.client.put(
        f"{BASE}/settings", json={"alert_weekly_summary": False, "email_addresses": []}
    )
    assert response.status_code == 200
    expected = {**initial, "alert_weekly_summary": False, "email_addresses": []}
    assert {key: response.json()[key] for key in expected} == expected
    row = reload_settings(fixture)
    assert {key: getattr(row, key) for key in expected} == expected


def test_settings_put_deduplicates_identical_addresses(alert_route):
    response = alert_route.client.put(
        f"{BASE}/settings",
        json={
            "email_addresses": ["one@example.edu", "two@example.edu", "one@example.edu"]
        },
    )
    assert response.status_code == 200
    assert sorted(response.json()["email_addresses"]) == [
        "one@example.edu",
        "two@example.edu",
    ]
    assert sorted(reload_settings(alert_route).email_addresses) == [
        "one@example.edu",
        "two@example.edu",
    ]
    assert alert_route.client.get(f"{BASE}/emails").json()["count"] == 2


@pytest.mark.parametrize("path", ["/emails", "/emails/add"])
def test_recipient_add_persists_first_and_subsequent_addresses(alert_route, path):
    fixture = alert_route
    for addresses in (
        ["first@example.edu"],
        ["first@example.edu", "second@example.edu"],
    ):
        response = fixture.client.post(f"{BASE}{path}", json={"email": addresses[-1]})
        assert response.status_code == 200
        assert response.json()["email_addresses"] == addresses
        assert reload_settings(fixture).email_addresses == addresses
        listed = fixture.client.get(f"{BASE}/emails")
        assert listed.status_code == 200
        assert listed.json() == {"emails": addresses, "count": len(addresses)}


@pytest.mark.parametrize("path", ["/emails", "/emails/add"])
def test_recipient_add_ignores_case_insensitive_duplicates(alert_route, path):
    fixture = alert_route
    assert (
        fixture.client.put(
            f"{BASE}/settings", json={"email_addresses": ["Faculty@example.edu"]}
        ).status_code
        == 200
    )
    response = fixture.client.post(
        f"{BASE}{path}", json={"email": "FACULTY@example.edu"}
    )
    assert response.status_code == 200
    assert response.json()["email_addresses"] == ["Faculty@example.edu"]
    assert reload_settings(fixture).email_addresses == ["Faculty@example.edu"]
    assert fixture.client.get(f"{BASE}/emails").json() == {
        "emails": ["Faculty@example.edu"],
        "count": 1,
    }


@pytest.mark.parametrize("path", ["/emails", "/emails/add"])
def test_twenty_ordinary_recipients_are_accepted_with_exact_count(alert_route, path):
    """No configured recipient cap exists; the old test silently stopped at errors."""
    fixture = alert_route
    recipients = [f"faculty{number}@example.edu" for number in range(20)]
    response = fixture.client.put(
        f"{BASE}/settings", json={"email_addresses": recipients[:-1]}
    )
    assert response.status_code == 200
    response = fixture.client.post(f"{BASE}{path}", json={"email": recipients[-1]})
    assert response.status_code == 200
    assert sorted(response.json()["email_addresses"]) == sorted(recipients)
    assert sorted(reload_settings(fixture).email_addresses) == sorted(recipients)
    listed = fixture.client.get(f"{BASE}/emails")
    assert listed.status_code == 200
    assert listed.json()["count"] == 20
    assert sorted(listed.json()["emails"]) == sorted(recipients)


@pytest.mark.parametrize("alias", [False, True])
def test_recipient_removal_is_case_insensitive_and_persisted(alert_route, alias):
    fixture = alert_route
    assert (
        fixture.client.put(
            f"{BASE}/settings",
            json={"email_addresses": ["Faculty@example.edu", "library@example.edu"]},
        ).status_code
        == 200
    )
    if alias:
        response = fixture.client.post(
            f"{BASE}/emails/remove", json={"email": "FACULTY@example.edu"}
        )
        assert response.status_code == 200
        assert response.json()["email_addresses"] == ["library@example.edu"]
    else:
        response = fixture.client.delete(f"{BASE}/emails/FACULTY@example.edu")
        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "message": "Email address removed: FACULTY@example.edu",
        }
    assert reload_settings(fixture).email_addresses == ["library@example.edu"]
    assert fixture.client.get(f"{BASE}/emails").json() == {
        "emails": ["library@example.edu"],
        "count": 1,
    }


def test_missing_recipient_delete_is_404_but_remove_alias_is_idempotent(alert_route):
    fixture = alert_route
    assert (
        fixture.client.put(
            f"{BASE}/settings", json={"email_addresses": ["faculty@example.edu"]}
        ).status_code
        == 200
    )
    response = fixture.client.delete(f"{BASE}/emails/missing@example.edu")
    assert response.status_code == 404
    assert response.json() == {"detail": "Email address not found: missing@example.edu"}
    assert reload_settings(fixture).email_addresses == ["faculty@example.edu"]
    response = fixture.client.post(
        f"{BASE}/emails/remove", json={"email": "missing@example.edu"}
    )
    assert response.status_code == 200
    assert response.json()["email_addresses"] == ["faculty@example.edu"]
    assert reload_settings(fixture).email_addresses == ["faculty@example.edu"]


def test_pause_and_resume_persist_flags_preserve_schedule_and_recipients(alert_route):
    fixture = alert_route
    assert (
        fixture.client.put(
            f"{BASE}/settings",
            json={
                "email_addresses": ["faculty@example.edu"],
                "weekly_summary_day": 6,
                "weekly_summary_hour": 0,
            },
        ).status_code
        == 200
    )
    for action, enabled, message in (
        (
            "pause",
            False,
            "All email alerts paused. Re-enable them in settings when ready.",
        ),
        ("resume", True, "All email alerts resumed."),
    ):
        response = fixture.client.post(f"{BASE}/{action}")
        assert response.status_code == 200
        assert response.json() == {"success": True, "message": message}
        row = reload_settings(fixture)
        assert all(getattr(row, flag) is enabled for flag in FLAGS)
        assert row.email_addresses == ["faculty@example.edu"]
        assert (row.weekly_summary_day, row.weekly_summary_hour) == (6, 0)
        data = fixture.client.get(f"{BASE}/settings").json()
        assert all(data[flag] is enabled for flag in FLAGS)
    assert fixture.mail.mock_calls == []


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"weekly_summary_day": -1}, "weekly_summary_day"),
        ({"weekly_summary_day": 7}, "weekly_summary_day"),
        ({"weekly_summary_hour": -1}, "weekly_summary_hour"),
        ({"weekly_summary_hour": 24}, "weekly_summary_hour"),
        ({"email_addresses": ["not-an-email"]}, "email_addresses"),
    ],
)
def test_invalid_settings_return_422_without_changing_persisted_values(
    alert_route, payload, field
):
    fixture = alert_route
    before = fixture.client.get(f"{BASE}/settings")
    assert before.status_code == 200
    response = fixture.client.put(f"{BASE}/settings", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:2] == ["body", field]
    reload_settings(fixture)
    assert fixture.client.get(f"{BASE}/settings").json() == before.json()


@pytest.mark.parametrize("path", ["/emails", "/emails/add", "/emails/remove"])
def test_invalid_recipient_returns_422_without_creating_settings(alert_route, path):
    response = alert_route.client.post(f"{BASE}{path}", json={"email": "not-an-email"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "email"]
    assert (
        alert_route.db.query(EmailAlertSettings)
        .filter_by(department_id=alert_route.department.id)
        .count()
        == 0
    )


def test_normal_department_switch_keeps_settings_and_mutations_separate(alert_route):
    fixture = alert_route
    first_id, second_id = fixture.department.id, fixture.other_department.id
    assert (
        fixture.client.put(
            f"{BASE}/settings",
            json={"email_addresses": ["faculty@example.edu"], "weekly_summary_day": 1},
        ).status_code
        == 200
    )
    first = fixture.client.get(f"{BASE}/settings").json()
    fixture.principal.department_id = second_id
    response = fixture.client.get(f"{BASE}/settings")
    assert response.status_code == 200
    assert response.json()["department_id"] == second_id
    assert response.json()["email_addresses"] == []
    assert response.json()["id"] != first["id"]
    for method, path, payload in (
        ("put", "/settings", {"weekly_summary_day": 4}),
        ("post", "/emails", {"email": "library@example.edu"}),
        ("post", "/emails/add", {"email": "archive@example.edu"}),
        ("post", "/emails/remove", {"email": "library@example.edu"}),
        ("delete", "/emails/archive@example.edu", None),
        ("post", "/pause", None),
        ("post", "/resume", None),
    ):
        response = fixture.client.request(method, f"{BASE}{path}", json=payload)
        assert response.status_code == 200
        first_row = reload_settings(fixture, first_id)
        assert first_row.email_addresses == ["faculty@example.edu"]
        assert first_row.weekly_summary_day == 1
        assert all(getattr(first_row, flag) is True for flag in FLAGS)
    assert reload_settings(fixture, second_id).email_addresses == []
    assert reload_settings(fixture, second_id).weekly_summary_day == 4
    fixture.principal.department_id = first_id
    assert fixture.client.get(f"{BASE}/settings").json() == first


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("get", "/settings", None),
        ("put", "/settings", {}),
        ("get", "/emails", None),
        ("post", "/emails", {"email": "faculty@example.edu"}),
        ("post", "/emails/add", {"email": "faculty@example.edu"}),
        ("post", "/emails/remove", {"email": "faculty@example.edu"}),
        ("delete", "/emails/faculty@example.edu", None),
        ("post", "/pause", None),
        ("post", "/resume", None),
    ],
)
def test_settings_and_recipient_routes_require_authentication(
    alert_route, method, path, payload
):
    fixture = alert_route
    fixture.application.dependency_overrides.pop(routes.get_current_api_key)
    response = fixture.client.request(method, f"{BASE}{path}", json=payload)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "detail": "Authentication required. Provide API key in Authorization header or login via dashboard."
    }
    assert (
        fixture.db.query(EmailAlertSettings)
        .filter_by(department_id=fixture.department.id)
        .count()
        == 0
    )
    assert fixture.mail.mock_calls == []


def test_settings_read_failure_returns_500_without_changing_persisted_data(
    alert_route, monkeypatch
):
    fixture = alert_route
    before = fixture.client.get(f"{BASE}/settings")
    assert before.status_code == 200

    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic database read unavailable")

    with monkeypatch.context() as failing_database:
        failing_database.setattr(fixture.db, "query", unavailable)
        response = fixture.client.get(f"{BASE}/settings")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    reload_settings(fixture)
    assert fixture.client.get(f"{BASE}/settings").json() == before.json()
    assert fixture.mail.mock_calls == []


@pytest.mark.parametrize(
    "method,path,first_payload,next_payload",
    [
        (
            "put",
            "/settings",
            {"weekly_summary_hour": 8},
            {"weekly_summary_hour": 16},
        ),
        (
            "post",
            "/emails",
            {"email": "first@example.edu"},
            {"email": "second@example.edu"},
        ),
        (
            "post",
            "/emails/add",
            {"email": "first@example.edu"},
            {"email": "second@example.edu"},
        ),
    ],
)
def test_settings_write_failure_is_unsuccessful_and_rollback_preserves_previous_commit(
    alert_route, monkeypatch, method, path, first_payload, next_payload
):
    fixture = alert_route
    response = fixture.client.request(method, f"{BASE}{path}", json=first_payload)
    assert response.status_code == 200
    before = fixture.client.get(f"{BASE}/settings")
    assert before.status_code == 200

    def unavailable():
        raise RuntimeError("synthetic database write unavailable")

    with monkeypatch.context() as failing_database:
        failing_database.setattr(fixture.db, "commit", unavailable)
        response = fixture.client.request(method, f"{BASE}{path}", json=next_payload)
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    # Production closes failed request sessions; this fixture shares a session,
    # so explicitly roll it back before asking PostgreSQL for stored values.
    fixture.db.rollback()
    reload_settings(fixture)
    assert fixture.client.get(f"{BASE}/settings").json() == before.json()
    assert fixture.mail.mock_calls == []
