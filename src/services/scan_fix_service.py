"""Shared durable ScanFix persistence and image-equation review policy."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any
import uuid

from pydantic import ValidationError
from sqlalchemy.orm import load_only

from ..db.models import (
    CloudFile,
    RemediationArtifact,
    ReviewAuditLog,
    Scan,
    ScanFix,
)
from ..education.equation_region_contract import (
    canonical_region_locator,
    valid_region_locator,
)
from ..education.remediation.base import FixedIssue, VerificationEvidence
from ..education.visual_semantic_contract import (
    EmbeddedImageOccurrenceLocator,
    FrozenPageRasterRegionLocator,
    PrintedEquationRoundtripEvidenceV1,
    VisualSemanticContract,
    VisualSemanticContractAdapter,
    canonical_sha256,
)
from ..utils.sanitization import sanitize_for_postgres

IMAGE_EQUATION_SOURCE_KIND = "image_equation"
IMAGE_EQUATION_THRESHOLD_VERSION = "printed-equation-v1"
IMAGE_EQUATION_REQUIRED_INK_IOU = 0.90
IMAGE_EQUATION_REQUIRED_PIXEL_SIMILARITY = 0.98
_CANONICAL_FIELDS = (
    "issue_id",
    "occurrence_key",
    "category",
    "severity",
    "description",
    "location",
    "original_content",
    "fixed_content",
    "fix_method",
    "provider_used",
    "model_used",
    "source_kind",
    "source_locator",
    "verification_evidence",
    "visual_semantic_contract",
    "confidence",
    "needs_review",
    "wcag_criteria",
    "page_number",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ScanReviewGraph:
    """Rows held in the canonical scan -> fix -> artifact -> cloud order."""

    scan: Any
    fixes: tuple[Any, ...]
    artifacts: tuple[Any, ...]
    cloud_files: tuple[Any, ...]


def invalidate_current_artifact_approvals(
    db: Any, graph: ScanReviewGraph, *, reason: str = "fix_review_changed"
) -> tuple[Any, ...]:
    """Invalidate every approved current output while graph locks are held."""
    cloud_by_artifact = {
        cloud.current_remediation_artifact_id: cloud
        for cloud in graph.cloud_files
        if cloud.current_remediation_artifact_id
    }
    current_ids = set(cloud_by_artifact)
    if graph.scan.current_remediation_artifact_id:
        current_ids.add(graph.scan.current_remediation_artifact_id)
    invalidated = []
    for artifact in graph.artifacts:
        if (
            artifact.id not in current_ids
            or artifact.review_status != "approved"
            or getattr(artifact, "written_back_at", None) is not None
        ):
            continue
        artifact.review_status = "pending"
        artifact.approval_checksum = None
        artifact.approval_review_digest = None
        artifact.approved_by_id = None
        artifact.approved_by_ref = None
        artifact.approved_at = None
        cloud = cloud_by_artifact.get(artifact.id)
        if cloud is not None:
            cloud.writeback_status = "pending_review"
            cloud.has_remediated_version = False
            cloud.remediation_origin = None
        db.add(
            ReviewAuditLog(
                id=str(uuid.uuid4()),
                scan_id=graph.scan.id,
                user_id=None,
                action="artifact_approval_invalidated",
                details={"artifact_id": artifact.id, "reason": reason},
            )
        )
        invalidated.append(artifact)
    if invalidated:
        db.flush()
    return tuple(invalidated)


def lock_scan_review_graph(
    db: Any, scan_id: str, *, invalidate_approvals: bool = False
) -> ScanReviewGraph:
    """Lock the review subset in global Scan→Cloud→Artifact→Fix order."""
    scan = (
        db.query(Scan)
        .options(
            load_only(
                Scan.id,
                Scan.department_id,
                Scan.current_remediation_artifact_id,
            )
        )
        .filter(Scan.id == scan_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if scan is None:
        raise ValueError("scan does not exist")
    artifact_metadata = tuple(
        db.query(RemediationArtifact)
        .options(
            load_only(
                RemediationArtifact.id,
                RemediationArtifact.cloud_file_id,
            )
        )
        .filter(RemediationArtifact.scan_id == scan_id)
        .order_by(RemediationArtifact.id.asc())
        .all()
    )
    cloud_ids = sorted(
        {
            artifact.cloud_file_id
            for artifact in artifact_metadata
            if artifact.cloud_file_id
        }
    )
    cloud_files = tuple(
        db.query(CloudFile)
        .options(
            load_only(
                CloudFile.id,
                CloudFile.current_remediation_artifact_id,
                CloudFile.writeback_status,
                CloudFile.has_remediated_version,
                CloudFile.remediation_origin,
            )
        )
        .filter(CloudFile.id.in_(cloud_ids))
        .order_by(CloudFile.id.asc())
        .with_for_update()
        .populate_existing()
        .all()
        if cloud_ids
        else ()
    )
    artifacts = tuple(
        db.query(RemediationArtifact)
        .options(
            load_only(
                RemediationArtifact.id,
                RemediationArtifact.scan_id,
                RemediationArtifact.cloud_file_id,
                RemediationArtifact.review_status,
                RemediationArtifact.written_back_at,
                RemediationArtifact.approval_checksum,
                RemediationArtifact.approval_review_digest,
                RemediationArtifact.approved_by_id,
                RemediationArtifact.approved_by_ref,
                RemediationArtifact.approved_at,
            )
        )
        .filter(RemediationArtifact.scan_id == scan_id)
        .order_by(RemediationArtifact.id.asc())
        .with_for_update()
        .populate_existing()
        .all()
    )
    fixes = tuple(
        db.query(ScanFix)
        .filter(ScanFix.scan_id == scan_id)
        .order_by(ScanFix.id.asc())
        .with_for_update()
        .populate_existing()
        .all()
    )
    graph = ScanReviewGraph(scan, fixes, artifacts, cloud_files)
    if invalidate_approvals:
        invalidate_current_artifact_approvals(db, graph)
    return graph


def _evidence_dict(value: Any) -> dict[str, Any]:
    """Return only the typed durable evidence allowlist."""
    evidence = VerificationEvidence.model_validate(value)
    return evidence.model_dump(mode="json")


def valid_image_equation_evidence(value: Any) -> bool:
    """Validate shape, pass state, and calibrated metric thresholds."""
    try:
        evidence = VerificationEvidence.model_validate(value)
    except (TypeError, ValueError, ValidationError):
        return False
    return bool(
        evidence.passed
        and evidence.threshold_version == IMAGE_EQUATION_THRESHOLD_VERSION
        and evidence.required_ink_iou == IMAGE_EQUATION_REQUIRED_INK_IOU
        and evidence.required_pixel_similarity
        == IMAGE_EQUATION_REQUIRED_PIXEL_SIMILARITY
        and evidence.ink_iou >= evidence.required_ink_iou
        and evidence.pixel_similarity >= evidence.required_pixel_similarity
    )


def valid_sha256(value: Any) -> bool:
    """Return whether a value is an exact lowercase SHA-256 digest."""
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def validated_visual_semantic_contract(
    value: Any,
) -> VisualSemanticContract | None:
    """Return only a complete allowlisted contract, never unvalidated JSON."""
    if value is None:
        return None
    try:
        return VisualSemanticContractAdapter.validate_python(value)
    except (TypeError, ValueError, ValidationError):
        return None


def visual_semantic_disposition(fix: Any) -> str:
    """Classify stored visual evidence without exposing corrupt raw payloads."""
    if getattr(fix, "source_kind", None) != IMAGE_EQUATION_SOURCE_KIND:
        return "not_applicable"
    raw_contract = getattr(fix, "visual_semantic_contract", None)
    if raw_contract is None:
        return "legacy_incomplete"
    contract = validated_visual_semantic_contract(raw_contract)
    return "complete" if _valid_image_contract_binding(fix, contract) else "invalid"


def _contract_dict(value: Any) -> dict[str, Any]:
    contract = validated_visual_semantic_contract(value)
    if contract is None:
        raise ValueError("image equation fix lacks a complete visual contract")
    return contract.model_dump(mode="json")


def _valid_image_contract_binding(
    fix: Any, contract: VisualSemanticContract | None = None
) -> bool:
    """Validate the typed contract and its legacy compatibility projections."""
    contract = contract or validated_visual_semantic_contract(
        getattr(fix, "visual_semantic_contract", None)
    )
    if contract is None:
        return False
    if (
        getattr(fix, "fixed_content", None) != contract.semantic_output.alt_text
        or getattr(fix, "page_number", None) != contract.locator.page_number
    ):
        return False
    roundtrip = next(
        (
            evidence
            for evidence in contract.verification_evidence
            if isinstance(evidence, PrintedEquationRoundtripEvidenceV1)
        ),
        None,
    )
    try:
        legacy_evidence = _evidence_dict(getattr(fix, "verification_evidence", None))
    except (TypeError, ValueError, ValidationError):
        return False
    if roundtrip is None or legacy_evidence != roundtrip.model_dump(
        mode="json", exclude={"evidence_kind"}
    ):
        return False
    raw_locator = getattr(fix, "source_locator", None)
    if isinstance(contract.locator, FrozenPageRasterRegionLocator):
        if raw_locator is None:
            return False
        try:
            if canonical_region_locator(raw_locator) != contract.locator.model_dump(
                mode="json"
            ):
                return False
        except (TypeError, ValueError, ValidationError):
            return False
    elif isinstance(contract.locator, EmbeddedImageOccurrenceLocator):
        if raw_locator is not None:
            return False
    else:
        return False
    return True


def _review_digest_value(value: Any) -> Any:
    """Represent arbitrary canonical text as bounded passive digest material."""
    if hasattr(value, "value"):
        value = value.value
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("review values must be finite")
        return value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return {
            "text_sha256": hashlib.sha256(encoded).hexdigest(),
            "utf8_bytes": len(encoded),
        }
    if isinstance(value, dict):
        return {key: _review_digest_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_review_digest_value(item) for item in value]
    raise TypeError("review values must contain only passive JSON data")


def review_digest_for(fix: Any) -> str | None:
    """Hash every canonical field that a reviewer is accepting."""
    contract: VisualSemanticContract | None = None
    if getattr(fix, "source_kind", None) == IMAGE_EQUATION_SOURCE_KIND:
        contract = validated_visual_semantic_contract(
            getattr(fix, "visual_semantic_contract", None)
        )
        if not _valid_image_contract_binding(fix, contract):
            return None
    material: dict[str, Any] = {"review_contract_version": "scan-fix-review-v1"}
    for field in _CANONICAL_FIELDS:
        value = getattr(fix, field, None)
        if field == "visual_semantic_contract" and contract is not None:
            value = contract.model_dump(mode="json")
        material[field] = value
    try:
        return canonical_sha256(_review_digest_value(material))
    except (TypeError, ValueError):
        return None


def _current_review_digest(fix: Any) -> str | None:
    stored = getattr(fix, "review_digest", None)
    expected = review_digest_for(fix)
    if expected is None or not valid_sha256(stored) or stored != expected:
        return None
    return expected


def _occurrence_key(fix: Any) -> str:
    """Bind durable identity to one stable document occurrence."""
    raw_locator = getattr(fix, "source_locator", None)
    if raw_locator is not None:
        locator = canonical_region_locator(raw_locator)
        payload = json.dumps(
            {
                "version": "page-raster-region-occurrence-v1",
                "source_locator": locator,
            },
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    contract = validated_visual_semantic_contract(
        getattr(fix, "visual_semantic_contract", None)
    )
    if contract is not None and isinstance(
        contract.locator, EmbeddedImageOccurrenceLocator
    ):
        payload = json.dumps(
            {
                "version": "embedded-image-occurrence-v1",
                "locator": contract.locator.model_dump(mode="json"),
            },
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    issue_id = sanitize_for_postgres(getattr(fix, "issue_id", None))
    location = sanitize_for_postgres(getattr(fix, "location", None))
    page_number = getattr(fix, "page_number", None)
    if not isinstance(issue_id, str) or not issue_id:
        raise ValueError("fix issue_id is invalid")
    payload = json.dumps(
        [issue_id, location, page_number],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_scan_fix(scan_id: str, fix: FixedIssue) -> ScanFix:
    """Build one canonical row, overriding forgeable image-review flags."""
    source_kind = getattr(fix, "source_kind", None)
    source_locator = (
        canonical_region_locator(getattr(fix, "source_locator", None))
        if getattr(fix, "source_locator", None) is not None
        else None
    )
    raw_confidence = float(getattr(fix, "confidence", math.nan))
    if not math.isfinite(raw_confidence) or not 0.0 <= raw_confidence <= 1.0:
        raise ValueError("fix confidence is invalid")
    evidence = (
        _evidence_dict(getattr(fix, "verification_evidence", None))
        if getattr(fix, "verification_evidence", None) is not None
        else None
    )
    raw_visual_contract = getattr(fix, "visual_semantic_contract", None)
    visual_contract = None
    if source_kind == IMAGE_EQUATION_SOURCE_KIND:
        visual_contract = _contract_dict(raw_visual_contract)
        if (
            not getattr(fix, "provider_used", None)
            or not getattr(fix, "model_used", None)
            or not valid_image_equation_evidence(evidence)
        ):
            raise ValueError("image equation fix lacks valid durable evidence")
        contract_model = VisualSemanticContractAdapter.validate_python(visual_contract)
        if source_locator is None:
            if not isinstance(contract_model.locator, EmbeddedImageOccurrenceLocator):
                raise ValueError("image equation source locator is incomplete")
        elif not isinstance(
            contract_model.locator, FrozenPageRasterRegionLocator
        ) or source_locator != contract_model.locator.model_dump(mode="json"):
            raise ValueError("visual contract locator does not match source locator")
        fix_method = "ai_vision"
        confidence = min(raw_confidence, 0.55)
        needs_review = True
        review_status = "pending"
    else:
        if (
            source_kind is not None
            or source_locator is not None
            or evidence is not None
            or raw_visual_contract is not None
        ):
            raise ValueError("verification evidence requires a supported source kind")
        fix_method = fix.fix_method
        confidence = raw_confidence
        needs_review = bool(fix.needs_review)
        review_status = "pending" if needs_review else "auto_approved"

    occurrence_key = _occurrence_key(fix)
    row = ScanFix(
        id=str(
            uuid.uuid5(
                uuid.NAMESPACE_URL, f"aelira:scan-fix:{scan_id}:{occurrence_key}"
            )
        ),
        scan_id=scan_id,
        issue_id=fix.issue_id,
        occurrence_key=occurrence_key,
        category=(
            fix.category.value if hasattr(fix.category, "value") else fix.category
        ),
        severity=(
            fix.severity.value if hasattr(fix.severity, "value") else fix.severity
        ),
        description=sanitize_for_postgres(fix.description),
        location=sanitize_for_postgres(fix.location),
        original_content=sanitize_for_postgres(fix.original_content),
        fixed_content=sanitize_for_postgres(fix.fixed_content),
        fix_method=fix_method,
        provider_used=getattr(fix, "provider_used", None),
        model_used=getattr(fix, "model_used", None),
        source_kind=source_kind,
        source_locator=source_locator,
        verification_evidence=evidence,
        visual_semantic_contract=visual_contract,
        confidence=confidence,
        needs_review=needs_review,
        review_status=review_status,
        reviewed_by=None,
        reviewed_at=None,
        wcag_criteria=fix.wcag_criteria,
        page_number=fix.page_number,
    )
    row.review_digest = review_digest_for(row)
    if row.review_digest is None:
        raise ValueError("fix review digest could not be constructed")
    row.approved_review_digest = (
        row.review_digest if review_status == "auto_approved" else None
    )
    return row


def persist_scan_fixes(
    db: Any, scan_id: str, fixes: Iterable[FixedIssue], *, replace: bool = True
) -> list[ScanFix]:
    """Persist canonical rows for direct and queued remediation paths."""
    rows = [build_scan_fix(scan_id, fix) for fix in fixes]
    occurrence_keys = [row.occurrence_key for row in rows]
    if len(set(occurrence_keys)) != len(occurrence_keys):
        raise ValueError("ambiguous duplicate fix occurrence")
    graph = lock_scan_review_graph(db, scan_id)
    existing = list(graph.fixes) if replace else []
    existing_by_occurrence: dict[str, ScanFix] = {}
    for row in existing:
        key = row.occurrence_key or _occurrence_key(row)
        if key in existing_by_occurrence:
            raise ValueError("ambiguous duplicate persisted fix occurrence")
        existing_by_occurrence[key] = row
    review_changed = (
        bool(rows)
        if not replace
        else set(existing_by_occurrence) != set(occurrence_keys)
    )
    persisted: list[ScanFix] = []
    for row in rows:
        current = existing_by_occurrence.pop(row.occurrence_key, None)
        if current is None:
            target = row
        else:
            canonical_unchanged = all(
                getattr(current, field, None) == getattr(row, field, None)
                for field in _CANONICAL_FIELDS
            )
            legacy_digest_bootstrap = (
                canonical_unchanged
                and getattr(current, "source_kind", None) is None
                and getattr(current, "review_digest", None) is None
                and getattr(current, "approved_review_digest", None) is None
            )
            unchanged = canonical_unchanged and (
                legacy_digest_bootstrap
                or getattr(current, "review_digest", None) == row.review_digest
            )
            for field in _CANONICAL_FIELDS:
                setattr(current, field, getattr(row, field))
            current.review_digest = row.review_digest
            if legacy_digest_bootstrap:
                current.approved_review_digest = (
                    row.review_digest
                    if current.review_status in {"auto_approved", "approved"}
                    else None
                )
            if not unchanged:
                review_changed = True
                previous_review_status = current.review_status
                previous_reviewer = current.reviewed_by
                current.review_status = row.review_status
                current.reviewed_by = None
                current.reviewed_at = None
                current.review_notes = None
                current.approved_review_digest = row.approved_review_digest
                db.add(
                    ReviewAuditLog(
                        id=str(uuid.uuid4()),
                        scan_id=scan_id,
                        fix_id=current.id,
                        user_id=None,
                        action="fix_replaced",
                        details={
                            "previous_review_status": previous_review_status,
                            "previous_reviewer": previous_reviewer,
                        },
                    )
                )
            target = current
        db.add(target)
        persisted.append(target)
    for stale in existing_by_occurrence.values():
        db.delete(stale)
    if review_changed:
        invalidate_current_artifact_approvals(db, graph, reason="fix_set_replaced")
    return persisted


def image_equation_review_blockers(fixes: Iterable[Any]) -> list[str]:
    """Require durable, explicit human acceptance for each image equation."""
    image_fixes = [
        fix
        for fix in fixes
        if getattr(fix, "source_kind", None) == IMAGE_EQUATION_SOURCE_KIND
    ]
    if not image_fixes:
        return []

    blockers: list[str] = []
    dispositions = [visual_semantic_disposition(fix) for fix in image_fixes]
    if "legacy_incomplete" in dispositions:
        blockers.append("image_equation_visual_contract_incomplete")
    if "invalid" in dispositions:
        blockers.append("image_equation_visual_contract_invalid")
    if any(
        getattr(fix, "fix_method", None) != "ai_vision"
        or not bool(getattr(fix, "needs_review", False))
        or not isinstance(getattr(fix, "confidence", None), (int, float))
        or not math.isfinite(float(getattr(fix, "confidence", math.nan)))
        or not 0.0 <= float(getattr(fix, "confidence", math.nan)) <= 0.55
        or not getattr(fix, "provider_used", None)
        or not getattr(fix, "model_used", None)
        or not _valid_image_contract_binding(fix)
        or (
            getattr(fix, "source_locator", None) is not None
            and not valid_region_locator(getattr(fix, "source_locator", None))
        )
        for fix in image_fixes
    ):
        blockers.append("image_equation_provenance_invalid")
    if any(getattr(fix, "review_status", None) != "approved" for fix in image_fixes):
        blockers.append("image_equation_not_human_approved")
    if any(not getattr(fix, "reviewed_by", None) for fix in image_fixes):
        blockers.append("image_equation_reviewer_missing")
    if any(getattr(fix, "reviewed_at", None) is None for fix in image_fixes):
        blockers.append("image_equation_review_time_missing")
    if any(
        not valid_image_equation_evidence(getattr(fix, "verification_evidence", None))
        for fix in image_fixes
    ):
        blockers.append("image_equation_evidence_invalid")
    current_digests = [_current_review_digest(fix) for fix in image_fixes]
    if any(digest is None for digest in current_digests):
        blockers.append("image_equation_review_digest_invalid")
    if any(
        getattr(fix, "review_status", None) == "approved"
        and (
            current_digest is None
            or getattr(fix, "approved_review_digest", None) != current_digest
        )
        for fix, current_digest in zip(image_fixes, current_digests)
    ):
        blockers.append("image_equation_approval_digest_invalid")
    return blockers


def artifact_review_blockers(fixes: Iterable[Any]) -> list[str]:
    """Return shared blockers used by metadata and approval boundaries."""
    rows = list(fixes)
    blockers: list[str] = []
    terminal = {"auto_approved", "approved", "rejected"}
    accepted = {"auto_approved", "approved"}
    if not rows:
        blockers.append("no_fixes")
    elif any(getattr(fix, "review_status", None) not in terminal for fix in rows):
        blockers.append("fixes_pending_review")
    if rows and not any(
        getattr(fix, "review_status", None) in accepted for fix in rows
    ):
        blockers.append("no_accepted_fix")
    accepted_rows = [
        fix for fix in rows if getattr(fix, "review_status", None) in accepted
    ]
    occurrence_keys = [getattr(fix, "occurrence_key", None) for fix in accepted_rows]
    if any(not valid_sha256(key) for key in occurrence_keys) or len(
        set(occurrence_keys)
    ) != len(occurrence_keys):
        blockers.append("fix_occurrence_identity_invalid")
    current_digests = [_current_review_digest(fix) for fix in accepted_rows]
    if any(digest is None for digest in current_digests):
        blockers.append("fix_review_digest_invalid")
    if any(
        current_digest is None
        or getattr(fix, "approved_review_digest", None) != current_digest
        for fix, current_digest in zip(accepted_rows, current_digests)
    ):
        blockers.append("fix_approval_digest_invalid")
    blockers.extend(image_equation_review_blockers(rows))
    return blockers


def artifact_approval_review_digest(
    artifact_sha256: str, fixes: Iterable[Any]
) -> str | None:
    """Bind artifact bytes to the exact accepted fix-review decisions."""
    if not valid_sha256(artifact_sha256):
        return None
    rows = list(fixes)
    if artifact_review_blockers(rows):
        return None
    accepted: list[dict[str, str]] = []
    for fix in rows:
        if getattr(fix, "review_status", None) not in {"auto_approved", "approved"}:
            continue
        occurrence_key = getattr(fix, "occurrence_key", None)
        current_digest = _current_review_digest(fix)
        if not valid_sha256(occurrence_key) or current_digest is None:
            return None
        accepted.append(
            {
                "occurrence_key": occurrence_key,
                "approved_review_digest": current_digest,
            }
        )
    accepted.sort(key=lambda item: item["occurrence_key"])
    return canonical_sha256(
        {
            "artifact_sha256": artifact_sha256,
            "accepted_fixes": accepted,
        }
    )


def validate_fix_review_action(fix: Any, action: str) -> None:
    """Prevent metadata-only edits from claiming to change immutable artifact bytes."""
    if (
        getattr(fix, "source_kind", None) == IMAGE_EQUATION_SOURCE_KIND
        and action == "edit"
    ):
        raise ValueError("image equation fixes cannot be edited after publication")
    if (
        getattr(fix, "source_kind", None) == IMAGE_EQUATION_SOURCE_KIND
        and action == "approve"
        and _current_review_digest(fix) is None
    ):
        raise ValueError("image equation visual review contract is incomplete or stale")


def bind_fix_review_decision(fix: Any, action: str) -> None:
    """Bind an approval to the exact canonical fix state being reviewed."""
    validate_fix_review_action(fix, action)
    if action == "reject":
        fix.approved_review_digest = None
        return
    current_digest = _current_review_digest(fix)
    if getattr(fix, "source_kind", None) != IMAGE_EQUATION_SOURCE_KIND:
        current_digest = review_digest_for(fix)
        if current_digest is None:
            raise ValueError("fix review digest could not be constructed")
        fix.review_digest = current_digest
    if current_digest is None:
        raise ValueError("fix review digest is incomplete or stale")
    fix.approved_review_digest = current_digest


def apply_authenticated_batch_review(
    db: Any,
    *,
    scan_id: str,
    fixes: Iterable[Any],
    action: str,
    user_id: str,
    reviewed_at: datetime,
    notes: str | None = None,
) -> list[Any]:
    """Apply one explicit authenticated decision and audit every selected fix."""
    if action not in {"approve", "reject"}:
        raise ValueError("batch review action is invalid")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("authenticated reviewer is required")
    rows = list(fixes)
    approval_digests: list[str | None] = [None] * len(rows)
    if action == "approve":
        for index, fix in enumerate(rows):
            validate_fix_review_action(fix, action)
            digest = _current_review_digest(fix)
            if getattr(fix, "source_kind", None) != IMAGE_EQUATION_SOURCE_KIND:
                digest = review_digest_for(fix)
            if digest is None:
                raise ValueError("fix review digest could not be constructed")
            approval_digests[index] = digest
    status = "approved" if action == "approve" else "rejected"
    for fix, approval_digest in zip(rows, approval_digests):
        if action == "approve":
            fix.review_digest = approval_digest
            fix.approved_review_digest = approval_digest
        else:
            fix.approved_review_digest = None
        fix.review_status = status
        fix.reviewed_by = user_id
        fix.reviewed_at = reviewed_at
        fix.review_notes = notes
        db.add(
            ReviewAuditLog(
                id=str(uuid.uuid4()),
                scan_id=scan_id,
                fix_id=fix.id,
                user_id=user_id,
                action=f"fix_batch_{action}",
                details={"notes": sanitize_for_postgres(notes)},
            )
        )
    return rows
