"""Alert emails link to the dashboard's scan page, which lives at /scan/{id}."""

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.jobs import remediation_job

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[1]


def test_dashboard_has_a_scan_route_and_no_scans_route():
    app = (ROOT / "dashboard/src/App.tsx").read_text()
    assert 'path="/scan/:id"' in app
    assert 'path="/scans' not in app


@pytest.mark.parametrize(
    "outcome, method",
    [
        ({"fixed_count": 2, "failed_count": 0}, "send_scan_complete_alert"),
        (
            {"fixed_count": 1, "failed_count": 1},
            "send_remediation_partial_success_alert",
        ),
        ({"fixed_count": 0, "failed_count": 2}, "send_remediation_failure_alert"),
    ],
)
def test_remediation_emails_link_to_the_absolute_scan_page(
    monkeypatch, outcome, method
):
    calls = {}

    class FakeAlertService:
        def __getattr__(self, name):
            async def record(**kwargs):
                calls[name] = kwargs
                return True

            return record

    monkeypatch.setattr(
        "src.services.alert_service.AlertService", lambda: FakeAlertService()
    )
    monkeypatch.setattr(
        "src.config.settings.get_settings",
        lambda: SimpleNamespace(dashboard_url="https://campus.example.edu/"),
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        contact_email="a11y@example.edu"
    )
    scan = SimpleNamespace(id="scan-1", file_name="course.docx")
    result = SimpleNamespace(
        manual_count=1,
        remediated_compliance_score=90.0,
        fixed_issues=[],
        failed_issues=[{"error": "failed"}],
        **outcome,
    )

    asyncio.run(
        remediation_job._send_remediation_notification(scan, result, "dept-1", db)
    )

    assert method in calls
    assert calls[method]["scan_url"] == "https://campus.example.edu/scan/scan-1"


def test_no_alert_email_builds_a_scans_link():
    for relative in (
        "src/jobs/email_alert_job.py",
        "src/jobs/remediation_job.py",
        "src/api/alert_routes.py",
    ):
        text = (ROOT / relative).read_text()
        assert not re.search(r"(dashboard_url|base_url)[^\n]*/scans/", text)
        assert 'scan_url=f"/scans/' not in text
