"""Brightspace PDF promotion preserves descriptor-bound output ownership."""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.models import CloudProvider, ScanFix
from src.education.remediation.base import RemediationResult
from src.education.remediation.output_claim import DescriptorBoundOutputClaim
from src.services.remediation_artifact_service import ArtifactPublicationResult
from src.services.scan_fix_service import ScanReviewGraph
from tests.test_image_equation_review_gate import EVIDENCE, _fix as _typed_equation_fix

CLAIMED_PDF = b"%PDF-1.7\nbrightspace descriptor claim\n%%EOF\n"


class _PdfResult(SimpleNamespace):
    def __init__(
        self,
        output_path: Path,
        *,
        with_claim: bool = True,
        metadata_error: Exception | None = None,
        metadata_override: dict[str, object] | None = None,
        fail_stream_on: int | None = None,
    ) -> None:
        super().__init__(
            success=True,
            output_file=str(output_path),
            verification_passed=True,
            fixed_count=1,
            manual_count=0,
            failed_count=0,
        )
        self.claim = None
        self.close_calls = 0
        self.stream_calls = 0
        self.metadata_error = metadata_error
        self.metadata_override = metadata_override
        self.fail_stream_on = fail_stream_on
        if with_claim:
            descriptor = os.open(output_path, os.O_RDONLY)
            self.claim = DescriptorBoundOutputClaim._snapshot_from_owned_descriptor(
                descriptor,
                filename=output_path.name,
                display_path=str(output_path),
                mime="application/pdf",
            )

    def has_output_claim(self) -> bool:
        return self.claim is not None and not self.claim.closed

    def output_claim_metadata(self) -> dict[str, object]:
        if self.metadata_error is not None:
            raise self.metadata_error
        if not self.has_output_claim():
            raise RuntimeError("missing output claim")
        if self.metadata_override is not None:
            return self.metadata_override
        return {
            "size_bytes": self.claim.size,
            "sha256": self.claim.sha256,
            "mime_type": self.claim.mime,
            "filename": self.claim.filename,
        }

    @contextmanager
    def open_output_stream(self):
        self.stream_calls += 1
        if self.fail_stream_on == self.stream_calls:
            if self.claim is not None:
                self.claim.close()
            raise RuntimeError("output claim closed during stream handoff")
        if not self.has_output_claim():
            raise RuntimeError("missing output claim")
        with self.claim.open_stream() as stream:
            yield stream

    def close_output_claim(self) -> None:
        self.close_calls += 1
        if self.claim is not None:
            self.claim.close()


def _cloud_file() -> SimpleNamespace:
    return SimpleNamespace(
        id="cloud-pdf",
        department_id="dept-1",
        last_scan_id="scan-pdf",
        provider=CloudProvider.BRIGHTSPACE.value,
        provider_metadata={
            "org_unit_id": 42,
            "url": "/content/source.pdf",
            "topic_type": "file",
        },
        file_name="source.pdf",
        file_size_bytes=len(CLAIMED_PDF),
        provider_file_id="7",
        content_body=None,
        remediated_body=None,
        has_remediated_version=False,
        remediation_origin=None,
        remediated_issues_fixed=0,
        remediated_issues_remaining=0,
        writeback_status=None,
    )


def _db(*, commit_error: Exception | None = None) -> MagicMock:
    scan_result = SimpleNamespace(
        issues=[
            {
                "id": "pdf-tagged",
                "category": "structure",
                "severity": "high",
                "description": "PDF is untagged",
            }
        ]
    )
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = scan_result
    db.query.return_value = query
    if commit_error is not None:
        db.commit.side_effect = commit_error
    return db


def _artifact() -> SimpleNamespace:
    return SimpleNamespace(
        id="artifact-pdf",
        lifecycle_status="available",
        mime_type="application/pdf",
        size_bytes=len(CLAIMED_PDF),
        sha256=hashlib.sha256(CLAIMED_PDF).hexdigest(),
        expires_at=datetime.now(timezone.utc),
        review_status="pending",
    )


