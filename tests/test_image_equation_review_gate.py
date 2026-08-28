"""Durable image-equation evidence and mandatory human-review gate."""

from datetime import datetime, timezone
import hashlib
import json
import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import Column, MetaData, String, Table, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.education.remediation.base import FixedIssue, IssueCategory, IssueSeverity
from tests.test_visual_semantic_contract import (
    _contract,
    _roundtrip,
    _scanned_saved,
    _semantic,
    _standalone_saved,
)

HASH = "a" * 64
EVIDENCE = {key: value for key, value in _roundtrip().items() if key != "evidence_kind"}


def _locator(**overrides):
    identity = {
        "source_kind": "page_raster_region",
        "page_number": 1,
        "parent_occurrence_id": "imgocc-v1-" + ("7" * 24),
        "image_xref": 7,
        "image_index": 0,
        "occurrence_ordinal": 0,
        "parent_bbox": [0.0, 0.0, 612.0, 792.0],
        "pixel_bbox": [100, 200, 500, 320],
        "pdf_bbox": [30.6, 63.36, 153.0, 101.376],
        "source_sha256": "e" * 64,
        "crop_pixel_sha256": "f" * 64,
        "source_width": 2000,
        "source_height": 2500,
        "detector_version": "raster-equation-region-v1",
        "threshold_version": "grayscale-lt245-v1",
        "ocr_engine_version": "5.3.4",
        "ocr_tessdata_sha256": "1" * 64,
        "ocr_language": "eng",
        "ocr_config": "--oem 3 --psm 6",
        "transform": [612.0, 0.0, 0.0, 792.0, 0.0, 0.0],
    }
    identity.update(overrides)
    if "pixel_bbox" in overrides and "pdf_bbox" not in overrides:
        x0, y0, x1, y1 = identity["pixel_bbox"]
        parent_x0, parent_y0, parent_x1, parent_y1 = identity["parent_bbox"]
        identity["pdf_bbox"] = [
            parent_x0 + (x0 / identity["source_width"]) * (parent_x1 - parent_x0),
            parent_y0 + (y0 / identity["source_height"]) * (parent_y1 - parent_y0),
            parent_x0 + (x1 / identity["source_width"]) * (parent_x1 - parent_x0),
            parent_y0 + (y1 / identity["source_height"]) * (parent_y1 - parent_y0),
        ]
    digest = hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return {**identity, "region_id": f"eqregion-v1-{digest[:24]}"}


def _fix(**overrides):
    values = {
        "issue_id": "equation-1",
        "category": IssueCategory.STRUCTURE,
        "severity": IssueSeverity.HIGH,
        "description": "Image equation requires accessible math",
        "fixed_content": "x plus one",
        "fix_method": "ai_vision",
        "confidence": 0.55,
        "needs_review": True,
        "provider_used": "gemini",
        "model_used": "vision-model",
        "source_kind": "image_equation",
        "verification_evidence": EVIDENCE,
        "page_number": 1,
    }
    values.update(overrides)
    if "visual_semantic_contract" not in overrides:
        semantic = _semantic(alt_text=values["fixed_content"])
        alt_text_sha256 = hashlib.sha256(values["fixed_content"].encode()).hexdigest()
        locator = values.get("source_locator")
        if locator is not None:
            values["page_number"] = locator["page_number"]
            x0, y0, x1, y1 = locator["pixel_bbox"]
            scale_x, _, _, scale_y, offset_x, offset_y = locator["transform"]
            formula_bbox = [
                offset_x + scale_x * x0 / locator["source_width"],
                offset_y + scale_y * (1.0 - y1 / locator["source_height"]),
                offset_x + scale_x * x1 / locator["source_width"],
                offset_y + scale_y * (1.0 - y0 / locator["source_height"]),
            ]
            saved = _scanned_saved(
                page_number=locator["page_number"],
                image_xref=locator["image_xref"],
                image_stream_sha256=locator["source_sha256"],
                formula_bbox=formula_bbox,
                alt_text_sha256=alt_text_sha256,
            )
        else:
            seed = "|".join(
                str(values.get(field))
                for field in ("issue_id", "location", "page_number")
            )
            image_xref = 11 + int(hashlib.sha256(seed.encode()).hexdigest()[:6], 16)
            page_number = values.get("page_number") or 1
            bbox = [12.5, 20.0, 240.0, 100.5]
            occurrence_ordinal = 0
            identity = f"{page_number}|{image_xref}|0|{occurrence_ordinal}|" + ",".join(
                f"{value:.6f}" for value in bbox
            )
            locator = {
                "source_kind": "embedded_image_occurrence",
                "page_number": page_number,
                "image_xref": image_xref,
                "image_index": 0,
                "occurrence_ordinal": occurrence_ordinal,
                "bbox": bbox,
                "image_stream_sha256": "4" * 64,
                "occurrence_id": "imgocc-v1-"
                + hashlib.sha256(identity.encode()).hexdigest()[:24],
            }
            saved = _standalone_saved(
                page_number=page_number,
                image_xref=image_xref,
                occurrence_ordinal=occurrence_ordinal,
                image_stream_sha256="4" * 64,
                alt_text_sha256=alt_text_sha256,
            )
        values["visual_semantic_contract"] = _contract(
            locator,
            saved,
            semantic=semantic,
            evidence=[_roundtrip(), saved],
        )
    return FixedIssue(**values)


