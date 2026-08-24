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
    RemediationOutcome,
    Scan,
    ScanFix,
    ScanResult,
    ScanStatus,
    ScanType,
)
from src.education.remediation.base import (
    FixedIssue,
    IssueCategory,
    IssueSeverity,
    RemediationResult,
)
from src.education.remediation.output_claim import DescriptorBoundOutputClaim
from src.services.remediation_artifact_service import ArtifactPublicationResult


class _Query:
    def __init__(self, value):
        self.value = value

    def filter(self, *args):
        return self

    def first(self):
        return self.value

    def all(self):
        return [] if self.value is None else [self.value]

    def delete(self):
        return 0


class _DB:
    def __init__(self, scan, scan_result, cloud_file):
        self.values = {
            Scan: scan,
            ScanResult: scan_result,
            ScanFix: None,
            CloudFile: cloud_file,
        }
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
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
        FixedIssue(
            issue_id="queued-issue-1",
            category=IssueCategory.STRUCTURE,
            severity=IssueSeverity.HIGH,
            description="Fix queued structure",
            location="page 1",
            fixed_content="tagged",
            fix_method="rule",
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
        result = await process_remediation_job(job_data, db)

    assert result["success"] is True
    assert result["artifact_sha256"] == hashlib.sha256(payload).hexdigest()
    assert published == [payload]
    assert matterhorn_bytes == [payload]
    assert remediation_result.has_output_claim() is False
    assert "output_file" not in result
    assert "source_stream" not in result
    audit_details = [getattr(row, "details", {}) for row in db.added]
    assert all("output_file" not in details for details in audit_details)
    assert all("source_stream" not in details for details in audit_details)
    assert scan.remediation_outcome == RemediationOutcome.COMPLETED.value
    persisted = [row for row in db.added if isinstance(row, ScanFix)]
    assert len(persisted) == 1
    assert persisted[0].issue_id == "queued-issue-1"
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
        publication_token="queued-publication-token",
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
