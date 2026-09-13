"""Authenticated reading-order previews use independently verified document bytes."""

import hashlib
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pikepdf
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import review_routes
from src.auth.dependencies import (
    AuthenticatedPrincipal,
    get_authenticated_principal,
    get_required_api_key,
)
from src.db.database import get_db_dependency
from src.db.models import CloudFile, RemediationArtifact, Scan, ScanType, UserRole
from src.services.remediation_artifact_service import RemediationArtifactService

pytestmark = pytest.mark.unit


def _pdf(text, pages=1):
    from pikepdf import Array, Dictionary, Name

    with pikepdf.new() as document:
        root = document.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        document.Root.StructTreeRoot = root
        children, numbers = [], []
        for number in range(pages):
            page = document.add_blank_page(page_size=(400, 500))
            page.obj.StructParents = number
            page.obj.Resources = Dictionary(
                Font=Dictionary(
                    F1=Dictionary(
                        Type=Name.Font,
                        Subtype=Name.Type1,
                        BaseFont=Name.Helvetica,
                    )
                )
            )
            page.obj.Contents = document.make_stream(
                f"/P <</MCID 0>> BDC BT /F1 12 Tf 30 460 Td ({text} page {number + 1}) Tj ET EMC".encode()
            )
            owner = document.make_indirect(
                Dictionary(
                    Type=Name.StructElem,
                    S=Name.P,
                    P=root,
                    Pg=page.obj,
                    K=0,
                )
            )
            children.append(owner)
            numbers.extend([number, Array([owner])])
        root.K = Array(children)
        root.ParentTree = Dictionary(Nums=Array(numbers))
        output = io.BytesIO()
        document.save(output)
        return output.getvalue()


@pytest.fixture
def preview(tmp_path, monkeypatch):
    department_id, scan_id, artifact_id = (str(uuid4()) for _ in range(3))
    source_bytes, saved_bytes = _pdf("Original", 2), _pdf("Saved")
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(source_bytes)
    scan = SimpleNamespace(
        id=scan_id,
        department_id=department_id,
        scan_type=ScanType.PDF,
        storage_path=str(source_path),
        file_name="source.pdf",
        file_size_bytes=len(source_bytes),
        file_hash=hashlib.sha256(source_bytes).hexdigest(),
        current_remediation_artifact_id=artifact_id,
        result={"output_path": "/never/read/legacy-output.pdf"},
    )
    storage_key = f"{department_id}/{scan_id}/{artifact_id}/{uuid4()}.pdf"
    artifact_path = tmp_path / "artifacts" / storage_key
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(saved_bytes)
    artifact = SimpleNamespace(
        id=artifact_id,
        department_id=department_id,
        scan_id=scan_id,
        scan_type="PDF",
        cloud_file_id=None,
        provider="local",
        filename="saved.pdf",
        mime_type="application/pdf",
        size_bytes=len(saved_bytes),
        sha256=hashlib.sha256(saved_bytes).hexdigest(),
        storage_backend="local",
        storage_key=storage_key,
        lifecycle_status="available",
        cleanup_claimed_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        review_status="pending",
    )
    service = RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=100 * 1024 * 1024,
        retention_days=1,
        staging_grace_seconds=60,
    )
    # Retain real current/lifecycle/hash/size/MIME/descriptor verification; only
    # replace database graph locking, which has its own PostgreSQL coverage.
    lock = MagicMock(
        return_value=(SimpleNamespace(id=department_id), scan, None, None, artifact)
    )
    monkeypatch.setattr(service, "_lock_existing_artifact", lock)
    monkeypatch.setattr(
        review_routes.RemediationArtifactService, "from_settings", lambda: service
    )
    state = SimpleNamespace(
        scan=scan,
        artifact=artifact,
        service=service,
        lock=lock,
        source_path=source_path,
        artifact_path=artifact_path,
        source_bytes=source_bytes,
        saved_bytes=saved_bytes,
        authenticated=True,
    )
    db = MagicMock()

    def query(model):
        result = MagicMock()
        if model is Scan:
            result.filter.return_value.first.return_value = state.scan
        elif model is RemediationArtifact:
            result.filter.return_value.one_or_none.return_value = state.artifact
        elif model is CloudFile:
            result.filter.return_value.first.return_value = state.cloud_file
        return result

    db.query.side_effect = query
    state.db = db
    state.principal = AuthenticatedPrincipal(
        None, "reviewer", department_id, UserRole.FACULTY, "session"
    )
    state.cloud_file = None
    app = FastAPI()
    app.include_router(review_routes.router, prefix="/api")

    def auth():
        if not state.authenticated:
            raise HTTPException(status_code=401, detail="Authentication required")
        return (None, "reviewer", department_id)

    app.dependency_overrides[get_required_api_key] = auth

    def principal_auth():
        auth()
        return state.principal

    app.dependency_overrides[get_authenticated_principal] = principal_auth
    app.dependency_overrides[get_db_dependency] = lambda: db
    state.client = TestClient(app)
    state.url = f"/api/reviews/{scan_id}/reading-order"
    return state


