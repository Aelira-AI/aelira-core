"""Integration tests for the review queue's public response contract."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import Column, DateTime, Float, MetaData, String, Table, create_engine
from sqlalchemy.orm import Session

from src.api.review_routes import get_review_queue, get_review_stats


@pytest.fixture
def review_queue_db():
    metadata = MetaData()
    scans = Table(
        "scans",
        metadata,
        Column("id", String, primary_key=True),
        Column("file_name", String, nullable=False),
        Column("department_id", String, nullable=False),
        Column("scan_type", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    fixes = Table(
        "scan_fixes",
        metadata,
        Column("id", String, primary_key=True),
        Column("scan_id", String, nullable=False),
        Column("review_status", String, nullable=False),
        Column("confidence", Float, nullable=False),
    )
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            scans.insert(),
            [
                {
                    "id": "scan-pending",
                    "file_name": "pending.pdf",
                    "department_id": "dept-one",
                    "scan_type": "PDF",
                    "created_at": now,
                },
                {
                    "id": "scan-approved",
                    "file_name": "approved.pdf",
                    "department_id": "dept-one",
                    "scan_type": "PDF",
                    "created_at": now,
                },
                {
                    "id": "scan-rejected",
                    "file_name": "rejected.pdf",
                    "department_id": "dept-one",
                    "scan_type": "PDF",
                    "created_at": now,
                },
                {
                    "id": "scan-legacy",
                    "file_name": "legacy.pdf",
                    "department_id": "dept-one",
                    "scan_type": "PDF",
                    "created_at": now,
                },
                {
                    "id": "scan-foreign",
                    "file_name": "foreign.pdf",
                    "department_id": "dept-two",
                    "scan_type": "PDF",
                    "created_at": now,
                },
            ],
        )
        connection.execute(
            fixes.insert(),
            [
                {
                    "id": "p1",
                    "scan_id": "scan-pending",
                    "review_status": "pending",
                    "confidence": 0.2,
                },
                {
                    "id": "p2",
                    "scan_id": "scan-pending",
                    "review_status": "approved",
                    "confidence": 0.8,
                },
                {
                    "id": "a1",
                    "scan_id": "scan-approved",
                    "review_status": "auto_approved",
                    "confidence": 0.7,
                },
                {
                    "id": "a2",
                    "scan_id": "scan-approved",
                    "review_status": "approved",
                    "confidence": 0.9,
                },
                {
                    "id": "r1",
                    "scan_id": "scan-rejected",
                    "review_status": "rejected",
                    "confidence": 0.6,
                },
                {
                    "id": "r2",
                    "scan_id": "scan-rejected",
                    "review_status": "approved",
                    "confidence": 0.95,
                },
                {
                    "id": "l1",
                    "scan_id": "scan-legacy",
                    "review_status": "in_review",
                    "confidence": 0.5,
                },
                {
                    "id": "f1",
                    "scan_id": "scan-foreign",
                    "review_status": "pending",
                    "confidence": 0.1,
                },
            ],
        )
    with Session(engine) as db:
        yield db
    engine.dispose()


def _queue(db: Session, *, status=None, offset=0, limit=20):
    return get_review_queue(
        department_id=None,
        status=status,
        scan_type=None,
        offset=offset,
        limit=limit,
        db=db,
        auth_result=("key-one", "user-one", "dept-one"),
    )


def test_queue_pagination_reports_filtered_total_and_boundary(review_queue_db):
    first_page = _queue(review_queue_db, limit=2)
    second_page = _queue(review_queue_db, offset=2, limit=2)

    assert first_page.total == 4
    assert first_page.has_more is True
    assert len(first_page.items) == 2
    assert second_page.total == 4
    assert second_page.has_more is False
    assert len(second_page.items) == 2


@pytest.mark.parametrize(
    ("status", "expected_ids"),
    [
        ("pending", {"scan-pending", "scan-legacy"}),
        ("approved", {"scan-approved"}),
        ("rejected", {"scan-rejected"}),
    ],
)
def test_queue_filters_and_labels_share_one_status_contract(
    review_queue_db, status, expected_ids
):
    response = _queue(review_queue_db, status=status)

    assert response.total == len(expected_ids)
    assert {item.scan_id for item in response.items} == expected_ids
    assert {item.status for item in response.items} == {status}


def test_queue_counts_only_unresolved_states_as_needing_review(review_queue_db):
    response = _queue(review_queue_db, status="pending")
    by_id = {item.scan_id: item for item in response.items}

    assert by_id["scan-pending"].needs_review_count == 1
    assert by_id["scan-legacy"].needs_review_count == 1


def test_queue_stats_use_the_same_status_vocabulary(review_queue_db):
    stats = get_review_stats(
        department_id=None,
        db=review_queue_db,
        auth_result=("key-one", "user-one", "dept-one"),
    )

    assert stats.model_dump() == {
        "pending": 2,
        "approved": 4,
        "rejected": 1,
        "total": 7,
        "by_type": {"pdf": 4},
    }
