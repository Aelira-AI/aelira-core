"""A rebuilt upload is the file that is stored, hashed and scanned."""

import asyncio
import hashlib
import io
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile

from src.api.education import scan_routes
from src.jobs.local_scan_job import materialize_verified_scan_input

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[1]
ORIGINAL = b"original upload with a VBA project"
REBUILT = b"rebuilt upload without the VBA project"


def test_rebuilt_word_upload_is_stored_and_passes_the_scan_hash_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    storage_root = tmp_path / "uploads"
    storage_root.mkdir()
    monkeypatch.setattr("src.utils.file_storage.UPLOAD_BASE_DIR", storage_root)
    monkeypatch.setattr("src.jobs.local_scan_job.UPLOAD_BASE_DIR", storage_root)
    monkeypatch.setattr(scan_routes, "check_scan_quota", AsyncMock())
    monkeypatch.setattr(
        scan_routes, "validate_uploaded_file", AsyncMock(return_value=REBUILT)
    )
    queued = {}
    monkeypatch.setattr(
        "src.jobs.local_scan_job.enqueue_local_scan_job",
        lambda db, **kwargs: queued.update(kwargs),
    )
    added = []
    db = MagicMock()
    db.add.side_effect = lambda scan: (
        setattr(scan, "id", "scan-1"),
        added.append(scan),
    )

    upload = UploadFile(file=io.BytesIO(ORIGINAL), filename="course.docx")
    asyncio.run(
        scan_routes.scan_word_document(
            file=upload, db=db, api_key_info=(None, "user-1", "dept-1")
        )
    )

    scan = added[0]
    assert Path(scan.storage_path).read_bytes() == REBUILT
    assert queued["input_sha256"] == hashlib.sha256(REBUILT).hexdigest()
    with materialize_verified_scan_input(scan, queued["input_sha256"]) as (_, content):
        assert content == REBUILT


def test_every_validated_upload_route_stores_the_validated_bytes():
    for relative in (
        "src/api/education/scan_routes.py",
        "src/api/education/web_scan_routes.py",
        "src/api/education/multimedia_routes.py",
    ):
        text = (ROOT / relative).read_text()
        validated = len(re.findall(r"content = await validate_uploaded_file\(", text))
        stored = len(
            re.findall(
                r"save_scan_bytes\(content, department_id, scan\.id, file\.filename\)",
                text,
            )
        )
        assert validated and stored == validated, relative
        assert "save_uploaded_file(file" not in text, relative
