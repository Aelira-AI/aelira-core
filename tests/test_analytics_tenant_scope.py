"""Tenant and authority boundaries for department-wide analytics."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, MetaData, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import analytics
from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import (
    AuditLog,
    Base,
    Department,
    IssuePriority,
    IssueStatus,
    IssueTracking,
    Scan,
    ScanStatus,
    ScanType,
    User,
    UserRole,
)

DEPARTMENT = "department-one"
OTHER_DEPARTMENT = "department-two"


@pytest.fixture
def tenant_db():
    """Run tenant mutation routes against real SQL rows without PostgreSQL."""

    table_names = {
        "audit_logs",
        "cloud_files",
        "cloud_job_queue",
        "cloud_oauth_credentials",
        "departments",
        "issue_tracking",
        "remediation_artifacts",
        "scans",
        "users",
    }
    metadata = MetaData()
    for name in table_names:
        Base.metadata.tables[name].to_metadata(metadata)
    for table in metadata.tables.values():
        for constraint in list(table.constraints):
            if isinstance(constraint, CheckConstraint):
                table.constraints.remove(constraint)
        for column in table.columns:
            if column.server_default and "::" in str(column.server_default.arg):
                column.server_default = None

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        yield db
    engine.dispose()


def _seed_tenant_issue_rows(db):
    db.add_all(
        [
            Department(
                id=DEPARTMENT,
                name="Accessibility",
                institution="Example University",
                contact_email="admin@example.edu",
            ),
            Department(
                id=OTHER_DEPARTMENT,
                name="Other",
                institution="Other University",
                contact_email="admin@other.example",
            ),
            User(
                id="user-one",
                email="actor@example.edu",
                department_id=DEPARTMENT,
                role=UserRole.ADMIN,
                is_active=True,
            ),
            User(
                id="user-two",
                email="member@example.edu",
                department_id=DEPARTMENT,
                role=UserRole.FACULTY,
                is_active=True,
            ),
            User(
                id="foreign-user",
                email="foreign@other.example",
                department_id=OTHER_DEPARTMENT,
                role=UserRole.ADMIN,
                is_active=True,
            ),
        ]
    )
    db.flush()
    db.add_all(
        [
            Scan(
                id="scan-one",
                scan_type=ScanType.PDF,
                status=ScanStatus.COMPLETED,
                file_name="one.pdf",
                user_id="user-one",
                department_id=DEPARTMENT,
            ),
            Scan(
                id="scan-other",
                scan_type=ScanType.PDF,
                status=ScanStatus.COMPLETED,
                file_name="other.pdf",
                user_id="foreign-user",
                department_id=OTHER_DEPARTMENT,
            ),
        ]
    )
    db.flush()
    db.add_all(
        [
            IssueTracking(
                id="issue-one",
                scan_id="scan-one",
                department_id=DEPARTMENT,
                issue_hash="hash-one",
                issue_type="missing_alt",
                severity=IssuePriority.HIGH,
                description="Missing alternative text",
                status=IssueStatus.OPEN,
            ),
            IssueTracking(
                id="issue-two",
                scan_id="scan-one",
                department_id=DEPARTMENT,
                issue_hash="hash-two",
                issue_type="heading_order",
                severity=IssuePriority.MEDIUM,
                description="Heading order is invalid",
                status=IssueStatus.OPEN,
            ),
            IssueTracking(
                id="issue-other",
                scan_id="scan-other",
                department_id=OTHER_DEPARTMENT,
                issue_hash="hash-other",
                issue_type="missing_label",
                severity=IssuePriority.HIGH,
                description="Form label is missing",
                status=IssueStatus.OPEN,
            ),
        ]
    )
    db.commit()


def _principal(
    role: UserRole = UserRole.FACULTY,
    *,
    auth_method: str = "session",
    account_wide: bool = False,
) -> AuthenticatedPrincipal:
    values = {
        "api_key": None,
        "user_id": "user-one",
        "department_id": DEPARTMENT,
        "user_role": role,
        "auth_method": auth_method,
    }
    if auth_method == "lti":
        values.update(
            {
                "lti_staff_role": "Administrator" if account_wide else "Instructor",
                "lti_account_wide": account_wide,
                "lti_course_id": None if account_wide else "course-one",
            }
        )
    return AuthenticatedPrincipal(**values)


DEPARTMENT_ROUTE_CASES = [
    (analytics.capture_snapshot, {}),
    (analytics.get_historical_trend, {"days": 30}),
    (
        analytics.get_trend_analysis,
        {"current_period": 7, "comparison_period": 7},
    ),
    (analytics.get_deadline_projection, {}),
    (
        analytics.get_department_issues,
        {
            "status": None,
            "severity": None,
            "assigned_to": None,
            "limit": 100,
            "offset": 0,
        },
    ),
    (analytics.get_issue_stats, {}),
    (analytics.generate_compliance_report, {"include_ai_recommendations": False}),
    (analytics.generate_compliance_certificate, {}),
    (analytics.check_certificate_eligibility, {}),
    (analytics.export_scans_csv, {"date_from": None, "date_to": None}),
    (analytics.export_scans_excel, {"date_from": None, "date_to": None}),
    (
        analytics.export_bulk_zip,
        {
            "include_pdfs": False,
            "include_certificate": False,
            "date_from": None,
            "date_to": None,
        },
    ),
    (analytics.predict_deadline_compliance, {}),
    (analytics.get_alt_text_quality_metrics, {"days": 30}),
]


def test_department_route_matrix_covers_every_department_path():
    routed_endpoints = {
        route.endpoint
        for route in analytics.router.routes
        if "{department_id}" in route.path
    }
    tested_endpoints = {route for route, _kwargs in DEPARTMENT_ROUTE_CASES}

    assert tested_endpoints == routed_endpoints


@pytest.mark.parametrize(("route", "kwargs"), DEPARTMENT_ROUTE_CASES)
def test_every_department_route_rejects_override_before_database_work(route, kwargs):
    db = MagicMock()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            route(
                department_id=OTHER_DEPARTMENT,
                db=db,
                principal=_principal(),
                **kwargs,
            )
        )

    assert exc.value.status_code == 403
    assert OTHER_DEPARTMENT not in str(exc.value.detail)
    db.query.assert_not_called()
    db.commit.assert_not_called()


def test_http_route_uses_typed_principal_as_scope_authority():
    db = MagicMock()
    app.dependency_overrides[get_db_dependency] = lambda: db
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal()
    try:
        response = TestClient(app).get(
            f"/analytics/trend/{OTHER_DEPARTMENT}", params={"days": 30}
        )
    finally:
        app.dependency_overrides.pop(get_db_dependency, None)
        app.dependency_overrides.pop(get_authenticated_principal, None)

    assert response.status_code == 403
    assert OTHER_DEPARTMENT not in response.text
    db.query.assert_not_called()


def test_issue_mutation_http_routes_persist_only_owned_rows_and_audits(tenant_db):
    _seed_tenant_issue_rows(tenant_db)
    app.dependency_overrides[get_db_dependency] = lambda: tenant_db
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal(
        UserRole.ADMIN
    )
    client = TestClient(app)
    try:
        status_response = client.patch(
            "/analytics/issues/issue-one/status", json={"status": "in_progress"}
        )
        assign_response = client.post(
            "/analytics/issues/issue-one/assign", json={"assigned_to": "user-two"}
        )
        note_response = client.post(
            "/analytics/issues/issue-one/note", json={"note": "Reviewed"}
        )
        bulk_response = client.post(
            "/analytics/issues/bulk-update",
            json={"issue_ids": ["issue-one", "issue-two"], "status": "resolved"},
        )
        foreign_response = client.patch(
            "/analytics/issues/issue-other/status", json={"status": "resolved"}
        )
    finally:
        app.dependency_overrides.pop(get_db_dependency, None)
        app.dependency_overrides.pop(get_authenticated_principal, None)

    assert status_response.status_code == 200
    assert assign_response.status_code == 200
    assert note_response.status_code == 200
    assert bulk_response.status_code == 200
    assert foreign_response.status_code == 404

    tenant_db.expire_all()
    owned = tenant_db.get(IssueTracking, "issue-one")
    second_owned = tenant_db.get(IssueTracking, "issue-two")
    foreign = tenant_db.get(IssueTracking, "issue-other")
    assert owned.status == IssueStatus.RESOLVED
    assert owned.assigned_to == "user-two"
    assert "Reviewed" in owned.notes
    assert second_owned.status == IssueStatus.RESOLVED
    assert foreign.status == IssueStatus.OPEN

    audits = tenant_db.query(AuditLog).order_by(AuditLog.created_at).all()
    assert [audit.action for audit in audits] == [
        "issue_status_update",
        "issue_assign",
        "issue_note_add",
        "issue_bulk_update",
    ]
    assert all(audit.user_id == "user-one" for audit in audits)
    assert all(audit.department_id == DEPARTMENT for audit in audits)


def test_department_denial_log_does_not_record_tenant_identifiers(caplog):
    with pytest.raises(HTTPException):
        asyncio.run(
            analytics.get_historical_trend(
                department_id=OTHER_DEPARTMENT,
                days=30,
                db=MagicMock(),
                principal=_principal(),
            )
        )

    assert DEPARTMENT not in caplog.text
    assert OTHER_DEPARTMENT not in caplog.text


def test_successful_authentication_log_omits_user_identifier(caplog):
    principal = _principal()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/analytics/trend/department-one",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
        }
    )
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="opaque-token"
    )
    caplog.set_level(logging.DEBUG)

    with (
        patch("src.auth.auth_service.AuthService.validate_api_key", return_value=None),
        patch("src.auth.session_service.get_session_service", return_value=MagicMock()),
        patch(
            "src.auth.dependencies.resolve_access_token",
            return_value=SimpleNamespace(principal=principal),
        ),
    ):
        resolved = get_authenticated_principal(
            request=request, credentials=credentials, db=MagicMock()
        )

    assert resolved == principal
    assert principal.user_id not in caplog.text
    assert "opaque-token" not in caplog.text


def test_snapshot_failure_log_omits_tenant_and_exception_text(caplog):
    marker = "raw-provider-secret"
    tenant_marker = "sensitive-department-id"
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(id=tenant_marker)
    ]

    with patch.object(
        analytics.SnapshotService,
        "capture_daily_snapshot",
        side_effect=RuntimeError(marker),
    ):
        snapshots = analytics.SnapshotService.capture_all_departments(db)

    assert snapshots == []
    assert marker not in caplog.text
    assert tenant_marker not in caplog.text


def test_course_scoped_lti_is_denied_department_analytics():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            analytics.get_historical_trend(
                department_id=DEPARTMENT,
                days=30,
                db=MagicMock(),
                principal=_principal(auth_method="lti"),
            )
        )

    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    ("route", "kwargs"),
    [
        (
            analytics.update_issue_status,
            {
                "issue_id": "issue-one",
                "request": analytics.UpdateIssueStatusRequest(status="resolved"),
            },
        ),
        (
            analytics.assign_issue,
            {
                "issue_id": "issue-one",
                "request": analytics.AssignIssueRequest(assigned_to="user-two"),
            },
        ),
        (
            analytics.add_issue_note,
            {
                "issue_id": "issue-one",
                "request": analytics.AddNoteRequest(note="Reviewed"),
            },
        ),
        (
            analytics.bulk_update_issues,
            {
                "request": analytics.BulkUpdateRequest(
                    issue_ids=["issue-one"], status="resolved"
                )
            },
        ),
    ],
)
def test_course_scoped_lti_is_denied_issue_collaboration(route, kwargs):
    db = MagicMock()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(route(db=db, principal=_principal(auth_method="lti"), **kwargs))

    assert exc.value.status_code == 403
    db.query.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "principal",
    [
        _principal(),
        _principal(auth_method="api_key"),
        _principal(UserRole.ADMIN, auth_method="lti", account_wide=True),
    ],
)
def test_authorized_principals_keep_own_department_access(principal):
    with patch.object(
        analytics.SnapshotService, "get_historical_trend", return_value=[]
    ) as get_trend:
        result = asyncio.run(
            analytics.get_historical_trend(
                department_id=DEPARTMENT,
                days=30,
                db=MagicMock(),
                principal=principal,
            )
        )

    assert result["department_id"] == DEPARTMENT
    get_trend.assert_called_once()


@pytest.mark.parametrize(
    "principal",
    [
        _principal(UserRole.FACULTY),
        _principal(UserRole.ADMIN),
        _principal(UserRole.ADMIN, auth_method="lti", account_wide=True),
    ],
)
def test_capture_all_rejects_non_global_principals(principal):
    with patch.object(
        analytics.SnapshotService, "capture_all_departments"
    ) as capture_all:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                analytics.capture_all_snapshots(db=MagicMock(), principal=principal)
            )

    assert exc.value.status_code == 403
    capture_all.assert_not_called()


def test_capture_all_allows_non_lti_super_admin():
    with patch.object(
        analytics.SnapshotService, "capture_all_departments", return_value=[]
    ) as capture_all:
        result = asyncio.run(
            analytics.capture_all_snapshots(
                db=MagicMock(), principal=_principal(UserRole.SUPER_ADMIN)
            )
        )

    assert result == {"success": True, "snapshots_captured": 0, "departments": []}
    capture_all.assert_called_once()


def test_unexpected_failure_is_sanitized_in_response_and_log(caplog):
    marker = "raw-database-secret"
    with patch.object(
        analytics.SnapshotService,
        "get_historical_trend",
        side_effect=RuntimeError(marker),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                analytics.get_historical_trend(
                    department_id=DEPARTMENT,
                    days=30,
                    db=MagicMock(),
                    principal=_principal(),
                )
            )

    assert exc.value.status_code == 500
    assert marker not in str(exc.value.detail)
    assert marker not in caplog.text
