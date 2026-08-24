"""Atomic failure gates for Brightspace image-equation publication."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.education.remediation.output_claim import DescriptorBoundOutputClaim
from src.services.remediation_artifact_service import ArtifactPublicationResult

PDF_BYTES = b"%PDF-1.7\nverified image equation\n%%EOF\n"


class _ClaimedResult(SimpleNamespace):
    def __init__(self, output: Path) -> None:
        output.write_bytes(PDF_BYTES)
        super().__init__(
            success=True,
            verification_passed=True,
            fixed_count=1,
            manual_count=0,
            failed_count=0,
            fixed_issues=[object()],
        )
        self.close_calls = 0
        self.claim = DescriptorBoundOutputClaim._snapshot_from_owned_descriptor(
            os.open(output, os.O_RDONLY),
            filename=output.name,
            display_path=str(output),
            mime="application/pdf",
        )

    def has_output_claim(self) -> bool:
        return not self.claim.closed

    def output_claim_metadata(self):
        return {
            "size_bytes": self.claim.size,
            "sha256": self.claim.sha256,
            "mime_type": self.claim.mime,
            "filename": self.claim.filename,
        }

    def open_output_stream(self):
        return self.claim.open_stream()

    def close_output_claim(self) -> None:
        self.close_calls += 1
        self.claim.close()


@pytest.mark.asyncio
async def test_scanfix_failure_aborts_exact_staging_and_preserves_prior_artifact(
    tmp_path,
):
    from src.api.brightspace_routes import _finish_brightspace_pdf_remediation

    cloud_file = SimpleNamespace(
        id="cloud-1",
        department_id="dept-1",
        last_scan_id="scan-1",
        current_remediation_artifact_id="artifact-prior",
        has_remediated_version=True,
        remediation_origin="manual",
        remediated_issues_fixed=2,
        remediated_issues_remaining=0,
        writeback_status="pending_review",
    )
    prior_state = dict(vars(cloud_file))
    result = _ClaimedResult(tmp_path / "fixed.pdf")
    artifact = SimpleNamespace(
        id="artifact-new",
        lifecycle_status="available",
        mime_type="application/pdf",
        size_bytes=len(PDF_BYTES),
        sha256=hashlib.sha256(PDF_BYTES).hexdigest(),
        expires_at=datetime.now(timezone.utc),
        review_status="pending",
    )
    publication = ArtifactPublicationResult(
        artifact=artifact,
        artifact_id=artifact.id,
        publication_token="exact-staging-token",
    )
    service = MagicMock()

    def publish(*_args, **kwargs):
        assert kwargs["source_stream"].read() == PDF_BYTES
        cloud_file.current_remediation_artifact_id = artifact.id
        return publication

    service.claim_and_publish_stream.side_effect = publish
    db = MagicMock()
    with (
        patch(
            "src.api.brightspace_routes.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch("src.api.brightspace_routes._validate_brightspace_pdf_claim"),
        patch(
            "src.api.brightspace_routes.persist_scan_fixes",
            side_effect=ValueError("invalid occurrence evidence"),
        ),
    ):
        outcome = await _finish_brightspace_pdf_remediation(
            cloud_file,
            db,
            result=result,
            complete=True,
            decisions={"remediation": "not_requested", "alt_text": "used"},
            alt_text_client=SimpleNamespace(provider="ollama"),
        )

    assert outcome.status == "failed"
    assert outcome.error_code == "artifact_unavailable"
    assert vars(cloud_file) == prior_state
    db.commit.assert_not_called()
    db.rollback.assert_called()
    service.abort_staging.assert_called_once_with(
        db,
        artifact_id="artifact-new",
        publication_token="exact-staging-token",
    )
    assert result.close_calls == 1
    assert result.has_output_claim() is False
