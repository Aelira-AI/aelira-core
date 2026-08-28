"""Regression coverage for canonical deadline output in reports and email."""

from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfReader

from src.db.models import Department
from src.education.accessibility_evidence_report import AccessibilityEvidenceReport
from src.education.deadline_config import DeadlineService
from src.jobs import email_alert_job
from src.mailer.email_service import EmailService
from src.services.alert_service import AlertService
from src.services.email_templates import (
    render_department_welcome_email,
    render_weekly_summary_email,
)


def _department(**overrides):
    values = {
        "id": "department-1",
        "name": "Accessibility Office",
        "institution": "Example University",
        "country_code": "AU",
        "regulatory_framework": "AU_DDA",
        "custom_deadline": None,
        "custom_deadline_verified_at": None,
        "title_ii_entity_class": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _pdf_text(pdf: bytes) -> str:
    return "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages
    )


def _report_with_standard(standard):
    return {
        "generated_at": "2026-08-28T00:00:00+00:00",
        "subject": {
            "department_name": "Accessibility Office",
            "institution": "Example University",
        },
        "coverage": {
            "scope": "scanned_content_only",
            "total_scans": 1,
            "completed_scans": 1,
            "unique_hashed_assets": 1,
            "unhashed_scan_count": 0,
            "pages_or_slides": 2,
            "content_types": {"pdf": 1},
            "earliest_scan_at": "2026-08-27T00:00:00+00:00",
            "latest_scan_at": "2026-08-27T00:00:00+00:00",
            "corpus_denominator": None,
        },
        "methodology": {
            "scan_modes": ["automated"],
            "engines_used": ["axe"],
            "engine_versions": [],
            "ruleset_version": None,
            "recorded_wcag_levels": ["AA"],
            "automated_checks_present": True,
            "validator_evidence_present": False,
            "human_review_present": False,
        },
        "score": {
            "average": 100,
            "minimum": 100,
            "maximum": 100,
            "is_conformance_determination": False,
        },
        "unresolved_findings": {
            "total": 0,
            "by_severity": {},
            "by_status": {},
            "tracker_coverage": "complete",
        },
        "verification": {
            "status": "automated_evidence_only",
            "automated_scan_count": 1,
            "validator_checkpoint_counts": {},
            "pending_manual_reviews": 0,
            "reviewed_fixes": 0,
            "statement": "Automated evidence only.",
        },
        "standard": standard,
        "limitations": [
            "Automated scan scores do not determine conformance or legal compliance."
        ],
        "support": {},
    }


@pytest.mark.parametrize(
    ("department", "expected"),
    [
        (
            _department(
                country_code="US",
                regulatory_framework="US_ADA_TITLE_II",
                title_ii_entity_class="large",
            ),
            ("April 26, 2027", "DOJ Title II ADA", "WCAG 2.1 Level AA"),
        ),
        (
            _department(
                country_code="US",
                regulatory_framework="US_ADA_TITLE_II",
                title_ii_entity_class="small_or_special_district",
            ),
            ("April 26, 2028", "DOJ Title II ADA", "WCAG 2.1 Level AA"),
        ),
        (
            _department(
                country_code="US",
                regulatory_framework="US_ADA_TITLE_II",
                title_ii_entity_class="large",
                custom_deadline=datetime(2030, 1, 15),
                custom_deadline_verified_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            ),
            ("January 15, 2030", "DOJ Title II ADA", "WCAG 2.1 Level AA"),
        ),
        (
            _department(country_code="DE", regulatory_framework="EU_EAA"),
            (
                "June 28, 2025",
                "European Accessibility Act (EAA)",
                "EN 301 549 (aligned with WCAG 2.1 AA)",
            ),
        ),
    ],
)
def test_weekly_helper_renders_canonical_dated_profiles(department, expected):
    rendered = render_weekly_summary_email(
        start_date="2026-08-21",
        end_date="2026-08-28",
        total_scans=2,
        total_issues=3,
        avg_compliance_score=82,
        department=department,
    )

    for value in expected:
        assert value in rendered
    assert "Configured accessibility target" in rendered
    assert "Automated scan results are bounded evidence" in rendered


@pytest.mark.parametrize(
    ("department", "expected_guidance"),
    [
        (
            _department(),
            "Disability Discrimination Act (DDA)",
        ),
        (
            _department(
                country_code="US",
                regulatory_framework="NONE",
            ),
            None,
        ),
    ],
)
def test_email_helpers_omit_deadline_claims_for_undated_profiles(
    department, expected_guidance
):
    weekly = render_weekly_summary_email(
        start_date="2026-08-21",
        end_date="2026-08-28",
        total_scans=0,
        total_issues=0,
        avg_compliance_score=0,
        department=department,
    )
    welcome = render_department_welcome_email(
        name="Alex",
        department_name=department.name,
        institution=department.institution,
        dashboard_url="https://dashboard.example.test",
        department=department,
    )

    for rendered in (weekly, welcome):
        assert "April" not in rendered
        assert "Title II" not in rendered
        assert "days remaining" not in rendered.lower()
        assert "on track" not in rendered.lower()
        if expected_guidance:
            assert expected_guidance in rendered
            assert "Accessibility guidance" in rendered
        else:
            assert "Configured accessibility target" not in rendered
            assert "Accessibility guidance" not in rendered


