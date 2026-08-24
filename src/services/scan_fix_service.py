"""Shared durable ScanFix persistence and image-equation review policy."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import hashlib
import json
import math
from typing import Any
import uuid

from pydantic import ValidationError

from ..db.models import ReviewAuditLog, Scan, ScanFix
from ..education.remediation.base import FixedIssue, VerificationEvidence
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
    "verification_evidence",
    "confidence",
    "needs_review",
    "wcag_criteria",
    "page_number",
)


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


def _occurrence_key(fix: Any) -> str:
    """Bind durable identity to one stable document occurrence."""
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
    raw_confidence = float(getattr(fix, "confidence", math.nan))
    if not math.isfinite(raw_confidence) or not 0.0 <= raw_confidence <= 1.0:
        raise ValueError("fix confidence is invalid")
    evidence = (
        _evidence_dict(getattr(fix, "verification_evidence", None))
        if getattr(fix, "verification_evidence", None) is not None
        else None
    )
    if source_kind == IMAGE_EQUATION_SOURCE_KIND:
        if (
            not getattr(fix, "provider_used", None)
            or not getattr(fix, "model_used", None)
            or not valid_image_equation_evidence(evidence)
        ):
            raise ValueError("image equation fix lacks valid durable evidence")
        fix_method = "ai_vision"
        confidence = min(raw_confidence, 0.55)
        needs_review = True
        review_status = "pending"
    else:
        if source_kind is not None or evidence is not None:
            raise ValueError("verification evidence requires a supported source kind")
        fix_method = fix.fix_method
        confidence = raw_confidence
        needs_review = bool(fix.needs_review)
        review_status = "pending" if needs_review else "auto_approved"

    occurrence_key = _occurrence_key(fix)
    return ScanFix(
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
        verification_evidence=evidence,
        confidence=confidence,
        needs_review=needs_review,
        review_status=review_status,
        reviewed_by=None,
        reviewed_at=None,
        wcag_criteria=fix.wcag_criteria,
        page_number=fix.page_number,
    )


def persist_scan_fixes(
    db: Any, scan_id: str, fixes: Iterable[FixedIssue], *, replace: bool = True
) -> list[ScanFix]:
    """Persist canonical rows for direct and queued remediation paths."""
    rows = [build_scan_fix(scan_id, fix) for fix in fixes]
    occurrence_keys = [row.occurrence_key for row in rows]
    if len(set(occurrence_keys)) != len(occurrence_keys):
        raise ValueError("ambiguous duplicate fix occurrence")
    locked_scan_id = (
        db.query(Scan.id).filter(Scan.id == scan_id).with_for_update().scalar()
    )
    if locked_scan_id is None:
        raise ValueError("scan does not exist")
    existing = (
        db.query(ScanFix).filter(ScanFix.scan_id == scan_id).all() if replace else []
    )
    existing_by_occurrence: dict[str, ScanFix] = {}
    for row in existing:
        key = row.occurrence_key or _occurrence_key(row)
        if key in existing_by_occurrence:
            raise ValueError("ambiguous duplicate persisted fix occurrence")
        existing_by_occurrence[key] = row
    persisted: list[ScanFix] = []
    for row in rows:
        current = existing_by_occurrence.pop(row.occurrence_key, None)
        if current is None:
            target = row
        else:
            unchanged = all(
                getattr(current, field, None) == getattr(row, field, None)
                for field in _CANONICAL_FIELDS
            )
            for field in _CANONICAL_FIELDS:
                setattr(current, field, getattr(row, field))
            if not unchanged:
                previous_review_status = current.review_status
                previous_reviewer = current.reviewed_by
                current.review_status = row.review_status
                current.reviewed_by = None
                current.reviewed_at = None
                current.review_notes = None
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
    if any(
        getattr(fix, "fix_method", None) != "ai_vision"
        or not bool(getattr(fix, "needs_review", False))
        or not isinstance(getattr(fix, "confidence", None), (int, float))
        or not math.isfinite(float(getattr(fix, "confidence", math.nan)))
        or not 0.0 <= float(getattr(fix, "confidence", math.nan)) <= 0.55
        or not getattr(fix, "provider_used", None)
        or not getattr(fix, "model_used", None)
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
    blockers.extend(image_equation_review_blockers(rows))
    return blockers


def validate_fix_review_action(fix: Any, action: str) -> None:
    """Prevent metadata-only edits from claiming to change immutable artifact bytes."""
    if (
        getattr(fix, "source_kind", None) == IMAGE_EQUATION_SOURCE_KIND
        and action == "edit"
    ):
        raise ValueError("image equation fixes cannot be edited after publication")


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
    status = "approved" if action == "approve" else "rejected"
    for fix in rows:
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
