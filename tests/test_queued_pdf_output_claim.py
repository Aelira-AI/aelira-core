"""Queued PDF publication consumes descriptor-claimed bytes only."""

import asyncio
import hashlib
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.models import (
    CloudFile,
    CloudProvider,
    MatterhornResult,
    RemediationArtifact,
    RemediationOutcome,
    Scan,
    ScanFix,
    ScanResult,
    ScanStatus,
    ScanType,
)
from src.education.remediation.base import RemediationResult
from src.education.remediation.output_claim import DescriptorBoundOutputClaim
from src.services.remediation_artifact_service import ArtifactPublicationResult
from tests.test_image_equation_matterhorn_gate import (
    GATE_ACCEPTED,
    GATE_REFUSED,
    publisher_params,
)
from tests.test_image_equation_review_gate import EVIDENCE, _fix as _typed_equation_fix


class _Query:
    def __init__(self, value):
        self.value = value

    def filter(self, *args):
        return self

    def options(self, *args):
        return self

    def order_by(self, *args):
        return self

    def populate_existing(self):
        return self

    def first(self):
        return self.value

    def one_or_none(self):
        return self.value

    def all(self):
        return [] if self.value is None else [self.value]

    def with_for_update(self):
        return self

    def scalar(self):
        return self.value

    def delete(self):
        return 0


class _DB:
    def __init__(self, scan, scan_result, cloud_file):
        self.values = {
            Scan: scan,
            ScanResult: scan_result,
            ScanFix: None,
            RemediationArtifact: None,
            CloudFile: cloud_file,
        }
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        if model is Scan.id:
            return _Query(self.values[Scan].id)
        return _Query(self.values.get(model))

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _snapshot_claim(path: Path) -> DescriptorBoundOutputClaim:
    descriptor = os.open(path, os.O_RDONLY)
    return DescriptorBoundOutputClaim._snapshot_from_owned_descriptor(
        descriptor,
        filename=path.name,
        display_path=str(path),
        mime="application/pdf",
    )


def _result(input_path: Path, output_path: Path, payload: bytes) -> RemediationResult:
    output_path.write_bytes(payload)
    result = RemediationResult(
        original_file=str(input_path),
        output_file=str(output_path),
        document_type="PDF",
        total_issues=1,
        fixed_count=1,
        manual_count=0,
        failed_count=0,
        skipped_count=0,
        fixed_issues=[],
        improvement=1.0,
        verification_passed=True,
        success=True,
    )
    result.set_output_claim(_snapshot_claim(output_path))
    return result


def _context(tmp_path: Path):
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "fixed.pdf"
    input_path.write_bytes(b"%PDF-input")
    scan = Scan(
        id="scan-claim",
        department_id="dept-1",
        scan_type=ScanType.PDF,
        storage_path=str(input_path),
        metadata={},
        status=ScanStatus.PROCESSING,
        file_name=input_path.name,
    )
    cloud_file = SimpleNamespace(
        id="cloud-1",
        provider=CloudProvider.CANVAS.value,
        current_remediation_artifact_id=None,
        has_remediated_version=False,
        remediation_origin=None,
        remediated_issues_fixed=None,
        remediated_issues_remaining=None,
        writeback_status=None,
    )
    db = _DB(scan, SimpleNamespace(issues=[{"category": "heading"}]), cloud_file)
    job_data = {
        "job_id": "job-1",
        "scan_id": scan.id,
        "cloud_file_id": cloud_file.id,
        "department_id": scan.department_id,
        "file_path": str(input_path),
    }
    return input_path, output_path, scan, cloud_file, db, job_data


def _artifact(payload: bytes):
    return SimpleNamespace(
        id="artifact-1",
        mime_type="application/pdf",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        expires_at=SimpleNamespace(isoformat=lambda: "2099-01-01T00:00:00Z"),
        review_status="pending",
    )


