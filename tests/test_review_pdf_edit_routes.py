"""Authenticated PDF candidate routes bind verified bytes to explicit review state."""

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.api import review_routes
from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import CloudFile, UserRole
from src.services.remediation_artifact_service import (
    ArtifactPublicationRetry,
    ArtifactPublicationRetryable,
)
from test_pdf_review_candidate import table_pdf

pytestmark = pytest.mark.unit
pytest_plugins = ["test_review_reading_order"]


def _configure(preview, *, source_kind="original", cloud_files=()):
    original_query = preview.db.query.side_effect

    def query(model):
        result = original_query(model)
        if model is CloudFile:
            result.filter.return_value.all.return_value = list(cloud_files)
        return result

    preview.db.query.side_effect = query
    content = preview.source_bytes if source_kind == "original" else preview.saved_bytes
    precondition = {
        "version": 1,
        "source_kind": source_kind,
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "expected_artifact_id": preview.artifact.id,
        "state_digest": "a" * 64,
        "scope_kind": "department",
        "course_id": None,
        "cloud_file_id": cloud_files[0].id if cloud_files else None,
    }
    preview.service.capture_edit_precondition = MagicMock(return_value=precondition)
    preview.service.claim_and_publish_stream = MagicMock(
        return_value=SimpleNamespace(artifact_id="new-artifact")
    )
    return precondition


def test_cloud_edit_selects_cloud_pointer_and_requires_exact_context(preview):
    cloud = SimpleNamespace(
        id=str(uuid4()),
        department_id=preview.scan.department_id,
        last_scan_id=preview.scan.id,
        provider="google",
        current_remediation_artifact_id=preview.artifact.id,
    )
    preview.scan.current_remediation_artifact_id = "unrelated-local-artifact"
    preview.scan.document_source = "cloud_file"
    preview.scan.document_id = cloud.id
    preview.artifact.cloud_file_id = cloud.id
    preview.artifact.provider = cloud.provider
    preview.lock.return_value = (
        SimpleNamespace(id=preview.scan.department_id),
        preview.scan,
        cloud,
        None,
        preview.artifact,
    )
    expected = _configure(preview, source_kind="saved", cloud_files=(cloud,))
    url = preview.url.replace("reading-order", "pdf-edit-targets")
    assert preview.client.get(url, params={"source_kind": "saved"}).status_code == 409
    assert (
        preview.client.get(
            url, params={"source_kind": "saved", "cloud_file_id": "other-cloud"}
        ).status_code
        == 404
    )
    inspected = preview.client.get(
        url, params={"source_kind": "saved", "cloud_file_id": cloud.id}
    )
    assert inspected.status_code == 200, inspected.text
    assert (
        inspected.json()["precondition"]["expected_artifact_id"] == preview.artifact.id
    )
    target = next(
        item for item in inspected.json()["targets"] if item["can_set_heading"]
    )
    response = preview.client.post(
        url.replace("pdf-edit-targets", "pdf-edit-candidates"),
        json={
            "source_kind": "saved",
            "cloud_file_id": cloud.id,
            "expected_artifact_id": preview.artifact.id,
            "expected_source_sha256": expected["source_sha256"],
            "expected_state_digest": expected["state_digest"],
            "operation": {
                "kind": "heading",
                "target_id": target["target_id"],
                "level": 2,
            },
        },
    )
    assert response.status_code == 201, response.text
    assert (
        preview.service.claim_and_publish_stream.call_args.kwargs["cloud_file_id"]
        == cloud.id
    )


