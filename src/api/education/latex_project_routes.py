"""Bounded LaTeX project intake and authorized original-source retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from ...auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ...db.database import get_db_dependency
from ...db.models import Scan, ScanResult, ScanStatus, ScanType
from ...education.latex_project import MAX_ARCHIVE_BYTES, ProjectError, inspect_archive
from ...jobs.local_scan_job import (
    LocalScanJobError,
    enqueue_local_scan_job,
    stored_latex_project_input,
)
from ...middleware.quota import require_feature
from ...utils import file_storage
from ._scope import authorize_scan_access, require_supported_scan_course

router = APIRouter()


def _read_original(scan: Scan, expected: str) -> bytes:
    """Read one bounded descriptor through owned directories, then check bytes."""
    source = Path(scan.storage_path or "")
    base = file_storage.UPLOAD_BASE_DIR.resolve()
    parts = (str(scan.department_id), str(scan.id), source.name)
    if any(
        not part or part in {".", ".."} or "/" in part or "\\" in part for part in parts
    ):
        raise HTTPException(404, "Original project not available")
    if source.absolute() != base.joinpath(*parts):
        raise HTTPException(404, "Original project not available")
    descriptors = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(base, directory_flags)
        descriptors.append(descriptor)
        for part in parts[:2]:
            descriptor = os.open(part, directory_flags, dir_fd=descriptor)
            descriptors.append(descriptor)
        descriptor = os.open(parts[2], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        descriptors.append(descriptor)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("not_regular")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            data = stream.read(MAX_ARCHIVE_BYTES + 1)
        if (
            len(data) > MAX_ARCHIVE_BYTES
            or hashlib.sha256(data).hexdigest() != expected
        ):
            raise OSError("changed_original")
        return data
    except OSError:
        raise HTTPException(409, "Original project unavailable or changed") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _authorized_project(db, scan_id, principal):
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if scan is None:
        raise HTTPException(404, "Scan not found")
    authorize_scan_access(db, scan, principal)
    if scan.scan_type != ScanType.LATEX:
        raise HTTPException(404, "Project not found")
    try:
        entry, expected = stored_latex_project_input(db, scan)
    except LocalScanJobError:
        raise HTTPException(404, "Project not found") from None
    data = _read_original(scan, expected)
    try:
        project = inspect_archive(data, entry)
    except ProjectError:
        raise HTTPException(409, "Original project unavailable or changed") from None
    return scan, data, project


@router.post("/latex/projects")
async def upload_latex_project(
    file: UploadFile = File(...),
    entry_file: str = Form(...),
    use_ollama: bool = False,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    # Local uploads cannot acquire a Canvas course binding from a filename.
    if principal.auth_method == "lti" and not principal.lti_account_wide:
        require_supported_scan_course(principal)
        raise HTTPException(403, "Local project uploads require workspace access")
    await require_feature(db, principal.department_id, "latex", "LaTeX Conversion")
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "Upload a ZIP project and an explicit entry TEX file")
    data = await file.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise HTTPException(413, "Project archive exceeds the size limit")
    try:
        project = await asyncio.to_thread(inspect_archive, data, entry_file)
    except ProjectError as exc:
        raise HTTPException(400, detail={"code": exc.code}) from None
    scan = Scan(
        scan_type=ScanType.LATEX,
        status=ScanStatus.PROCESSING,
        file_name=file.filename,
        file_size_bytes=len(data),
        file_hash=project.archive_digest,
        user_id=principal.user_id,
        department_id=principal.department_id,
        progress=0,
        progress_message="Inspecting LaTeX project...",
    )
    db.add(scan)
    db.flush()
    directory = file_storage.get_scan_storage_dir(principal.department_id, scan.id)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    original = directory / "original.zip"
    with original.open("xb") as stream:
        stream.write(data)
    original.chmod(0o400)
    scan.storage_path = str(original.absolute())
    enqueue_local_scan_job(
        db,
        scan=scan,
        scan_kind="local_latex",
        options={"use_ollama": use_ollama, "entry_file": entry_file},
        input_sha256=project.archive_digest,
    )
    db.commit()
    return {
        "success": True,
        "scan_id": scan.id,
        "status": "PROCESSING",
        "project_url": f"/education/latex/projects/{scan.id}",
        "original_url": f"/education/latex/projects/{scan.id}/original",
    }


@router.get("/latex/projects/{scan_id}")
def get_latex_project(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    scan, _, project = _authorized_project(db, scan_id, principal)
    provenance = _bound_project_provenance(scan, project)
    return {
        "scan_id": scan.id,
        "state": "unresolved" if project.issues else "resolved",
        "manifest": project.manifest,
        "conversion": provenance,
        "human_review_required": True,
        "original_url": f"/education/latex/projects/{scan.id}/original",
        "html_url": (
            f"/education/latex/projects/{scan.id}/html"
            if _project_html(scan, project) is not None
            else None
        ),
    }


@router.get("/latex/projects/{scan_id}/original")
def download_latex_project_original(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    scan, data, project = _authorized_project(db, scan_id, principal)
    filename = quote(Path(scan.file_name or "project.zip").name, safe="")
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "ETag": f'"{project.archive_digest}"',
            "Cache-Control": "private, no-store",
        },
    )


def process_latex_project_background(file_content, entry_file, scan_id, department_id):
    """Inspect the whole source graph without feeding fragments to latex2mathml."""
    from ...db.database import SessionLocal
    from ...education.latex_processor import LaTeXProcessor
    from ...education.latex_project_conversion import convert_project_html

    db = SessionLocal()
    try:
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if scan is None or scan.department_id != department_id:
            raise ValueError("workspace_scope_invalid")
        project = inspect_archive(file_content, entry_file)
        if project.flattened_source is None or project.issues:
            scan.status = ScanStatus.FAILED
            scan.error_message = "Project dependencies require review. Retrieve the project report for unresolved files."
            scan.progress_message = scan.error_message
            scan.completed_at = func.now()
            db.commit()
            return
        processor = LaTeXProcessor(use_ai=False, llm_client=False)
        measured = processor.scan_source(project.flattened_source)
        issues = []
        counts = {key: 0 for key in ("critical", "high", "medium", "low")}
        severity_map = {
            "critical": "critical",
            "serious": "high",
            "moderate": "medium",
            "minor": "low",
        }
        for issue in measured["issues"]:
            severity = severity_map.get(issue["severity"], "medium")
            counts[severity] += 1
            issues.append(
                {
                    "type": issue["issue_type"],
                    "severity": severity,
                    "description": issue["description"],
                    "line_number": issue.get("line_number"),
                    "location_scope": "expanded_project_source",
                    "latex_snippet": issue.get("latex_snippet"),
                    "wcag": issue.get("wcag_criterion"),
                    "recommendation": issue.get("recommendation"),
                }
            )
        with tempfile.TemporaryDirectory(prefix="latex-project-scan-") as temporary:
            conversion = convert_project_html(project, Path(temporary))
            html = None
            if conversion.path:
                candidate = Path(conversion.path).resolve(strict=True)
                if not candidate.is_relative_to(Path(temporary).resolve()):
                    raise ValueError("project_output_scope_invalid")
                candidate_bytes = candidate.read_bytes()
                if (
                    len(candidate_bytes) > MAX_ARCHIVE_BYTES
                    or conversion.provenance.get("status") != "accepted"
                    or hashlib.sha256(candidate_bytes).hexdigest()
                    != conversion.provenance.get("output_sha256")
                ):
                    raise ValueError("project_output_changed")
                html = candidate_bytes.decode("utf-8")
        record = {
            "manifest": project.manifest,
            "conversion": conversion.provenance,
            "issues": list(conversion.issues),
        }
        db.add(
            ScanResult(
                scan_id=scan.id,
                compliance_score=measured["compliance_score"],
                wcag_level="AA",
                critical_issues=counts["critical"],
                high_issues=counts["high"],
                medium_issues=counts["medium"],
                low_issues=counts["low"],
                issues=issues,
                structure={
                    "latex_project": record,
                    "conversion_scope": "complete_project",
                    "human_review_required": True,
                    "accessibility_status": "not_verified",
                },
                html_output=html,
                ocr_used=False,
            )
        )
        scan.status = ScanStatus.COMPLETED
        scan.file_hash = project.archive_digest
        scan.progress = 100
        scan.progress_message = "Project inspection complete; source review required"
        scan.completed_at = func.now()
        db.commit()
    except Exception:
        db.rollback()
        scan = (
            db.query(Scan)
            .filter(Scan.id == scan_id, Scan.department_id == department_id)
            .first()
        )
        if scan:
            scan.status = ScanStatus.FAILED
            scan.error_message = "Project processing failed. Retrieve the original project and review its dependencies."
            scan.progress_message = scan.error_message
            db.commit()
        raise
    finally:
        db.close()


def _bound_project_provenance(scan, project):
    from ...education.latex_project_conversion import public_project_provenance

    structure = getattr(scan.result, "structure", None)
    record = structure.get("latex_project", {}) if isinstance(structure, dict) else {}
    provenance = (
        public_project_provenance(record.get("conversion"))
        if isinstance(record, dict)
        else None
    )
    if (
        not provenance
        or project.issues
        or project.flattened_source is None
        or provenance.get("archive_sha256") != project.archive_digest
        or provenance.get("source_sha256") != project.source_digest
        or provenance.get("analysis_sha256")
        != hashlib.sha256(project.flattened_source.encode("utf-8")).hexdigest()
    ):
        return None
    return provenance


def _project_html(scan, project):
    provenance = _bound_project_provenance(scan, project)
    html = getattr(scan.result, "html_output", None)
    if (
        not provenance
        or provenance.get("status") != "accepted"
        or not isinstance(html, str)
        or not html
    ):
        return None
    data = html.encode("utf-8")
    if len(data) > MAX_ARCHIVE_BYTES or hashlib.sha256(
        data
    ).hexdigest() != provenance.get("output_sha256"):
        return None
    return data


@router.get("/latex/projects/{scan_id}/html")
def download_latex_project_html(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    scan, _, project = _authorized_project(db, scan_id, principal)
    data = _project_html(scan, project)
    if data is None:
        raise HTTPException(404, "Verified project conversion not available")
    return Response(
        data,
        media_type="text/html",
        headers={
            "Content-Disposition": 'attachment; filename="project.html"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'",
            "Cache-Control": "private, no-store",
        },
    )
