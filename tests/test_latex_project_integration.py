"""PostgreSQL HTTP/worker contracts; converter execution has a separate real smoke."""

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from src.api.education import latex_project_routes as routes
from src.api.education import scan_history_routes, remediation_routes
from src.auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from src.db import database
from src.db.database import engine, get_db_dependency
from src.db.models import (
    CloudJobQueue,
    Department,
    Scan,
    ScanResult,
    ScanStatus,
    User,
    UserRole,
)
from src.education import latex_project_conversion
from src.education.latex_project_conversion import ProjectConversion, ProjectProvenance
from src.jobs import local_scan_job

pytestmark = pytest.mark.integration


def _archive(*, missing=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "Course notes/main.tex",
            r"\documentclass{article}\usepackage[english]{babel}\title{Course}\author{Author}\begin{document}\input{chapter}\end{document}",
        )
        if not missing:
            archive.writestr(
                "Course notes/chapter.tex",
                r"A complete chapter. \begin{equation}x=1\end{equation}",
            )
        archive.writestr("original notes.txt", b"Authored notes\r\n")
    return buffer.getvalue()


@pytest.fixture
def project_http(tmp_path, monkeypatch):
    with engine.connect() as connection:
        transaction = connection.begin()
        db = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            department = Department(
                id=str(uuid.uuid4()),
                name="Project integration",
                institution="Example University",
                contact_email="projects@example.edu",
            )
            db.add(department)
            db.flush()
            user = User(
                id=str(uuid.uuid4()),
                department_id=department.id,
                email=f"project-{uuid.uuid4().hex}@example.edu",
                role=UserRole.ADMIN,
            )
            db.add(user)
            db.commit()
            principal = AuthenticatedPrincipal(
                None, user.id, department.id, UserRole.ADMIN, "session"
            )
            app = FastAPI()
            app.include_router(routes.router, prefix="/education")
            app.include_router(scan_history_routes.router, prefix="/education")
            app.include_router(remediation_routes.router, prefix="/education")
            app.dependency_overrides[get_db_dependency] = lambda: db
            app.dependency_overrides[get_authenticated_principal] = lambda: principal
            monkeypatch.setattr(routes, "require_feature", AsyncMock())
            monkeypatch.setattr(routes.file_storage, "UPLOAD_BASE_DIR", tmp_path)
            monkeypatch.setattr(local_scan_job, "UPLOAD_BASE_DIR", tmp_path)
            monkeypatch.setattr(
                database,
                "SessionLocal",
                lambda: Session(
                    bind=connection, join_transaction_mode="create_savepoint"
                ),
            )
            conversions = []

            def bounded_converter(project, output_dir):
                conversions.append(project)
                output = Path(output_dir) / "project.html"
                data = b'<!doctype html><html lang="en"><head><title>Course</title></head><body>A complete chapter.</body></html>'
                output.write_bytes(data)
                provenance = ProjectProvenance(
                    archive_sha256=project.archive_digest,
                    source_sha256=project.source_digest,
                    analysis_sha256=hashlib.sha256(
                        project.flattened_source.encode()
                    ).hexdigest(),
                    output_sha256=hashlib.sha256(data).hexdigest(),
                    status="accepted",
                    tool_version="3.1.11",
                ).model_dump(mode="json")
                return ProjectConversion(str(output), provenance, ())

            monkeypatch.setattr(
                latex_project_conversion, "convert_project_html", bounded_converter
            )
            with TestClient(app) as client:
                yield SimpleNamespace(
                    client=client,
                    db=db,
                    app=app,
                    principal=principal,
                    conversions=conversions,
                )
        finally:
            db.close()
            transaction.rollback()


def _upload(case, *, missing=False):
    data = _archive(missing=missing)
    response = case.client.post(
        "/education/latex/projects",
        files={"file": ("Course project.zip", data, "application/zip")},
        data={"entry_file": "Course notes/main.tex"},
    )
    assert response.status_code == 200, response.text
    scan_id = response.json()["scan_id"]
    scan = case.db.get(Scan, scan_id)
    job = (
        case.db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.department_id == case.principal.department_id,
            CloudJobQueue.payload["scan_id"].as_string() == scan_id,
        )
        .one()
    )
    return scan, job, data


