"""Project archive retrieval binds tenant authority and immutable input bytes."""

import hashlib
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, UploadFile

from src.api.education import latex_project_routes as routes
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import ScanType, UserRole
from src.education.latex_project import inspect_archive


def archive(source=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr(
            "main.tex",
            source or r"\documentclass{article}\begin{document}Hello\end{document}",
        )
        zipped.writestr("notes.txt", "Preserve these original bytes.\r\n")
    return buffer.getvalue()


@pytest.fixture
def stored_project(tmp_path, monkeypatch):
    data = archive()
    directory = tmp_path / "department" / "scan"
    directory.mkdir(parents=True)
    path = directory / "original.zip"
    path.write_bytes(data)
    scan = SimpleNamespace(
        id="scan",
        department_id="department",
        file_name="Course project.zip",
        storage_path=str(path),
        file_hash=hashlib.sha256(data).hexdigest(),
        scan_type=ScanType.LATEX,
        result=None,
    )
    principal = AuthenticatedPrincipal(
        None, "user", "department", UserRole.ADMIN, "session"
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = scan
    monkeypatch.setattr(routes.file_storage, "UPLOAD_BASE_DIR", tmp_path)
    monkeypatch.setattr(
        routes,
        "stored_latex_project_input",
        lambda db, scan: ("main.tex", hashlib.sha256(data).hexdigest()),
    )
    return db, scan, principal, data, path


def test_original_retrieval_returns_exact_archive_bytes(stored_project):
    db, scan, principal, data, _ = stored_project
    response = routes.download_latex_project_original(scan.id, db, principal)
    assert response.body == data
    assert response.media_type == "application/zip"
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("change", ["bytes", "symlink", "parent_symlink", "outside"])
def test_original_changes_or_escaped_storage_are_refused(
    stored_project, tmp_path, change
):
    db, scan, principal, data, path = stored_project
    if change == "bytes":
        path.write_bytes(data + b"changed")
    elif change == "symlink":
        path.rename(path.with_suffix(".saved"))
        path.symlink_to(path.with_suffix(".saved"))
    elif change == "parent_symlink":
        directory = path.parent
        moved = directory.with_name("moved")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    else:
        other = tmp_path / "other.zip"
        other.write_bytes(data)
        scan.storage_path = str(other)
    with pytest.raises(HTTPException) as caught:
        routes.download_latex_project_original(scan.id, db, principal)
    assert caught.value.status_code in {404, 409}


def test_cross_workspace_original_is_refused_before_storage_read(
    stored_project, monkeypatch
):
    db, scan, _, _, _ = stored_project
    principal = AuthenticatedPrincipal(
        None, "other", "other", UserRole.ADMIN, "session"
    )
    read = MagicMock()
    monkeypatch.setattr(routes, "_read_original", read)
    with pytest.raises(HTTPException) as caught:
        routes.download_latex_project_original(scan.id, db, principal)
    assert caught.value.status_code == 403
    read.assert_not_called()


def test_course_lti_cannot_read_unbound_local_project(stored_project, monkeypatch):
    db, scan, _, _, _ = stored_project
    principal = AuthenticatedPrincipal(
        None,
        "teacher",
        "department",
        UserRole.FACULTY,
        "lti",
        lti_course_id="course",
        lti_staff_role="Instructor",
    )
    db.query.return_value.filter.return_value.first.side_effect = [scan, None]
    read = MagicMock()
    monkeypatch.setattr(routes, "_read_original", read)
    with pytest.raises(HTTPException) as caught:
        routes.download_latex_project_original(scan.id, db, principal)
    assert caught.value.status_code == 404
    read.assert_not_called()


@pytest.mark.asyncio
async def test_upload_preserves_zip_and_queues_explicit_entry(tmp_path, monkeypatch):
    data = archive()
    principal = AuthenticatedPrincipal(
        None, "user", "department", UserRole.ADMIN, "session"
    )
    db = MagicMock()
    created = []
    db.add.side_effect = created.append
    db.flush.side_effect = lambda: setattr(created[0], "id", "new-scan")
    queued = MagicMock()
    monkeypatch.setattr(routes, "require_feature", AsyncMock())
    monkeypatch.setattr(routes, "enqueue_local_scan_job", queued)
    monkeypatch.setattr(routes.file_storage, "UPLOAD_BASE_DIR", tmp_path)
    response = await routes.upload_latex_project(
        UploadFile(io.BytesIO(data), filename="project.zip"),
        "main.tex",
        False,
        db,
        principal,
    )
    assert response["scan_id"] == "new-scan"
    assert (tmp_path / "department/new-scan/original.zip").read_bytes() == data
    assert queued.call_args.kwargs["options"] == {
        "use_ollama": False,
        "entry_file": "main.tex",
    }
    assert queued.call_args.kwargs["input_sha256"] == hashlib.sha256(data).hexdigest()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_upload_bound_is_enforced_before_inspection(monkeypatch):
    principal = AuthenticatedPrincipal(
        None, "user", "department", UserRole.ADMIN, "session"
    )
    monkeypatch.setattr(routes, "require_feature", AsyncMock())
    monkeypatch.setattr(routes, "MAX_ARCHIVE_BYTES", 16)
    with pytest.raises(HTTPException) as caught:
        await routes.upload_latex_project(
            UploadFile(io.BytesIO(b"x" * 17), filename="project.zip"),
            "main.tex",
            False,
            MagicMock(),
            principal,
        )
    assert caught.value.status_code == 413


def test_html_download_requires_matching_receipt(stored_project, monkeypatch):
    db, scan, principal, data, _ = stored_project
    project = inspect_archive(data, "main.tex")
    html = "<!doctype html><html><body>Hello</body></html>"
    receipt = {
        "schema_version": 1,
        "profile": "pandoc-project-html-v1",
        "archive_sha256": project.archive_digest,
        "source_sha256": project.source_digest,
        "analysis_sha256": hashlib.sha256(
            project.flattened_source.encode()
        ).hexdigest(),
        "output_sha256": hashlib.sha256(html.encode()).hexdigest(),
        "tool_version": "3.1.11",
        "status": "accepted",
        "transformations": [],
        "reasons": [],
        "accessibility_status": "not_verified",
        "human_review_required": True,
    }
    scan.result = SimpleNamespace(
        structure={"latex_project": {"conversion": receipt}}, html_output=html
    )
    assert (
        routes.download_latex_project_html(scan.id, db, principal).body == html.encode()
    )
    scan.result.html_output += "tampered"
    with pytest.raises(HTTPException) as caught:
        routes.download_latex_project_html(scan.id, db, principal)
    assert caught.value.status_code == 404


def test_unresolved_project_has_no_invented_score(stored_project, monkeypatch):
    db, scan, _, _, _ = stored_project
    from src.db import database

    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    data = archive(
        r"\documentclass{article}\begin{document}\input{missing}\end{document}"
    )
    routes.process_latex_project_background(
        data, "main.tex", scan.id, scan.department_id
    )
    assert scan.status.value == "FAILED"
    assert "dependencies require review" in scan.error_message
    db.add.assert_not_called()
    db.commit.assert_called_once()


def test_worker_scans_expanded_project_source(stored_project, monkeypatch):
    db, scan, _, _, _ = stored_project
    from src.db import database
    from src.education import latex_project_conversion

    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr(
            "main.tex",
            r"\documentclass{article}\title{Notes}\author{Author}\begin{document}\input{chapter}\end{document}",
        )
        zipped.writestr("chapter.tex", r"\begin{equation}x=1\end{equation}")
    monkeypatch.setattr(
        latex_project_conversion,
        "convert_project_html",
        lambda project, output: SimpleNamespace(
            path=None, provenance={"status": "refused"}, issues=()
        ),
    )
    routes.process_latex_project_background(
        buffer.getvalue(), "main.tex", scan.id, scan.department_id
    )
    saved = db.add.call_args.args[0]
    assert any(issue["type"] == "equation_no_label" for issue in saved.issues)
    assert all(
        issue["location_scope"] == "expanded_project_source" for issue in saved.issues
    )
    assert saved.structure["conversion_scope"] == "complete_project"
    assert saved.html_output is None
    assert scan.status.value == "COMPLETED"


def test_project_router_requires_authentication():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.auth.dependencies import get_authenticated_principal

    app = FastAPI()
    app.include_router(routes.router)

    def unauthenticated():
        raise HTTPException(401, "Authentication required")

    app.dependency_overrides[get_authenticated_principal] = unauthenticated
    app.dependency_overrides[routes.get_db_dependency] = lambda: MagicMock()
    client = TestClient(app)
    for endpoint in (
        "/latex/projects/scan",
        "/latex/projects/scan/original",
        "/latex/projects/scan/html",
    ):
        assert client.get(endpoint).status_code == 401
