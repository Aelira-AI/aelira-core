"""Real PostgreSQL Task17B enqueue race coverage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from src.db.models import CloudJobQueue, Department
from src.services.job_enqueue_service import enqueue_cloud_job

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_enqueue_scope():
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception:
        engine.dispose()
        pytest.skip("PostgreSQL unavailable")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    department_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            Department(
                id=department_id,
                name="Task17B enqueue race",
                institution="Test",
                contact_email=f"{department_id}@example.test",
            )
        )
        db.commit()
    yield factory, department_id
    with factory() as db:
        db.execute(delete(Department).where(Department.id == department_id))
        db.commit()
    engine.dispose()


def test_concurrent_enqueue_unique_race_returns_the_single_winner(pg_enqueue_scope):
    factory, department_id = pg_enqueue_scope
    barrier = threading.Barrier(2)

    def enqueue() -> str:
        with factory() as db:
            barrier.wait(timeout=5)
            job = enqueue_cloud_job(
                db,
                department_id=department_id,
                job_type="scan",
                payload={"course_id": "course-1"},
                dedupe_key="task17b-race:course-1",
            )
            db.commit()
            return job.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _value: enqueue(), range(2)))

    assert len(set(ids)) == 1
    with factory() as db:
        rows = (
            db.query(CloudJobQueue)
            .filter(
                CloudJobQueue.department_id == department_id,
                CloudJobQueue.dedupe_key == "task17b-race:course-1",
            )
            .all()
        )
        assert [row.id for row in rows] == [ids[0]]
