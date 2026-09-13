"""Document queue capability never fabricates per-finding auto-fix promises."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.education import remediation_routes as routes
from src.api.education import scan_history_routes as history
from src.db.models import ScanStatus, ScanType

_REAL_SOURCE_RESOLVER = routes._resolve_bound_scan_cloud_file
_REAL_CREDENTIAL_RESOLVER = routes._get_bound_cloud_credential


@pytest.fixture
def context(monkeypatch, tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"synthetic source")
    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        scan_type=ScanType.PDF,
        status=ScanStatus.COMPLETED,
        storage_path=str(source),
        result=SimpleNamespace(
            issues=[{"category": "heading"}],
            compliance_score=95,
            wcag_level="AA",
            critical_issues=1,
            high_issues=0,
            medium_issues=0,
            low_issues=0,
            structure=None,
            suggestions=None,
            ocr_used=False,
            ollama_used=False,
        ),
        file_name="source.pdf",
        pages=1,
        file_size_bytes=16,
        processing_time_ms=1,
        created_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00"),
        completed_at=None,
    )
    principal = SimpleNamespace(
        department_id="department-1",
        user_id="user-1",
        as_legacy_tuple=lambda: (None, "user-1", "department-1"),
    )
    monkeypatch.setattr(routes, "authorize_scan_access", MagicMock(return_value=None))
    monkeypatch.setattr(
        routes, "_resolve_bound_scan_cloud_file", MagicMock(return_value=None)
    )
    return MagicMock(), scan, principal


@pytest.mark.parametrize(
    "scan_type",
    [
        ScanType.PDF,
        ScanType.WORD,
        ScanType.EXCEL,
        ScanType.POWERPOINT,
        ScanType.LATEX,
        ScanType.VIDEO,
        ScanType.MULTIMEDIA,
    ],
)
def test_completed_supported_document_is_eligible_without_finding_flags(
    context, scan_type
):
    db, scan, principal = context
    scan.scan_type = scan_type
    assert routes.document_remediation_eligibility(db, scan, principal) == {
        "eligible": True,
        "reason": None,
    }
    assert scan.result.issues == [{"category": "heading"}]
    db.commit.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "scan_type",
    [
        ScanType.IMAGE,
        ScanType.BATCH,
        ScanType.CODE,
        ScanType.WEBSITE,
        ScanType.CANVAS_CONTENT,
    ],
)
def test_non_document_workflows_are_not_advertised_as_document_queue(
    context, scan_type
):
    db, scan, principal = context
    scan.scan_type = scan_type
    assert routes.document_remediation_eligibility(db, scan, principal) == {
        "eligible": False,
        "reason": "unsupported_document_type",
    }


@pytest.mark.parametrize(
    "status", [ScanStatus.PENDING, ScanStatus.PROCESSING, ScanStatus.FAILED]
)
def test_incomplete_scan_is_not_eligible(context, status):
    db, scan, principal = context
    scan.status = status
    assert (
        routes.document_remediation_eligibility(db, scan, principal)["reason"]
        == "scan_not_completed"
    )


def test_missing_results_are_not_eligible(context):
    db, scan, principal = context
    scan.result = None
    assert (
        routes.document_remediation_eligibility(db, scan, principal)["reason"]
        == "scan_results_unavailable"
    )


def test_missing_source_refused_by_both_read_contract_and_enqueue(context):
    db, scan, principal = context
    scan.storage_path += ".missing"
    assert routes.document_remediation_eligibility(db, scan, principal) == {
        "eligible": False,
        "reason": "source_file_unavailable",
    }
    with pytest.raises(HTTPException, match="Original file not available"):
        routes._enqueue_scan_remediation(db, scan=scan, principal=principal, options={})
    db.commit.assert_not_called()


def test_authorization_failure_is_not_converted_to_capability(context, monkeypatch):
    db, scan, principal = context
    monkeypatch.setattr(
        routes,
        "authorize_scan_access",
        MagicMock(side_effect=HTTPException(404, "Scan not found")),
    )
    with pytest.raises(HTTPException) as error:
        routes.document_remediation_eligibility(db, scan, principal)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_scan_detail_exposes_authoritative_contract_without_mutating_issues(
    context, monkeypatch
):
    db, scan, principal = context
    monkeypatch.setattr(
        history.ScanService, "get_scan_with_result", MagicMock(return_value=scan)
    )
    monkeypatch.setattr(history, "authorize_scan_access", MagicMock(return_value=None))
    response = await history.get_scan_details("scan-1", db, principal)
    assert response["scan"]["remediation_eligibility"] == {
        "eligible": True,
        "reason": None,
    }
    assert response["scan"]["result"]["issues"] == [{"category": "heading"}]


@pytest.mark.parametrize("provider", ["google", "microsoft", "canvas", "blackboard"])
def test_bound_active_supported_provider_can_supply_missing_local_source(
    context, monkeypatch, provider
):
    db, scan, principal = context
    scan.storage_path = None
    cloud = SimpleNamespace(
        provider=provider, credential_id="credential-1", provider_file_id="file-1"
    )
    monkeypatch.setattr(
        routes, "_resolve_bound_scan_cloud_file", MagicMock(return_value=cloud)
    )
    monkeypatch.setattr(
        routes,
        "_get_bound_cloud_credential",
        MagicMock(return_value=SimpleNamespace(is_active=True)),
    )
    assert (
        routes.document_remediation_eligibility(db, scan, principal)["eligible"] is True
    )


@pytest.mark.parametrize(
    "provider,active,file_id,reason",
    [
        ("moodle", True, "file-1", "unsupported_source_provider"),
        ("brightspace", True, "file-1", "unsupported_source_provider"),
        ("canvas", False, "file-1", "source_file_unavailable"),
        ("canvas", True, None, "source_file_unavailable"),
    ],
)
def test_unusable_bound_source_is_not_eligible(
    context, monkeypatch, provider, active, file_id, reason
):
    db, scan, principal = context
    cloud = SimpleNamespace(
        provider=provider, credential_id="credential-1", provider_file_id=file_id
    )
    monkeypatch.setattr(
        routes, "_resolve_bound_scan_cloud_file", MagicMock(return_value=cloud)
    )
    monkeypatch.setattr(
        routes,
        "_get_bound_cloud_credential",
        MagicMock(return_value=SimpleNamespace(is_active=active)),
    )
    assert routes.document_remediation_eligibility(db, scan, principal) == {
        "eligible": False,
        "reason": reason,
    }


@pytest.mark.parametrize("mismatch", ["department_id", "provider", "id"])
def test_real_credential_binding_rejects_mismatch(context, monkeypatch, mismatch):
    db, scan, principal = context
    cloud = SimpleNamespace(
        provider="canvas", credential_id="credential-1", provider_file_id="file-1"
    )
    credential = SimpleNamespace(
        id="credential-1",
        department_id=principal.department_id,
        provider="canvas",
        is_active=True,
    )
    setattr(credential, mismatch, "not-the-bound-value")
    db.query.return_value.filter.return_value.first.return_value = credential
    monkeypatch.setattr(
        routes, "_resolve_bound_scan_cloud_file", MagicMock(return_value=cloud)
    )
    monkeypatch.setattr(
        routes, "_get_bound_cloud_credential", _REAL_CREDENTIAL_RESOLVER
    )
    assert routes.document_remediation_eligibility(db, scan, principal) == {
        "eligible": False,
        "reason": "source_file_unavailable",
    }


def test_real_source_resolver_hides_ambiguous_links(context, monkeypatch):
    db, scan, principal = context
    db.query.return_value.filter.return_value.limit.return_value.all.return_value = [
        SimpleNamespace(id="link-1"),
        SimpleNamespace(id="link-2"),
    ]
    monkeypatch.setattr(routes, "_resolve_bound_scan_cloud_file", _REAL_SOURCE_RESOLVER)
    with pytest.raises(HTTPException) as error:
        routes.document_remediation_eligibility(db, scan, principal)
    assert error.value.status_code == 404
    assert error.value.detail == "Scan not found"


def test_eligibility_is_rechecked_when_source_disappears(context, monkeypatch):
    db, scan, principal = context
    assert (
        routes.document_remediation_eligibility(db, scan, principal)["eligible"] is True
    )
    monkeypatch.setattr(routes.os.path, "isfile", lambda _path: False)
    with pytest.raises(HTTPException) as error:
        routes._enqueue_scan_remediation(db, scan=scan, principal=principal, options={})
    assert error.value.status_code == 400
    db.commit.assert_not_called()