@pytest.mark.parametrize("scope", ["same", "other", "missing", "provider", "tenant"])
def test_course_scoped_preview_authorizes_before_reading_bytes(
    preview, monkeypatch, scope
):
    preview.principal = AuthenticatedPrincipal(
        None,
        "instructor",
        preview.scan.department_id,
        UserRole.FACULTY,
        "lti",
        lti_course_id="course-a",
        lti_staff_role="Instructor",
    )
    preview.cloud_file = (
        None
        if scope == "missing"
        else SimpleNamespace(
            last_scan_id=preview.scan.id,
            department_id="other" if scope == "tenant" else preview.scan.department_id,
            provider="google" if scope == "provider" else "canvas",
            provider_parent_id="course-b" if scope == "other" else "course-a",
        )
    )
    reader = MagicMock(wraps=review_routes._read_verified_source)
    monkeypatch.setattr(review_routes, "_read_verified_source", reader)
    response = preview.client.get(preview.url)
    assert response.status_code == (200 if scope == "same" else 404)
    assert reader.call_count == (1 if scope == "same" else 0)
    assert preview.lock.call_count == (1 if scope == "same" else 0)


def test_verified_source_and_pending_saved_artifact_are_previewable(preview):
    response = preview.client.get(preview.url)
    assert response.status_code == 200
    payload = response.json()
    assert payload["scan_id"] == preview.scan.id
    assert payload["artifact_id"] == preview.artifact.id
    assert payload["page_number"] == 1
    for name, data, text, pages in (
        ("source", preview.source_bytes, "Original", 2),
        ("saved", preview.saved_bytes, "Saved", 1),
    ):
        snapshot = payload[name]
        assert snapshot["status"] == "available"
        assert snapshot["reason"] is None
        assert snapshot["sha256"] == hashlib.sha256(data).hexdigest()
        assert snapshot["page_count"] == pages
        assert snapshot["page_number"] == 1
        assert snapshot["width"] == 400
        assert snapshot["height"] == 500
        assert snapshot["preview_png_base64"].startswith("iVBOR")
        assert text in " ".join(block["text"] for block in snapshot["blocks"])
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    preview.db.commit.assert_not_called()
    preview.db.add.assert_not_called()