def test_fixed_issue_accepts_only_bounded_allowlisted_evidence():
    fix = _fix()

    assert fix.source_kind == "image_equation"
    assert fix.provider_used == "gemini"
    assert fix.verification_evidence.model_dump() == EVIDENCE

    with pytest.raises(ValidationError):
        _fix(source_kind="x" * 33)
    with pytest.raises(ValidationError):
        _fix(verification_evidence={**EVIDENCE, "provider_payload": "secret"})
    with pytest.raises(ValidationError):
        _fix(verification_evidence={**EVIDENCE, "source_sha256": "not-a-hash"})
    with pytest.raises(ValidationError):
        _fix(confidence=math.nan)

    from src.services.scan_fix_service import valid_image_equation_evidence

    assert not valid_image_equation_evidence(
        {
            **EVIDENCE,
            "threshold_version": "attacker-v1",
            "required_ink_iou": 0.0,
            "required_pixel_similarity": 0.0,
        }
    )


def test_page_raster_region_locator_is_strict_bounded_and_digest_bound():
    from src.education.equation_region_contract import PageRasterRegionLocator

    locator = PageRasterRegionLocator.from_mapping(_locator())
    assert locator.region_id.startswith("eqregion-v1-")
    assert locator.model_dump(mode="json") == _locator()
    assert (
        PageRasterRegionLocator.from_evidence(
            {"issue_type": "manual-envelope", **_locator()}
        )
        == locator
    )

    for invalid in (
        {**_locator(), "provider_payload": "secret"},
        {**_locator(), "region_id": "eqregion-v1-" + "0" * 24},
        _locator(pixel_bbox=[100, 200, 2500, 320]),
        _locator(pdf_bbox=[30.6, 63.36, 700.0, 101.376]),
        _locator(source_width=True),
    ):
        with pytest.raises((ValidationError, ValueError)):
            PageRasterRegionLocator.from_mapping(invalid)

    with pytest.raises(ValidationError, match="requires image_equation"):
        _fix(source_kind=None, source_locator=_locator())


def test_shared_persistence_forces_image_equation_pending_and_preserves_evidence():
    from src.services.scan_fix_service import build_scan_fix

    forged = _fix(
        fix_method="rule",
        confidence=1.0,
        needs_review=False,
    )

    row = build_scan_fix("scan-1", forged)

    assert row.fix_method == "ai_vision"
    assert row.confidence == 0.55
    assert row.needs_review is True
    assert row.review_status == "pending"
    assert row.reviewed_by is None
    assert row.reviewed_at is None
    assert row.provider_used == "gemini"
    assert row.model_used == "vision-model"
    assert row.source_kind == "image_equation"
    assert row.verification_evidence == EVIDENCE
    assert (
        "\x00"
        not in build_scan_fix(
            "scan-1", _fix(description="safe\x00description")
        ).description
    )

    nan_forgery = _fix().model_copy(update={"confidence": math.nan})
    with pytest.raises(ValueError):
        build_scan_fix("scan-1", nan_forgery)

    with pytest.raises(ValueError, match="complete visual contract"):
        build_scan_fix(
            "scan-1",
            _fix(visual_semantic_contract=None),
        )


def test_visual_contract_persistence_covers_both_locator_and_saved_variants():
    from src.services.scan_fix_service import build_scan_fix

    embedded = build_scan_fix("scan-embedded", _fix())
    scanned = build_scan_fix("scan-scanned", _fix(source_locator=_locator()))

    assert embedded.visual_semantic_contract["locator"]["source_kind"] == (
        "embedded_image_occurrence"
    )
    assert (
        embedded.visual_semantic_contract["verification_evidence"][1]["evidence_kind"]
        == "standalone_formula_saved_v1"
    )
    assert scanned.visual_semantic_contract["locator"]["source_kind"] == (
        "page_raster_region"
    )
    assert (
        scanned.visual_semantic_contract["verification_evidence"][1]["evidence_kind"]
        == "scanned_region_formula_saved_v1"
    )


def test_review_digest_is_mapping_order_stable_and_list_order_sensitive():
    from src.services.scan_fix_service import build_scan_fix

    fix = _fix()
    contract = fix.visual_semantic_contract.model_dump(mode="json")
    reordered = dict(reversed(list(contract.items())))
    reordered_fix = fix.model_copy(update={"visual_semantic_contract": reordered})
    reversed_evidence = _contract(
        contract["locator"],
        contract["verification_evidence"][1],
        evidence=list(reversed(contract["verification_evidence"])),
    )
    reversed_fix = fix.model_copy(
        update={"visual_semantic_contract": reversed_evidence}
    )

    original_row = build_scan_fix("scan-1", fix)
    reordered_row = build_scan_fix("scan-1", reordered_fix)
    reversed_row = build_scan_fix("scan-1", reversed_fix)

    assert original_row.review_digest == reordered_row.review_digest
    assert original_row.review_digest != reversed_row.review_digest


