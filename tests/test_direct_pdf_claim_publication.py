"""Direct PDF remediation consumes only descriptor-bound output claims."""

from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import CloudFile, Scan, ScanFix, ScanStatus, ScanType, UserRole
from src.education.remediation.output_claim import DescriptorBoundOutputClaim

CLAIMED_BYTES = b"%PDF-1.7\nexact descriptor-bound remediation\n%%EOF\n"


class _CloudFileQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *criteria):
        for criterion in criteria:
            key = criterion.left.key
            expected = criterion.right.value
            self.rows = [
                row
                for row in self.rows
                if (getattr(row, key, row) == expected)
            ]
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def all(self):
        return self.rows

    def with_for_update(self):
        return self

    def scalar(self):
        return self.rows[0] if self.rows else None

    def first(self):
        return self.rows[0] if self.rows else None

    def delete(self):
        self.rows.clear()
        return 0


class _RouteDB:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        if model is Scan.id:
            return _CloudFileQuery(["scan-1"])
        assert model in {CloudFile, ScanFix}
        return _CloudFileQuery([])

    def add(self, value):
        self.added.append(value)

    def flush(self):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _DirectPdfResult(SimpleNamespace):
    def __init__(self, output_path: Path, *, with_claim: bool = True):
        fixed = SimpleNamespace(
            issue_id="issue-1",
            category=SimpleNamespace(value="pdf_tagging"),
            severity=SimpleNamespace(value="high"),
            description="Fixed PDF structure",
            location=None,
            original_content=None,
            fixed_content=None,
            fix_method="mechanical",
            model_used=None,
            confidence=1.0,
            needs_review=False,
            wcag_criteria=None,
            page_number=None,
        )
        super().__init__(
            success=True,
            original_file=str(output_path.with_name("source.pdf")),
            output_file=str(output_path),
            verification_passed=True,
            total_issues=1,
            fixed_count=1,
            manual_count=0,
            failed_count=0,
            skipped_count=0,
            original_compliance_score=50.0,
            remediated_compliance_score=100.0,
            improvement=50.0,
            duration_seconds=0.01,
            fixed_issues=[fixed],
            manual_issues=[],
            warnings=[],
        )
        self.claim = None
        self.close_calls = 0
        if with_claim:
            descriptor = os.open(output_path, os.O_RDONLY)
            self.claim = DescriptorBoundOutputClaim._snapshot_from_owned_descriptor(
                descriptor,
                filename=output_path.name,
                display_path=str(output_path),
                mime="application/pdf",
            )

    def has_output_claim(self):
        return self.claim is not None and not self.claim.closed

    def open_output_stream(self):
        if not self.has_output_claim():
            raise RuntimeError("RemediationResult has no live output claim")
        return self.claim.open_stream()

    def output_claim_metadata(self):
        if not self.has_output_claim():
            raise RuntimeError("RemediationResult has no live output claim")
        return {
            "size_bytes": self.claim.size,
            "sha256": self.claim.sha256,
            "mime_type": self.claim.mime,
            "filename": self.claim.filename,
        }

    def close_output_claim(self):
        self.close_calls += 1
        if self.claim is not None:
            self.claim.close()


def _principal():
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method="session",
    )


def _scan(source: Path):
    return SimpleNamespace(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.PDF,
        storage_path=str(source),
        file_name=source.name,
        status=ScanStatus.PROCESSING,
        remediation_outcome=None,
        result=SimpleNamespace(issues=[{"description": "untagged PDF"}]),
    )


def _artifact():
    return SimpleNamespace(
        id="66666666-6666-4666-8666-666666666666",
        mime_type="application/pdf",
        size_bytes=len(CLAIMED_BYTES),
        sha256=hashlib.sha256(CLAIMED_BYTES).hexdigest(),
        expires_at=datetime.now(timezone.utc),
        review_status="pending",
        lifecycle_status="available",
    )