@pytest.mark.parametrize("path_mutation", ["replace", "truncate", "unlink"])
async def test_queued_pdf_publishes_and_validates_exact_claim_bytes(
    tmp_path, path_mutation
):
    from src.jobs.remediation_job import process_remediation_job

    payload = b"%PDF-exact-claimed-output"
    _, output_path, scan, cloud_file, db, job_data = _context(tmp_path)
    remediation_result = _result(Path(job_data["file_path"]), output_path, payload)
    remediation_result.fixed_issues = [
        _typed_equation_fix(
            issue_id="queued-issue-1",
            description="Associated queued image equation",
            location="page 1 / image 0 / occurrence 0",
            fixed_content="Formula, Alt, and MathML",
            provider_used="ollama",
            model_used="vision-test",
            page_number=1,
        )
    ]
    remediator = MagicMock()

    def remediate():
        if path_mutation == "replace":
            replacement = output_path.with_suffix(".replacement")
            replacement.write_bytes(b"%PDF-replaced-path-bytes")
            replacement.replace(output_path)
        elif path_mutation == "truncate":
            output_path.write_bytes(b"")
        else:
            output_path.unlink()
        return remediation_result

    remediator.remediate.side_effect = remediate
    artifact = _artifact(payload)
    service = MagicMock()
    published = []

    def publish(*args, **kwargs):
        stream = kwargs["source_stream"]
        published.append(stream.read())
        assert kwargs["claimed_size_bytes"] == len(payload)
        assert kwargs["claimed_sha256"] == hashlib.sha256(payload).hexdigest()
        assert kwargs["claimed_mime_type"] == "application/pdf"
        assert kwargs["claimed_filename"] == output_path.name
        cloud_file.current_remediation_artifact_id = artifact.id
        cloud_file.has_remediated_version = True
        return artifact

    service.claim_and_publish_stream.side_effect = publish
    matterhorn_bytes = []
    validator = MagicMock()

    def validate(path):
        validation_path = Path(path)
        assert validation_path != output_path
        assert validation_path.is_file()
        matterhorn_bytes.append(validation_path.read_bytes())
        checkpoint = SimpleNamespace(
            id="01-003",
            name="Structure tree present",
            status=SimpleNamespace(value="pass"),
            severity="error",
            details=None,
            page_number=None,
        )
        return SimpleNamespace(
            checkpoints=[checkpoint],
            total=1,
            passed=1,
            failed=0,
            warnings=0,
            compliance_level="compliant",
        )

    validator.validate.side_effect = validate
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
        patch(
            "src.education.validation.matterhorn.MatterhornValidator",
            return_value=validator,
        ),
    ):
        result = await process_remediation_job(job_data, db)

    assert result["success"] is True
    assert result["artifact_sha256"] == hashlib.sha256(payload).hexdigest()
    assert published == [payload]
    assert matterhorn_bytes == [payload]
    assert remediation_result.has_output_claim() is False
    assert "output_file" not in result
    assert "source_stream" not in result
    audit_details = [
        details
        for row in db.added
        if isinstance((details := getattr(row, "details", None)), dict)
    ]
    assert all("output_file" not in details for details in audit_details)
    assert all("source_stream" not in details for details in audit_details)
    assert scan.remediation_outcome == RemediationOutcome.COMPLETED.value
    persisted = [row for row in db.added if isinstance(row, ScanFix)]
    assert len(persisted) == 1
    assert persisted[0].issue_id == "queued-issue-1"
    assert persisted[0].source_kind == "image_equation"
    assert persisted[0].review_status == "pending"
    assert (
        persisted[0].verification_evidence["source_sha256"] == EVIDENCE["source_sha256"]
    )
    assert len(persisted[0].occurrence_key) == 64