def test_artifact_review_digest_binds_exact_accepted_contracts_and_set():
    from copy import deepcopy

    from src.services.scan_fix_service import (
        artifact_approval_review_digest,
        build_scan_fix,
    )
    from src.education.visual_semantic_contract import canonical_sha256

    first = build_scan_fix("scan-1", _fix())
    second = build_scan_fix(
        "scan-1", _fix(issue_id="equation-2", location="page 2", page_number=2)
    )
    for row in (first, second):
        row.review_status = "approved"
        row.reviewed_by = "user-1"
        row.reviewed_at = datetime.now(timezone.utc)
        row.approved_review_digest = row.review_digest

    forward = artifact_approval_review_digest(HASH, [first, second])
    reverse = artifact_approval_review_digest(HASH, [second, first])

    assert forward == reverse
    assert forward == canonical_sha256(
        {
            "artifact_sha256": HASH,
            "accepted_fixes": sorted(
                [
                    {
                        "occurrence_key": row.occurrence_key,
                        "approved_review_digest": row.review_digest,
                    }
                    for row in (first, second)
                ],
                key=lambda item: item["occurrence_key"],
            ),
        }
    )
    assert forward != artifact_approval_review_digest(HASH, [first])

    original_approval = first.approved_review_digest
    first.fixed_content = "tampered semantic output"
    assert artifact_approval_review_digest(HASH, [first, second]) is None
    first.fixed_content = "x plus one"

    first.source_locator = {"forged": "source"}
    assert artifact_approval_review_digest(HASH, [first, second]) is None
    first.source_locator = None

    first.provider_used = "tampered-verifier"
    assert artifact_approval_review_digest(HASH, [first, second]) is None
    first.provider_used = "gemini"

    original_contract = deepcopy(first.visual_semantic_contract)
    first.visual_semantic_contract = deepcopy(original_contract)
    first.visual_semantic_contract["verification_evidence"][0][
        "renderer_version"
    ] = "tampered-renderer"
    assert artifact_approval_review_digest(HASH, [first, second]) is None
    first.visual_semantic_contract = original_contract

    first.verification_evidence = {**EVIDENCE, "pixel_similarity": 0.999}
    assert artifact_approval_review_digest(HASH, [first, second]) is None
    first.verification_evidence = EVIDENCE

    first.approved_review_digest = original_approval
    contract = _fix().visual_semantic_contract.model_dump(mode="json")
    reordered_evidence = _contract(
        contract["locator"],
        contract["verification_evidence"][1],
        evidence=list(reversed(contract["verification_evidence"])),
    )
    reordered = build_scan_fix(
        "scan-1", _fix(visual_semantic_contract=reordered_evidence)
    )
    reordered.review_status = "approved"
    reordered.reviewed_by = "user-1"
    reordered.reviewed_at = datetime.now(timezone.utc)
    reordered.approved_review_digest = original_approval
    assert reordered.review_digest != original_approval
    assert artifact_approval_review_digest(HASH, [reordered]) is None


def test_artifact_review_digest_rejects_stale_missing_and_duplicate_bindings():
    from src.services.scan_fix_service import (
        artifact_approval_review_digest,
        build_scan_fix,
    )

    first = build_scan_fix("scan-1", _fix())
    first.review_status = "approved"
    first.reviewed_by = "user-1"
    first.reviewed_at = datetime.now(timezone.utc)
    first.approved_review_digest = first.review_digest
    duplicate = build_scan_fix("scan-2", _fix())
    duplicate.review_status = "approved"
    duplicate.reviewed_by = "user-1"
    duplicate.reviewed_at = datetime.now(timezone.utc)
    duplicate.approved_review_digest = duplicate.review_digest

    assert artifact_approval_review_digest(HASH, [first]) is not None
    assert artifact_approval_review_digest(HASH, [first, duplicate]) is None

    first.approved_review_digest = None
    assert artifact_approval_review_digest(HASH, [first]) is None
    first.approved_review_digest = first.review_digest
    first.occurrence_key = "not-a-digest"
    assert artifact_approval_review_digest(HASH, [first]) is None


def test_region_persistence_is_canonical_review_required_and_occurrence_bound():
    from src.services.scan_fix_service import build_scan_fix

    locator = _locator()
    row = build_scan_fix(
        "scan-1",
        _fix(
            issue_id="display-id-a",
            location="old display location",
            confidence=1.0,
            needs_review=False,
            source_locator=locator,
        ),
    )
    same_region = build_scan_fix(
        "scan-1",
        _fix(
            issue_id="display-id-b",
            location="new display location",
            source_locator=dict(reversed(list(locator.items()))),
        ),
    )
    moved = build_scan_fix(
        "scan-1",
        _fix(source_locator=_locator(pixel_bbox=[101, 200, 500, 320])),
    )

    assert row.source_kind == "image_equation"
    assert row.source_locator == locator
    assert row.confidence == 0.55
    assert row.needs_review is True
    assert row.review_status == "pending"
    assert row.occurrence_key == same_region.occurrence_key
    assert row.id == same_region.id
    assert moved.occurrence_key != row.occurrence_key
    assert moved.id != row.id


