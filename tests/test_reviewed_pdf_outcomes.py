"""Reviewed PDF replay changes only selected source-bound content without AI."""

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pikepdf
import pymupdf as fitz
import pytest
from starlette.requests import Request

from src.education.remediation.base import FixedIssue, RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.reviewed_pdf import recover_reviewed_pdf_issues
from src.services.scan_fix_service import (
    artifact_output_membership_blockers,
    bind_fix_review_decision,
    build_output_membership,
    build_scan_fix,
    inherit_output_membership,
)


def _fix(identifier, content="Description"):
    row = build_scan_fix(
        "scan",
        FixedIssue(
            issue_id=identifier,
            category="alt_text",
            severity="high",
            description="Missing alt",
            fixed_content=content,
            fix_method="ai_vision",
            confidence=0.8,
            needs_review=True,
        ),
    )
    row.review_status = "approved"
    bind_fix_review_decision(row, "approve")
    return row


def test_rejecting_or_editing_applied_fix_invalidates_output_membership():
    first, second = _fix("first"), _fix("second")
    artifact = SimpleNamespace(
        sha256="b" * 64,
        provider_result={
            "reviewed_output_membership": build_output_membership(
                "b" * 64, "a" * 64, [first, second]
            )
        },
    )
    assert (
        artifact_output_membership_blockers(
            artifact, [first, second], source_sha256="a" * 64
        )
        == []
    )
    second.review_status = "rejected"
    bind_fix_review_decision(second, "reject")
    assert artifact_output_membership_blockers(
        artifact, [first, second], source_sha256="a" * 64
    ) == ["reviewed_changes_not_in_output"]
    second.fixed_content = "An edited and separately approved description"
    second.review_status = "approved"
    bind_fix_review_decision(second, "approve")
    assert artifact_output_membership_blockers(
        artifact, [first, second], source_sha256="a" * 64
    ) == ["reviewed_changes_not_in_output"]
    second.review_status = "rejected"
    regenerated = SimpleNamespace(
        sha256="c" * 64,
        provider_result={
            "reviewed_output_membership": build_output_membership(
                "c" * 64, "a" * 64, [first]
            )
        },
    )
    assert (
        artifact_output_membership_blockers(
            regenerated, [first, second], source_sha256="a" * 64
        )
        == []
    )
    assert artifact_output_membership_blockers(
        regenerated, [first, second], source_sha256="d" * 64
    ) == ["reviewed_changes_not_in_output"]


def test_missing_membership_is_unknown_and_forged_receipt_is_rejected():
    fix = _fix("first")
    artifact = SimpleNamespace(sha256="b" * 64, provider_result={})
    assert artifact_output_membership_blockers(
        artifact, [fix], source_sha256="a" * 64
    ) == ["output_membership_unrecorded"]
    receipt = build_output_membership("b" * 64, "a" * 64, [fix])
    assert build_output_membership("bad", "a" * 64, [fix]) is None
    assert build_output_membership("b" * 64, "a" * 64, [fix, fix]) is None
    for mutation in (
        {"version": True},
        {"source_sha256": "c" * 64},
        {"extra": "forged"},
        {"fixes": []},
    ):
        artifact.provider_result = {
            "reviewed_output_membership": {**receipt, **mutation}
        }
        assert artifact_output_membership_blockers(
            artifact, [fix], source_sha256="a" * 64
        )


def test_manual_edit_preserves_applied_members_and_cannot_erase_rejected_bytes():
    first, second = _fix("first"), _fix("second")
    original = build_output_membership("b" * 64, "a" * 64, [first, second])
    predecessor = SimpleNamespace(
        sha256="b" * 64, provider_result={"reviewed_output_membership": original}
    )
    inherited = inherit_output_membership(
        predecessor, source_sha256="a" * 64, output_sha256="c" * 64
    )
    manual = SimpleNamespace(
        sha256="c" * 64, provider_result={"reviewed_output_membership": inherited}
    )
    assert inherited == {**original, "output_sha256": "c" * 64}
    assert (
        artifact_output_membership_blockers(
            manual, [first, second], source_sha256="a" * 64
        )
        == []
    )
    second.review_status = "rejected"
    assert artifact_output_membership_blockers(
        manual, [first, second], source_sha256="a" * 64
    ) == ["reviewed_changes_not_in_output"]
    assert (
        inherit_output_membership(
            predecessor, source_sha256="d" * 64, output_sha256="c" * 64
        )
        is None
    )
    predecessor.sha256 = "d" * 64
    assert (
        inherit_output_membership(
            predecessor, source_sha256="a" * 64, output_sha256="c" * 64
        )
        is None
    )
    predecessor.sha256 = "b" * 64
    for malformed in (
        {**original, "version": True},
        {**original, "fixes": list(reversed(original["fixes"]))},
        {**original, "fixes": [original["fixes"][0]] * 2},
    ):
        predecessor.provider_result = {"reviewed_output_membership": malformed}
        assert (
            inherit_output_membership(
                predecessor, source_sha256="a" * 64, output_sha256="c" * 64
            )
            is None
        )


