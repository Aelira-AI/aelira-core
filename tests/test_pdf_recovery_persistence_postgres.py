"""Real recovery fixes fit the migrated PostgreSQL persistence contract."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import DataError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from src.database_url import engine_url
from src.db.models import Department, Scan, ScanFix, ScanStatus, ScanType
from src.education.pdf_processor import PDFProcessor
from src.jobs.remediation_subprocess import run_remediation_subprocess
from src.services.scan_fix_service import persist_scan_fixes
from test_remediation_subprocess_pdf_recovery import recovery as recovery

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_recovery_scan():
    # conftest fences DATABASE_URL to a disposable test target. The required
    # backend CI lane migrates it and includes integration tests; a missing DB
    # must fail that lane rather than silently lose this regression.
    engine = create_engine(engine_url(os.environ["DATABASE_URL"]))
    try:
        if engine.dialect.name != "postgresql":
            raise RuntimeError("recovery persistence requires PostgreSQL")
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except (SQLAlchemyError, RuntimeError):
        engine.dispose()
        if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("required recovery PostgreSQL database unavailable")
        pytest.skip("recovery persistence requires disposable PostgreSQL")

    sessions = sessionmaker(bind=engine, autoflush=False)
    department_id, scan_id = str(uuid4()), str(uuid4())
    try:
        with sessions() as db:
            db.add(
                Department(
                    id=department_id,
                    name="Synthetic recovery persistence",
                    institution="Test",
                    contact_email=f"{department_id}@example.test",
                )
            )
            db.flush()
            db.add(
                Scan(
                    id=scan_id,
                    department_id=department_id,
                    scan_type=ScanType.PDF,
                    file_name="synthetic.pdf",
                    status=ScanStatus.FAILED,
                    progress=35,
                    error_message="Previous attempt failed",
                )
            )
            db.commit()
        yield sessions, scan_id
    finally:
        with sessions() as db:
            db.execute(delete(Scan).where(Scan.id == scan_id))
            db.execute(delete(Department).where(Department.id == department_id))
            db.commit()
        engine.dispose()


@pytest.mark.asyncio
async def test_emitted_recovery_fixes_commit_without_approving_independent_review(
    pg_recovery_scan, recovery, tmp_path
):
    sessions, scan_id = pg_recovery_scan
    source, plan, _ = recovery
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(source)
    baseline = PDFProcessor(
        generate_alt_text=False, validate_alt_text=False, require_complete_scan=True
    ).process_pdf(str(path))
    execution = await run_remediation_subprocess(
        source_path=str(path),
        scan_type="PDF",
        issues=baseline.issues,
        options={"use_ai": False, "generate_alt_text": False},
        work_root=tmp_path / "work",
        reviewed_pdf_recovery=plan,
        timeout_seconds=30,
        termination_grace_seconds=5,
    )
    try:
        assert execution.success is True and execution.verification_passed is True
        assert execution.human_review_required is True
        assert execution.reviewed_pdf_recovery["independent_review_pending"] is True
        fixes = execution.fixed_issues
        assert fixes and len(fixes) == execution.fixed_count
        with sessions() as db:
            scan = db.get(Scan, scan_id)
            scan.status = ScanStatus.COMPLETED
            scan.progress = 100
            scan.error_message = None
            legacy = [
                SimpleNamespace(
                    **{**vars(fix), "fix_method": "reviewed_source_recovery"}
                )
                for fix in fixes
            ]
            persist_scan_fixes(db, scan_id, legacy)
            # Match production's autoflush=False: staging succeeds, while the
            # previous 24-character method rejects the final atomic commit.
            assert (
                db.scalar(select(ScanFix.id).where(ScanFix.scan_id == scan_id)) is None
            )
            with pytest.raises(DataError) as failure:
                db.commit()
            assert getattr(failure.value.orig, "pgcode", None) == "22001"
            db.rollback()
            assert db.get(Scan, scan_id).status == ScanStatus.FAILED
            assert db.get(Scan, scan_id).progress == 35
            assert db.get(Scan, scan_id).error_message == "Previous attempt failed"
            assert (
                db.scalar(select(ScanFix.id).where(ScanFix.scan_id == scan_id)) is None
            )

            persist_scan_fixes(db, scan_id, fixes)
            scan.status = ScanStatus.COMPLETED
            scan.progress = 100
            scan.error_message = None
            db.commit()

        with sessions() as db:
            saved = list(db.scalars(select(ScanFix).where(ScanFix.scan_id == scan_id)))
            assert len(saved) == len(fixes)
            assert {row.issue_id for row in saved} == {fix.issue_id for fix in fixes}
            assert all(row.fix_method == "reviewed_recovery" for row in saved)
            assert all(
                row.needs_review and row.review_status == "pending" for row in saved
            )
            assert all(
                row.approved_review_digest is None and row.reviewed_by is None
                for row in saved
            )
            assert db.get(Scan, scan_id).status == ScanStatus.COMPLETED
            assert db.get(Scan, scan_id).progress == 100
            assert db.get(Scan, scan_id).error_message is None
        assert path.read_bytes() == source
    finally:
        execution.close_output_claim()
