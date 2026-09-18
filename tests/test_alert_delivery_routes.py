"""HTTP alert contracts with PostgreSQL settings and a signature-checked mail seam.

The fixture supplies identity; no SMTP, external provider or queue worker runs.
"""

from unittest.mock import AsyncMock, Mock

import pytest
import alert_route_fixtures as fixtures

from src.api import alert_routes as routes
from src.config.settings import get_settings
from src.db.models import EmailAlertSettings
from src.mailer.email_service import EmailService

alert_route = fixtures.alert_route
pytestmark = pytest.mark.integration

CASES = [
    (
        "scan-complete",
        "send_scan_complete",
        "alert_on_scan_complete",
        {
            "scan_id": "fixture-scan",
            "file_name": "Lecture.pdf",
            "issues_found": 3,
            "compliance_score": 82.5,
        },
    ),
    (
        "critical-issues",
        "send_critical_issues",
        "alert_on_critical_issues",
        {
            "scan_id": "fixture-scan",
            "file_name": "Lecture.pdf",
            "critical_issues": [
                {
                    "type": "image-alt",
                    "description": "Missing alternative text",
                    "count": 2,
                }
            ],
        },
    ),
    (
        "weekly-summary",
        "send_weekly_summary",
        "alert_weekly_summary",
        {
            "start_date": "2026-01-05",
            "end_date": "2026-01-11",
            "total_scans": 7,
            "total_issues": 3,
            "avg_compliance_score": 0.825,
        },
    ),
]


def configure(case):
    response = case.client.put(
        "/api/alerts/settings", json={"email_addresses": ["faculty@example.edu"]}
    )
    assert response.status_code == 200