@pytest.mark.parametrize(
    "change",
    [
        {"id": "source-1"},
        {"description": "Different"},
        {"location": "Other"},
        {"category": "link"},
    ],
)
def test_reviewed_source_metadata_requires_exact_original_finding(change):
    source = {
        "message": "Image missing alternative text",
        "category": "alt_text",
        "location": "Page 1, Image 1",
        "image_index": 0,
    }
    selected = {
        "id": "source-0",
        "description": source["message"],
        "category": "alt_text",
        "location": source["location"],
        "fixed_content": "Reviewed",
    }
    assert (
        recover_reviewed_pdf_issues([selected], [source])[0]["metadata"]["image_index"]
        == 0
    )
    with pytest.raises(ValueError, match="source_binding_unavailable"):
        recover_reviewed_pdf_issues([{**selected, **change}], [source])


def test_legacy_wcag_image_row_recovers_identity_without_trusting_ai_label():
    source = {
        "message": 'AI-Generated Alt Text: "Old suggestion"',
        "rule": "WCAG 1.1.1",
        "location": "Page 1, Image 1",
        "image_index": 0,
    }
    selected = {
        "id": "source-0",
        "description": source["message"],
        "category": "alt_text",
        "location": source["location"],
        "fixed_content": "Reviewed description",
    }
    row = recover_reviewed_pdf_issues([selected], [source])[0]
    assert row["metadata"]["reviewed_fixed_content"] == "Reviewed description"
    assert row["metadata"]["image_index"] == 0


@pytest.mark.parametrize("unselected_change", [False, True])
def test_real_reviewed_alt_replay_keeps_rejected_image_and_root_unmodified(
    tmp_path, monkeypatch, unselected_change
):
    from test_pdf_image_finding_identity import _fixture, _tag, _findings

    raw, source = tmp_path / "raw.pdf", tmp_path / "source.pdf"
    _fixture(raw)

    def remove_alts_and_wrapper(pdf):
        root = pdf.Root.StructTreeRoot
        figures = list(root.K[0].K)
        for figure in figures:
            del figure.Alt
            figure.P = root
        root.K = pikepdf.Array(figures)

    _tag(raw, source, first_only=False, mutate=remove_alts_and_wrapper)
    _, findings = _findings(source)
    issue = findings[0].model_dump(mode="json")
    issue["metadata"]["reviewed_fixed_content"] = "The reviewed first blue rectangle."
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    remediator = PdfRemediator(
        str(source),
        [issue],
        RemediationConfig(
            use_supplied_fixes=True,
            use_ai=False,
            allow_legacy_nested_ai=False,
            output_directory=str(tmp_path / "output"),
            create_backup=False,
        ),
    )
    if unselected_change:
        original_apply = remediator._apply_alt_text_fix

        def mutate_unselected(*args):
            changed = original_apply(*args)
            remediator._pikepdf_doc.Root.StructTreeRoot.K[1].Alt = (
                "An unapproved change"
            )
            return changed

        monkeypatch.setattr(remediator, "_apply_alt_text_fix", mutate_unselected)
    result = remediator.remediate()
    try:
        if unselected_change:
            assert result.verification_passed is False
            assert not result.has_output_claim()
            assert (
                "reviewed_pdf_unselected_finding_changed"
                in result.verification_result.unavailable_checks
            )
            assert hashlib.sha256(source.read_bytes()).hexdigest() == before
            return
        assert result.success and result.verification_passed
        assert len(result.fixed_issues) == 1
        assert (
            result.fixed_issues[0].fixed_content == "The reviewed first blue rectangle."
        )
        assert result.ai_calls_made == 0
        with pikepdf.open(result.output_file) as pdf:
            figures = list(pdf.Root.StructTreeRoot.K)
            assert all(str(figure.S) == "/Figure" for figure in figures)
            assert str(figures[0].Alt) == "The reviewed first blue rectangle."
            assert all("/Alt" not in figure for figure in figures[1:])
        with fitz.open(source) as old, fitz.open(result.output_file) as new:
            assert old[0].get_pixmap().samples == new[0].get_pixmap().samples
            assert old[0].get_text() == new[0].get_text()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    finally:
        result.close_output_claim()


