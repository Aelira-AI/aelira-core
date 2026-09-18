"""Bounded review HTTP failures after proving each probe's success path."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from src.api import review_routes
from src.db.models import ReviewAuditLog, ScanFix
from src.education.reports.evidence_package import EvidencePackageError
from test_review_mutation_routes import review_http as review_http

pytestmark = pytest.mark.integration


def _deferral(case):
    return case.client.put(
        f"{case.url}/fixes/{case.fix_ids['high']}/deferral",
        json={
            "owner": "Document review team",
            "reason": "Source clarification pending",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )


def _probe(case, endpoint):
    if endpoint == "deferral":
        return _deferral(case)
    if endpoint == "revoke":
        return case.client.post(
            f"{case.url}/fixes/{case.fix_ids['high']}/deferral/revoke"
        )
    paths = {
        "queue": "/api/reviews/queue",
        "stats": "/api/reviews/queue/stats",
        "department": "/api/reviews/department-summary",
        "document": case.url,
        "audit": f"{case.url}/audit",
        "json": f"{case.url}/audit/export?format=json",
        "csv": f"{case.url}/audit/export?format=csv",
        "pdf": f"{case.url}/audit/export?format=pdf",
        "package": f"{case.url}/audit/package",
    }
    return case.client.get(paths[endpoint])


def _snapshot(case):
    with case.factory() as db:
        fixes = (
            db.query(ScanFix)
            .filter(ScanFix.scan_id == case.scan_id)
            .order_by(ScanFix.id)
            .all()
        )
        audits = (
            db.query(ReviewAuditLog.id)
            .filter(ReviewAuditLog.scan_id == case.scan_id)
            .order_by(ReviewAuditLog.id)
            .all()
        )
        return (
            [
                (
                    fix.id,
                    fix.review_status,
                    fix.deferral_status,
                    fix.deferral_updated_at,
                )
                for fix in fixes
            ],
            list(audits),
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "queue",
        "stats",
        "department",
        "document",
        "audit",
        "json",
        "csv",
        "pdf",
        "package",
        "deferral",
        "revoke",
    ],
)
def test_review_database_failure_is_unsuccessful_and_preserves_state(
    review_http, endpoint
):
    case = review_http
    # Exercise a real persisted audit and a real active deferral in the context
    # used by every probe, including the revoke endpoint.
    assert _deferral(case).status_code == 200
    success = _probe(case, endpoint)
    assert success.status_code == 200
    if endpoint == "revoke":
        assert _deferral(case).status_code == 200
    before = _snapshot(case)
    failed_queries = []

    def database_unavailable(execute_state):
        assert execute_state.is_select
        failed_queries.append(True)
        raise OperationalError(
            "synthetic SELECT", {}, RuntimeError("Synthetic database unavailable")
        )

    event.listen(case.factory, "do_orm_execute", database_unavailable)
    try:
        response = _probe(case, endpoint)
    finally:
        event.remove(case.factory, "do_orm_execute", database_unavailable)
    assert failed_queries == [True]
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert _snapshot(case) == before
    assert _probe(case, endpoint).status_code == 200


@pytest.mark.parametrize(
    "params",
    [
        {"status": "unknown"},
        {"offset": -1},
        {"limit": 0},
        {"limit": 101},
    ],
)
def test_review_queue_rejects_invalid_filter_and_pagination(review_http, params):
    case = review_http
    success = case.client.get("/api/reviews/queue")
    assert success.status_code == 200
    assert success.json()["total"] == 1
    response = case.client.get("/api/reviews/queue", params=params)
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(error["loc"] == ["query", next(iter(params))] for error in errors)


@pytest.mark.parametrize("format", ["json", "csv", "pdf"])
def test_audit_renderer_failure_does_not_return_a_download(
    review_http, monkeypatch, format
):
    case = review_http
    success = _probe(case, format)
    assert success.status_code == 200
    assert "attachment" in success.headers["content-disposition"]
    renderer = Mock(side_effect=RuntimeError("Synthetic renderer unavailable"))
    monkeypatch.setattr(
        review_routes.AuditReportGenerator, f"generate_{format}", renderer
    )
    response = _probe(case, format)
    renderer.assert_called_once()
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert "content-disposition" not in response.headers


def test_evidence_builder_failure_returns_bounded_conflict(review_http, monkeypatch):
    case = review_http
    success = _probe(case, "package")
    assert success.status_code == 200
    assert success.headers["content-type"] == "application/zip"
    builder = Mock(side_effect=EvidencePackageError("Synthetic builder unavailable"))
    monkeypatch.setattr(review_routes, "build_evidence_package", builder)
    response = _probe(case, "package")
    builder.assert_called_once()
    assert response.status_code == 409
    assert response.json() == {"detail": "Evidence package unavailable"}
    assert "content-disposition" not in response.headers