def test_scan_fix_identity_is_occurrence_bound_and_reorder_stable():
    from src.services.scan_fix_service import build_scan_fix, persist_scan_fixes

    first = _fix(issue_id="duplicate-rule", location="page 1 / image 2", page_number=1)
    second = _fix(issue_id="duplicate-rule", location="page 4 / image 1", page_number=4)

    forward = [build_scan_fix("scan-1", fix) for fix in (first, second)]
    reverse = [build_scan_fix("scan-1", fix) for fix in (second, first)]

    assert len({row.id for row in forward}) == 2
    assert {row.id for row in forward} == {row.id for row in reverse}
    assert {row.occurrence_key for row in forward} == {
        row.occurrence_key for row in reverse
    }

    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.all.return_value = []
    persisted = persist_scan_fixes(db, "scan-1", [second, first])
    assert {row.id for row in persisted} == {row.id for row in forward}


def test_scan_fix_persistence_rejects_ambiguous_duplicate_occurrence_before_writes():
    from src.services.scan_fix_service import persist_scan_fixes

    db = MagicMock()
    duplicate = _fix(issue_id="same", location="page 1", page_number=1)

    with pytest.raises(ValueError, match="ambiguous duplicate fix occurrence"):
        persist_scan_fixes(db, "scan-1", [duplicate, duplicate.model_copy()])

    db.query.assert_not_called()
    db.add.assert_not_called()


def test_appending_fix_invalidates_current_artifact_approval(monkeypatch):
    from src.services import scan_fix_service

    artifact = SimpleNamespace(
        id="artifact-1",
        cloud_file_id=None,
        review_status="approved",
        written_back_at=None,
        approval_checksum=HASH,
        approval_review_digest=HASH,
        approved_by_id="user-1",
        approved_by_ref="reviewer@example.test",
        approved_at=datetime.now(timezone.utc),
    )
    graph = scan_fix_service.ScanReviewGraph(
        scan=SimpleNamespace(id="scan-1", current_remediation_artifact_id="artifact-1"),
        fixes=(),
        artifacts=(artifact,),
        cloud_files=(),
    )
    monkeypatch.setattr(scan_fix_service, "lock_scan_review_graph", lambda db, _: graph)
    db = MagicMock()

    scan_fix_service.persist_scan_fixes(db, "scan-1", [_fix()], replace=False)

    assert artifact.review_status == "pending"
    assert artifact.approval_checksum is None
    assert artifact.approval_review_digest is None


def test_region_locator_change_replaces_occurrence_and_invalidates_approval(
    monkeypatch,
):
    from src.services import scan_fix_service

    existing = scan_fix_service.build_scan_fix(
        "scan-1", _fix(source_locator=_locator())
    )
    existing.review_status = "approved"
    existing.reviewed_by = "user-1"
    existing.reviewed_at = datetime.now(timezone.utc)
    existing.approved_review_digest = existing.review_digest
    artifact = SimpleNamespace(
        id="artifact-1",
        cloud_file_id=None,
        review_status="approved",
        written_back_at=None,
        approval_checksum=HASH,
        approval_review_digest=HASH,
        approved_by_id="user-1",
        approved_by_ref="reviewer@example.test",
        approved_at=datetime.now(timezone.utc),
    )
    graph = scan_fix_service.ScanReviewGraph(
        scan=SimpleNamespace(id="scan-1", current_remediation_artifact_id="artifact-1"),
        fixes=(existing,),
        artifacts=(artifact,),
        cloud_files=(),
    )
    monkeypatch.setattr(scan_fix_service, "lock_scan_review_graph", lambda db, _: graph)
    db = MagicMock()

    replacement = scan_fix_service.persist_scan_fixes(
        db,
        "scan-1",
        [_fix(source_locator=_locator(pixel_bbox=[101, 200, 500, 320]))],
    )[0]

    assert replacement.id != existing.id
    assert replacement.review_status == "pending"
    assert replacement.approved_review_digest is None
    db.delete.assert_called_once_with(existing)
    assert artifact.review_status == "pending"
    assert artifact.approval_checksum is None
    assert artifact.approval_review_digest is None


def test_region_locator_survives_subprocess_json_and_queued_persistence(tmp_path):
    from src.jobs.remediation_subprocess import (
        SubprocessRemediationResult,
        _json_record,
    )
    from src.education.remediation.output_claim import DescriptorBoundOutputClaim
    from src.services.scan_fix_service import build_scan_fix

    locator = _locator()
    artifact = tmp_path / "queued.pdf"
    artifact.write_bytes(b"exact queued artifact bytes")
    artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    x0, y0, x1, y1 = locator["pixel_bbox"]
    scale_x, _, _, scale_y, offset_x, offset_y = locator["transform"]
    saved = _scanned_saved(
        saved_file_sha256=artifact_sha256,
        page_number=locator["page_number"],
        image_xref=locator["image_xref"],
        image_stream_sha256=locator["source_sha256"],
        formula_bbox=[
            offset_x + scale_x * x0 / locator["source_width"],
            offset_y + scale_y * (1.0 - y1 / locator["source_height"]),
            offset_x + scale_x * x1 / locator["source_width"],
            offset_y + scale_y * (1.0 - y0 / locator["source_height"]),
        ],
    )
    child_record = _json_record(
        _fix(
            source_locator=locator,
            visual_semantic_contract=_contract(locator, saved),
        )
    )
    wire_payload = json.loads(
        json.dumps(
            {"fixed_issues": [child_record]},
            allow_nan=False,
            separators=(",", ":"),
        )
    )

    output_claim = DescriptorBoundOutputClaim.from_path(
        artifact,
        display_path=str(artifact),
        mime="application/pdf",
    )
    queued_result = SubprocessRemediationResult(wire_payload, output_claim)
    queued_fix = queued_result.fixed_issues[0]
    persisted = build_scan_fix("scan-queued", queued_fix)

    assert queued_fix.source_locator == locator
    assert persisted.source_locator == locator
    assert (
        persisted.occurrence_key
        == build_scan_fix(
            "scan-queued", _fix(source_locator=dict(reversed(list(locator.items()))))
        ).occurrence_key
    )
    assert persisted.review_status == "pending"
    assert persisted.needs_review is True
    queued_result.close_output_claim()


