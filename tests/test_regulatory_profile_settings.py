"""Institution-admin regulatory-profile management contracts."""

from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import AuditLog, AuditLogAction, Department, UserRole

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "2026_08_29_regulatory_profile_revision.py"


def _principal(
    role=UserRole.ADMIN,
    *,
    auth_method="session",
    department_id="dept-1",
    **overrides,
):
    values = {
        "api_key": MagicMock() if auth_method == "api_key" else None,
        "user_id": "user-1",
        "department_id": department_id,
        "user_role": role,
        "auth_method": auth_method,
    }
    values.update(overrides)
    return AuthenticatedPrincipal(**values)


def _department(**overrides):
    values = {
        "id": "dept-1",
        "country_code": None,
        "regulatory_framework": None,
        "title_ii_entity_class": None,
        "custom_deadline": None,
        "custom_deadline_verified_at": None,
        "regulatory_profile_revision": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _db(department):
    db = MagicMock()
    query = db.query.return_value.filter.return_value
    query.first.return_value = department
    query.with_for_update.return_value.first.return_value = department
    return db


def _valid_payload(**overrides):
    payload = {
        "country_code": "US",
        "regulatory_framework": "US_ADA_TITLE_II",
        "title_ii_entity_class": "large",
        "custom_deadline": None,
        "custom_deadline_verified": False,
        "expected_revision": 0,
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_authenticated_principal, None)
    app.dependency_overrides.pop(get_db_dependency, None)


@pytest.mark.parametrize(
    ("role", "auth_method", "extra"),
    [
        (UserRole.FACULTY, "session", {}),
        (
            UserRole.ADMIN,
            "lti",
            {
                "lti_staff_role": "Administrator",
                "lti_account_wide": True,
                "lti_platform": "canvas",
            },
        ),
        (
            UserRole.FACULTY,
            "lti",
            {
                "lti_course_id": "course-1",
                "lti_staff_role": "Instructor",
                "lti_account_wide": False,
                "lti_platform": "blackboard",
            },
        ),
        (UserRole.ADMIN, "mock", {}),
    ],
)
def test_get_and_put_reject_disallowed_principals_before_lookup(
    role, auth_method, extra
):
    client = TestClient(app)
    for method in ("get", "put"):
        db = _db(_department())
        app.dependency_overrides[get_authenticated_principal] = lambda: _principal(
            role, auth_method=auth_method, **extra
        )
        app.dependency_overrides[get_db_dependency] = lambda: db
        kwargs = (
            {"json": _valid_payload(), "headers": {"Origin": "http://testserver"}}
            if method == "put"
            else {}
        )

        response = getattr(client, method)("/admin/regulatory-profile", **kwargs)

        assert response.status_code == 403
        db.query.assert_not_called()


def test_anonymous_principal_is_rejected_before_lookup():
    client = TestClient(app)
    db = _db(_department())

    def anonymous():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[get_authenticated_principal] = anonymous
    app.dependency_overrides[get_db_dependency] = lambda: db

    assert client.get("/admin/regulatory-profile").status_code == 401
    db.query.assert_not_called()


@pytest.mark.parametrize(
    ("role", "auth_method"),
    [
        (UserRole.ADMIN, "session"),
        (UserRole.ADMIN, "api_key"),
        (UserRole.SUPER_ADMIN, "session"),
        (UserRole.SUPER_ADMIN, "api_key"),
    ],
)
def test_normal_admins_read_only_their_principal_tenant(role, auth_method):
    client = TestClient(app)
    department = _department(country_code="AU", regulatory_framework="AU_DDA")
    db = _db(department)
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal(
        role, auth_method=auth_method
    )
    app.dependency_overrides[get_db_dependency] = lambda: db

    response = client.get("/admin/regulatory-profile")

    assert response.status_code == 200
    assert response.json()["profile_revision"] == 0
    assert response.json()["configuration_complete"] is True
    assert response.json()["deadline"]["applicability"] == "ongoing_no_date"
    tenant_filter = db.query.return_value.filter.call_args.args[0]
    assert tenant_filter.left.name == "id"
    assert tenant_filter.right.value == "dept-1"


def test_missing_principal_department_is_not_replaced_by_another_tenant():
    client = TestClient(app)
    db = _db(None)
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal(
        department_id="missing-dept"
    )
    app.dependency_overrides[get_db_dependency] = lambda: db

    assert client.get("/admin/regulatory-profile").status_code == 404


@pytest.mark.parametrize(
    "injected", [{"department_id": "dept-2"}, {"tenant_id": "dept-2"}]
)
def test_put_forbids_cross_tenant_and_extra_field_injection(injected):
    client = TestClient(app)
    db = _db(_department())
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal()
    app.dependency_overrides[get_db_dependency] = lambda: db

    response = client.put(
        "/admin/regulatory-profile",
        headers={"Origin": "http://testserver"},
        json={**_valid_payload(), **injected},
    )

    assert response.status_code == 422
    db.query.assert_not_called()


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"regulatory_framework": "US_SECTION_508"}, "regulatory_framework"),
        (
            {
                "country_code": "ZZ",
                "regulatory_framework": "NONE",
                "title_ii_entity_class": None,
            },
            "country_code",
        ),
        ({"title_ii_entity_class": None}, "title_ii_entity_class"),
        (
            {
                "country_code": "DE",
                "regulatory_framework": "EU_EAA",
                "title_ii_entity_class": "large",
            },
            "title_ii_entity_class",
        ),
        (
            {
                "country_code": "CA",
                "regulatory_framework": None,
                "title_ii_entity_class": None,
            },
            "regulatory_framework",
        ),
        (
            {
                "regulatory_framework": "NONE",
                "title_ii_entity_class": None,
                "custom_deadline": "2027-06-01",
                "custom_deadline_verified": True,
            },
            "custom_deadline",
        ),
        (
            {"custom_deadline": "2027-06-01", "custom_deadline_verified": False},
            "custom_deadline_verified",
        ),
        (
            {"custom_deadline": None, "custom_deadline_verified": True},
            "custom_deadline_verified",
        ),
        (
            {
                "custom_deadline": "2027-06-01T00:00:00Z",
                "custom_deadline_verified": True,
            },
            "custom_deadline",
        ),
        ({"custom_deadline_verified": "true"}, "custom_deadline_verified"),
    ],
)
def test_invalid_combinations_return_actionable_422_without_mutation(overrides, field):
    client = TestClient(app)
    department = _department()
    before = vars(department).copy()
    db = _db(department)
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal()
    app.dependency_overrides[get_db_dependency] = lambda: db

    response = client.put(
        "/admin/regulatory-profile",
        headers={"Origin": "http://testserver"},
        json=_valid_payload(**overrides),
    )

    assert response.status_code == 422
    assert field in response.text
    assert vars(department) == before
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_all_null_reset_is_valid_and_fails_closed():
    client = TestClient(app)
    department = _department(
        country_code="US",
        regulatory_framework="US_ADA_TITLE_II",
        title_ii_entity_class="large",
        custom_deadline=datetime(2029, 1, 1, tzinfo=timezone.utc),
        custom_deadline_verified_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        regulatory_profile_revision=2,
    )
    db = _db(department)
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal()
    app.dependency_overrides[get_db_dependency] = lambda: db

    response = client.put(
        "/admin/regulatory-profile",
        headers={"Origin": "http://testserver"},
        json={
            "country_code": None,
            "regulatory_framework": None,
            "title_ii_entity_class": None,
            "custom_deadline": None,
            "custom_deadline_verified": False,
            "expected_revision": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["profile_revision"] == 3
    assert response.json()["configuration_complete"] is False
    assert response.json()["deadline"]["applicability"] == "configuration_required"
    assert department.custom_deadline is None
    assert department.custom_deadline_verified_at is None


def test_verified_custom_date_persists_at_utc_midnight_and_audits_allowlist():
    client = TestClient(app)
    department = _department()
    db = _db(department)
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal(
        auth_method="api_key"
    )
    app.dependency_overrides[get_db_dependency] = lambda: db

    response = client.put(
        "/admin/regulatory-profile",
        json=_valid_payload(
            custom_deadline="2027-06-01", custom_deadline_verified=True
        ),
    )

    assert response.status_code == 200
    assert response.json()["custom_deadline"] == "2027-06-01"
    assert response.json()["custom_deadline_verified"] is True
    assert department.custom_deadline == datetime(2027, 6, 1, tzinfo=timezone.utc)
    assert department.custom_deadline_verified_at.tzinfo is timezone.utc
    assert department.regulatory_profile_revision == 1
    audits = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], AuditLog)
    ]
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == AuditLogAction.REGULATORY_PROFILE_UPDATE.value
    assert set(audit.details) == {
        "old",
        "new",
        "old_revision",
        "new_revision",
        "schema_version",
        "outcome",
    }
    assert set(audit.details["new"]) == {
        "country_code",
        "regulatory_framework",
        "title_ii_entity_class",
        "custom_deadline",
        "custom_deadline_verified",
    }
    assert "contact" not in str(audit.details).lower()
    assert db.commit.call_count == 1