@pytest.mark.asyncio
async def test_active_critical_email_uses_neutral_undated_guidance():
    service = EmailService()
    service.send_email = AsyncMock(return_value={"success": True})

    await service.send_critical_issues(
        to_emails=["accessibility@example.test"],
        file_name="course.pdf",
        critical_issues=[{"type": "missing_alt", "description": "Missing alt"}],
        scan_url="https://dashboard.example.test/scans/1",
        department=_department(),
    )

    rendered = service.send_email.await_args.kwargs["html_content"]
    assert "Disability Discrimination Act (DDA)" in rendered
    assert "Accessibility guidance" in rendered
    assert "April" not in rendered
    assert "Title II" not in rendered
    assert "must be fixed to meet" not in rendered


@pytest.mark.asyncio
async def test_alert_service_forwards_department_profile_to_email_consumers():
    department = _department(
        country_code="US",
        regulatory_framework="US_ADA_TITLE_II",
        title_ii_entity_class="small_or_special_district",
    )
    email_service = SimpleNamespace(
        send_critical_issues=AsyncMock(return_value={"success": True}),
        send_weekly_summary=AsyncMock(return_value={"success": True}),
    )
    service = AlertService(email_service=email_service)
    service.check_department_alert_enabled = lambda *args: True
    service.filter_emails_by_preference = lambda emails, *args: emails
    database = _Database(department)

    critical_sent = await service.send_critical_issue_alert(
        to_emails=["accessibility@example.test"],
        scan_id="scan-1",
        file_name="course.pdf",
        critical_issues=[{"type": "missing_alt", "description": "Missing alt"}],
        department_id=department.id,
        db=database,
    )
    weekly_sent = await service.send_weekly_summary(
        to_emails=["accessibility@example.test"],
        start_date="2026-08-21",
        end_date="2026-08-28",
        total_scans=1,
        total_issues=1,
        avg_compliance_score=80,
        department_id=department.id,
        db=database,
    )

    assert critical_sent is True
    assert weekly_sent is True
    assert (
        email_service.send_critical_issues.await_args.kwargs["department"] is department
    )
    assert (
        email_service.send_weekly_summary.await_args.kwargs["department"] is department
    )


def test_evidence_pdf_omits_deadline_row_for_undated_profile():
    deadline = DeadlineService.for_department(_department())
    standard = {
        "applicability": deadline.applicability,
        "framework_code": deadline.framework_code,
        "framework_name": deadline.framework_name,
        "target_standard": deadline.standard,
        "deadline_date": None,
        "deadline_label": None,
        "has_deadline": False,
        "message": deadline.message,
        "applicability_source": "department_configuration",
    }

    text = _pdf_text(
        AccessibilityEvidenceReport.render(_report_with_standard(standard))
    )

    assert "Disability Discrimination Act (DDA)" in text
    assert "Deadline date" not in text
    assert "Deadline label" not in text
    assert "April" not in text
    assert "Title II" not in text
    assert "does not determine whether" in text
    assert "Conformance determination" in text
    assert "False" in text


class _Query:
    def __init__(self, model, department):
        self.model = model
        self.department = department

    def filter(self, *args):
        return self

    def first(self):
        return self.department if self.model is Department else None

    def all(self):
        return []

    def count(self):
        return 0


class _Database:
    def __init__(self, department):
        self.department = department

    def query(self, model):
        return _Query(model, self.department)


def test_evidence_collection_uses_department_profile():
    report = AccessibilityEvidenceReport.collect(
        _Database(_department()), "department-1"
    )

    assert report["standard"] == {
        "applicability": "ongoing_no_date",
        "framework_code": "AU_DDA",
        "framework_name": "Disability Discrimination Act (DDA)",
        "target_standard": "WCAG 2.1 Level AA (recommended)",
        "deadline_date": None,
        "deadline_label": None,
        "has_deadline": False,
        "message": (
            "This framework has an ongoing compliance obligation without a single "
            "deadline date."
        ),
        "applicability_source": "department_configuration",
    }


@pytest.mark.asyncio
async def test_scheduled_weekly_job_reaches_delivery_with_canonical_deadline(
    monkeypatch,
):
    department = _department(country_code="DE", regulatory_framework="EU_EAA")
    database = _Database(department)
    service = EmailService()
    service.send_email = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(email_alert_job, "get_email_service", lambda: service)
    monkeypatch.setattr(
        email_alert_job,
        "filter_emails_by_user_preference",
        lambda *args: ["accessibility@example.test"],
    )
    settings = SimpleNamespace(
        department_id=department.id,
        email_addresses=["accessibility@example.test"],
    )

    sent = await email_alert_job._send_weekly_summary_for_department(database, settings)

    assert sent is True
    service.send_email.assert_awaited_once()
    rendered = service.send_email.await_args.kwargs["html_content"]
    assert "European Accessibility Act (EAA)" in rendered
    assert "June 28, 2025" in rendered
    assert "{{" not in rendered
    assert "April 26, 2027" not in rendered