def test_nonvisual_fixed_issue_keeps_pre_contract_subprocess_wire_bytes():
    from src.jobs.remediation_subprocess import _json_record

    fix = FixedIssue(
        issue_id="title-1",
        category=IssueCategory.TITLE,
        severity=IssueSeverity.MEDIUM,
        description="Document title was missing",
        fixed_content="Accessible title",
        fix_method="rule",
    )
    legacy_record = fix.model_dump(mode="json", exclude={"visual_semantic_contract"})

    assert "visual_semantic_contract" not in _json_record(fix)
    assert _json_record({**legacy_record, "visual_semantic_contract": None}) == (
        legacy_record
    )
    assert (
        json.dumps(_json_record(fix), allow_nan=False, separators=(",", ":")).encode()
        == json.dumps(legacy_record, allow_nan=False, separators=(",", ":")).encode()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delivery_path", "cloud_file_id"),
    [
        ("direct", None),
        ("queued", None),
        ("canvas", "canvas-file-1"),
        ("brightspace", "brightspace-file-1"),
    ],
)
async def test_region_artifact_is_unavailable_on_every_delivery_path_before_review(
    monkeypatch, delivery_path, cloud_file_id
):
    from fastapi import HTTPException

    from src.api.education import remediation_routes as routes
    from src.db.models import CloudJobStatus, RemediationOutcome, ScanStatus
    from src.services.remediation_artifact_service import (
        ArtifactAuthorizationError,
        RemediationArtifactService,
    )
    from src.services.scan_fix_service import build_scan_fix

    artifact = SimpleNamespace(
        id="artifact-1",
        cloud_file_id=cloud_file_id,
        filename="fixed.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        sha256=HASH,
        provider_result={},
        scan_id="scan-1",
    )
    region_fix = build_scan_fix(
        "scan-1",
        _fix(source_locator=_locator()),
    )
    db = MagicMock()
    db.get.return_value = artifact
    query = db.query.return_value
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.populate_existing.return_value = query
    query.first.return_value = (region_fix.id,)
    query.all.return_value = [region_fix]
    service = MagicMock()

    canonical_gate = RemediationArtifactService.__new__(RemediationArtifactService)
    with pytest.raises(ArtifactAuthorizationError, match="human review"):
        canonical_gate._require_approvable_review(
            db,
            artifact,
            SimpleNamespace(
                status=ScanStatus.COMPLETED,
                remediation_outcome=RemediationOutcome.COMPLETED.value,
            ),
        )

    if delivery_path == "queued":
        service.resolve_record.side_effect = ArtifactAuthorizationError(
            "approval required"
        )
        monkeypatch.setattr(
            routes.RemediationArtifactService,
            "from_settings",
            classmethod(lambda cls: service),
        )
        job = SimpleNamespace(
            status=CloudJobStatus.COMPLETED.value,
            department_id="department-1",
            cloud_file_id=None,
            result_data={"artifact_id": artifact.id},
        )

        assert routes._artifact_is_downloadable(db, job, "scan-1") == (False, None)
        assert service.resolve_record.call_args.kwargs["require_approved"] is True
        assert service.resolve_record.call_args.kwargs["approval_checksum"] == HASH
        return

    context = MagicMock()
    context.__enter__.side_effect = ArtifactAuthorizationError("approval required")
    service.open_verified.return_value = context
    cloud_file = (
        SimpleNamespace(id=cloud_file_id, provider=delivery_path)
        if cloud_file_id is not None
        else None
    )
    monkeypatch.setattr(
        routes,
        "_managed_artifact_authority",
        MagicMock(return_value=(SimpleNamespace(id="scan-1"), cloud_file, artifact)),
    )
    monkeypatch.setattr(
        routes.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: service),
    )

    with pytest.raises(HTTPException) as exc_info:
        await routes.download_managed_artifact(
            "scan-1",
            artifact.id,
            db=db,
            principal=SimpleNamespace(department_id="department-1"),
        )

    assert exc_info.value.status_code == 404
    assert service.open_verified.call_args.kwargs["require_approved"] is True
    assert service.open_verified.call_args.kwargs["approval_checksum"] == HASH
    assert service.open_verified.call_args.kwargs["cloud_file_id"] == cloud_file_id


