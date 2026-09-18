"""Analytics HTTP contracts with rollback-only PostgreSQL rows.

Identity is supplied at the principal dependency. Individual snapshot capture and
exports use real services; capture-all and selected prediction/trend seams are controlled.
These contracts do not establish browser journeys or accessibility conformance.
"""

import csv
from datetime import datetime, timezone
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api import analytics
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.config.settings import get_settings
from src.db.database import get_db_dependency
from src.db.models import (
    ComplianceSnapshot,
    Department,
    IssuePriority,
    IssueStatus,
    IssueTracking,
    Scan,
    ScanResult,
    ScanStatus,
    ScanType,
    User,
    UserRole,
)


@pytest.fixture
def analytics_case():
    engine = create_engine(get_settings().database_url)
    assert engine.dialect.name == "postgresql"
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    departments, users, scans = [], [], []
    for label in ("owned", "foreign"):
        department = Department(
            id=str(uuid4()),
            name=label,
            institution="Example",
            contact_email=f"{label}@example.edu",
        )
        db.add(department)
        db.flush()
        user = User(
            id=str(uuid4()),
            department_id=department.id,
            email=f"{uuid4()}@example.edu",
            role=UserRole.ADMIN,
        )
        db.add(user)
        db.flush()
        scan = Scan(
            id=str(uuid4()),
            department_id=department.id,
            user_id=user.id,
            file_name=f"{label}-résumé.pdf",
            scan_type=ScanType.PDF,
            status=ScanStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
        )
        db.add(scan)
        db.flush()
        db.add(
            ScanResult(
                id=str(uuid4()),
                scan_id=scan.id,
                compliance_score=80,
                critical_issues=0,
                high_issues=1,
                medium_issues=0,
                low_issues=0,
                issues=[],
            )
        )
        db.add(
            IssueTracking(
                id=str(uuid4()),
                scan_id=scan.id,
                department_id=department.id,
                issue_hash=str(uuid4()),
                issue_type="missing_alt",
                severity=IssuePriority.HIGH,
                status=IssueStatus.OPEN,
                description=f"{label} finding",
            )
        )
        departments.append(department)
        users.append(user)
        scans.append(scan)
    db.commit()
    case = SimpleNamespace(db=db, departments=departments, users=users, scans=scans)
    case.principal = AuthenticatedPrincipal(
        api_key=None,
        user_id=users[0].id,
        department_id=departments[0].id,
        user_role=UserRole.ADMIN,
        auth_method="session",
    )
    app = FastAPI()
    app.include_router(analytics.router)
    app.dependency_overrides[get_authenticated_principal] = lambda: case.principal
    app.dependency_overrides[get_db_dependency] = lambda: db
    case.client = TestClient(app, raise_server_exceptions=False)
    case.url = lambda suffix: f"/analytics/{suffix}".replace(
        "{department}", departments[0].id
    )
    try:
        yield case
    finally:
        case.client.close()
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_snapshot_capture_persists_scoped_rollup(analytics_case):
    c = analytics_case
    response = c.client.post(c.url("snapshots/capture/{department}"))
    assert response.status_code == 200, response.text
    data = response.json()
    c.db.expire_all()
    saved = c.db.get(ComplianceSnapshot, data["snapshot_id"])
    assert saved.department_id == c.departments[0].id
    assert data["avg_compliance_score"] == saved.avg_compliance_score == 80
    assert data["total_issues"] == saved.total_issues == 1
    assert (
        c.db.query(ComplianceSnapshot)
        .filter_by(department_id=c.departments[1].id)
        .count()
        == 0
    )
    again = c.client.post(c.url("snapshots/capture/{department}"))
    assert again.status_code == 200
    assert again.json()["snapshot_id"] == saved.id


def test_capture_all_requires_global_principal_and_serializes_result(
    analytics_case, monkeypatch
):
    c = analytics_case
    capture = Mock(
        return_value=[SimpleNamespace(department_id=d.id) for d in c.departments]
    )
    monkeypatch.setattr(analytics.SnapshotService, "capture_all_departments", capture)
    c.principal = AuthenticatedPrincipal(
        api_key=None,
        user_id=c.users[0].id,
        department_id=c.departments[0].id,
        user_role=UserRole.SUPER_ADMIN,
        auth_method="session",
    )
    response = c.client.post("/analytics/snapshots/capture-all")
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "snapshots_captured": 2,
        "departments": [d.id for d in c.departments],
    }
    capture.assert_called_once_with(c.db)