@pytest.mark.parametrize("closed", [False, True])
async def test_queued_pdf_missing_or_closed_claim_fails_closed(tmp_path, closed):
    from src.jobs.remediation_job import process_remediation_job

    payload = b"%PDF-output"
    _, output_path, scan, _, db, job_data = _context(tmp_path)
    remediation_result = _result(Path(job_data["file_path"]), output_path, payload)
    if closed:
        remediation_result.close_output_claim()
    else:
        claim = remediation_result.take_output_claim()
        assert claim is not None
        claim.close()
    remediator = MagicMock()
    remediator.remediate.return_value = remediation_result
    service = MagicMock()
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
    ):
        result = await process_remediation_job(job_data, db)

    assert result["success"] is False
    assert result["error"] == "remediation_artifact_unavailable"
    assert scan.remediation_outcome == RemediationOutcome.ARTIFACT_UNAVAILABLE.value
    assert remediation_result.has_output_claim() is False
    service.claim_and_publish_stream.assert_not_called()


async def test_queued_pdf_closes_claim_when_publication_raises(tmp_path):
    from src.jobs.remediation_job import process_remediation_job

    payload = b"%PDF-output"
    _, output_path, _, _, db, job_data = _context(tmp_path)
    remediation_result = _result(Path(job_data["file_path"]), output_path, payload)
    remediator = MagicMock()
    remediator.remediate.return_value = remediation_result
    service = MagicMock()
    service.claim_and_publish_stream.side_effect = OSError("storage unavailable")
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
    ):
        result = await process_remediation_job(job_data, db)

    assert result["success"] is False
    assert result["error"] == "remediation_failed"
    assert remediation_result.has_output_claim() is False


async def test_queued_pdf_cancellation_waits_for_worker_then_closes_claim(tmp_path):
    from src.jobs.remediation_job import process_remediation_job

    payload = b"%PDF-output"
    _, output_path, _, _, db, job_data = _context(tmp_path)
    remediation_result = _result(Path(job_data["file_path"]), output_path, payload)
    started = threading.Event()
    release = threading.Event()
    remediator = MagicMock()

    def remediate():
        started.set()
        assert release.wait(timeout=5)
        return remediation_result

    remediator.remediate.side_effect = remediate
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
    ):
        task = asyncio.create_task(process_remediation_job(job_data, db))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert task.done() is False
        assert remediation_result.has_output_claim() is True
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert remediation_result.has_output_claim() is False


@pytest.mark.parametrize("acquisition_stage", ["metadata", "stream"])
async def test_queued_pdf_claim_acquisition_race_fails_closed(
    tmp_path, acquisition_stage
):
    from src.jobs.remediation_job import process_remediation_job

    class RacingResult(RemediationResult):
        def output_claim_metadata(self):
            if acquisition_stage == "metadata":
                self.close_output_claim()
            return super().output_claim_metadata()

        def open_output_stream(self):
            if acquisition_stage == "stream":
                self.close_output_claim()
            return super().open_output_stream()

    payload = b"%PDF-racing-output"
    _, output_path, scan, _, db, job_data = _context(tmp_path)
    output_path.write_bytes(payload)
    remediation_result = RacingResult(
        original_file=job_data["file_path"],
        output_file=str(output_path),
        document_type="PDF",
        total_issues=1,
        fixed_count=1,
        fixed_issues=[],
        improvement=1.0,
        verification_passed=True,
        success=True,
    )
    remediation_result.set_output_claim(_snapshot_claim(output_path))
    remediator = MagicMock(remediate=MagicMock(return_value=remediation_result))
    service = MagicMock()
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
    ):
        result = await process_remediation_job(job_data, db)

    assert result["error"] == "remediation_artifact_unavailable"
    assert scan.remediation_outcome == RemediationOutcome.ARTIFACT_UNAVAILABLE.value
    assert remediation_result.has_output_claim() is False
    service.claim_and_publish_stream.assert_not_called()


async def test_queued_pdf_unrelated_metadata_runtime_error_is_not_claim_loss(tmp_path):
    from src.jobs.remediation_job import process_remediation_job

    class BrokenMetadataResult(RemediationResult):
        def output_claim_metadata(self):
            raise RuntimeError("metadata implementation bug")

    payload = b"%PDF-broken-metadata"
    _, output_path, _, _, db, job_data = _context(tmp_path)
    output_path.write_bytes(payload)
    remediation_result = BrokenMetadataResult(
        original_file=job_data["file_path"],
        output_file=str(output_path),
        document_type="PDF",
        total_issues=1,
        fixed_count=1,
        fixed_issues=[],
        improvement=1.0,
        verification_passed=True,
        success=True,
    )
    remediation_result.set_output_claim(_snapshot_claim(output_path))
    remediator = MagicMock(remediate=MagicMock(return_value=remediation_result))
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
    ):
        result = await process_remediation_job(job_data, db)

    assert result["error"] == "remediation_failed"
    assert remediation_result.has_output_claim() is False


