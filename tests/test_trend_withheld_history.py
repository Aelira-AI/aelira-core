"""Withheld-history flags on trend reads after the v0.10.0 restart.

Trends and projections count only snapshots recorded with the
current_document_stock_v1 provenance. Snapshots recorded before v0.10.0 carry
no provenance and are withheld because their averages counted unassessed items
as 0%. These contracts pin that consumers can tell the two empty-chart cases
apart; they do not establish browser journeys or accessibility conformance.
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, MetaData, create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from src.api.education.compliance_routes import get_compliance_trend
from src.db.models import Base, ComplianceSnapshot, Department
from src.education.snapshot_service import (
    CURRENT_STOCK_PROVENANCE,
    SnapshotService,
)

DEPARTMENT = "department-with-legacy-history"


@pytest.fixture
def snapshot_db():
    """Real SQL semantics for the withheld count without PostgreSQL."""
    metadata = MetaData()
    for name in ("departments", "compliance_snapshots"):
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
        db.add(
            Department(
                id=DEPARTMENT,
                name="Legacy History",
                institution="Example",
                contact_email="chair@example.edu",
            )
        )
        db.commit()
        yield db
    engine.dispose()


def _add_snapshot(db, *, provenance, age_days=1):
    db.add(
        ComplianceSnapshot(
            id=str(uuid4()),
            department_id=DEPARTMENT,
            snapshot_date=datetime.utcnow() - timedelta(days=age_days),
            avg_compliance_score=100 if provenance is None else None,
            metric_provenance=provenance,
            total_scans=1,
            total_issues=0,
        )
    )


def test_count_withheld_counts_null_and_legacy_provenance_only(snapshot_db):
    _add_snapshot(snapshot_db, provenance=None, age_days=5)
    _add_snapshot(snapshot_db, provenance="legacy_video_scores_v0", age_days=4)
    _add_snapshot(snapshot_db, provenance=CURRENT_STOCK_PROVENANCE, age_days=3)
    # Outside the 30-day window: history, but not withheld from this read.
    _add_snapshot(snapshot_db, provenance=None, age_days=60)
    snapshot_db.commit()

    assert SnapshotService.count_withheld_snapshots(snapshot_db, DEPARTMENT, 30) == 2

    # The date-typed storage column loads as a date, not a datetime; the
    # trend read must survive that row shape instead of crashing the chart.
    points = SnapshotService.get_historical_trend(snapshot_db, DEPARTMENT, 30)
    assert [point.date for point in points] == [
        (datetime.utcnow() - timedelta(days=3)).date().isoformat()
    ]


def test_count_withheld_is_zero_without_any_snapshots(snapshot_db):
    assert SnapshotService.count_withheld_snapshots(snapshot_db, DEPARTMENT, 30) == 0


def _education_trend_body(monkeypatch, withheld):
    monkeypatch.setattr(
        "src.education.compliance_dashboard."
        "ComplianceDashboard.get_compliance_trend",
        lambda _db, _department, days=30: {
            "dates": [],
            "scores": [],
            "scans_per_day": [],
        },
    )
    monkeypatch.setattr(
        "src.education.snapshot_service." "SnapshotService.count_withheld_snapshots",
        lambda _db, _department, _days: withheld,
    )
    return asyncio.run(
        get_compliance_trend(
            department_id=DEPARTMENT,
            days=30,
            db=MagicMock(),
            api_key_info=(None, "chair-user", DEPARTMENT),
        )
    )


def test_education_trend_flags_withheld_history(monkeypatch):
    body = _education_trend_body(monkeypatch, withheld=4)
    assert body["has_withheld_history"] is True
    assert body["withheld_snapshot_count"] == 4
    assert body["trend"] == []


def test_education_trend_stays_quiet_without_withheld_history(monkeypatch):
    body = _education_trend_body(monkeypatch, withheld=0)
    assert body["has_withheld_history"] is False
    assert body["withheld_snapshot_count"] == 0
    assert body["trend"] == []
