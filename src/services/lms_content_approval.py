"""Human review receipts bound to the exact stored LMS HTML candidate."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any

APPROVAL_KEY = "content_writeback_approval"


class LMSContentApprovalError(ValueError):
    """The current content has no applicable human approval."""


def _snapshot(cloud_file: Any) -> dict[str, Any]:
    source = getattr(cloud_file, "content_body", None)
    candidate = getattr(cloud_file, "remediated_body", None)
    if (
        not isinstance(source, str)
        or not isinstance(candidate, str)
        or not candidate.strip()
        or getattr(cloud_file, "needs_rescan", False)
        or getattr(cloud_file, "current_remediation_artifact_id", None)
    ):
        raise LMSContentApprovalError("content_approval_missing_or_stale")
    result: dict[str, Any] = {"version": 1}
    for field in (
        "id",
        "department_id",
        "credential_id",
        "provider",
        "provider_file_id",
        "provider_parent_id",
    ):
        value = getattr(cloud_file, field, None)
        if not isinstance(value, str) or not value:
            raise LMSContentApprovalError("content_approval_missing_or_stale")
        result[field] = value
    for field in ("last_scan_id", "content_source", "content_slug"):
        value = getattr(cloud_file, field, None)
        result[field] = value if isinstance(value, str) else None
    updated = getattr(cloud_file, "content_updated_at", None)
    result["content_updated_at"] = (
        updated.isoformat() if isinstance(updated, datetime) else None
    )
    metadata = getattr(cloud_file, "provider_metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    result["url"] = metadata.get("url")
    result["org_unit_id"] = metadata.get("org_unit_id")
    provenance = metadata.get("content_remediation_candidate")
    result["candidate_fingerprint"] = (
        provenance.get("fingerprint") if isinstance(provenance, dict) else None
    )
    result["source_sha256"] = hashlib.sha256(source.encode("utf-8")).hexdigest()
    result["candidate_sha256"] = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    return result


def approve_lms_content(
    cloud_file: Any, *, actor_id: str, actor_ref: str
) -> dict[str, Any]:
    """Record an explicit authenticated review; workers never call this function."""
    if (
        not isinstance(actor_id, str)
        or not actor_id.strip()
        or not isinstance(actor_ref, str)
        or not actor_ref.strip()
    ):
        raise LMSContentApprovalError("content_approval_actor_missing")
    receipt = {
        **_snapshot(cloud_file),
        "approved_by_id": actor_id,
        "approved_by_ref": actor_ref,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata = getattr(cloud_file, "provider_metadata", None)
    cloud_file.provider_metadata = {
        **(metadata if isinstance(metadata, dict) else {}),
        APPROVAL_KEY: receipt,
    }
    cloud_file.writeback_status = "approved"
    return receipt


def current_lms_content_approval(cloud_file: Any) -> dict[str, Any] | None:
    """Return the original approver only while every approved binding is current."""
    metadata = getattr(cloud_file, "provider_metadata", None)
    receipt = metadata.get(APPROVAL_KEY) if isinstance(metadata, dict) else None
    if not isinstance(receipt, dict) or cloud_file.writeback_status != "approved":
        return None
    try:
        expected = _snapshot(cloud_file)
        if any(receipt.get(key) != value for key, value in expected.items()):
            return None
        if any(
            not isinstance(receipt.get(key), str) or not receipt[key].strip()
            for key in ("approved_by_id", "approved_by_ref", "approved_at")
        ):
            return None
        if datetime.fromisoformat(receipt["approved_at"]).tzinfo is None:
            return None
    except (LMSContentApprovalError, TypeError, ValueError):
        return None
    return dict(receipt)


def clear_lms_content_approval(cloud_file: Any) -> None:
    metadata = getattr(cloud_file, "provider_metadata", None)
    if isinstance(metadata, dict) and APPROVAL_KEY in metadata:
        cloud_file.provider_metadata = {
            key: value for key, value in metadata.items() if key != APPROVAL_KEY
        }