async def _run_pdf_outer(
    tmp_path: Path,
    result: _PdfResult,
    *,
    tamper: str | None = None,
    publication_error: BaseException | None = None,
    publication_hook=None,
    commit_error: Exception | None = None,
    matterhorn_result=None,
    matterhorn_error: Exception | None = None,
):
    from src.api.brightspace_routes import (
        _WorkerRemediationResult,
        _remediate_file_impl,
    )

    cloud_file = _cloud_file()
    db = _db(commit_error=commit_error)
    api_client = AsyncMock()
    api_client.get_topic_file.return_value = (b"%PDF-source", "application/pdf")
    service = MagicMock()
    published: list[bytes] = []

    def publish(_db_arg, **kwargs):
        published.append(kwargs["source_stream"].read())
        if publication_hook is not None:
            publication_hook()
        if publication_error is not None:
            raise publication_error
        return _artifact()

    service.claim_and_publish_stream.side_effect = publish
    service.claim_and_publish.side_effect = AssertionError(
        "Brightspace PDF publication must not use a pathname"
    )
    validated: list[bytes] = []
    validator = MagicMock()

    def validate(path):
        validation_path = Path(path)
        assert validation_path != Path(result.output_file)
        assert validation_path.is_file()
        validated.append(validation_path.read_bytes())
        if matterhorn_error is not None:
            raise matterhorn_error
        if matterhorn_result is not None:
            return matterhorn_result
        checkpoint = SimpleNamespace(
            id="01-003",
            name="Structure tree present",
            status=SimpleNamespace(value="pass"),
            severity="error",
            details=None,
            page_number=None,
        )
        return SimpleNamespace(
            checkpoints=[checkpoint], passed=1, failed=0, warnings=0, total=1
        )

    validator.validate.side_effect = validate

    async def run_worker(_department_id, worker, *args, **kwargs):
        assert worker.__name__ == "_run_remediator_worker"
        output = Path(result.output_file)
        if tamper == "replace":
            replacement = output.with_suffix(".replacement")
            replacement.write_bytes(b"%PDF-replaced-path")
            replacement.replace(output)
        elif tamper == "truncate":
            output.write_bytes(b"")
        elif tamper == "unlink":
            output.unlink()
        return _WorkerRemediationResult(result=result)

    with (
        patch(
            "src.api.brightspace_routes._run_brightspace_worker",
            new=run_worker,
        ),
        patch(
            "src.api.brightspace_routes.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.education.validation.matterhorn.MatterhornValidator",
            return_value=validator,
        ),
        patch(
            "src.services.scan_fix_service.lock_scan_review_graph",
            return_value=ScanReviewGraph(
                SimpleNamespace(id="scan-pdf", current_remediation_artifact_id=None),
                (),
                (),
                (),
            ),
        ),
    ):
        outcome = await _remediate_file_impl(
            cloud_file,
            db,
            api_client=api_client,
        )
    return SimpleNamespace(
        outcome=outcome,
        cloud_file=cloud_file,
        db=db,
        service=service,
        published=published,
        validated=validated,
    )


def _image_equation_result(output_path: Path) -> RemediationResult:
    output_path.write_bytes(CLAIMED_PDF)
    fixed = _typed_equation_fix(
        issue_id="image-equation-1",
        description="Image equation lacked an accessible formula",
        location="page 1 / image 0 / occurrence 0",
        fixed_content="Associated verified Formula, Alt, and MathML",
        provider_used="ollama",
        model_used="vision-test",
        page_number=1,
    )
    result = RemediationResult(
        original_file=str(output_path.with_name("source.pdf")),
        output_file=str(output_path),
        document_type="PDF",
        total_issues=1,
        fixed_count=1,
        manual_count=0,
        failed_count=0,
        fixed_issues=[fixed],
        verification_passed=True,
        success=True,
    )
    result.set_output_claim(
        DescriptorBoundOutputClaim._snapshot_from_owned_descriptor(
            os.open(output_path, os.O_RDONLY),
            filename=output_path.name,
            display_path=str(output_path),
            mime="application/pdf",
        )
    )
    return result


