"""Scan alerts and the weekly summary read scores and findings from ScanResult.

These tests use real ``Scan`` and ``ScanResult`` rows in an in-memory SQLite
database and stub only the lowest email seam, ``EmailService.send_email``.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, MetaData, create_engine
from sqlalchemy.orm import Session

from src.config.settings import get_settings
from src.db.models import (
    CloudFile,
    Department,
    EmailAlertSettings,
    Scan,
    ScanResult,
    ScanStatus,
    ScanType,
    User,
)
from src.jobs import email_alert_job, weekly_summary_job
from src.jobs.contracts import JobFailure
from src.mailer.email_service import EmailService

RECIPIENT = "accessibility@example.test"
WINDOW_END = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)
WINDOW_START = WINDOW_END - timedelta(days=7)
IN_WINDOW = WINDOW_END - timedelta(days=1)


def _create_portable_table(model, engine, metadata: MetaData) -> None:
    """Create a model's table on SQLite, dropping Postgres-only DDL.

    Department and User carry JSONB casts in defaults and CHECK constraints
    that SQLite cannot parse, and foreign keys point at tables this test does
    not create; the columns the alert jobs read are unchanged.
    """
    table = model.__table__.to_metadata(metadata)
    for constraint in list(table.constraints):
        if isinstance(constraint, (CheckConstraint, ForeignKeyConstraint)):
            table.constraints.discard(constraint)
    for column in table.columns:
        column.foreign_keys.clear()
    for column in table.columns:
        default = column.server_default
        if default is not None and "::" in str(getattr(default, "arg", "")):
            column.server_default = None
    table.create(engine)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for model in (Department, User, Scan, ScanResult, CloudFile, EmailAlertSettings):
        _create_portable_table(model, engine, metadata)
    with Session(engine) as session:
        session.add(
            Department(
                id="dept-1",
                name="Mathematics",
                institution="Example University",
                contact_email="owner@example.test",
            )
        )
        session.add(
            User(
                id="user-1",
                email=RECIPIENT,
                department_id="dept-1",
            )
        )
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def send_email(monkeypatch):
    """Real EmailService rendering; only the transport is stubbed."""
    service = EmailService()
    stub = AsyncMock(return_value={"success": True})
    service.send_email = stub
    monkeypatch.setattr(email_alert_job, "get_email_service", lambda: service)
    return stub


def _settings(db: Session, **overrides) -> EmailAlertSettings:
    settings = EmailAlertSettings(
        id="settings-1",
        department_id="dept-1",
        email_addresses=[RECIPIENT],
        **overrides,
    )
    db.add(settings)
    db.commit()
    return settings


def _scan(
    db: Session,
    scan_id: str,
    *,
    score: float | None = 85.0,
    issues: list | None = None,
    with_result: bool = True,
    file_name: str = "syllabus.pdf",
    created_at: datetime = IN_WINDOW,
) -> Scan:
    scan = Scan(
        id=scan_id,
        department_id="dept-1",
        scan_type=ScanType.PDF,
        status=ScanStatus.COMPLETED,
        file_name=file_name,
        created_at=created_at,
    )
    db.add(scan)
    if with_result:
        db.add(
            ScanResult(
                id=f"result-{scan_id}",
                scan_id=scan_id,
                compliance_score=score,
                issues=issues,
            )
        )
    db.commit()
    db.refresh(scan)
    return scan


def _issues(*severities: str) -> list[dict]:
    return [
        {"severity": s, "rule": f"WCAG-{i}", "message": f"Problem {i}"}
        for i, s in enumerate(severities)
    ]


@pytest.mark.asyncio
async def test_scan_alert_email_carries_result_score_issue_count_and_file_name(
    db, send_email, monkeypatch
):
    monkeypatch.setattr(get_settings(), "dashboard_url", "https://campus.example.edu")
    _settings(db)
    scan = _scan(db, "scan-1", score=85.0, issues=_issues("medium", "low", "low"))

    sent = await email_alert_job.trigger_scan_alerts(db, scan)

    assert sent == {"scan_complete_sent": True, "critical_issues_sent": False}
    send_email.assert_awaited_once()
    kwargs = send_email.await_args.kwargs
    assert kwargs["to_emails"] == [RECIPIENT]
    assert "syllabus.pdf" in kwargs["subject"]
    assert "85%" in kwargs["subject"]
    assert "85%" in kwargs["html_content"]
    assert "3 issues found" in kwargs["html_content"]
    assert "https://campus.example.edu/scans/scan-1" in kwargs["html_content"]


@pytest.mark.asyncio
async def test_clean_scan_with_empty_findings_still_sends_scan_complete(db, send_email):
    _settings(db)
    scan = _scan(db, "scan-1", score=100.0, issues=[])

    sent = await email_alert_job.trigger_scan_alerts(db, scan)

    assert sent == {"scan_complete_sent": True, "critical_issues_sent": False}
    send_email.assert_awaited_once()
    html = send_email.await_args.kwargs["html_content"]
    assert "100%" in html
    assert "0 issues found" in html


@pytest.mark.asyncio
async def test_scan_with_unrecorded_findings_sends_no_scan_complete(db, send_email):
    _settings(db)
    scan = _scan(db, "scan-1", score=90.0, issues=None)

    sent = await email_alert_job.trigger_scan_alerts(db, scan)

    assert sent["scan_complete_sent"] is False
    send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_alert_links_use_the_configured_dashboard_url(
    db, send_email, monkeypatch
):
    monkeypatch.setattr(get_settings(), "dashboard_url", "https://campus.example.edu")
    settings = _settings(db)
    scan = _scan(db, "scan-1", score=40.0, issues=_issues("critical"))

    await email_alert_job.trigger_scan_alerts(db, scan)

    scan_html, critical_html = (
        call.kwargs["html_content"] for call in send_email.await_args_list
    )
    assert "https://campus.example.edu/scans/scan-1" in scan_html
    assert "https://campus.example.edu/scans/scan-1" in critical_html
    assert "https://campus.example.edu/remediate/scan-1" in critical_html
    assert "dashboard.example.com" not in scan_html + critical_html

    payload = email_alert_job.build_weekly_summary_payload(
        db, settings, window_start=WINDOW_START, window_end=WINDOW_END
    )
    assert payload["dashboard_url"] == (
        "https://campus.example.edu/dashboard?dept=dept-1"
    )


@pytest.mark.asyncio
async def test_explicit_base_url_overrides_the_configured_dashboard_url(
    db, send_email, monkeypatch
):
    monkeypatch.setattr(get_settings(), "dashboard_url", "https://campus.example.edu")
    _settings(db)
    scan = _scan(db, "scan-1", score=90.0, issues=[])

    await email_alert_job.trigger_scan_alerts(
        db, scan, app_base_url="https://other.example.org/"
    )

    html = send_email.await_args.kwargs["html_content"]
    assert "https://other.example.org/scans/scan-1" in html


@pytest.mark.asyncio
async def test_file_name_is_escaped_exactly_once_in_scan_emails(db, send_email):
    _settings(db)
    name = "Q&A <draft>.pdf"
    scan = _scan(db, "scan-1", score=40.0, issues=_issues("critical"), file_name=name)

    await email_alert_job.trigger_scan_alerts(db, scan)

    for call in send_email.await_args_list:
        html = call.kwargs["html_content"]
        assert "Q&amp;A &lt;draft&gt;.pdf" in html
        assert "&amp;amp;" not in html
        assert "&amp;lt;" not in html
        assert name in call.kwargs["subject"]
    scan_html = send_email.await_args_list[0].kwargs["html_content"]
    # The hand-built message is markup, not text shown to the reader.
    assert "<strong>Q&amp;A &lt;draft&gt;.pdf</strong>" in scan_html
    assert "&lt;p&gt;" not in scan_html


@pytest.mark.asyncio
async def test_critical_findings_send_a_critical_alert_with_rule_names(db, send_email):
    _settings(db)
    scan = _scan(db, "scan-1", score=40.0, issues=_issues("critical", "high"))

    sent = await email_alert_job.trigger_scan_alerts(db, scan)

    assert sent == {"scan_complete_sent": True, "critical_issues_sent": True}
    assert send_email.await_count == 2
    critical_html = send_email.await_args_list[1].kwargs["html_content"]
    assert "WCAG-0" in critical_html
    assert "Problem 0" in critical_html
    assert "Unknown" not in critical_html


@pytest.mark.asyncio
async def test_scan_without_result_sends_no_fabricated_zero_score(db, send_email):
    _settings(db)
    scan = _scan(db, "scan-1", with_result=False)

    sent = await email_alert_job.trigger_scan_alerts(db, scan)

    assert sent == {"scan_complete_sent": False, "critical_issues_sent": False}
    send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_unscored_result_sends_no_scan_complete_email(db, send_email):
    _settings(db)
    scan = _scan(db, "scan-1", score=None, issues=[])

    sent = await email_alert_job.trigger_scan_alerts(db, scan)

    assert sent["scan_complete_sent"] is False
    send_email.assert_not_awaited()


def test_weekly_summary_payload_aggregates_scan_results(db):
    settings = _settings(db)
    _scan(db, "scan-1", score=80.0, issues=_issues("critical", "high", "medium"))
    _scan(db, "scan-2", score=60.0, issues=_issues("low", "minor"))
    _scan(db, "scan-3", score=None, issues=[], file_name="video.mp4")
    _scan(db, "scan-4", with_result=False)
    _scan(db, "old", score=1.0, issues=_issues("critical"), created_at=WINDOW_START)
    db.query(Scan).filter(Scan.id == "old").update(
        {"created_at": WINDOW_START - timedelta(days=1)}
    )
    db.commit()

    payload = email_alert_job.build_weekly_summary_payload(
        db, settings, window_start=WINDOW_START, window_end=WINDOW_END
    )

    assert payload["scans_this_week"] == 4
    assert payload["average_score"] == 70.0
    assert payload["total_issues"] == 5
    assert payload["critical_count"] == 1
    assert payload["serious_count"] == 1
    assert payload["moderate_count"] == 1
    assert payload["minor_count"] == 2
    assert payload["to_emails"] == [RECIPIENT]


@pytest.mark.asyncio
async def test_weekly_summary_email_renders_result_values(db, send_email):
    settings = _settings(db)
    _scan(db, "scan-1", score=80.0, issues=_issues("critical", "high"))
    _scan(db, "scan-2", score=60.0, issues=_issues("low"))
    now = datetime.now(timezone.utc)
    for scan in db.query(Scan).all():
        scan.created_at = now - timedelta(days=1)
    db.commit()

    assert await email_alert_job._send_weekly_summary_for_department(db, settings)

    html = send_email.await_args.kwargs["html_content"]
    assert "70%" in html
    assert "{{" not in html


def test_weekly_summary_without_result_data_reports_not_available(db):
    settings = _settings(db)
    _scan(db, "scan-1", with_result=False)

    payload = email_alert_job.build_weekly_summary_payload(
        db, settings, window_start=WINDOW_START, window_end=WINDOW_END
    )

    assert payload["scans_this_week"] == 1
    assert payload["average_score"] is None
    assert payload["total_issues"] is None
    assert payload["critical_count"] is None
    assert payload["minor_count"] is None


@pytest.mark.asyncio
async def test_weekly_summary_email_says_not_available_instead_of_zero(db, send_email):
    settings = _settings(db)
    _scan(db, "scan-1", with_result=False)
    scan = db.get(Scan, "scan-1")
    scan.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    assert await email_alert_job._send_weekly_summary_for_department(db, settings)

    html = send_email.await_args.kwargs["html_content"]
    assert "Not available" in html
    assert not re.search(r">\s*\d+%\s*<", html)


def test_weekly_summary_with_no_scans_is_a_true_zero(db):
    settings = _settings(db)

    payload = email_alert_job.build_weekly_summary_payload(
        db, settings, window_start=WINDOW_START, window_end=WINDOW_END
    )

    assert payload["scans_this_week"] == 0
    assert payload["total_issues"] == 0
    assert payload["critical_count"] == 0
    assert payload["average_score"] is None


@pytest.mark.asyncio
async def test_weekly_job_logs_preparation_failure_at_error_level(
    db, monkeypatch, caplog
):
    _settings(db)
    job = SimpleNamespace(department_id="dept-1")
    database = MagicMock()
    database.get.return_value = job
    database.scalar.return_value = SimpleNamespace(alert_weekly_summary=True)
    monkeypatch.setattr(
        weekly_summary_job,
        "build_weekly_summary_payload",
        MagicMock(side_effect=AttributeError("no such column")),
    )
    context = SimpleNamespace(
        job_id="job-42",
        payload={
            "window_start": WINDOW_START.isoformat(),
            "window_end": WINDOW_END.isoformat(),
        },
        assert_owned=AsyncMock(),
        begin_external_effect=AsyncMock(),
    )

    with caplog.at_level(logging.ERROR, logger=weekly_summary_job.logger.name):
        result = await weekly_summary_job.handle_weekly_summary_job(
            context, database, None
        )

    assert isinstance(result, JobFailure)
    records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(records) == 1
    assert "job-42" in records[0].getMessage()
    assert "AttributeError" in records[0].getMessage()
