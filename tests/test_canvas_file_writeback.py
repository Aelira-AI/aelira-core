"""Remediated files must reach Canvas, and say so honestly when they cannot.

A remediated file used to have no route back to the course at all: approve
worked, write-back returned "not wired up yet", and the remediated artefact
sat on disk. Canvas files are not edited in place, so the remediated copy is
uploaded alongside the original and nothing anyone authored is overwritten.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.education.canvas_content_scanner import CanvasContentScanner


def _scanner(canvas_client=None, db=None):
    return CanvasContentScanner(
        canvas_client=canvas_client or MagicMock(),
        db=db or MagicMock(),
        department_id="d1",
        credential_id="cred-1",
    )


def _file_row(**overrides):
    cf = MagicMock()
    cf.id = "cf-1"
    cf.content_source = "file"
    cf.file_name = "syllabus.pdf"
    cf.provider_file_id = "9001"
    cf.provider_parent_id = "101"
    cf.writeback_status = "approved"
    cf.has_remediated_version = True
    cf.remediated_compliance_score = 96.0
    for key, value in overrides.items():
        setattr(cf, key, value)
    return cf


@pytest.mark.asyncio
async def test_a_remediated_file_is_uploaded_and_recorded(tmp_path, monkeypatch):
    artefact = tmp_path / "syllabus_fixed.pdf"
    artefact.write_bytes(b"%PDF-1.7")

    upload = MagicMock(
        success=True, file_id="canvas-77", web_view_link="https://canvas/files/77"
    )
    client = MagicMock()
    client.upload_file = AsyncMock(return_value=upload)

    scanner = _scanner(canvas_client=client)
    monkeypatch.setattr(scanner, "_find_remediated_file_path", lambda cf: str(artefact))

    cf = _file_row()
    result = await scanner.write_back_file(cf, approved_by="u1")

    assert result["success"] is True
    assert result["file_name"] == "syllabus_accessible.pdf"
    client.upload_file.assert_awaited_once()
    kwargs = client.upload_file.await_args.kwargs
    assert kwargs["course_id"] == "101"
    assert kwargs["local_path"] == str(artefact)
    assert cf.remediated_file_id == "canvas-77"
    assert cf.writeback_status == "written_back"
    # The verified remediated score becomes the item's current score.
    assert cf.last_compliance_score == 96.0


@pytest.mark.asyncio
async def test_a_missing_artefact_is_reported_not_uploaded(monkeypatch):
    client = MagicMock()
    client.upload_file = AsyncMock()
    scanner = _scanner(canvas_client=client)
    monkeypatch.setattr(scanner, "_find_remediated_file_path", lambda cf: None)

    result = await scanner.write_back_file(_file_row(), approved_by="u1")

    assert result["success"] is False
    assert "no longer on disk" in result["error"]
    client.upload_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unapproved_file_is_refused(monkeypatch):
    scanner = _scanner()
    monkeypatch.setattr(scanner, "_find_remediated_file_path", lambda cf: "/tmp/x.pdf")

    result = await scanner.write_back_file(
        _file_row(writeback_status="pending_review"), approved_by="u1"
    )

    assert result["success"] is False
    assert "must be 'approved'" in result["error"]


@pytest.mark.asyncio
async def test_a_failed_upload_leaves_the_row_untouched(tmp_path, monkeypatch):
    artefact = tmp_path / "a.pdf"
    artefact.write_bytes(b"%PDF")

    client = MagicMock()
    client.upload_file = AsyncMock(
        return_value=MagicMock(success=False, error="quota exceeded")
    )
    db = MagicMock()
    scanner = _scanner(canvas_client=client, db=db)
    monkeypatch.setattr(scanner, "_find_remediated_file_path", lambda cf: str(artefact))

    cf = _file_row()
    cf.writeback_at = None
    result = await scanner.write_back_file(cf, approved_by="u1")

    assert result["success"] is False
    assert "quota exceeded" in result["error"]
    assert cf.writeback_status == "approved"
    db.rollback.assert_called_once()


def test_the_remediated_path_comes_from_a_completed_job(tmp_path):
    artefact = tmp_path / "out.pdf"
    artefact.write_bytes(b"%PDF")

    job = MagicMock()
    job.result_data = {"output_file": str(artefact)}
    db = MagicMock()
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.first.return_value = job
    db.query.return_value = chain

    scanner = _scanner(db=db)
    assert scanner._find_remediated_file_path(_file_row()) == str(artefact)

    job.result_data = {"output_file": str(tmp_path / "gone.pdf")}
    assert scanner._find_remediated_file_path(_file_row()) is None