@pytest.mark.asyncio
async def test_brightspace_pdf_persists_real_image_equation_review_evidence(
    tmp_path,
):
    result = _image_equation_result(tmp_path / "fixed.pdf")

    run = await _run_pdf_outer(tmp_path, result)

    assert run.outcome.status == "completed"
    persisted = [
        call.args[0]
        for call in run.db.add.call_args_list
        if call.args and isinstance(call.args[0], ScanFix)
    ]
    assert len(persisted) == 1
    row = persisted[0]
    assert row.issue_id == "image-equation-1"
    assert row.source_kind == "image_equation"
    assert row.provider_used == "ollama"
    assert row.model_used == "vision-test"
    assert row.fix_method == "ai_vision"
    assert row.confidence == 0.55
    assert row.needs_review is True
    assert row.review_status == "pending"
    assert row.verification_evidence["threshold_version"] == "printed-equation-v1"
    assert row.verification_evidence["source_sha256"] == EVIDENCE["source_sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("matterhorn_result", "matterhorn_error"),
    [
        (None, RuntimeError("validator unavailable")),
        (
            SimpleNamespace(checkpoints=[], total=0, passed=0, failed=0, warnings=0),
            None,
        ),
        (
            SimpleNamespace(
                checkpoints=[SimpleNamespace(status=SimpleNamespace(value="fail"))],
                total=1,
                passed=0,
                failed=1,
                warnings=0,
            ),
            None,
        ),
        (
            SimpleNamespace(
                checkpoints=[SimpleNamespace(status=SimpleNamespace(value="pass"))],
                total=2,
                passed=1,
                failed=0,
                warnings=0,
            ),
            None,
        ),
    ],
    ids=["exception", "unavailable", "disqualifying", "integrity"],
)
async def test_brightspace_image_equation_matterhorn_failure_preserves_prior_state(
    tmp_path, matterhorn_result, matterhorn_error
):
    result = _image_equation_result(tmp_path / "fixed.pdf")

    run = await _run_pdf_outer(
        tmp_path,
        result,
        matterhorn_result=matterhorn_result,
        matterhorn_error=matterhorn_error,
    )

    assert run.outcome.status == "failed"
    assert run.outcome.error_code == "artifact_unavailable"
    assert run.published == []
    assert run.cloud_file.has_remediated_version is False
    assert run.cloud_file.remediation_origin is None
    assert run.cloud_file.remediated_issues_fixed == 0
    assert run.cloud_file.remediated_issues_remaining == 0
    assert result.has_output_claim() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["replace", "truncate", "unlink"])
async def test_brightspace_pdf_publishes_and_validates_exact_claim_after_path_tamper(
    tmp_path, tamper
):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output)

    run = await _run_pdf_outer(tmp_path, result, tamper=tamper)

    assert run.outcome.status == "completed"
    assert run.outcome.artifact_sha256 == hashlib.sha256(CLAIMED_PDF).hexdigest()
    assert run.published == [CLAIMED_PDF]
    assert run.validated == [CLAIMED_PDF]
    run.service.claim_and_publish.assert_not_called()
    assert result.close_calls == 1
    assert result.has_output_claim() is False
    serialized = run.outcome.model_dump()
    assert all("path" not in key and "fd" not in key for key in serialized)