async def test_non_pdf_cancellation_retains_ordinary_to_thread_behavior(tmp_path):
    from src.jobs.remediation_job import process_remediation_job

    _, _, scan, _, db, job_data = _context(tmp_path)
    scan.scan_type = ScanType.WORD
    started = threading.Event()
    release = threading.Event()
    remediator = MagicMock()

    def remediate():
        started.set()
        assert release.wait(timeout=5)
        return SimpleNamespace(success=False)

    remediator.remediate.side_effect = remediate
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
    ):
        task = asyncio.create_task(process_remediation_job(job_data, db))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.done() is True
        release.set()


async def test_queued_pdf_cancellation_waits_for_matterhorn_worker(tmp_path):
    from src.jobs.remediation_job import process_remediation_job

    payload = b"%PDF-matterhorn-claim"
    _, output_path, _, cloud_file, db, job_data = _context(tmp_path)
    remediation_result = _result(Path(job_data["file_path"]), output_path, payload)
    remediator = MagicMock(remediate=MagicMock(return_value=remediation_result))
    artifact = _artifact(payload)
    service = MagicMock()

    def publish(*args, **kwargs):
        cloud_file.current_remediation_artifact_id = artifact.id
        cloud_file.has_remediated_version = True
        return artifact

    service.claim_and_publish_stream.side_effect = publish
    started = threading.Event()
    release = threading.Event()
    verification_paths = []
    validator = MagicMock()

    def validate(path):
        verification_paths.append(Path(path))
        started.set()
        assert release.wait(timeout=5)
        assert Path(path).read_bytes() == payload
        return SimpleNamespace(
            checkpoints=[],
            total=0,
            passed=0,
            failed=0,
            warnings=0,
            compliance_level="unknown",
        )

    validator.validate.side_effect = validate
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
        patch(
            "src.education.validation.matterhorn.MatterhornValidator",
            return_value=validator,
        ),
    ):
        task = asyncio.create_task(process_remediation_job(job_data, db))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert task.done() is False
        assert remediation_result.has_output_claim() is True
        assert verification_paths[0].is_file()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert remediation_result.has_output_claim() is False
    assert verification_paths[0].exists() is False


async def test_queued_pdf_cancellation_after_publication_aborts_exact_claim(
    tmp_path,
):
    from src.jobs.remediation_job import process_remediation_job

    payload = b"%PDF-cancel-after-publication"
    _, output_path, scan, cloud_file, db, job_data = _context(tmp_path)
    remediation_result = _result(Path(job_data["file_path"]), output_path, payload)
    remediator = MagicMock(remediate=MagicMock(return_value=remediation_result))
    artifact = _artifact(payload)
    publication = ArtifactPublicationResult(
        artifact=artifact,
        artifact_id=str(artifact.id),
        publication_token="-".join(("queued", "publication", "fixture")),
    )
    service = MagicMock()

    def publish(*args, **kwargs):
        cloud_file.current_remediation_artifact_id = artifact.id
        cloud_file.has_remediated_version = True
        return publication

    service.claim_and_publish_stream.side_effect = publish
    matterhorn_started = threading.Event()
    matterhorn_release = threading.Event()
    validator = MagicMock()

    def validate(_path):
        matterhorn_started.set()
        assert matterhorn_release.wait(timeout=5)
        return SimpleNamespace(
            checkpoints=[],
            total=0,
            passed=0,
            failed=0,
            warnings=0,
            compliance_level="unknown",
        )

    validator.validate.side_effect = validate
    original_close = RemediationResult.close_output_claim

    def close_once(result):
        original_close(result)

    with (
        patch.object(
            RemediationResult,
            "close_output_claim",
            autospec=True,
            side_effect=close_once,
        ) as close_output_claim,
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
        patch(
            "src.education.validation.matterhorn.MatterhornValidator",
            return_value=validator,
        ),
    ):
        task = asyncio.create_task(process_remediation_job(job_data, db))
        assert await asyncio.to_thread(matterhorn_started.wait, 2)
        task.cancel()
        matterhorn_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    service.abort_staging.assert_called_once_with(
        db,
        artifact_id=publication.artifact_id,
        publication_token=publication.publication_token,
    )
    assert db.rollbacks >= 1
    assert scan.status == ScanStatus.FAILED
    assert scan.remediation_outcome == RemediationOutcome.ARTIFACT_UNAVAILABLE.value
    assert close_output_claim.call_count == 1
    assert remediation_result.has_output_claim() is False


