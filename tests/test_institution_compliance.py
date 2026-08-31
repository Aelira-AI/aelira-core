"""Institution rollups keep current coverage explicit and tenant-scoped."""

import asyncio
from datetime import datetime, timedelta, timezone
from itertools import product
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import CheckConstraint, MetaData, create_engine
from sqlalchemy.orm import Session

from src.api import analytics
from src.api import auth_routes
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import Base, Department, ScanStatus, ScanType, UserRole
from src.education.current_compliance import (
    CurrentComplianceProjection,
    CurrentDocumentState,
    project_current_documents,
)
from src.education.institution_compliance import (
    DepartmentCurrentCompliance,
    InvalidComplianceStateError,
    aggregate_institution_compliance,
    get_institution_current_compliance,
)

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def _scan(
    scan_id: str,
    *,
    department_id: str,
    document_id: str | None = None,
    document_source: str = "standalone",
    file_hash: str | None = None,
    status: ScanStatus = ScanStatus.COMPLETED,
    created_offset: int = 0,
):
    created_at = NOW + timedelta(minutes=created_offset)
    return SimpleNamespace(
        id=scan_id,
        department_id=department_id,
        document_id=document_id,
        document_source=document_source,
        file_hash=file_hash,
        file_name=f"{scan_id}.pdf",
        scan_type=ScanType.PDF,
        status=status,
        remediation_outcome=None,
        created_at=created_at,
        completed_at=created_at if status is ScanStatus.COMPLETED else None,
        pages=1,
    )


def _result(scan_id: str, score: float):
    return SimpleNamespace(
        scan_id=scan_id,
        compliance_score=score,
        critical_issues=0,
        high_issues=0,
        medium_issues=0,
        low_issues=0,
    )


def _department_rollup(department_id: str, name: str, scores: list[float]):
    scans = [
        _scan(
            f"{department_id}-{index}",
            department_id=department_id,
            file_hash=f"{index + 1:064x}",
            created_offset=index,
        )
        for index, _score in enumerate(scores)
    ]
    projection = project_current_documents(
        scans,
        [_result(scan.id, score) for scan, score in zip(scans, scores)],
        [],
    )
    return DepartmentCurrentCompliance(
        department_id=department_id,
        department_name=name,
        projection=projection,
    )


def _principal(role: UserRole, *, auth_method: str = "session", account_wide=False):
    values = {
        "api_key": None,
        "user_id": "user-a",
        "department_id": "dept-a",
        "user_role": role,
        "auth_method": auth_method,
    }
    if auth_method == "lti":
        values.update(
            {
                "lti_staff_role": "Administrator" if account_wide else "Instructor",
                "lti_account_wide": account_wide,
                "lti_course_id": None if account_wide else "course-a",
            }
        )
    return AuthenticatedPrincipal(**values)


def test_document_weighted_score_does_not_flatten_unequal_departments():
    rollup = aggregate_institution_compliance(
        "Example University",
        [
            _department_rollup("dept-small", "Small", [100]),
            _department_rollup("dept-large", "Large", [0, 0, 0]),
        ],
    )

    assert rollup.document_weighted_score == 25
    assert rollup.flat_department_mean == 50
    assert rollup.flat_department_mean_label == "Secondary: flat department mean"
    assert rollup.coverage.enrolled == 4
    assert rollup.coverage.verified == 4


def test_coverage_keeps_failed_rescan_verified_score_and_canvas_staleness_visible():
    verified = _scan(
        "verified",
        department_id="dept-a",
        file_hash="a" * 64,
        created_offset=0,
    )
    failed_rescan = _scan(
        "failed-rescan",
        department_id="dept-a",
        file_hash="a" * 64,
        status=ScanStatus.FAILED,
        created_offset=10,
    )
    pending = _scan(
        "pending",
        department_id="dept-a",
        file_hash="b" * 64,
        status=ScanStatus.PENDING,
        created_offset=20,
    )
    canvas_file = SimpleNamespace(
        id="canvas-file",
        department_id="dept-a",
        provider="canvas",
        file_name="course-file.pdf",
        last_scan_id=None,
        needs_rescan=True,
    )
    projection = project_current_documents(
        [verified, failed_rescan, pending],
        [_result("verified", 80)],
        [canvas_file],
    )

    rollup = aggregate_institution_compliance(
        "Example University",
        [
            DepartmentCurrentCompliance(
                department_id="dept-a",
                department_name="Accessibility",
                projection=projection,
            )
        ],
    )

    assert rollup.document_weighted_score == 80
    assert rollup.coverage.enrolled == 3
    assert rollup.coverage.scanned == 2
    assert rollup.coverage.verified == 1
    assert rollup.coverage.stale == 1
    assert rollup.coverage.failed == 1
    assert rollup.coverage.total_coverage_percent == pytest.approx(33.33)
    assert rollup.departments[0].coverage.failed == 1
    assert any(
        document.source_kind == "cloud_file"
        for document in projection.current_documents
    )