def _run_worker(case, scan, job):
    payload = dict(job.payload)
    options = local_scan_job.normalize_local_scan_options(
        payload["scan_kind"], payload["options"]
    )
    # Release the route session's savepoint before the worker takes its own.
    case.db.commit()
    with local_scan_job.materialize_verified_scan_input(
        scan, payload["input_sha256"]
    ) as (path, data):
        local_scan_job._run_local_processor(
            payload["scan_kind"], scan, options, path, data
        )
    case.db.expire_all()


def test_project_http_to_durable_scan_worker_and_verified_downloads(project_http):
    case = project_http
    scan, job, original = _upload(case)
    assert job.payload == {
        "scan_kind": "local_latex",
        "scan_id": scan.id,
        "options": {"use_ollama": False, "entry_file": "Course notes/main.tex"},
        "input_sha256": hashlib.sha256(original).hexdigest(),
    }
    assert Path(scan.storage_path).read_bytes() == original
    assert local_scan_job.stored_latex_project_input(case.db, scan) == (
        "Course notes/main.tex",
        hashlib.sha256(original).hexdigest(),
    )
    _run_worker(case, scan, job)
    case.db.refresh(scan)
    assert scan.status == ScanStatus.COMPLETED
    assert len(case.conversions) == 1
    assert "A complete chapter" in case.conversions[0].flattened_source
    saved = case.db.query(ScanResult).filter(ScanResult.scan_id == scan.id).one()
    assert any(issue["type"] == "equation_no_label" for issue in saved.issues)
    assert all(
        issue["location_scope"] == "expanded_project_source" for issue in saved.issues
    )
    url = f"/education/latex/projects/{scan.id}"
    report = case.client.get(url)
    assert report.status_code == 200
    assert report.json()["manifest"]["entry"] == "Course notes/main.tex"
    assert report.json()["conversion"]["status"] == "accepted"
    assert case.client.get(f"{url}/original").content == original
    html = case.client.get(f"{url}/html")
    assert html.status_code == 200
    assert (
        hashlib.sha256(html.content).hexdigest()
        == report.json()["conversion"]["output_sha256"]
    )
    alias = case.client.get(f"/education/scans/{scan.id}/html")
    assert alias.status_code == 200
    assert alias.content == html.content
    assert "sandbox" in alias.headers["content-security-policy"]
    assert Path(scan.storage_path).read_bytes() == original


def test_failed_dependency_scan_retains_original_and_actionable_manifest(project_http):
    case = project_http
    scan, job, original = _upload(case, missing=True)
    _run_worker(case, scan, job)
    case.db.refresh(scan)
    assert scan.status == ScanStatus.FAILED
    assert case.db.query(ScanResult).filter(ScanResult.scan_id == scan.id).count() == 0
    assert case.conversions == []
    url = f"/education/latex/projects/{scan.id}"
    report = case.client.get(url)
    assert report.status_code == 200
    assert report.json()["state"] == "unresolved"
    assert report.json()["manifest"]["issues"]
    assert case.client.get(f"{url}/original").content == original
    assert case.client.get(f"{url}/html").status_code == 404


def test_project_original_tamper_is_rejected_from_persisted_binding(project_http):
    case = project_http
    scan, _, original = _upload(case)
    path = Path(scan.storage_path)
    path.chmod(0o600)
    path.write_bytes(original + b"changed")
    url = f"/education/latex/projects/{scan.id}"
    assert case.client.get(url).status_code == 409
    assert case.client.get(f"{url}/original").status_code == 409