async def _run_equation_gate_job(
    tmp_path, matterhorn, *, source_failures=None, abort_error=None
):
    """Publish one verified equation through the job, with a scripted validator."""
    from src.jobs.remediation_job import process_remediation_job

    payload = b"%PDF-queued-image-equation-gate"
    _, output_path, scan, cloud_file, db, job_data = _context(tmp_path)
    prior_state = dict(vars(cloud_file))
    remediation_result = _result(Path(job_data["file_path"]), output_path, payload)
    remediation_result.source_matterhorn_failures = source_failures
    remediation_result.fixed_issues = [
        _typed_equation_fix(
            issue_id="source-0",
            description="verified equation",
            fixed_content="Formula and MathML",
            provider_used="ollama",
            model_used="vision-test",
        )
    ]
    remediator = MagicMock(remediate=MagicMock(return_value=remediation_result))
    artifact = _artifact(payload)
    publication = ArtifactPublicationResult(
        artifact=artifact,
        artifact_id=str(artifact.id),
        publication_token="queued-matterhorn-token",
    )
    service = MagicMock()
    if abort_error is not None:
        service.abort_staging.side_effect = abort_error

    def publish(*_args, **_kwargs):
        cloud_file.current_remediation_artifact_id = artifact.id
        cloud_file.has_remediated_version = True
        return publication

    service.claim_and_publish_stream.side_effect = publish
    validator = MagicMock()
    if isinstance(matterhorn, BaseException):
        validator.validate.side_effect = matterhorn
    else:
        validator.validate.return_value = matterhorn

    result = error = None
    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=remediator,
        ),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
        patch(
            "src.education.validation.matterhorn.MatterhornValidator",
            return_value=validator,
        ),
    ):
        try:
            result = await process_remediation_job(job_data, db)
        except Exception as exc:  # the caller asserts the exact type
            error = exc
    return SimpleNamespace(
        result=result,
        error=error,
        service=service,
        publication=publication,
        scan=scan,
        db=db,
        cloud_file=cloud_file,
        prior_state=prior_state,
        remediation_result=remediation_result,
    )


@pytest.mark.parametrize(
    ("matterhorn", "source_failures"), publisher_params(GATE_ACCEPTED)
)
async def test_queued_image_equation_publishes_when_matterhorn_failures_pre_existed(
    tmp_path, matterhorn, source_failures
):
    run = await _run_equation_gate_job(
        tmp_path, matterhorn, source_failures=source_failures
    )

    assert run.error is None
    assert run.result["success"] is True
    assert run.result["artifact_id"] == "artifact-1"
    run.service.abort_staging.assert_not_called()
    assert run.scan.remediation_outcome == RemediationOutcome.COMPLETED.value
    # The validator's findings, including inherited failures, stay on record.
    recorded = [row for row in run.db.added if isinstance(row, MatterhornResult)]
    assert [row.checkpoint_id for row in recorded] == [
        item.id for item in matterhorn.checkpoints
    ]
    assert [row.status for row in recorded] == [
        item.status.value for item in matterhorn.checkpoints
    ]
    assert run.remediation_result.has_output_claim() is False


