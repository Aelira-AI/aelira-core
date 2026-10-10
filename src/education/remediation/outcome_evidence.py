"""Narrow saved-file finding evidence; never semantic or conformance approval."""

import re
from collections.abc import Mapping

_HASH = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_RECEIPT_KEYS = frozenset(
    {"method_version", "issue_id", "source_sha256", "output_sha256"}
)


def saved_finding_receipt(value, *, issue_id):
    """Validate only the receipt's shape. Artifact authority is checked separately."""
    if (
        not isinstance(value, Mapping)
        or set(value) != _RECEIPT_KEYS
        or value.get("method_version") != "pdf-finding-presence-v1"
        or not isinstance(issue_id, str)
        or not _IDENTIFIER.fullmatch(issue_id)
        or value.get("issue_id") != issue_id
        or any(
            not isinstance(value.get(key), str) or not _HASH.fullmatch(value[key])
            for key in ("source_sha256", "output_sha256")
        )
    ):
        return None
    return {key: value[key] for key in sorted(_RECEIPT_KEYS)}


def _public_receipt(record):
    value = record.get("saved_file_verification")
    index, scope = record.get("source_index"), record.get("source_index_scope")
    mapping_keys = (
        {"original_source_index"} if "original_source_index" in record else set()
    )
    if (
        not isinstance(value, Mapping)
        or set(value)
        != (_RECEIPT_KEYS - {"issue_id"})
        | {"source_index", "source_index_scope"}
        | mapping_keys
        or type(index) is not int
        or not 0 <= index < 10_000
        or not isinstance(scope, str)
        or scope not in {"original_scan", "approved_subset"}
        or type(value.get("source_index")) is not int
        or value.get("source_index") != index
        or value.get("source_index_scope") != scope
        or bool(mapping_keys)
        and (
            scope != "approved_subset"
            or type(record["original_source_index"]) is not int
            or not 0 <= record["original_source_index"] < 10_000
            or type(value.get("original_source_index")) is not int
            or value["original_source_index"] != record["original_source_index"]
        )
    ):
        return None
    # Reuse the same method and digest validation without inventing a public
    # issue ID for historical scans whose source rows have none.
    canonical = saved_finding_receipt(
        {
            key: item
            for key, item in value.items()
            if key
            not in {"source_index", "source_index_scope", "original_source_index"}
        }
        | {"issue_id": "source"},
        issue_id="source",
    )
    if canonical is None:
        return None
    canonical.pop("issue_id")
    return {
        **canonical,
        "source_index": index,
        "source_index_scope": scope,
        **(
            {"original_source_index": record["original_source_index"]}
            if mapping_keys
            else {}
        ),
    }


def fixed_record_evidence(record, *, status, source_sha256, output_sha256):
    """Reconcile a child finding's ID and independently established byte bindings."""
    if status not in {"fixed", "withheld"}:
        return {}
    evidence = {}
    if type(record.get("needs_review")) is bool:
        evidence["needs_review"] = record["needs_review"]
    if record.get("verification_passed") is False:
        evidence["verification_passed"] = False
    elif status == "fixed" and record.get("verification_passed") is True:
        receipt = saved_finding_receipt(
            record.get("saved_file_verification"), issue_id=record.get("issue_id")
        )
        if receipt is not None and (
            receipt["source_sha256"] == source_sha256
            and receipt["output_sha256"] == output_sha256
        ):
            evidence.update(
                verification_passed=True,
                verification_scope="saved_file_finding",
                saved_file_verification=receipt,
            )
    return evidence


def public_outcome_evidence(record, *, status):
    """Keep strict states and a canonical receipt, without inventing missing proof."""
    if status not in {"fixed", "withheld"}:
        return {}
    evidence = {}
    if type(record.get("needs_review")) is bool:
        evidence["needs_review"] = record["needs_review"]
    if record.get("verification_passed") is False:
        evidence["verification_passed"] = False
    elif status == "fixed" and record.get("verification_passed") is True:
        receipt = _public_receipt(record)
        if receipt is not None:
            evidence.update(
                verification_passed=True,
                verification_scope="saved_file_finding",
                saved_file_verification=receipt,
            )
            if "original_source_index" in receipt:
                evidence["original_source_index"] = receipt["original_source_index"]
    return evidence


def bind_outcome_evidence(record, *, status, source_sha256, output_sha256):
    """Positive proof requires the caller's independently established byte bindings."""
    evidence = public_outcome_evidence(record, status=status)
    receipt = evidence.get("saved_file_verification")
    if receipt is not None and (
        receipt["source_sha256"] != source_sha256
        or receipt["output_sha256"] != output_sha256
    ):
        for key in (
            "verification_passed",
            "verification_scope",
            "saved_file_verification",
            "original_source_index",
        ):
            evidence.pop(key, None)
    return evidence