def test_changed_storage_suffix_cannot_bypass_generic_html_guard(project_http):
    case = project_http
    scan, job, _ = _upload(case)
    _run_worker(case, scan, job)
    changed = Path(scan.storage_path).with_suffix(".tex")
    changed.write_bytes(b"Changed source")
    scan.storage_path = str(changed)
    case.db.commit()
    assert case.client.get(f"/education/scans/{scan.id}/html").status_code == 409


def test_project_read_authority_remains_workspace_and_course_scoped(project_http):
    case = project_http
    scan, _, _ = _upload(case)
    url = f"/education/latex/projects/{scan.id}"
    other = AuthenticatedPrincipal(
        None, "other-user", "other-department", UserRole.ADMIN, "session"
    )
    case.app.dependency_overrides[get_authenticated_principal] = lambda: other
    for suffix in ("", "/original", "/html"):
        assert case.client.get(url + suffix).status_code == 403
    teacher = AuthenticatedPrincipal(
        None,
        "teacher",
        case.principal.department_id,
        UserRole.FACULTY,
        "lti",
        lti_course_id="unrelated-course",
        lti_staff_role="Instructor",
    )
    case.app.dependency_overrides[get_authenticated_principal] = lambda: teacher
    for suffix in ("", "/original", "/html"):
        assert case.client.get(url + suffix).status_code == 404


def test_stale_conversion_binding_is_hidden_on_report_and_both_html_routes(
    project_http,
):
    case = project_http
    scan, job, _ = _upload(case)
    _run_worker(case, scan, job)
    saved = case.db.query(ScanResult).filter(ScanResult.scan_id == scan.id).one()
    structure = dict(saved.structure)
    structure["latex_project"] = {
        **structure["latex_project"],
        "conversion": {
            **structure["latex_project"]["conversion"],
            "archive_sha256": "f" * 64,
        },
    }
    saved.structure = structure
    case.db.commit()
    url = f"/education/latex/projects/{scan.id}"
    assert case.client.get(url).json()["conversion"] is None
    assert case.client.get(f"{url}/html").status_code == 404
    assert case.client.get(f"/education/scans/{scan.id}/html").status_code == 404


def test_project_remediation_is_explicitly_manual(project_http):
    case = project_http
    scan, job, _ = _upload(case)
    _run_worker(case, scan, job)
    assert remediation_routes.document_remediation_eligibility(
        case.db, scan, case.principal
    ) == {"eligible": False, "reason": "project_source_review_required"}
    response = case.client.post(
        f"/education/remediate/{scan.id}", json={"use_ai": False}
    )
    assert response.status_code == 400
    assert "project_source_review_required" in response.text
    assert (
        case.db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.job_type == "remediation",
            CloudJobQueue.department_id == scan.department_id,
        )
        .count()
        == 0
    )


def test_same_archive_other_entry_cannot_reuse_old_html_receipt(project_http):
    case = project_http
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "main.tex",
            r"\documentclass{article}\begin{document}First entry\end{document}",
        )
        archive.writestr(
            "other.tex",
            r"\documentclass{article}\begin{document}Other entry\end{document}",
        )
    response = case.client.post(
        "/education/latex/projects",
        files={"file": ("project.zip", buffer.getvalue(), "application/zip")},
        data={"entry_file": "main.tex"},
    )
    assert response.status_code == 200
    scan = case.db.get(Scan, response.json()["scan_id"])
    job = (
        case.db.query(CloudJobQueue)
        .filter(CloudJobQueue.payload["scan_id"].as_string() == scan.id)
        .one()
    )
    _run_worker(case, scan, job)
    url = f"/education/latex/projects/{scan.id}"
    assert case.client.get(f"{url}/html").status_code == 200
    job.payload = {
        **job.payload,
        "options": {**job.payload["options"], "entry_file": "other.tex"},
    }
    case.db.commit()
    report = case.client.get(url)
    assert report.status_code == 200
    assert report.json()["manifest"]["entry"] == "other.tex"
    assert report.json()["conversion"] is None
    assert case.client.get(f"{url}/html").status_code == 404
    assert case.client.get(f"/education/scans/{scan.id}/html").status_code == 404