def test_current_membership_changes_recompute_without_mutating_prior_rollup():
    first = aggregate_institution_compliance(
        "Example University",
        [_department_rollup("dept-a", "A", [100])],
    )
    first_payload = first.to_dict()

    second = aggregate_institution_compliance(
        "Example University",
        [
            _department_rollup("dept-a", "A", [100]),
            _department_rollup("dept-b", "B", [0]),
        ],
    )

    assert first.to_dict() == first_payload
    assert first.document_weighted_score == 100
    assert second.document_weighted_score == 50
    assert "trend" not in second.to_dict()
    assert "history" not in second.to_dict()


@pytest.mark.parametrize(
    "invalid_score", [None, "not-a-score", float("nan"), float("inf"), -1, 101]
)
def test_invalid_verified_scores_fail_closed(invalid_score):
    with pytest.raises(InvalidComplianceStateError):
        aggregate_institution_compliance(
            "Example University",
            [_department_rollup("dept-a", "A", [invalid_score])],
        )


def _coverage_state(kind: str, identity: str):
    verified_scan = _scan(
        f"{identity}-verified",
        department_id="dept-a",
        file_hash=f"{len(identity):064x}",
    )
    failed_scan = _scan(
        f"{identity}-failed",
        department_id="dept-a",
        file_hash=f"{len(identity):064x}",
        status=ScanStatus.FAILED,
        created_offset=1,
    )
    pending_scan = _scan(
        f"{identity}-pending",
        department_id="dept-a",
        file_hash=f"{len(identity):064x}",
        status=ScanStatus.PENDING,
        created_offset=1,
    )
    states = {
        "unscanned": (None, None, None, False, (0, 0, 0, 0)),
        "pending": (None, None, pending_scan, False, (1, 0, 0, 0)),
        "verified": (
            verified_scan,
            _result(verified_scan.id, 80),
            verified_scan,
            False,
            (1, 1, 0, 0),
        ),
        "stale_verified": (
            verified_scan,
            _result(verified_scan.id, 80),
            verified_scan,
            True,
            (1, 1, 1, 0),
        ),
        "failed": (None, None, failed_scan, False, (1, 0, 0, 1)),
        "failed_after_verified": (
            verified_scan,
            _result(verified_scan.id, 80),
            failed_scan,
            False,
            (1, 1, 0, 1),
        ),
    }
    scan, result, latest_attempt, stale, expected = states[kind]
    return (
        CurrentDocumentState(
            identity=identity,
            source_kind="standalone_upload",
            scan=scan,
            result=result,
            latest_attempt=latest_attempt,
            stale=stale,
        ),
        expected,
    )


def test_coverage_reconciles_for_every_supported_state_combination():
    kinds = (
        "unscanned",
        "pending",
        "verified",
        "stale_verified",
        "failed",
        "failed_after_verified",
    )
    for combination in product(kinds, repeat=3):
        generated = [
            _coverage_state(kind, f"document-{index}")
            for index, kind in enumerate(combination)
        ]
        projection = CurrentComplianceProjection(
            historical_scans=(),
            current_documents=tuple(state for state, _expected in generated),
        )
        rollup = aggregate_institution_compliance(
            "Example University",
            [
                DepartmentCurrentCompliance(
                    department_id="dept-a",
                    department_name="A",
                    projection=projection,
                )
            ],
        )
        scanned, verified, stale, failed = map(
            sum, zip(*(expected for _state, expected in generated))
        )

        assert rollup.coverage.enrolled == 3
        assert rollup.coverage.scanned == scanned
        assert rollup.coverage.verified == verified
        assert rollup.coverage.stale == stale
        assert rollup.coverage.failed == failed
        assert rollup.coverage.total_coverage_percent == pytest.approx(
            verified / 3 * 100,
            abs=0.01,
        )


def test_department_totals_and_score_numerators_reconcile_to_institution():
    rollup = aggregate_institution_compliance(
        "Example University",
        [
            _department_rollup("dept-a", "A", [100]),
            _department_rollup("dept-b", "B", [0, 50]),
            _department_rollup("dept-empty", "Empty", []),
        ],
    )

    assert (
        sum(department.coverage.enrolled for department in rollup.departments)
        == rollup.coverage.enrolled
    )
    assert sum(
        department.score_numerator for department in rollup.departments
    ) == pytest.approx(150)
    assert rollup.document_weighted_score == 50
    assert rollup.flat_department_mean == 62.5


