"""Generated caption files accompany the media; they never replace it at a provider."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db import models
from src.jobs import upload_job
from src.services.remediation_artifact_service import is_companion_artifact


@pytest.mark.parametrize(
    ("mime_type", "scan_type", "expected"),
    [
        ("text/vtt", "VIDEO", True),
        ("text/vtt", models.ScanType.MULTIMEDIA, True),
        ("application/pdf", "PDF", False),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            models.ScanType.WORD,
            False,
        ),
    ],
)
def test_companion_artifact_classification(mime_type, scan_type, expected):
    artifact = SimpleNamespace(mime_type=mime_type, scan_type=scan_type)
    assert is_companion_artifact(artifact) is expected


async def test_upload_job_refuses_caption_artifacts_before_provider_contact(
    monkeypatch,
):
    artifact = SimpleNamespace(
        id="artifact-1",
        department_id="department-1",
        cloud_file_id="file-1",
        provider="google",
        scan_id="scan-1",
        mime_type="text/vtt",
        scan_type="VIDEO",
        filename="lecture_remediated.vtt",
    )
    db = MagicMock()
    db.get.side_effect = lambda model, _id: (
        artifact if model is models.RemediationArtifact else None
    )
    service = MagicMock()
    monkeypatch.setattr(
        upload_job.RemediationArtifactService, "from_settings", lambda: service
    )
    sink = AsyncMock()
    monkeypatch.setattr(upload_job, "_process_upload_path", sink)

    result = await upload_job.process_upload_job(
        {
            "artifact_id": "artifact-1",
            "scan_id": "scan-1",
            "department_id": "department-1",
            "cloud_file_id": "file-1",
            "credential_id": "credential-1",
            "provider": "google",
            "artifact_checksum": "a" * 64,
        },
        db,
    )

    assert result == {
        "success": False,
        "uploaded": False,
        "error": "companion_artifact_not_writable",
    }
    service.open_verified.assert_not_called()
    sink.assert_not_awaited()


async def test_canvas_file_writeback_refuses_caption_artifacts():
    from src.education.canvas_content_scanner import CanvasContentScanner

    scanner = object.__new__(CanvasContentScanner)
    scanner.db = MagicMock()
    artifact = SimpleNamespace(id="artifact-1", mime_type="text/vtt", scan_type="VIDEO")
    cloud_file = SimpleNamespace(content_source=None, needs_rescan=False)
    scanner._lock_file_writeback_graph = MagicMock(
        return_value=(cloud_file, SimpleNamespace(), artifact)
    )

    result = await scanner.write_back_file(cloud_file, approved_by="reviewer-1")

    assert result["success"] is False
    assert result["error_code"] == "companion_artifact_not_writable"
    scanner.db.rollback.assert_called_once()
    scanner.db.commit.assert_not_called()
