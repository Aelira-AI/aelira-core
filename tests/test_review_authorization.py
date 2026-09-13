"""Review aggregates and document routes retain authenticated course scope."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, DateTime, Float, MetaData, String, Table, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.api import review_routes as routes
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db.database import get_db_dependency
from src.db.models import CloudFile, Scan, UserRole
from src.education.reports.evidence_package import EvidenceFile

pytestmark = pytest.mark.unit


def principal(kind="course", course="course-a", platform="canvas"):
    if kind in {"session", "api_key"}:
        return AuthenticatedPrincipal(
            None, "reviewer", "dept-a", UserRole.FACULTY, kind
        )
    return AuthenticatedPrincipal(
        None,
        "reviewer",
        "dept-a",
        UserRole.ADMIN if kind == "admin" else UserRole.FACULTY,
        "lti",
        lti_course_id=None if kind == "admin" else course,
        lti_staff_role="Administrator" if kind == "admin" else "Instructor",
        lti_account_wide=kind == "admin",
        lti_platform=platform,
    )


def client_for(db, actor):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[get_db_dependency] = lambda: db
    app.dependency_overrides[get_authenticated_principal] = lambda: actor
    return TestClient(app)


@pytest.fixture
def aggregate_db():
    """Execute production SQL against deliberately distinct tenant/course data."""
    metadata = MetaData()
    scans = Table(
        "scans",
        metadata,
        Column("id", String, primary_key=True),
        Column("file_name", String),
        Column("department_id", String),
        Column("scan_type", String),
        Column("created_at", DateTime(timezone=True)),
    )
    fixes = Table(
        "scan_fixes",
        metadata,
        Column("id", String, primary_key=True),
        Column("scan_id", String),
        Column("review_status", String),
        Column("confidence", Float),
    )
    links = Table(
        "cloud_files",
        metadata,
        Column("id", String, primary_key=True),
        Column("last_scan_id", String),
        Column("department_id", String),
        Column("provider", String),
        Column("provider_parent_id", String),
    )
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    scan_rows, fix_rows, link_rows = [], [], []
    specs = [
        ("own-pending", "dept-a", "PDF", [("pending", 0.2), ("approved", 0.8)]),
        ("own-approved", "dept-a", "WORD", [("auto_approved", 0.6)]),
        ("own-rejected", "dept-a", "PDF", [("rejected", 0.4)]),
        ("other-course", "dept-a", "EXCEL", [("pending", 0.1)]),
        ("unlinked", "dept-a", "IMAGE", [("approved", 0.9)]),
        ("wrong-provider", "dept-a", "POWERPOINT", [("pending", 0.3)]),
        ("wrong-link-tenant", "dept-a", "PDF", [("pending", 0.7)]),
        ("other-tenant", "dept-b", "PDF", [("pending", 0.05)]),
    ]
    for scan_id, department, scan_type, statuses in specs:
        scan_rows.append(
            dict(
                id=scan_id,
                file_name=scan_id,
                department_id=department,
                scan_type=scan_type,
                created_at=datetime.now(timezone.utc),
            )
        )
        fix_rows.extend(
            dict(
                id=f"{scan_id}-{index}",
                scan_id=scan_id,
                review_status=status,
                confidence=confidence,
            )
            for index, (status, confidence) in enumerate(statuses)
        )
        if scan_id != "unlinked":
            link_rows.append(
                dict(
                    id=scan_id,
                    last_scan_id=scan_id,
                    department_id=(
                        "dept-b" if scan_id == "wrong-link-tenant" else department
                    ),
                    provider="google" if scan_id == "wrong-provider" else "canvas",
                    provider_parent_id=(
                        "course-b" if scan_id == "other-course" else "course-a"
                    ),
                )
            )
    # Multiple valid links must not inflate document or fix counts.
    link_rows.append(dict(link_rows[0], id="second-own-link"))
    with engine.begin() as connection:
        connection.execute(scans.insert(), scan_rows)
        connection.execute(fixes.insert(), fix_rows)
        connection.execute(links.insert(), link_rows)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_course_queue_filters_before_count_and_pagination(aggregate_db):
    client = client_for(aggregate_db, principal())
    first = client.get("/api/reviews/queue?limit=2").json()
    last = client.get("/api/reviews/queue?offset=2&limit=2").json()
    assert first["total"] == last["total"] == 3
    assert first["has_more"] is True and last["has_more"] is False
    assert [row["scan_id"] for row in first["items"]] == ["own-pending", "own-rejected"]
    assert [row["scan_id"] for row in last["items"]] == ["own-approved"]
    assert first["items"][0]["total_fixes"] == 2
    assert first["items"][0]["needs_review_count"] == 1


@pytest.mark.parametrize(
    "query, expected",
    [
        ("status=pending", ["own-pending"]),
        ("status=approved", ["own-approved"]),
        ("status=rejected", ["own-rejected"]),
        ("scan_type=word", ["own-approved"]),
        ("department_id=dept-a&scan_type=pdf", ["own-pending", "own-rejected"]),
    ],
)
def test_course_queue_preserves_status_type_and_explicit_department_filters(
    aggregate_db, query, expected
):
    response = client_for(aggregate_db, principal()).get("/api/reviews/queue?" + query)
    assert response.status_code == 200
    assert response.json()["total"] == len(expected)
    assert [row["scan_id"] for row in response.json()["items"]] == expected


def test_course_stats_and_summary_count_only_authorized_fixes(aggregate_db):
    client = client_for(aggregate_db, principal())
    assert client.get("/api/reviews/queue/stats").json() == {
        "pending": 1,
        "approved": 2,
        "rejected": 1,
        "total": 4,
        "by_type": {"pdf": 2, "word": 1},
    }
    assert client.get("/api/reviews/department-summary").json() == {
        "total_documents": 3,
        "reviewed_percent": 75.0,
        "approved_count": 2,
        "pending_count": 1,
        "rejected_count": 1,
        "avg_confidence": 0.5,
        "by_type": {"pdf": 2, "word": 1},
    }


@pytest.mark.parametrize(
    "kind,platform",
    [
        ("session", "canvas"),
        ("api_key", "canvas"),
        ("admin", "canvas"),
        ("admin", "blackboard"),
        ("admin", "brightspace"),
    ],
)
def test_department_wide_principals_keep_unlinked_and_other_course_access(
    aggregate_db, kind, platform
):
    client = client_for(aggregate_db, principal(kind, platform=platform))
    queue = client.get("/api/reviews/queue").json()
    stats = client.get("/api/reviews/queue/stats").json()
    summary = client.get("/api/reviews/department-summary").json()
    assert queue["total"] == summary["total_documents"] == 7
    assert "other-tenant" not in {item["scan_id"] for item in queue["items"]}
    assert stats["total"] == 8
    assert (stats["pending"], stats["approved"], stats["rejected"]) == (4, 3, 1)
    assert summary["reviewed_percent"] == 50.0
    assert summary["avg_confidence"] == 0.5
    assert sum(stats["by_type"].values()) == 7


@pytest.mark.parametrize("kind", ["course", "session", "admin"])
@pytest.mark.parametrize("endpoint", ["queue", "queue/stats"])
def test_department_query_cannot_change_authenticated_tenant(
    aggregate_db, kind, endpoint
):
    response = client_for(aggregate_db, principal(kind)).get(
        f"/api/reviews/{endpoint}?department_id=dept-b"
    )
    assert response.status_code == 404


def test_unknown_course_has_empty_aggregates(aggregate_db):
    client = client_for(aggregate_db, principal(course="empty-course"))
    assert client.get("/api/reviews/queue").json() == {
        "items": [],
        "total": 0,
        "has_more": False,
    }
    assert client.get("/api/reviews/queue/stats").json()["total"] == 0
    summary = client.get("/api/reviews/department-summary").json()
    assert (
        summary["total_documents"]
        == summary["avg_confidence"]
        == summary["reviewed_percent"]
        == 0
    )


@pytest.mark.parametrize("platform", ["blackboard", "brightspace"])
def test_non_canvas_course_id_collision_does_not_expose_canvas_aggregates(
    aggregate_db, platform
):
    client = client_for(aggregate_db, principal(platform=platform))
    queue = client.get("/api/reviews/queue")
    stats = client.get("/api/reviews/queue/stats")
    summary = client.get("/api/reviews/department-summary")
    assert queue.status_code == stats.status_code == summary.status_code == 200
    assert queue.json() == {"items": [], "total": 0, "has_more": False}
    assert stats.json() == {
        "pending": 0,
        "approved": 0,
        "rejected": 0,
        "total": 0,
        "by_type": None,
    }
    assert summary.json() == {
        "total_documents": 0,
        "reviewed_percent": 0.0,
        "approved_count": 0,
        "pending_count": 0,
        "rejected_count": 0,
        "avg_confidence": 0.0,
        "by_type": None,
    }


DOCUMENT_REQUESTS = [
    ("GET", "", None),
    ("GET", "/audit", None),
    ("GET", "/audit/export?format=json", None),
    ("GET", "/audit/export?format=csv", None),
    ("GET", "/audit/export?format=pdf", None),
    ("GET", "/audit/package?include_source=true&include_output=true", None),
    ("GET", "/reading-order", None),
    ("POST", "/fixes/fix", {"action": "approve"}),
    ("POST", "/batch", {"action": "approve"}),
    (
        "PUT",
        "/fixes/fix/deferral",
        {
            "owner": "Team",
            "reason": "Source review",
            "expires_at": "2099-01-01T00:00:00Z",
        },
    ),
    ("POST", "/fixes/fix/deferral/revoke", None),
]


@pytest.fixture
def document_state(monkeypatch):
    state = SimpleNamespace(
        scan=SimpleNamespace(
            id="scan",
            department_id="dept-a",
            file_name="source.pdf",
            current_remediation_artifact_id=None,
        ),
        link=SimpleNamespace(
            last_scan_id="scan",
            department_id="dept-a",
            provider="canvas",
            provider_parent_id="course-a",
        ),
        db=MagicMock(),
    )

    def query(model):
        result = MagicMock()
        result.filter.return_value = result
        result.order_by.return_value = result
        result.all.return_value = []
        result.first.return_value = (
            state.scan if model is Scan else state.link if model is CloudFile else None
        )
        return result

    state.db.query.side_effect = query
    fix = SimpleNamespace(
        id="fix",
        scan_id="scan",
        review_status="pending",
        confidence=0.8,
        deferral_status="active",
        deferral_owner="Team",
        deferral_reason="Source review",
        deferral_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        deferral_created_at=datetime.now(timezone.utc),
        deferral_updated_at=datetime.now(timezone.utc),
        deferral_closed_at=None,
    )
    values = {
        "lock_scan_review_graph": SimpleNamespace(fixes=[fix]),
        "validate_fix_review_action": None,
        "bind_fix_review_decision": None,
        "invalidate_current_artifact_approvals": None,
        "apply_authenticated_batch_review": None,
        "_audit_export_inputs": ([], [], [], None),
        "_read_verified_source": EvidenceFile(
            filename="source.txt", media_type="text/plain", content=b"source"
        ),
        "build_evidence_package": b"package",
    }
    state.effects = {}
    for name, value in values.items():
        mock = MagicMock(return_value=value)
        monkeypatch.setattr(routes, name, mock)
        state.effects[name] = mock
    for name, value in [
        ("generate_json", {}),
        ("generate_csv", "audit"),
        ("generate_pdf", b"%PDF-audit"),
    ]:
        mock = MagicMock(return_value=value)
        monkeypatch.setattr(routes.AuditReportGenerator, name, mock)
        state.effects[name] = mock
    state.artifact_service = MagicMock()
    monkeypatch.setattr(
        routes.RemediationArtifactService, "from_settings", state.artifact_service
    )
    return state


@pytest.mark.parametrize("method,suffix,body", DOCUMENT_REQUESTS)
@pytest.mark.parametrize(
    "scope",
    [
        "other_course",
        "missing_course",
        "missing_link",
        "wrong_provider",
        "wrong_link_tenant",
        "wrong_scan_tenant",
        "wrong_scan_link",
        "missing_scan",
        "blackboard",
        "brightspace",
    ],
)
def test_document_routes_deny_before_reads_mutations_or_exports(
    document_state, method, suffix, body, scope
):
    state = document_state
    if scope == "other_course":
        state.link.provider_parent_id = "course-b"
    elif scope == "missing_course":
        state.link.provider_parent_id = None
    elif scope == "missing_link":
        state.link = None
    elif scope == "wrong_provider":
        state.link.provider = "google"
    elif scope == "wrong_link_tenant":
        state.link.department_id = "dept-b"
    elif scope == "wrong_scan_tenant":
        state.scan.department_id = "dept-b"
    elif scope == "wrong_scan_link":
        state.link.last_scan_id = "another-scan"
    elif scope == "missing_scan":
        state.scan = None
    actor = (
        principal(platform=scope)
        if scope in {"blackboard", "brightspace"}
        else principal()
    )
    response = client_for(state.db, actor).request(
        method, "/api/reviews/scan" + suffix, json=body
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Scan not found"
    for effect in state.effects.values():
        effect.assert_not_called()
    state.artifact_service.assert_not_called()
    state.db.add.assert_not_called()
    state.db.commit.assert_not_called()
    assert all(
        call.args[0] in {Scan, CloudFile} for call in state.db.query.call_args_list
    )


@pytest.mark.parametrize("method,suffix,body", DOCUMENT_REQUESTS)
@pytest.mark.parametrize(
    "kind,platform",
    [
        ("course", "canvas"),
        ("session", "canvas"),
        ("admin", "canvas"),
        ("admin", "blackboard"),
        ("admin", "brightspace"),
    ],
)
def test_authorized_document_routes_reach_success(
    document_state, method, suffix, body, kind, platform
):
    state = document_state
    if kind != "course":
        state.link = None
    response = client_for(state.db, principal(kind, platform=platform)).request(
        method, "/api/reviews/scan" + suffix, json=body
    )
    assert response.status_code == 200, response.text
    if method != "GET":
        state.effects["lock_scan_review_graph"].assert_called_once()
        state.db.commit.assert_called_once()
        assert all(
            call.args[0].user_id == "reviewer" for call in state.db.add.call_args_list
        )