def test_historical_custom_date_is_not_fabricated_as_verified():
    client = TestClient(app)
    department = _department(
        country_code="DE",
        regulatory_framework="EU_EAA",
        custom_deadline=datetime(2027, 6, 1, tzinfo=timezone.utc),
        custom_deadline_verified_at=None,
    )
    db = _db(department)
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal()
    app.dependency_overrides[get_db_dependency] = lambda: db

    response = client.get("/admin/regulatory-profile")

    assert response.status_code == 200
    assert response.json()["custom_deadline"] == "2027-06-01"
    assert response.json()["custom_deadline_verified"] is False
    assert response.json()["deadline"]["deadline_date"] == "2025-06-28"


def test_legacy_unsupported_framework_is_readable_but_not_selectable():
    client = TestClient(app)
    db = _db(
        _department(
            country_code="DE",
            regulatory_framework="EU_WAD",
        )
    )
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal()
    app.dependency_overrides[get_db_dependency] = lambda: db

    response = client.get("/admin/regulatory-profile")

    assert response.status_code == 200
    body = response.json()
    assert body["regulatory_framework"] == "EU_WAD"
    assert body["configuration_complete"] is False
    assert body["deadline"]["applicability"] == "configuration_required"
    assert "EU_WAD" not in {
        framework["code"] for framework in body["supported_frameworks"]
    }