def test_issue_list_and_stats_use_stored_department_rows(analytics_case):
    c = analytics_case
    response = c.client.get(
        c.url("issues/{department}"), params={"status": "open", "severity": "high"}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["count"] == 1
    assert data["issues"][0]["scan_id"] == c.scans[0].id
    assert data["issues"][0]["description"] == "owned finding"
    assert c.scans[1].id not in response.text
    response = c.client.get(c.url("issues/{department}/stats"))
    assert response.status_code == 200
    assert response.json()["stats"] == {
        "total_issues": 1,
        "open_issues": 1,
        "in_progress_issues": 0,
        "resolved_issues": 0,
        "wont_fix_issues": 0,
        "false_positive_issues": 0,
        "auto_fixable_issues": 0,
        "auto_fixed_issues": 0,
        "resolution_rate": 0.0,
    }
    empty = c.client.get(c.url("issues/{department}"), params={"status": "resolved"})
    assert empty.status_code == 200
    assert empty.json()["issues"] == []


@pytest.mark.parametrize("kind", ["csv", "excel", "bulk"])
@pytest.mark.parametrize("score", [80, 0, None])
def test_exports_preserve_scope_and_unknown_scores(analytics_case, kind, score):
    c = analytics_case
    result = c.db.query(ScanResult).filter_by(scan_id=c.scans[0].id).one()
    if score is None:
        c.db.delete(result)
    else:
        result.compliance_score = score
    c.db.commit()
    response = c.client.get(
        c.url("export/{department}/" + kind), params={"include_evidence_report": False}
    )
    assert response.status_code == 200, response.text
    assert int(response.headers["content-length"]) == len(response.content)
    assert "attachment;" in response.headers["content-disposition"]
    if kind == "excel":
        workbook = load_workbook(BytesIO(response.content))
        rows = list(workbook["All Scans"].values)
        workbook.close()
        assert rows[1][5] == (score if score is not None else "Not assessed")
    else:
        content = response.content
        if kind == "bulk":
            with ZipFile(BytesIO(content)) as archive:
                assert set(archive.namelist()) == {"summary.csv", "README.txt"}
                content = archive.read("summary.csv")
        rows = list(csv.reader(StringIO(content.decode())))
        assert rows[1][5] == (f"{score:.1f}" if score is not None else "Not assessed")
    assert len(rows) == 2
    assert rows[1][0] == c.scans[0].id
    assert rows[1][2] == c.scans[0].file_name
    assert c.scans[1].id not in str(rows)


@pytest.mark.parametrize(
    "query",
    [
        "status=unknown",
        "severity=unknown",
        "limit=0",
        "limit=-1",
        "limit=501",
        "offset=-1",
    ],
)
def test_issue_filters_reject_invalid_values(analytics_case, query):
    c = analytics_case
    response = c.client.get(c.url("issues/{department}") + "?" + query)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "suffix,query",
    [
        ("trend/{department}", "days=6"),
        ("trend/{department}/analysis", "current_period=0"),
        ("trend/{department}/analysis", "comparison_period=31"),
        ("alt-text-quality/{department}", "days=366"),
    ],
)
def test_analysis_bounds_are_validated(analytics_case, suffix, query):
    assert (
        analytics_case.client.get(analytics_case.url(suffix) + "?" + query).status_code
        == 422
    )


@pytest.mark.parametrize("kind", ["csv", "excel", "bulk"])
@pytest.mark.parametrize("field", ["date_from", "date_to"])
def test_export_rejects_invalid_dates(analytics_case, kind, field):
    c = analytics_case
    response = c.client.get(
        c.url("export/{department}/" + kind), params={field: "invalid"}
    )
    assert response.status_code == 400
    assert response.json()["detail"].startswith(f"Invalid {field} format")


@pytest.mark.parametrize(
    "suffix,method",
    [
        ("trend/{department}", "get_historical_trend"),
        ("trend/{department}/analysis", "analyze_trend"),
        ("projection/{department}", "get_deadline_projection"),
    ],
)
def test_trend_routes_bound_unexpected_errors(
    analytics_case, monkeypatch, suffix, method
):
    c = analytics_case
    success = c.client.get(c.url(suffix))
    assert success.status_code == 200, success.text
    monkeypatch.setattr(
        analytics.SnapshotService,
        method,
        Mock(side_effect=RuntimeError("private fixture failure")),
    )
    response = c.client.get(c.url(suffix))
    assert response.status_code == 500
    assert "private fixture failure" not in response.text
    assert set(response.json()) == {"detail"}