def test_authentication_required(preview):
    preview.authenticated = False
    assert preview.client.get(preview.url).status_code == 401
    preview.lock.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_tenant_or_missing_scan_404_before_any_bytes(preview, monkeypatch, missing):
    reader = MagicMock(side_effect=AssertionError("must not read source"))
    monkeypatch.setattr(review_routes, "_read_verified_source", reader)
    if missing:
        preview.scan = None
    else:
        preview.scan.department_id = str(uuid4())
    assert preview.client.get(preview.url).status_code == 404
    reader.assert_not_called()
    preview.lock.assert_not_called()


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("missing", "source_missing_or_unsafe"),
        ("tampered", "source_integrity_failed"),
        ("metadata", "source_metadata_unavailable"),
        ("oversized", "file_too_large"),
    ],
)
def test_source_failure_does_not_hide_saved(preview, failure, reason):
    if failure == "missing":
        preview.scan.storage_path = str(preview.source_path.parent / "missing.pdf")
    elif failure == "tampered":
        preview.source_path.write_bytes(b"!" + preview.source_bytes[1:])
    elif failure == "metadata":
        preview.scan.file_hash = None
    else:
        preview.scan.file_size_bytes = 50 * 1024 * 1024 + 1
    response = preview.client.get(preview.url)
    assert response.status_code == 200
    assert response.json()["source"]["reason"] == reason
    assert response.json()["source"]["sha256"] is None
    assert response.json()["saved"]["status"] == "available"
    assert str(preview.source_path.parent) not in response.text


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("missing", "saved_artifact_integrity_failed"),
        ("tampered", "saved_artifact_integrity_failed"),
        ("expired", "saved_artifact_expired"),
        ("superseded", "saved_artifact_unavailable"),
        ("cleanup", "saved_artifact_unavailable"),
        ("noartifact", "no_saved_artifact"),
        ("missingrecord", "saved_artifact_unavailable"),
        ("oversized", "file_too_large"),
    ],
)
def test_saved_failure_does_not_hide_source(preview, failure, reason):
    if failure == "missing":
        preview.artifact.storage_key = (
            preview.artifact.storage_key.rsplit("/", 1)[0] + f"/{uuid4()}.pdf"
        )
    elif failure == "tampered":
        preview.artifact_path.write_bytes(b"!" + preview.saved_bytes[1:])
    elif failure == "expired":
        preview.artifact.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif failure == "superseded":
        preview.artifact.lifecycle_status = "superseded"
    elif failure == "cleanup":
        preview.artifact.cleanup_claimed_at = datetime.now(timezone.utc)
    elif failure == "noartifact":
        preview.scan.current_remediation_artifact_id = None
    elif failure == "missingrecord":
        preview.artifact = None
    else:
        preview.artifact.size_bytes = 50 * 1024 * 1024 + 1
    response = preview.client.get(preview.url)
    assert response.status_code == 200
    assert response.json()["source"]["status"] == "available"
    assert response.json()["saved"]["reason"] == reason
    assert response.json()["saved"]["sha256"] is None
    assert str(preview.artifact_path.parent) not in response.text


def test_page_range_is_independent_and_retains_document_metadata(preview):
    response = preview.client.get(preview.url, params={"page": 2})
    payload = response.json()
    assert payload["source"]["status"] == "available"
    assert payload["saved"]["status"] == "unavailable"
    assert payload["saved"]["reason"] == "invalid_page"
    assert payload["saved"]["page_count"] == 1
    assert payload["saved"]["page_number"] == 2
    assert payload["saved"]["sha256"] == preview.artifact.sha256


@pytest.mark.parametrize("page", [0, -1, "abc"])
def test_invalid_page_rejected(preview, page):
    assert preview.client.get(preview.url, params={"page": page}).status_code == 422
    preview.lock.assert_not_called()


def test_non_pdf_source_is_explicit_and_does_not_hide_saved(preview):
    content = b"A text document"
    preview.source_path.write_bytes(content)
    preview.scan.file_hash = hashlib.sha256(content).hexdigest()
    preview.scan.file_size_bytes = len(content)
    response = preview.client.get(preview.url)
    assert response.json()["source"]["reason"] == "unsupported_format"
    assert response.json()["saved"]["status"] == "available"


def test_cloud_artifact_requires_current_pointer(preview):
    preview.artifact.cloud_file_id = str(uuid4())
    preview.artifact.provider = "google"
    cloud = SimpleNamespace(
        id=preview.artifact.cloud_file_id,
        provider="google",
        current_remediation_artifact_id=str(uuid4()),
    )
    preview.lock.return_value = (
        SimpleNamespace(id=preview.scan.department_id),
        preview.scan,
        cloud,
        None,
        preview.artifact,
    )
    response = preview.client.get(preview.url)
    assert response.json()["saved"]["reason"] == "saved_artifact_unavailable"
    assert response.json()["source"]["status"] == "available"


