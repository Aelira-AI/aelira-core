"""Task17B central durable enqueue boundary tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _db(*, existing=None, dependency=None, credential=None, cloud_file=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = existing

    def get(model, identity):
        name = model.__name__
        if name == "CloudJobQueue":
            return dependency
        if name == "CloudOAuthCredentials":
            return credential
        if name == "CloudFile":
            return cloud_file
        return None

    db.get.side_effect = get
    return db


def test_enqueue_requires_exact_dict_payload_and_registered_type():
    from src.services.job_enqueue_service import JobEnqueueError, enqueue_cloud_job

    db = _db()
    with pytest.raises(JobEnqueueError, match="payload_object_required"):
        enqueue_cloud_job(
            db,
            department_id="dept-1",
            job_type="scan",
            payload=[],
            dedupe_key="scan:file-1:v1",
        )
    with pytest.raises(JobEnqueueError, match="job_type_not_registered"):
        enqueue_cloud_job(
            db,
            department_id="dept-1",
            job_type="unknown",
            payload={},
            dedupe_key="unknown:file-1:v1",
        )


def test_enqueue_rejects_cross_tenant_dependency():
    from src.services.job_enqueue_service import JobEnqueueError, enqueue_cloud_job

    db = _db(dependency=SimpleNamespace(id="parent", department_id="dept-2"))
    with pytest.raises(JobEnqueueError, match="dependency_tenant_mismatch"):
        enqueue_cloud_job(
            db,
            department_id="dept-1",
            job_type="remediate",
            payload={"cloud_file_id": "file-1"},
            depends_on_job_id="parent",
            dedupe_key="remediate:file-1:v1",
        )


@pytest.mark.parametrize(
    ("credential", "provider", "error"),
    [
        (
            SimpleNamespace(
                id="cred-1", department_id="dept-2", provider="canvas", is_active=True
            ),
            "canvas",
            "credential_tenant_mismatch",
        ),
        (
            SimpleNamespace(
                id="cred-1", department_id="dept-1", provider="google", is_active=True
            ),
            "canvas",
            "credential_provider_mismatch",
        ),
    ],
)
def test_enqueue_rejects_credential_authority_mismatch(credential, provider, error):
    from src.services.job_enqueue_service import JobEnqueueError, enqueue_cloud_job

    db = _db(credential=credential)
    with pytest.raises(JobEnqueueError, match=error):
        enqueue_cloud_job(
            db,
            department_id="dept-1",
            job_type="scan",
            payload={"cloud_file_id": "file-1"},
            provider=provider,
            credential_id="cred-1",
            dedupe_key="scan:file-1:v1",
        )


def test_enqueue_copies_payload_and_returns_existing_active_dedupe():
    from src.services.job_enqueue_service import enqueue_cloud_job

    existing = SimpleNamespace(id="existing-job")
    db = _db(existing=existing)
    payload = {"nested": {"course_id": "course-1"}}

    result = enqueue_cloud_job(
        db,
        department_id="dept-1",
        job_type="scan",
        payload=payload,
        provider="canvas",
        dedupe_key="scan:canvas:course-1:file-1:v1",
    )
    payload["nested"]["course_id"] = "mutated"

    assert result is existing
    db.add.assert_not_called()