def test_review_edit_inspection_and_save_use_verified_original(preview):
    expected = _configure(preview)
    url = preview.url.replace("reading-order", "pdf-edit-targets")
    inspected = preview.client.get(url)
    assert inspected.status_code == 200
    assert inspected.headers["cache-control"] == "no-store"
    target = next(
        item for item in inspected.json()["targets"] if item["can_set_heading"]
    )
    saved = preview.client.post(
        url.replace("pdf-edit-targets", "pdf-edit-candidates"),
        json={
            "source_kind": "original",
            "cloud_file_id": None,
            "expected_artifact_id": expected["expected_artifact_id"],
            "expected_source_sha256": expected["source_sha256"],
            "expected_state_digest": expected["state_digest"],
            "operation": {
                "kind": "heading",
                "target_id": target["target_id"],
                "level": 2,
            },
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["review_status"] == "pending"
    kwargs = preview.service.claim_and_publish_stream.call_args.kwargs
    assert kwargs["edit_precondition"] == expected
    assert kwargs["edit_provenance"]["source_sha256"] == expected["source_sha256"]
    assert kwargs["provider_result"] is None
    assert kwargs["claimed_sha256"] != expected["source_sha256"]


def test_saved_table_edit_uses_exact_saved_artifact_bytes(preview, tmp_path):
    table = table_pdf(tmp_path)
    preview.artifact_path.write_bytes(table)
    preview.artifact.size_bytes = len(table)
    preview.artifact.sha256 = hashlib.sha256(table).hexdigest()
    preview.saved_bytes = table
    expected = _configure(preview, source_kind="saved")
    url = preview.url.replace("reading-order", "pdf-edit-targets")
    inspected = preview.client.get(url, params={"source_kind": "saved"})
    assert inspected.status_code == 200, inspected.text
    target = next(
        item for item in inspected.json()["targets"] if item["role"] == "Table"
    )
    assert target["can_set_column_headers"]
    saved = preview.client.post(
        url.replace("pdf-edit-targets", "pdf-edit-candidates"),
        json={
            "source_kind": "saved",
            "cloud_file_id": None,
            "expected_artifact_id": expected["expected_artifact_id"],
            "expected_source_sha256": expected["source_sha256"],
            "expected_state_digest": expected["state_digest"],
            "operation": {
                "kind": "table_column_headers",
                "target_id": target["target_id"],
            },
        },
    )
    assert saved.status_code == 201, saved.text
    kwargs = preview.service.claim_and_publish_stream.call_args.kwargs
    assert kwargs["edit_provenance"]["operation"]["kind"] == "table_column_headers"


@pytest.mark.parametrize("failure", ["unauthenticated", "department", "course"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_review_edit_denies_access_before_verified_source(preview, failure, method):
    expected = _configure(preview)
    if failure == "unauthenticated":
        preview.authenticated = False
    elif failure == "department":
        preview.principal = AuthenticatedPrincipal(
            None, "reviewer", "other-department", UserRole.FACULTY, "session"
        )
    else:
        preview.principal = AuthenticatedPrincipal(
            None,
            "instructor",
            preview.scan.department_id,
            UserRole.FACULTY,
            "lti",
            lti_course_id="wrong-course",
            lti_staff_role="Instructor",
        )
    if method == "GET":
        response = preview.client.get(
            preview.url.replace("reading-order", "pdf-edit-targets")
        )
    else:
        response = preview.client.post(
            preview.url.replace("reading-order", "pdf-edit-candidates"),
            json={
                "source_kind": "original",
                "cloud_file_id": None,
                "expected_artifact_id": expected["expected_artifact_id"],
                "expected_source_sha256": expected["source_sha256"],
                "expected_state_digest": expected["state_digest"],
                "operation": {"kind": "heading", "target_id": "x", "level": 2},
            },
        )
    assert response.status_code in (401, 404)
    preview.service.capture_edit_precondition.assert_not_called()
    preview.service.claim_and_publish_stream.assert_not_called()


def test_stale_and_malformed_edit_requests_never_publish(preview):
    expected = _configure(preview)
    url = preview.url.replace("reading-order", "pdf-edit-candidates")
    payload = {
        "source_kind": "original",
        "cloud_file_id": None,
        "expected_artifact_id": None,
        "expected_source_sha256": expected["source_sha256"],
        "expected_state_digest": expected["state_digest"],
        "operation": {"kind": "heading", "target_id": "x", "level": 2},
    }
    assert preview.client.post(url, json=payload).status_code == 409
    payload["expected_artifact_id"] = expected["expected_artifact_id"]
    for bad_level in (True, 2.0, "2"):
        payload["operation"]["level"] = bad_level
        assert preview.client.post(url, json=payload).status_code == 422
    payload["operation"] = {"kind": "order", "target_id": "x", "children": ["a"] * 2001}
    assert preview.client.post(url, json=payload).status_code == 422
    payload["operation"] = {"kind": "heading", "target_id": "x", "level": 2}
    payload["source_path"] = "/untrusted.pdf"
    assert preview.client.post(url, json=payload).status_code == 422
    preview.service.claim_and_publish_stream.assert_not_called()


def test_storage_failure_returns_safe_error_without_changing_current(preview):
    expected = _configure(preview)
    target = next(
        item
        for item in review_routes.inspect_pdf_edit_targets(
            preview.source_bytes, expected["source_sha256"]
        )
        if item["can_set_heading"]
    )
    preview.service.claim_and_publish_stream.side_effect = ArtifactPublicationRetryable(
        ArtifactPublicationRetry("failed-artifact", cleanup_complete=True)
    )
    original_id = preview.scan.current_remediation_artifact_id
    response = preview.client.post(
        preview.url.replace("reading-order", "pdf-edit-candidates"),
        json={
            "source_kind": "original",
            "cloud_file_id": None,
            "expected_artifact_id": expected["expected_artifact_id"],
            "expected_source_sha256": expected["source_sha256"],
            "expected_state_digest": expected["state_digest"],
            "operation": {
                "kind": "heading",
                "target_id": target["target_id"],
                "level": 2,
            },
        },
    )
    assert response.status_code == 503
    assert preview.scan.current_remediation_artifact_id == original_id