def test_stale_revision_returns_current_safe_state_without_mutation_or_audit():
    client = TestClient(app)
    department = _department(
        regulatory_profile_revision=3,
        country_code="AU",
        regulatory_framework="AU_DDA",
    )
    before = vars(department).copy()
    db = _db(department)
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal()
    app.dependency_overrides[get_db_dependency] = lambda: db

    response = client.put(
        "/admin/regulatory-profile",
        headers={"Origin": "http://testserver"},
        json=_valid_payload(expected_revision=2),
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "regulatory_profile_revision_conflict"
    assert detail["reason"] == "stale_revision"
    assert detail["current"]["profile_revision"] == 3
    assert vars(department) == before
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_commit_failure_rolls_back_and_returns_bounded_error():
    client = TestClient(app)
    db = _db(_department())
    db.commit.side_effect = RuntimeError("database internals must not leak")
    app.dependency_overrides[get_authenticated_principal] = lambda: _principal()
    app.dependency_overrides[get_db_dependency] = lambda: db

    response = client.put(
        "/admin/regulatory-profile",
        headers={"Origin": "http://testserver"},
        json=_valid_payload(),
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Regulatory profile update failed"
    assert "database internals" not in response.text
    db.rollback.assert_called_once()


def test_openapi_is_tenant_id_free_secret_free_and_documents_conflict():
    schema = app.openapi()
    operation = schema["paths"]["/admin/regulatory-profile"]["put"]
    request_schema = schema["components"]["schemas"]["RegulatoryProfileUpdate"]
    response_schema = schema["components"]["schemas"]["RegulatoryProfileResponse"]
    serialized = str({"request": request_schema, "response": response_schema}).lower()

    assert "expected_revision" in serialized
    assert "profile_revision" in serialized
    assert "configuration_complete" in serialized
    assert "department_id" not in serialized
    assert "tenant_id" not in serialized
    assert "api_key" not in serialized
    assert "409" in operation["responses"]
    assert "regulatory_profile_revision_conflict" in str(operation["responses"]["409"])


def test_model_revision_column_and_audit_action_contract():
    column = Department.__table__.c.regulatory_profile_revision
    assert column.nullable is False
    assert column.default.arg == 0
    assert str(column.server_default.arg) == "0"
    assert Department.__table__.c.custom_deadline_verified_at.nullable is True
    assert AuditLogAction.REGULATORY_PROFILE_UPDATE.value == "regulatory_profile_update"


def test_revision_migration_is_reversible_and_single_head(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "regulatory_profile_revision", MIGRATION
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE departments (id VARCHAR(36) PRIMARY KEY)")
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        migration.upgrade()
        columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("departments")
        }
        assert columns["regulatory_profile_revision"]["nullable"] is False
        assert columns["custom_deadline_verified_at"]["nullable"] is True
        connection.execute(sa.text("INSERT INTO departments (id) VALUES ('new')"))
        assert (
            connection.execute(
                sa.text("SELECT regulatory_profile_revision FROM departments")
            ).scalar_one()
            == 0
        )
        migration.downgrade()
        migration.downgrade()
        remaining = {
            column["name"]
            for column in sa.inspect(connection).get_columns("departments")
        }
        assert "regulatory_profile_revision" not in remaining
        assert "custom_deadline_verified_at" not in remaining

    engine.dispose()

    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == ["20260830_cvd_metrics"]
    assert migration.down_revision == "20260828_visual_contracts"


def test_provisioning_reuses_semantic_validation_and_utc_normalization():
    from pydantic import ValidationError
    from src.api.auth_routes import (
        CreateDepartmentRequest,
        _new_department_from_request,
    )

    common = {
        "name": "Accessibility",
        "institution": "Example University",
        "contact_email": "admin@example.edu",
        "contact_name": "Admin",
    }
    with pytest.raises(ValidationError):
        CreateDepartmentRequest(
            **common,
            country_code="US",
            regulatory_framework="US_ADA_TITLE_II",
            title_ii_entity_class=None,
        )

    request = CreateDepartmentRequest(
        **common,
        country_code="US",
        regulatory_framework="US_ADA_TITLE_II",
        title_ii_entity_class="large",
        custom_deadline=date(2027, 6, 1),
        custom_deadline_verified=True,
    )
    department = _new_department_from_request(request)
    assert department.custom_deadline == datetime(2027, 6, 1, tzinfo=timezone.utc)
    assert department.custom_deadline_verified_at.tzinfo is timezone.utc


def test_explicit_framework_can_override_country_without_legal_inference():
    from src.education.deadline_config import DeadlineService

    profile = DeadlineService.validate_regulatory_profile(
        country_code="AU",
        regulatory_framework="EU_EAA",
        title_ii_entity_class=None,
        custom_deadline=None,
        custom_deadline_verified=False,
    )

    assert profile.country_code == "AU"
    assert profile.regulatory_framework == "EU_EAA"
