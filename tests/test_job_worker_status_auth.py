"""Authorization boundary tests for operational worker status."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import UserRole


def _principal(
    role: UserRole,
    *,
    auth_method="session",
    course_id=None,
    staff_role=None,
    account_wide=False,
):
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=role,
        auth_method=auth_method,
        lti_course_id=course_id,
        lti_staff_role=staff_role,
        lti_account_wide=account_wide,
    )


def _db():
    db = MagicMock()
    db.query.return_value.group_by.return_value = []
    db.query.return_value.filter.return_value.scalar.return_value = 0
    db.query.return_value.scalar.return_value = None
    return db


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_authenticated_principal, None)
    app.dependency_overrides.pop(get_db_dependency, None)


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.SUPER_ADMIN])
def test_worker_status_allows_account_managers(role):
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal(role)
    app.dependency_overrides[get_db_dependency] = _db
    assert TestClient(app).get("/api/jobs/worker-status").status_code == 200


def test_worker_status_allows_account_wide_lti_administrator():
    principal = _principal(
        UserRole.ADMIN,
        auth_method="lti",
        staff_role="Administrator",
        account_wide=True,
    )
    app.dependency_overrides[get_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_db_dependency] = _db
    assert TestClient(app).get("/api/jobs/worker-status").status_code == 200


@pytest.mark.parametrize(
    "principal",
    [
        _principal(UserRole.FACULTY),
        _principal(
            UserRole.FACULTY,
            auth_method="lti",
            course_id="course-1",
            staff_role="Instructor",
        ),
    ],
)
def test_worker_status_denies_faculty_and_course_scoped_lti(principal):
    app.dependency_overrides[get_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_db_dependency] = _db
    assert TestClient(app).get("/api/jobs/worker-status").status_code == 403
