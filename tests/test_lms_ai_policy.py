"""Task 14 slice 1: explicit, fail-closed LMS AI policy contracts."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import AuditLog, AuditLogAction, Base, Department, User, UserRole


def _select_policy_test_database_url(environment):
    explicit_url = environment.get("TEST_DATABASE_URL")
    if explicit_url:
        return explicit_url

    running_in_ci = any(
        environment.get(name, "").lower() == "true" for name in ("CI", "GITHUB_ACTIONS")
    )
    if running_in_ci:
        return environment.get("DATABASE_URL") or None
    return None


POLICY_TEST_DATABASE_URL = _select_policy_test_database_url(os.environ)
POLICY_TEST_DATABASE_SKIP_REASON = (
    "Set TEST_DATABASE_URL explicitly, or DATABASE_URL with CI=true or "
    "GITHUB_ACTIONS=true, for PostgreSQL policy lock verification"
    if POLICY_TEST_DATABASE_URL is None
    else "PostgreSQL policy test database is configured"
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    app.dependency_overrides.pop(get_authenticated_principal, None)
    app.dependency_overrides.pop(get_db_dependency, None)
    yield
    app.dependency_overrides.pop(get_authenticated_principal, None)
    app.dependency_overrides.pop(get_db_dependency, None)


def principal(
    *,
    role=UserRole.FACULTY,
    auth_method="session",
    department_id="dept-1",
    lti_staff_role=None,
    lti_account_wide=False,
    lti_course_id=None,
):
    return AuthenticatedPrincipal(
        api_key=MagicMock() if auth_method == "api_key" else None,
        user_id="user-1",
        department_id=department_id,
        user_role=role,
        auth_method=auth_method,
        lti_staff_role=lti_staff_role,
        lti_account_wide=lti_account_wide,
        lti_course_id=lti_course_id,
    )


def department(**overrides):
    values = {
        "id": "dept-1",
        "lms_ai_enabled": False,
        "lms_ai_provider": None,
        "lms_ai_purposes": [],
        "byok_provider": None,
        "byok_api_key_encrypted": None,
        "pilot_gemini_approved": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def db_for(dept):
    db = MagicMock()
    filtered_query = db.query.return_value.filter.return_value
    filtered_query.first.return_value = dept
    filtered_query.with_for_update.return_value.first.return_value = dept
    return db


def override(client, *, actor, dept):
    db = db_for(dept)
    app.dependency_overrides[get_authenticated_principal] = lambda: actor
    app.dependency_overrides[get_db_dependency] = lambda: db
    return db


def test_department_policy_defaults_are_explicitly_disabled():
    enabled = Department.__table__.c.lms_ai_enabled
    provider = Department.__table__.c.lms_ai_provider
    purposes = Department.__table__.c.lms_ai_purposes

    assert enabled.nullable is False
    assert enabled.default.arg is False
    assert str(enabled.server_default.arg).lower() == "false"
    assert provider.nullable is True
    assert purposes.nullable is False
    assert purposes.default.arg(None) == []
    assert "ck_departments_lms_ai_provider" in {
        constraint.name for constraint in Department.__table__.constraints
    }
    assert "ck_departments_lms_ai_purposes" in {
        constraint.name for constraint in Department.__table__.constraints
    }


def test_department_policy_model_constraints_enforce_consistency_and_unique_purposes():
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Department.__table__.constraints
        if constraint.name
        in {
            "ck_departments_lms_ai_purposes",
            "ck_departments_lms_ai_policy_consistency",
        }
    }

    assert set(checks) == {
        "ck_departments_lms_ai_purposes",
        "ck_departments_lms_ai_policy_consistency",
    }
    purposes_check = checks["ck_departments_lms_ai_purposes"]
    assert "jsonb_typeof(lms_ai_purposes::jsonb) = 'array'" in purposes_check
    assert "jsonb_array_length(lms_ai_purposes::jsonb)" in purposes_check
    assert purposes_check.count("CASE WHEN") == 2
    assert "@> '[\"remediation\"]'::jsonb" in purposes_check
    assert "@> '[\"alt_text\"]'::jsonb" in purposes_check

    consistency_check = checks["ck_departments_lms_ai_policy_consistency"]
    assert "NOT lms_ai_enabled" in consistency_check
    assert "lms_ai_provider IS NULL" in consistency_check
    assert "lms_ai_purposes::jsonb = '[]'::jsonb" in consistency_check
    assert "lms_ai_enabled" in consistency_check
    assert "lms_ai_provider IS NOT NULL" in consistency_check
    assert "jsonb_array_length(lms_ai_purposes::jsonb) > 0" in consistency_check


def test_department_policy_constraints_reject_invalid_rows_in_postgresql():
    from src.config.settings import get_settings

    engine = create_engine(get_settings().database_url)
    try:
        connection = engine.connect()
    except Exception:
        pytest.skip("PostgreSQL test database unavailable")

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Department.__table__.constraints
        if constraint.name
        in {
            "ck_departments_lms_ai_provider",
            "ck_departments_lms_ai_purposes",
            "ck_departments_lms_ai_policy_consistency",
        }
    }
    with connection:
        connection.execute(
            text(
                "CREATE TEMP TABLE lms_ai_policy_constraint_test ("
                "lms_ai_enabled boolean NOT NULL, "
                "lms_ai_provider varchar(50), "
                "lms_ai_purposes jsonb NOT NULL, "
                + ", ".join(
                    f'CONSTRAINT "{name}" CHECK ({condition})'
                    for name, condition in checks.items()
                )
                + ")"
            )
        )

        invalid_rows = [
            {"enabled": False, "provider": "openai", "purposes": "[]"},
            {"enabled": False, "provider": None, "purposes": '["remediation"]'},
            {"enabled": True, "provider": None, "purposes": '["remediation"]'},
            {"enabled": True, "provider": "openai", "purposes": "[]"},
            {
                "enabled": True,
                "provider": "openai",
                "purposes": '["remediation", "remediation"]',
            },
        ]
        for row in invalid_rows:
            with connection.begin_nested():
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text(
                            "INSERT INTO lms_ai_policy_constraint_test VALUES "
                            "(:enabled, :provider, CAST(:purposes AS jsonb))"
                        ),
                        row,
                    )


def test_resolver_is_immutable_and_allows_only_consistent_policy():
    from src.ai.lms_policy import resolve_lms_ai_policy

    decision = resolve_lms_ai_policy(
        department(
            lms_ai_enabled=True,
            lms_ai_provider="ollama",
            lms_ai_purposes=["remediation", "alt_text"],
        ),
        "remediation",
    )

    assert asdict(decision) == {
        "enabled": True,
        "allowed": True,
        "provider": "ollama",
        "locality": "local",
        "purpose": "remediation",
        "reason": "allowed",
        "version": 1,
    }
    with pytest.raises(FrozenInstanceError):
        decision.allowed = False


def test_resolver_reports_enabled_policy_when_requested_purpose_is_denied():
    from src.ai.lms_policy import resolve_lms_ai_policy

    decision = resolve_lms_ai_policy(
        department(
            lms_ai_enabled=True,
            lms_ai_provider="openai",
            lms_ai_purposes=["alt_text"],
        ),
        "remediation",
    )

    assert asdict(decision) == {
        "enabled": True,
        "allowed": False,
        "provider": None,
        "locality": None,
        "purpose": "remediation",
        "reason": "purpose_not_enabled",
        "version": 1,
    }


@pytest.mark.parametrize("purpose", [None, ["remediation"], {"name": "remediation"}])
def test_resolver_denies_non_string_purpose_without_raising(purpose):
    from src.ai.lms_policy import resolve_lms_ai_policy

    decision = resolve_lms_ai_policy(
        department(
            lms_ai_enabled=True,
            lms_ai_provider="openai",
            lms_ai_purposes=["remediation"],
        ),
        purpose,
    )

    assert asdict(decision) == {
        "enabled": True,
        "allowed": False,
        "provider": None,
        "locality": None,
        "purpose": None,
        "reason": "invalid_purpose",
        "version": 1,
    }


@pytest.mark.parametrize(
    ("overrides", "purpose", "reason"),
    [
        ({}, "remediation", "disabled"),
        (
            {"byok_provider": "openai", "byok_api_key_encrypted": "secret"},
            "remediation",
            "disabled",
        ),
        ({"pilot_gemini_approved": True}, "remediation", "disabled"),
        (
            {
                "lms_ai_enabled": True,
                "lms_ai_provider": None,
                "lms_ai_purposes": ["remediation"],
            },
            "remediation",
            "invalid_policy",
        ),
        (
            {
                "lms_ai_enabled": True,
                "lms_ai_provider": "bogus",
                "lms_ai_purposes": ["remediation"],
            },
            "remediation",
            "invalid_policy",
        ),
        (
            {
                "lms_ai_enabled": True,
                "lms_ai_provider": "openai",
                "lms_ai_purposes": "remediation",
            },
            "remediation",
            "invalid_policy",
        ),
        (
            {
                "lms_ai_enabled": True,
                "lms_ai_provider": "openai",
                "lms_ai_purposes": ["other"],
            },
            "remediation",
            "invalid_policy",
        ),
        (
            {
                "lms_ai_enabled": True,
                "lms_ai_provider": ["openai"],
                "lms_ai_purposes": [{"purpose": "remediation"}],
            },
            "remediation",
            "invalid_policy",
        ),
        (
            {
                "lms_ai_enabled": True,
                "lms_ai_provider": "openai",
                "lms_ai_purposes": ["alt_text"],
            },
            "remediation",
            "purpose_not_enabled",
        ),
    ],
)
def test_resolver_fails_closed_for_disabled_or_malformed_policy(
    overrides, purpose, reason
):
    from src.ai.lms_policy import resolve_lms_ai_policy

    decision = resolve_lms_ai_policy(department(**overrides), purpose)

    assert decision.allowed is False
    assert decision.reason == reason
    assert decision.purpose == purpose


def test_resolver_does_not_initialize_or_contact_providers():
    from src.ai.lms_policy import resolve_lms_ai_policy

    with patch("src.ai.providers.get_provider_manager") as manager:
        resolve_lms_ai_policy(
            department(
                lms_ai_enabled=True,
                lms_ai_provider="gemini",
                lms_ai_purposes=["alt_text"],
            ),
            "alt_text",
        )
    manager.assert_not_called()


def test_policy_request_rejects_unknown_provider_purpose_and_extra_fields():
    from src.api.llm_providers import LMSAIPolicyUpdate

    for payload in (
        {"enabled": True, "provider": "other", "purposes": ["remediation"]},
        {"enabled": True, "provider": "openai", "purposes": ["other"]},
        {
            "enabled": True,
            "provider": "openai",
            "purposes": ["remediation"],
            "api_key": "secret",
        },
        {"enabled": True, "provider": None, "purposes": ["remediation"]},
    ):
        with pytest.raises(ValidationError):
            LMSAIPolicyUpdate.model_validate(payload)


@pytest.mark.parametrize(
    ("method", "request_kwargs"),
    [
        ("get", {}),
        (
            "put",
            {
                "json": {
                    "enabled": True,
                    "provider": "openai",
                    "purposes": ["remediation"],
                }
            },
        ),
    ],
)
def test_lms_policy_routes_reject_genuinely_unauthenticated_requests_before_behavior(
    client, method, request_kwargs
):
    assert get_authenticated_principal not in app.dependency_overrides
    assert get_db_dependency not in app.dependency_overrides

    with (
        patch("src.api.llm_providers._get_own_department") as get_department,
        patch("src.api.llm_providers.get_provider_manager") as provider_manager,
    ):
        response = getattr(client, method)("/llm/lms-policy", **request_kwargs)

    assert response.status_code == 401
    assert response.json()["detail"] == (
        "Authentication required. Provide 'Authorization: Bearer ***' header "
        "or login via dashboard."
    )
    get_department.assert_not_called()
    provider_manager.assert_not_called()


def test_get_policy_is_authenticated_department_scoped_and_secret_free(client):
    dept = department(
        lms_ai_enabled=True,
        lms_ai_provider="anthropic",
        lms_ai_purposes=["remediation"],
        byok_api_key_encrypted="must-not-leak",
    )
    db = override(client, actor=principal(), dept=dept)

    response = client.get("/llm/lms-policy", headers={"Authorization": "Bearer x"})

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "provider": "anthropic",
        "purposes": ["remediation"],
        "version": 1,
    }
    assert "must-not-leak" not in response.text
    queried_department_id = db.query.return_value.filter.call_args.args[0].right.value
    assert queried_department_id == "dept-1"
    db.query.return_value.filter.return_value.with_for_update.assert_not_called()


def test_put_locks_department_row_before_reading_and_mutating_policy(client):
    dept = department()
    db = MagicMock()
    filtered_query = db.query.return_value.filter.return_value
    filtered_query.with_for_update.return_value.first.return_value = dept
    app.dependency_overrides[get_authenticated_principal] = lambda: principal(
        role=UserRole.ADMIN
    )
    app.dependency_overrides[get_db_dependency] = lambda: db

    response = client.put(
        "/llm/lms-policy",
        headers={"Authorization": "Bearer x", "Origin": "http://testserver"},
        json={"enabled": True, "provider": "openai", "purposes": ["remediation"]},
    )

    assert response.status_code == 200
    filtered_query.with_for_update.assert_called_once_with()
    filtered_query.first.assert_not_called()
    filtered_query.with_for_update.return_value.first.assert_called_once_with()


@pytest.mark.parametrize(
    "actor",
    [
        principal(role=UserRole.FACULTY),
        principal(role=UserRole.FACULTY, auth_method="api_key"),
        principal(
            role=UserRole.FACULTY,
            auth_method="lti",
            lti_staff_role="Instructor",
            lti_course_id="course-1",
        ),
    ],
)
def test_put_denies_unprivileged_and_course_scoped_callers_before_mutation(
    client, actor
):
    dept = department()
    db = override(client, actor=actor, dept=dept)

    response = client.put(
        "/llm/lms-policy",
        headers={"Authorization": "Bearer x", "Origin": "http://testserver"},
        json={"enabled": True, "provider": "openai", "purposes": ["remediation"]},
    )

    assert response.status_code == 403
    assert dept.lms_ai_enabled is False
    db.query.assert_not_called()
    db.query.return_value.filter.return_value.with_for_update.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "actor",
    [
        principal(role=UserRole.ADMIN),
        principal(role=UserRole.SUPER_ADMIN),
        principal(
            role=UserRole.ADMIN,
            auth_method="lti",
            lti_staff_role="Administrator",
            lti_account_wide=True,
        ),
    ],
)
def test_put_allows_admins_and_writes_allowlisted_audit_transactionally(client, actor):
    dept = department()
    db = override(client, actor=actor, dept=dept)

    response = client.put(
        "/llm/lms-policy",
        headers={"Authorization": "Bearer x", "Origin": "http://testserver"},
        json={"enabled": True, "provider": "xai", "purposes": ["alt_text"]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "provider": "xai",
        "purposes": ["alt_text"],
        "version": 1,
    }
    assert db.commit.call_count == 1
    audit = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], AuditLog)
    )
    assert audit.action == AuditLogAction.LMS_AI_POLICY_UPDATE.value
    assert audit.department_id == "dept-1"
    assert audit.user_id == "user-1"
    assert audit.details == {
        "old": {"enabled": False, "provider": None, "purposes": []},
        "new": {"enabled": True, "provider": "xai", "purposes": ["alt_text"]},
        "version": 1,
    }
    serialized = str(audit.details).lower()
    assert not any(
        term in serialized
        for term in ("content", "prompt", "file", "key", "body", "secret")
    )


def test_put_rolls_back_policy_when_transactional_audit_commit_fails(client):
    dept = department()
    db = override(client, actor=principal(role=UserRole.ADMIN), dept=dept)
    db.commit.side_effect = RuntimeError("database unavailable")

    response = client.put(
        "/llm/lms-policy",
        headers={"Authorization": "Bearer x", "Origin": "http://testserver"},
        json={"enabled": True, "provider": "openai", "purposes": ["remediation"]},
    )

    assert response.status_code == 500
    db.rollback.assert_called_once_with()


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, None),
        ({"DATABASE_URL": "postgresql://ci-db"}, None),
        ({"CI": "true", "DATABASE_URL": "postgresql://ci-db"}, "postgresql://ci-db"),
        (
            {"GITHUB_ACTIONS": "true", "DATABASE_URL": "postgresql://gha-db"},
            "postgresql://gha-db",
        ),
        (
            {
                "TEST_DATABASE_URL": "postgresql://explicit-db",
                "CI": "true",
                "DATABASE_URL": "postgresql://ci-db",
            },
            "postgresql://explicit-db",
        ),
        ({"CI": "false", "DATABASE_URL": "postgresql://ci-db"}, None),
    ],
)
def test_policy_test_database_url_selection(environment, expected):
    assert _select_policy_test_database_url(environment) == expected


@pytest.mark.integration
@pytest.mark.skipif(
    POLICY_TEST_DATABASE_URL is None,
    reason=POLICY_TEST_DATABASE_SKIP_REASON,
)
def test_concurrent_policy_updates_serialize_audit_transitions_in_postgresql():
    """The second transaction waits and audits the first transaction's policy."""

    from src.api.llm_providers import LMSAIPolicyUpdate, update_lms_ai_policy

    assert POLICY_TEST_DATABASE_URL is not None
    engine = create_engine(POLICY_TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    suffix = uuid.uuid4().hex[:12]
    department_id = f"dept-{suffix}"
    user_id = f"user-{suffix}"
    actor = principal(role=UserRole.ADMIN, department_id=department_id)
    actor = AuthenticatedPrincipal(
        api_key=actor.api_key,
        user_id=user_id,
        department_id=actor.department_id,
        user_role=actor.user_role,
        auth_method=actor.auth_method,
        lti_staff_role=actor.lti_staff_role,
        lti_account_wide=actor.lti_account_wide,
        lti_course_id=actor.lti_course_id,
    )
    update_x = LMSAIPolicyUpdate(
        enabled=True, provider="openai", purposes=["remediation"]
    )
    update_y = LMSAIPolicyUpdate(enabled=True, provider="xai", purposes=["alt_text"])
    a_at_commit = Event()
    release_a = Event()
    b_started = Event()
    b_pid = []

    with session_factory() as setup_db:
        setup_db.add(
            Department(
                id=department_id,
                name="Policy Lock Test",
                institution="Test",
                contact_email=f"{suffix}@example.edu",
            )
        )
        setup_db.add(
            User(
                id=user_id,
                email=f"{suffix}@example.edu",
                name="Policy Lock Test",
                department_id=department_id,
                role=UserRole.ADMIN,
                is_active=True,
            )
        )
        setup_db.commit()

    def update_a():
        with session_factory() as db:
            real_commit = db.commit

            def commit_after_release():
                a_at_commit.set()
                if not release_a.wait(timeout=10):
                    raise TimeoutError(
                        "timed out coordinating first policy transaction"
                    )
                real_commit()

            db.commit = commit_after_release
            return update_lms_ai_policy(update_x, actor, db)

    def update_b():
        with session_factory() as db:
            b_pid.append(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            b_started.set()
            return update_lms_ai_policy(update_y, actor, db)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(update_a)
            assert a_at_commit.wait(timeout=10), "first update never reached commit"
            future_b = pool.submit(update_b)
            assert b_started.wait(timeout=10), "second update never started"

            observed_row_lock_wait = False
            deadline = time.monotonic() + 10
            with engine.connect() as monitor:
                while time.monotonic() < deadline:
                    wait_event_type = monitor.execute(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": b_pid[0]},
                    ).scalar_one_or_none()
                    if wait_event_type == "Lock":
                        observed_row_lock_wait = True
                        break
                    if future_b.done():
                        break
                    time.sleep(0.02)

            release_a.set()
            future_a.result(timeout=10)
            future_b.result(timeout=10)
            assert (
                observed_row_lock_wait
            ), "second update did not block on the first update's department row lock"

        with session_factory() as verify_db:
            stored = verify_db.query(Department).filter_by(id=department_id).one()
            assert {
                "enabled": stored.lms_ai_enabled,
                "provider": stored.lms_ai_provider,
                "purposes": stored.lms_ai_purposes,
            } == {"enabled": True, "provider": "xai", "purposes": ["alt_text"]}
            audits = (
                verify_db.query(AuditLog)
                .filter(
                    AuditLog.department_id == department_id,
                    AuditLog.action == AuditLogAction.LMS_AI_POLICY_UPDATE.value,
                )
                .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                .all()
            )
            assert [
                (audit.details["old"], audit.details["new"]) for audit in audits
            ] == [
                (
                    {"enabled": False, "provider": None, "purposes": []},
                    {
                        "enabled": True,
                        "provider": "openai",
                        "purposes": ["remediation"],
                    },
                ),
                (
                    {
                        "enabled": True,
                        "provider": "openai",
                        "purposes": ["remediation"],
                    },
                    {"enabled": True, "provider": "xai", "purposes": ["alt_text"]},
                ),
            ]
    finally:
        release_a.set()
        with session_factory() as cleanup_db:
            cleanup_db.query(AuditLog).filter_by(department_id=department_id).delete()
            cleanup_db.query(User).filter_by(id=user_id).delete()
            cleanup_db.query(Department).filter_by(id=department_id).delete()
            cleanup_db.commit()
        engine.dispose()