def test_institution_membership_uses_normalized_name_and_excludes_foreign_tenant(
    monkeypatch,
):
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Base.metadata.tables["departments"].to_metadata(metadata)
    department_table = metadata.tables["departments"]
    for constraint in list(department_table.constraints):
        if isinstance(constraint, CheckConstraint):
            department_table.constraints.remove(constraint)
    for column in department_table.columns:
        if column.server_default and "::" in str(column.server_default.arg):
            column.server_default = None
    metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                Department(
                    id="dept-a",
                    institution_scope_id="scope-example",
                    name="A",
                    institution=" Example University ",
                    contact_email="a@example.edu",
                ),
                Department(
                    id="dept-b",
                    institution_scope_id="scope-example",
                    name="B",
                    institution="example university",
                    contact_email="b@example.edu",
                ),
                Department(
                    id="dept-foreign",
                    institution_scope_id="scope-foreign",
                    name="Foreign",
                    institution="EXAMPLE UNIVERSITY",
                    contact_email="foreign@example.edu",
                ),
            ]
        )
        db.commit()

        projections = {
            department_id: _department_rollup(
                department_id, department_id, [score]
            ).projection
            for department_id, score in {
                "dept-a": 100,
                "dept-b": 50,
                "dept-foreign": 0,
            }.items()
        }
        monkeypatch.setattr(
            "src.education.institution_compliance.get_department_current_compliance",
            lambda _db, department_id: projections[department_id],
        )

        rollup = get_institution_current_compliance(db, "dept-a")

    assert [department.department_id for department in rollup.departments] == [
        "dept-a",
        "dept-b",
    ]
    assert rollup.document_weighted_score == 75


def test_institution_scope_is_required_and_not_derived_from_display_name():
    default = Department.__table__.c.institution_scope_id.default

    assert Department.__table__.c.institution_scope_id.nullable is False
    assert default is not None
    assert default.arg(None) != default.arg(None)


def test_admin_inherits_scope_only_for_their_own_institution():
    request = auth_routes.CreateDepartmentRequest(
        name="Library",
        institution=" example university ",
        contact_email="library@example.edu",
        contact_name="Library Admin",
    )
    current = SimpleNamespace(
        institution="Example University",
        institution_scope_id="scope-example",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = current

    scope_id = auth_routes._institution_scope_for_new_department(
        db,
        request,
        _principal(UserRole.ADMIN),
    )

    assert scope_id == "scope-example"
    db.query.assert_called_once_with(Department)


def test_admin_cannot_join_foreign_scope_by_reusing_its_display_name():
    request = auth_routes.CreateDepartmentRequest(
        name="Impostor",
        institution="Foreign University",
        contact_email="impostor@example.edu",
        contact_name="Impostor Admin",
    )
    current = SimpleNamespace(
        institution="Example University",
        institution_scope_id="scope-example",
    )
    foreign = SimpleNamespace(
        institution="Foreign University",
        institution_scope_id="scope-foreign",
    )
    current_query = MagicMock()
    current_query.filter.return_value.first.return_value = current
    foreign_query = MagicMock()
    foreign_query.filter.return_value.order_by.return_value.first.return_value = foreign
    db = MagicMock()
    db.query.side_effect = [current_query, foreign_query]

    scope_id = auth_routes._institution_scope_for_new_department(
        db,
        request,
        _principal(UserRole.ADMIN),
    )

    assert scope_id not in {"scope-example", "scope-foreign"}
    assert len(scope_id) == 36
    db.query.assert_called_once_with(Department)


@pytest.mark.parametrize(
    "principal",
    [
        _principal(UserRole.FACULTY),
        _principal(UserRole.FACULTY, auth_method="lti", account_wide=False),
    ],
)
def test_institution_endpoint_rejects_unauthorized_principal_before_database_work(
    principal,
):
    db = MagicMock()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(analytics.get_institution_compliance(db=db, principal=principal))

    assert exc.value.status_code == 403
    db.query.assert_not_called()


def test_institution_endpoint_returns_current_rollup_without_historical_series(
    monkeypatch,
):
    rollup = aggregate_institution_compliance(
        "Example University",
        [_department_rollup("dept-a", "A", [90])],
    )
    monkeypatch.setattr(
        analytics,
        "get_institution_current_compliance",
        lambda _db, _department_id: rollup,
    )

    response = asyncio.run(
        analytics.get_institution_compliance(
            db=MagicMock(),
            principal=_principal(UserRole.ADMIN),
        )
    )

    assert response["document_weighted_score"] == 90
    assert response["coverage"]["total_coverage_percent"] == 100
    assert "trend" not in response
    assert "history" not in response
