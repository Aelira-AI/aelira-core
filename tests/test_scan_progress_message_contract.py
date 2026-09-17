"""Upload response poll instructions must resolve on the direct education API."""

from io import BytesIO
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import UploadFile
from starlette.routing import Match

# The production database module requires PostgreSQL engine options even when
# persistence is mocked. Import with an inert configuration, then restore the
# suite environment (which may deliberately use memory SQLite). No connection is
# made here or by these tests; the shared database fixture is disabled below.
with patch.dict(
    os.environ,
    {"DATABASE_URL": "postgresql://unused:unused@127.0.0.1:1/aelira_contract_test"},
):
    from src.api.education import router, scan_routes


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """These route contracts use mocked persistence and never connect to a DB."""
    yield


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "filename"),
    [
        (scan_routes.scan_pdf, "fixture.pdf"),
        (scan_routes.scan_powerpoint, "fixture.pptx"),
        (scan_routes.scan_word_document, "fixture.docx"),
        (scan_routes.scan_excel_spreadsheet, "fixture.xlsx"),
        (scan_routes.scan_latex_document, "fixture.tex"),
        (scan_routes.convert_latex_document, "fixture.pdf"),
    ],
)
async def test_upload_message_points_to_registered_progress_route(
    monkeypatch, endpoint, filename
):
    db = MagicMock()
    db.add.side_effect = lambda scan: setattr(scan, "id", "contract-scan")
    for name in ("check_scan_quota", "increment_usage", "require_feature"):
        monkeypatch.setattr(scan_routes, name, AsyncMock())
    monkeypatch.setattr(
        scan_routes, "validate_uploaded_file", AsyncMock(return_value=b"fixture")
    )
    monkeypatch.setattr(
        "src.utils.file_storage.save_uploaded_file",
        AsyncMock(return_value="/unused/fixture"),
    )
    enqueue = MagicMock()
    monkeypatch.setattr("src.jobs.local_scan_job.enqueue_local_scan_job", enqueue)

    response = await endpoint(
        file=UploadFile(filename=filename, file=BytesIO(b"fixture")),
        db=db,
        api_key_info=(None, "contract-user", "contract-workspace"),
    )

    enqueue.assert_called_once()
    db.commit.assert_called_once()
    path = response["message"].split("Poll ", 1)[1].split()[0]
    path = path.format(scan_id=response["scan_id"])
    matches = [
        route
        for route in router.routes
        if route.matches({"type": "http", "method": "GET", "path": path})[0]
        == Match.FULL
    ]
    assert len(matches) == 1, f"Upload suggested an unregistered GET path: {path}"
    assert path == router.url_path_for("get_scan_progress", scan_id=response["scan_id"])