def test_scan_fix_database_constraint_rejects_concurrent_duplicate_occurrence():
    from src.db.models import ScanFix
    from src.services.scan_fix_service import build_scan_fix

    engine = create_engine("sqlite://")
    metadata = MetaData()
    Table("scans", metadata, Column("id", String(36), primary_key=True))
    Table("users", metadata, Column("id", String(36), primary_key=True))
    ScanFix.__table__.to_metadata(metadata)
    metadata.create_all(engine)
    row = build_scan_fix("scan-1", _fix(location="page 1", page_number=1))

    with Session(engine) as first:
        first.add(row)
        first.commit()
    with Session(engine) as second:
        colliding = build_scan_fix("scan-1", _fix(location="page 1", page_number=1))
        colliding.id = "different-primary-key"
        second.add(colliding)
        with pytest.raises(IntegrityError):
            second.commit()


def test_review_gate_requires_exact_human_approval_for_every_image_equation_fix():
    from src.services.scan_fix_service import (
        build_scan_fix,
        image_equation_review_blockers,
    )

    approved = build_scan_fix("scan-1", _fix())
    approved.review_status = "approved"
    approved.reviewed_by = "user-1"
    approved.reviewed_at = datetime.now(timezone.utc)
    approved.approved_review_digest = approved.review_digest
    assert image_equation_review_blockers([approved]) == []

    for mutation in (
        {"review_status": "auto_approved"},
        {"review_status": "pending"},
        {"review_status": "rejected"},
        {"reviewed_by": None},
        {"reviewed_at": None},
        {"fix_method": "rule"},
        {"confidence": 1.0},
        {"confidence": math.nan},
        {"needs_review": False},
        {"provider_used": None},
        {"model_used": None},
        {"verification_evidence": {**EVIDENCE, "passed": False}},
        {"verification_evidence": {**EVIDENCE, "source_sha256": "forged"}},
    ):
        candidate = SimpleNamespace(**{**approved.__dict__, **mutation})
        assert image_equation_review_blockers([candidate])

    mixed = SimpleNamespace(**{**approved.__dict__, "review_status": "auto_approved"})
    assert image_equation_review_blockers([approved, mixed])

    approved_region = build_scan_fix("scan-region", _fix(source_locator=_locator()))
    approved_region.review_status = "approved"
    approved_region.reviewed_by = "user-1"
    approved_region.reviewed_at = datetime.now(timezone.utc)
    approved_region.approved_review_digest = approved_region.review_digest
    assert image_equation_review_blockers([approved_region]) == []
    forged_region = SimpleNamespace(
        **{
            **approved_region.__dict__,
            "source_locator": {**_locator(), "region_id": "eqregion-v1-" + "0" * 24},
        }
    )
    assert "image_equation_provenance_invalid" in image_equation_review_blockers(
        [forged_region]
    )


def test_image_equation_review_cannot_edit_stale_artifact_metadata():
    from src.services.scan_fix_service import build_scan_fix, validate_fix_review_action

    image_fix = SimpleNamespace(source_kind="image_equation")
    with pytest.raises(ValueError, match="cannot be edited"):
        validate_fix_review_action(image_fix, "edit")
    with pytest.raises(ValueError, match="incomplete or stale"):
        validate_fix_review_action(image_fix, "approve")

    complete = build_scan_fix("scan-1", _fix())
    validate_fix_review_action(complete, "approve")


def test_human_approval_binds_exact_current_review_digest_and_reject_clears_it():
    from src.services.scan_fix_service import (
        bind_fix_review_decision,
        build_scan_fix,
    )

    row = build_scan_fix("scan-1", _fix())
    bind_fix_review_decision(row, "approve")
    assert row.approved_review_digest == row.review_digest

    row.description = "tampered after review"
    with pytest.raises(ValueError, match="incomplete or stale"):
        bind_fix_review_decision(row, "approve")

    bind_fix_review_decision(row, "reject")
    assert row.approved_review_digest is None


@pytest.mark.parametrize(
    ("raw_contract", "expected_blocker"),
    [
        (None, "image_equation_visual_contract_incomplete"),
        (
            {"contract_kind": "printed_equation"},
            "image_equation_visual_contract_invalid",
        ),
    ],
)
def test_historical_incomplete_or_corrupt_visual_rows_fail_closed(
    raw_contract, expected_blocker
):
    from src.services.scan_fix_service import (
        image_equation_review_blockers,
        validate_fix_review_action,
    )

    historical = SimpleNamespace(
        source_kind="image_equation",
        source_locator=None,
        visual_semantic_contract=raw_contract,
        review_digest=None,
        approved_review_digest=None,
        fix_method="ai_vision",
        confidence=0.55,
        needs_review=True,
        provider_used="gemini",
        model_used="vision-model",
        review_status="pending",
        reviewed_by=None,
        reviewed_at=None,
        verification_evidence=EVIDENCE,
    )

    assert expected_blocker in image_equation_review_blockers([historical])
    with pytest.raises(ValueError, match="incomplete or stale"):
        validate_fix_review_action(historical, "approve")


