"""An internal recovery plan cannot borrow a different actor or tenant scope."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.models import Scan
from src.jobs import remediation_job
from src.education.remediation.pdf_recovery_plan import ReviewedPDFRecovery
from src.education.remediation.pdf_reviewed_semantics import ReviewedSemanticManifest
from src.education.remediation.pdf_verified_font_recovery import FontRecoveryManifest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [None, "actor", "tenant", "type", "upload"])
async def test_reviewed_job_retains_fresh_scan_authority(monkeypatch, mismatch):
    plan = ReviewedPDFRecovery(
        FontRecoveryManifest("a" * 64, "test", "test", (), ()),
        ReviewedSemanticManifest("b" * 64, "test", "test", "Study", "en", (), ()),
    )
    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        user_id="user-1",
        scan_type="WORD" if mismatch == "type" else "PDF",
        storage_path="/uploads/source.pdf",
        status="failed",
        remediation_outcome=None,
        completed_at=None,
    )
    job = SimpleNamespace(
        id="job-1",
        department_id="other" if mismatch == "tenant" else "department-1",
        provider="local",
        cloud_file_id=None,
        credential_id=None,
        payload={
            "scan_id": "scan-1",
            "requested_by_id": "other" if mismatch == "actor" else "user-1",
            "upload_to_cloud": mismatch == "upload",
            "options": {"use_ai": False},
        },
        execution_context={},
        claim_token=None,
        worker_id=None,
    )
    db = MagicMock()
    db.get.side_effect = lambda model, identity, **kwargs: (
        scan if model is Scan else None
    )
    process = AsyncMock(return_value={"success": True, "scan_id": "scan-1"})
    monkeypatch.setattr(remediation_job, "process_remediation_job", process)
    if mismatch:
        with pytest.raises(remediation_job.RemediationJobFailed):
            await remediation_job.handle_remediation_job(
                job, db, object(), reviewed_pdf_recovery=plan
            )
        process.assert_not_called()
    else:
        result = await remediation_job.handle_remediation_job(
            job, db, object(), reviewed_pdf_recovery=plan
        )
        assert result["success"]
        assert process.await_args.kwargs["reviewed_pdf_recovery"] is plan