@pytest.mark.asyncio
async def test_reviewed_pdf_route_enqueues_no_ai_and_preserves_specialized_visual_gate(
    monkeypatch,
):
    from src.api.education import remediation_routes as routes

    scan = SimpleNamespace(id="scan", scan_type="PDF", result=object())
    principal = SimpleNamespace(department_id="dept")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"prefer", b"respond-async")],
        }
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(routes.ScanService, "get_scan_with_result", lambda **_: scan)
    authorized = MagicMock()
    monkeypatch.setattr(routes, "authorize_scan_access", authorized)
    monkeypatch.setattr(routes, "_require_editable_source", lambda *_: None)
    enqueue = MagicMock(return_value=SimpleNamespace(id="job", status="pending"))
    monkeypatch.setattr(routes, "_enqueue_scan_remediation", enqueue)
    response = await routes.remediate_reviewed_pdf_scan("scan", request, db, principal)
    assert response.status_code == 202
    authorized.assert_called_once_with(db, scan, principal)
    assert enqueue.call_args.kwargs["approved_fixes_only"] is True
    assert enqueue.call_args.kwargs["options"]["use_ai"] is False
    assert enqueue.call_args.kwargs["options"]["generate_alt_text"] is False
    db.query.return_value.filter.return_value.first.return_value = object()
    with pytest.raises(routes.HTTPException) as error:
        await routes.remediate_reviewed_pdf_scan("scan", request, db, principal)
    assert error.value.status_code == 409
    assert enqueue.call_count == 1


@pytest.mark.parametrize("resolved_id", [None, "other-cloud", "source-cloud"])
def test_cloud_bound_reviewed_source_cannot_fall_back_to_local(
    tmp_path, monkeypatch, resolved_id
):
    from src.api.education import remediation_routes as routes

    source = tmp_path / "source.pdf"
    source.write_bytes(b"original source remains available locally")
    scan = SimpleNamespace(
        id="scan",
        document_source="cloud_file",
        document_id="source-cloud",
        storage_path=str(source),
        result=SimpleNamespace(issues=[{"message": "Source finding"}]),
    )
    principal = SimpleNamespace(
        department_id="dept", auth_method="session", lti_account_wide=False
    )
    cloud = (
        SimpleNamespace(
            id=resolved_id,
            credential_id=None,
            department_id="dept",
            last_scan_id="scan",
        )
        if resolved_id is not None
        else None
    )
    monkeypatch.setattr(routes, "authorize_scan_access", lambda *_: None)
    monkeypatch.setattr(routes, "_require_editable_source", lambda *_: None)
    db = MagicMock()
    db.query.return_value.filter.return_value.limit.return_value.all.return_value = (
        [cloud] if cloud is not None else []
    )
    if resolved_id == "source-cloud":
        assert routes._resolve_remediation_queue_source(
            db, scan=scan, principal=principal
        ) == (cloud, None)
    else:
        with pytest.raises(routes.HTTPException) as error:
            routes._resolve_remediation_queue_source(db, scan=scan, principal=principal)
        assert error.value.status_code == 404


def test_reviewed_rebuild_refuses_to_discard_current_manual_corrections(monkeypatch):
    from src.api.education import remediation_routes as routes

    scan = SimpleNamespace(
        id="scan", scan_type="PDF", current_remediation_artifact_id="manual"
    )
    principal = SimpleNamespace(department_id="dept")
    artifact = SimpleNamespace(
        cloud_file_id=None, edit_provenance={"operation": {"kind": "heading"}}
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = artifact
    monkeypatch.setattr(
        routes, "_resolve_remediation_queue_source", lambda *_, **__: (None, None)
    )
    enqueue = MagicMock()
    monkeypatch.setattr(routes, "enqueue_cloud_job", enqueue)
    with pytest.raises(routes.HTTPException) as error:
        routes._enqueue_scan_remediation(
            db, scan=scan, principal=principal, options={}, approved_fixes_only=True
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "manual_pdf_edits_require_review"
    enqueue.assert_not_called()