def test_artifact_service_and_metadata_share_image_equation_blockers():
    from src.api.education.remediation_routes import _artifact_review_blockers
    from src.db.models import RemediationOutcome, Scan, ScanFix, ScanStatus
    from src.services.remediation_artifact_service import (
        ArtifactAuthorizationError,
        RemediationArtifactService,
    )

    scan = SimpleNamespace(
        id="scan-1",
        status=ScanStatus.COMPLETED,
        remediation_outcome=RemediationOutcome.COMPLETED.value,
    )
    artifact = SimpleNamespace(scan_id=scan.id, review_status="pending")
    forged = SimpleNamespace(
        source_kind="image_equation",
        review_status="auto_approved",
        reviewed_by=None,
        reviewed_at=None,
        verification_evidence=EVIDENCE,
    )
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.with_for_update.return_value = chain
        chain.populate_existing.return_value = chain
        if model is Scan:
            chain.one_or_none.return_value = scan
        elif model is ScanFix:
            chain.all.return_value = [forged]
        return chain

    db.query.side_effect = query
    blockers = _artifact_review_blockers(db, scan, artifact)
    assert "image_equation_not_human_approved" in blockers

    service = RemediationArtifactService.__new__(RemediationArtifactService)
    with pytest.raises(ArtifactAuthorizationError, match="human review"):
        service._require_approvable_review(db, artifact, scan)


def test_authenticated_batch_review_records_actor_time_and_each_fix():
    from src.db.models import ReviewAuditLog
    from src.services.scan_fix_service import apply_authenticated_batch_review

    first = SimpleNamespace(id="fix-1", review_status="pending")
    second = SimpleNamespace(id="fix-2", review_status="pending")
    db = MagicMock()
    now = datetime.now(timezone.utc)

    apply_authenticated_batch_review(
        db,
        scan_id="scan-1",
        fixes=[first, second],
        action="approve",
        user_id="user-1",
        reviewed_at=now,
        notes="checked",
    )

    assert first.review_status == second.review_status == "approved"
    assert first.reviewed_by == second.reviewed_by == "user-1"
    assert first.reviewed_at == second.reviewed_at == now
    records = [call.args[0] for call in db.add.call_args_list]
    assert len(records) == 2
    assert all(isinstance(record, ReviewAuditLog) for record in records)
    assert {record.fix_id for record in records} == {"fix-1", "fix-2"}
    assert all(record.user_id == "user-1" for record in records)


def test_review_graph_invalidation_clears_every_approved_current_sink():
    from src.db.models import (
        CloudFile,
        RemediationArtifact,
        ReviewAuditLog,
        Scan,
        ScanFix,
    )
    from src.services.scan_fix_service import lock_scan_review_graph

    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        current_remediation_artifact_id="artifact-local",
    )
    fix = SimpleNamespace(id="fix-1")
    local = SimpleNamespace(
        id="artifact-local",
        cloud_file_id=None,
        review_status="approved",
        approval_review_digest=HASH,
    )
    cloud_artifact = SimpleNamespace(
        id="artifact-cloud",
        cloud_file_id="cloud-1",
        review_status="approved",
        approval_review_digest=HASH,
    )
    cloud = SimpleNamespace(
        id="cloud-1",
        current_remediation_artifact_id="artifact-cloud",
        writeback_status="approved",
        has_remediated_version=True,
        remediation_origin="manual",
    )
    db = MagicMock()
    calls = []

    def query(model):
        calls.append(model)
        chain = MagicMock()
        chain.options.return_value = chain
        chain.filter.return_value = chain
        chain.order_by.return_value = chain
        chain.with_for_update.return_value = chain
        chain.populate_existing.return_value = chain
        if model is Scan:
            chain.one_or_none.return_value = scan
        elif model is ScanFix:
            chain.all.return_value = [fix]
        elif model is RemediationArtifact:
            chain.all.return_value = [local, cloud_artifact]
        elif model is CloudFile:
            chain.all.return_value = [cloud]
        return chain

    db.query.side_effect = query

    graph = lock_scan_review_graph(db, "scan-1", invalidate_approvals=True)

    assert graph.scan is scan
    assert graph.fixes == (fix,)
    assert calls == [
        Scan,
        RemediationArtifact,
        CloudFile,
        RemediationArtifact,
        ScanFix,
    ]
    for artifact in (local, cloud_artifact):
        assert artifact.review_status == "pending"
        assert artifact.approval_checksum is None
        assert artifact.approval_review_digest is None
        assert artifact.approved_by_id is None
        assert artifact.approved_by_ref is None
        assert artifact.approved_at is None
    assert cloud.writeback_status == "pending_review"
    assert cloud.has_remediated_version is False
    assert cloud.remediation_origin is None
    audits = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], ReviewAuditLog)
    ]
    assert {audit.details["artifact_id"] for audit in audits} == {
        "artifact-local",
        "artifact-cloud",
    }
    db.flush.assert_called_once()