async def _run_route(
    tmp_path: Path,
    result: _DirectPdfResult,
    *,
    tamper=None,
    publication_error: Exception | None = None,
    matterhorn_error: Exception | None = None,
):
    from src.api.education.remediation_routes import remediate_scan

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nsource\n%%EOF\n")
    scan = _scan(source)
    db = _RouteDB()
    remediator = MagicMock()

    def remediate():
        if tamper is not None:
            tamper(Path(result.output_file))
        return result

    remediator.remediate.side_effect = remediate
    artifact_service = MagicMock()
    publication = {}

    def publish(db_arg, **kwargs):
        assert db_arg is db
        publication["bytes"] = kwargs.pop("source_stream").read()
        publication["kwargs"] = kwargs
        if publication_error is not None:
            raise publication_error
        return _artifact()

    artifact_service.claim_and_publish_stream.side_effect = publish
    artifact_service.claim_and_publish.side_effect = AssertionError(
        "PDF publication must not use a path"
    )
    matterhorn = MagicMock()
    validation = {}

    def validate(path):
        validation["path"] = str(path)
        validation["bytes"] = Path(path).read_bytes()
        validation["exists_during_validation"] = Path(path).exists()
        if matterhorn_error is not None:
            raise matterhorn_error
        return SimpleNamespace(checkpoints=[], passed=0, total=0)

    matterhorn.validate.side_effect = validate
    audit = MagicMock()
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "src.api.education.remediation_routes.ScanService.get_scan_with_result",
                return_value=scan,
            )
        )
        stack.enter_context(
            patch("src.education.remediation.PdfRemediator", return_value=remediator)
        )
        stack.enter_context(
            patch(
                "src.api.education.remediation_routes.get_provider_manager",
                return_value=object(),
            )
        )
        stack.enter_context(
            patch(
                "src.api.education.remediation_routes.RemediationArtifactService.from_settings",
                return_value=artifact_service,
            )
        )
        stack.enter_context(
            patch(
                "src.education.validation.matterhorn.MatterhornValidator",
                return_value=matterhorn,
            )
        )
        stack.enter_context(
            patch("src.security.audit_service.AuditService", return_value=audit)
        )
        response = await remediate_scan(
            "scan-1", MagicMock(), db=db, principal=_principal()
        )
    return SimpleNamespace(
        response=response,
        scan=scan,
        db=db,
        service=artifact_service,
        publication=publication,
        validation=validation,
        audit=audit,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(
            lambda path: (
                path.with_name("replacement.pdf").write_bytes(b"replaced"),
                os.replace(path.with_name("replacement.pdf"), path),
            ),
            id="replace",
        ),
        pytest.param(lambda path: path.write_bytes(b"truncated"), id="truncate"),
        pytest.param(lambda path: path.unlink(), id="unlink"),
    ],
)
async def test_direct_pdf_publishes_and_validates_exact_claim_after_path_tamper(
    tmp_path, tamper
):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_BYTES)
    result = _DirectPdfResult(output)

    run = await _run_route(tmp_path, result, tamper=tamper)

    assert run.response["success"] is True
    assert run.response["artifact_sha256"] == hashlib.sha256(CLAIMED_BYTES).hexdigest()
    assert run.publication["bytes"] == CLAIMED_BYTES
    assert run.publication["kwargs"]["claimed_size_bytes"] == len(CLAIMED_BYTES)
    assert (
        run.publication["kwargs"]["claimed_sha256"]
        == hashlib.sha256(CLAIMED_BYTES).hexdigest()
    )
    assert run.publication["kwargs"]["claimed_mime_type"] == "application/pdf"
    assert run.publication["kwargs"]["claimed_filename"] == "fixed.pdf"
    assert run.validation["bytes"] == CLAIMED_BYTES
    assert run.validation["exists_during_validation"] is True
    assert run.validation["path"] != result.output_file
    assert not Path(run.validation["path"]).exists()
    assert result.close_calls == 1
    assert result.has_output_claim() is False
    persisted = [row for row in run.db.added if isinstance(row, ScanFix)]
    assert len(persisted) == 1
    assert persisted[0].issue_id == "issue-1"
    assert len(persisted[0].occurrence_key) == 64

    audit_fields = run.audit.log_remediation_complete.call_args.kwargs
    assert all("path" not in key and "fd" not in key for key in audit_fields)
    assert "output_file" not in run.response


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", [False, True], ids=["missing", "closed"])
async def test_direct_pdf_missing_or_closed_claim_fails_artifact_unavailable(
    tmp_path, closed
):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_BYTES)
    result = _DirectPdfResult(output, with_claim=closed)
    if closed:
        result.claim.close()

    run = await _run_route(tmp_path, result)

    assert run.response["success"] is False
    assert run.response["error"] == "remediation_artifact_unavailable"
    assert run.scan.status == ScanStatus.FAILED
    assert run.scan.remediation_outcome == "artifact_unavailable"
    run.service.claim_and_publish_stream.assert_not_called()
    assert run.validation == {}
    assert result.close_calls == 1