@pytest.mark.parametrize("kind", ["csv", "excel", "bulk"])
def test_export_query_failure_is_bounded(analytics_case, monkeypatch, kind):
    c = analytics_case
    assert (
        c.client.get(
            c.url("export/{department}/" + kind),
            params={"include_evidence_report": False},
        ).status_code
        == 200
    )
    monkeypatch.setattr(
        c.db, "query", Mock(side_effect=RuntimeError("private fixture failure"))
    )
    response = c.client.get(c.url("export/{department}/" + kind))
    assert response.status_code == 500
    assert "private fixture failure" not in response.text


def test_alt_text_quality_uses_only_measured_scores(analytics_case):
    c = analytics_case
    result = c.db.query(ScanResult).filter_by(scan_id=c.scans[0].id).one()
    result.issues = [
        {"type": "image_alt", "alt_text_quality_score": 90},
        {"type": "image_alt", "alt_text_quality_score": 80},
        {"type": "image_alt"},
    ]
    c.db.commit()
    response = c.client.get(c.url("alt-text-quality/{department}"))
    assert response.status_code == 200
    data = response.json()
    assert data["overall_average_score"] == 85
    assert data["total_images_analyzed"] == 2
    assert data["grade_distribution"] == {"A": 1, "B": 1, "C": 0, "D": 0, "F": 0}
    assert data["wcag_compliance_rate"] is None


def test_alt_text_without_measurements_stays_unknown(analytics_case):
    c = analytics_case
    response = c.client.get(c.url("alt-text-quality/{department}"))
    assert response.status_code == 200
    assert response.json()["overall_average_score"] is None
    assert response.json()["average_grade"] is None
    assert response.json()["wcag_compliance_rate"] is None


@pytest.mark.parametrize("empty", [False, True])
def test_trend_serializes_exact_points_and_request_scope(
    analytics_case, monkeypatch, empty
):
    c = analytics_case
    point = {
        "date": "2026-01-02",
        "avg_compliance_score": 80,
        "scan_count": 1,
        "total_issues": 1,
        "files_compliant": 0,
        "files_needs_work": 1,
        "files_critical": 0,
    }
    get_trend = Mock(return_value=[] if empty else [SimpleNamespace(**point)])
    monkeypatch.setattr(analytics.SnapshotService, "get_historical_trend", get_trend)
    response = c.client.get(c.url("trend/{department}"), params={"days": 14})
    assert response.status_code == 200
    assert response.json() == {
        "department_id": c.departments[0].id,
        "period_days": 14,
        "data_points": 0 if empty else 1,
        "trend": [] if empty else [point],
    }
    get_trend.assert_called_once_with(c.db, c.departments[0].id, 14)


def test_analysis_serializes_exact_service_fields(analytics_case, monkeypatch):
    c = analytics_case
    fields = {
        "current_avg_score": 80,
        "previous_avg_score": 75,
        "score_change": 5,
        "score_change_pct": 6.67,
        "current_total_issues": 2,
        "previous_total_issues": 3,
        "issues_change": -1,
        "issues_change_pct": -33.33,
        "trend_direction": "improving",
        "on_track_for_deadline": None,
    }
    deadline = {"has_deadline": False}
    analyze = Mock(return_value=SimpleNamespace(**fields, deadline=deadline))
    monkeypatch.setattr(analytics.SnapshotService, "analyze_trend", analyze)
    response = c.client.get(
        c.url("trend/{department}/analysis"),
        params={"current_period": 3, "comparison_period": 5},
    )
    assert response.status_code == 200
    assert response.json() == {
        "department_id": c.departments[0].id,
        "current_period_days": 3,
        "comparison_period_days": 5,
        "analysis": fields,
        "deadline": deadline,
    }
    analyze.assert_called_once_with(c.db, c.departments[0].id, 3, 5)