@pytest.mark.parametrize(
    ("matterhorn", "source_failures", "code"),
    [pytest.param(c.result, c.source_failures, c.code, id=c.id) for c in GATE_REFUSED],
)
async def test_queued_image_equation_gate_refusal_aborts_staging_and_is_final(
    tmp_path, matterhorn, source_failures, code
):
    from src.jobs.remediation_job import RemediationJobFailed, _commit_terminal_failure

    run = await _run_equation_gate_job(
        tmp_path, matterhorn, source_failures=source_failures
    )

    # A refusal returns a terminal failure instead of raising the retryable
    # error, so the queue does not rerun remediation for the same bytes.
    assert run.error is None
    assert run.result["success"] is False
    assert run.result["error"] == "remediation_artifact_unavailable"
    run.service.abort_staging.assert_called_once_with(
        run.db,
        artifact_id=run.publication.artifact_id,
        publication_token=run.publication.publication_token,
    )
    assert vars(run.cloud_file) == run.prior_state
    assert run.scan.status == ScanStatus.FAILED
    assert run.scan.remediation_outcome == RemediationOutcome.ARTIFACT_UNAVAILABLE.value
    assert not [row for row in run.db.added if isinstance(row, MatterhornResult)]
    assert run.remediation_result.has_output_claim() is False

    # The queue handler turns that result into a deterministic failure (a
    # RemediationJobFailed without a cause is never requeued) that credits no
    # fix.
    with pytest.raises(RemediationJobFailed) as terminal:
        await _commit_terminal_failure(
            SimpleNamespace(),
            run.db,
            run.result["error"],
            scan=run.scan,
            result=run.result,
        )
    assert terminal.value.code == "remediation_artifact_unavailable"
    assert terminal.value.__cause__ is None
    assert terminal.value.details["fixed_count"] == 0
    # The user-facing reason says why the file was withheld; only a failure
    # that remediation introduced is described as one retrying cannot fix.
    [outcome] = [
        item
        for item in terminal.value.details["issue_outcomes"]
        if item["status"] == "withheld"
    ]
    if code == "matterhorn_result_disqualifying":
        assert outcome["reason_code"] == "output_adds_pdfua_failures"
        assert "original is unchanged" in outcome["reason"]
        assert "is unlikely to change the result" in outcome["next_step"]
    else:
        assert outcome["reason_code"] == "output_not_published"


async def test_queued_image_equation_validator_exception_still_retries(tmp_path):
    from src.jobs.remediation_job import RetryableRemediationJobError

    run = await _run_equation_gate_job(
        tmp_path, RuntimeError("validator unavailable"), source_failures=None
    )

    # No gate verdict exists, so the transient validator failure keeps its
    # retryable handling after the staged artifact is aborted.
    assert isinstance(run.error, RetryableRemediationJobError)
    assert run.error.cleanup_complete is True
    run.service.abort_staging.assert_called_once_with(
        run.db,
        artifact_id=run.publication.artifact_id,
        publication_token=run.publication.publication_token,
    )
    assert vars(run.cloud_file) == run.prior_state
    assert run.scan.status == ScanStatus.PROCESSING
    assert run.scan.remediation_outcome is None
    assert run.remediation_result.has_output_claim() is False


async def test_queued_image_equation_refusal_with_failed_cleanup_keeps_cleanup_retry(
    tmp_path,
):
    from src.jobs.remediation_job import RetryableRemediationJobError

    refused = GATE_REFUSED[0]
    run = await _run_equation_gate_job(
        tmp_path,
        refused.result,
        source_failures=refused.source_failures,
        abort_error=RuntimeError("storage unavailable"),
    )

    # An orphaned staging claim must still be reaped, so only this case keeps
    # the retryable path, now carrying the pending-cleanup marker.
    assert isinstance(run.error, RetryableRemediationJobError)
    assert run.error.cleanup_complete is False
    assert run.error.artifact_id == run.publication.artifact_id
    assert vars(run.cloud_file) == run.prior_state