@pytest.mark.parametrize("path,method,toggle,payload", CASES)
def test_trigger_delivers_scoped_inputs(
    alert_route, monkeypatch, path, method, toggle, payload
):
    case = alert_route
    configure(case)
    monkeypatch.setattr(get_settings(), "dashboard_url", "https://campus.example.edu")
    response = case.client.post(f"/api/alerts/trigger/{path}", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["recipients_count"] == 1
    sender = getattr(case.mail, method)
    sender.assert_awaited_once()
    args = sender.await_args.kwargs
    assert args["to_emails"] == ["faculty@example.edu"]
    if path == "scan-complete":
        assert args == {
            "to_emails": ["faculty@example.edu"],
            "file_name": "Lecture.pdf",
            "compliance_score": 82.5,
            "issues_found": 3,
            "scan_url": "https://campus.example.edu/scans/fixture-scan",
        }
    elif path == "critical-issues":
        assert args["critical_issues"] == payload["critical_issues"]
        assert args["department"].id == case.department.id
        assert args["action_url"] == "https://campus.example.edu/scans/fixture-scan"
    else:
        assert args["department"].id == case.department.id
        assert args["department_name"] == case.department.name
        assert args["total_files"] == args["scans_this_week"] == 7
        assert args["total_issues"] == 3
        assert args["average_score"] == 82.5
        assert args["week_start"] == payload["start_date"]
        assert args["week_end"] == payload["end_date"]
        assert args["dashboard_url"] == "https://campus.example.edu/dashboard"
        assert args.get("issues_fixed") is None
        assert "minor_count" not in args  # Severity was not supplied.


@pytest.mark.parametrize("path,method,toggle,payload", CASES)
@pytest.mark.parametrize("mode", ["disabled", "empty"])
def test_trigger_no_delivery_when_disabled_or_empty(
    alert_route, path, method, toggle, payload, mode
):
    case = alert_route
    configure(case)
    update = {toggle: False} if mode == "disabled" else {"email_addresses": []}
    assert case.client.put("/api/alerts/settings", json=update).status_code == 200
    response = case.client.post(f"/api/alerts/trigger/{path}", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["recipients_count"] == 0
    getattr(case.mail, method).assert_not_awaited()


@pytest.mark.parametrize("path,method,toggle,payload", CASES)
@pytest.mark.parametrize("failure", ["result", "exception"])
def test_trigger_delivery_failure_after_success(
    alert_route, path, method, toggle, payload, failure
):
    case = alert_route
    configure(case)
    sender = getattr(case.mail, method)
    assert (
        case.client.post(f"/api/alerts/trigger/{path}", json=payload).json()["success"]
        is True
    )
    if failure == "result":
        sender.return_value = {"success": False, "error": "fixture mail unavailable"}
    else:
        sender.side_effect = RuntimeError("fixture mail unavailable")
    response = case.client.post(f"/api/alerts/trigger/{path}", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["recipients_count"] == 0
    assert "fixture mail unavailable" not in response.text


@pytest.mark.parametrize(
    "email_type", ["scan_complete", "critical_issues", "weekly_summary"]
)
def test_sample_email_uses_real_service_signature(alert_route, email_type):
    case = alert_route
    configure(case)
    response = case.client.post("/api/alerts/test", json={"email_type": email_type})
    assert response.status_code == 200
    assert response.json()["success"] is True
    sender = getattr(case.mail, f"send_{email_type}")
    sender.assert_awaited_once()
    assert sender.await_args.kwargs["to_emails"] == ["faculty@example.edu"]
    sender.return_value = {"success": False, "error": "fixture rejection"}
    response = case.client.post("/api/alerts/test", json={"email_type": email_type})
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "fixture rejection" not in response.text


def test_sample_email_input_refusal(alert_route):
    case = alert_route
    assert (
        case.client.post(
            "/api/alerts/test", json={"email_type": "scan_complete"}
        ).status_code
        == 400
    )
    configure(case)
    assert (
        case.client.post("/api/alerts/test", json={"email_type": "unknown"}).status_code
        == 400
    )
    case.mail.send_scan_complete.assert_not_awaited()


def test_custom_send_uses_current_service_contract(alert_route):
    case = alert_route
    payload = {
        "recipients": ["one@example.edu", "two@example.edu"],
        "subject": "Lecture ready",
        "body": "Ready to review.",
    }
    response = case.client.post("/api/alerts/send", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["recipients_count"] == 2
    assert [call.kwargs for call in case.mail.send_email.await_args_list] == [
        {
            "to_emails": [email],
            "subject": payload["subject"],
            "html_content": "<p>Ready to review.</p>",
        }
        for email in payload["recipients"]
    ]
    case.mail.send_email.reset_mock()
    case.mail.send_email.side_effect = [{"success": True}, {"success": False}]
    response = case.client.post("/api/alerts/send", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["recipients_count"] == 1


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"recipients": [], "subject": "Lecture", "body": "Ready"}, 400),
        ({"recipients": ["invalid"], "subject": "Lecture", "body": "Ready"}, 422),
    ],
)
def test_custom_send_invalid_input(alert_route, payload, status):
    response = alert_route.client.post("/api/alerts/send", json=payload)
    assert response.status_code == status
    alert_route.mail.send_email.assert_not_awaited()


@pytest.mark.parametrize("path,method,toggle,payload", CASES)
def test_trigger_requires_identity_and_input(
    alert_route, path, method, toggle, payload
):
    case = alert_route
    assert case.client.post(f"/api/alerts/trigger/{path}", json={}).status_code == 422
    case.application.dependency_overrides.pop(routes.get_current_api_key)
    assert (
        case.client.post(f"/api/alerts/trigger/{path}", json=payload).status_code == 401
    )
    getattr(case.mail, method).assert_not_awaited()


def test_history_explicitly_reports_unavailable_tracking(alert_route):
    case = alert_route
    configure(case)
    assert (
        case.client.post(
            "/api/alerts/test", json={"email_type": "weekly_summary"}
        ).json()["success"]
        is True
    )
    response = case.client.get("/api/alerts/history", params={"limit": 10})
    assert response.status_code == 200
    assert response.json() == {
        "alerts": [],
        "message": "Email history tracking coming soon.",
    }
    case.db.expire_all()
    assert case.db.query(EmailAlertSettings).filter_by(
        department_id=case.department.id
    ).one().email_addresses == ["faculty@example.edu"]


@pytest.mark.parametrize("path,method,toggle,payload", CASES)
def test_trigger_recipient_scope_follows_current_department(
    alert_route, path, method, toggle, payload
):
    case = alert_route
    configure(case)
    case.principal.department_id = case.other_department.id
    assert (
        case.client.put(
            "/api/alerts/settings", json={"email_addresses": ["library@example.edu"]}
        ).status_code
        == 200
    )
    response = case.client.post(f"/api/alerts/trigger/{path}", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert getattr(case.mail, method).await_args.kwargs["to_emails"] == [
        "library@example.edu"
    ]
    case.principal.department_id = case.department.id
    assert case.client.get("/api/alerts/emails").json() == {
        "emails": ["faculty@example.edu"],
        "count": 1,
    }


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/api/alerts/history", None),
        ("POST", "/api/alerts/test", {"email_type": "scan_complete"}),
        (
            "POST",
            "/api/alerts/send",
            {
                "recipients": ["faculty@example.edu"],
                "subject": "Lecture",
                "body": "Ready",
            },
        ),
    ],
)
def test_delivery_routes_require_identity(alert_route, method, path, payload):
    case = alert_route
    case.application.dependency_overrides.pop(routes.get_current_api_key)
    response = case.client.request(method, path, json=payload)
    assert response.status_code == 401
    assert case.mail.mock_calls == []