@pytest.mark.parametrize("kind", ["projection", "predict"])
def test_prediction_routes_preserve_unknown_service_values(
    analytics_case, monkeypatch, kind
):
    from src.education import compliance_predictor

    c = analytics_case
    result = {"projected_score": None, "deadline": {"has_deadline": False}}
    service = Mock(return_value=result)
    if kind == "projection":
        monkeypatch.setattr(
            analytics.SnapshotService, "get_deadline_projection", service
        )
    else:
        monkeypatch.setattr(compliance_predictor, "predict_compliance", service)
    response = c.client.get(c.url(kind + "/{department}"))
    assert response.status_code == 200
    assert response.json() == (
        {"department_id": c.departments[0].id, "projection": result}
        if kind == "projection"
        else result
    )
    service.assert_called_once_with(c.db, c.departments[0].id)
    service.side_effect = RuntimeError("private prediction failure")
    failed = c.client.get(c.url(kind + "/{department}"))
    assert failed.status_code == 500
    assert "private prediction failure" not in failed.text


@pytest.mark.parametrize("all_departments", [False, True])
def test_snapshot_service_failure_is_bounded(
    analytics_case, monkeypatch, all_departments
):
    c = analytics_case
    if all_departments:
        c.principal = AuthenticatedPrincipal(
            api_key=None,
            user_id=c.users[0].id,
            department_id=c.departments[0].id,
            user_role=UserRole.SUPER_ADMIN,
            auth_method="session",
        )
    method = "capture_all_departments" if all_departments else "capture_daily_snapshot"
    monkeypatch.setattr(
        analytics.SnapshotService,
        method,
        Mock(side_effect=RuntimeError("private capture failure")),
    )
    response = c.client.post(
        "/analytics/snapshots/capture-all"
        if all_departments
        else c.url("snapshots/capture/{department}")
    )
    assert response.status_code == 500
    assert "private capture failure" not in response.text
    assert (
        c.db.query(ComplianceSnapshot)
        .filter_by(department_id=c.departments[0].id)
        .count()
        == 0
    )


@pytest.mark.parametrize(
    "suffix",
    [
        "issues/{department}",
        "issues/{department}/stats",
        "alt-text-quality/{department}",
    ],
)
def test_query_failure_after_allowed_read_is_bounded(
    analytics_case, monkeypatch, suffix
):
    c = analytics_case
    assert c.client.get(c.url(suffix)).status_code == 200
    monkeypatch.setattr(
        c.db, "query", Mock(side_effect=RuntimeError("private query failure"))
    )
    response = c.client.get(c.url(suffix))
    assert response.status_code == 500
    assert "private query failure" not in response.text


@pytest.mark.parametrize("invalid_score", [True, "90", -1, 101, None, 10**400])
def test_alt_text_invalid_measurements_are_not_counted(analytics_case, invalid_score):
    c = analytics_case
    result = c.db.query(ScanResult).filter_by(scan_id=c.scans[0].id).one()
    result.issues = [{"type": "image_alt", "alt_text_quality_score": invalid_score}]
    c.db.commit()
    response = c.client.get(c.url("alt-text-quality/{department}"))
    assert response.status_code == 200
    assert response.json()["total_images_analyzed"] == 0
    assert response.json()["overall_average_score"] is None


@pytest.mark.parametrize(
    "filters",
    [{"status": "OPEN", "severity": "HIGH"}, {"status": "Open", "severity": "High"}],
)
def test_issue_filters_preserve_case_insensitive_values(analytics_case, filters):
    c = analytics_case
    response = c.client.get(c.url("issues/{department}"), params=filters)
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["issues"][0]["scan_id"] == c.scans[0].id


def test_quality_measured_zero_is_preserved(analytics_case):
    c = analytics_case
    result = c.db.query(ScanResult).filter_by(scan_id=c.scans[0].id).one()
    result.issues = [{"type": "image_alt", "alt_text_quality_score": 0}]
    c.db.commit()
    response = c.client.get(c.url("alt-text-quality/{department}"))
    assert response.status_code == 200
    assert response.json()["total_images_analyzed"] == 1
    assert response.json()["overall_average_score"] == 0
    assert response.json()["average_grade"] == "F"


def test_quality_empty_department_returns_unavailable(analytics_case):
    c = analytics_case
    empty = Department(
        id=str(uuid4()),
        name="Empty",
        institution="Example",
        contact_email="empty@example.edu",
    )
    c.db.add(empty)
    c.db.commit()
    c.principal = AuthenticatedPrincipal(
        api_key=None,
        user_id=c.users[0].id,
        department_id=empty.id,
        user_role=UserRole.SUPER_ADMIN,
        auth_method="session",
    )
    response = c.client.get(f"/analytics/alt-text-quality/{empty.id}")
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "department_id": empty.id,
        "period_days": 30,
        "message": "No scans found in the specified period",
        "overall_average_score": None,
        "total_images_analyzed": 0,
    }