def test_durable_evidence_and_per_fix_audit_survive_session_restart():
    from src.db.models import ReviewAuditLog, ScanFix
    from src.services.scan_fix_service import (
        apply_authenticated_batch_review,
        image_equation_review_blockers,
        persist_scan_fixes,
    )

    engine = create_engine("sqlite://")
    metadata = MetaData()
    Table(
        "scans",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("department_id", String(36)),
        Column("current_remediation_artifact_id", String(36)),
    )
    Table("users", metadata, Column("id", String(36), primary_key=True))
    ScanFix.__table__.to_metadata(metadata)
    ReviewAuditLog.__table__.to_metadata(metadata)
    Table(
        "remediation_artifacts",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("scan_id", String(36), nullable=False),
        Column("cloud_file_id", String(36)),
        Column("review_status", String(20)),
        Column("written_back_at", String),
        Column("approval_checksum", String(64)),
        Column("approval_review_digest", String(64)),
        Column("approved_by_id", String(36)),
        Column("approved_by_ref", String(255)),
        Column("approved_at", String),
    )
    Table(
        "cloud_files",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("current_remediation_artifact_id", String(36)),
        Column("writeback_status", String(20)),
        Column("has_remediated_version", String),
        Column("remediation_origin", String(16)),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(metadata.tables["scans"].insert().values(id="scan-1"))

    with Session(engine) as session:
        row = persist_scan_fixes(session, "scan-1", [_fix(source_locator=_locator())])[
            0
        ]
        row_id = row.id
        session.commit()

    with Session(engine) as session:
        row = session.get(ScanFix, row_id)
        assert row is not None
        assert row.verification_evidence == EVIDENCE
        assert row.source_locator == _locator()
        assert image_equation_review_blockers([row])
        apply_authenticated_batch_review(
            session,
            scan_id="scan-1",
            fixes=[row],
            action="approve",
            user_id="user-1",
            reviewed_at=datetime.now(timezone.utc),
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(ScanFix, row_id)
        assert row is not None
        assert image_equation_review_blockers([row]) == []
        assert row.approved_review_digest == row.review_digest
        audits = session.scalars(select(ReviewAuditLog)).all()
        assert len(audits) == 1
        assert audits[0].fix_id == row_id
        assert audits[0].user_id == "user-1"

        retried = persist_scan_fixes(
            session, "scan-1", [_fix(source_locator=_locator())]
        )[0]
        session.flush()
        assert retried.id == row_id
        assert retried.review_status == "approved"
        assert retried.reviewed_by == "user-1"
        assert retried.approved_review_digest == retried.review_digest
        assert session.scalars(select(ReviewAuditLog)).all()[0].fix_id == row_id

        changed = persist_scan_fixes(
            session,
            "scan-1",
            [
                _fix(
                    description="Changed image equation review description",
                    source_locator=_locator(),
                )
            ],
        )[0]
        session.flush()
        assert changed.id == row_id
        assert changed.review_status == "pending"
        assert changed.reviewed_by is None
        assert changed.approved_review_digest is None
        assert "fix_replaced" in {
            audit.action for audit in session.scalars(select(ReviewAuditLog)).all()
        }


def test_generic_fixes_keep_existing_auto_approval_behavior():
    from src.services.scan_fix_service import (
        build_scan_fix,
        image_equation_review_blockers,
    )

    generic = FixedIssue(
        issue_id="language-1",
        category=IssueCategory.LANGUAGE,
        severity=IssueSeverity.LOW,
        description="Missing language",
        fixed_content="en-AU",
        fix_method="rule",
    )
    row = build_scan_fix("scan-1", generic)

    assert row.review_status == "auto_approved"
    assert row.visual_semantic_contract is None
    assert row.approved_review_digest == row.review_digest
    assert image_equation_review_blockers([row]) == []


def test_exact_legacy_nonvisual_replay_bootstraps_digests_without_resetting_approval(
    monkeypatch,
):
    from src.services import scan_fix_service
    from src.db.models import ReviewAuditLog

    fix = FixedIssue(
        issue_id="language-review-1",
        category=IssueCategory.LANGUAGE,
        severity=IssueSeverity.LOW,
        description="Reviewed language correction",
        fixed_content="en-AU",
        fix_method="rule",
        needs_review=True,
    )
    existing = scan_fix_service.build_scan_fix("scan-1", fix)
    existing.review_status = "approved"
    existing.reviewed_by = "user-1"
    existing.reviewed_at = datetime.now(timezone.utc)
    existing.review_digest = None
    existing.approved_review_digest = None
    artifact = SimpleNamespace(
        id="artifact-1",
        cloud_file_id=None,
        review_status="approved",
        written_back_at=None,
        approval_checksum=HASH,
        approval_review_digest=HASH,
        approved_by_id="user-1",
        approved_by_ref="reviewer@example.test",
        approved_at=datetime.now(timezone.utc),
    )
    graph = scan_fix_service.ScanReviewGraph(
        scan=SimpleNamespace(id="scan-1", current_remediation_artifact_id="artifact-1"),
        fixes=(existing,),
        artifacts=(artifact,),
        cloud_files=(),
    )
    monkeypatch.setattr(scan_fix_service, "lock_scan_review_graph", lambda db, _: graph)
    db = MagicMock()

    replayed = scan_fix_service.persist_scan_fixes(db, "scan-1", [fix])[0]

    assert replayed.review_status == "approved"
    assert replayed.reviewed_by == "user-1"
    assert replayed.review_digest is not None
    assert replayed.approved_review_digest == replayed.review_digest
    assert artifact.review_status == "approved"
    assert artifact.approval_checksum == HASH
    assert artifact.approval_review_digest == HASH
    assert not any(
        isinstance(call.args[0], ReviewAuditLog) for call in db.add.call_args_list
    )