def test_custom_send_exception_after_success(alert_route):
    case = alert_route
    payload = {
        "recipients": ["faculty@example.edu"],
        "subject": "Lecture",
        "body": "Research & teaching",
    }
    assert case.client.post("/api/alerts/send", json=payload).json()["success"] is True
    assert (
        case.mail.send_email.await_args.kwargs["html_content"]
        == "<p>Research &amp; teaching</p>"
    )
    case.mail.send_email.side_effect = RuntimeError("fixture delivery unavailable")
    response = case.client.post("/api/alerts/send", json=payload)
    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "message": "Failed to send alert.",
        "recipients_count": 0,
    }


def test_real_template_service_reaches_controlled_transport(alert_route, monkeypatch):
    """Unlike autospec mapping tests, executes the actual scan mail renderer."""
    from src import mailer

    case = alert_route
    configure(case)
    service = EmailService()
    transport = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(service, "send_email", transport)
    monkeypatch.setattr(mailer, "get_email_service", lambda: service)
    response = case.client.post("/api/alerts/trigger/scan-complete", json=CASES[0][3])
    assert response.status_code == 200
    assert response.json()["success"] is True
    transport.assert_awaited_once()
    assert transport.await_args.kwargs["to_emails"] == ["faculty@example.edu"]
    assert "Lecture.pdf" in transport.await_args.kwargs["html_content"]
    assert "82%" in transport.await_args.kwargs["html_content"]


def test_weekly_renderer_keeps_unknown_severity_unavailable(alert_route, monkeypatch):
    from src import mailer

    case = alert_route
    configure(case)
    service = EmailService()
    transport = AsyncMock(return_value={"success": True})
    render = Mock(wraps=service.render_template)
    monkeypatch.setattr(service, "send_email", transport)
    monkeypatch.setattr(service, "render_template", render)
    monkeypatch.setattr(mailer, "get_email_service", lambda: service)
    response = case.client.post("/api/alerts/trigger/weekly-summary", json=CASES[2][3])
    assert response.status_code == 200
    assert response.json()["success"] is True
    variables = render.call_args.args[1]
    assert variables["total_issues"] == 3
    for severity in ("critical", "serious", "moderate", "minor"):
        assert variables[f"{severity}_count"] == "Not available"
    rendered = transport.await_args.kwargs["html_content"]
    assert rendered.count("Not available") >= 5
    assert "2026-01-05" in rendered
    assert "2026-01-11" in rendered


@pytest.mark.parametrize(
    "counts,percentages",
    [
        ([0, 0, 0, 0], [0, 0, 0, 0]),
        ([1, 2, 3, 4], [10, 20, 30, 40]),
        ([1, None, None, None], [0, 0, 0, 0]),
    ],
)
async def test_weekly_renderer_preserves_measured_counts(
    monkeypatch, counts, percentages
):
    service = EmailService()
    transport = AsyncMock(return_value={"success": True})
    render = Mock(wraps=service.render_template)
    monkeypatch.setattr(service, "send_email", transport)
    monkeypatch.setattr(service, "render_template", render)
    names = ("critical", "serious", "moderate", "minor")
    result = await service.send_weekly_summary(
        to_emails=["faculty@example.edu"],
        department_name="Faculty",
        total_files=7,
        **{f"{name}_count": count for name, count in zip(names, counts)},
    )
    assert result == {"success": True}
    variables = render.call_args.args[1]
    for name, count, percentage in zip(names, counts, percentages):
        assert variables[f"{name}_count"] == (
            count if count is not None else "Not available"
        )
        assert variables[f"{name}_percent"] == percentage
    assert "{{" not in transport.await_args.kwargs["html_content"]