@pytest.mark.parametrize("field", ["department_id", "scan_id"])
def test_saved_artifact_authority_mismatch_is_unavailable(preview, field):
    setattr(preview.artifact, field, str(uuid4()))
    response = preview.client.get(preview.url)
    assert response.json()["saved"]["reason"] == "saved_artifact_unavailable"
    assert response.json()["source"]["status"] == "available"


def test_current_cloud_artifact_can_be_previewed_before_approval(preview):
    preview.artifact.cloud_file_id = str(uuid4())
    preview.artifact.provider = "google"
    cloud = SimpleNamespace(
        id=preview.artifact.cloud_file_id,
        provider="google",
        current_remediation_artifact_id=preview.artifact.id,
    )
    preview.lock.return_value = (
        SimpleNamespace(id=preview.scan.department_id),
        preview.scan,
        cloud,
        None,
        preview.artifact,
    )
    response = preview.client.get(preview.url)
    assert response.json()["saved"]["status"] == "available"
    assert preview.artifact.review_status == "pending"


def test_local_pointer_changed_during_lock_is_unavailable(preview):
    locked = preview.lock.return_value

    def supersede(*args, **kwargs):
        preview.scan.current_remediation_artifact_id = str(uuid4())
        return locked

    preview.lock.side_effect = supersede
    response = preview.client.get(preview.url)
    assert response.json()["saved"]["reason"] == "saved_artifact_unavailable"
    assert response.json()["source"]["status"] == "available"


def test_source_symlink_is_not_followed(preview):
    alias = preview.source_path.parent / "alias.pdf"
    alias.symlink_to(preview.source_path)
    preview.scan.storage_path = str(alias)
    response = preview.client.get(preview.url)
    assert response.json()["source"]["reason"] == "source_missing_or_unsafe"
    assert response.json()["saved"]["status"] == "available"


def test_saved_stream_changed_after_verification_is_rejected(preview, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def changed(*args, **kwargs):
        yield io.BytesIO(b"!" + preview.saved_bytes[1:])

    monkeypatch.setattr(preview.service, "open_verified", changed)
    response = preview.client.get(preview.url)
    assert response.json()["saved"]["reason"] == "saved_artifact_integrity_failed"
    assert response.json()["source"]["status"] == "available"


def test_extractor_exception_is_safe_and_independent(preview, monkeypatch):
    inspect = review_routes.inspect_pdf_reading_order

    def fail_source(content, page_number):
        if content == preview.source_bytes:
            raise RuntimeError(f"parser error in {preview.source_path}")
        return inspect(content, page_number)

    monkeypatch.setattr(review_routes, "inspect_pdf_reading_order", fail_source)
    response = preview.client.get(preview.url)
    assert response.json()["source"]["reason"] == "extraction_failed"
    assert response.json()["saved"]["status"] == "available"
    assert str(preview.source_path) not in response.text


def test_verified_non_pdf_saved_artifact_is_explicit(preview):
    content = b"<!doctype html><html><body>Saved</body></html>"
    preview.scan.scan_type = ScanType.WEBSITE
    preview.artifact.scan_type = "WEBSITE"
    preview.artifact.filename = "saved.html"
    preview.artifact.mime_type = "text/html"
    preview.artifact.storage_key = (
        preview.artifact.storage_key.removesuffix(".pdf") + ".html"
    )
    preview.artifact_path.with_suffix(".html").write_bytes(content)
    preview.artifact.size_bytes = len(content)
    preview.artifact.sha256 = hashlib.sha256(content).hexdigest()
    response = preview.client.get(preview.url)
    assert response.json()["saved"]["reason"] == "unsupported_format"
    assert response.json()["saved"]["sha256"] == preview.artifact.sha256
    assert response.json()["source"]["status"] == "available"