def test_brightspace_pdf_worker_never_reopens_claimed_output_path(monkeypatch):
    from src.api.brightspace_routes import _run_remediator_worker

    state: dict[str, object] = {"remediation_returned": False}
    real_open = builtins.open
    real_isfile = os.path.isfile

    class FakePdfRemediator:
        def __init__(self, file_path, *_args, **_kwargs):
            self.file_path = Path(file_path)

        def remediate(self):
            output = self.file_path.with_name("fixed.pdf")
            output.write_bytes(CLAIMED_PDF)
            result = _PdfResult(output)
            state["output"] = output
            state["result"] = result
            state["remediation_returned"] = True
            return result

    def guarded_open(file, *args, **kwargs):
        if state["remediation_returned"] and Path(file) == state["output"]:
            raise AssertionError("PDF output pathname was reopened")
        return real_open(file, *args, **kwargs)

    def guarded_isfile(path):
        if state["remediation_returned"] and Path(path) == state["output"]:
            raise AssertionError("PDF output pathname was restatted")
        return real_isfile(path)

    module = SimpleNamespace(PdfRemediator=FakePdfRemediator)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os.path, "isfile", guarded_isfile)
    monkeypatch.setattr(
        "src.api.brightspace_routes.importlib.import_module",
        lambda *_args, **_kwargs: module,
    )

    worker_result = _run_remediator_worker(
        ext="pdf",
        raw_issues=[{"category": "structure"}],
        config=SimpleNamespace(),
        remediation_client=None,
        source_bytes=b"%PDF-source",
    )
    result = worker_result.result
    try:
        assert worker_result.remediated_bytes is None
        assert result.has_output_claim() is True
        with result.open_output_stream() as stream:
            assert stream.read() == CLAIMED_PDF
        assert Path(result.output_file).exists() is False
    finally:
        result.close_output_claim()


def test_brightspace_non_pdf_worker_keeps_remediated_bytes(monkeypatch):
    from src.api.brightspace_routes import _run_remediator_worker

    office_bytes = b"PK\x03\x04remediated-docx"

    class FakeDocxRemediator:
        def __init__(self, file_path, *_args, **_kwargs):
            self.file_path = Path(file_path)

        def remediate(self):
            output = self.file_path.with_name("fixed.docx")
            output.write_bytes(office_bytes)
            return SimpleNamespace(
                success=True,
                output_file=str(output),
                verification_passed=True,
                fixed_count=1,
                manual_count=0,
                failed_count=0,
            )

    monkeypatch.setattr(
        "src.api.brightspace_routes.importlib.import_module",
        lambda *_args, **_kwargs: SimpleNamespace(DocxRemediator=FakeDocxRemediator),
    )

    worker_result = _run_remediator_worker(
        ext="docx",
        raw_issues=[{"category": "heading"}],
        config=SimpleNamespace(),
        remediation_client=None,
        source_bytes=b"PK\x03\x04source-docx",
    )

    assert worker_result.remediated_bytes == office_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_state", ["missing", "closed"])
async def test_brightspace_pdf_missing_or_closed_claim_is_artifact_unavailable(
    tmp_path, claim_state
):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output, with_claim=claim_state == "closed")
    if result.claim is not None:
        result.claim.close()

    run = await _run_pdf_outer(tmp_path, result)

    assert run.outcome.status == "failed"
    assert run.outcome.error_code == "artifact_unavailable"
    assert run.outcome.has_remediated_version is False
    run.service.claim_and_publish_stream.assert_not_called()
    assert run.validated == []
    assert result.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result_kwargs",
    [
        {"metadata_error": RuntimeError("metadata race")},
        {
            "metadata_override": {
                "size_bytes": len(CLAIMED_PDF),
                "sha256": "not-a-digest",
                "mime_type": "application/pdf",
                "filename": "fixed.pdf",
            }
        },
        {"fail_stream_on": 2},
    ],
)
async def test_brightspace_pdf_metadata_and_stream_races_fail_closed(
    tmp_path, result_kwargs
):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output, **result_kwargs)

    run = await _run_pdf_outer(tmp_path, result)

    assert run.outcome.status == "failed"
    assert run.outcome.error_code == "artifact_unavailable"
    assert run.outcome.has_remediated_version is False
    assert result.close_calls == 1
    assert result.has_output_claim() is False


@pytest.mark.asyncio
async def test_brightspace_pdf_publication_failure_closes_claim_and_fails_closed(
    tmp_path,
):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output)

    run = await _run_pdf_outer(
        tmp_path,
        result,
        publication_error=OSError("artifact store unavailable"),
    )

    assert run.outcome.status == "failed"
    assert run.outcome.error_code == "artifact_unavailable"
    assert run.outcome.has_remediated_version is False
    assert result.close_calls == 1
    assert result.has_output_claim() is False


