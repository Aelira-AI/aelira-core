"""Authorization boundary tests for operational worker status."""

from datetime import datetime
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
    db.scalar.return_value = 0
    db.query.return_value.group_by.return_value = []
    db.query.return_value.filter.return_value.scalar.return_value = 0
    db.query.return_value.scalar.return_value = None
    return db


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_authenticated_principal, None)
    app.dependency_overrides.pop(get_db_dependency, None)


def test_worker_status_allows_only_super_admin():
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal(
        UserRole.SUPER_ADMIN
    )
    app.dependency_overrides[get_db_dependency] = _db
    response = TestClient(app).get("/api/jobs/worker-status")
    assert response.status_code == 200
    body = response.json()
    generated_at = datetime.fromisoformat(body["generated_at"])
    assert generated_at.tzinfo is not None
    assert body["progress"] == {
        "jobs_claimed": 0,
        "jobs_completed": 0,
        "jobs_failed": 0,
        "oldest_pending_created_at": None,
        "oldest_pending_age_seconds": None,
        "oldest_processing_heartbeat_at": None,
        "oldest_running_job_age_seconds": None,
        "runnable_pending": 0,
        "expired_processing": 0,
        "stalled_processing": 0,
        "latest_progress_at": None,
        "latest_progress_age_seconds": None,
    }
    assert body["weekly_summary_scheduler"] == {
        "state": "not_started",
        "last_success_at": None,
        "last_success_age_seconds": None,
        "last_error_code": None,
    }

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not {
        "department_id",
        "tenant_id",
        "scan_id",
        "cloud_file_id",
        "worker_id",
        "job_id",
        "provider",
        "credential_id",
        "file_name",
    } & keys(body)


def test_worker_status_declares_a_closed_bounded_response_contract():
    openapi = app.openapi()
    response_schema = openapi["paths"]["/api/jobs/worker-status"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/WorkerStatusResponse")

    schema = openapi["components"]["schemas"]["WorkerStatusResponse"]
    assert set(schema["required"]) == {
        "generated_at",
        "status",
        "health_state",
        "queue",
        "workers",
        "progress",
        "maintenance",
        "weekly_summary_scheduler",
        "reconciliation",
        "orphans",
    }
    assert schema["properties"]["status"]["enum"] == ["healthy", "degraded"]
    assert set(schema["properties"]["health_state"]["enum"]) == {
        "worker_unavailable",
        "expired_lease",
        "stuck_processing",
        "healthy_processing",
        "stuck_runnable_backlog",
        "healthy_advancing",
        "healthy_idle",
    }

    expected_nested_fields = {
        "WorkerQueueStatus": {"pending", "processing", "completed", "failed"},
        "WorkerLivenessStatus": {
            "live",
            "draining",
            "latest_heartbeat_at",
            "latest_heartbeat_age_seconds",
        },
        "WorkerProgressStatus": {
            "jobs_claimed",
            "jobs_completed",
            "jobs_failed",
            "oldest_pending_created_at",
            "oldest_pending_age_seconds",
            "oldest_processing_heartbeat_at",
            "oldest_running_job_age_seconds",
            "runnable_pending",
            "expired_processing",
            "stalled_processing",
            "latest_progress_at",
            "latest_progress_age_seconds",
        },
        "WorkerMaintenanceStatus": {"artifact_cleanup_due"},
        "WeeklySummarySchedulerStatus": {
            "state",
            "last_success_at",
            "last_success_age_seconds",
            "last_error_code",
        },
        "WorkerReconciliationStatus": {
            "required",
            "manual_required",
            "failed_manual",
        },
        "WorkerOrphanStatus": {
            "pending_move",
            "quarantined",
            "restore_required",
            "reviewed",
            "purging",
        },
    }
    for model_name, required_fields in expected_nested_fields.items():
        assert set(openapi["components"]["schemas"][model_name]["required"]) == (
            required_fields
        )

    queue_properties = openapi["components"]["schemas"]["WorkerQueueStatus"][
        "properties"
    ]
    assert all(field["minimum"] == 0 for field in queue_properties.values())


@pytest.mark.parametrize(
    "principal",
    [
        _principal(UserRole.ADMIN),
        _principal(
            UserRole.ADMIN,
            auth_method="lti",
            staff_role="Administrator",
            account_wide=True,
        ),
        _principal(UserRole.FACULTY),
        _principal(
            UserRole.FACULTY,
            auth_method="lti",
            course_id="course-1",
            staff_role="Instructor",
        ),
    ],
)
def test_worker_status_denies_all_non_global_operators(principal):
    app.dependency_overrides[get_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_db_dependency] = _db
    assert TestClient(app).get("/api/jobs/worker-status").status_code == 403


def test_worker_status_requires_authentication():
    response = TestClient(app).get("/api/jobs/worker-status")
    assert response.status_code == 401
    assert "queue" not in response.text