@pytest.mark.asyncio
async def test_direct_pdf_claim_closes_when_publication_raises(tmp_path):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_BYTES)
    result = _DirectPdfResult(output)

    with pytest.raises(HTTPException) as caught:
        await _run_route(
            tmp_path,
            result,
            publication_error=RuntimeError("publication failed"),
        )

    assert caught.value.status_code == 500
    assert caught.value.detail == "Remediation failed. Please try again."
    assert result.close_calls == 1
    assert result.has_output_claim() is False


@pytest.mark.asyncio
async def test_direct_pdf_claim_closes_when_matterhorn_raises(tmp_path):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_BYTES)
    result = _DirectPdfResult(output)

    run = await _run_route(
        tmp_path,
        result,
        matterhorn_error=RuntimeError("Matterhorn unavailable"),
    )

    assert run.response["success"] is True
    assert run.publication["bytes"] == CLAIMED_BYTES
    assert run.validation["bytes"] == CLAIMED_BYTES
    assert result.close_calls == 1
    assert result.has_output_claim() is False


@pytest.mark.asyncio
async def test_direct_pdf_claim_closes_on_unsuccessful_remediation_result(tmp_path):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_BYTES)
    result = _DirectPdfResult(output)
    result.success = False
    result.fixed_count = 0
    result.fixed_issues = []
    result.failed_count = 1

    run = await _run_route(tmp_path, result)

    assert run.response["success"] is False
    assert run.scan.status == ScanStatus.FAILED
    assert run.scan.remediation_outcome == "remediation_failed"
    run.service.claim_and_publish_stream.assert_not_called()
    assert result.close_calls == 1
    assert result.has_output_claim() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", [False, True], ids=["missing", "closed"])
async def test_successful_pdf_noop_still_requires_live_output_claim(tmp_path, closed):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_BYTES)
    result = _DirectPdfResult(output, with_claim=closed)
    result.fixed_count = 0
    result.fixed_issues = []
    if closed:
        result.claim.close()

    run = await _run_route(tmp_path, result)

    assert run.response["success"] is False
    assert run.response["error"] == "remediation_artifact_unavailable"
    assert run.scan.status == ScanStatus.FAILED
    assert run.scan.remediation_outcome == "artifact_unavailable"
    run.service.claim_and_publish_stream.assert_not_called()
    assert run.validation == {}
    assert result.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manual_count", "failed_count", "expected_outcome"),
    [
        pytest.param(1, 0, "manual_required", id="manual"),
        pytest.param(0, 1, "remediation_failed", id="failed"),
    ],
)
async def test_claimless_unsuccessful_pdf_never_falls_back_to_matterhorn_path(
    tmp_path, manual_count, failed_count, expected_outcome
):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_BYTES)
    result = _DirectPdfResult(output, with_claim=False)
    result.success = manual_count > 0
    result.fixed_count = 0
    result.fixed_issues = []
    result.manual_count = manual_count
    result.failed_count = failed_count

    run = await _run_route(tmp_path, result)

    assert run.response["success"] is False
    assert run.scan.remediation_outcome == expected_outcome
    assert run.validation == {}
    run.service.claim_and_publish_stream.assert_not_called()
    assert result.close_calls == 1


@pytest.mark.asyncio
async def test_direct_pdf_cancellation_closes_output_claim(tmp_path):
    output = tmp_path / "fixed.pdf"
    output.write_bytes(CLAIMED_BYTES)
    result = _DirectPdfResult(output)

    with pytest.raises(asyncio.CancelledError):
        await _run_route(
            tmp_path,
            result,
            matterhorn_error=asyncio.CancelledError(),
        )

    assert result.close_calls == 1
    assert result.has_output_claim() is False