@pytest.mark.asyncio
async def test_brightspace_pdf_db_failure_closes_claim(tmp_path):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output)

    run = await _run_pdf_outer(
        tmp_path,
        result,
        commit_error=RuntimeError("database unavailable"),
    )

    assert run.outcome.status == "failed"
    assert run.outcome.error_code == "remediation_failed"
    assert result.close_calls == 1
    assert result.has_output_claim() is False


@pytest.mark.asyncio
async def test_brightspace_worker_cancellation_waits_then_closes_returned_claim(
    tmp_path,
):
    from src.api.brightspace_routes import (
        _WorkerRemediationResult,
        _run_brightspace_worker,
    )

    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output)
    started = threading.Event()
    release = threading.Event()

    def worker():
        started.set()
        assert release.wait(timeout=5)
        return _WorkerRemediationResult(result=result)

    task = asyncio.create_task(_run_brightspace_worker("dept-cancel", worker))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    assert result.has_output_claim() is True
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert result.close_calls == 1
    assert result.has_output_claim() is False


@pytest.mark.asyncio
async def test_brightspace_pdf_publish_cancellation_closes_claim(tmp_path):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output)

    with pytest.raises(asyncio.CancelledError):
        await _run_pdf_outer(
            tmp_path,
            result,
            publication_error=asyncio.CancelledError(),
        )

    assert result.close_calls == 1
    assert result.has_output_claim() is False


@pytest.mark.asyncio
async def test_brightspace_pdf_real_cancellation_during_publish_is_observed(tmp_path):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output)
    publish_started = threading.Event()
    publish_release = threading.Event()
    loop = asyncio.get_running_loop()
    task = None

    def block_publish():
        publish_started.set()
        assert publish_release.wait(timeout=5)

    def cancel_during_publish():
        assert publish_started.wait(timeout=5)
        assert task is not None
        loop.call_soon_threadsafe(task.cancel)
        publish_release.set()

    controller = threading.Thread(target=cancel_during_publish)
    controller.start()
    task = asyncio.create_task(
        _run_pdf_outer(
            tmp_path,
            result,
            publication_hook=block_publish,
        )
    )
    with pytest.raises(asyncio.CancelledError):
        await task
    controller.join(timeout=5)

    assert controller.is_alive() is False
    assert result.close_calls == 1
    assert result.has_output_claim() is False


@pytest.mark.asyncio
async def test_brightspace_pdf_cancellation_after_sync_publish_aborts_exact_claim(
    tmp_path,
):
    from src.api.brightspace_routes import _finish_brightspace_pdf_remediation

    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_PDF)
    result = _PdfResult(output)
    cloud_file = _cloud_file()
    db = _db()
    artifact = _artifact()
    publication = ArtifactPublicationResult(
        artifact=artifact,
        artifact_id=str(artifact.id),
        publication_token="-".join(("brightspace", "publication", "fixture")),
    )
    service = MagicMock()
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()

    def publish(*args, **kwargs):
        cloud_file.has_remediated_version = True
        cloud_file.remediation_origin = "manual"
        loop.call_soon(task.cancel)
        return publication

    service.claim_and_publish_stream.side_effect = publish
    with (
        patch(
            "src.api.brightspace_routes.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.api.brightspace_routes._validate_brightspace_pdf_claim",
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await _finish_brightspace_pdf_remediation(
                cloud_file,
                db,
                result=result,
                complete=True,
                decisions={"remediation": "used", "alt_text": "not_requested"},
                alt_text_client=None,
            )

    service.abort_staging.assert_called_once_with(
        db,
        artifact_id=publication.artifact_id,
        publication_token=publication.publication_token,
    )
    db.rollback.assert_called_once_with()
    assert cloud_file.has_remediated_version is False
    assert cloud_file.remediation_origin is None
    assert cloud_file.writeback_status is None
    assert result.close_calls == 1
    assert result.has_output_claim() is False
